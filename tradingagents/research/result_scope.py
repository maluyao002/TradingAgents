"""Conservative eligibility gates for Stage 1 model outputs.

This module deliberately describes what a calculation may show; it does not
validate the economics of a calculation or turn an absent assessment into an
approval.  In particular, a false ``external_funding_required`` flag and a
positive FCFF forecast do not make an equity result eligible.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from math import isfinite
from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, Identifier

EligibilityStatus = Literal["conditional", "blocked", "not_assessed"]


class ComponentEligibility(Contract):
    """Whether one distinct part of a model result may be shown."""

    status: EligibilityStatus
    reasons: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = ()

    @field_validator("reasons")
    @classmethod
    def nonblank_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not reason.strip() for reason in value):
            raise ValueError("reasons cannot contain blank text")
        return value


class ModelResultScope(Contract):
    """Eligibility of the independently assessed components of a result."""

    operating_asset_value: ComponentEligibility
    equity_per_share_value: ComponentEligibility
    funding_assessment: ComponentEligibility
    opening_date_alignment: ComponentEligibility


# These are the only model result fields this boundary understands.  Adding a
# new output upstream must therefore involve an explicit scope decision here.
_OPERATING_RESULT_FIELDS = frozenset({
    "forecasts",
    "explicit_period_present_value",
    "terminal_value_at_horizon",
    "terminal_value_present_value",
    "enterprise_value",
    "terminal_value_share_of_enterprise_value",
    "limitations",
    "scope",
    "units",
})
_EQUITY_RESULT_FIELDS = frozenset({
    "net_debt",
    "equity_value",
    "value_per_current_diluted_share",
})
_FORECAST_FIELDS = frozenset({
    "label",
    "revenue",
    "operating_profit_before_tax",
    "economic_sbc",
    "nopat",
    "depreciation_amortization",
    "capex",
    "working_capital",
    "change_in_working_capital",
    "fcff",
    "discount_years",
    "discount_factor",
    "present_value",
})
_RESULT_TEXT_FIELDS = frozenset({"limitations", "scope"})
_WRAPPER_FIELDS = frozenset({"status", "limitations", "valuation_method", "share_count_basis"})


def _filter_forecasts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        {name: deepcopy(period[name]) for name in _FORECAST_FIELDS if name in period}
        for period in value
        if isinstance(period, Mapping)
    ]


def _filter_operating_result(result: Mapping[str, object]) -> dict[str, object]:
    """Copy only named operating outputs, excluding arbitrary numeric fields."""
    output: dict[str, object] = {}
    for name in _OPERATING_RESULT_FIELDS:
        if name not in result:
            continue
        if name == "forecasts":
            output[name] = _filter_forecasts(result[name])
        elif name == "units" and isinstance(result[name], Mapping):
            # Scales are display metadata, not values, but still copy only the
            # known unit vocabulary rather than an upstream object wholesale.
            output[name] = {
                field: deepcopy(result[name][field])
                for field in ("currency", "amount_scale", "share_scale")
                if field in result[name]
            }
        else:
            output[name] = deepcopy(result[name])
    return output


def _filter_blocked_result(result: Mapping[str, object]) -> dict[str, object]:
    """Retain explanatory text but no numeric model output."""
    return {
        name: deepcopy(result[name])
        for name in _RESULT_TEXT_FIELDS
        if name in result
    }


def _filter_result(result: Mapping[str, object], scope: ModelResultScope) -> dict[str, object]:
    if scope.operating_asset_value.status != "conditional":
        return _filter_blocked_result(result)

    output = _filter_operating_result(result)
    if scope.equity_per_share_value.status == "conditional":
        output.update({
            name: deepcopy(result[name])
            for name in _EQUITY_RESULT_FIELDS
            if name in result
        })
    return output


def scope_calculation(calculation: dict, scope: ModelResultScope) -> dict:
    """Return a separately owned, eligibility-limited calculation presentation.

    Both a raw result dictionary and the engine's ``{"result": ...}`` wrapper
    are accepted.  Unknown fields are always withheld, including unknown numeric
    fields that may otherwise be interpreted as targets or returns.
    """
    if not isinstance(calculation, dict):
        raise TypeError("calculation must be a dict")

    serialized_scope = scope.model_dump(mode="json")
    if "status" in calculation or "result" in calculation:
        output = {
            name: deepcopy(calculation[name])
            for name in _WRAPPER_FIELDS
            if name in calculation
        }
        if isinstance(calculation.get("result"), Mapping):
            output["result"] = _filter_result(calculation["result"], scope)
        output["model_result_scope"] = serialized_scope
        return output
    output = _filter_result(calculation, scope)
    output["model_result_scope"] = serialized_scope
    return output


def _finite_enterprise_value(result: object) -> bool:
    if not isinstance(result, Mapping):
        return False
    value = result.get("enterprise_value")
    return isinstance(value, (Decimal, int, float)) and not isinstance(value, bool) and isfinite(value)


def conservative_scope(
    calculation: dict,
    *,
    missing_operating_inputs: tuple[str, ...] = (),
) -> ModelResultScope:
    """Provide the Stage 1 default without inferring any economic clearance."""
    if not isinstance(calculation, dict):
        raise TypeError("calculation must be a dict")
    if not isinstance(missing_operating_inputs, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in missing_operating_inputs
    ):
        raise ValueError("missing_operating_inputs must be a tuple of nonblank strings")

    result = calculation.get("result")
    operating_is_conditional = (
        calculation.get("status") == "illustrative"
        and _finite_enterprise_value(result)
        and not missing_operating_inputs
    )
    operating = (
        ComponentEligibility(
            status="conditional",
            reasons=("Operating output is illustrative only; this is not an economic approval.",),
        )
        if operating_is_conditional
        else ComponentEligibility(
            status="blocked",
            reasons=(
                (
                    "Operating output is blocked until missing inputs are supplied: "
                    + ", ".join(missing_operating_inputs)
                    if missing_operating_inputs
                    else "Operating output is blocked until an illustrative calculation includes a finite enterprise value."
                ),
            ),
        )
    )
    return ModelResultScope(
        operating_asset_value=operating,
        equity_per_share_value=ComponentEligibility(
            status="blocked",
            reasons=(
                "Equity and per-share output require reconciled equity inputs and an aligned opening date.",
            ),
        ),
        funding_assessment=ComponentEligibility(
            status="not_assessed",
            reasons=("Funding adequacy is not assessed by the Stage 1 scope.",),
        ),
        opening_date_alignment=ComponentEligibility(
            status="not_assessed",
            reasons=("Opening-date alignment is not assessed by the Stage 1 scope.",),
        ),
    )
