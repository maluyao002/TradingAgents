"""Deterministic financial-value rendering for bilingual reader reports."""

import re
from decimal import Decimal, localcontext

from .contracts import FinancialFact

_FACT = re.compile(r"\{\{fact:([^{}]+)\}\}")


def render_fact(fact: FinancialFact, language: str) -> str:
    with localcontext() as context:
        context.prec = 40
        value = fact.normalized_value
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
        unit = fact.currency or fact.unit
        return f"{number}{suffix} {unit}"


def render_references(text: str, facts: tuple[FinancialFact, ...], language: str) -> str:
    by_id = {fact.id: fact for fact in facts}

    def substitute(match):
        identifier = match[1]
        if identifier not in by_id:
            raise ValueError("reader references an unknown financial fact")
        return render_fact(by_id[identifier], language)

    return _FACT.sub(substitute, text)
