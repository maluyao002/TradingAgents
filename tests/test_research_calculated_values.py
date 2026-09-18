from dataclasses import asdict, replace
from decimal import Decimal, localcontext

import pytest

from tests.test_research_equity_valuation import _model as equity_model
from tests.test_research_valuation import _model
from tradingagents.research.calculated_values import calculation_catalog, render_calculations
from tradingagents.research.equity_valuation import equity_dcf_valuation
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import digest
from tradingagents.research.valuation import ValuationUnits, dcf_valuation


@pytest.mark.parametrize("model,calculate", [(_model(), dcf_valuation), (equity_model(), equity_dcf_valuation)])
def test_code_owned_catalog_contains_financial_bridge_and_per_share(model, calculate):
    proposal = ValuationProposal(model=asdict(model), evidence_ids=("source",))
    valuation = {"status": "illustrative", "result": asdict(calculate(model))}
    catalog = calculation_catalog(proposal, valuation)
    values = {value.id: value for value in catalog}
    assert values["valuation.value_per_current_diluted_share"].value == calculate(model).value_per_current_diluted_share
    assert all(value.model_input_sha256 == digest(proposal) for value in catalog)
    assert all(value.model_result_sha256 == digest(valuation) for value in catalog)
    assert all(value.classification in {"illustrative_calculation_not_reported_fact",
                                       "model_input_not_a_new_reported_fact", "model_assumption_or_convention"} for value in catalog)
    assert values["valuation.inputs.current_diluted_shares"].unit == "diluted shares"
    assert values["valuation.forecasts.0.discount_years"].unit == "years"
    assert values["valuation.inputs.terminal_growth"].unit == "%"
    rendered = render_calculations("Value {{calc:valuation.value_per_current_diluted_share}}", catalog)
    assert rendered.endswith("USD/share")
    assert "{{" not in rendered
    if calculate is equity_dcf_valuation:
        assert "valuation.forecasts.0.equity_cash_flow" in values
        assert "valuation.total_equity_present_value" in values
        assert "valuation.enterprise_value" not in values
        assert values["valuation.inputs.cost_of_equity"].value == 10
        assert all(value.valuation_method == "equity_fcfe" for value in catalog)
    else:
        assert "valuation.forecasts.0.fcff" in values
        assert "valuation.enterprise_value" in values
        assert all(value.valuation_method == "fcff" for value in catalog)


def test_model_amount_and_share_scales_are_not_confused():
    model = replace(_model(), units=ValuationUnits("USD", Decimal("1e6"), Decimal("1e6")))
    result = asdict(dcf_valuation(model))
    proposal = ValuationProposal(model=asdict(model))
    catalog = calculation_catalog(proposal, {"status": "illustrative", "result": result})
    values = {value.id: value for value in catalog}
    with localcontext() as context:
        context.prec = 50
        assert values["valuation.equity_value"].value == result["equity_value"] * Decimal("1e6")
    assert values["valuation.value_per_current_diluted_share"].value == result["value_per_current_diluted_share"]
    assert "million USD" in render_calculations("{{calc:valuation.equity_value}}", catalog)


def test_unavailable_model_cannot_leak_a_target_and_unknown_reference_fails():
    assert calculation_catalog(ValuationProposal(), {"status": "unavailable"}) == ()
    with pytest.raises(ValueError, match="unknown calculation"):
        render_calculations("{{calc:valuation.equity_value}}", ())


def test_nonfinite_calculation_is_rejected():
    model = _model()
    result = asdict(dcf_valuation(model))
    result["equity_value"] = Decimal("NaN")
    with pytest.raises(ValueError):
        calculation_catalog(ValuationProposal(model=asdict(model)), {"status": "illustrative", "result": result})
