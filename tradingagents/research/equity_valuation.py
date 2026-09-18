"""Bounded equity-cash-flow DCF for brokerage and financial platforms.

This calculator values cash flow available to common equity directly.  It does
not calculate industrial-company FCFF, use WACC, or bridge enterprise value with
corporate net debt/cash.  In particular, customer assets, customer deposits,
receivables, and omnibus cash are outside this API.

Callers supply explicit forecasts of net income attributable to common and the
capital that must be retained or reinvested.  Stock compensation remains an
economic expense in net income.  This first version accepts no noncash add-backs,
buyback schedule, or future share-count schedule, which prevents those items from
being counted twice.  The calculator does not certify regulatory capital
adequacy; retention forecasts are analyst assumptions that require independent
source linkage outside this module.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, localcontext
from functools import wraps
from typing import ParamSpec, TypeVar

from tradingagents.research.valuation import ValuationError, ValuationUnits

EQUITY_MODEL_SCOPE = (
    "Deterministic brokerage equity-cash-flow DCF only; not a regulatory capital "
    "adequacy certification or a production-acceptance claim."
)

ZERO = Decimal("0")
ONE = Decimal("1")
MAX_AMOUNT = Decimal("1e50")
MAX_COST_OF_EQUITY = Decimal("1")
MAX_TERMINAL_GROWTH = Decimal("0.25")
MIN_TERMINAL_GROWTH = Decimal("-0.999999")
MAX_FORECAST_PERIODS = 50
DISCOUNT_TIME_TOLERANCE = Decimal(
    "0.005479452054794520547945205479452054795"
)

P = ParamSpec("P")
R = TypeVar("R")


def _fixed_decimal_context(function: Callable[P, R]) -> Callable[P, R]:
    """Make public calculations independent of the process-global context."""

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


def _date(value: date, name: str) -> date:
    if not isinstance(value, date):
        raise ValuationError(f"{name} must be date")
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


def _calendar_anniversary(origin: date, years: int) -> date:
    """Return an end-of-month-preserving calendar anniversary."""

    try:
        target_year = origin.year + years
        origin_month_end = calendar.monthrange(origin.year, origin.month)[1]
        target_month_end = calendar.monthrange(target_year, origin.month)[1]
        day = target_month_end if origin.day == origin_month_end else min(
            origin.day, target_month_end
        )
        return date(target_year, origin.month, day)
    except (OverflowError, ValueError) as exc:
        raise ValuationError("forecast date is outside the supported calendar range") from exc


def _period_end_years(as_of_date: date, period_end: date) -> Decimal:
    """Return canonical period-end timing under a calendar-anniversary convention."""

    if period_end <= as_of_date:
        raise ValuationError("forecast period end must be after the valuation date")
    whole_years = period_end.year - as_of_date.year
    anniversary = _calendar_anniversary(as_of_date, whole_years)
    if anniversary > period_end:
        whole_years -= 1
        anniversary = _calendar_anniversary(as_of_date, whole_years)
    if anniversary == period_end:
        return Decimal(whole_years)
    next_anniversary = _calendar_anniversary(as_of_date, whole_years + 1)
    elapsed_days = (period_end - anniversary).days
    anniversary_days = (next_anniversary - anniversary).days
    if elapsed_days <= 0 or anniversary_days <= 0:
        raise ValuationError("invalid dated discount period")
    try:
        with localcontext() as context:
            context.prec = 40
            result = Decimal(whole_years) + Decimal(elapsed_days) / Decimal(
                anniversary_days
            )
    except (DecimalException, OverflowError) as exc:
        raise ValuationError("dated discount timing is outside the numeric domain") from exc
    return _decimal(result, "dated discount years")


@dataclass(frozen=True, slots=True)
class EquityForecastPeriod:
    """One forecast of common net income and required retained capital.

    ``discount_years`` is a caller assertion checked against the dated period end.
    The calculation always uses canonical dated timing.  Retention cannot be
    negative in this bounded API; a supported capital-release model needs a
    separate explicit contract rather than a negative reinvestment shortcut.
    """

    label: str
    period_start: date
    period_end: date
    discount_years: Decimal
    net_income_common: Decimal
    required_capital_retention: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValuationError("forecast period label is required")
        _date(self.period_start, "period_start")
        _date(self.period_end, "period_end")
        if self.period_start >= self.period_end:
            raise ValuationError("forecast period must end after it starts")
        _positive(self.discount_years, "discount_years")
        _decimal(self.net_income_common, "net_income_common")
        _nonnegative(self.required_capital_retention, "required_capital_retention")


@dataclass(frozen=True, slots=True)
class EquityDCFModelInput:
    """Inputs for a present equity DCF as of ``as_of_date``.

    ``current_net_income`` is the annual common-net-income opening anchor supplied
    by the caller (``net_income_common``, annual duration).  Current diluted shares
    are the common period-end instant share metric (``diluted_shares``).  Period net
    income forecasts are explicit amounts, not a mechanically inferred growth
    series, so loss-to-profit transitions remain representable without ambiguous
    percentage growth.
    """

    as_of_date: date
    current_net_income: Decimal
    periods: tuple[EquityForecastPeriod, ...]
    cost_of_equity: Decimal
    terminal_growth: Decimal
    current_diluted_shares: Decimal
    units: ValuationUnits

    def __post_init__(self) -> None:
        _date(self.as_of_date, "as_of_date")
        _decimal(self.current_net_income, "current_net_income")
        if not isinstance(self.periods, tuple):
            raise ValuationError("periods must be a tuple")
        if not self.periods or len(self.periods) > MAX_FORECAST_PERIODS:
            raise ValuationError(
                f"periods must contain 1 to {MAX_FORECAST_PERIODS} entries"
            )
        _positive(self.cost_of_equity, "cost_of_equity")
        if self.cost_of_equity > MAX_COST_OF_EQUITY:
            raise ValuationError(
                f"cost_of_equity must not exceed {MAX_COST_OF_EQUITY}"
            )
        _decimal(self.terminal_growth, "terminal_growth", max_abs=ONE)
        if not MIN_TERMINAL_GROWTH <= self.terminal_growth <= MAX_TERMINAL_GROWTH:
            raise ValuationError(
                "terminal_growth must be between "
                f"{MIN_TERMINAL_GROWTH} and {MAX_TERMINAL_GROWTH}"
            )
        if self.cost_of_equity <= self.terminal_growth:
            raise ValuationError("cost_of_equity must exceed terminal_growth")
        _positive(self.current_diluted_shares, "current_diluted_shares")
        if not isinstance(self.units, ValuationUnits):
            raise ValuationError("units must be ValuationUnits")

        labels: set[str] = set()
        previous_end = self.as_of_date
        previous_discount_years = ZERO
        for period in self.periods:
            if not isinstance(period, EquityForecastPeriod):
                raise ValuationError("periods must contain EquityForecastPeriod entries")
            if period.label in labels:
                raise ValuationError("forecast period labels must be unique")
            labels.add(period.label)
            if period.period_start != previous_end:
                raise ValuationError(
                    "forecast periods must start at as_of_date and remain contiguous"
                )
            dated_discount_years = _period_end_years(self.as_of_date, period.period_end)
            if abs(period.discount_years - dated_discount_years) > DISCOUNT_TIME_TOLERANCE:
                raise ValuationError(
                    f"{period.label} discount_years does not match dated period-end timing"
                )
            if period.discount_years <= previous_discount_years:
                raise ValuationError("discount_years must strictly increase")
            previous_end = period.period_end
            previous_discount_years = period.discount_years


@dataclass(frozen=True, slots=True)
class EquityForecastCashFlow:
    label: str
    period_start: date
    period_end: date
    net_income_common: Decimal
    required_capital_retention: Decimal
    equity_cash_flow: Decimal
    discount_years: Decimal
    discount_factor: Decimal
    present_value: Decimal


@dataclass(frozen=True, slots=True)
class EquityDCFResult:
    as_of_date: date
    current_net_income: Decimal
    forecasts: tuple[EquityForecastCashFlow, ...]
    explicit_period_present_value: Decimal
    terminal_equity_cash_flow: Decimal
    terminal_value_at_horizon: Decimal
    terminal_value_present_value: Decimal
    total_equity_present_value: Decimal
    current_diluted_shares: Decimal
    value_per_current_diluted_share: Decimal
    terminal_value_share_of_total_equity_present_value: Decimal | None
    cost_of_equity: Decimal
    terminal_growth: Decimal
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    units: ValuationUnits
    scope: str = EQUITY_MODEL_SCOPE


@_fixed_decimal_context
def forecast_equity_cash_flow(
    model: EquityDCFModelInput,
) -> tuple[EquityForecastCashFlow, ...]:
    """Calculate dated equity cash flow without noncash or SBC add-backs."""

    forecasts: list[EquityForecastCashFlow] = []
    for period in model.periods:
        equity_cash_flow = period.net_income_common - period.required_capital_retention
        discount_years = _period_end_years(model.as_of_date, period.period_end)
        discount_factor = _pow(ONE + model.cost_of_equity, discount_years)
        present_value = equity_cash_flow / discount_factor
        for name, value in (
            ("equity_cash_flow", equity_cash_flow),
            ("discount_factor", discount_factor),
            ("present_value", present_value),
        ):
            _decimal(value, name)
        forecasts.append(
            EquityForecastCashFlow(
                label=period.label,
                period_start=period.period_start,
                period_end=period.period_end,
                net_income_common=period.net_income_common,
                required_capital_retention=period.required_capital_retention,
                equity_cash_flow=equity_cash_flow,
                discount_years=discount_years,
                discount_factor=discount_factor,
                present_value=present_value,
            )
        )
    return tuple(forecasts)


@_fixed_decimal_context
def equity_dcf_valuation(model: EquityDCFModelInput) -> EquityDCFResult:
    """Value explicit common-equity cash flows plus a Gordon terminal value."""

    forecasts = forecast_equity_cash_flow(model)
    final_period = model.periods[-1]
    final_days = (final_period.period_end - final_period.period_start).days
    if not 360 <= final_days <= 371:
        raise ValuationError("Gordon growth requires a full annual terminal forecast period")

    final_equity_cash_flow = forecasts[-1].equity_cash_flow
    if final_equity_cash_flow <= ZERO:
        raise ValuationError(
            "positive normalized final equity cash flow is required for Gordon growth"
        )
    terminal_equity_cash_flow = final_equity_cash_flow * (ONE + model.terminal_growth)
    terminal_value = terminal_equity_cash_flow / (
        model.cost_of_equity - model.terminal_growth
    )
    terminal_present_value = terminal_value / forecasts[-1].discount_factor
    explicit_present_value = sum((item.present_value for item in forecasts), ZERO)
    total_equity_present_value = explicit_present_value + terminal_present_value
    per_share = model.units.per_share(
        total_equity_present_value, model.current_diluted_shares
    )
    terminal_share = (
        terminal_present_value / total_equity_present_value
        if total_equity_present_value != ZERO
        else None
    )
    for name, value in (
        ("terminal_equity_cash_flow", terminal_equity_cash_flow),
        ("terminal_value", terminal_value),
        ("terminal_present_value", terminal_present_value),
        ("explicit_period_present_value", explicit_present_value),
        ("total_equity_present_value", total_equity_present_value),
        ("value_per_current_diluted_share", per_share),
    ):
        _decimal(value, name)
    if terminal_share is not None:
        _decimal(terminal_share, "terminal_value_share")

    assumptions = (
        "Equity cash flow equals net income attributable to common less explicit "
        "required capital retention.",
        "No noncash adjustment, including stock compensation, is added back.",
        "Cash flows are discounted at the cost of equity using canonical dated "
        "period ends; mid-year timing is not modeled.",
        "The terminal value applies Gordon growth to the final full-year equity "
        "cash flow.",
        "Present per-share value uses current diluted common shares; buybacks and "
        "future share-count changes are not modeled.",
    )
    limitations = (
        "Required capital retention is an analyst assumption requiring independent "
        "source linkage; this calculation does not certify regulatory capital adequacy.",
        "Customer assets, customer deposits, receivables, and omnibus cash are not "
        "corporate net debt/cash adjustments in this model.",
        "The current net-income anchor and explicit forecasts must be normalized and "
        "basis-consistent by the caller; this calculator does not normalize accounting data.",
        "Future dilution, repurchases, dividends, external capital needs, and capital "
        "releases require separate supported schedules and are not inferred here.",
    )
    return EquityDCFResult(
        as_of_date=model.as_of_date,
        current_net_income=model.current_net_income,
        forecasts=forecasts,
        explicit_period_present_value=explicit_present_value,
        terminal_equity_cash_flow=terminal_equity_cash_flow,
        terminal_value_at_horizon=terminal_value,
        terminal_value_present_value=terminal_present_value,
        total_equity_present_value=total_equity_present_value,
        current_diluted_shares=model.current_diluted_shares,
        value_per_current_diluted_share=per_share,
        terminal_value_share_of_total_equity_present_value=terminal_share,
        cost_of_equity=model.cost_of_equity,
        terminal_growth=model.terminal_growth,
        assumptions=assumptions,
        limitations=limitations,
        units=model.units,
    )
