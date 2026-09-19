"""Terminal reinvestment derivation contracts and reviewed-fixture regression tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from scripts import research_nvda_scenarios as scenarios
from tests.test_research_scenario_compiler import _request
from tradingagents.research.derivations import (
    TERMINAL_CAPEX_FORMULA,
    TerminalReinvestmentDerivation,
    terminal_derivation,
)

D = Decimal
MARKET = {
    "risk_free_rate": ".0501",
    "erp": ".0414",
    "unlevered_beta_cash_corrected": "1.50",
}


def _cases_and_audit():
    return scenarios.authored_cases(_request(), MARKET)


def _changed_final(case, **updates):
    periods = (*case.periods[:-1], replace(case.periods[-1], **updates))
    return case.model_copy(update={"periods": periods})


def test_current_three_case_fixture_produces_typed_derivations() -> None:
    cases, audit = _cases_and_audit()
    derivations = tuple(terminal_derivation(case, audit[case.id]) for case in cases)

    assert tuple(item.scenario_id for item in derivations) == ("downside", "base", "upside")
    assert tuple(item.terminal_roic_assumption for item in derivations) == (
        D(".15"),
        D(".20"),
        D(".25"),
    )
    assert all(item.kind == "analyst_derivation" for item in derivations)
    assert all(item.unit == "fraction" for item in derivations)
    assert all(item.terminal_capex_formula == TERMINAL_CAPEX_FORMULA for item in derivations)
    assert all("not issuer-reported" in " ".join(item.limitations) for item in derivations)
    assert all("does not accept" in item.rationale for item in derivations)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("terminal_roic_assumption", D(".21"), "reinvestment"),
        ("terminal_growth", D(".031"), "growth"),
        ("terminal_reinvestment_fraction_of_nopat", D(".151"), "reinvestment"),
        ("terminal_capex_pct_revenue", D(".01"), "capex"),
        ("terminal_capex_formula", "DA + invented", "formula"),
    ),
)
def test_tampered_audit_claims_fail(field, value, message) -> None:
    cases, audit = _cases_and_audit()
    case = cases[1]
    changed = {**audit[case.id], field: value}

    with pytest.raises(ValueError, match=message):
        terminal_derivation(case, changed)


@pytest.mark.parametrize(
    ("case_update", "message"),
    (
        ({"revenue_growth": D(".031")}, "growth"),
        ({"working_capital_pct_revenue": D(".171")}, "working-capital"),
        ({"capex_pct_revenue": D(".01")}, "capex"),
    ),
)
def test_tampered_final_period_fails(case_update, message) -> None:
    cases, audit = _cases_and_audit()
    case = _changed_final(cases[1], **case_update)

    with pytest.raises(ValueError, match=message):
        terminal_derivation(case, audit[case.id])


def test_tampered_prior_working_capital_fails() -> None:
    cases, audit = _cases_and_audit()
    case = cases[1]
    periods = (
        *case.periods[:-2],
        replace(case.periods[-2], working_capital_pct_revenue=D(".171")),
        case.periods[-1],
    )
    changed = case.model_copy(update={"periods": periods})

    with pytest.raises(ValueError, match="working-capital"):
        terminal_derivation(changed, audit[case.id])


def test_pre_sbc_terminal_margin_is_rejected_without_silent_adjustment() -> None:
    cases, audit = _cases_and_audit()
    changed = _changed_final(cases[1], operating_margin_basis="before_sbc")

    with pytest.raises(ValueError, match="after_sbc"):
        terminal_derivation(changed, audit[changed.id])


def test_forged_model_copy_is_revalidated_before_terminal_math() -> None:
    cases, audit = _cases_and_audit()
    forged = cases[1].model_copy(update={"periods": (*cases[1].periods[:-1], "forged")})

    with pytest.raises(ValueError, match="ConditionalScenario contract"):
        terminal_derivation(forged, audit[cases[1].id])


def test_every_audit_field_is_required() -> None:
    cases, audit = _cases_and_audit()
    case = cases[1]
    required = {
        "terminal_roic_assumption",
        "terminal_growth",
        "terminal_reinvestment_fraction_of_nopat",
        "terminal_capex_pct_revenue",
        "terminal_capex_formula",
    }

    for field in required:
        incomplete = {key: value for key, value in audit[case.id].items() if key != field}
        with pytest.raises(ValueError, match="missing required fields"):
            terminal_derivation(case, incomplete)


def test_decimal_hostile_caller_context_does_not_change_results() -> None:
    cases, audit = _cases_and_audit()
    expected = terminal_derivation(cases[1], audit["base"])

    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        actual = terminal_derivation(cases[1], audit["base"])

    assert actual == expected


def _valid_contract_values() -> dict:
    cases, audit = _cases_and_audit()
    return terminal_derivation(cases[1], audit["base"]).model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("terminal_growth", D("NaN")),
        ("terminal_growth", D("Infinity")),
        ("terminal_growth", D("-0.01")),
        ("terminal_roic_assumption", D("NaN")),
        ("terminal_roic_assumption", D("Infinity")),
        ("terminal_roic_assumption", D("-0.01")),
        ("terminal_roic_assumption", D("0")),
        ("terminal_capex_pct_revenue", D("-0.01")),
    ),
)
def test_invalid_nonfinite_negative_and_zero_values_fail(field, value) -> None:
    values = _valid_contract_values()
    values[field] = value

    with pytest.raises(ValidationError):
        TerminalReinvestmentDerivation.model_validate(values)


def test_zero_terminal_growth_is_allowed_when_the_bridge_reconciles() -> None:
    values = _valid_contract_values()
    values.update(
        {
            "terminal_growth": D("0"),
            "final_revenue_growth": D("0"),
            "terminal_reinvestment_fraction_of_nopat": D("0"),
            "terminal_capex_pct_revenue": values[
                "depreciation_amortization_pct_revenue"
            ],
        }
    )

    derivation = TerminalReinvestmentDerivation.model_validate(values)
    assert derivation.terminal_growth == 0
