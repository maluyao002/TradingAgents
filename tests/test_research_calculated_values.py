from dataclasses import asdict, replace
from decimal import Decimal, localcontext

import pytest

from tests.test_research_equity_valuation import _model as equity_model
from tests.test_research_valuation import _model
from tradingagents.research.calculated_values import (
    CalculatedValue,
    calculation_catalog,
    render_calculations,
)
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


def _scenario_value(
    identifier,
    value,
    *,
    evidence_ids=("filing",),
    currency="USD",
    input_hash="a" * 64,
    result_hash="b" * 64,
    classification="conditional_operating_scenario_calculation_not_reported_fact",
):
    return CalculatedValue(
        id=identifier,
        value=Decimal(value),
        unit=currency,
        currency=currency,
        valuation_method="operating_scenario",
        share_count_basis="not_applicable",
        model_input_sha256=input_hash,
        model_result_sha256=result_hash,
        evidence_ids=evidence_ids,
        classification=classification,
    )


def test_declared_fraction_and_source_backed_scenario_table_render_safely():
    fraction = CalculatedValue(
        id="valuation.inputs.tax_rate",
        value=Decimal("0.165"),
        unit="fraction",
        valuation_method="fcff",
        share_count_basis="not_applicable",
        model_input_sha256="a" * 64,
        model_result_sha256="b" * 64,
        evidence_ids=("filing",),
    )
    values = (
        _scenario_value(
            "operating_scenario.base.fiscal_total.revenue",
            "399237000000",
            evidence_ids=("anchor-revenue",),
        ),
        _scenario_value(
            "operating_scenario.base.fiscal_total.operating_income",
            "258376000000",
            evidence_ids=("anchor-operating-income",),
        ),
        _scenario_value("operating_scenario.downside.fiscal_total.revenue", "388437000000"),
        _scenario_value("operating_scenario.downside.fiscal_total.operating_income", "248204000000"),
        fraction,
    )

    assert render_calculations("Tax {{calc:valuation.inputs.tax_rate}}", (fraction,)) == "Tax 16.50 %"
    table = render_calculations("{{scenario_table}}", values)
    assert "| Scenario | Fiscal revenue | Operating income |" in table
    assert "| Base | 399.24 billion USD | 258.38 billion USD |" in table
    assert "[anchor-revenue] [anchor-operating-income]" in table

    unsafe = values[0].model_copy(update={"evidence_ids": ()})
    with pytest.raises(ValueError, match="source-backed"):
        render_calculations("{{scenario_table}}", (unsafe, *values[1:]))

    mixed_package = values[2].model_copy(update={"model_input_sha256": "c" * 64})
    with pytest.raises(ValueError, match="source-backed"):
        render_calculations("{{scenario_table}}", (*values[:2], mixed_package, *values[3:]))

    mixed_result = values[3].model_copy(update={"model_result_sha256": "d" * 64})
    with pytest.raises(ValueError, match="source-backed"):
        render_calculations("{{scenario_table}}", (*values[:3], mixed_result, *values[4:]))

    mixed_currency = values[3].model_copy(update={"unit": "EUR", "currency": "EUR"})
    with pytest.raises(ValueError, match="source-backed"):
        render_calculations("{{scenario_table}}", (*values[:3], mixed_currency, *values[4:]))

    unsupported = values[3].model_copy(
        update={"classification": "operating_scenario_assumption_not_reported_fact"}
    )
    with pytest.raises(ValueError, match="source-backed"):
        render_calculations("{{scenario_table}}", (*values[:3], unsupported, *values[4:]))
