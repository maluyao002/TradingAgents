from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from tradingagents.research.valuation import (
    ComparableObservation,
    EquityCashFlow,
    FCFFModelInput,
    ForecastPeriod,
    PerShareCashFlow,
    SOTPInput,
    SOTPSegment,
    ThreeYearReturnInput,
    TwelveMonthRollForwardInput,
    ValuationError,
    ValuationUnits,
    apply_comparable_multiple,
    comparable_multiple,
    dcf_sensitivity,
    dcf_valuation,
    forecast_fcff,
    reverse_dcf,
    sotp_valuation,
    three_year_shareholder_return,
    twelve_month_value_roll_forward,
)

D = Decimal
UNITS = ValuationUnits(currency="USD", amount_scale=D("1"), share_scale=D("1"))


def test_quarterly_terminal_flow_is_not_capitalized_as_annual():
    model = _model()
    quarterly = replace(model.periods[-1], period_end=date(2026, 3, 31),
                        discount_years=D("1.25"))
    with pytest.raises(ValuationError, match="full annual"):
        dcf_valuation(replace(model, periods=(model.periods[0], quarterly)))


@pytest.mark.parametrize(
    "periods",
    [
        lambda model: (replace(model.periods[0], discount_years=D("0.01")), model.periods[1]),
        lambda model: (model.periods[0], replace(model.periods[1], discount_years=D("50"))),
    ],
)
def test_discount_timing_must_match_dated_period_ends(periods):
    model = _model()
    with pytest.raises(ValuationError, match="dated period-end timing"):
        replace(model, periods=periods(model))


def test_tolerated_discount_timing_rounding_cannot_change_calculation():
    model = _model()
    rounded_periods = tuple(
        replace(period, discount_years=period.discount_years + D("0.001"))
        for period in model.periods
    )
    result = dcf_valuation(replace(model, periods=rounded_periods))
    assert tuple(item.discount_years for item in result.forecasts) == (D("1"), D("2"))
    assert tuple(item.discount_factor for item in result.forecasts) == (
        D("1.10"), D("1.10") ** 2
    )
    assert any("dated period ends" in limitation for limitation in result.limitations)


@pytest.mark.parametrize(
    ("as_of", "period_start", "period_end"),
    [
        (date(2023, 2, 28), date(2023, 2, 28), date(2024, 2, 29)),
        (date(2024, 2, 29), date(2024, 2, 29), date(2025, 2, 28)),
    ],
)
def test_month_end_leap_year_anniversaries_are_exact_years(as_of, period_start, period_end):
    period = _period("FY", period_start, period_end, "1")
    result = dcf_valuation(replace(_model(), as_of_date=as_of, periods=(period,)))
    assert result.forecasts[0].discount_years == D("1")
    assert result.forecasts[0].discount_factor == D("1.10")


def test_stub_period_uses_actual_fraction_of_surrounding_calendar_year():
    with localcontext() as context:
        context.prec = 40
        stub_years = D("181") / D("365")
        second_period_years = D("1") + stub_years
    periods = (
        _period("H1 2025", date(2024, 12, 31), date(2025, 6, 30), str(stub_years)),
        _period(
            "LTM June 2026",
            date(2025, 6, 30),
            date(2026, 6, 30),
            str(second_period_years),
        ),
    )
    result = dcf_valuation(replace(_model(), periods=periods))
    assert result.forecasts[0].discount_years == stub_years
    assert result.forecasts[1].discount_years == second_period_years


def _period(
    label: str,
    start: date,
    end: date,
    discount_years: str,
    *,
    growth: str = "0.10",
    margin: str = "0.20",
    margin_basis: str = "after_sbc",
    tax: str = "0.25",
    da: str = "0.05",
    capex: str = "0.04",
    wc: str = "0.10",
    sbc: str = "0.03",
    funding: bool = False,
) -> ForecastPeriod:
    return ForecastPeriod(
        label=label,
        period_start=start,
        period_end=end,
        discount_years=D(discount_years),
        revenue_growth=D(growth),
        operating_margin=D(margin),
        operating_margin_basis=margin_basis,  # type: ignore[arg-type]
        tax_rate=D(tax),
        depreciation_amortization_pct_revenue=D(da),
        capex_pct_revenue=D(capex),
        working_capital_pct_revenue=D(wc),
        sbc_pct_revenue=D(sbc),
        external_funding_required=funding,
    )


def _model() -> FCFFModelInput:
    return FCFFModelInput(
        as_of_date=date(2024, 12, 31),
        current_revenue=D("100"),
        current_working_capital=D("10"),
        periods=(
            _period("FY2025", date(2024, 12, 31), date(2025, 12, 31), "1"),
            _period("FY2026", date(2025, 12, 31), date(2026, 12, 31), "2"),
        ),
        discount_rate=D("0.10"),
        terminal_growth=D("0.03"),
        net_debt=D("20"),
        current_diluted_shares=D("10"),
        units=UNITS,
    )


def test_fcff_dcf_matches_independent_reference_calculation() -> None:
    result = dcf_valuation(_model())

    # Independent schedule, deliberately not calling forecast_fcff.
    revenue_1 = D("100") * D("1.10")
    fcff_1 = (
        revenue_1 * D("0.20") * D("0.75")
        + revenue_1 * D("0.05")
        - revenue_1 * D("0.04")
        - (revenue_1 * D("0.10") - D("10"))
    )
    revenue_2 = revenue_1 * D("1.10")
    fcff_2 = (
        revenue_2 * D("0.20") * D("0.75")
        + revenue_2 * D("0.05")
        - revenue_2 * D("0.04")
        - (revenue_2 * D("0.10") - revenue_1 * D("0.10"))
    )
    with localcontext() as context:
        context.prec = 40
        terminal = fcff_2 * D("1.03") / D("0.07")
        expected_enterprise = (
            fcff_1 / D("1.10") + fcff_2 / D("1.10") ** 2 + terminal / D("1.10") ** 2
        )
        expected_equity = expected_enterprise - D("20")
        expected_per_share = expected_equity / D("10")

    assert fcff_1 == D("16.60")
    assert fcff_2 == D("18.2600")
    assert result.forecasts[0].fcff == fcff_1
    assert result.forecasts[1].fcff == fcff_2
    assert result.enterprise_value == expected_enterprise
    assert result.equity_value == expected_equity
    assert result.value_per_current_diluted_share == expected_per_share
    assert result.terminal_value_share_of_enterprise_value is not None


def test_raw_statement_and_share_scales_are_explicit_in_per_share_value() -> None:
    # Monetary statements in USD millions and shares in USD thousands must not be
    # divided as if their raw values used the same scale.
    scaled = replace(
        _model(),
        units=ValuationUnits(currency="USD", amount_scale=D("1000000"), share_scale=D("1000")),
    )
    result = dcf_valuation(scaled)
    with localcontext() as context:
        context.prec = 40
        expected_per_share = result.equity_value * D("100")
    assert result.value_per_current_diluted_share == expected_per_share
    assert result.units.amount_scale == D("1000000")


def test_sbc_before_margin_is_economic_cost_and_never_added_back() -> None:
    after_sbc = _model()
    before_sbc_periods = tuple(
        replace(period, operating_margin_basis="before_sbc") for period in after_sbc.periods
    )
    before_sbc = replace(after_sbc, periods=before_sbc_periods)

    after_cash_flow = forecast_fcff(after_sbc)[0]
    before_cash_flow = forecast_fcff(before_sbc)[0]

    # A 3% SBC charge lowers pre-tax profit by 3.30 and FCFF by the after-tax 2.475.
    assert before_cash_flow.economic_sbc == D("3.30")
    assert after_cash_flow.fcff - before_cash_flow.fcff == D("2.4750")
    assert "not added back" in dcf_valuation(before_sbc).limitations[1]


def test_negative_working_capital_is_supported_and_release_adds_fcff() -> None:
    model = replace(
        _model(),
        current_working_capital=D("-5"),
        periods=(
            _period(
                "FY2025",
                date(2024, 12, 31),
                date(2025, 12, 31),
                "1",
                wc="-0.06",
            ),
        ),
    )
    cash_flow = forecast_fcff(model)[0]
    assert cash_flow.working_capital == D("-6.6000")
    assert cash_flow.change_in_working_capital == D("-1.6000")
    assert cash_flow.fcff == D("19.2000")


def test_loss_period_does_not_fabricate_immediate_tax_benefit() -> None:
    loss_period = _period(
        "FY2025",
        date(2024, 12, 31),
        date(2025, 12, 31),
        "1",
        margin="-0.10",
        tax="0.25",
        da="0",
        capex="0",
        wc="0.10",
    )
    cash_flow = forecast_fcff(replace(_model(), periods=(loss_period,)))[0]
    assert cash_flow.operating_profit_before_tax == D("-11.00")
    assert cash_flow.nopat == D("-11.00")


def test_external_funding_requires_specific_caveat_but_is_not_fcff_financing() -> None:
    funded_period = _period(
        "FY2025",
        date(2024, 12, 31),
        date(2025, 12, 31),
        "1",
        funding=True,
    )
    with pytest.raises(ValuationError, match="funding_caveats"):
        replace(_model(), periods=(funded_period,))

    model = replace(
        _model(),
        periods=(funded_period,),
        funding_caveats=("Liquidity depends on an undrawn revolving facility.",),
    )
    result = dcf_valuation(model)
    assert result.forecasts[0].fcff == D("16.6000")
    assert result.limitations[-1].startswith("Liquidity")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"discount_rate": D("0.03")}, "exceed"),
        ({"current_diluted_shares": D("0")}, "positive"),
        ({"current_revenue": D("NaN")}, "finite"),
    ],
)
def test_dcf_rejects_unsupported_inputs(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValuationError, match=message):
        replace(_model(), **change)


def test_negative_terminal_fcff_withholds_gordon_target() -> None:
    loss_period = _period(
        "FY2025",
        date(2024, 12, 31),
        date(2025, 12, 31),
        "1",
        margin="-0.50",
        da="0",
        capex="0.20",
        wc="0.10",
    )
    with pytest.raises(ValuationError, match="positive normalized final FCFF"):
        dcf_valuation(replace(_model(), periods=(loss_period,)))


def test_sotp_applies_corporate_cost_debt_and_claims_once() -> None:
    result = sotp_valuation(
        SOTPInput(
            segments=(SOTPSegment("Products", D("100")), SOTPSegment("Services", D("80"))),
            corporate_cost_present_value=D("20"),
            net_debt=D("30"),
            other_claims=D("10"),
            non_operating_assets=D("5"),
            current_diluted_shares=D("10"),
            units=UNITS,
        )
    )
    assert result.gross_segment_enterprise_value == D("180")
    assert result.consolidated_enterprise_value == D("160")
    assert result.equity_value == D("125")
    assert result.value_per_current_diluted_share == D("12.5")


def test_comparable_rejects_negative_earnings_and_unmatched_basis() -> None:
    with pytest.raises(ValuationError, match="negative or zero earnings"):
        ComparableObservation("LossCo", D("100"), "equity_value", D("-5"), "net_income", "FY2026")
    with pytest.raises(ValuationError, match="unmatched"):
        ComparableObservation(
            "WrongBasis", D("100"), "enterprise_value", D("10"), "net_income", "FY2026"
        )


def test_comparable_requires_same_metric_and_period_without_averaging() -> None:
    observation = ComparableObservation(
        "Peer A", D("200"), "enterprise_value", D("20"), "ebitda", "FY2026"
    )
    multiple = comparable_multiple(observation)
    assert multiple.multiple == D("10")
    assert apply_comparable_multiple(
        multiple,
        target_metric_value=D("30"),
        target_metric_basis="ebitda",
        target_period="FY2026",
    ).implied_value == D("300")
    with pytest.raises(ValuationError, match="must match"):
        apply_comparable_multiple(
            multiple,
            target_metric_value=D("30"),
            target_metric_basis="ebitda",
            target_period="FY2027",
        )


def test_twelve_month_roll_forward_is_distinct_and_cash_flow_explicit() -> None:
    model = TwelveMonthRollForwardInput(
        present_equity_value=D("100"),
        annual_required_return=D("0.10"),
        current_diluted_shares=D("10"),
        cash_flows=(EquityCashFlow(D("6"), D("4"), "dividend"),),
        units=UNITS,
    )
    result = twelve_month_value_roll_forward(model)
    with localcontext() as context:
        context.prec = 40
        expected = D("110") - context.power(D("1.10"), D("0.5")) * D("4")
    assert result.present_equity_value == D("100")
    assert result.future_equity_value_ex_cash_flows == expected
    assert result.nominal_distributions_per_current_diluted_share == D("0.4")
    assert "current diluted shares constant" in result.convention


def test_three_year_return_uses_unreinvested_cash_distribution_convention() -> None:
    result = three_year_shareholder_return(
        ThreeYearReturnInput(
            beginning_price=D("100"),
            ending_price=D("121"),
            cash_flows_per_share=(
                PerShareCashFlow(D("1"), D("2")),
                PerShareCashFlow(D("2"), D("2")),
                PerShareCashFlow(D("3"), D("2")),
            ),
        )
    )
    assert result.total_cash_distributions_per_share == D("6")
    assert result.holding_period_return == D("0.27")
    with localcontext() as context:
        context.prec = 40
        expected_annualized = context.power(D("1.27"), D("1") / D("3")) - D("1")
    assert result.annualized_return == expected_annualized
    assert "not reinvested" in result.convention


def test_reverse_dcf_solves_one_driver_and_requires_bracket() -> None:
    target = dcf_valuation(_model()).value_per_current_diluted_share
    solved = reverse_dcf(
        replace(_model(), terminal_growth=D("0.01")),
        target_value_per_share=target,
        driver="terminal_growth",
        lower=D("0"),
        upper=D("0.05"),
        tolerance=D("0.0000000001"),
    )
    assert abs(solved.solved_value - D("0.03")) < D("0.00000001")
    assert abs(solved.implied_value_per_share - target) <= D("0.0000000001")
    assert "all other inputs stayed fixed" in solved.caveat

    with pytest.raises(ValuationError, match="do not bracket"):
        reverse_dcf(
            _model(),
            target_value_per_share=D("100000"),
            driver="terminal_growth",
            lower=D("0"),
            upper=D("0.05"),
        )


def test_sensitivity_is_explicit_ordered_grid_and_rejects_invalid_pair() -> None:
    points = dcf_sensitivity(
        _model(),
        discount_rates=(D("0.09"), D("0.10")),
        terminal_growth_rates=(D("0.02"), D("0.03")),
    )
    assert [(point.discount_rate, point.terminal_growth) for point in points] == [
        (D("0.09"), D("0.02")),
        (D("0.09"), D("0.03")),
        (D("0.10"), D("0.02")),
        (D("0.10"), D("0.03")),
    ]
    assert points[0].value_per_current_diluted_share > points[-1].value_per_current_diluted_share
    with pytest.raises(ValuationError, match="exceed"):
        dcf_sensitivity(
            _model(),
            discount_rates=(D("0.03"),),
            terminal_growth_rates=(D("0.03"),),
        )
