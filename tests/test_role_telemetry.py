"""Per-call telemetry remains role-aware, concurrency-safe, and content-free."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from cli.stats_handler import StatsCallbackHandler


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _response(*, usage=None, runtime=None):
    message = AIMessage(
        content="ok",
        usage_metadata=usage,
        response_metadata={"tradingagents_runtime": runtime or {}},
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


@pytest.mark.unit
def test_shared_model_separates_roles_and_reports_union_active_time_and_failure():
    clock = FakeClock()
    handler = StatsCallbackHandler(clock=clock)

    handler.on_chat_model_start(
        {"model": "gpt-shared"},
        [[HumanMessage("market")]],
        run_id="market",
        metadata={"langgraph_node": "Market Analyst"},
    )
    clock.value = 2.0
    handler.on_chat_model_start(
        {"model": "gpt-shared", "role": "bear"},
        [[HumanMessage("bear")]],
        run_id="bear",
    )
    clock.value = 5.0
    handler.on_llm_end(
        _response(usage={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}),
        run_id="market",
    )
    clock.value = 7.0
    handler.on_llm_error(RuntimeError("offline failure"), run_id="bear")

    stats = handler.get_persistence_stats()
    assert stats["llm_elapsed_seconds"] == 10.0
    assert stats["model_active_seconds"] == 7.0
    assert stats["per_model"]["gpt-shared"].items() >= {
        "calls_started": 2,
        "calls_finished": 2,
        "calls_with_usage": 1,
        "usage_complete": False,
    }.items()
    assert stats["per_role"]["market"].items() >= {
        "input_tokens": 8,
        "elapsed_seconds": 5.0,
        "calls_failed": 0,
        "usage_complete": True,
    }.items()
    assert stats["per_role"]["bear"].items() >= {
        "input_tokens": None,
        "elapsed_seconds": 5.0,
        "calls_failed": 1,
        "usage_complete": False,
    }.items()
    assert [call["status"] for call in stats["calls"]] == ["succeeded", "failed"]
    assert stats["calls"][1]["input_tokens"] is None


@pytest.mark.unit
def test_runtime_fields_are_strictly_whitelisted_and_numeric():
    clock = FakeClock(1.0)
    handler = StatsCallbackHandler(clock=clock)
    handler.on_chat_model_start(
        {"kwargs": {"model": "gpt-safe", "role": "portfolio_manager"}},
        [[HumanMessage("request")]],
        run_id="runtime",
    )
    clock.value = 4.0
    handler.on_llm_end(
        _response(
            runtime={
                "bridge_seconds": 3.0,
                "adapter_seconds": 2,
                "cleanup_seconds": 0.25,
                "validation_seconds": 0.1,
                "instructions_characters": 140,
                "serialized_prompt_characters": 900,
                "output_schema_characters": 25.5,
                "archive_seconds": 99,
                "turn_wait_seconds": float("inf"),
                "thread_start_seconds": -1,
                "turn_start_seconds": True,
                "secret": "never persist",
            }
        ),
        run_id="runtime",
    )

    call = handler.get_persistence_stats()["calls"][0]
    assert call["runtime"] == {
        "adapter_seconds": 2,
        "bridge_seconds": 3.0,
        "cleanup_seconds": 0.25,
        "instructions_characters": 140,
        "serialized_prompt_characters": 900,
        "validation_seconds": 0.1,
    }


@pytest.mark.unit
def test_reused_evidence_block_is_found_inside_different_messages_without_content_leakage():
    clock = FakeClock()
    handler = StatsCallbackHandler(clock=clock)
    evidence = "<analyst_evidence>private source payload</analyst_evidence>"
    first = f"Round one\n{evidence}\nBull history"
    second = f"Round two changed\n{evidence}\nBear history"

    handler.on_chat_model_start(
        {"model": "gpt-test"}, [[HumanMessage(first)]], run_id="one",
        metadata={"tradingagents_role": "bull"},
    )
    clock.value = 1.0
    handler.on_llm_end(_response(), run_id="one")
    handler.on_chat_model_start(
        {"model": "gpt-test"}, [[HumanMessage(second)]], run_id="two",
        metadata={"tradingagents_role": "bear"},
    )
    clock.value = 2.0
    handler.on_llm_end(_response(), run_id="two")

    stats = handler.get_persistence_stats()
    first_call, second_call = stats["calls"]
    assert first_call["analyst_evidence_characters"] == len(evidence)
    assert first_call["repeated_analyst_evidence_characters"] == 0
    assert second_call["repeated_message_characters"] == 0
    assert second_call["repeated_analyst_evidence_characters"] == len(evidence)
    assert stats["per_role"]["bear"]["repeated_analyst_evidence_characters"] == len(evidence)
    assert "private source payload" not in json.dumps(stats)
    assert "Bull history" not in json.dumps(stats)


@pytest.mark.unit
def test_exact_message_repeats_count_within_and_across_calls_by_message_type():
    clock = FakeClock()
    handler = StatsCallbackHandler(clock=clock)
    handler.on_chat_model_start(
        {"model": "gpt-test", "role": "trader"},
        [[HumanMessage("same"), HumanMessage("same"), SystemMessage("same")]],
        run_id="first",
    )
    clock.value = 1.0
    handler.on_llm_end(_response(), run_id="first")
    handler.on_chat_model_start(
        {"model": "gpt-test", "role": "trader"},
        [[HumanMessage("same")]],
        run_id="second",
    )
    clock.value = 2.0
    handler.on_llm_end(_response(), run_id="second")

    calls = handler.get_persistence_stats()["calls"]
    assert calls[0]["prompt_characters"] == 12
    assert calls[0]["repeated_message_characters"] == 4
    assert calls[1]["repeated_message_characters"] == 4


@pytest.mark.unit
def test_duplicate_callbacks_do_not_double_count_and_unsafe_labels_become_unknown():
    clock = FakeClock()
    handler = StatsCallbackHandler(clock=clock)
    serialized = {"model": "https://secret.invalid/model", "role": "invented role"}
    handler.on_llm_start(serialized, ["sensitive prompt"], run_id="duplicate")
    handler.on_llm_start(serialized, ["sensitive prompt"], run_id="duplicate")
    clock.value = 3.0
    handler.on_llm_end(_response(), run_id="duplicate")
    handler.on_llm_error(RuntimeError("duplicate terminal callback"), run_id="duplicate")

    stats = handler.get_persistence_stats()
    assert stats["llm_calls"] == 1
    assert len(stats["calls"]) == 1
    assert stats["calls"][0].items() >= {
        "role": "unknown", "model": "unknown", "status": "succeeded",
        "elapsed_seconds": 3.0,
    }.items()
    assert stats["per_role"]["unknown"]["calls_finished"] == 1
    assert "sensitive prompt" not in json.dumps(stats)


@pytest.mark.unit
def test_metadata_role_precedes_graph_and_serialized_fallbacks():
    clock = FakeClock()
    handler = StatsCallbackHandler(clock=clock)
    handler.on_llm_start(
        {"model": "gpt-test", "role": "bear"}, ["x"], run_id="role",
        metadata={"tradingagents_role": "research_manager", "langgraph_node": "Trader"},
    )
    clock.value = 1.0
    handler.on_llm_end(_response(), run_id="role")
    assert handler.get_persistence_stats()["calls"][0]["role"] == "research_manager"
