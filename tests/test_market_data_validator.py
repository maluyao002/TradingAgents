"""Tests for the deterministic market-data verification snapshot (#830/#881)."""

from __future__ import annotations

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator


def _sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    closes = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


@pytest.mark.unit
class TestVerifiedSnapshot:
    def test_excludes_future_rows(self, monkeypatch):
        data = pd.concat([
            _sample_ohlcv(),
            pd.DataFrame({"Date": [pd.Timestamp("2026-06-01")], "Open": [999.0],
                          "High": [999.0], "Low": [999.0], "Close": [999.0], "Volume": [999]}),
        ], ignore_index=True)
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: data)

        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Verified market data snapshot for COF" in snap
        assert "Requested analysis date: 2026-05-13" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "999.00" not in snap          # future row excluded
        assert "boll_lb" in snap             # indicators present

    def test_uses_previous_trading_day_when_date_is_weekend(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        # 2026-05-16 is a Saturday; latest row should be Fri 2026-05-15
        snap = validator.build_verified_market_snapshot("COF", "2026-05-16")
        assert "Latest trading row used: 2026-05-15" in snap
        assert "Recent verified closes" in snap

    def test_raises_when_no_rows_on_or_before_date(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2020-01-01")

    def test_raises_on_empty_data(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: pd.DataFrame())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2026-05-13")

    def test_look_back_window_capped_at_30(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-20", look_back_days=999)
        # last-N closes table has at most 30 data rows
        close_rows = [ln for ln in snap.splitlines() if ln.startswith("| 2026-")]
        assert 0 < len(close_rows) <= 30


@pytest.mark.unit
class TestTool:
    def test_tool_delegates_to_builder(self, monkeypatch):
        from tradingagents.agents.utils.market_data_validation_tools import (
            get_verified_market_snapshot,
        )
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        out = get_verified_market_snapshot.invoke(
            {"symbol": "COF", "curr_date": "2026-05-20"}
        )
        assert "Verified market data snapshot for COF" in out


def test_snapshot_retains_dated_trend_change_without_another_fetch(monkeypatch):
    dates = pd.bdate_range("2025-01-01", periods=301)
    close = [float(100 + i) for i in range(300)] + [99999.0]
    frame = pd.DataFrame({"Date": dates, "Open": close, "High": close, "Low": close,
                          "Close": close, "Volume": [1000] * 301})
    calls = []

    def load(symbol, date):
        calls.append((symbol, date))
        return frame

    monkeypatch.setattr(validator, "load_ohlcv", load)
    cutoff = dates[299].strftime("%Y-%m-%d")
    snapshot = validator.build_verified_market_snapshot_data("TEST", cutoff)
    comparisons = {item["indicator"]: item for item in snapshot["indicator_comparisons"]}
    # A fully populated moving average of a linear +1/row series rises by 5
    # across five trading rows; the future extreme must not affect that result.
    assert comparisons["close_50_sma"]["change"] == pytest.approx(5)
    assert comparisons["close_200_sma"]["change"] == pytest.approx(5)
    assert comparisons["close_50_sma"]["start_date"] == dates[294].strftime("%Y-%m-%d")
    assert comparisons["close_50_sma"]["end_date"] == cutoff
    assert len(calls) == 1
    report = validator.render_verified_market_snapshot(snapshot)
    assert "stockstats" in report
    assert "configured windows=" in report
    assert "do not establish a monotonic trend" in report
    assert "99999" not in report


def test_short_snapshot_does_not_invent_five_row_history(monkeypatch):
    monkeypatch.setattr(validator, "load_ohlcv", lambda *args: _sample_ohlcv().head(5))
    snapshot = validator.build_verified_market_snapshot_data("TEST", "2026-05-20")
    assert snapshot["indicator_comparisons"] == []


@pytest.mark.parametrize("observed,status", [
    ("2026-05-20T19:18:00+00:00", "provisional"),
    ("2026-05-21T01:00:00+00:00", "provisional"),
    ("2026-05-22T19:18:00+00:00", "historical"),
])
def test_daily_bar_finality_is_conservative(monkeypatch, observed, status):
    from datetime import datetime
    monkeypatch.setattr(validator, "load_ohlcv", lambda *args: _sample_ohlcv())
    monkeypatch.setattr(validator, "_observed_at", lambda: datetime.fromisoformat(observed))
    snapshot = validator.build_verified_market_snapshot_data("TEST", "2026-05-20")
    assert snapshot["bar_status"] == status
    assert snapshot["observed_at"] == observed
    rendered = validator.render_verified_market_snapshot(snapshot)
    if status == "provisional":
        assert "Close (provisional price)" in rendered
        assert "do not say 'closed at'" in rendered
        assert "indicators and changes" in rendered
    else:
        assert "exchange-final status is not independently verified" in rendered


def test_finality_survives_market_preparation(monkeypatch):
    from datetime import datetime, timezone

    from tradingagents.dataflows.preparation import prepare_market
    monkeypatch.setattr(validator, "load_ohlcv", lambda *args: _sample_ohlcv())
    monkeypatch.setattr(validator, "_observed_at", lambda: datetime(2026, 5, 20, 19, tzinfo=timezone.utc))
    prepared = prepare_market("TEST", "2026-05-20")
    assert "PROVISIONAL" in prepared["caveats"][0]
    assert prepared["facts"]
    assert all("PROVISIONAL" in fact["caveats"][-1] for fact in prepared["facts"])
    assert "PROVISIONAL" in prepared["sources"][0]["content"]


def test_identical_market_data_keeps_citation_ids_across_observation_times(monkeypatch):
    from datetime import datetime, timezone

    from tradingagents.dataflows.preparation import prepare_market
    monkeypatch.setattr(validator, "load_ohlcv", lambda *args: _sample_ohlcv())
    monkeypatch.setattr(validator, "_observed_at", lambda: datetime(2026, 5, 20, 19, 0, tzinfo=timezone.utc))
    first = prepare_market("TEST", "2026-05-20")
    monkeypatch.setattr(validator, "_observed_at", lambda: datetime(2026, 5, 20, 19, 1, tzinfo=timezone.utc))
    second = prepare_market("TEST", "2026-05-20")
    assert first["sources"][0]["retrieved_at"] != second["sources"][0]["retrieved_at"]
    assert first["sources"][0]["id"] == second["sources"][0]["id"]
    assert [fact["id"] for fact in first["facts"]] == [fact["id"] for fact in second["facts"]]
