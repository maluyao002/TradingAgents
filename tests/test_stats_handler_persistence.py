"""Usage persistence keeps provider omissions distinct from observed zeroes."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from cli.stats_handler import StatsCallbackHandler


@pytest.mark.unit
@pytest.mark.parametrize("response", [
    SimpleNamespace(generations=[]),
    SimpleNamespace(generations=[[]]),
    SimpleNamespace(generations=None),
    SimpleNamespace(generations={}),
    SimpleNamespace(),
])
def test_empty_or_malformed_response_finishes_call_without_usage(response):
    handler = StatsCallbackHandler()
    handler.on_chat_model_start({"kwargs": {"model": "gpt-test"}}, [[]], run_id="empty")
    handler.on_llm_end(response, run_id="empty")

    stats = handler.get_persistence_stats()
    assert stats["usage_completeness"] == {
        "calls_started": 1,
        "calls_finished": 1,
        "calls_with_usage": 0,
        "calls_missing_usage": 1,
        "calls_unfinished": 0,
        "usage_complete": False,
    }
    assert stats["per_model"]["gpt-test"]["calls_finished"] == 1
    assert stats["input_tokens"] is None
    assert stats["output_tokens"] is None


@pytest.mark.unit
def test_persistence_stats_aggregate_model_and_cached_usage():
    handler = StatsCallbackHandler()
    handler.on_chat_model_start({"kwargs": {"model_name": "gpt-test"}}, [[]], run_id="run-1")
    response = SimpleNamespace(generations=[[
        SimpleNamespace(message=AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "input_token_details": {"cache_read": 7},
            },
        ))
    ]])
    handler.on_llm_end(response, run_id="run-1")

    stats = handler.get_persistence_stats()
    assert stats["llm_calls"] == 1
    assert stats["input_tokens"] == 12
    assert stats["output_tokens"] == 4
    assert stats["cached_input_tokens"] == 7
    assert stats["per_model"]["gpt-test"].items() >= {
        "input_tokens": 12,
        "output_tokens": 4,
        "cached_input_tokens": 7,
        "usage_complete": True,
    }.items()
    assert stats["usage_completeness"]["usage_complete"] is True
    assert stats["billed_cost"] is None
    assert "excludes deterministic prefetch" in stats["tool_calls_scope"]


@pytest.mark.unit
def test_persistence_stats_leaves_unreported_usage_unknown():
    handler = StatsCallbackHandler()
    handler.on_chat_model_start({"kwargs": {"model": "gpt-test"}}, [[]], run_id="run-2")
    response = SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content="ok"))]])
    handler.on_llm_end(response, run_id="run-2")

    stats = handler.get_persistence_stats()
    assert stats["input_tokens"] is None
    assert stats["output_tokens"] is None
    assert stats["cached_input_tokens"] is None
    assert stats["per_model"]["gpt-test"].items() >= {
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "usage_complete": False,
    }.items()


@pytest.mark.unit
def test_persistence_stats_marks_mixed_provider_usage_incomplete():
    handler = StatsCallbackHandler()
    handler.on_chat_model_start({"kwargs": {"model": "gpt-test"}}, [[]], run_id="with-usage")
    handler.on_chat_model_start({"kwargs": {"model": "gpt-test"}}, [[]], run_id="missing-usage")
    response = SimpleNamespace(generations=[[
        SimpleNamespace(message=AIMessage(
            content="ok", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        ))
    ]])
    handler.on_llm_end(response, run_id="with-usage")
    handler.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content="ok"))]]), run_id="missing-usage")

    stats = handler.get_persistence_stats()
    assert stats["input_tokens"] == 3
    assert stats["usage_completeness"] == {
        "calls_started": 2,
        "calls_finished": 2,
        "calls_with_usage": 1,
        "calls_missing_usage": 1,
        "calls_unfinished": 0,
        "usage_complete": False,
    }


@pytest.mark.unit
def test_reasoning_is_an_output_subset_and_not_added_to_totals():
    handler = StatsCallbackHandler()
    for number, model in enumerate(["gpt-one", "gpt-two", "gpt-one"]):
        handler.on_chat_model_start({"model": model}, [[]], run_id=str(number))
        message = AIMessage(content="ok", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "input_token_details": {"cache_read": 4},
            "output_token_details": {"reasoning": 3},
        })
        handler.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=message)]]), run_id=str(number))
    stats = handler.get_persistence_stats()
    assert stats["total_tokens"] == 45
    assert stats["output_tokens"] == 15
    assert stats["reasoning_output_tokens"] == 9
    assert stats["per_model"]["gpt-one"]["total_tokens"] == 30
    assert stats["per_model"]["gpt-two"]["reasoning_output_tokens"] == 3


@pytest.mark.unit
def test_empty_usage_object_does_not_mark_tokens_complete():
    handler = StatsCallbackHandler()
    handler.on_chat_model_start({"model": "gpt-test"}, [[]], run_id="empty-usage")
    message = AIMessage.model_construct(content="ok", usage_metadata={})
    handler.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=message)]]), run_id="empty-usage")
    stats = handler.get_persistence_stats()
    assert stats["usage_completeness"]["calls_with_usage"] == 0
    assert stats["total_tokens"] is None
    assert stats["reasoning_output_tokens"] is None
