"""Offline regressions for reviewed cash-flow bridge provenance and timing."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from tests.test_research_cashflow_bridge import _reviewed, bridge_setup
from tradingagents.research.cashflow_bridge import (
    CommitmentOverlapAssumption,
    _eligible_fact_map,
    evaluate_cashflow_bridge,
)
from tradingagents.research.contracts import InstrumentIdentity, SourceDocument
from tradingagents.research.operating_scenarios import (
    OperatingScenarioReview,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import digest, parse_json


def _incremental_setup(tmp_path, due_start: date, due_end: date):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path)
    items = tuple(
        item.model_copy(
            update={
                "timing": "known",
                "due_start": due_start,
                "due_end": due_end,
                "overlap": "none",
                "treatment": "deduct_incrementally",
            }
        )
        if item.id == "cash-commitment-supply-h2"
        else item
        for item in case.commitments.items
    )
    case = case.model_copy(
        update={"commitments": case.commitments.model_copy(update={"items": items})}
    )
    case_hash = digest(case.model_dump(mode="json"))
    operating_package = operating_package.model_copy(
        update={
            "case_sha256": case_hash,
            "review": None,
        }
    )
    operating_package = operating_package.model_copy(
        update={
            "review": _operating_review(operating_package),
        }
    )
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    treatments = tuple(
        CommitmentOverlapAssumption(
            commitment_id=item.commitment_id,
            treatment="incremental_deduction",
            period_id="q3",
            incremental_cash_outflow=Decimal("40"),
            rationale="Synthetic dated non-overlapping commitment.",
        )
        if item.commitment_id == "cash-commitment-supply-h2"
        else item
        for item in bridge.commitment_assumptions
    )
    bridge = bridge.model_copy(
        update={
            "case_sha256": case_hash,
            "operating_package_sha256": operating_scenario_package_sha256(operating_package),
            "commitment_assumptions": treatments,
            "review": None,
        }
    )
    return snapshot, case, operating_package, operating, bridge


def _operating_review(package):
    """Refresh the fixture's independent review after a case or scenario edit."""
    return OperatingScenarioReview(
        reviewer_id="synthetic_operating_reviewer",
        reviewed_at="2026-09-19T12:00:00Z",
        package_sha256=operating_scenario_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        decision="conditional_operating_scenarios",
        limitations=("Synthetic mechanics review is not economic approval.",),
    )


@pytest.mark.parametrize(
    ("due_start", "due_end", "accepted"),
    [
        (date(2026, 9, 30), date(2026, 10, 15), True),
        (date(2026, 6, 1), date(2026, 7, 1), True),
        (date(2027, 1, 1), date(2027, 3, 31), False),
    ],
)
def test_incremental_commitment_due_range_overlaps_assigned_period(
    tmp_path, due_start, due_end, accepted
):
    snapshot, case, _, operating, bridge = _incremental_setup(tmp_path, due_start, due_end)
    if not accepted:
        with pytest.raises(ValueError, match="(?i)(due|timing|overlap|period)"):
            evaluate_cashflow_bridge(bridge, case, snapshot, operating)
        return
    result = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    payload = parse_json(result.artifacts["cashflow_bridge_result.json"])
    assert Decimal(
        payload["scenarios"][0]["periods"][0]["incremental_commitment_deduction"]
    ) == Decimal("40")


def _two_scenario_setup(tmp_path, *, mismatch=None):
    snapshot, case, operating_package, operating, bridge = _incremental_setup(
        tmp_path, date(2026, 7, 1), date(2026, 9, 30)
    )
    base = operating_package.scenarios[0]
    other_periods = base.periods
    cash_periods = bridge.scenarios[0].periods
    if mismatch == "ids":
        other_periods = tuple(
            period.model_copy(update={"id": f"other_{period.id}"}) for period in other_periods
        )
        cash_periods = tuple(
            period.model_copy(update={"period_id": f"other_{period.period_id}"})
            for period in cash_periods
        )
    elif mismatch == "dates":
        other_periods = (
            other_periods[0].model_copy(update={"period_end": date(2026, 10, 15)}),
            other_periods[1].model_copy(update={"period_start": date(2026, 10, 16)}),
        )
        cash_periods = (
            cash_periods[0].model_copy(update={"period_end": date(2026, 10, 15)}),
            cash_periods[1].model_copy(update={"period_start": date(2026, 10, 16)}),
        )
    other = base.model_copy(
        update={
            "id": "upside",
            "label": "Synthetic upside",
            "periods": other_periods,
        }
    )
    operating_package = operating_package.model_copy(
        update={
            "scenarios": (base, other),
            "review": None,
        }
    )
    operating_package = operating_package.model_copy(
        update={
            "review": _operating_review(operating_package),
        }
    )
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    cash_other = bridge.scenarios[0].model_copy(
        update={
            "id": "upside",
            "periods": cash_periods,
        }
    )
    bridge = bridge.model_copy(
        update={
            "scenarios": (*bridge.scenarios, cash_other),
            "operating_package_sha256": operating_scenario_package_sha256(operating_package),
        }
    )
    return snapshot, case, operating, bridge


@pytest.mark.parametrize("mismatch", ["ids", "dates"])
def test_global_incremental_deduction_requires_common_period_mapping(tmp_path, mismatch):
    snapshot, case, operating, bridge = _two_scenario_setup(tmp_path, mismatch=mismatch)
    with pytest.raises(ValueError, match="(?i)(period|mapping|scenario)"):
        evaluate_cashflow_bridge(bridge, case, snapshot, operating)


def test_common_period_mapping_deducts_same_amount_in_every_scenario(tmp_path):
    snapshot, case, operating, bridge = _two_scenario_setup(tmp_path)
    result = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    payload = parse_json(result.artifacts["cashflow_bridge_result.json"])
    assert {
        scenario["id"]: tuple(
            Decimal(period["incremental_commitment_deduction"]) for period in scenario["periods"]
        )
        for scenario in payload["scenarios"]
    } == {"base": (Decimal("40"), Decimal("0")), "upside": (Decimal("40"), Decimal("0"))}


@pytest.mark.parametrize(
    ("kind", "accession", "url", "eligible"),
    [
        (
            "filing",
            "0001045810-26-000001",
            "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/report.htm",
            True,
        ),
        ("news", "0001045810-26-000001", "https://example.test/mutable-news", False),
        (
            "filing",
            "0000002488-26-000001",
            "https://www.sec.gov/Archives/edgar/data/2488/000000248826000001/report.htm",
            False,
        ),
        (
            "filing",
            "0001045810-26-000002",
            "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/report.htm",
            False,
        ),
    ],
)
def test_bridge_eligible_facts_obey_exact_late_sec_archive_binding(
    tmp_path, kind, accession, url, eligible
):
    snapshot, _, _, _ = bridge_setup(tmp_path)
    instrument = InstrumentIdentity(
        issuer="NVIDIA",
        cik="0001045810",
        exchange="NASDAQ",
        share_class="common",
        quote_currency="USD",
        reporting_currency="USD",
        identity_source="filing",
    )
    source = SourceDocument(
        id="late-source",
        url=url,
        title="Synthetic source",
        publisher="SEC",
        retrieved_at=snapshot.cutoff + timedelta(days=1),
        published_at=snapshot.cutoff - timedelta(days=1),
        content="Synthetic archived filing text",
        content_sha256=sha256(b"Synthetic archived filing text").hexdigest(),
        kind=kind,
        accession=accession,
    )
    fact = snapshot.facts[0].model_copy(update={"id": "late-fact", "source_id": source.id})
    snapshot = snapshot.model_copy(
        update={
            "instrument": instrument,
            "sources": (*snapshot.sources, source),
            "facts": (*snapshot.facts, fact),
        }
    )
    assert ("late-fact" in _eligible_fact_map(snapshot)) is eligible


def test_cashflow_calculations_retain_operating_assumption_and_commitment_evidence(tmp_path):
    snapshot, case, _, operating, bridge = _incremental_setup(
        tmp_path, date(2026, 7, 1), date(2026, 9, 30)
    )
    result = evaluate_cashflow_bridge(_reviewed(bridge), case, snapshot, operating)
    by_id = {value.id: value for value in result.calculated_values}
    source_operating = next(
        value
        for value in operating.calculated_values
        if value.id == "operating_scenario.base.q3.operating_income"
    )
    assumption_ids = {
        evidence_id
        for assumption in (
            bridge.scenarios[0].periods[0].tax_rate,
            bridge.scenarios[0].periods[0].depreciation_amortization,
            bridge.scenarios[0].periods[0].capex,
            bridge.scenarios[0].periods[0].change_in_operating_working_capital,
        )
        for evidence_id in assumption.evidence_ids
    }
    required = set(source_operating.evidence_ids) | assumption_ids | {"cash-commitment-supply"}
    period = by_id["cashflow_bridge.base.q3.conditional_cash_flow"]
    assert required <= set(period.evidence_ids)
    for suffix in ("future_period_cash_flow", "fiscal_total.conditional_cash_flow"):
        assert required <= set(by_id[f"cashflow_bridge.base.{suffix}"].evidence_ids)


def test_reviewed_bridge_fails_closed_without_operating_income_catalog_entry(tmp_path):
    snapshot, case, _, operating, bridge = _incremental_setup(
        tmp_path, date(2026, 7, 1), date(2026, 9, 30)
    )
    missing_id = "operating_scenario.base.q3.operating_income"
    incomplete = replace(
        operating,
        calculated_values=tuple(
            value for value in operating.calculated_values if value.id != missing_id
        ),
    )
    assert len(incomplete.calculated_values) == len(operating.calculated_values) - 1
    with pytest.raises(ValueError, match="operating-income calculation ancestry"):
        evaluate_cashflow_bridge(_reviewed(bridge), case, snapshot, incomplete)
