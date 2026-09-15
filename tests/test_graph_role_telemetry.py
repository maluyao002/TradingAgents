"""Role attribution through real graph callbacks with a shared chat client."""

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from cli.stats_handler import StatsCallbackHandler
from tradingagents.reporting import build_run_metadata


def test_shared_api_style_client_keeps_graph_roles_and_repeated_evidence_separate():
    stats = StatsCallbackHandler()
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    client = FakeMessagesListChatModel(
        responses=[AIMessage(content="bull", usage_metadata=usage),
                   AIMessage(content="bear", usage_metadata=usage)], callbacks=[stats],
    )
    evidence = "<analyst_evidence>Same provider facts</analyst_evidence>"
    graph = StateGraph(MessagesState)
    graph.add_node("Bull Researcher", lambda state: {
        "messages": [client.invoke([HumanMessage("Bull role " + evidence)])],
    })
    graph.add_node("Bear Researcher", lambda state: {
        "messages": [client.invoke([HumanMessage("Bear role plus previous debate " + evidence)])],
    })
    graph.add_edge(START, "Bull Researcher")
    graph.add_edge("Bull Researcher", "Bear Researcher")
    graph.add_edge("Bear Researcher", END)
    graph.compile().invoke({"messages": []})
    observed = stats.get_persistence_stats()
    assert set(observed["per_role"]) == {"bull", "bear"}
    assert observed["per_role"]["bull"]["total_tokens"] == 12
    assert observed["per_role"]["bear"]["total_tokens"] == 12
    assert observed["total_tokens"] == 24
    assert observed["per_role"]["bear"]["repeated_message_characters"] == 0
    assert observed["per_role"]["bear"]["repeated_analyst_evidence_characters"] == len(evidence)
    saved = build_run_metadata({"_run_metadata": {"usage": observed}}, "TEST")
    assert saved["usage"]["per_role"]["bear"]["repeated_analyst_evidence_characters"] == len(evidence)
    assert len(saved["usage"]["calls"]) == 2
