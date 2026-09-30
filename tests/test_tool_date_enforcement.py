"""Graph state is the upper date bound for every analyst data tool."""

from __future__ import annotations

from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils import (
    core_stock_tools,
    fundamental_data_tools,
    macro_data_tools,
    market_data_validation_tools,
    news_data_tools,
    prediction_markets_tools,
    technical_indicators_tools,
)
from tradingagents.dataflows.date_window import as_of, as_of_window

TRADE_DATE = "2026-08-14"


class _State(MessagesState):
    trade_date: str


def _run(tool, args, module, target="route_to_vendor"):
    graph = StateGraph(_State)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    with mock.patch.object(module, target, return_value="ok") as called:
        state = graph.compile().invoke({
            "messages": [AIMessage("", tool_calls=[{"name": tool.name, "args": args, "id": "date-1"}])],
            "trade_date": TRADE_DATE,
        })
    assert state["messages"][-1].content == "ok"
    return called.call_args.args


@pytest.mark.parametrize("requested, expected", [
    ("2026-09-14", TRADE_DATE),
    ("2026-08-01", "2026-08-01"),
    (None, TRADE_DATE),
    ("not-a-date", TRADE_DATE),
])
def test_as_of_never_advances_past_the_run(requested, expected):
    assert as_of(requested, TRADE_DATE) == expected


def test_direct_date_call_preserves_legacy_behavior():
    assert as_of("2026-09-14", "") == "2026-09-14"


@pytest.mark.parametrize("start, end, expected", [
    ("2026-08-01", "2026-09-14", ("2026-08-01", TRADE_DATE)),
    ("2026-08-01", "2026-08-10", ("2026-08-01", "2026-08-10")),
    ("2026-09-01", "2026-09-08", ("2026-08-07", TRADE_DATE)),
])
def test_window_clamps_or_moves_wholly_future_ranges(start, end, expected):
    assert as_of_window(start, end, TRADE_DATE) == expected


def test_reversed_window_is_rejected_only_inside_a_graph_run():
    with pytest.raises(ValueError, match="starts after it ends"):
        as_of_window("2026-09-01", "2026-08-01", TRADE_DATE)
    assert as_of_window("2026-09-01", "2026-08-01", "") == ("2026-09-01", "2026-08-01")


@pytest.mark.parametrize("tool", [
    core_stock_tools.get_stock_data,
    fundamental_data_tools.get_fundamentals,
    fundamental_data_tools.get_balance_sheet,
    fundamental_data_tools.get_cashflow,
    fundamental_data_tools.get_income_statement,
    macro_data_tools.get_macro_indicators,
    market_data_validation_tools.get_verified_market_snapshot,
    news_data_tools.get_news,
    news_data_tools.get_global_news,
    news_data_tools.get_insider_transactions,
    prediction_markets_tools.get_prediction_markets,
    technical_indicators_tools.get_indicators,
], ids=lambda tool: tool.name)
def test_trade_date_is_injected_and_hidden_from_model(tool):
    assert "trade_date" in tool.args_schema.model_json_schema()["properties"]
    assert "trade_date" not in tool.tool_call_schema.model_json_schema()["properties"]


@pytest.mark.parametrize("tool, module, args, expected", [
    (fundamental_data_tools.get_fundamentals, fundamental_data_tools,
     {"ticker": "AAPL", "curr_date": "2026-09-14"}, ("get_fundamentals", "AAPL", TRADE_DATE)),
    (fundamental_data_tools.get_balance_sheet, fundamental_data_tools,
     {"ticker": "AAPL"}, ("get_balance_sheet", "AAPL", "quarterly", TRADE_DATE)),
    (fundamental_data_tools.get_cashflow, fundamental_data_tools,
     {"ticker": "AAPL", "curr_date": "2026-09-14"}, ("get_cashflow", "AAPL", "quarterly", TRADE_DATE)),
    (fundamental_data_tools.get_income_statement, fundamental_data_tools,
     {"ticker": "AAPL"}, ("get_income_statement", "AAPL", "quarterly", TRADE_DATE)),
    (macro_data_tools.get_macro_indicators, macro_data_tools,
     {"indicator": "cpi", "curr_date": "2026-09-14"}, ("get_macro_indicators", "cpi", TRADE_DATE, None)),
    (news_data_tools.get_global_news, news_data_tools,
     {"curr_date": "2026-09-14"}, ("get_global_news", TRADE_DATE, None, None)),
    (news_data_tools.get_insider_transactions, news_data_tools,
     {"ticker": "AAPL"}, ("get_insider_transactions", "AAPL", TRADE_DATE)),
    (prediction_markets_tools.get_prediction_markets, prediction_markets_tools,
     {"topic": "rates"}, ("get_prediction_markets", "rates", None, TRADE_DATE)),
])
def test_dated_tool_routes_the_bounded_date(tool, module, args, expected):
    assert _run(tool, args, module) == expected


@pytest.mark.parametrize("tool, module, args, expected", [
    (core_stock_tools.get_stock_data, core_stock_tools,
     {"symbol": "AAPL", "start_date": "2026-09-01", "end_date": "2026-09-08"},
     ("get_stock_data", "AAPL", "2026-08-07", TRADE_DATE)),
    (news_data_tools.get_news, news_data_tools,
     {"ticker": "AAPL", "start_date": "2026-08-01", "end_date": "2026-09-14"},
     ("get_news", "AAPL", "2026-08-01", TRADE_DATE)),
])
def test_window_tool_never_routes_a_future_window(tool, module, args, expected):
    assert _run(tool, args, module) == expected


def test_snapshot_receives_the_bounded_date():
    assert _run(
        market_data_validation_tools.get_verified_market_snapshot,
        {"symbol": "AAPL", "curr_date": "2026-09-14"},
        market_data_validation_tools,
        target="build_verified_market_snapshot",
    ) == ("AAPL", TRADE_DATE, 30)


def test_toolnode_overrides_a_spoofed_injected_date():
    args = _run(
        fundamental_data_tools.get_balance_sheet,
        {"ticker": "AAPL", "trade_date": "2026-09-29"},
        fundamental_data_tools,
    )
    assert args == ("get_balance_sheet", "AAPL", "quarterly", TRADE_DATE)


def test_reversed_window_never_reaches_a_vendor():
    tool = core_stock_tools.get_stock_data
    graph = StateGraph(_State)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    with (
        mock.patch.object(core_stock_tools, "route_to_vendor") as routed,
        pytest.raises(ValueError, match="starts after it ends"),
    ):
        graph.compile().invoke({
            "messages": [AIMessage("", tool_calls=[{
                "name": tool.name,
                "args": {"symbol": "AAPL", "start_date": "2026-09-01", "end_date": "2026-08-01"},
                "id": "reversed-1",
            }])],
            "trade_date": TRADE_DATE,
        })
    routed.assert_not_called()


def test_indicator_multi_request_keeps_the_bound_for_each_vendor_call():
    with mock.patch.object(technical_indicators_tools, "route_to_vendor", return_value="ok") as routed:
        result = technical_indicators_tools.get_indicators.invoke({
            "symbol": "AAPL", "indicator": "rsi,macd", "curr_date": "2026-09-14",
            "trade_date": TRADE_DATE,
        })
    assert result == "ok\n\nok"
    assert [call.args for call in routed.call_args_list] == [
        ("get_indicators", "AAPL", "rsi", TRADE_DATE, 30),
        ("get_indicators", "AAPL", "macd", TRADE_DATE, 30),
    ]
