from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from tradingagents.research.equity_valuation import (
    EquityDCFModelInput,
    EquityForecastPeriod,
    equity_dcf_valuation,
    forecast_equity_cash_flow,
)
from tradingagents.research.valuation import ValuationError, ValuationUnits

D = Decimal
UNITS = ValuationUnits(currency="USD", amount_scale=D("1"), share_scale=D("1"))


def _period(
    label: str,
    start: date,
    end: date,
    discount_years: str,
    *,
    net_income: str,
    retention: str,
) -> EquityForecastPeriod:
    return EquityForecastPeriod(
        label=label,
        period_start=start,
        period_end=end,
        discount_years=D(discount_years),
        net_income_common=D(net_income),
        required_capital_retention=D(retention),
    )


def _model() -> EquityDCFModelInput:
    return EquityDCFModelInput(
        as_of_date=date(2024, 12, 31),
        current_net_income=D("80"),
        periods=(
            _period(
                "FY2025",
                date(2024, 12, 31),
                date(2025, 12, 31),
                "1",
                net_income="100",
                retention="20",
            ),
            _period(
                "FY2026",
                date(2025, 12, 31),
                date(2026, 12, 31),
                "2",
                net_income="120",
                retention="30",
            ),
        ),
        cost_of_equity=D("0.10"),
        terminal_growth=D("0.03"),
        current_diluted_shares=D("10"),
        units=UNITS,
    )


def test_equity_dcf_matches_independent_reference_calculation() -> None:
    result = equity_dcf_valuation(_model())

    with localcontext() as context:
        context.prec = 40
        cash_flow_1 = D("100") - D("20")
        cash_flow_2 = D("120") - D("30")
        terminal_cash_flow = cash_flow_2 * D("1.03")
        terminal_value = terminal_cash_flow / D("0.07")
        explicit_present_value = cash_flow_1 / D("1.10") + cash_flow_2 / D("1.10") ** 2
        terminal_present_value = terminal_value / D("1.10") ** 2
        total_equity_value = explicit_present_value + terminal_present_value
        per_share_value = total_equity_value / D("10")
        terminal_share = terminal_present_value / total_equity_value

    assert tuple(item.equity_cash_flow for item in result.forecasts) == (
        cash_flow_1,
        cash_flow_2,
    )
    assert result.explicit_period_present_value == explicit_present_value
    assert result.terminal_equity_cash_flow == terminal_cash_flow
    assert result.terminal_value_at_horizon == terminal_value
    assert result.terminal_value_present_value == terminal_present_value
    assert result.total_equity_present_value == total_equity_value
    assert result.value_per_current_diluted_share == per_share_value
    assert result.terminal_value_share_of_total_equity_present_value == terminal_share


def test_amount_and_share_scales_are_applied_to_present_equity_value() -> None:
    scaled = replace(
        _model(),
        units=ValuationUnits(
            currency="USD", amount_scale=D("1000000"), share_scale=D("1000")
        ),
    )
    result = equity_dcf_valuation(scaled)
    with localcontext() as context:
        context.prec = 40
        expected_per_share = (
            result.total_equity_present_value * D("1000000")
            / (D("10") * D("1000"))
        )

    assert result.value_per_current_diluted_share == expected_per_share
    assert result.units.currency == "USD"


def test_tolerated_timing_rounding_cannot_change_dated_discounting() -> None:
    model = _model()
    rounded = tuple(
        replace(period, discount_years=period.discount_years + D("0.001"))
        for period in model.periods
    )
    forecasts = forecast_equity_cash_flow(replace(model, periods=rounded))

    assert tuple(item.discount_years for item in forecasts) == (D("1"), D("2"))
    assert tuple(item.discount_factor for item in forecasts) == (
        D("1.10"),
        D("1.10") ** 2,
    )


def test_stub_period_uses_actual_dated_fraction() -> None:
    with localcontext() as context:
        context.prec = 40
        stub_years = D("181") / D("365")
        final_years = D("1") + stub_years
    model = replace(
        _model(),
        periods=(
            EquityForecastPeriod(
                label="H1 2025",
                period_start=date(2024, 12, 31),
                period_end=date(2025, 6, 30),
                discount_years=stub_years,
                net_income_common=D("30"),
                required_capital_retention=D("10"),
            ),
            EquityForecastPeriod(
                label="LTM June 2026",
                period_start=date(2025, 6, 30),
                period_end=date(2026, 6, 30),
                discount_years=final_years,
                net_income_common=D("100"),
                required_capital_retention=D("20"),
            ),
        ),
    )

    result = equity_dcf_valuation(model)
    assert result.forecasts[0].discount_years == stub_years
    assert result.forecasts[1].discount_years == final_years


def test_present_per_share_value_uses_current_diluted_shares_only() -> None:
    model = _model()
    result = equity_dcf_valuation(model)
    doubled_shares = equity_dcf_valuation(
        replace(model, current_diluted_shares=D("20"))
    )
    with localcontext() as context:
        context.prec = 40
        expected_doubled_share_value = result.value_per_current_diluted_share / D("2")

    assert doubled_shares.total_equity_present_value == result.total_equity_present_value
    assert doubled_shares.value_per_current_diluted_share == expected_doubled_share_value
    period_fields = {field.name for field in fields(EquityForecastPeriod)}
    model_fields = {field.name for field in fields(EquityDCFModelInput)}
    assert not period_fields & {
        "future_diluted_shares",
        "buybacks",
        "repurchases",
        "stock_compensation_addback",
    }
    assert not model_fields & {"net_debt", "corporate_cash", "customer_assets"}


def test_interim_negative_equity_cash_flow_is_preserved() -> None:
    loss_period = _period(
        "FY2025",
        date(2024, 12, 31),
        date(2025, 12, 31),
        "1",
        net_income="-25",
        retention="10",
    )
    final_period = replace(
        _model().periods[-1],
        period_start=date(2025, 12, 31),
    )
    result = equity_dcf_valuation(
        replace(_model(), periods=(loss_period, final_period))
    )

    assert result.forecasts[0].equity_cash_flow == D("-35")
    assert result.forecasts[0].present_value < D("0")


def test_nonpositive_terminal_equity_cash_flow_cannot_be_capitalized() -> None:
    final_loss = replace(
        _model().periods[-1],
        net_income_common=D("20"),
        required_capital_retention=D("20"),
    )
    with pytest.raises(ValuationError, match="positive normalized final equity cash flow"):
        equity_dcf_valuation(replace(_model(), periods=(_model().periods[0], final_loss)))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"current_net_income": D("NaN")}, "finite"),
        ({"cost_of_equity": D("0")}, "positive"),
        ({"cost_of_equity": D("1.01")}, "must not exceed"),
        ({"terminal_growth": D("0.10")}, "must exceed"),
        ({"terminal_growth": D("0.26")}, "must be between"),
        ({"current_diluted_shares": D("0")}, "positive"),
    ],
)
def test_model_rejects_invalid_or_unbounded_inputs(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValuationError, match=message):
        replace(_model(), **change)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"net_income_common": D("Infinity")}, "finite"),
        ({"required_capital_retention": D("-1")}, "nonnegative"),
        ({"discount_years": D("NaN")}, "finite"),
    ],
)
def test_period_rejects_invalid_amounts(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValuationError, match=message):
        replace(_model().periods[0], **change)


def test_periods_must_be_contiguous_and_timing_must_match_dates() -> None:
    model = _model()
    with pytest.raises(ValuationError, match="start at as_of_date and remain contiguous"):
        replace(
            model,
            periods=(
                replace(model.periods[0], period_start=date(2025, 1, 1)),
                model.periods[1],
            ),
        )
    with pytest.raises(ValuationError, match="dated period-end timing"):
        replace(
            model,
            periods=(replace(model.periods[0], discount_years=D("1.5")),),
        )


def test_terminal_period_must_be_full_year() -> None:
    stub = _period(
        "H1 2025",
        date(2024, 12, 31),
        date(2025, 6, 30),
        str(D("181") / D("365")),
        net_income="50",
        retention="10",
    )
    with pytest.raises(ValuationError, match="full annual"):
        equity_dcf_valuation(replace(_model(), periods=(stub,)))


def test_result_states_broker_equity_assumptions_and_limitations() -> None:
    result = equity_dcf_valuation(_model())
    assumptions = " ".join(result.assumptions).lower()
    limitations = " ".join(result.limitations).lower()

    assert "net income attributable to common" in assumptions
    assert "stock compensation" in assumptions
    assert "current diluted common shares" in assumptions
    assert "regulatory capital adequacy" in limitations
    assert "independent source linkage" in limitations
    assert "customer assets" in limitations
    assert "corporate net debt/cash" in limitations
    assert "production-acceptance" in result.scope
