"""A recorded rating must describe an unambiguous decision."""

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.agents.utils.rating import extract_explicit_rating, parse_rating
from tradingagents.graph.signal_processing import SignalProcessor


@pytest.mark.parametrize(("decision", "expected"), [
    ("No readable decision.", "REVIEW"),
    ("We reject Buy and consider Underweight.", "REVIEW"),
    ("Rating: Buy\nRating: Sell", "REVIEW"),
    ("Rating: Buy / Sell", "REVIEW"),
    ("Rating: Buy / REVIEW", "REVIEW"),
    ("Rating: Buy or REVIEW", "REVIEW"),
    ("Rating: Buy / TBD", "REVIEW"),
    ("Rating: REVIEW\nThe Buy case is incomplete.", "REVIEW"),
    ("Rating Scale: Buy, Overweight, Hold, Underweight, Sell", "REVIEW"),
    ("Rating Scale: Buy, Overweight, Hold, Underweight, Sell\nRating: Sell", "Sell"),
    ("Rating: HOLD\n**Rating**: Hold", "Hold"),
    ("**Rating:** Hold", "Hold"),
    ("## Rating — **Underweight**", "Underweight"),
    ("1. Rating: Buy", "Buy"),
    ("Rating：Sell", "Sell"),
])
def test_signal_and_new_decision_log_agree(tmp_path, decision, expected):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "decisions.md")})
    log.store_decision("NVDA", "2026-01-10", decision)
    assert log.load_entries()[0]["rating"] == expected
    assert parse_rating(decision) == expected
    assert SignalProcessor().process_signal(decision) == expected
    assert (extract_explicit_rating(decision) or "REVIEW") == expected


def test_existing_decision_log_is_not_reinterpreted(tmp_path):
    path = tmp_path / "decisions.md"
    original = (
        "[2026-01-10 | NVDA | Hold | pending]\n\nDECISION:\nNo decision."
        + TradingMemoryLog._SEPARATOR
    )
    path.write_text(original)
    log = TradingMemoryLog({"memory_log_path": str(path)})
    assert log.load_entries()[0]["rating"] == "Hold"
    assert path.read_text() == original
