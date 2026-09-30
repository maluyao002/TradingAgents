"""Offline checks for data vendor configuration bound to each analysis run."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Barrier

import pytest

from tradingagents.dataflows.config import get_config, run_config
from tradingagents.dataflows.interface import get_vendor
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _vendor() -> str:
    return get_vendor("fundamental_data", "get_balance_sheet")


def _graph(vendor: str, seen: list[str], barrier: Barrier | None = None):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = deepcopy(DEFAULT_CONFIG)
    graph.config["tool_vendors"] = {"get_balance_sheet": vendor}

    def resolve_pending(ticker):
        seen.append(_vendor())  # Settlement and reflection use this context.

    @contextmanager
    def checkpoint_scope(*args):
        seen.append(_vendor())
        yield None

    def run_graph(*args, **kwargs):
        if barrier is not None:
            barrier.wait(timeout=5)
        seen.append(_vendor())  # Data tools in the graph use this context.
        return {}, "REVIEW"

    graph._resolve_pending_entries = resolve_pending
    graph.checkpoint_scope = checkpoint_scope
    graph._run_graph = run_graph
    return graph


def test_run_config_uses_defaults_and_restores_outer_scope():
    outside = get_config()
    with run_config({"data_vendors": {"fundamental_data": "alpha_vantage"}}):
        first = get_config()
        assert first["data_vendors"]["fundamental_data"] == "alpha_vantage"
        assert first["data_vendors"]["news_data"] == DEFAULT_CONFIG["data_vendors"]["news_data"]
        first["data_vendors"]["fundamental_data"] = "changed"
        assert get_config()["data_vendors"]["fundamental_data"] == "alpha_vantage"
        with run_config({"tool_vendors": {"get_balance_sheet": "yfinance"}}):
            assert _vendor() == "yfinance"
        assert get_config()["data_vendors"]["fundamental_data"] == "alpha_vantage"
    assert get_config() == outside


def test_programmatic_run_binds_its_config_before_settlement_and_checkpoint():
    seen = []
    graph = _graph("alpha_vantage", seen)
    outside = get_config()
    assert graph.propagate("AAPL", "2026-09-01") == ({}, "REVIEW")
    assert seen == ["alpha_vantage"] * 3
    assert get_config() == outside


def test_concurrent_programmatic_runs_keep_distinct_vendors():
    barrier = Barrier(2)
    seen_a, seen_b = [], []
    a = _graph("alpha_vantage", seen_a, barrier)
    b = _graph("yfinance", seen_b, barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(graph.propagate, "AAPL", "2026-09-01") for graph in (a, b)]
        assert [future.result(timeout=10) for future in futures] == [({}, "REVIEW")] * 2
    assert seen_a == ["alpha_vantage"] * 3
    assert seen_b == ["yfinance"] * 3


def test_cli_stream_body_receives_selected_config(monkeypatch):
    import cli.main as cli

    config = deepcopy(DEFAULT_CONFIG)
    config["tool_vendors"] = {"get_balance_sheet": "alpha_vantage"}
    monkeypatch.setattr(cli, "_build_run_config", lambda selections, checkpoint: config)
    monkeypatch.setattr(cli, "_run_analysis_scoped", lambda *args: _vendor())
    outside = get_config()
    assert cli.run_analysis(selections={"analysis_date": "2026-09-01"}) == "alpha_vantage"
    assert get_config() == outside


def test_fundamentals_pilot_receives_explicit_vendor_config(monkeypatch):
    from tradingagents.codex import fundamentals

    config = deepcopy(DEFAULT_CONFIG)
    config["tool_vendors"] = {"get_balance_sheet": "alpha_vantage"}
    monkeypatch.setattr(fundamentals, "_run_fundamentals_scoped", lambda *a, **k: _vendor())
    outside = get_config()
    assert fundamentals.run_fundamentals(
        "AAPL", "2026-09-01", backend="api", model="example", effort="low", config=config,
    ) == "alpha_vantage"
    assert get_config() == outside


def test_run_config_restores_after_error():
    outside = get_config()
    with pytest.raises(RuntimeError), run_config(
        {"tool_vendors": {"get_balance_sheet": "alpha_vantage"}}
    ):
        raise RuntimeError("offline failure")
    assert get_config() == outside


def test_langgraph_tool_node_reads_bound_vendor():
    """LangGraph must carry the run context into its tool execution thread."""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    @tool
    def selected_vendor() -> str:
        """Return the configured vendor for the balance sheet tool."""
        return _vendor()

    def request_tool(state):
        return {"messages": [AIMessage("", tool_calls=[
            {"name": "selected_vendor", "args": {}, "id": "vendor-check"},
        ])]}

    workflow = StateGraph(MessagesState)
    workflow.add_node("request", request_tool)
    workflow.add_node("tools", ToolNode([selected_vendor]))
    workflow.add_edge(START, "request")
    workflow.add_edge("request", "tools")
    workflow.add_edge("tools", END)
    with run_config({"tool_vendors": {"get_balance_sheet": "alpha_vantage"}}):
        result = workflow.compile().invoke({"messages": [("user", "check")]})
    assert result["messages"][-1].content == "alpha_vantage"
