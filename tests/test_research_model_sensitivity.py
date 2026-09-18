from dataclasses import asdict, replace
from decimal import Decimal, localcontext

import pytest

from tests.test_research_equity_valuation import _model as equity_model
from tests.test_research_valuation import _model as fcff_model
from tradingagents.research.equity_valuation import equity_dcf_valuation
from tradingagents.research.model_sensitivity import recompute_sensitivity
from tradingagents.research.storage import digest
from tradingagents.research.valuation import ValuationError, dcf_valuation

D = Decimal
RATES = (D("0.10"), D("0.12"))
GROWTH = (D("0.02"), D("0.03"))


def grid(model, rates=RATES, growth=GROWTH):
    return recompute_sensitivity(model, discount_rates=rates, terminal_growth_rates=growth,
                                 share_count_basis="latest_quarter_diluted_proxy")


@pytest.mark.parametrize("factory,calculate,rate_field", [
    (fcff_model, dcf_valuation, "discount_rate"),
    (equity_model, equity_dcf_valuation, "cost_of_equity"),
])
def test_each_cell_recomputed_from_exact_model(factory, calculate, rate_field):
    model = factory()
    result = grid(model)
    assert result.base_model_sha256 == digest(asdict(model))
    assert result.base_result_sha256 == digest(asdict(calculate(model)))
    assert result.share_count_basis == "latest_quarter_diluted_proxy"
    assert len(result.cells) == 4
    for cell in result.cells:
        point = replace(model, **{rate_field: cell.discount_rate,
                                 "terminal_growth": cell.terminal_growth})
        assert cell.model_sha256 == digest(asdict(point))
        assert cell.result_sha256 == digest(asdict(calculate(point)))
        assert cell.value_per_current_diluted_share == calculate(point).value_per_current_diluted_share
    assert result.cells[0].value_per_current_diluted_share > result.cells[2].value_per_current_diluted_share
    assert result.cells[1].value_per_current_diluted_share > result.cells[0].value_per_current_diluted_share


def test_equity_grid_matches_independent_cashflow_formula():
    model = equity_model()
    for cell in grid(model).cells:
        with localcontext() as ctx:
            ctx.prec = 40
            flows = [p.net_income_common - p.required_capital_retention for p in model.periods]
            expected = sum(flow / (1 + cell.discount_rate) ** p.discount_years
                           for flow, p in zip(flows, model.periods, strict=True))
            expected += flows[-1] * (1 + cell.terminal_growth) / (
                cell.discount_rate - cell.terminal_growth
            ) / (1 + cell.discount_rate) ** model.periods[-1].discount_years
        assert abs(cell.equity_present_value - expected) < D("1e-30")


def test_forecast_revision_invalidates_all_bound_hashes_and_values():
    model = equity_model()
    amended = replace(model, periods=tuple(replace(p, net_income_common=p.net_income_common + D(10))
                                           for p in model.periods))
    old, new = grid(model), grid(amended)
    assert old.base_model_sha256 != new.base_model_sha256
    assert old.base_result_sha256 != new.base_result_sha256
    assert all(a.model_sha256 != b.model_sha256 and a.result_sha256 != b.result_sha256
               and a.value_per_current_diluted_share != b.value_per_current_diluted_share
               for a, b in zip(old.cells, new.cells, strict=True))


@pytest.mark.parametrize("rates,growth", [
    ((), (D(".02"),)), ((D("NaN"),), (D(".02"),)),
    ((0.1,), (D(".02"),)), ((D(".1"), D(".1")), (D(".02"),)),
    ((D(".01"),), (D(".02"),)),
    (tuple(D(i) / 100 for i in range(1, 13)), tuple(D(i) / 1000 for i in range(12))),
])
def test_invalid_grid_is_not_partial_or_zero(rates, growth):
    with pytest.raises(ValuationError):
        grid(equity_model(), rates, growth)


def test_decimal_context_does_not_change_grid():
    expected = grid(equity_model())
    with localcontext() as ctx:
        ctx.prec = 9
        assert grid(equity_model()) == expected
