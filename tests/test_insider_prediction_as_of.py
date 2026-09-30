"""Historical insider and prediction tools cannot serve present-day observations."""

from __future__ import annotations

import json
from unittest import mock

import pandas as pd

from tradingagents.dataflows import alpha_vantage_news, polymarket, y_finance


def test_yahoo_historical_insiders_are_withheld_before_fetch():
    with mock.patch.object(y_finance, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(y_finance.yf, "Ticker", side_effect=AssertionError("must not fetch")):
        out = y_finance.get_insider_transactions("AAPL", "2025-06-01")
    assert "unavailable" in out and "Form 4 filing dates" in out


def test_yahoo_live_insiders_keep_same_day_times_but_drop_future_rows():
    frame = pd.DataFrame({
        "Shares": [100, 200],
        "Start Date": pd.to_datetime(["2026-09-29 18:30:00", "2026-09-30 00:00:00"], utc=True),
    })
    with mock.patch.object(y_finance, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(y_finance.yf, "Ticker", return_value=mock.Mock(insider_transactions=frame)):
        out = y_finance.get_insider_transactions("AAPL", "2026-09-29")
    assert "2026-09-29 18:30:00" in out and "2026-09-30" not in out
    assert "Form 4" in out and "Data retrieved on" not in out


def test_yahoo_empty_live_feed_keeps_no_transactions_result():
    with mock.patch.object(y_finance, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(y_finance.yf, "Ticker", return_value=mock.Mock(insider_transactions=pd.DataFrame())):
        out = y_finance.get_insider_transactions("AAPL", "2026-09-29")
    assert "No insider transactions reported" in out


def test_alpha_vantage_historical_insiders_are_withheld_before_fetch():
    with mock.patch.object(alpha_vantage_news, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(alpha_vantage_news, "_make_api_request", side_effect=AssertionError("must not fetch")):
        out = alpha_vantage_news.get_insider_transactions("AAPL", "2025-06-01")
    assert "unavailable" in out and "Form 4 filing dates" in out


def test_alpha_vantage_live_insiders_filter_future_rows_and_state_limit():
    response = json.dumps({"data": [
        {"transaction_date": "2026-09-29", "executive": "A"},
        {"transaction_date": "2026-09-30", "executive": "B"},
    ]})
    with mock.patch.object(alpha_vantage_news, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(alpha_vantage_news, "_make_api_request", return_value=response):
        out = json.loads(alpha_vantage_news.get_insider_transactions("AAPL", "2026-09-29"))
    assert [row["executive"] for row in out["data"]] == ["A"]
    assert "Form 4" in out["point_in_time_notice"]


def test_alpha_vantage_empty_live_feed_keeps_empty_result():
    with mock.patch.object(alpha_vantage_news, "get_current_date", return_value="2026-09-29"), \
         mock.patch.object(alpha_vantage_news, "_make_api_request", return_value='{"data": []}'):
        out = alpha_vantage_news.get_insider_transactions("AAPL", "2026-09-29")
    assert json.loads(out) == {"data": []}


def test_polymarket_withholds_current_odds_before_network_for_historical_date():
    with mock.patch.object(polymarket, "_request", side_effect=AssertionError("must not fetch")):
        out = polymarket.get_prediction_markets("rates", curr_date="2025-06-01")
    assert "withheld" in out and "2025-06-01" in out


def test_polymarket_direct_live_call_keeps_existing_behavior():
    with mock.patch.object(polymarket, "_request", return_value={"events": []}) as request:
        polymarket.get_prediction_markets("rates")
    request.assert_called_once()
