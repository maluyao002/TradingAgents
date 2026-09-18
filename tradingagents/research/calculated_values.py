"""Code-owned valuation references, kept distinct from reported financial facts."""

import re
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field

from .contracts import Contract, Identifier
from .storage import digest


class CalculatedValue(Contract):
    id: Identifier
    value: Decimal
    unit: str
    currency: str | None = None
    classification: str = "illustrative_calculation_not_reported_fact"
    valuation_method: Literal["fcff", "equity_fcfe"]
    share_count_basis: Literal["point_in_time_diluted", "latest_quarter_diluted_proxy"]
    model_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[Identifier, ...]
    display_decimal_places: int = Field(default=2, ge=0, le=6)


_REFERENCE = re.compile(r"\{\{calc:([^{}]+)\}\}")
_TOTAL_AMOUNTS = {
    "explicit_period_present_value", "terminal_value_at_horizon",
    "terminal_value_present_value", "enterprise_value", "net_debt", "equity_value",
    "total_equity_present_value", "terminal_equity_cash_flow",
}
_PER_SHARE = {"value_per_current_diluted_share"}
_RATIOS = {"terminal_value_share_of_enterprise_value", "terminal_value_share_of_total_equity_present_value"}
_FORECAST_AMOUNTS = {
    "revenue", "operating_profit_before_tax", "economic_sbc", "nopat",
    "depreciation_amortization", "capex", "working_capital", "change_in_working_capital",
    "fcff", "equity_cash_flow", "present_value", "net_income_common", "required_capital_retention",
}


def calculation_catalog(proposal, valuation) -> tuple[CalculatedValue, ...]:
    """Allowlisted outputs only; never elevate model-authored numbers to facts."""
    if valuation.get("status") != "illustrative":
        return ()
    result = valuation["result"]
    model = proposal.model or {}
    method = valuation.get("valuation_method", "equity_fcfe" if "cost_of_equity" in model else "fcff")
    share_basis = valuation.get("share_count_basis", "point_in_time_diluted")
    units = result["units"]
    amount_scale = Decimal(units["amount_scale"])
    if not amount_scale.is_finite() or amount_scale <= 0:
        raise ValueError("invalid calculated-value scale")
    values = []
    input_hash = digest(proposal)
    result_hash = digest(valuation)

    def add(path, raw, unit, scale=Decimal(1), currency=None, classification="illustrative_calculation_not_reported_fact"):
        if raw is None:
            return
        with localcontext() as context:
            context.prec = 50
            value = Decimal(raw) * scale
        values.append(CalculatedValue(
            id=f"valuation.{path}", value=value, unit=unit, currency=currency,
            model_input_sha256=input_hash, model_result_sha256=result_hash,
            evidence_ids=proposal.evidence_ids,
            valuation_method=method, share_count_basis=share_basis, classification=classification,
        ))

    for name in sorted(_TOTAL_AMOUNTS & result.keys()):
        add(name, result[name], units["currency"], amount_scale, units["currency"])
    for name in sorted(_PER_SHARE & result.keys()):
        denominator = "proxy diluted share" if valuation.get("share_count_basis") == "latest_quarter_diluted_proxy" else "share"
        add(name, result[name], f"{units['currency']}/{denominator}", currency=units["currency"])
    for name in sorted(_RATIOS & result.keys()):
        add(name, result[name], "%", Decimal(100))
    for index, period in enumerate(result.get("forecasts", ())):
        for name in sorted(_FORECAST_AMOUNTS & period.keys()):
            add(f"forecasts.{index}.{name}", period[name], units["currency"],
                amount_scale, units["currency"])
        for name, unit in (("discount_years", "years"), ("discount_factor", "multiple")):
            if name in period:
                add(f"forecasts.{index}.{name}", period[name], unit)
    for name in ("current_revenue", "current_working_capital", "net_debt", "current_net_income"):
        if name in model:
            add(f"inputs.{name}", model[name], units["currency"], amount_scale,
                units["currency"], "model_input_not_a_new_reported_fact")
    if "current_diluted_shares" in model:
        add("inputs.current_diluted_shares", model["current_diluted_shares"],
            "proxy diluted shares" if share_basis == "latest_quarter_diluted_proxy" else "diluted shares",
            Decimal(units["share_scale"]), classification="model_input_not_a_new_reported_fact")
    for name in ("discount_rate", "cost_of_equity", "terminal_growth"):
        if name in model:
            add(f"inputs.{name}", model[name], "%", Decimal(100),
                classification="model_assumption_or_convention")
    return tuple(values)


def render_calculations(text: str, values: tuple[CalculatedValue, ...], language="English") -> str:
    by_id = {value.id: value for value in values}
    if len(by_id) != len(values):
        raise ValueError("ambiguous calculation identifiers")

    def replace(match):
        if match[1] not in by_id:
            raise ValueError("reader references an unknown calculation")
        item = by_id[match[1]]
        with localcontext() as context:
            context.prec = 80
            value, suffix = item.value, ""
            if item.unit == item.currency:
                choices = ((Decimal("1e8"), "亿"), (Decimal("1e4"), "万")) if language == "Chinese" else (
                    (Decimal("1e9"), " billion"), (Decimal("1e6"), " million"))
                for scale, label in choices:
                    if abs(value) >= scale:
                        value, suffix = value / scale, label
                        break
            number = format(value, f".{item.display_decimal_places}f")
        return f"{number}{suffix} {item.unit}"

    rendered = _REFERENCE.sub(replace, text)
    if "{{calc:" in rendered:
        raise ValueError("malformed calculation reference")
    return rendered
