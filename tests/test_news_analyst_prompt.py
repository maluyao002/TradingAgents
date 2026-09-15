"""Guard the news analyst prompt against tool-signature drift (#1116).

The prompt used to advertise ``get_news(query, ...)`` while the tool takes a
``ticker``, tricking the LLM into hallucinating free-text query calls.
"""
import inspect

import pytest

import tradingagents.agents.analysts.news_analyst as na
from tradingagents.agents.utils.news_data_tools import get_news


@pytest.mark.unit
def test_get_news_takes_ticker_not_query():
    arg_names = set(get_news.args.keys())
    assert "ticker" in arg_names
    assert "query" not in arg_names


@pytest.mark.unit
def test_news_prompt_matches_get_news_signature():
    src = inspect.getsource(na)
    assert "get_news(ticker, start_date, end_date)" in src
    assert "get_news(query" not in src


def test_required_company_news_precedes_inference_without_tool_calls(monkeypatch):
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from tradingagents.agents.utils.evidence import HANDOFF_END, HANDOFF_START

    prepared = {"sources": [], "facts": [], "caveats": []}
    monkeypatch.setattr(na, "prepare_macro", lambda *a: prepared)
    fetch = MagicMock(return_value="Company announcement {untrusted content}")
    monkeypatch.setattr(na.get_news, "func", fetch)
    calls = []

    def respond(prompt):
        fetch.assert_called_once_with("TEST", "2026-09-07", "2026-09-14")
        messages = prompt.to_messages()
        assert "Company announcement" not in messages[0].content
        assert "Company announcement" in messages[2].content
        calls.append(messages)
        return AIMessage(content=HANDOFF_START + '{"conclusions":["A company announcement is supplied [news-company-baseline]."],"caveats":[],"conflicts":[],"evidence_ids":["news-company-baseline"]}' + HANDOFF_END)

    llm = MagicMock()
    llm.bind_tools.return_value = RunnableLambda(respond)
    result = na.create_news_analyst(llm)({
        "company_of_interest": "TEST", "trade_date": "2026-09-14", "messages": [],
    })
    assert len(calls) == 1
    assert result["evidence_packets"]["news"]["compacted"]
    assert prepared["sources"] == []  # preparation never mutates prior state


@pytest.mark.parametrize("ticker,date", [("OTHER", "2026-09-14"), ("TEST", "2026-09-15")])
def test_required_news_reuses_only_matching_ticker_and_window(monkeypatch, ticker, date):
    from unittest.mock import MagicMock

    fetch = MagicMock(return_value="Company news")
    monkeypatch.setattr(na.get_news, "func", fetch)
    prepared = na.prepare_required_news({"sources": [], "facts": [], "caveats": []}, "TEST", "2026-09-14")
    assert na.prepare_required_news(prepared, "TEST", "2026-09-14") == prepared
    assert fetch.call_count == 1
    updated = na.prepare_required_news(prepared, ticker, date)
    assert fetch.call_count == 2
    assert len(updated["sources"]) == 1
    assert updated["company_news_request"]["ticker"] == ticker
    assert updated["company_news_request"]["end_date"] == date


def test_required_news_empty_response_and_failure_are_not_silent_success(monkeypatch):
    monkeypatch.setattr(na.get_news, "func", lambda *a: "")
    prepared = na.prepare_required_news({"sources": []}, "TEST", "2026-09-14")
    assert "unavailable" in prepared["sources"][0]["content"]

    def fail(*args):
        raise RuntimeError("offline provider failure")
    monkeypatch.setattr(na.get_news, "func", fail)
    with pytest.raises(RuntimeError, match="offline provider failure"):
        na.prepare_required_news({"sources": []}, "TEST", "2026-09-14")


def test_required_news_preserves_structured_provider_payload(monkeypatch):
    import json

    payload = {"feed": [{"title": "Company announcement", "url": "https://example.com/news"}]}
    monkeypatch.setattr(na.get_news, "func", lambda *a: payload)
    prepared = na.prepare_required_news({"sources": []}, "TEST", "2026-09-14")
    assert json.loads(prepared["sources"][0]["content"]) == payload


def test_required_news_shares_sentiment_provider_request_cache(monkeypatch):
    from unittest.mock import MagicMock

    from tradingagents.dataflows.interface import VENDOR_METHODS
    from tradingagents.dataflows.request_cache import data_request_scope

    provider = MagicMock(return_value="Shared company snapshot")
    monkeypatch.setitem(VENDOR_METHODS["get_news"], "yfinance", provider)
    monkeypatch.setattr("tradingagents.dataflows.interface.get_vendor", lambda *a: "yfinance")
    with data_request_scope():
        sentiment_content = na.get_news.func("TEST", "2026-09-07", "2026-09-14")
        prepared = na.prepare_required_news({"sources": []}, "TEST", "2026-09-14")
    assert provider.call_count == 1
    assert prepared["sources"][0]["content"] == sentiment_content


@pytest.mark.parametrize("backend", ["api", "codex-app-server"])
def test_global_prefetch_is_codex_only_and_full_content_reaches_first_call(monkeypatch, backend):
    import json
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from tradingagents.agents.utils.evidence import HANDOFF_END, HANDOFF_START

    company_text = "Complete company news {external text}" * 50
    global_text = "Complete global news {external text}" * 50
    monkeypatch.setattr(na, "prepare_macro", lambda *a: {"sources": [], "facts": [], "caveats": []})
    company = MagicMock(return_value=company_text)
    global_news = MagicMock(return_value=global_text)
    monkeypatch.setattr(na.get_news, "func", company)
    monkeypatch.setattr(na.get_global_news, "func", global_news)
    calls = []

    def respond(prompt):
        messages = prompt.to_messages()
        calls.append(messages)
        assert company.call_count == 1
        assert company_text not in messages[0].content
        assert messages[2].content.count(company_text) == 1
        assessed = "Company evidence [news-company-baseline]."
        if backend == "codex-app-server":
            global_news.assert_called_once_with("2026-09-14", 7, 10)
            assert global_text not in messages[0].content
            assert messages[2].content.count(global_text) == 1
            assert "targeted follow-up queries remain optional" in messages[0].content
            assessed += " Global evidence [news-global-baseline]."
        else:
            global_news.assert_not_called()
            assert "news-global-baseline" not in messages[0].content
            assert global_text not in messages[2].content
        return AIMessage(content=HANDOFF_START + json.dumps({
            "conclusions": [assessed], "caveats": [], "conflicts": [],
            "evidence_ids": ["news-company-baseline"],
        }) + HANDOFF_END)

    llm = MagicMock()
    llm._llm_type = backend
    llm.bind_tools.return_value = RunnableLambda(respond)
    node = na.create_news_analyst(llm)
    state = {"company_of_interest": "TEST", "trade_date": "2026-09-14", "messages": []}
    first = node(state)
    assert len(calls) == 1
    assert first["evidence_packets"]["news"]["compacted"]
    # A later tool round reuses prepared sources instead of fetching them again.
    state["prepared_data"] = first["prepared_data"]
    node(state)
    assert len(calls) == 2
    bound_names = {tool.name for tool in llm.bind_tools.call_args.args[0]}
    assert {"get_news", "get_global_news"} <= bound_names


def test_global_prefetch_honors_configured_limits_and_invalidates_changed_request(monkeypatch):
    import json
    from unittest.mock import MagicMock

    from tradingagents.dataflows.config import set_config

    payload = {"feed": [{"title": "Global announcement", "summary": "Full supplied summary"}]}
    fetch = MagicMock(return_value=payload)
    monkeypatch.setattr(na.get_global_news, "func", fetch)
    set_config({"global_news_lookback_days": 14, "global_news_article_limit": 25})
    prepared = na.prepare_required_global_news({"sources": []}, "2026-09-14")
    fetch.assert_called_once_with("2026-09-14", 14, 25)
    assert prepared["sources"][0]["period"] == "2026-08-31..2026-09-14"
    assert json.loads(prepared["sources"][0]["content"]) == payload
    na.prepare_required_global_news(prepared, "2026-09-14")
    assert fetch.call_count == 1
    set_config({"global_news_article_limit": 30})
    updated = na.prepare_required_global_news(prepared, "2026-09-14")
    assert fetch.call_count == 2
    assert len(updated["sources"]) == 1
    na.prepare_required_global_news(updated, "2026-09-15")
    assert fetch.call_count == 3


def test_global_prefetch_preserves_empty_and_failure_semantics(monkeypatch):
    monkeypatch.setattr(na.get_global_news, "func", lambda *a: "")
    prepared = na.prepare_required_global_news({"sources": []}, "2026-09-14")
    assert "Global news unavailable" in prepared["sources"][0]["content"]

    def fail(*args):
        raise RuntimeError("offline global-news failure")
    monkeypatch.setattr(na.get_global_news, "func", fail)
    with pytest.raises(RuntimeError, match="offline global-news failure"):
        na.prepare_required_global_news({"sources": []}, "2026-09-14")
