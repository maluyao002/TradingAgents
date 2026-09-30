"""Historical runs must date the instrument context before agents see it."""

from types import SimpleNamespace

from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_programmatic_historical_state_never_uses_live_identity(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.resolve_instrument_identity",
        lambda ticker: (_ for _ in ()).throw(AssertionError("live identity fetched")),
    )
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {"llm_backend": "api"}
    graph.selected_analysts = ("market",)
    graph.debug = False
    graph.memory_log = SimpleNamespace(get_past_context=lambda *a, **k: "")
    captured = {}

    def initial_state(*args, **kwargs):
        captured.update(kwargs)
        return {"trade_date": args[1]}

    graph.propagator = SimpleNamespace(
        create_initial_state=initial_state,
        get_graph_args=lambda: {},
    )
    graph.checkpoint_input = lambda state: state
    graph.graph = SimpleNamespace(invoke=lambda *a, **k: {})
    graph._log_state = lambda *a: None
    graph.clear_checkpoint_on_success = lambda *a: None

    final_state, signal = graph._run_graph("EXM", "2020-01-02")

    assert "EXM" in captured["instrument_context"]
    assert "historical identity" in captured["instrument_context"].lower()
    assert "Example Company" not in captured["instrument_context"]
    assert final_state["research_quality"]["accepted"] is False
    assert signal == "REVIEW"
