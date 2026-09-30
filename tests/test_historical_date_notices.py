"""Historical context and unavailable notices do not reveal later dates."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest import mock

from tradingagents.agents.utils import agent_utils
from tradingagents.dataflows import alpha_vantage_news, date_window, y_finance


def test_historical_notices_name_no_date_after_analysis(monkeypatch):
    cutoff = "2025-01-07"
    monkeypatch.setattr(date_window, "get_current_date", lambda: "2026-09-29")
    monkeypatch.setattr(agent_utils, "get_current_date", lambda: "2026-09-29")
    monkeypatch.setattr(y_finance, "get_current_date", lambda: "2026-09-29")
    monkeypatch.setattr(alpha_vantage_news, "get_current_date", lambda: "2026-09-29")

    with mock.patch.object(y_finance.yf, "Ticker", side_effect=AssertionError("must not fetch")), \
         mock.patch.object(alpha_vantage_news, "_make_api_request", side_effect=AssertionError("must not fetch")):
        notices = [
            date_window.coverage_gap(
                [datetime(2026, 9, 29, tzinfo=timezone.utc)],
                "2025-01-01", cutoff, "Yahoo Finance news", "news for AAPL",
            ),
            date_window.withhold_live_profile(cutoff, "AAPL"),
            y_finance.get_insider_transactions("AAPL", cutoff),
            alpha_vantage_news.get_insider_transactions("AAPL", cutoff),
            agent_utils.build_instrument_context(
                "AAPL", "stock", {"company_name": "Example"}, curr_date=cutoff,
            ),
        ]

    assert all(notice for notice in notices)
    assert notices[0].startswith("<unavailable:")
    assert notices[2].startswith("<unavailable:")
    assert notices[3].startswith("<unavailable:")
    assert "<unavailable: historical identity as of 2025-01-07" in notices[4]
    assert "Example" not in notices[4]
    assert "Resolved identity" not in notices[4]
    for notice in notices:
        assert all(date <= cutoff for date in re.findall(r"\d{4}-\d{2}-\d{2}", notice)), notice
