"""Deterministic, bounded foundation models for company valuation.

This module deliberately does not choose assumptions, probabilities, peer sets, or
valuation methods.  Callers must supply explicit, supportable inputs.  The models
here are a foundation for M3, not sector-calibrated models and not investment advice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, DecimalException, localcontext
from functools import wraps
from typing import Literal, ParamSpec, TypeVar

MODEL_SCOPE = (
    "M3 deterministic foundation only; assumptions require independent support and "
    "company/sector schedules may be needed before a target is supportable."
)

ZERO = Decimal("0")
ONE = Decimal("1")
MAX_AMOUNT = Decimal("1e50")
MAX_RATE = Decimal("10")
MAX_FORECAST_PERIODS = 50
MAX_SENSITIVITY_CELLS = 400

MarginBasis = Literal["after_sbc", "before_sbc"]
ValueBasis = Literal["enterprise_value", "equity_value"]
ComparableMetric = Literal["revenue", "ebitda", "ebit", "fcff", "net_income", "eps", "book_value"]
ReverseDriver = Literal["terminal_growth", "discount_rate"]

P = ParamSpec("P")
R = TypeVar("R")


class ValuationError(ValueError):
    """Raised when a valuation is unsupported or an input is invalid."""


def _fixed_decimal_context(function: Callable[P, R]) -> Callable[P, R]:
    """Make public calculations independent of the process-global Decimal context."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext() as context:
            context.prec = 40
            return function(*args, **kwargs)

    return wrapped


def _decimal(value: Decimal, name: str, *, max_abs: Decimal = MAX_AMOUNT) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValuationError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValuationError(f"{name} must be finite")
    if abs(value) > max_abs:
        raise ValuationError(f"{name} exceeds the bounded model range")
    return value


def _rate(value: Decimal, name: str) -> Decimal:
    return _decimal(value, name, max_abs=MAX_RATE)


def _nonnegative(value: Decimal, name: str) -> Decimal:
    value = _decimal(value, name)
    if value < ZERO:
        raise ValuationError(f"{name} must be nonnegative")
    return value


def _positive(value: Decimal, name: str) -> Decimal:
    value = _decimal(value, name)
    if value <= ZERO:
        raise ValuationError(f"{name} must be positive")
    return value


def _bounded_rate(value: Decimal, name: str, low: Decimal, high: Decimal) -> Decimal:
    value = _rate(value, name)
    if value < low or value > high:
        raise ValuationError(f"{name} must be between {low} and {high}")
    return value


def _pow(base: Decimal, exponent: Decimal) -> Decimal:
    if base <= ZERO:
        raise ValuationError("fractional compounding requires a positive base")
    try:
        with localcontext() as context:
            context.prec = 40
            result = context.power(base, exponent)
    except (DecimalException, OverflowError) as exc:
        raise ValuationError("compounding is outside the bounded numeric domain") from exc
    return _decimal(result, "compounded value")


@dataclass(frozen=True, slots=True)
class ValuationUnits:
    """Explicit bridge from raw model values to currency and share units.

    Every aggregate monetary input/output is a raw value whose economic amount is
    ``raw value * amount_scale`` currency units.  Diluted shares similarly equal
    ``raw shares * share_scale`` shares.  Per-share outputs apply both scales.
    """

    currency: str
    amount_scale: Decimal
    share_scale: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValuationError("currency is required")
        _positive(self.amount_scale, "amount_scale")
        _positive(self.share_scale, "share_scale")

    @_fixed_decimal_context
    def per_share(self, raw_amount: Decimal, raw_shares: Decimal) -> Decimal:
        _decimal(raw_amount, "raw_amount")
        _positive(raw_shares, "raw_shares")
        return raw_amount * self.amount_scale / (raw_shares * self.share_scale)


@dataclass(frozen=True, slots=True)
class ForecastPeriod:
    """One explicit forecast period; growth is for this period, not annualized."""

    label: str
    period_start: date
    period_end: date
    discount_years: Decimal
    revenue_growth: Decimal
    operating_margin: Decimal
    operating_margin_basis: MarginBasis
    tax_rate: Decimal
    depreciation_amortization_pct_revenue: Decimal
    capex_pct_revenue: Decimal
    working_capital_pct_revenue: Decimal
    sbc_pct_revenue: Decimal
    external_funding_required: bool = False

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValuationError("forecast period label is required")
        if self.period_start >= self.period_end:
            raise ValuationError("forecast period must end after it starts")
        _positive(self.discount_years, "discount_years")
        _bounded_rate(self.revenue_growth, "revenue_growth", Decimal("-0.999999"), MAX_RATE)
        _bounded_rate(self.operating_margin, "operating_margin", -MAX_RATE, ONE)
        if self.operating_margin_basis not in {"after_sbc", "before_sbc"}:
            raise ValuationError("unsupported operating_margin_basis")
        _bounded_rate(self.tax_rate, "tax_rate", ZERO, ONE)
        _bounded_rate(
            self.depreciation_amortization_pct_revenue,
            "depreciation_amortization_pct_revenue",
            ZERO,
            MAX_RATE,
        )
        _bounded_rate(self.capex_pct_revenue, "capex_pct_revenue", ZERO, MAX_RATE)
        _bounded_rate(
            self.working_capital_pct_revenue,
            "working_capital_pct_revenue",
            -MAX_RATE,
            MAX_RATE,
        )
        _bounded_rate(self.sbc_pct_revenue, "sbc_pct_revenue", ZERO, ONE)
        if not isinstance(self.external_funding_required, bool):
            raise ValuationError("external_funding_required must be bool")


@dataclass(frozen=True, slots=True)
class FCFFModelInput:
    as_of_date: date
    current_revenue: Decimal
    current_working_capital: Decimal
    periods: tuple[ForecastPeriod, ...]
    discount_rate: Decimal
    terminal_growth: Decimal
    net_debt: Decimal
    current_diluted_shares: Decimal
    units: ValuationUnits
    funding_caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _positive(self.current_revenue, "current_revenue")
        _decimal(self.current_working_capital, "current_working_capital")
        if not self.periods or len(self.periods) > MAX_FORECAST_PERIODS:
            raise ValuationError(f"periods must contain 1 to {MAX_FORECAST_PERIODS} entries")
        _bounded_rate(self.discount_rate, "discount_rate", Decimal("-0.999999"), MAX_RATE)
        _bounded_rate(self.terminal_growth, "terminal_growth", Decimal("-0.999999"), MAX_RATE)
        if self.discount_rate <= self.terminal_growth:
            raise ValuationError("discount_rate must exceed terminal_growth")
        _decimal(self.net_debt, "net_debt")
        _positive(self.current_diluted_shares, "current_diluted_shares")

        labels: set[str] = set()
        previous_end = self.as_of_date
        previous_discount_years = ZERO
        for period in self.periods:
            if period.label in labels:
                raise ValuationError("forecast period labels must be unique")
            labels.add(period.label)
            if period.period_start < previous_end:
                raise ValuationError("forecast periods must be ordered and non-overlapping")
            if period.discount_years <= previous_discount_years:
                raise ValuationError("discount_years must strictly increase")
            previous_end = period.period_end
            previous_discount_years = period.discount_years
        if any(period.external_funding_required for period in self.periods):
            if not self.funding_caveats or any(
                not caveat.strip() for caveat in self.funding_caveats
            ):
                raise ValuationError("external funding requirements need explicit funding_caveats")
        elif any(not caveat.strip() for caveat in self.funding_caveats):
            raise ValuationError("funding_caveats cannot contain blank text")


@dataclass(frozen=True, slots=True)
class ForecastCashFlow:
    label: str
    revenue: Decimal
    operating_profit_before_tax: Decimal
    economic_sbc: Decimal
    nopat: Decimal
    depreciation_amortization: Decimal
    capex: Decimal
    working_capital: Decimal
    change_in_working_capital: Decimal
    fcff: Decimal
    discount_years: Decimal
    discount_factor: Decimal
    present_value: Decimal


@dataclass(frozen=True, slots=True)
class DCFResult:
    forecasts: tuple[ForecastCashFlow, ...]
    explicit_period_present_value: Decimal
    terminal_value_at_horizon: Decimal
    terminal_value_present_value: Decimal
    enterprise_value: Decimal
    net_debt: Decimal
    equity_value: Decimal
    value_per_current_diluted_share: Decimal
    terminal_value_share_of_enterprise_value: Decimal | None
    limitations: tuple[str, ...]
    units: ValuationUnits
    scope: str = MODEL_SCOPE


@_fixed_decimal_context
def forecast_fcff(model: FCFFModelInput) -> tuple[ForecastCashFlow, ...]:
    """Build coherent revenue-to-FCFF forecasts without adding back economic SBC.

    ``after_sbc`` margins already include SBC.  ``before_sbc`` margins are reduced
    by the explicitly supplied SBC rate.  In both cases SBC is never added back;
    current diluted shares are therefore not being used as a second adjustment for
    forecast SBC.  The caller must separately assess whether future dilution makes
    the constant-current-share presentation inadequate.
    """

    revenue = model.current_revenue
    working_capital = model.current_working_capital
    forecasts: list[ForecastCashFlow] = []
    for period in model.periods:
        revenue *= ONE + period.revenue_growth
        economic_sbc = revenue * period.sbc_pct_revenue
        operating_profit = revenue * period.operating_margin
        if period.operating_margin_basis == "before_sbc":
            operating_profit -= economic_sbc
        # Do not invent immediate tax assets for loss periods.  A caller that can
        # support an NOL valuation needs a separate tax schedule beyond M3 scope.
        nopat = (
            operating_profit * (ONE - period.tax_rate)
            if operating_profit > ZERO
            else operating_profit
        )
        depreciation = revenue * period.depreciation_amortization_pct_revenue
        capex = revenue * period.capex_pct_revenue
        next_working_capital = revenue * period.working_capital_pct_revenue
        change_in_working_capital = next_working_capital - working_capital
        fcff = nopat + depreciation - capex - change_in_working_capital
        discount_factor = _pow(ONE + model.discount_rate, period.discount_years)
        present_value = fcff / discount_factor
        for name, value in (
            ("revenue", revenue),
            ("operating_profit", operating_profit),
            ("nopat", nopat),
            ("fcff", fcff),
            ("present_value", present_value),
        ):
            _decimal(value, name)
        forecasts.append(
            ForecastCashFlow(
                label=period.label,
                revenue=revenue,
                operating_profit_before_tax=operating_profit,
                economic_sbc=economic_sbc,
                nopat=nopat,
                depreciation_amortization=depreciation,
                capex=capex,
                working_capital=next_working_capital,
                change_in_working_capital=change_in_working_capital,
                fcff=fcff,
                discount_years=period.discount_years,
                discount_factor=discount_factor,
                present_value=present_value,
            )
        )
        working_capital = next_working_capital
    return tuple(forecasts)


@_fixed_decimal_context
def dcf_valuation(model: FCFFModelInput) -> DCFResult:
    """Value forecast FCFF plus a Gordon-growth terminal value as of today."""

    forecasts = forecast_fcff(model)
    # A quarter/stub cash flow cannot be capitalized as an annual perpetuity.
    # Fiscal 52/53-week years are accepted, but no implicit annualization is done.
    final_days = (model.periods[-1].period_end - model.periods[-1].period_start).days
    if not 360 <= final_days <= 371:
        raise ValuationError("Gordon growth requires a full annual terminal forecast period")
    final_fcff = forecasts[-1].fcff
    if final_fcff <= ZERO:
        raise ValuationError("positive normalized final FCFF is required for Gordon growth")
    terminal_value = (
        final_fcff * (ONE + model.terminal_growth) / (model.discount_rate - model.terminal_growth)
    )
    if terminal_value < ZERO:
        raise ValuationError("terminal value cannot be negative")
    terminal_present_value = terminal_value / forecasts[-1].discount_factor
    explicit_present_value = sum((item.present_value for item in forecasts), ZERO)
    enterprise_value = explicit_present_value + terminal_present_value
    equity_value = enterprise_value - model.net_debt
    per_share = model.units.per_share(equity_value, model.current_diluted_shares)
    terminal_share = terminal_present_value / enterprise_value if enterprise_value != ZERO else None
    for name, value in (
        ("terminal_value", terminal_value),
        ("enterprise_value", enterprise_value),
        ("equity_value", equity_value),
        ("per_share", per_share),
    ):
        _decimal(value, name)
    limitations = (
        "Present equity value uses current diluted shares; future dilution and repurchases are not modeled.",
        "SBC is treated as an economic cost and is not added back to FCFF.",
        *model.funding_caveats,
    )
    return DCFResult(
        forecasts=forecasts,
        explicit_period_present_value=explicit_present_value,
        terminal_value_at_horizon=terminal_value,
        terminal_value_present_value=terminal_present_value,
        enterprise_value=enterprise_value,
        net_debt=model.net_debt,
        equity_value=equity_value,
        value_per_current_diluted_share=per_share,
        terminal_value_share_of_enterprise_value=terminal_share,
        limitations=limitations,
        units=model.units,
    )


@dataclass(frozen=True, slots=True)
class SensitivityPoint:
    discount_rate: Decimal
    terminal_growth: Decimal
    enterprise_value: Decimal
    equity_value: Decimal
    value_per_current_diluted_share: Decimal
    units: ValuationUnits


@_fixed_decimal_context
def dcf_sensitivity(
    model: FCFFModelInput,
    *,
    discount_rates: tuple[Decimal, ...],
    terminal_growth_rates: tuple[Decimal, ...],
) -> tuple[SensitivityPoint, ...]:
    """Return an ordered grid; no scenario weights or averages are assigned."""

    if not discount_rates or not terminal_growth_rates:
        raise ValuationError("sensitivity axes cannot be empty")
    if len(discount_rates) * len(terminal_growth_rates) > MAX_SENSITIVITY_CELLS:
        raise ValuationError("sensitivity grid exceeds the bounded cell limit")
    points: list[SensitivityPoint] = []
    for discount_rate in discount_rates:
        for terminal_growth in terminal_growth_rates:
            result = dcf_valuation(
                replace(model, discount_rate=discount_rate, terminal_growth=terminal_growth)
            )
            points.append(
                SensitivityPoint(
                    discount_rate=discount_rate,
                    terminal_growth=terminal_growth,
                    enterprise_value=result.enterprise_value,
                    equity_value=result.equity_value,
                    value_per_current_diluted_share=result.value_per_current_diluted_share,
                    units=model.units,
                )
            )
    return tuple(points)


@dataclass(frozen=True, slots=True)
class SOTPSegment:
    """A segment value before parent-level corporate costs, debt, and other claims."""

    name: str
    enterprise_value: Decimal

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValuationError("segment name is required")
        _nonnegative(self.enterprise_value, "segment enterprise_value")


@dataclass(frozen=True, slots=True)
class SOTPInput:
    segments: tuple[SOTPSegment, ...]
    corporate_cost_present_value: Decimal
    net_debt: Decimal
    other_claims: Decimal
    non_operating_assets: Decimal
    current_diluted_shares: Decimal
    units: ValuationUnits

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValuationError("SOTP requires at least one segment")
        names = [segment.name for segment in self.segments]
        if len(names) != len(set(names)):
            raise ValuationError("SOTP segment names must be unique")
        _nonnegative(self.corporate_cost_present_value, "corporate_cost_present_value")
        _decimal(self.net_debt, "net_debt")
        _nonnegative(self.other_claims, "other_claims")
        _nonnegative(self.non_operating_assets, "non_operating_assets")
        _positive(self.current_diluted_shares, "current_diluted_shares")


@dataclass(frozen=True, slots=True)
class SOTPResult:
    gross_segment_enterprise_value: Decimal
    corporate_cost_present_value: Decimal
    consolidated_enterprise_value: Decimal
    non_operating_assets: Decimal
    net_debt: Decimal
    other_claims: Decimal
    equity_value: Decimal
    value_per_current_diluted_share: Decimal
    units: ValuationUnits
    scope: str = MODEL_SCOPE


@_fixed_decimal_context
def sotp_valuation(model: SOTPInput) -> SOTPResult:
    """Aggregate segment EVs, then apply each parent-level adjustment exactly once."""

    gross = sum((segment.enterprise_value for segment in model.segments), ZERO)
    consolidated = gross - model.corporate_cost_present_value
    equity = consolidated + model.non_operating_assets - model.net_debt - model.other_claims
    per_share = model.units.per_share(equity, model.current_diluted_shares)
    _decimal(per_share, "SOTP per_share")
    return SOTPResult(
        gross_segment_enterprise_value=gross,
        corporate_cost_present_value=model.corporate_cost_present_value,
        consolidated_enterprise_value=consolidated,
        non_operating_assets=model.non_operating_assets,
        net_debt=model.net_debt,
        other_claims=model.other_claims,
        equity_value=equity,
        value_per_current_diluted_share=per_share,
        units=model.units,
    )


_ENTERPRISE_METRICS = {"revenue", "ebitda", "ebit", "fcff"}
_EQUITY_METRICS = {"net_income", "eps", "book_value"}
_EARNINGS_METRICS = {"ebitda", "ebit", "fcff", "net_income", "eps"}


@dataclass(frozen=True, slots=True)
class ComparableObservation:
    name: str
    numerator_value: Decimal
    numerator_basis: ValueBasis
    metric_value: Decimal
    metric_basis: ComparableMetric
    period: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.period.strip():
            raise ValuationError("comparable name and period are required")
        _positive(self.numerator_value, "comparable numerator_value")
        _decimal(self.metric_value, "comparable metric_value")
        _validate_comparable_basis(self.numerator_basis, self.metric_basis)
        if self.metric_value <= ZERO:
            if self.metric_basis in _EARNINGS_METRICS:
                raise ValuationError("negative or zero earnings cannot receive a multiple")
            raise ValuationError("comparable metric_value must be positive")


@dataclass(frozen=True, slots=True)
class ComparableMultiple:
    source_name: str
    multiple: Decimal
    numerator_basis: ValueBasis
    metric_basis: ComparableMetric
    period: str

    def __post_init__(self) -> None:
        if not self.source_name.strip() or not self.period.strip():
            raise ValuationError("comparable source name and period are required")
        _positive(self.multiple, "comparable multiple")
        _validate_comparable_basis(self.numerator_basis, self.metric_basis)


@dataclass(frozen=True, slots=True)
class ComparableValue:
    source_name: str
    implied_value: Decimal
    value_basis: ValueBasis
    metric_basis: ComparableMetric
    period: str


def _validate_comparable_basis(value_basis: ValueBasis, metric_basis: ComparableMetric) -> None:
    if value_basis == "enterprise_value" and metric_basis not in _ENTERPRISE_METRICS:
        raise ValuationError("enterprise-value multiple has an unmatched metric basis")
    if value_basis == "equity_value" and metric_basis not in _EQUITY_METRICS:
        raise ValuationError("equity-value multiple has an unmatched metric basis")
    if value_basis not in {"enterprise_value", "equity_value"}:
        raise ValuationError("unsupported comparable value basis")


@_fixed_decimal_context
def comparable_multiple(observation: ComparableObservation) -> ComparableMultiple:
    """Derive one traceable multiple; peer aggregation is intentionally caller-owned."""

    multiple = observation.numerator_value / observation.metric_value
    _positive(multiple, "comparable multiple")
    return ComparableMultiple(
        source_name=observation.name,
        multiple=multiple,
        numerator_basis=observation.numerator_basis,
        metric_basis=observation.metric_basis,
        period=observation.period,
    )


@_fixed_decimal_context
def apply_comparable_multiple(
    multiple: ComparableMultiple,
    *,
    target_metric_value: Decimal,
    target_metric_basis: ComparableMetric,
    target_period: str,
) -> ComparableValue:
    """Apply a multiple only to an exactly matched metric and forecast period basis."""

    _positive(target_metric_value, "target_metric_value")
    if target_metric_basis != multiple.metric_basis or target_period != multiple.period:
        raise ValuationError("target and comparable metric/period bases must match")
    _validate_comparable_basis(multiple.numerator_basis, target_metric_basis)
    implied_value = multiple.multiple * target_metric_value
    _positive(implied_value, "implied comparable value")
    return ComparableValue(
        source_name=multiple.source_name,
        implied_value=implied_value,
        value_basis=multiple.numerator_basis,
        metric_basis=target_metric_basis,
        period=target_period,
    )


CashFlowKind = Literal["dividend", "other_distribution", "capital_contribution"]


@dataclass(frozen=True, slots=True)
class EquityCashFlow:
    """Aggregate owner cash flow; amounts are nonnegative and direction follows kind."""

    month: Decimal
    amount: Decimal
    kind: CashFlowKind

    def __post_init__(self) -> None:
        _positive(self.month, "cash flow month")
        _nonnegative(self.amount, "cash flow amount")
        if self.kind not in {"dividend", "other_distribution", "capital_contribution"}:
            raise ValuationError("unsupported owner cash flow kind")


@dataclass(frozen=True, slots=True)
class TwelveMonthRollForwardInput:
    present_equity_value: Decimal
    annual_required_return: Decimal
    current_diluted_shares: Decimal
    cash_flows: tuple[EquityCashFlow, ...]
    units: ValuationUnits
    horizon_months: Decimal = Decimal("12")

    def __post_init__(self) -> None:
        _decimal(self.present_equity_value, "present_equity_value")
        _bounded_rate(
            self.annual_required_return,
            "annual_required_return",
            Decimal("-0.999999"),
            MAX_RATE,
        )
        _positive(self.current_diluted_shares, "current_diluted_shares")
        if self.horizon_months != Decimal("12"):
            raise ValuationError("this API supports an explicit 12-month roll-forward only")
        for cash_flow in self.cash_flows:
            if cash_flow.month > self.horizon_months:
                raise ValuationError("owner cash flow falls outside the 12-month horizon")


@dataclass(frozen=True, slots=True)
class TwelveMonthRollForwardResult:
    present_equity_value: Decimal
    future_equity_value_ex_cash_flows: Decimal
    present_value_per_current_diluted_share: Decimal
    future_value_per_current_diluted_share: Decimal
    nominal_distributions_per_current_diluted_share: Decimal
    convention: str
    units: ValuationUnits
    scope: str = MODEL_SCOPE


@_fixed_decimal_context
def twelve_month_value_roll_forward(
    model: TwelveMonthRollForwardInput,
) -> TwelveMonthRollForwardResult:
    """Accrete present value and subtract/add explicitly timed owner cash flows.

    Positive dividends/distributions leave equity; capital contributions enter it.
    Shares remain at the current diluted count.  Repurchases are intentionally not
    accepted because a valid bridge would also need a price/share-count schedule.
    """

    growth_base = ONE + model.annual_required_return
    future_value = model.present_equity_value * growth_base
    nominal_distributions = ZERO
    for cash_flow in model.cash_flows:
        remaining_years = (model.horizon_months - cash_flow.month) / Decimal("12")
        value_at_horizon = cash_flow.amount * _pow(growth_base, remaining_years)
        if cash_flow.kind == "capital_contribution":
            future_value += value_at_horizon
        else:
            future_value -= value_at_horizon
            nominal_distributions += cash_flow.amount
    _decimal(future_value, "future_equity_value")
    return TwelveMonthRollForwardResult(
        present_equity_value=model.present_equity_value,
        future_equity_value_ex_cash_flows=future_value,
        present_value_per_current_diluted_share=model.units.per_share(
            model.present_equity_value, model.current_diluted_shares
        ),
        future_value_per_current_diluted_share=model.units.per_share(
            future_value, model.current_diluted_shares
        ),
        nominal_distributions_per_current_diluted_share=model.units.per_share(
            nominal_distributions, model.current_diluted_shares
        ),
        convention=(
            "Accrete present aggregate equity value at the required return; compound timed "
            "owner cash flows to month 12; hold current diluted shares constant."
        ),
        units=model.units,
    )


@dataclass(frozen=True, slots=True)
class PerShareCashFlow:
    year: Decimal
    amount: Decimal
    kind: Literal["dividend", "other_distribution"] = "dividend"

    def __post_init__(self) -> None:
        _positive(self.year, "per-share cash flow year")
        _nonnegative(self.amount, "per-share cash flow amount")
        if self.kind not in {"dividend", "other_distribution"}:
            raise ValuationError("unsupported per-share cash flow kind")


@dataclass(frozen=True, slots=True)
class ThreeYearReturnInput:
    beginning_price: Decimal
    ending_price: Decimal
    cash_flows_per_share: tuple[PerShareCashFlow, ...] = ()
    horizon_years: Decimal = Decimal("3")

    def __post_init__(self) -> None:
        _positive(self.beginning_price, "beginning_price")
        _nonnegative(self.ending_price, "ending_price")
        if self.horizon_years != Decimal("3"):
            raise ValuationError("this API supports an explicit three-year return only")
        for cash_flow in self.cash_flows_per_share:
            if cash_flow.year > self.horizon_years:
                raise ValuationError("per-share cash flow falls outside the three-year horizon")


@dataclass(frozen=True, slots=True)
class ShareholderReturnResult:
    total_cash_distributions_per_share: Decimal
    holding_period_return: Decimal
    annualized_return: Decimal
    convention: str


@_fixed_decimal_context
def three_year_shareholder_return(model: ThreeYearReturnInput) -> ShareholderReturnResult:
    """Calculate price-plus-cash return with distributions not reinvested."""

    distributions = sum((cash_flow.amount for cash_flow in model.cash_flows_per_share), ZERO)
    terminal_wealth = model.ending_price + distributions
    wealth_multiple = terminal_wealth / model.beginning_price
    if wealth_multiple < ZERO:
        raise ValuationError("terminal shareholder wealth cannot be negative")
    annualized = _pow(wealth_multiple, ONE / model.horizon_years) - ONE
    return ShareholderReturnResult(
        total_cash_distributions_per_share=distributions,
        holding_period_return=wealth_multiple - ONE,
        annualized_return=annualized,
        convention=(
            "Per-share cash distributions are received but not reinvested or compounded; "
            "annualization uses the total terminal wealth over exactly three years."
        ),
    )


@dataclass(frozen=True, slots=True)
class ReverseDCFResult:
    driver: ReverseDriver
    solved_value: Decimal
    implied_value_per_share: Decimal
    target_value_per_share: Decimal
    iterations: int
    caveat: str = (
        "One named driver changed while all other inputs stayed fixed; many other assumption "
        "sets can fit the same target value."
    )


@_fixed_decimal_context
def reverse_dcf(
    model: FCFFModelInput,
    *,
    target_value_per_share: Decimal,
    driver: ReverseDriver,
    lower: Decimal,
    upper: Decimal,
    tolerance: Decimal = Decimal("0.000001"),
    max_iterations: int = 100,
) -> ReverseDCFResult:
    """Solve terminal growth or discount rate by bounded bisection.

    The endpoints must bracket a root.  Failure to bracket is an error rather than
    an extrapolated or fabricated answer.
    """

    _nonnegative(target_value_per_share, "target_value_per_share")
    _rate(lower, "lower")
    _rate(upper, "upper")
    _positive(tolerance, "tolerance")
    if lower >= upper:
        raise ValuationError("reverse-DCF lower bound must be below upper bound")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise ValuationError("max_iterations must be int")
    if max_iterations < 1 or max_iterations > 256:
        raise ValuationError("max_iterations must be between 1 and 256")
    if driver not in {"terminal_growth", "discount_rate"}:
        raise ValuationError("unsupported reverse-DCF driver")

    def objective(value: Decimal) -> Decimal:
        trial = replace(model, **{driver: value})
        return dcf_valuation(trial).value_per_current_diluted_share - target_value_per_share

    lower_value = objective(lower)
    upper_value = objective(upper)
    if abs(lower_value) <= tolerance:
        return ReverseDCFResult(
            driver, lower, lower_value + target_value_per_share, target_value_per_share, 0
        )
    if abs(upper_value) <= tolerance:
        return ReverseDCFResult(
            driver, upper, upper_value + target_value_per_share, target_value_per_share, 0
        )
    if lower_value * upper_value > ZERO:
        raise ValuationError("reverse-DCF bounds do not bracket a root")

    midpoint = lower
    midpoint_value = lower_value
    for iteration in range(1, max_iterations + 1):
        midpoint = (lower + upper) / Decimal("2")
        midpoint_value = objective(midpoint)
        if abs(midpoint_value) <= tolerance:
            return ReverseDCFResult(
                driver=driver,
                solved_value=midpoint,
                implied_value_per_share=midpoint_value + target_value_per_share,
                target_value_per_share=target_value_per_share,
                iterations=iteration,
            )
        if lower_value * midpoint_value <= ZERO:
            upper = midpoint
        else:
            lower = midpoint
            lower_value = midpoint_value
    raise ValuationError("reverse-DCF did not converge within max_iterations")
