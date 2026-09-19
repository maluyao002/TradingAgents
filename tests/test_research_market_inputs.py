import os
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from scripts import research_market_inputs as market
from scripts.research_market_inputs import (
    MarketInputParseError,
    _parse_beta,
    _parse_erp,
    _parse_h15,
    _parse_sep,
    merge_evidence,
)
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.storage import canonical_json, read_json


def _source() -> SourceDocument:
    content = "base source"
    return SourceDocument(
        id="base-source", url="https://example.test/base", title="Base", publisher="Test",
        retrieved_at="2026-09-01T00:00:00Z", published_at="2026-09-01T00:00:00Z",
        content=content, content_sha256=sha256(content.encode()).hexdigest(), kind="other",
    )


def test_h15_requires_nominal_section_and_latest_date():
    html = b'''<title>Federal Reserve Board - H.15 - Selected Interest Rates (Daily) - September 17, 2026</title><table id=h15table><tr><th>Instruments</th><th>2026 Sep 10</th><th>2026 Sep 11</th><th>2026 Sep 14</th><th>2026 Sep 15</th><th>2026 Sep 16</th></tr><tr><th>Nominal 9</th><td></td><td></td><td></td><td></td><td></td></tr><tr><th>10-year</th><td>4.95</td><td>4.96</td><td>4.97</td><td>5.00</td><td>5.01</td></tr><tr><th>Inflation indexed 10</th><td></td><td></td><td></td><td></td><td></td></tr><tr><th>10-year</th><td>2.55</td><td>2.60</td><td>2.60</td><td>2.62</td><td>2.68</td></tr></table>'''
    assert _parse_h15(html) == (Decimal("0.0501"), "2026-09-16")
    with pytest.raises(MarketInputParseError):
        _parse_h15(html.replace(b"Nominal 9", b"Nominal"))


def test_erp_preserves_stated_risk_free_pairing_and_default_spread():
    html = b"Implied ERP on September 1, 2026 = 4.14% (Trailing 12 month, with adjusted payout) x US treasury rate of 4.75% used as the riskfree rate x default spread (0.22%)"
    assert _parse_erp(html) == (Decimal("0.0414"), "2026-09-01", Decimal("0.0475"))
    with pytest.raises(MarketInputParseError):
        _parse_erp(html.replace(b"0.22", b"0.21"))
    with pytest.raises(MarketInputParseError):
        _parse_erp(html.replace(b"x US treasury", b"Implied ERP in previous month = 4.28% x US treasury"))


def test_beta_requires_semiconductor_not_equipment_and_exact_headers():
    headings = ['Industry Name','Number of firms','Beta','D/E Ratio','Effective Tax rate','Unlevered beta','Cash/Firm value','Unlevered beta corrected for cash','HiLo Risk','Standard deviation of equity','Standard deviation in operating income (last 10 years)']
    row = ['Semiconductor','66','1.52','2.59%','5.11%','1.49','1.02%','1.50','0.5440','55.83%','41.94%']
    html = ('Data used is as of January 2026<table><tr>' + ''.join(f'<th>{x}</th>' for x in headings) + '</tr><tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr></table>').encode()
    assert _parse_beta(html) == ('1.50', 66, '2026-01')
    with pytest.raises(MarketInputParseError):
        _parse_beta(html.replace(b"Semiconductor", b"Semiconductor Equip", 1))
    with pytest.raises(MarketInputParseError):
        _parse_beta(html.replace(b"January 2026", b"January 2027"))


def test_sep_uses_median_longer_run_real_and_pce_not_nominal():
    labels = ['2026','2027','2028','2029','Longer run'] * 3
    real = ['Change in real GDP','2.3','2.4','2.2','2.1','2.0'] + ['x'] * 10
    pce = ['PCE inflation','3.7','2.3','2.1','2.0','2.0'] + ['x'] * 10
    html = ('<div><h4>Summary of Economic Projections</h4>September 15–16, 2026<table><tr><th>Variable</th><th>Median 1</th><th>Central Tendency 2</th><th>Range 3</th></tr><tr>' + ''.join(f'<th>{x}</th>' for x in labels) + '</tr>' + ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in r) + '</tr>' for r in (real,pce)) + '</table></div>').encode()
    assert _parse_sep(html) == (Decimal("0.02"), Decimal("0.02"))


def test_merge_writes_new_snapshot_without_touching_base_or_cache(tmp_path: Path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    base_path = input_dir / "evidence.json"
    base = EvidenceSnapshot(ticker="NVDA", cutoff="2026-09-01T00:00:00Z", sources=(_source(),))
    base_path.write_bytes(canonical_json(base))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    raw = cache_dir / "untouched"
    raw.write_bytes(b"synthetic unchanged cache sentinel")
    stamp = datetime(2026, 9, 18, 7, 37, 28, tzinfo=timezone.utc)
    documents = tuple(_source().model_copy(update={"id": sid, "published_at": stamp,
                       "retrieved_at": stamp}) for sid in (
                           "market-fed-h15", "market-damodaran-erp", "market-damodaran-beta", "market-fed-sep"))
    monkeypatch.setattr("scripts.research_market_inputs.parse_market_inputs",
                        lambda cache: (documents, {"limitations": ["synthetic metadata"]}))
    before = sha256(raw.read_bytes()).hexdigest()
    destination = tmp_path / "output" / "evidence.json"

    merged = merge_evidence(base_path, cache_dir, destination)

    assert EvidenceSnapshot.model_validate(read_json(base_path)) == base
    assert merged.cutoff.isoformat().startswith("2026-09-18T07:37:28")
    assert merged.sources[:1] == base.sources
    assert [source.id for source in merged.sources[1:]] == [
        "market-fed-h15", "market-damodaran-erp", "market-damodaran-beta", "market-fed-sep",
    ]
    assert sha256(raw.read_bytes()).hexdigest() == before
    assert destination.with_suffix(".json.market-inputs.json").is_file()

    collision = tmp_path / "occupied" / "evidence.json"
    collision.parent.mkdir()
    occupied_sidecar = collision.with_suffix(".json.market-inputs.json")
    occupied_sidecar.write_bytes(b"existing metadata")
    with pytest.raises(MarketInputParseError, match="already exists"):
        merge_evidence(base_path, cache_dir, collision)
    assert not collision.exists()
    assert occupied_sidecar.read_bytes() == b"existing metadata"


def test_exclusive_publish_preserves_a_concurrent_winner(tmp_path, monkeypatch):
    destination = tmp_path / "evidence.json"
    original = os.link

    def competing_link(source, target, **kwargs):
        target.write_bytes(b"concurrent winner")
        return original(source, target, **kwargs)

    monkeypatch.setattr(market.os, "link", competing_link)
    with pytest.raises(MarketInputParseError, match="already exists"):
        market._write_new_file(destination, b"new payload")
    assert destination.read_bytes() == b"concurrent winner"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["evidence.json"]


@pytest.mark.parametrize("replace_owned", [False, True])
def test_pair_failure_rolls_back_only_owned_links(tmp_path, monkeypatch, replace_owned):
    sidecar, evidence = tmp_path / "metadata.json", tmp_path / "evidence.json"
    original = os.link

    def fail_second(source, target, **kwargs):
        if target == evidence:
            if replace_owned:
                sidecar.unlink()
                sidecar.write_bytes(b"another publisher")
            raise OSError("simulated second publication failure")
        return original(source, target, **kwargs)

    monkeypatch.setattr(market.os, "link", fail_second)
    with pytest.raises(OSError, match="second publication"):
        market._publish_new_files({sidecar: b"metadata", evidence: b"evidence"})
    assert not evidence.exists()
    if replace_owned:
        assert sidecar.read_bytes() == b"another publisher"
    else:
        assert not sidecar.exists()
    assert not list(tmp_path.glob(".*"))


def test_existing_sidecar_blocks_both_outputs(tmp_path):
    sidecar, evidence = tmp_path / "metadata.json", tmp_path / "evidence.json"
    sidecar.write_bytes(b"existing metadata")
    with pytest.raises(MarketInputParseError, match="already exists"):
        market._publish_new_files({sidecar: b"new metadata", evidence: b"evidence"})
    assert sidecar.read_bytes() == b"existing metadata"
    assert not evidence.exists()


def test_success_path_sidecar_replacement_blocks_completion(tmp_path, monkeypatch):
    sidecar, evidence = tmp_path / "metadata.json", tmp_path / "evidence.json"
    original = os.link

    def replace_sidecar_before_evidence(source, target, **kwargs):
        if target == evidence:
            sidecar.unlink()
            sidecar.write_bytes(b"foreign replacement")
        return original(source, target, **kwargs)

    monkeypatch.setattr(market.os, "link", replace_sidecar_before_evidence)
    with pytest.raises(MarketInputParseError, match="replaced before completion"):
        market._publish_new_files({sidecar: b"metadata", evidence: b"evidence"})
    assert sidecar.read_bytes() == b"foreign replacement"
    assert not evidence.exists()
    assert not list(tmp_path.glob(".*"))


@pytest.mark.skipif(os.name != "posix", reason="symlink creation may require Windows privileges")
def test_merge_rejects_a_dangling_destination_symlink_before_resolution(tmp_path):
    target, link = tmp_path / "absent-target", tmp_path / "output-link"
    link.symlink_to(target)
    with pytest.raises(MarketInputParseError, match="already exists"):
        merge_evidence(tmp_path / "not-read.json", tmp_path / "cache", link)
    assert link.is_symlink() and not target.exists()
