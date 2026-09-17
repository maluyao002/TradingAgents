"""Bounded, one-period statement mechanics; not a calibrated company model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

ZERO = Decimal("0")
MAX_AMOUNT = Decimal("1e30")
MAX_UNITS = Decimal("1e18")
MODEL_SCOPE = (
    "Uncalibrated M3 mechanics only: inputs require independent evidence and do not "
    "establish company-model or valuation acceptance."
)


class FinancialModelError(ValueError):
    """An input is missing, incompatible, or outside the bounded model domain."""


def _decimal(value: Decimal, name: str, *, allow_negative: bool = True) -> Decimal:
    if not isinstance(value, Decimal):
        raise FinancialModelError(f"{name} must be Decimal")
    if not value.is_finite() or abs(value) > MAX_AMOUNT:
        raise FinancialModelError(f"{name} is outside the bounded model range")
    if not allow_negative and value < ZERO:
        raise FinancialModelError(f"{name} must be nonnegative")
    return value


def _units(value: Decimal, name: str) -> Decimal:
    value = _decimal(value, name, allow_negative=False)
    if value > MAX_UNITS:
        raise FinancialModelError(f"{name} exceeds the bounded unit range")
    return value


@dataclass(frozen=True, slots=True)
class StatementUnits:
    currency: str
    amount_scale: Decimal
    share_scale: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isupper()
        ):
            raise FinancialModelError("currency must be an ISO-style uppercase code")
        if _decimal(self.amount_scale, "amount_scale", allow_negative=False) <= ZERO:
            raise FinancialModelError("amount_scale must be positive")
        if _decimal(self.share_scale, "share_scale", allow_negative=False) <= ZERO:
            raise FinancialModelError("share_scale must be positive")


@dataclass(frozen=True, slots=True)
class OpeningBalance:
    units: StatementUnits
    assets: Decimal
    liabilities: Decimal
    equity: Decimal
    cash: Decimal
    net_ppe: Decimal
    debt: Decimal
    shares: Decimal

    def __post_init__(self) -> None:
        for name in ("assets", "liabilities", "equity", "cash", "net_ppe", "debt"):
            _decimal(getattr(self, name), name, allow_negative=False)
        _units(self.shares, "shares")
        if self.shares <= ZERO:
            raise FinancialModelError("shares must be positive")
        if self.assets != self.liabilities + self.equity:
            raise FinancialModelError("opening balance does not reconcile")
        if self.debt > self.liabilities:
            raise FinancialModelError("opening debt cannot exceed liabilities")
        if self.cash + self.net_ppe > self.assets:
            raise FinancialModelError("cash and net_ppe exceed opening assets")


@dataclass(frozen=True, slots=True)
class PeriodDrivers:
    units: StatementUnits
    revenue: Decimal
    cogs: Decimal
    opex_including_sbc: Decimal
    sbc_expense: Decimal
    depreciation_amortization: Decimal
    interest_expense: Decimal
    interest_tax_shield: Decimal
    cash_tax: Decimal
    change_nwc: Decimal
    capex: Decimal
    debt_issued: Decimal
    debt_repaid: Decimal
    share_issuance_amount: Decimal
    shares_issued: Decimal
    repurchases_amount: Decimal
    shares_repurchased: Decimal
    dividends: Decimal

    def __post_init__(self) -> None:
        nonnegative = (
            "revenue",
            "cogs",
            "opex_including_sbc",
            "sbc_expense",
            "depreciation_amortization",
            "interest_expense",
            "interest_tax_shield",
            "cash_tax",
            "capex",
            "debt_issued",
            "debt_repaid",
            "share_issuance_amount",
            "shares_issued",
            "repurchases_amount",
            "shares_repurchased",
            "dividends",
        )
        for name in nonnegative:
            _decimal(getattr(self, name), name, allow_negative=False)
        _decimal(self.change_nwc, "change_nwc")
        _units(self.shares_issued, "shares_issued")
        _units(self.shares_repurchased, "shares_repurchased")
        if self.sbc_expense > self.opex_including_sbc:
            raise FinancialModelError("sbc_expense cannot exceed opex_including_sbc")
        if self.interest_tax_shield > self.interest_expense:
            raise FinancialModelError("interest_tax_shield cannot exceed interest_expense")


@dataclass(frozen=True, slots=True)
class PeriodResult:
    revenue: Decimal
    ebit: Decimal
    net_income: Decimal
    accounting_cfo: Decimal
    economic_fcff: Decimal
    ending_cash: Decimal
    ending_net_ppe: Decimal
    ending_debt: Decimal
    ending_equity: Decimal
    ending_shares: Decimal
    ending_assets: Decimal
    ending_liabilities: Decimal
    balance_reconciles: bool
    funding_negative_cash: bool
    negative_equity: bool
    scope: str = MODEL_SCOPE


def build_one_period(opening: OpeningBalance, drivers: PeriodDrivers) -> PeriodResult:
    """Calculate one integrated period without inventing tax assets or funding."""
    if opening.units != drivers.units:
        raise FinancialModelError("opening balance and drivers use mismatched units")
    with localcontext() as context:
        context.prec = 40
        ebit = (
            drivers.revenue
            - drivers.cogs
            - drivers.opex_including_sbc
            - drivers.depreciation_amortization
        )
        net_income = ebit - drivers.interest_expense - drivers.cash_tax
        # Accounting CFO adds back noncash D&A and SBC. Economic FCFF restores
        # unlevered interest net of its explicit tax shield and subtracts SBC as
        # an economic cost. Share pricing/dilution is outside this model.
        accounting_cfo = (
            net_income
            + drivers.depreciation_amortization
            + drivers.sbc_expense
            - drivers.change_nwc
        )
        economic_fcff = (
            accounting_cfo
            - drivers.capex
            + drivers.interest_expense
            - drivers.interest_tax_shield
            - drivers.sbc_expense
        )
        net_debt_change = drivers.debt_issued - drivers.debt_repaid
        net_share_issuance = drivers.share_issuance_amount - drivers.repurchases_amount
        ending_cash = (
            opening.cash
            + accounting_cfo
            - drivers.capex
            + net_debt_change
            + net_share_issuance
            - drivers.dividends
        )
        ending_ppe = opening.net_ppe + drivers.capex - drivers.depreciation_amortization
        ending_debt = opening.debt + net_debt_change
        ending_equity = (
            opening.equity
            + net_income
            + drivers.sbc_expense
            + net_share_issuance
            - drivers.dividends
        )
        ending_shares = opening.shares + drivers.shares_issued - drivers.shares_repurchased
        ending_assets = (
            opening.assets
            + (ending_cash - opening.cash)
            + (ending_ppe - opening.net_ppe)
            + drivers.change_nwc
        )
        ending_liabilities = opening.liabilities + net_debt_change

    if ending_ppe < ZERO:
        raise FinancialModelError("depreciation exceeds available net_ppe")
    if ending_debt < ZERO:
        raise FinancialModelError("debt repayment exceeds available debt")
    if ending_shares <= ZERO:
        raise FinancialModelError("repurchases reduce shares to zero or below")
    for name, value in (
        ("ebit", ebit),
        ("net_income", net_income),
        ("accounting_cfo", accounting_cfo),
        ("economic_fcff", economic_fcff),
        ("ending_cash", ending_cash),
        ("ending_net_ppe", ending_ppe),
        ("ending_debt", ending_debt),
        ("ending_equity", ending_equity),
    ):
        _decimal(value, name)
    _units(ending_shares, "ending_shares")
    for name, value in (
        ("ending_assets", ending_assets),
        ("ending_liabilities", ending_liabilities),
    ):
        _decimal(value, name, allow_negative=False)
    return PeriodResult(
        revenue=drivers.revenue,
        ebit=ebit,
        net_income=net_income,
        accounting_cfo=accounting_cfo,
        economic_fcff=economic_fcff,
        ending_cash=ending_cash,
        ending_net_ppe=ending_ppe,
        ending_debt=ending_debt,
        ending_equity=ending_equity,
        ending_shares=ending_shares,
        ending_assets=ending_assets,
        ending_liabilities=ending_liabilities,
        balance_reconciles=ending_assets == ending_liabilities + ending_equity,
        funding_negative_cash=ending_cash < ZERO,
        negative_equity=ending_equity < ZERO,
    )


def fabless_revenue(units_sold: Decimal, average_selling_price: Decimal) -> Decimal:
    """Explicit product mechanics: units sold times ASP, with no demand inference."""
    with localcontext() as context:
        context.prec = 40
        result = _units(units_sold, "units_sold") * _decimal(
            average_selling_price, "average_selling_price", allow_negative=False
        )
    return _decimal(result, "fabless revenue", allow_negative=False)


def foundry_revenue(
    capacity_wafers: Decimal, utilization: Decimal, wafer_price: Decimal
) -> Decimal:
    """Explicit foundry mechanics with bounded capacity and utilization."""
    capacity = _units(capacity_wafers, "capacity_wafers")
    utilization = _decimal(utilization, "utilization", allow_negative=False)
    if utilization > Decimal("1"):
        raise FinancialModelError("utilization must be between 0 and 1")
    with localcontext() as context:
        context.prec = 40
        result = capacity * utilization * _decimal(
            wafer_price, "wafer_price", allow_negative=False
        )
    return _decimal(result, "foundry revenue", allow_negative=False)


def recovery_cash_tax(pre_tax_income: Decimal, cash_tax: Decimal) -> Decimal:
    """A loss period receives no invented tax asset; cash tax remains explicit."""
    _decimal(pre_tax_income, "pre_tax_income")
    return _decimal(cash_tax, "cash_tax", allow_negative=False)


def sum_mixed_segments(segments: Mapping[str, Decimal]) -> Decimal:
    """Sum explicitly named segments; valuation weighting is deliberately unsupported."""
    if not segments or any(not isinstance(name, str) or not name.strip() for name in segments):
        raise FinancialModelError("mixed business requires explicitly named segments")
    with localcontext() as context:
        context.prec = 40
        result = sum(
            _decimal(value, f"segment {name}", allow_negative=False)
            for name, value in segments.items()
        )
    return _decimal(result, "mixed segment revenue", allow_negative=False)
