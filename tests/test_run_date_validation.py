"""Malformed run dates must fail before settlement, setup, or data fetching."""

from unittest.mock import Mock

import pytest

from tradingagents.dataflows.date_window import require_iso_date
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.parametrize("value", ["2026-1-1", "20260101", "2026-W01-1", "2026-02-30", "", None])
def test_graph_rejects_noncanonical_date_before_any_run_work(value):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {}
    graph._resolve_pending_entries = Mock()
    graph.checkpoint_scope = Mock()
    graph._run_graph = Mock()
    with pytest.raises(ValueError, match="canonical YYYY-MM-DD"):
        graph.propagate("AAPL", value)
    graph._resolve_pending_entries.assert_not_called()
    graph.checkpoint_scope.assert_not_called()
    graph._run_graph.assert_not_called()


@pytest.mark.parametrize("value", ["2026-1-1", "20260101", "2026-W01-1", "2026-02-30", "", None])
def test_cli_rejects_noncanonical_date_before_config_or_stream(monkeypatch, value):
    import cli.main as cli

    build = Mock()
    run = Mock()
    monkeypatch.setattr(cli, "_build_run_config", build)
    monkeypatch.setattr(cli, "_run_analysis_scoped", run)
    with pytest.raises(ValueError, match="canonical YYYY-MM-DD"):
        cli.run_analysis(selections={"analysis_date": value})
    build.assert_not_called()
    run.assert_not_called()


def test_canonical_leap_day_is_accepted():
    assert require_iso_date("2024-02-29") == "2024-02-29"
