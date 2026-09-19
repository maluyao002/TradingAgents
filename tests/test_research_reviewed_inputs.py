"""Portable offline regressions for material delivery, plus optional frozen replay."""
import os
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from scripts.research_model_probe import _validate_authored_context, build_payload
from scripts.research_nvda_scenarios import authored_cases
from scripts.research_reviewed_inputs import build_reviewed_inputs
from tests.test_research_scenario_compiler import _package, _request, _snapshot
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.reviewed_inputs import (
    audit_model_payload,
    require_material_coverage,
    validate_material,
)
from tradingagents.research.storage import canonical_json, digest, read_bytes, read_json


def delivery_fixture():
    """Synthetic public-table shapes, not external claims or live data."""
    stamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    texts = {
        "market-fed-h15": (
            "Release date: September 17, 2026\nSelected Interest Rates\n"
            "Yields in percent per annum\nInstruments\n"
            + "".join(f"2026\nSep\n{day}\n" for day in (10, 11, 14, 15, 16))
            + "Treasury constant maturities\nNominal 9\n1-month\n0\n"
            "10-year\n4.95\n4.96\n4.97\n5.00\n5.01\n20-year\n5.39\nInflation indexed 10\n"
        ),
        "market-damodaran-erp": (
            "Implied ERP on September 1, 2026 = 4.14% (Trailing 12 month, with adjusted payout); "
            "US treasury rate of 4.75% used as the riskfree rate; default spread (0.22%)\n"
            "Day-to-day ERP\n"
        ),
        "market-damodaran-beta": (
            "Date of Analysis: Data used is as of January 2026\nIndustry Name\nNumber of firms\n"
            "Beta\nD/E Ratio\nEffective Tax rate\nUnlevered beta\nCash/Firm value\n"
            "Unlevered beta corrected for cash\nHiLo Risk\nStandard deviation of equity\n"
            "Standard deviation in operating income (last 10\nyears)\nAdvertising\n"
            "Semiconductor\n66\n1.52\n2.59%\n5.11%\n1.49\n1.02%\n1.50\n0.5440\n55.83%\n41.94%\n"
            "Semiconductor Equip\n"
        ),
        "market-fed-sep": (
            "For release at 2:00 p.m., EDT, September 16, 2026\nSummary of Economic Projections\n"
            "Percent\nVariable\nMedian1\nCentral Tendency2\nRange3\n"
            + "2026\n2027\n2028\n2029\nLonger run\n" * 3
            + "Change in real GDP\n2.3\n2.4\n2.2\n2.1\n2.0\n" + "2.0–2.1\n" * 10
            + "June projection\nPCE inflation\n3.7\n2.3\n2.1\n2.0\n2.0\n" + "2.0–2.1\n" * 10
            + "June projection\nCore PCE inflation4\n"
        ),
    }
    sources = tuple(SourceDocument(id=key, content=value, content_sha256=sha256(value.encode()).hexdigest(),
        url=f"https://example.test/{key}", title=key, publisher="Synthetic", kind="market",
        published_at=stamp, retrieved_at=stamp) for key, value in texts.items())
    old = _snapshot()
    snapshot = old.model_copy(update={"cutoff": stamp, "sources": (*old.sources, *sources)})
    request = _request(cutoff=stamp)
    market = {"risk_free_rate": ".0501", "risk_free_as_of": "2026-09-16", "erp": ".0414",
        "erp_as_of": "2026-09-01", "erp_risk_free_rate": ".0475", "unlevered_beta_cash_corrected": "1.50",
        "beta_as_of": "2026-01", "real_growth_long_run": ".02", "inflation_long_run": ".02",
        "source_ids": list(texts), "limitations": ["Synthetic frozen market references."]}
    cases, economic = authored_cases(request, market)
    package, _ = _package(snapshot)
    package = package.model_copy(update={"cutoff": stamp})
    bundle = build_reviewed_inputs(snapshot, package, cases, market, economic, "a" * 64)
    return request, snapshot, bundle


def test_actual_serialized_payload_contains_rows_headers_dates_and_derivations():
    request, snapshot, bundle = delivery_fixture()
    payload, estimate = build_payload(request, snapshot, reviewed_inputs=bundle)
    prompt = canonical_json({key: value for key, value in payload.items()
                            if key not in {"system", "response_schema", "timeout_seconds"}})
    audit = require_material_coverage(prompt, bundle)
    assert audit.complete and not audit.affected_outputs
    assert b"Nominal 9" in prompt and b"10-year" in prompt and b"Semiconductor" in prompt
    assert b"2026-09-16" in prompt and b"percent_per_annum" in prompt
    assert b"terminal_roic_assumption" in prompt and b"g/ROIC" in prompt
    assert b"analyst_derivation" in prompt
    assert estimate["estimated_input_components_utf8_bytes"]["canonical_prompt"] == len(prompt)


@pytest.mark.parametrize("section", ["source_material", "market_inputs", "terminal_derivations",
    "economic_derivations", "assumptions", "scenarios", "financial_facts", "review_sha256", "limitations"])
def test_hash_inventory_cannot_replace_delivered_material(section):
    request, snapshot, bundle = delivery_fixture()
    payload, _ = build_payload(request, snapshot, reviewed_inputs=bundle)
    value = payload["reviewed_inputs"].pop(section)
    payload["reviewed_inputs"][section + "_sha256"] = digest(value)
    audit = audit_model_payload(canonical_json(payload), bundle)
    assert not audit.complete and "operating_asset_value" in audit.affected_outputs
    with pytest.raises(ValueError, match="omitted or changed"):
        require_material_coverage(payload, bundle)


@pytest.mark.parametrize("mutation", ["header", "row", "roic", "capex", "market_date", "assumption"])
def test_tampered_actual_payload_fails_coverage(mutation):
    request, snapshot, bundle = delivery_fixture()
    payload, _ = build_payload(request, snapshot, reviewed_inputs=bundle)
    data = payload["reviewed_inputs"]
    if mutation in {"header", "row"}:
        key = "Nominal 9" if mutation == "header" else "5.01"
        data["source_material"][0]["text"] = data["source_material"][0]["text"].replace(key, "MISSING")
    elif mutation == "market_date":
        data["market_inputs"]["risk_free_as_of"] = "2026-09-15"
    elif mutation == "assumption":
        data["assumptions"]["entries"][0]["rationale"] = "Changed"
    else:
        field = "terminal_roic_assumption" if mutation == "roic" else "terminal_capex_pct_revenue"
        data["terminal_derivations"][0][field] = "0.99"
    assert not audit_model_payload(payload, bundle).complete


def test_source_material_must_match_eligible_original_source():
    _, snapshot, bundle = delivery_fixture()
    item = bundle.source_material[0]
    changed = item.model_copy(update={"text": "x" * len(item.text)})
    altered = bundle.model_copy(update={"source_material": (changed, *bundle.source_material[1:])})
    with pytest.raises(ValueError, match="changed source"):
        validate_material(altered, snapshot)
    source = snapshot.sources[1].model_copy(update={"published_at": None})
    with pytest.raises(ValueError, match="ineligible"):
        validate_material(bundle, snapshot.model_copy(update={"sources": (snapshot.sources[0], source, *snapshot.sources[2:])}))


@pytest.mark.parametrize("source_id,old,new", [
    ("market-fed-h15", "2026\nSep\n16", "2026\nSep\n15"),
    ("market-damodaran-beta", "Beta\nD/E Ratio", "D/E Ratio\nBeta"),
    ("market-damodaran-beta", "Semiconductor\n66", "Semiconductor Equip\n66"),
    ("market-fed-sep", "Change in real GDP\n2.3", "Change in real GDP\n9.9\n2.3"),
])
def test_rehashed_sources_cannot_hide_changed_material_layout(source_id, old, new):
    from scripts.research_reviewed_inputs import market_material

    _, snapshot, bundle = delivery_fixture()
    sources = []
    for source in snapshot.sources:
        if source.id == source_id:
            text = source.content.replace(old, new, 1)
            assert text != source.content
            source = SourceDocument.model_validate({**source.model_dump(), "content": text,
                                                    "content_sha256": sha256(text.encode()).hexdigest()})
        sources.append(source)
    with pytest.raises(ValueError):
        market_material(snapshot.model_copy(update={"sources": tuple(sources)}), bundle.market_inputs)


def test_adapter_rejects_market_value_and_economic_tampering():
    _, snapshot, bundle = delivery_fixture()
    market = deepcopy(bundle.market_inputs)
    market["risk_free_rate"] = ".051"
    with pytest.raises(ValueError, match="discount-rate"):
        build_reviewed_inputs(snapshot, bundle.assumptions, bundle.scenarios, market,
                              bundle.economic_derivations, bundle.review_sha256)
    economic = deepcopy(bundle.economic_derivations)
    economic["base"]["terminal_roic_assumption"] = ".99"
    with pytest.raises(ValueError, match="reinvestment|capex"):
        build_reviewed_inputs(snapshot, bundle.assumptions, bundle.scenarios, bundle.market_inputs,
                              economic, bundle.review_sha256)


@pytest.mark.parametrize("field,value", [
    ("probability", ".6"), ("target_debt_weight", ".2"),
    ("discount_rate_formula", "accepted WACC"), ("accepted_forecast", True),
])
def test_economic_packet_cannot_add_probabilities_or_acceptance(field, value):
    _, snapshot, bundle = delivery_fixture()
    economic = deepcopy(bundle.economic_derivations)
    economic["base"][field] = value
    with pytest.raises(ValueError):
        build_reviewed_inputs(snapshot, bundle.assumptions, bundle.scenarios, bundle.market_inputs,
                              economic, bundle.review_sha256)


@pytest.mark.skipif(os.name != "posix", reason="authored probe requires POSIX file guards")
@pytest.mark.parametrize("empty_value", [[], None, "", 0])
def test_falsey_malformed_auxiliaries_are_not_legacy_packets(tmp_path, empty_value):
    from tests.test_research_authored_probe import fixture

    request, context, _ = fixture(tmp_path)
    packet = tmp_path / "inputs"
    for name in ("market_inputs.json", "economic_audit.json"):
        raw = canonical_json(empty_value)
        (packet / name).write_bytes(raw)
        context["packet_manifest"]["artifact_hashes"][name] = sha256(raw).hexdigest()
    (packet / "manifest.json").write_bytes(canonical_json(context["packet_manifest"]))
    context["review"]["manifest_sha256"] = digest(context["packet_manifest"])
    context["review_sha256"] = digest(context["review"])
    raw = read_bytes(packet / "evidence.json")
    with pytest.raises(ValueError, match="must be objects"):
        _validate_authored_context(context, request, EvidenceSnapshot.model_validate_json(raw), raw,
                                   context["review_sha256"], packet)


def test_compiler_preserves_request_size_limit(tmp_path):
    from scripts.research_nvda_scenarios import _publish, compile_reviewed
    from tradingagents.research.scenario_compiler import REVIEWED_PACKET_FILES

    packet = tmp_path / "packet"
    blobs = dict.fromkeys(REVIEWED_PACKET_FILES, b"{}")
    blobs["request.json"] = b" " * (1024 * 1024) + b"{}"
    _publish(packet, blobs)
    with pytest.raises(ValueError, match="1 MiB"):
        compile_reviewed(packet, tmp_path / "unused-review", tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.skipif(os.name != "posix", reason="authored probe requires POSIX file guards")
def test_dispatch_boundary_reaudits_payload_after_builder(tmp_path, monkeypatch):
    from scripts import research_model_probe as probe
    from tests.test_research_authored_probe import fixture, worker
    from tests.test_research_model_probe import _install_service

    request, _, path = fixture(tmp_path)
    _, _, bundle = delivery_fixture()
    original = probe.build_payload

    def omit(*args, **kwargs):
        payload, estimate = original(*args, **kwargs)
        payload.pop("reviewed_inputs")
        return payload, estimate

    # Isolate the final dispatch guard from packet construction, tested separately.
    monkeypatch.setattr(probe, "_validate_authored_context", lambda *args: bundle)
    monkeypatch.setattr(probe, "build_payload", omit)
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.usage.total_tokens == 0
    assert result.stop_reason == "model_probe_failed"


@pytest.mark.skipif(os.name != "posix", reason="authored probe requires POSIX file guards")
def test_scoped_probe_keeps_operating_output_but_withholds_equity(tmp_path, monkeypatch):
    from scripts import research_model_probe as probe
    from tests.test_research_authored_probe import fixture, worker
    from tests.test_research_model_probe import _install_service

    request, context, path = fixture(tmp_path)
    _, _, bundle = delivery_fixture()
    monkeypatch.setattr(probe, "_validate_authored_context", lambda *args: bundle)
    calls = _install_service(monkeypatch, data=context["proposal"])
    result = worker(tmp_path, path)(request)
    assert len(calls) == 1 and result.stop_reason == "model_probe_completed"
    calculation = read_json(request.output_dir / "calculation_result.json")
    assert "enterprise_value" in calculation["result"] and "equity_value" not in calculation["result"]
    assert calculation["model_result_scope"]["funding_assessment"]["status"] == "not_assessed"
    assert calculation["model_result_scope"]["operating_asset_value"]["status"] == "conditional"
    assert read_json(request.output_dir / "provenance.json")["material_coverage"]["complete"]


def test_optional_frozen_packet_now_delivers_both_previously_missing_inputs():
    from cli.research import load_request

    root = Path(__file__).resolve().parents[1] / "reports/RESEARCH_MODEL_20260918"
    context_path = root / "compiled_model_1/authored_context.json"
    if not context_path.is_file():
        pytest.skip("optional immutable local research packet is not in a clean checkout")
    packet = root / "scenario_packet_1"
    request = load_request(packet / "request.json")
    raw = read_bytes(packet / "evidence.json")
    snapshot = EvidenceSnapshot.model_validate_json(raw)
    context = read_json(context_path)
    before = digest(context)
    bundle = _validate_authored_context(context, request, snapshot, raw, context["review_sha256"], packet)
    assert bundle is not None
    payload, _ = build_payload(request, snapshot, context, bundle)
    assert require_material_coverage(canonical_json(payload), bundle).complete
    rows = {item.id: item.text for item in bundle.source_material}
    assert "10-year\n4.95\n4.96\n4.97\n5.00\n5.01" in rows["treasury-table"]
    assert "Semiconductor\n66\n1.52" in rows["beta-row"]
    assert {item.scenario_id for item in bundle.terminal_derivations} == {"base", "downside", "upside"}
    assert digest(read_json(context_path)) == before


def test_optional_compilation_publishes_scoped_memo_and_actual_payload_audit(tmp_path):
    from scripts.research_nvda_scenarios import compile_reviewed

    root = Path(__file__).resolve().parents[1] / "reports/RESEARCH_MODEL_20260918"
    packet = root / "scenario_packet_1"
    review = root / "compiled_model_1/review.json"
    if not review.is_file():
        pytest.skip("optional immutable local research packet is not in a clean checkout")
    before = {item.name: sha256(read_bytes(item)).hexdigest() for item in packet.iterdir() if item.is_file()}
    output = tmp_path / "new-compilation"
    compile_reviewed(packet, review, output)
    coverage = read_json(output / "material_coverage.json")
    assert coverage["coverage"]["complete"]
    assert coverage["mode"] == "offline_preflight" and coverage["dispatched"] is False
    for case in read_json(output / "scoped_results.json").values():
        assert "enterprise_value" in case["result"] and "equity_value" not in case["result"]
        assert case["model_result_scope"]["opening_date_alignment"]["status"] == "not_assessed"
    memo = (output / "valuation_memo.md").read_text()
    assert "Equity/per-share conclusions are withheld" in memo
    assert "$/current share" not in memo
    assert {item.name: sha256(read_bytes(item)).hexdigest() for item in packet.iterdir() if item.is_file()} == before
