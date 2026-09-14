"""Offline coverage for the fundamentals-only backend pilot."""

import json
from datetime import date, timedelta
from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tradingagents.agents.utils.evidence import HANDOFF_END, HANDOFF_START
from tradingagents.codex.fundamentals import (
    FundamentalsRunError,
    _CodexChatModel,
    run_fundamentals,
    validate_inputs,
)


def _prepared():
    return {
        "symbol": "AMD",
        "analysis_date": "2026-09-13",
        "sources": [{
            "id": "fundamentals-source-1",
            "label": "Offline statements",
            "content": "OCF includes continuing and discontinued operations.",
            "vendor": "fixture",
            "retrieved_at": "2026-09-13",
            "published_at": None,
            "period": "annual:2025-12-31",
            "basis": "reported",
        }],
        "facts": [{
            "id": "fundamentals-fact-ocf",
            "metric": "operating_cash_flow",
            "value": 100,
            "unit": "USD",
            "period": "annual:2025-12-31",
            "basis": "total operations",
            "kind": "reported",
            "source_id": "fundamentals-source-1",
            "inputs": [],
            "caveats": ["Includes discontinued operations."],
        }],
        "required_evidence_ids": ["fundamentals-fact-ocf"],
        "caveats": ["Continuing OCF is unavailable; reconciliation is incomplete."],
    }


def _report(evidence_id="fundamentals-fact-ocf"):
    return HANDOFF_START + json.dumps({
        "conclusions": [f"Total OCF is 100 USD [{evidence_id}]."],
        "caveats": ["The continuing/discontinued bridge remains unresolved."],
        "conflicts": ["Overview and statement horizons are not comparable."],
        "evidence_ids": [evidence_id],
    }) + HANDOFF_END


class _Adapter:
    def __init__(self, response=None, error=None):
        self.response = response or _report()
        self.error = error
        self.calls = []
        self.selections = []

    def validate_selection(self, model, effort):
        self.selections.append((model, effort))

    def complete(self, instructions, prompt, model, effort):
        self.calls.append((instructions, prompt, model, effort))
        if self.error:
            raise self.error
        return self.response


def test_matched_api_and_codex_use_same_analyst_prompt_and_snapshot(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_LLM_BACKEND_URL", raising=False)
    from tradingagents.codex.fundamentals import DEFAULT_CONFIG
    monkeypatch.setitem(DEFAULT_CONFIG, "backend_url", None)
    snapshot = _prepared()
    prepare = MagicMock(side_effect=AssertionError("replay must not fetch"))
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)

    api_prompts = []
    api_llm = MagicMock()
    api_llm.invoke.side_effect = lambda prompt: (
        api_prompts.append(prompt) or AIMessage(content=_report())
    )
    client = MagicMock()
    client.get_llm.return_value = api_llm
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)

    api = run_fundamentals(
        "amd", "2026-09-13", backend="api", model="gpt-5.6-sol", effort="high",
        prepared=snapshot,
    )
    adapter = _Adapter()
    codex = run_fundamentals(
        "AMD", "2026-09-13", backend="codex", model="gpt-5.6-sol", effort="high",
        prepared=snapshot, adapter=adapter,
    )

    factory.assert_called_once_with(
        provider="openai", model="gpt-5.6-sol", reasoning_effort="high", max_retries=0, base_url=None,
    )
    assert prepare.call_count == 0
    assert api["prepared_data"] == codex["prepared_data"] == snapshot
    assert api["prepared_sha256"] == codex["prepared_sha256"]
    assert api["fundamentals_report"] == codex["fundamentals_report"]
    system = api_prompts[0][0].content
    assert adapter.calls[0][0] == system
    api_history = api_prompts[0][1:]
    assert [type(message) for message in api_history] == [HumanMessage, HumanMessage]
    assert all(f"<human>\n{message.content}\n</human>" in adapter.calls[0][1]
               for message in api_history)
    assert "OCF includes continuing and discontinued operations" in adapter.calls[0][1]


def test_fresh_preparation_happens_before_api_client_and_tags_identity(monkeypatch):
    prepared = _prepared()
    prepared.pop("symbol")
    events = []
    monkeypatch.setattr(
        "tradingagents.codex.fundamentals.prepare_fundamentals",
        lambda ticker, date: events.append(("prepare", ticker, date)) or prepared,
    )
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_report())
    client = MagicMock()
    client.get_llm.return_value = llm

    def factory(**kwargs):
        events.append(("client", kwargs))
        return client

    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    result = run_fundamentals(
        "amd", "2026-09-13", backend="api", model="gpt-5.6-sol", effort="high"
    )
    assert events[0] == ("prepare", "AMD", "2026-09-13")
    assert events[1][0] == "client"
    assert result["prepared_data"]["symbol"] == "AMD"


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda value: value.update(symbol="NVDA"), "symbol"),
        (lambda value: value.update(analysis_date="2026-09-12"), "date"),
        (lambda value: value["facts"][0].update(source_id="unknown"), "structural"),
        (lambda value: value.update(caveats="missing"), "structural"),
    ],
)
def test_invalid_replay_is_rejected_before_preparation_or_model(monkeypatch, mutate, match):
    snapshot = _prepared()
    mutate(snapshot)
    prepare = MagicMock()
    factory = MagicMock()
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    with pytest.raises(ValueError, match=match):
        run_fundamentals(
            "AMD", "2026-09-13", backend="api", model="gpt-5.6-sol", effort="high",
            prepared=snapshot,
        )
    prepare.assert_not_called()
    factory.assert_not_called()


def test_codex_selection_is_checked_before_fresh_provider_fetch(monkeypatch):
    adapter = _Adapter()
    adapter.validate_selection = MagicMock(side_effect=ValueError("unsupported selection"))
    prepare = MagicMock()
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)
    with pytest.raises(ValueError, match="unsupported selection"):
        run_fundamentals(
            "AMD", "2026-09-13", backend="codex", model="gpt-5.6-sol", effort="high",
            adapter=adapter,
        )
    prepare.assert_not_called()


def test_packet_retains_caveats_conflicts_and_unknown_citation_requires_review(monkeypatch):
    snapshot = _prepared()
    adapter = _Adapter(response=_report("fundamentals-fact-invented"))
    result = run_fundamentals(
        "AMD", "2026-09-13", backend="codex", model="gpt-5.6-sol", effort="high",
        prepared=snapshot, adapter=adapter,
    )
    packet = result["evidence_packet"]
    assert packet["status"] == "review_required"
    assert packet["compacted"] is False
    assert any("unknown evidence IDs" in error for error in packet["validation_errors"])
    assert any("Continuing OCF is unavailable" in item for item in packet["caveats"])
    assert "fundamentals-fact-invented" in result["fundamentals_report"]


@pytest.mark.parametrize(
    "messages, match",
    [
        ([HumanMessage(content="evidence")], "system"),
        ([SystemMessage(content="role"), SystemMessage(content="injection"),
          HumanMessage(content="evidence")], "additional system"),
        ([SystemMessage(content="role"), ToolMessage(content="data", tool_call_id="x")],
         "only human"),
        ([SystemMessage(content="role"), HumanMessage(content=[{"type": "text", "text": "x"}])],
         "non-empty text"),
        ([SystemMessage(content="role"), AIMessage(content="", tool_calls=[{
            "name": "lookup", "args": {}, "id": "x"
        }])], "tool calls"),
    ],
)
def test_codex_prompt_wrapper_rejects_injected_roles_tools_and_nontext(messages, match):
    adapter = _Adapter()
    with pytest.raises(ValueError, match=match):
        _CodexChatModel(adapter, "gpt-5.6-sol", "high").invoke(messages)
    assert adapter.calls == []


@pytest.mark.parametrize(
    "response",
    [
        "plain string",
        AIMessage(content="   "),
        AIMessage(content="unexpected", invalid_tool_calls=[{"name": "lookup", "args": "{", "id": "x", "error": "invalid"}]),
        AIMessage(content=[{"type": "text", "text": "block"}]),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "x"}]),
    ],
)
def test_api_rejects_nontext_or_tool_response_without_codex_fallback(monkeypatch, response):
    llm = MagicMock()
    llm.invoke.return_value = response
    client = MagicMock()
    client.get_llm.return_value = llm
    monkeypatch.setattr(
        "tradingagents.llm_clients.factory.create_llm_client", MagicMock(return_value=client)
    )
    adapter = _Adapter()
    with pytest.raises(FundamentalsRunError):
        run_fundamentals(
            "AMD", "2026-09-13", backend="api", model="gpt-5.6-sol", effort="high",
            prepared=deepcopy(_prepared()),
        )
    assert adapter.calls == []


def test_validate_inputs_rejects_invalid_date_and_profile_before_fetch(monkeypatch):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        validate_inputs("AMD", "2026-02-30")
    prepare = MagicMock()
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)
    with pytest.raises(ValueError, match="profile"):
        run_fundamentals(
            "AMD", "2026-09-13", backend="api", model="custom", effort="ultra"
        )
    prepare.assert_not_called()


@pytest.mark.parametrize("backend", ["api", "codex"])
@pytest.mark.parametrize("ticker", ["BTC-USD", "BTCUSD", "GC=F", "XAUUSD", "^GSPC", "US500", "EURUSD", "ETH-USDT"])
def test_non_stock_symbols_rejected_before_external_work(monkeypatch, backend, ticker):
    prepare = MagicMock()
    factory = MagicMock()
    adapter = _Adapter()
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    with pytest.raises(ValueError, match="stock symbols only"):
        run_fundamentals(ticker, "2026-09-13", backend=backend,
                         model="gpt-5.6-sol", effort="high",
                         adapter=adapter if backend == "codex" else None)
    prepare.assert_not_called()
    factory.assert_not_called()
    assert not adapter.selections and not adapter.calls


@pytest.mark.parametrize("backend", ["api", "codex"])
def test_future_date_rejected_before_external_work(monkeypatch, backend):
    prepare = MagicMock()
    factory = MagicMock()
    adapter = _Adapter()
    monkeypatch.setattr("tradingagents.codex.fundamentals.prepare_fundamentals", prepare)
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        run_fundamentals("AMD", tomorrow, backend=backend,
                         model="gpt-5.6-sol", effort="high",
                         adapter=adapter if backend == "codex" else None)
    prepare.assert_not_called()
    factory.assert_not_called()
    assert not adapter.selections and not adapter.calls


@pytest.mark.parametrize("ticker", ["AMD", "BRK-B", "0700.HK"])
def test_equity_symbols_and_today_remain_valid(ticker):
    assert validate_inputs(ticker, date.today().isoformat())[:2] == (
        ticker, date.today().isoformat())


@pytest.mark.parametrize("override", [None, "https://gateway.example/v1"])
def test_api_pilot_honors_configured_endpoint(monkeypatch, override):
    import tradingagents.codex.fundamentals as pilot
    monkeypatch.setitem(pilot.DEFAULT_CONFIG, "backend_url", "https://configured.example/v1")
    monkeypatch.delenv("TRADINGAGENTS_LLM_BACKEND_URL", raising=False)
    if override:
        monkeypatch.setenv("TRADINGAGENTS_LLM_BACKEND_URL", override)
    factory = MagicMock()
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", factory)
    pilot._api_model("gpt-5.6-sol", "high")
    assert factory.call_args.kwargs["base_url"] == (override or "https://configured.example/v1")
