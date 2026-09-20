"""Deterministic financial-value rendering for bilingual reader reports."""

import re
from decimal import Decimal, localcontext

from .contracts import FinancialFact

_FACT = re.compile(r"\{\{fact:([^{}]+)\}\}")


def _display_value_and_unit(value: Decimal, unit: str) -> tuple[Decimal, str]:
    """Render ratios according to their declared unit, never by magnitude.

    A ``fraction`` is a base-unit ratio (``0.74`` means 74%), while ``%`` and
    its spelled-out forms are already percentage points.  Treating every value
    below one as a percentage would silently corrupt small currency amounts and
    basis-point-like reported values.
    """

    if unit.strip().lower() == "fraction":
        return value * Decimal(100), "%"
    if unit.strip().lower() in {"%", "percent", "percentage"}:
        return value, "%"
    return value, unit


def render_fact(fact: FinancialFact, language: str) -> str:
    with localcontext() as context:
        context.prec = 40
        value, declared_unit = _display_value_and_unit(fact.normalized_value, fact.unit)
        magnitude = abs(value)
        choices = ((Decimal("1e8"), "亿"), (Decimal("1e4"), "万")) if language == "Chinese" else (
            (Decimal("1e9"), " billion"), (Decimal("1e6"), " million"),
            (Decimal("1e3"), " thousand"))
        suffix = ""
        for scale, label in choices:
            if magnitude >= scale:
                value /= scale
                suffix = label
                break
        # Preserve the source precision: presentation must not round into a new claim.
        number = format(value, "f")
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        unit = fact.currency or declared_unit
        return f"{number}{suffix} {unit}"


def render_references(text: str, facts: tuple[FinancialFact, ...], language: str) -> str:
    by_id = {fact.id: fact for fact in facts}

    def substitute(match):
        identifier = match[1]
        if identifier not in by_id:
            raise ValueError("reader references an unknown financial fact")
        return render_fact(by_id[identifier], language)

    return _FACT.sub(substitute, text)
