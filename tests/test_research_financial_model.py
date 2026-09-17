from decimal import Decimal, localcontext

import pytest

from tradingagents.research.financial_model import (
    FinancialModelError,
    OpeningBalance,
    PeriodDrivers,
    StatementUnits,
    build_one_period,
    fabless_revenue,
    foundry_revenue,
    recovery_cash_tax,
    sum_mixed_segments,
)

D = Decimal
USD = StatementUnits("USD", D("1"), D("1"))


def opening(**changes):
    data = {
        "units": USD,
        "assets": D("200"),
        "liabilities": D("80"),
        "equity": D("120"),
        "cash": D("40"),
        "net_ppe": D("60"),
        "debt": D("50"),
        "shares": D("10"),
    }
    data.update(changes)
    return OpeningBalance(**data)


def drivers(**changes):
    data = {
        "units": USD,
        "revenue": D("100"),
        "cogs": D("40"),
        "opex_including_sbc": D("20"),
        "sbc_expense": D("5"),
        "depreciation_amortization": D("10"),
        "interest_expense": D("2"),
        "interest_tax_shield": D("0.5"),
        "cash_tax": D("7"),
        "change_nwc": D("3"),
        "capex": D("12"),
        "debt_issued": D("4"),
        "debt_repaid": D("6"),
        "share_issuance_amount": D("8"),
        "shares_issued": D("1"),
        "repurchases_amount": D("3"),
        "shares_repurchased": D("0.5"),
        "dividends": D("2"),
    }
    data.update(changes)
    return PeriodDrivers(**data)


def test_integrated_statement_cash_share_and_balance_bridges_match_reference():
    result = build_one_period(opening(), drivers())
    # Independent reference arithmetic, including SBC's accounting addback and economic deduction.
    assert result.ebit == D("30")
    assert result.net_income == D("21")
    assert result.accounting_cfo == D("33")
    assert result.economic_fcff == D("17.5")
    assert result.ending_cash == D("62")
    assert result.ending_net_ppe == D("62")
    assert result.ending_debt == D("48")
    assert result.ending_equity == D("149")
    assert result.ending_shares == D("10.5")
    assert result.ending_assets == D("227")
    assert result.ending_liabilities == D("78")
    assert result.balance_reconciles and not result.funding_negative_cash
    assert not result.negative_equity


def test_templates_are_explicit_mechanics_and_losses_do_not_invent_tax_assets():
    assert fabless_revenue(D("10"), D("3")) == D("30")
    assert foundry_revenue(D("100"), D("0.8"), D("2")) == D("160.0")
    assert recovery_cash_tax(D("-100"), D("0")) == D("0")
    assert sum_mixed_segments({"products": D("12"), "services": D("8")}) == D("20")
    with pytest.raises(FinancialModelError, match="utilization"):
        foundry_revenue(D("100"), D("1.01"), D("2"))
    with pytest.raises(FinancialModelError, match="capacity_wafers"):
        foundry_revenue(D("-1"), D("0.8"), D("2"))
    with localcontext() as context:
        context.prec = 4
        precise = fabless_revenue(D("123456789"), D("0.123456789"))
    assert precise == D("15241578.750190521")


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: opening(shares=D("-1")), "shares"),
        (lambda: build_one_period(opening(), drivers(debt_repaid=D("55"))), "debt repayment"),
        (
            lambda: build_one_period(
                opening(), drivers(units=StatementUnits("EUR", D("1"), D("1")))
            ),
            "mismatched units",
        ),
        (
            lambda: OpeningBalance(
                USD, D("100"), D("80"), D("10"), D("10"), D("10"), D("1"), D("1")
            ),
            "does not reconcile",
        ),
        (lambda: opening(debt=D("81")), "debt cannot exceed"),
    ],
)
def test_invalid_balance_units_shares_and_funding_are_rejected(factory, message):
    with pytest.raises(FinancialModelError, match=message):
        factory()


def test_negative_cash_is_flagged_not_silently_funded_and_decimal_inputs_are_strict():
    result = build_one_period(
        opening(), drivers(capex=D("100"), debt_issued=D("0"), share_issuance_amount=D("0"))
    )
    assert result.funding_negative_cash
    with pytest.raises(FinancialModelError, match="Decimal"):
        fabless_revenue(10, D("3"))  # type: ignore[arg-type]


def test_negative_equity_is_flagged_without_inventing_a_tax_asset():
    result = build_one_period(
        opening(),
        drivers(
            revenue=D("0"),
            cogs=D("150"),
            opex_including_sbc=D("0"),
            sbc_expense=D("0"),
            depreciation_amortization=D("0"),
            interest_expense=D("0"),
            interest_tax_shield=D("0"),
            cash_tax=D("0"),
            change_nwc=D("0"),
            capex=D("0"),
            debt_issued=D("0"),
            debt_repaid=D("0"),
            share_issuance_amount=D("0"),
            repurchases_amount=D("0"),
            dividends=D("0"),
        ),
    )
    assert result.negative_equity and result.balance_reconciles
    assert recovery_cash_tax(D("-150"), D("0")) == D("0")


@pytest.mark.parametrize(
    ("calculation", "message"),
    [
        (lambda: fabless_revenue(D("1e18"), D("1e30")), "fabless revenue"),
        (lambda: foundry_revenue(D("1e18"), D("1"), D("1e30")), "foundry revenue"),
        (lambda: sum_mixed_segments({"a": D("1e30"), "b": D("1e30")}),
         "mixed segment revenue"),
        (
            lambda: build_one_period(
                opening(shares=D("1e18")), drivers(shares_issued=D("1e18"))
            ),
            "ending_shares",
        ),
    ],
)
def test_derived_results_cannot_escape_declared_model_bounds(calculation, message):
    with pytest.raises(FinancialModelError, match=message):
        calculation()
