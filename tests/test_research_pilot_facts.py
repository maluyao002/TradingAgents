from decimal import Decimal

import pytest

from scripts.research_nvda_pilot_facts import extract_facts, numbers


def test_preserve_parentheses_and_columns():
    assert numbers(["$", "24,077", "", "(23,707", ")"]) == [Decimal(24077), Decimal(-23707)]


@pytest.mark.parametrize("value", ["-", "N/A", "1.2 %", "1 billion", "NaN"])
def test_unknown_numeric_cells_fail_closed(value):
    with pytest.raises(ValueError):
        numbers([value])


def test_layout_change_fails_closed():
    with pytest.raises(ValueError, match="layout"):
        extract_facts(b"<html><table><tr><td>1</td></tr></table></html>")
