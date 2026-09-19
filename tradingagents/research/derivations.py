"""Typed, mechanical derivations for reviewed conditional research inputs.

These contracts preserve analyst assumptions as assumptions.  They reconcile
values already present in a conditional scenario and its economic audit; they do
not decide that a terminal ROIC, growth rate, or scenario is economically sound.
"""

from __future__ import annotations

from decimal import Context, Decimal, DecimalException, localcontext
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract

if TYPE_CHECKING:
    from .scenario_compiler import ConditionalScenario


TERMINAL_REINVESTMENT_FORMULA = "g/ROIC"
TERMINAL_CAPEX_FORMULA = (
    "DA_ratio + margin*(1-tax)*g/ROIC - WC_ratio*g/(1+g)"
)
DERIVATION_TOLERANCE = Decimal("1e-24")

_DECIMAL_CONTEXT = Context(prec=60)
_AUDIT_FIELDS = frozenset(
    {
        "terminal_roic_assumption",
        "terminal_growth",
        "terminal_reinvestment_fraction_of_nopat",
        "terminal_capex_pct_revenue",
        "terminal_capex_formula",
    }
)
_RATIONALE = (
    "Mechanical reconciliation of the conditional scenario using its supplied "
    "analyst terminal ROIC assumption; this derivation does not accept that "
    "assumption as economically justified."
)
_LIMITATIONS = (
    "Terminal ROIC and growth are analyst assumptions, not issuer-reported facts or guidance.",
    "Arithmetic reconciliation does not make the scenario probable, accepted, or an investment recommendation.",
)


def _close(left: Decimal, right: Decimal) -> bool:
    with localcontext(_DECIMAL_CONTEXT):
        return abs(left - right) <= DERIVATION_TOLERANCE


def _derived_values(
    *,
    growth: Decimal,
    roic: Decimal,
    margin: Decimal,
    tax_rate: Decimal,
    da_ratio: Decimal,
    wc_ratio: Decimal,
) -> tuple[Decimal, Decimal]:
    try:
        with localcontext(_DECIMAL_CONTEXT):
            reinvestment = growth / roic
            capex = (
                da_ratio
                + margin * (Decimal(1) - tax_rate) * growth / roic
                - wc_ratio * growth / (Decimal(1) + growth)
            )
    except DecimalException as exc:
        raise ValueError("terminal derivation is outside the finite decimal domain") from exc
    if not reinvestment.is_finite() or not capex.is_finite():
        raise ValueError("terminal derivation must be finite")
    return reinvestment, capex


class TerminalReinvestmentDerivation(Contract):
    """Reproducible terminal reinvestment bridge for one conditional scenario."""

    scenario_id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:/-]+$")
    terminal_roic_assumption: Decimal
    terminal_growth: Decimal
    operating_margin: Decimal
    tax_rate: Decimal
    depreciation_amortization_pct_revenue: Decimal
    working_capital_pct_revenue: Decimal
    prior_working_capital_pct_revenue: Decimal
    final_revenue_growth: Decimal
    terminal_reinvestment_fraction_of_nopat: Decimal
    terminal_capex_pct_revenue: Decimal
    terminal_reinvestment_formula: Literal["g/ROIC"] = TERMINAL_REINVESTMENT_FORMULA
    terminal_capex_formula: Literal[
        "DA_ratio + margin*(1-tax)*g/ROIC - WC_ratio*g/(1+g)"
    ] = TERMINAL_CAPEX_FORMULA
    kind: Literal["analyst_derivation"] = "analyst_derivation"
    unit: Literal["fraction"] = "fraction"
    rationale: str = _RATIONALE
    limitations: tuple[str, ...] = _LIMITATIONS

    @field_validator(
        "terminal_roic_assumption",
        "terminal_growth",
        "operating_margin",
        "tax_rate",
        "depreciation_amortization_pct_revenue",
        "working_capital_pct_revenue",
        "prior_working_capital_pct_revenue",
        "final_revenue_growth",
        "terminal_reinvestment_fraction_of_nopat",
        "terminal_capex_pct_revenue",
    )
    @classmethod
    def finite_decimals(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("terminal derivation values must be finite")
        return value

    @field_validator("rationale")
    @classmethod
    def explicit_analyst_rationale(cls, value: str) -> str:
        lowered = value.lower()
        if not value.strip() or "analyst" not in lowered or "does not accept" not in lowered:
            raise ValueError("rationale must identify the analyst assumption and non-acceptance")
        return value

    @field_validator("limitations")
    @classmethod
    def explicit_unique_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value.strip() for value in values):
            raise ValueError("terminal derivation limitations must be explicit and nonblank")
        if len(values) != len(set(values)):
            raise ValueError("terminal derivation limitations must be unique")
        text = " ".join(values).lower()
        if "not issuer-reported" not in text or "not" not in text:
            raise ValueError("limitations must state that the derivation is not an issuer fact")
        return values

    @model_validator(mode="after")
    def reconciles_terminal_economics(self):
        if self.terminal_growth < 0:
            raise ValueError("terminal growth must be nonnegative")
        if self.terminal_roic_assumption <= self.terminal_growth:
            raise ValueError("terminal ROIC must be greater than terminal growth")
        if self.terminal_capex_pct_revenue < 0:
            raise ValueError("terminal capex must be nonnegative")
        if not _close(self.final_revenue_growth, self.terminal_growth):
            raise ValueError("final-period growth must equal stable terminal growth")
        if not _close(
            self.prior_working_capital_pct_revenue,
            self.working_capital_pct_revenue,
        ):
            raise ValueError("final and prior working-capital ratios must be stable")

        expected_reinvestment, expected_capex = _derived_values(
            growth=self.terminal_growth,
            roic=self.terminal_roic_assumption,
            margin=self.operating_margin,
            tax_rate=self.tax_rate,
            da_ratio=self.depreciation_amortization_pct_revenue,
            wc_ratio=self.working_capital_pct_revenue,
        )
        if not _close(
            self.terminal_reinvestment_fraction_of_nopat,
            expected_reinvestment,
        ):
            raise ValueError("terminal reinvestment fraction does not equal g/ROIC")
        if not _close(self.terminal_capex_pct_revenue, expected_capex):
            raise ValueError("terminal capex does not reconcile to the stated formula")
        return self

    @property
    def formula(self) -> str:
        """Canonical capex formula, exposed for generic derivation consumers."""

        return self.terminal_capex_formula


def _audit_decimal(economic_audit: dict[str, Any], field: str) -> Decimal:
    value = economic_audit[field]
    if isinstance(value, bool):
        raise ValueError(f"economic audit {field} must be a decimal rate")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError) as exc:
        raise ValueError(f"economic audit {field} must be a decimal rate") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"economic audit {field} must be finite")
    return decimal_value


def terminal_derivation(
    scenario: ConditionalScenario,
    economic_audit: dict[str, Any],
) -> TerminalReinvestmentDerivation:
    """Validate and type one scenario's authored terminal economic audit.

    ``economic_audit`` is the single scenario entry (for example,
    ``audit[scenario.id]``), not the full mapping of scenario IDs to entries.
    """

    # ``model_copy(update=...)`` deliberately bypasses Pydantic validation.  This
    # public boundary accepts scenarios from callers, so round-trip through the
    # declared contract before dereferencing periods or rates.  The local import
    # avoids a module-import cycle with the scenario compiler.
    from .scenario_compiler import ConditionalScenario

    if not isinstance(scenario, ConditionalScenario):
        raise ValueError("scenario must satisfy the ConditionalScenario contract")
    try:
        with localcontext(_DECIMAL_CONTEXT):
            scenario = ConditionalScenario.model_validate_json(
                scenario.model_dump_json(warnings="error")
            )
    except (TypeError, ValueError) as exc:
        raise ValueError("scenario must satisfy the ConditionalScenario contract") from exc

    if not isinstance(economic_audit, dict):
        raise ValueError("economic audit must be the dictionary for one scenario")
    missing = sorted(_AUDIT_FIELDS - economic_audit.keys())
    if missing:
        raise ValueError(f"economic audit missing required fields: {', '.join(missing)}")

    if len(scenario.periods) < 2:
        raise ValueError("terminal derivation requires final and prior forecast periods")
    final = scenario.periods[-1]
    prior = scenario.periods[-2]
    if final.operating_margin_basis != "after_sbc":
        raise ValueError("terminal derivation requires an after_sbc terminal operating margin")

    audit_growth = _audit_decimal(economic_audit, "terminal_growth")
    audit_roic = _audit_decimal(economic_audit, "terminal_roic_assumption")
    audit_reinvestment = _audit_decimal(
        economic_audit, "terminal_reinvestment_fraction_of_nopat"
    )
    audit_capex = _audit_decimal(economic_audit, "terminal_capex_pct_revenue")

    if economic_audit["terminal_capex_formula"] != TERMINAL_CAPEX_FORMULA:
        raise ValueError("economic audit terminal capex formula is unsupported")
    if not _close(audit_growth, scenario.terminal_growth):
        raise ValueError("economic audit terminal growth does not match the scenario")
    if not _close(final.revenue_growth, scenario.terminal_growth):
        raise ValueError("scenario final-period growth is not stable terminal growth")
    if not _close(
        prior.working_capital_pct_revenue,
        final.working_capital_pct_revenue,
    ):
        raise ValueError("scenario final working-capital ratio is not stable")
    if not _close(audit_capex, final.capex_pct_revenue):
        raise ValueError("economic audit terminal capex does not match the scenario")

    expected_reinvestment, expected_capex = _derived_values(
        growth=scenario.terminal_growth,
        roic=audit_roic,
        margin=final.operating_margin,
        tax_rate=final.tax_rate,
        da_ratio=final.depreciation_amortization_pct_revenue,
        wc_ratio=final.working_capital_pct_revenue,
    )
    if not _close(audit_reinvestment, expected_reinvestment):
        raise ValueError("economic audit terminal reinvestment does not match g/ROIC")
    if not _close(audit_capex, expected_capex):
        raise ValueError("economic audit terminal capex does not match recomputation")

    return TerminalReinvestmentDerivation(
        scenario_id=scenario.id,
        terminal_roic_assumption=audit_roic,
        terminal_growth=audit_growth,
        operating_margin=final.operating_margin,
        tax_rate=final.tax_rate,
        depreciation_amortization_pct_revenue=(
            final.depreciation_amortization_pct_revenue
        ),
        working_capital_pct_revenue=final.working_capital_pct_revenue,
        prior_working_capital_pct_revenue=prior.working_capital_pct_revenue,
        final_revenue_growth=final.revenue_growth,
        terminal_reinvestment_fraction_of_nopat=audit_reinvestment,
        terminal_capex_pct_revenue=audit_capex,
    )
