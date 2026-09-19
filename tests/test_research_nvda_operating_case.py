"""Mechanics-only regressions for the offline NVDA operating-case preparer."""
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from scripts.research_nvda_operating_case import _fact, prepare
from tests.test_research_case_scenarios import operating_setup
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.financial_case import FinancialCase, evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    OperatingScenarioReview,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import canonical_json, read_json


def _source(identifier, content):
    return SourceDocument(id=identifier, url=f"https://example.test/{identifier}", title="Synthetic mechanics-only frozen source", publisher="Synthetic issuer", retrieved_at="2026-09-17T10:00:00Z", published_at="2026-09-16T10:00:00Z", content=content, content_sha256=sha256(content.encode()).hexdigest(), availability="full_text")


def _inventory(directory, blobs):
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in blobs.items():
        (directory / name).write_bytes(raw)
    (directory / "manifest.json").write_bytes(canonical_json({"artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in blobs.items()}}))


def synthetic_inputs(tmp_path):
    """Build a complete mechanics-only packet; no local report artifact is read."""
    request, base_snapshot, _ = operating_setup(tmp_path / "base", reviewed=False)
    release = _source("nvda-q2-release", "Outlook\nNVIDIA’s outlook for the third quarter of fiscal 2027 is as follows:\nRevenue is expected to be $108.0 billion, plus or minus 2%. NVIDIA is not assuming any Data Center compute revenue from China in its outlook.\nGAAP and non-GAAP gross margins are expected to be 74.0%, plus or minus 50 basis points.\nGAAP and non-GAAP operating expenses are expected to be approximately $9.2 billion and $9.0 billion, respectively.")
    call = _source("nvda-q2-call", "Let me turn to the outlook for the third quarter. Synthetic demand grows\nas supply of Vera Rubin grows over time.\nMany of you have expressed concerns regarding our gross margins as component costs have risen significantly.\nWe expect margins to bottom in Q4 in the 71% to 72% range before price increases take effect in Q1.\nincrease the capacity our road map requires. ")
    filing = _source("nvda-q2-filing", "Fiscal year 2027 is a 53-week year, both ending on the last Sunday in January. The fourth quarter of fiscal year 2027\nwill be a 14-week quarter.\nGuarantees\nLand, power, and shell guarantees for AI clouds have maximum gross exposure of $3.5 billion.\nSB Energy guarantees are capped at a total of $105 billion; payment obligations are triggered upon certain defaults.\nTotal $ 108.5")
    h1 = (
        FinancialFact(id="nvda-revenue-h1", source_id=release.id, metric="revenue", value="177837", scale="1000000", unit="USD", currency="USD", basis="US GAAP", location="Synthetic H1 revenue", period_start="2026-01-26", period_end="2026-07-26", period_type="duration"),
        FinancialFact(id="nvda-operating_income-h1", source_id=release.id, metric="operating_income", value="117270", scale="1000000", unit="USD", currency="USD", basis="US GAAP", location="Synthetic H1 operating income", period_start="2026-01-26", period_end="2026-07-26", period_type="duration"),
    )
    snapshot = base_snapshot.model_copy(update={"sources": (*base_snapshot.sources, release, call, filing), "facts": (*base_snapshot.facts, *h1)})
    source_case = FinancialCase.model_validate(read_json(request.financial_case_path)["case"])
    case = FinancialCase.model_validate({**source_case.model_dump(mode="json"), "snapshot_sha256": evidence_snapshot_sha256(snapshot)})
    case_dir, preflight = tmp_path / "case", tmp_path / "preflight"
    packet = {"request.json": canonical_json(request), "evidence.json": canonical_json(snapshot)}
    _inventory(case_dir / "scenario_packet", packet)
    _inventory(case_dir, {"financial_case.json": canonical_json(case), "scenario_packet_hashes.json": canonical_json({name: sha256(raw).hexdigest() for name, raw in packet.items()})})
    _inventory(preflight, {"request.json": canonical_json(request), "evidence.json": canonical_json(snapshot), "financial_case.json": canonical_json(case), "case_input.json": canonical_json({"case": case})})
    return case_dir, preflight


def test_prepare_is_self_contained_and_preserves_sources(tmp_path):
    case, preflight = synthetic_inputs(tmp_path)
    before = {str(path): path.read_bytes() for root in (case, preflight) for path in root.rglob("*") if path.is_file()}
    output = tmp_path / "operating_case"
    result = prepare(case, preflight, output)
    assert result["status"] == "draft_unreviewed_operating_scenarios"
    assert not result["reviewed"] and result["model_calls"] == 0
    assert {str(path): path.read_bytes() for root in (case, preflight) for path in root.rglob("*") if path.is_file()} == before
    envelope = read_json(output / "case_input.json")
    package = envelope["operating_scenarios"]
    assert envelope["case"]["opening_date"] == "2026-07-26" and package["review"] is None
    assert [row["periods"][0]["revenue"]["value"] for row in package["scenarios"]] == ["108000000000"] * 3
    assert [row["periods"][1]["revenue"]["value"] for row in package["scenarios"]] == ["102600000000", "113400000000", "124200000000"]
    materials = {item["id"]: item["text"] for item in package["source_material"]}
    assert "margins to bottom in Q4" in materials["memory_pricing_q4_margin"]
    assert "$3.5 billion" in materials["guarantees"] and "$105" in materials["guarantees"]
    diagnostics = read_json(output / "operating_scenario_diagnostics.json")
    assert diagnostics["observations"][0]["q4_vs_q3_per_week_revenue_change"] == "-11.785714285714%"
    assert diagnostics["observations"][1]["q4_vs_q3_per_week_revenue_change"] == "-2.5%"
    context = read_json(output / "operating_scenario_context.json")
    assert context["context_kind"] == "operating_scenario_audit_only"
    assert diagnostics["observations"][1]["interpretation"] in package["limitations"]
    assert diagnostics["observations"][1]["interpretation"] not in context["limitations"]
    hashes = read_json(output / "manifest.json")["artifact_hashes"]
    assert all(sha256((output / name).read_bytes()).hexdigest() == expected for name, expected in hashes.items())
    with pytest.raises(ValueError, match="new directory"):
        prepare(case, preflight, output)


def test_generic_total_is_h1_plus_quarters_not_ttm(tmp_path):
    case, preflight = synthetic_inputs(tmp_path)
    output = tmp_path / "operating_case"
    prepare(case, preflight, output)
    package = OperatingScenarioPackage.model_validate(read_json(output / "operating_scenario_package.json"))
    review = OperatingScenarioReview(reviewer_id="independent_test_reviewer", reviewed_at=datetime(2026, 9, 20, tzinfo=timezone.utc), package_sha256=operating_scenario_package_sha256(package), case_sha256=package.case_sha256, evidence_sha256=package.evidence_sha256, decision="conditional_operating_scenarios", limitations=("Mechanical review only.",))
    result = evaluate_operating_scenarios(package.model_copy(update={"review": review}), FinancialCase.model_validate(read_json(output / "financial_case.json")), EvidenceSnapshot.model_validate(read_json(output / "evidence.json")))
    base = next(item for item in result.model_context["scenarios"] if item["id"] == "base")
    assert base["fiscal_total"]["revenue"] == 399237000000
    assert base["fiscal_total"]["revenue"] != 302970000000


def test_preflight_relation_and_fact_selector_fail_closed(tmp_path):
    case, preflight = synthetic_inputs(tmp_path)
    snapshot = EvidenceSnapshot.model_validate(read_json(preflight / "evidence.json"))
    with pytest.raises(ValueError, match="fact selector mismatch"):
        _fact(snapshot, "nvda-revenue-h1", "operating_income")
    evidence = preflight / "evidence.json"
    evidence.write_bytes(canonical_json({"tampered": True}))
    manifest = read_json(preflight / "manifest.json")
    manifest["artifact_hashes"]["evidence.json"] = sha256(evidence.read_bytes()).hexdigest()
    (preflight / "manifest.json").write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError, match="preflight evidence does not match"):
        prepare(case, preflight, tmp_path / "output")
    assert not (tmp_path / "output").exists()
