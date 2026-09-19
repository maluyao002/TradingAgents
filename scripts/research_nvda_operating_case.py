"""Prepare an offline, draft-only FY27 NVDA operating-scenario package.

The generic operating-scenarios boundary performs the only operating arithmetic.
This adapter authenticates frozen inputs and builds source-linked anchors and
analyst stress inputs; it never creates forecast totals, DCF, or valuation output.
"""
from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.financial_case import FinancialCase, evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    ForecastSourceMaterial,
    HistoricalOperatingAnchor,
    OperatingScenario,
    OperatingScenarioAssumption,
    OperatingScenarioPackage,
    OperatingScenarioPeriod,
    evaluate_operating_scenarios,
)
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)

D = Decimal
# Values are unscaled USD. Q3 is held at the management midpoint; Q4
# growth/opex/margin are arbitrary analyst stress inputs, not calibrated forecasts.
_SCENARIOS = (
    ("downside", "Downside", D("102600000000"), D(".69"), D("10580000000")),
    ("base", "Base", D("113400000000"), D(".71"), D("10120000000")),
    ("upside", "Upside", D("124200000000"), D(".72"), D("9936000000")),
)


def _inventory(directory: Path) -> dict[str, bytes]:
    manifest = parse_json(read_bytes(directory / "manifest.json"))
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("source artifact inventory is missing")
    result = {}
    for name, expected in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid source artifact path")
        raw = read_bytes(directory / name)
        if not isinstance(expected, str) or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("source artifact hash mismatch")
        result[name] = raw
    return result


def _json(blobs: dict[str, bytes], name: str) -> Any:
    if name not in blobs:
        raise ValueError(f"source artifact is missing: {name}")
    return parse_json(blobs[name])


def _fact(snapshot: EvidenceSnapshot, identifier: str, metric: str) -> dict[str, Any]:
    for fact in snapshot.facts:
        if fact.id == identifier:
            if fact.metric != metric or fact.unit != "USD" or fact.source_id != "nvda-q2-release":
                raise ValueError(f"fact selector mismatch: {identifier}")
            return fact.model_dump(mode="json")
    raise ValueError(f"required fact is absent: {identifier}")


def _material(snapshot: EvidenceSnapshot, source_id: str, first: str, last: str,
              identifier: str, context: str) -> ForecastSourceMaterial:
    source = next((item for item in snapshot.sources if item.id == source_id), None)
    if source is None or not source.content:
        raise ValueError(f"frozen source is absent: {source_id}")
    start = source.content.find(first)
    end = source.content.find(last, start)
    if start < 0 or end < 0:
        raise ValueError(f"exact source passage is absent: {identifier}")
    end += len(last)
    return ForecastSourceMaterial(id=identifier, source_id=source_id, source_sha256=source.content_sha256,
        start=start, end=end, text=source.content[start:end], context=context)


def _materials(snapshot: EvidenceSnapshot) -> tuple[ForecastSourceMaterial, ...]:
    return (
        _material(snapshot, "nvda-q2-release", "Outlook\nNVIDIA’s outlook", "GAAP and non-GAAP operating expenses are expected to be approximately $9.2 billion and $9.0 billion, respectively.", "q3_release_outlook", "Exact Q3 revenue, China exclusion, GAAP/non-GAAP margin labels, and opex outlook."),
        _material(snapshot, "nvda-q2-call", "Let me turn to the outlook for the third quarter.", "as supply of Vera Rubin grows over time.", "q3_call_revenue", "Exact management Q3 revenue outlook and Q4 hyperscale context."),
        _material(snapshot, "nvda-q2-call", "Many of you have expressed concerns regarding our gross margins as component costs have risen significantly.", "increase the capacity our road map requires. ", "memory_pricing_q4_margin", "Complete contiguous memory-cost, pricing, GAAP/non-GAAP margin, Q4-bottom, Q1 price-action, and supplier-capacity passage."),
        _material(snapshot, "nvda-q2-filing", "Fiscal year 2027 is a 53-week year", "will be a 14-week quarter.", "fy27_calendar", "Exact 53-week, last-Sunday-in-January, and 14-week-Q4 disclosure."),
        _material(snapshot, "nvda-q2-filing", "Guarantees\nLand, power, and shell guarantees for AI clouds", "Total $ 108.5", "guarantees", "Exact $3.5bn/$105bn maximum-gross-exposure quantities, SB Energy trigger/term context, and total; not expected losses."),
    )


def _validate(case_dir: Path, preflight_dir: Path):
    case_blobs, packet_blobs, preflight_blobs = _inventory(case_dir), _inventory(case_dir / "scenario_packet"), _inventory(preflight_dir)
    packet_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in packet_blobs.items()}
    if _json(case_blobs, "scenario_packet_hashes.json") != packet_hashes:
        raise ValueError("case and scenario packet inventories differ")
    if _json(preflight_blobs, "evidence.json") != _json(packet_blobs, "evidence.json"):
        raise ValueError("preflight evidence does not match the frozen scenario packet")
    case = _json(case_blobs, "financial_case.json")
    if _json(preflight_blobs, "financial_case.json") != case:
        raise ValueError("preflight financial case does not match the frozen case")
    envelope = _json(preflight_blobs, "case_input.json")
    if envelope.get("case") != case or envelope.get("review") not in (None,):
        raise ValueError("preflight case envelope does not match the unreviewed frozen case")
    request = ResearchRequest.model_validate(_json(preflight_blobs, "request.json"))
    if request.backend != "replay":
        raise ValueError("preflight request is not replay-only")
    return case_blobs, packet_blobs, preflight_blobs, FinancialCase.model_validate(case), EvidenceSnapshot.model_validate(_json(preflight_blobs, "evidence.json")), request


def _a(value: Decimal, classification: str, evidence_id: str, rationale: str) -> OperatingScenarioAssumption:
    return OperatingScenarioAssumption(value=value, classification=classification,
        evidence_ids=(evidence_id,), rationale=rationale)


def _package(case: FinancialCase, snapshot: EvidenceSnapshot, request: ResearchRequest,
             diagnostics: dict[str, Any]) -> OperatingScenarioPackage:
    revenue = _fact(snapshot, "nvda-revenue-h1", "revenue")
    op_income = _fact(snapshot, "nvda-operating_income-h1", "operating_income")
    scenarios = []
    for identifier, label, q4_rev, q4_gm, q4_opex in _SCENARIOS:
        q3 = OperatingScenarioPeriod(id="q3", fiscal_label="FY2027 Q3", period_start=date(2026, 7, 27), period_end=date(2026, 10, 25), accounting_basis="US GAAP",
            revenue=_a(D("108000000000"), "management_guidance_anchor", "q3_release_outlook", "Management midpoint anchor of $108bn. The stated ±2% guidance uncertainty is retained as a limitation but deliberately not varied across these Q4 sensitivity cases."),
            gross_margin=_a(D(".74"), "management_guidance_anchor", "q3_release_outlook", "Management midpoint anchor of 74.0% GAAP gross margin. The stated ±50bp uncertainty is retained as a limitation but deliberately not varied across these Q4 sensitivity cases."),
            opex=_a(D("9200000000"), "management_guidance_anchor", "q3_release_outlook", "Management's approximate Q3 GAAP opex anchor; no range was supplied."))
        growth = {"downside": "−5%", "base": "+5%", "upside": "+15%"}[identifier]
        opex_growth = {"downside": "+15%", "base": "+10%", "upside": "+8%"}[identifier]
        q4 = OperatingScenarioPeriod(id="q4", fiscal_label="FY2027 Q4", period_start=date(2026, 10, 26), period_end=date(2027, 1, 31), accounting_basis="US GAAP",
            revenue=_a(q4_rev, "analyst_assumption", "q3_call_revenue", f"Explicit arbitrary analyst stress: Q4 revenue {growth} versus corresponding Q3 point; neither guidance, consensus, probability, nor empirically calibrated forecast."),
            gross_margin=_a(q4_gm, "analyst_assumption", "memory_pricing_q4_margin", "Explicit analyst stress informed by the complete memory-cost/pricing and Q4 71–72% bottom passage. Q4 is not separately GAAP-labelled; no non-GAAP figure is silently substituted for GAAP."),
            opex=_a(q4_opex, "analyst_assumption", "q3_release_outlook", f"Explicit arbitrary analyst stress: Q4 GAAP opex {opex_growth} versus $9.2bn; neither guidance, consensus, probability, nor empirically calibrated forecast."))
        scenarios.append(OperatingScenario(id=identifier, label=label,
            rationale="Conditional operating stress only: Q3 is held at the same management midpoint for comparable Q4 sensitivity; Q4 varies demand, memory-cost/pricing pressure, and GAAP opex without forecast acceptance or margin clearance.",
            rationale_evidence_ids=("q3_release_outlook", "memory_pricing_q4_margin"),
            falsifier="Reported Q3/Q4 revenue, GAAP gross margin, GAAP opex, China treatment, or memory-price/pass-through conditions materially differ from these conditional inputs.",
            falsifier_evidence_ids=("q3_release_outlook", "memory_pricing_q4_margin"), fiscal_year_end=date(2027, 1, 31), periods=(q3, q4)))
    base_diagnostic = next(item for item in diagnostics["observations"] if item["scenario"] == "base")
    return OperatingScenarioPackage(ticker="NVDA", cutoff=request.cutoff, author_id="offline_nvda_operating_case_preparer",
        case_sha256=digest(case.model_dump(mode="json")), evidence_sha256=evidence_snapshot_sha256(snapshot),
        historical_anchor=HistoricalOperatingAnchor(fiscal_label="FY2027 H1 actual", case_opening_date=date(2026, 7, 26), revenue_fact=revenue, operating_income_fact=op_income),
        source_material=_materials(snapshot), scenarios=tuple(scenarios), review=None,
        limitations=("Numerical outputs require an independent hash-bound review after package finalization.", "FY2027 is a reported 53-week year ending the last Sunday in January; explicit Q3/Q4 dates are derived under that convention, with reported 14-week Q4.", "Q3 is held at management's midpoint; its ±2% revenue and ±50bp GAAP gross-margin guidance uncertainty is not modeled across the Q4 sensitivity cases.", base_diagnostic["interpretation"], "Q3 outlook excludes Data Center compute revenue from China; this remains retained in every scenario.", "Observed July 26, 2026 anchors have an UNOBSERVED bridge to the local request cutoff, not zero.", "No DCF, target, cash-flow, valuation, equity, per-share, funding, or terminal output is requested or supplied."))


def _ledger(request: ResearchRequest) -> dict[str, Any]:
    cutoff = request.cutoff.astimezone(ZoneInfo(request.timezone)).date().isoformat()
    bridge = f"Observed July 26, 2026 data have no observed bridge to the {cutoff} local request cutoff; UNOBSERVED is not zero."
    reasons = {"capital_and_liquidity": "Cash, securities, debt, reserves, and restrictions are unreconciled.", "shares": "Basic shares are not diluted point-in-time capitalization.", "realizability": "Lock-ups, investments, and tax/repatriation constraints prevent an equity bridge.", "tax": "No scenario tax forecast is claimed.", "working_capital": "Mixed rows and timing remain unresolved.", "commitments_and_guarantees": "Commitment and guarantee timing/realizability remain blocked; consult the preserved case commitments and exact guarantee source material rather than treating any exposure as expected loss, debt, or cash flow."}
    return {"schema_version": 1, "status": "draft_unreviewed_machine_closure_ledger", "local_cutoff_date": cutoff,
        "entries": [{"area": area, "status": "blocked", "bridge_to_request_cutoff": "UNOBSERVED", "reason": bridge + " " + reason} for area, reason in reasons.items()],
        "model_output_scopes": {"operating_asset_value": "blocked", "equity_per_share_value": "blocked", "funding_assessment": "blocked", "opening_date_alignment": "blocked"}}


def _derived_observed_case(case: FinancialCase, snapshot: EvidenceSnapshot) -> FinancialCase:
    """Create a fresh case at the exact reported H1 end without touching sources."""
    h1 = _fact(snapshot, "nvda-revenue-h1", "revenue")
    observed_end = date.fromisoformat(h1["period_end"])
    return FinancialCase.model_validate({**case.model_dump(mode="json"), "opening_date": observed_end})


def _percent(value: Decimal) -> str:
    return f"{value * D(100):.12f}".rstrip("0").rstrip(".") + "%"


def _period_diagnostics() -> dict[str, Any]:
    q3_revenue, q3_weeks, q4_weeks = D("108000000000"), D(13), D(14)
    observations = []
    for identifier, _, q4_revenue, _, _ in _SCENARIOS:
        quarter_change = q4_revenue / q3_revenue - D(1)
        weekly_change = (q4_revenue / q4_weeks) / (q3_revenue / q3_weeks) - D(1)
        observation = {"scenario": identifier, "q4_vs_q3_revenue_growth": _percent(quarter_change), "q4_vs_q3_per_week_revenue_change": _percent(weekly_change)}
        if identifier == "base":
            observation["interpretation"] = f"A {_percent(quarter_change)} aggregate Q4/Q3 revenue input is a {_percent(weekly_change)} per-week comparison because Q4 has 14 weeks and Q3 has 13; it does not claim uniform weekly growth."
        observations.append(observation)
    return {
        "classification": "input-comparability-diagnostic_not_forecast_output",
        "q3_weeks": int(q3_weeks),
        "q4_weeks": int(q4_weeks),
        "q3_anchor_usd": "108000000000",
        "observations": observations,
    }


def prepare(case_dir: Path, preflight_dir: Path, output: Path) -> dict[str, Any]:
    """Validate immutable sources, then write a fresh generic-schema draft only."""
    case_dir, preflight_dir, output = case_dir.resolve(), preflight_dir.resolve(), output.resolve()
    if output.exists() or output in {case_dir, preflight_dir} or case_dir in output.parents or preflight_dir in output.parents:
        raise ValueError("output must be a new directory outside both source directories")
    case_blobs, packet_blobs, preflight_blobs, source_case, snapshot, request = _validate(case_dir, preflight_dir)
    case = _derived_observed_case(source_case, snapshot)
    diagnostics = _period_diagnostics()
    package = _package(case, snapshot, request, diagnostics)
    result = evaluate_operating_scenarios(package, case, snapshot)
    if result.reviewed or result.calculated_values:
        raise ValueError("draft package unexpectedly exposed reviewed numerical output")
    envelope = deepcopy(_json(preflight_blobs, "case_input.json"))
    envelope["review"] = None
    envelope["case"] = case.model_dump(mode="json")
    envelope["operating_scenarios"] = package.model_dump(mode="json")
    replay = request.model_dump(mode="json")
    replay.update({"backend": "replay", "financial_case_path": str(output / "case_input.json"), "evidence_path": str(output / "evidence.json"), "output_dir": str(output / "run")})
    provenance = {"case_manifest_hashes": {k: hashlib.sha256(v).hexdigest() for k, v in case_blobs.items()}, "scenario_packet_hashes": {k: hashlib.sha256(v).hexdigest() for k, v in packet_blobs.items()}, "preflight_manifest_hashes": {k: hashlib.sha256(v).hexdigest() for k, v in preflight_blobs.items()}, "source_case_sha256": digest(source_case.model_dump(mode="json")), "derived_observed_case_sha256": digest(case.model_dump(mode="json")), "derived_case_change": "opening_date only: reset from source case opening date to exact reported H1 end; inherited gaps and assessments are preserved", "input_relation": "all source manifests and case/packet/preflight hashes matched before creation"}
    outputs = {"case_input.json": canonical_json(envelope), "financial_case.json": canonical_json(case), "evidence.json": preflight_blobs["evidence.json"], "request.json": canonical_json(ResearchRequest.model_validate(replay)), "operating_scenario_package.json": result.artifacts["operating_scenario_package.json"], "operating_scenario_context.json": result.artifacts["operating_scenario_context.json"], "operating_scenario_diagnostics.json": canonical_json(diagnostics), "closure_ledger.json": canonical_json(_ledger(request)), "provenance.json": canonical_json(provenance)}
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({"artifact_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()}, "status": "draft_unreviewed_operating_scenarios", "scope": "Offline replay-only operating-scenario preparation; independent review follows finalization."}))
    return {"status": "draft_unreviewed_operating_scenarios", "output": str(output), "reviewed": False, "model_calls": 0, "output_schema": {"case_input_root_fields": ["case", "review", "source_passages", "operating_scenarios"], "operating_scenarios": "OperatingScenarioPackage with review=null", "review_binding": "external independent hash-bound review required after finalized bundle"}}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--preflight-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(canonical_json(prepare(args.case_dir, args.preflight_dir, args.output)).decode())


if __name__ == "__main__":
    main()
