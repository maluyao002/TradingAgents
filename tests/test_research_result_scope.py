from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradingagents.research.result_scope import (
    ComponentEligibility,
    ModelResultScope,
    conservative_scope,
    scope_calculation,
)


def eligibility(status, reason="reason"):
    return ComponentEligibility(status=status, reasons=(reason,))


def scope(*, operating="conditional", equity="blocked", funding="not_assessed", opening="not_assessed"):
    return ModelResultScope(
        operating_asset_value=eligibility(operating),
        equity_per_share_value=eligibility(equity),
        funding_assessment=eligibility(funding),
        opening_date_alignment=eligibility(opening),
    )


def calculation():
    return {
        "forecasts": [{
            "label": "FY27", "revenue": Decimal("120"), "fcff": Decimal("12"),
            "present_value": Decimal("10"), "unknown_forecast_number": Decimal("99"),
        }],
        "explicit_period_present_value": Decimal("10"),
        "terminal_value_at_horizon": Decimal("200"),
        "terminal_value_present_value": Decimal("150"),
        "enterprise_value": Decimal("160"),
        "terminal_value_share_of_enterprise_value": Decimal("0.9375"),
        "net_debt": Decimal("20"),
        "equity_value": Decimal("140"),
        "value_per_current_diluted_share": Decimal("14"),
        "annualized_return": Decimal("0.15"),
        "external_funding_required": False,
        "unknown_numeric_target": Decimal("1000"),
        "limitations": ("illustrative",),
        "scope": "model scope",
        "units": {"currency": "USD", "amount_scale": Decimal("1e6"), "unexpected": 7},
    }


def illustrative_calculation(result=None):
    return {"status": "illustrative", "result": calculation() if result is None else result}


def test_component_eligibility_requires_nonempty_nonblank_reasons_and_known_statuses():
    with pytest.raises(ValidationError):
        ComponentEligibility(status="conditional", reasons=())
    with pytest.raises(ValidationError, match="blank"):
        ComponentEligibility(status="conditional", reasons=(" ",))
    with pytest.raises(ValidationError):
        ComponentEligibility(status="accepted", reasons=("reason",))


@pytest.mark.parametrize("component", ["operating_asset_value", "equity_per_share_value"])
def test_scope_boundary_revalidates_forged_eligibility(component):
    forged = scope().model_copy(update={
        component: ComponentEligibility.model_construct(status="conditional", reasons=()),
    })
    with pytest.raises(ValidationError):
        scope_calculation(calculation(), forged)


@pytest.mark.parametrize("status", [0, -1, "approved"])
def test_component_eligibility_rejects_zero_negative_and_bad_statuses(status):
    with pytest.raises(ValidationError):
        ComponentEligibility(status=status, reasons=("reason",))


def test_unassessed_funding_is_distinct_from_false_funding_flag():
    result = scope_calculation(calculation(), scope())
    assert result["enterprise_value"] == Decimal("160")
    assert "external_funding_required" not in result
    assert scope().funding_assessment.status == "not_assessed"
    assert result["model_result_scope"]["funding_assessment"]["status"] == "not_assessed"


def test_conditional_operating_output_keeps_enterprise_and_forecasts_but_withholds_equity_share_and_return():
    result = scope_calculation(calculation(), scope())
    assert result["enterprise_value"] == Decimal("160")
    assert result["forecasts"] == [{
        "label": "FY27", "revenue": Decimal("120"), "fcff": Decimal("12"),
        "present_value": Decimal("10"),
    }]
    assert "equity_value" not in result
    assert "value_per_current_diluted_share" not in result
    assert "annualized_return" not in result
    assert "unknown_numeric_target" not in result


def test_zero_or_negative_operating_outputs_do_not_change_scope_or_reasons():
    raw = calculation()
    raw["enterprise_value"] = Decimal("0")
    raw["forecasts"][0]["fcff"] = Decimal("-1")
    operating_reason = "Only an illustrative operating presentation is permitted."
    explicit_scope = scope()
    explicit_scope = explicit_scope.model_copy(update={
        "operating_asset_value": eligibility("conditional", operating_reason),
    })
    result = scope_calculation(raw, explicit_scope)
    assert result["enterprise_value"] == Decimal("0")
    assert result["forecasts"][0]["fcff"] == Decimal("-1")
    assert explicit_scope.operating_asset_value.reasons == (operating_reason,)


@pytest.mark.parametrize("status", ["blocked", "not_assessed"])
def test_unavailable_operating_scope_withholds_all_numeric_valuation_output(status):
    result = scope_calculation(calculation(), scope(operating=status))
    assert result["limitations"] == ("illustrative",)
    assert result["scope"] == "model scope"
    assert result["model_result_scope"]["operating_asset_value"]["status"] == status
    assert len(result) == 3


def test_scope_filter_does_not_mutate_input_and_handles_engine_wrapper():
    raw = calculation()
    original = deepcopy(raw)
    wrapped = {"status": "illustrative", "result": raw, "unknown": Decimal("8")}
    result = scope_calculation(wrapped, scope())
    assert raw == original
    assert result["status"] == "illustrative"
    assert result["result"]["enterprise_value"] == Decimal("160")
    assert result["model_result_scope"] == scope().model_dump(mode="json")
    assert "unknown" not in result


def test_conservative_scope_is_engineering_limited_and_never_accepts_equity_or_funding():
    clear = conservative_scope(illustrative_calculation())
    missing = conservative_scope(
        illustrative_calculation(), missing_operating_inputs=("capex", "tax rate")
    )
    assert clear.operating_asset_value.status == "conditional"
    assert clear.equity_per_share_value.status == "blocked"
    assert clear.funding_assessment.status == "not_assessed"
    assert clear.opening_date_alignment.status == "not_assessed"
    assert missing.operating_asset_value.status == "blocked"
    assert "capex, tax rate" in missing.operating_asset_value.reasons[0]


@pytest.mark.parametrize("calculation_input", [
    {"status": "unavailable", "limitations": ["inputs missing"]},
    {"status": "unavailable", "result": calculation()},
    {"status": "illustrative", "result": None},
    {"status": "illustrative", "result": {}},
    {"status": "illustrative", "result": {"enterprise_value": Decimal("NaN")}},
    {"status": "illustrative", "result": {"enterprise_value": float("inf")}},
])
def test_conservative_scope_never_conditions_unavailable_null_or_nonfinite_results(calculation_input):
    assert conservative_scope(calculation_input).operating_asset_value.status == "blocked"


def test_unavailable_wrapper_keeps_status_limitations_and_persisted_scope():
    result = scope_calculation(
        {"status": "unavailable", "limitations": ["inputs missing"], "unknown": Decimal("3")},
        conservative_scope({"status": "unavailable"}),
    )
    assert result["status"] == "unavailable"
    assert result["limitations"] == ["inputs missing"]
    assert result["model_result_scope"]["funding_assessment"]["status"] == "not_assessed"
    assert "unknown" not in result
