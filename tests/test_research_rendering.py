from decimal import Decimal

import pytest

from tradingagents.research.contracts import FinancialFact
from tradingagents.research.rendering import render_fact, render_references


def fact(value, scale="1"):
    return FinancialFact(id="revenue", source_id="filing", metric="revenue", value=Decimal(value),
                         unit="USD", currency="USD", scale=Decimal(scale), period_end="2025-12-31",
                         basis="GAAP", location="synthetic")


def test_billion_is_ten_yi_not_one_yi():
    assert render_fact(fact("1", "1e9"), "Chinese") == "10亿 USD"
    assert render_fact(fact("1000", "1e6"), "English") == "1 billion USD"
    assert render_fact(fact("-125", "1e6"), "Chinese") == "-1.25亿 USD"


def test_zero_and_precision_are_not_silently_changed():
    assert render_fact(fact("0"), "Chinese") == "0 USD"
    assert render_fact(fact("0.123456789"), "Chinese") == "0.123456789 USD"


def test_percentages_follow_declared_units_not_value_magnitude():
    fraction = FinancialFact(id="margin", source_id="filing", metric="margin", value=Decimal("0.74"),
                             unit="fraction", period_end="2025-12-31", basis="GAAP", location="synthetic")
    percentage = fraction.model_copy(update={"id": "tax-rate", "value": Decimal("16.5"), "unit": "percent"})

    assert render_fact(fraction, "English") == "74 %"
    assert render_fact(percentage, "English") == "16.5 %"


def test_references_are_resolved_or_rejected():
    assert render_references("收入 {{fact:revenue}}", (fact("1e9"),), "Chinese") == "收入 10亿 USD"
    with pytest.raises(ValueError, match="unknown"):
        render_references("收入 {{fact:invented}}", (), "Chinese")
