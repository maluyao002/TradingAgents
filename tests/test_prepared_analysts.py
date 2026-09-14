"""Offline integration of preparation, same-call handoffs and report persistence."""

import json
from copy import deepcopy
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.utils.evidence import (
    HANDOFF_END,
    HANDOFF_START,
    build_packet,
    render_analyst_context,
)
from tradingagents.dataflows import interface, preparation
from tradingagents.graph.propagation import Propagator
from tradingagents.reporting import write_report_tree


def _prepared(role):
    return {
        "analysis_date": "2026-09-13",
        "sources": [{
            "id": role + "-source", "label": "Offline source", "content": "Original evidence: basis undisclosed.",
            "vendor": "fixture", "retrieved_at": "2026-09-13", "published_at": None,
        }],
        "facts": [{
            "id": role + "-eps", "metric": "Forward EPS", "value": 15.51, "unit": "USD/share",
            "period": "unknown forecast horizon", "basis": "unknown", "kind": "reported",
            "source_id": role + "-source", "inputs": [], "caveats": ["Not comparable to trailing EPS."],
        }],
        "caveats": ["Filing footnotes unavailable."],
    }


def _report(role):
    return HANDOFF_START + json.dumps({
        "conclusions": ["Growth expectations require comparable inputs."],
        "caveats": ["EPS basis is undisclosed."], "conflicts": [], "evidence_ids": [role + "-eps"],
    }) + HANDOFF_END


def test_fundamentals_prepares_once_and_preserves_evidence_in_same_call(monkeypatch, tmp_path):
    prepared = _prepared("fundamentals")
    prepare = MagicMock(return_value=deepcopy(prepared))
    monkeypatch.setattr("tradingagents.agents.analysts.fundamentals_analyst.prepare_fundamentals", prepare)
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_report("fundamentals"))
    state = Propagator().create_initial_state("TEST", "2026-09-13")
    update = create_fundamentals_analyst(llm)(state)
    assert llm.invoke.call_count == prepare.call_count == 1
    llm.bind_tools.assert_not_called()
    messages = llm.invoke.call_args.args[0]
    assert "Original evidence" not in messages[0].content
    assert any("Original evidence" in str(message.content) for message in messages[1:])
    assert HANDOFF_START not in update["fundamentals_report"]
    assert update["prepared_data"]["fundamentals"] == prepared
    assert update["evidence_packets"]["fundamentals"]["compacted"]

    state.update(update)
    downstream = render_analyst_context(state)
    for required in ("15.51", "unknown forecast horizon", "Not comparable", "Filing footnotes unavailable"):
        assert required in downstream
    assert "Growth expectations require comparable inputs." in downstream
    write_report_tree(state, "TEST", tmp_path)
    saved = json.loads((tmp_path / "evidence.json").read_text())
    assert saved["prepared_data"]["fundamentals"] == prepared
    assert "Growth expectations require comparable inputs." in (tmp_path / "complete_report.md").read_text()


def test_market_can_investigate_history_without_repreparing_snapshot(monkeypatch):
    prepared = _prepared("market")
    prepare = MagicMock(return_value=deepcopy(prepared))
    monkeypatch.setattr("tradingagents.agents.analysts.market_analyst.prepare_market", prepare)
    calls = []
    def respond(prompt):
        calls.append(prompt.to_messages())
        if len(calls) == 1:
            return AIMessage(content="", tool_calls=[{
                "name": "get_stock_data", "args": {"symbol": "TEST"}, "id": "history-1",
            }])
        return AIMessage(content=_report("market"))
    llm = MagicMock()
    llm.bind_tools.return_value = RunnableLambda(respond)
    node = create_market_analyst(llm)
    state = Propagator().create_initial_state("TEST", "2026-09-13")
    first = node(state)
    assert first["market_report"] == ""
    state.update(first)
    source_message = ToolMessage(content="Historical prices for a material question.", tool_call_id="history-1", name="get_stock_data")
    state["messages"] = [*first["messages"], source_message]
    second = node(state)
    assert prepare.call_count == 1
    assert len(calls) == 2
    assert "Evidence source ID: market-tool-" in calls[-1][-1].content
    assert source_message.content == "Historical prices for a material question."
    assert sum(str(m.content).count(source_message.content) for m in calls[-1]) == 1
    assert len(second["prepared_data"]["market"]["sources"]) == 2
    assert second["evidence_packets"]["market"]["compacted"]


def test_unstructured_report_is_preserved_without_extra_generation(monkeypatch):
    monkeypatch.setattr("tradingagents.agents.analysts.fundamentals_analyst.prepare_fundamentals", lambda *a: _prepared("fundamentals"))
    llm = MagicMock()
    original = "Important adverse evidence that must not disappear."
    llm.invoke.return_value = AIMessage(content=original)
    state = Propagator().create_initial_state("TEST", "2026-09-13")
    result = create_fundamentals_analyst(llm)(state)
    assert llm.invoke.call_count == 1
    assert result["fundamentals_report"] == original
    state.update(result)
    assert original in render_analyst_context(state)
    assert not result["evidence_packets"]["fundamentals"]["compacted"]


def test_news_requests_reuse_run_cache_inside_toolnode(monkeypatch):
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from tradingagents.agents.utils.agent_utils import get_news
    from tradingagents.dataflows.interface import VENDOR_METHODS
    from tradingagents.dataflows.request_cache import data_request_scope

    provider = MagicMock(return_value="Same news source response")
    monkeypatch.setitem(VENDOR_METHODS["get_news"], "yfinance", provider)
    monkeypatch.setattr("tradingagents.dataflows.interface.get_vendor", lambda *a: "yfinance")
    # Exact positional call matches the sentiment prefetch; tool call uses keyword args.
    with data_request_scope():
        get_news.func(ticker="TEST", start_date="2026-09-06", end_date="2026-09-13")
        workflow = StateGraph(MessagesState)
        workflow.add_node("tools", ToolNode([get_news]))
        workflow.add_edge(START, "tools")
        workflow.add_edge("tools", END)
        reply = workflow.compile().invoke({"messages": [AIMessage(content="", tool_calls=[{
            "name": "get_news", "args": {"ticker": "TEST", "start_date": "2026-09-06", "end_date": "2026-09-13"}, "id": "news-call",
        }])]})
        assert reply["messages"][-1].content == "Same news source response"
    assert provider.call_count == 1


def test_prepared_financial_calculations_survive_handoff_with_dependencies(monkeypatch):
    monkeypatch.setattr(preparation.date_window, "get_current_date", lambda: "2026-09-13")

    def provider(method, *args):
        if method == "get_fundamentals":
            value = {"EPS": "4", "ForwardPE": "20"}
        elif method == "get_income_statement":
            value = {"annualReports": [{
                "fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD",
                "totalRevenue": "100", "grossProfit": "40",
            }], "quarterlyReports": []}
        else:
            value = {"annualReports": [], "quarterlyReports": []}
        return interface.RoutedVendorResult(json.dumps(value), "alpha_vantage", method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", provider)
    prepared = preparation.prepare_fundamentals("AMD", "2026-09-13")
    margin = next(fact for fact in prepared["facts"] if fact["metric"] == "gross_margin")
    report = HANDOFF_START + json.dumps({
        "conclusions": ["Reported gross margin is 40%; filing footnotes are unavailable."],
        "caveats": [], "conflicts": [], "evidence_ids": [margin["id"]],
    }) + HANDOFF_END
    packet = build_packet("fundamentals", report, prepared)
    assert packet.compacted
    context = render_analyst_context({
        "fundamentals_report": packet.report,
        "prepared_data": {"fundamentals": prepared},
        "evidence_packets": {"fundamentals": packet.to_dict()},
    })
    for required in (margin["id"], *margin["inputs"], "gross_margin", "not_disclosed", "annual:2025-12-31"):
        assert required in context
    assert "forecast methodology" in context
