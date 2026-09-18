"""Offline preparation tests; no model or network service is constructed."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import research_assumption_package as builder
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.storage import canonical_json, read_json


def fixture(tmp_path):
    import hashlib

    cutoff = "2026-09-17T15:21:06+00:00"
    content = "Cash flow statement. Depreciation and amortization 100 80. Context."
    source = SourceDocument(id="nvda-q2-filing", url="https://example.test/filing",
                            title="Synthetic filing", publisher="Synthetic",
                            content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            retrieved_at=cutoff, published_at="2026-08-26T12:00:00+00:00", kind="filing")

    def fact(identifier, metric, value, *, start=None, unit="USD"):
        return FinancialFact(id=identifier, source_id=source.id, metric=metric,
                             value=value, scale="1000000", unit=unit,
                             currency="USD" if unit == "USD" else None,
                             basis="US GAAP", period_end="2026-07-26", period_start=start,
                             period_type="duration" if start else "instant", location="synthetic table")

    facts = [fact(builder.ANCHORS["current_revenue"], "revenue", "300", start="2025-07-28"),
             fact(builder.ANCHORS["current_working_capital"], "working_capital", "40"),
             fact(builder.ANCHORS["net_debt"], "net_debt", "10"),
             fact(builder.ANCHORS["current_diluted_shares"], "weighted_average_diluted_shares",
                  "20", start="2026-04-27", unit="shares")]
    for suffix, start in (("q2", "2026-04-27"), ("h1", "2026-01-26")):
        for metric, value in (("revenue", "100"), ("operating_income", "60"),
                              ("capex_cashflow", "-3"), ("stock_based_compensation", "2")):
            facts.append(fact(f"nvda-{metric}-{suffix}", metric, value, start=start))
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff=cutoff, sources=(source,), facts=tuple(facts))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    evidence_path = inputs / "evidence.json"
    evidence_path.write_bytes(canonical_json(snapshot))
    request = ResearchRequest(ticker="NVDA", cutoff=cutoff, backend="replay", report_language="English",
                              output_dir=tmp_path / "unused", evidence_path=evidence_path,
                              quality_revision="evidence-led", share_count_basis="latest_quarter_diluted_proxy")
    return request, snapshot


def test_bundle_calibrates_history_without_inventing_forecasts(tmp_path):
    request, _ = fixture(tmp_path)
    before = request.evidence_path.read_bytes()
    output = tmp_path / "package"
    manifest = builder.write_bundle(request, output)
    assert request.evidence_path.read_bytes() == before
    assert manifest["model_calls"] == manifest["network_calls"] == 0
    assert manifest["ready_for_model_review"] is False
    package = read_json(output / "assumptions.json")
    assert not any(entry["status"] == "reviewed" for entry in package["entries"])
    by_path = {entry["model_path"]: entry for entry in package["entries"]}
    assert by_path["current_revenue"]["range"]["base"] == "300000000"
    for path, fact_id in builder.ANCHORS.items():
        source = read_json(output / "evidence.json")
        fact = next(f for f in source["facts"] if f["id"] == fact_id)
        scale_path = "units.share_scale" if path == "current_diluted_shares" else "units.amount_scale"
        assert Decimal(by_path[path]["range"]["base"]) * Decimal(by_path[scale_path]["value"]) == (
            Decimal(fact["value"]) * Decimal(fact["scale"]))
    assert (output / "evidence.json").read_bytes() == before
    for path in ("discount_rate", "terminal_growth", "periods.*.operating_margin"):
        assert by_path[path]["status"] == "missing" and by_path[path]["range"] is None
    calibration = read_json(output / "calibration.json")
    assert len(calibration["observations"]) == 6
    ratios = {row["model_path"]: Decimal(row["value"]) for row in calibration["observations"]}
    assert ratios["periods.*.operating_margin"] == Decimal("0.6")
    assert ratios["periods.*.capex_pct_revenue"] == Decimal("0.03")
    assert ratios["periods.*.sbc_pct_revenue"] == Decimal("0.02")
    assert "overlap" in (output / "workbook.md").read_text()
    import hashlib
    for name, digest in manifest["artifact_hashes"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("mutation", ["period", "basis", "unit", "segment", "sign", "metric"])
def test_calibration_rejects_incompatible_or_mislabeled_inputs(tmp_path, mutation):
    _, snapshot = fixture(tmp_path)
    data = snapshot.model_dump(mode="json")
    fact = next(f for f in data["facts"] if f["id"] == "nvda-capex_cashflow-q2")
    updates = {"period": {"period_start": "2026-01-26"}, "basis": {"basis": "non-GAAP"},
               "unit": {"unit": "EUR", "currency": "EUR"}, "segment": {"segment": "segment"},
               "sign": {"value": "3"}, "metric": {"metric": "revenue"}}
    fact.update(updates[mutation])
    with pytest.raises(ValueError):
        builder.historical_calibration(EvidenceSnapshot.model_validate(data))


def test_missing_history_is_gap_not_zero(tmp_path):
    _, snapshot = fixture(tmp_path)
    snapshot = snapshot.model_copy(update={"facts": tuple(f for f in snapshot.facts
                                    if f.id != "nvda-capex_cashflow-q2")})
    result = builder.historical_calibration(snapshot)
    assert len(result["observations"]) == 5 and len(result["gaps"]) == 1


def test_passage_offsets_hash_and_missing_status(tmp_path):
    _, snapshot = fixture(tmp_path)
    rows = builder.source_passages(snapshot)
    located = rows[0]
    assert located["text"] == snapshot.sources[0].content[located["start"]:located["end"]]
    assert located["content_sha256"] == snapshot.sources[0].content_sha256
    assert rows[1]["status"] == "not_located"


def test_requires_new_output_outside_frozen_inputs(tmp_path):
    request, _ = fixture(tmp_path)
    output = tmp_path / "package"
    output.mkdir()
    with pytest.raises(ValueError, match="new"):
        builder.write_bundle(request, output)
    with pytest.raises(ValueError, match="outside"):
        builder.write_bundle(request, request.evidence_path.parent / "nested")


def test_source_change_aborts_before_publication(tmp_path, monkeypatch):
    request, _ = fixture(tmp_path)
    original = builder.read_bytes
    reads = 0

    def changed(path):
        nonlocal reads
        reads += 1
        return original(path) + (b" " if reads == 2 else b"")

    monkeypatch.setattr(builder, "read_bytes", changed)
    with pytest.raises(ValueError, match="changed"):
        builder.write_bundle(request, tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_wrong_company_or_share_policy_rejected(tmp_path):
    request, _ = fixture(tmp_path)
    for update in ({"ticker": "HOOD"}, {"share_count_basis": "point_in_time_diluted"}):
        with pytest.raises(ValueError, match="supports"):
            builder.build_package(request.model_copy(update=update), request.evidence_path.read_bytes())


def test_vintage_separate_from_cutoff_and_cli_read_only_validation(tmp_path, capsys):
    request, _ = fixture(tmp_path)
    package, _ = builder.build_package(request, request.evidence_path.read_bytes(),
                                      created_at=datetime.now(timezone.utc))
    assert package.created_at > package.cutoff
    config = tmp_path / "request.json"
    config.write_bytes(canonical_json(request))
    output = tmp_path / "package"
    assert builder.main(["--config", str(config), "--output", str(output)]) == 0
    before = (output / "assumptions.json").read_bytes()
    assert builder.main(["--config", str(config), "--validate", str(output / "assumptions.json")]) == 0
    assert (output / "assumptions.json").read_bytes() == before
    assert '"ready_for_model_review":false' in capsys.readouterr().out


def test_publication_failure_never_leaves_completion_marker(tmp_path, monkeypatch):
    request, _ = fixture(tmp_path)
    original = Path.open

    def fail_on_calibration(path, *args, **kwargs):
        if path.name == "calibration.json" and args == ("xb",):
            raise OSError("synthetic storage failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_on_calibration)
    output = tmp_path / "package"
    with pytest.raises(OSError):
        builder.write_bundle(request, output)
    assert (output / "assumptions.json").exists()
    assert not (output / "manifest.json").exists()


def test_source_change_during_publication_withholds_manifest(tmp_path, monkeypatch):
    request, _ = fixture(tmp_path)
    original = builder.read_bytes
    reads = 0

    def changed(path):
        nonlocal reads
        reads += 1
        return original(path) + (b" " if reads == 3 else b"")

    monkeypatch.setattr(builder, "read_bytes", changed)
    output = tmp_path / "package"
    with pytest.raises(ValueError, match="during publication"):
        builder.write_bundle(request, output)
    assert (output / "assumptions.json").exists()
    assert not (output / "manifest.json").exists()
