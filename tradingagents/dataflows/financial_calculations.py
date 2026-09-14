"""Small, auditable calculations for prepared financial-statement facts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def parse_decimal(value: Any) -> Decimal | None:
    """Parse a documented provider number without treating missing markers as zero."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"none", "null", "nan", "n/a", "-"}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def plain_number(value: Decimal) -> int | float:
    """Convert a finite Decimal to a JSON-friendly number without rounding it."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def ratio(
    numerator: Decimal,
    denominator: Decimal,
    *,
    denominator_label: str,
) -> tuple[Decimal | None, list[str]]:
    """Divide two reported values and explain zero or unusual negative bases."""
    if denominator == 0:
        return None, [f"{denominator_label} is zero; the ratio is undefined."]
    caveats = []
    if denominator < 0:
        caveats.append(
            f"{denominator_label} is negative; the ratio is arithmetically valid "
            "but may not have its usual economic interpretation."
        )
    return numerator / denominator, caveats


def free_cash_flow(
    operating_cash_flow: Decimal,
    capital_expenditure: Decimal,
    *,
    capital_expenditure_is_outflow_magnitude: bool,
) -> tuple[Decimal, list[str]]:
    """Compute simple FCF using the provider's documented capex sign convention."""
    caveats = []
    if capital_expenditure_is_outflow_magnitude:
        value = operating_cash_flow - capital_expenditure
        if capital_expenditure < 0:
            caveats.append(
                "Capital expenditures are negative although this source normally "
                "reports an outflow magnitude; verify the provider sign convention."
            )
    else:
        value = operating_cash_flow + capital_expenditure
        if capital_expenditure > 0:
            caveats.append(
                "Capital expenditures are positive although this source normally "
                "reports cash outflows as negative; verify the provider sign convention."
            )
    return value, caveats
