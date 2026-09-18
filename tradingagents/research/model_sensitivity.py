"""Explicit, reproducible rate/growth grids for either supported DCF method.

This is a mechanical stress test, not an economic scenario, probability estimate,
price target or validation of the caller's forecasts. Every point recomputes the
entire valuation; no previously rendered sensitivity table is reused.
"""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Literal

from .equity_valuation import EquityDCFModelInput, equity_dcf_valuation
from .storage import digest
from .valuation import FCFFModelInput, ValuationError, ValuationUnits, dcf_valuation

ShareBasis = Literal["point_in_time_diluted", "latest_quarter_diluted_proxy"]


@dataclass(frozen=True, slots=True)
class RecomputedSensitivityCell:
    discount_rate: Decimal
    terminal_growth: Decimal
    model_sha256: str
    result_sha256: str
    equity_present_value: Decimal
    value_per_current_diluted_share: Decimal


@dataclass(frozen=True, slots=True)
class RecomputedSensitivity:
    valuation_method: Literal["fcff", "equity_fcfe"]
    base_model_sha256: str
    base_result_sha256: str
    share_count_basis: ShareBasis
    units: ValuationUnits
    cells: tuple[RecomputedSensitivityCell, ...]
    scope: str = (
        "Mechanical discount-rate/terminal-growth sensitivity; operating forecasts "
        "held constant. Not economic scenarios, probabilities, a horizon price "
        "target, or certification of assumptions, capital adequacy or share basis."
    )


def _axis(values: tuple[Decimal, ...], name: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValuationError(f"{name} must be a nonempty tuple")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValuationError(f"{name} must contain finite Decimal values")
    if len(set(values)) != len(values):
        raise ValuationError(f"{name} must not contain duplicate values")


def recompute_sensitivity(
    model: FCFFModelInput | EquityDCFModelInput,
    *,
    discount_rates: tuple[Decimal, ...],
    terminal_growth_rates: tuple[Decimal, ...],
    share_count_basis: ShareBasis,
) -> RecomputedSensitivity:
    """Recompute all cells or fail the entire invalid grid, never substitute zero.

    Rates are explicit caller assumptions, not inferred confidence intervals.
    The same current-share denominator is retained in every cell. Amount outputs
    retain ``units.amount_scale``; per-share values are already currency/share.
    Hashes bind the actual typed model and result, including all forecast periods.
    """
    if share_count_basis not in {"point_in_time_diluted", "latest_quarter_diluted_proxy"}:
        raise ValuationError("unknown share-count basis")
    _axis(discount_rates, "discount_rates")
    _axis(terminal_growth_rates, "terminal_growth_rates")
    if len(discount_rates) * len(terminal_growth_rates) > 121:
        raise ValuationError("sensitivity grid exceeds 121 cells")
    if isinstance(model, FCFFModelInput):
        method, rate_field, equity_field = "fcff", "discount_rate", "equity_value"
        calculate = dcf_valuation
    elif isinstance(model, EquityDCFModelInput):
        method, rate_field, equity_field = "equity_fcfe", "cost_of_equity", "total_equity_present_value"
        calculate = equity_dcf_valuation
    else:
        raise ValuationError("unsupported sensitivity model")
    base_result = calculate(model)
    cells = []
    for rate in discount_rates:
        for growth in terminal_growth_rates:
            point_model = replace(model, **{rate_field: rate, "terminal_growth": growth})
            result = calculate(point_model)
            cells.append(RecomputedSensitivityCell(
                discount_rate=rate, terminal_growth=growth,
                model_sha256=digest(asdict(point_model)), result_sha256=digest(asdict(result)),
                equity_present_value=getattr(result, equity_field),
                value_per_current_diluted_share=result.value_per_current_diluted_share,
            ))
    return RecomputedSensitivity(
        valuation_method=method, base_model_sha256=digest(asdict(model)),
        base_result_sha256=digest(asdict(base_result)), share_count_basis=share_count_basis,
        units=model.units, cells=tuple(cells),
    )
