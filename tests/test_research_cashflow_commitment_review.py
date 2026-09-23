"""Offline regressions for commitment provenance and bridge-horizon coverage."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.test_research_cashflow_bridge import _reviewed, bridge_setup
from tests.test_research_cashflow_review_regressions import (
    _incremental_setup,
    _operating_review,
)
from tradingagents.research.cashflow_bridge import (
    CashFlowBridgePackage,
    evaluate_cashflow_bridge,
)
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import digest, parse_json


def _rebind(snapshot, case, operating_package, bridge):
    """Rebuild both independent reviews after changing a case or bridge."""
    case_hash = digest(case.model_dump(mode="json"))
    evidence_hash = evidence_snapshot_sha256(snapshot)
    operating_package = operating_package.model_copy(
        update={"case_sha256": case_hash, "evidence_sha256": evidence_hash, "review": None}
    )
    operating_package = operating_package.model_copy(
        update={"review": _operating_review(operating_package)}
    )
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    assert operating.reviewed
    bridge = bridge.model_copy(
        update={
            "case_sha256": case_hash,
            "evidence_sha256": evidence_hash,
            "operating_package_sha256": operating_scenario_package_sha256(operating_package),
            "review": None,
        }
    )
    return operating, _reviewed(bridge)


def _replace_source(case, commitment_id, **updates):
    items = tuple(
        item.model_copy(update=updates) if item.id == commitment_id else item
        for item in case.commitments.items
    )
    return case.model_copy(
        update={"commitments": case.commitments.model_copy(update={"items": items})}
    )


def _replace_source_and_fact(snapshot, case, commitment_id, field, value):
    fact_id = next(item.fact_id for item in case.commitments.items if item.id == commitment_id)
    snapshot = snapshot.model_copy(
        update={
            "facts": tuple(
                fact.model_copy(update={field: value}) if fact.id == fact_id else fact
                for fact in snapshot.facts
            )
        }
    )
    case = _replace_source(case, commitment_id, **{field: value})
    case = case.model_copy(update={"snapshot_sha256": evidence_snapshot_sha256(snapshot)})
    return snapshot, case


@pytest.mark.parametrize(
    ("source_updates", "error"),
    [
        ({"amount_basis": "maximum_exposure", "treatment": "not_assessed"}, "contractual cash"),
        ({"amount_basis": "other"}, "contractual cash"),
        ({"overlap": "opex", "treatment": "already_reflected"}, "source treatment"),
        ({"overlap": "unknown", "treatment": "not_assessed"}, "source treatment"),
        ({"currency": "EUR"}, "USD units and currency"),
        ({"unit": "EUR"}, "USD units and currency"),
    ],
    ids=[
        "maximum-exposure",
        "other-basis",
        "already-reflected",
        "not-assessed",
        "non-USD-currency",
        "non-USD-unit",
    ],
)
def test_incremental_deduction_requires_compatible_usd_source(tmp_path, source_updates, error):
    snapshot, case, operating_package, _, bridge = _incremental_setup(
        tmp_path, date(2026, 7, 1), date(2026, 9, 30)
    )
    if "currency" in source_updates or "unit" in source_updates:
        field, value = next(iter(source_updates.items()))
        snapshot, case = _replace_source_and_fact(
            snapshot, case, "cash-commitment-supply-h2", field, value
        )
    else:
        case = _replace_source(case, "cash-commitment-supply-h2", **source_updates)
    operating, bridge = _rebind(snapshot, case, operating_package, bridge)

    with pytest.raises(ValueError, match=error):
        evaluate_cashflow_bridge(bridge, case, snapshot, operating)


def test_incremental_contractual_usd_source_remains_conditional(tmp_path):
    snapshot, case, operating_package, _, bridge = _incremental_setup(
        tmp_path, date(2026, 7, 1), date(2026, 9, 30)
    )
    operating, bridge = _rebind(snapshot, case, operating_package, bridge)
    result = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    payload = parse_json(result.artifacts["cashflow_bridge_result.json"])

    assert result.reviewed
    assert payload["commitment_overlap"]["items"]
    assert Decimal(
        payload["scenarios"][0]["periods"][0]["incremental_commitment_deduction"]
    ) == Decimal("40")
    assert (
        result.model_context["output_scope"]["conditional_cash_flow_bridge"]["status"]
        == "conditional"
    )
    assert result.model_context["output_scope"]["operating_asset_value"]["status"] == "blocked"


@pytest.mark.parametrize("no_commitments", [False, True], ids=["outside-horizon", "no-commitments"])
def test_empty_assumptions_allowed_for_empty_in_horizon_source_set(tmp_path, no_commitments):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path)
    if no_commitments:
        removed_ids = {item.id for item in case.commitments.items}
        conventions = tuple(
            convention
            for convention in case.conventions
            if not removed_ids.intersection(convention.affected_component_ids)
        )
        case = case.model_copy(
            update={
                "commitments": case.commitments.model_copy(update={"items": ()}),
                "conventions": conventions,
            }
        )
    else:
        case = case.model_copy(
            update={
                "commitments": case.commitments.model_copy(
                    update={
                        "items": tuple(
                            item.model_copy(update={"disclosed_timing": "next-year"})
                            for item in case.commitments.items
                        )
                    }
                )
            }
        )
    bridge = bridge.model_copy(update={"commitment_assumptions": ()})
    operating, bridge = _rebind(snapshot, case, operating_package, bridge)
    result = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    payload = parse_json(result.artifacts["cashflow_bridge_result.json"])

    assert result.reviewed
    assert payload["commitment_overlap"]["items"] == []
    assert all(
        Decimal(value) == 0
        for value in payload["commitment_overlap"]["reported_amounts_by_treatment"].values()
    )
    assert len(payload["commitment_overlap"]["outside_horizon_item_ids"]) == (
        0 if no_commitments else 7
    )


@pytest.mark.parametrize("omit_all", [False, True], ids=["one-omission", "all-omitted"])
def test_nonempty_in_horizon_commitments_require_exact_assumption_coverage(tmp_path, omit_all):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path)
    remaining = () if omit_all else bridge.commitment_assumptions[1:]
    bridge = bridge.model_copy(update={"commitment_assumptions": remaining})
    operating, bridge = _rebind(snapshot, case, operating_package, bridge)

    with pytest.raises(ValueError, match="exactly cover the disclosed bridge horizon"):
        evaluate_cashflow_bridge(bridge, case, snapshot, operating)


def test_commitment_assumptions_field_remains_required(tmp_path):
    _, _, _, bridge = bridge_setup(tmp_path)
    payload = bridge.model_dump(mode="json", exclude={"commitment_assumptions"})

    with pytest.raises(ValidationError, match="commitment_assumptions"):
        CashFlowBridgePackage.model_validate(payload)


@pytest.mark.parametrize(
    "commitment_id", ["cash-commitment-cloud-h2", "cash-commitment-investments-h2"]
)
@pytest.mark.parametrize("field", ["currency", "unit"])
def test_nonincremental_non_usd_commitment_cannot_enter_usd_totals(tmp_path, commitment_id, field):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path)
    snapshot, case = _replace_source_and_fact(snapshot, case, commitment_id, field, "EUR")
    operating, bridge = _rebind(snapshot, case, operating_package, bridge)

    with pytest.raises(ValueError, match="USD units and currency"):
        evaluate_cashflow_bridge(bridge, case, snapshot, operating)
