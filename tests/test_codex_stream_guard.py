"""Offline stream fragmentation, flood and identity regression tests."""
from itertools import chain, repeat
from types import SimpleNamespace

import pytest

from tradingagents.codex import adapter as module
from tradingagents.codex.adapter import CodexAdapter, CodexInferenceError, CodexTokenUsage
from tradingagents.codex.transport import TransportTimeout


def event(method, **params):
    return {"method": method, "params": {"threadId": "thread-1", "turnId": "turn-1", **params}}


def start(item_type="agentMessage"):
    return [event("turn/started", turn={"id": "turn-1", "status": "inProgress"}),
            event("item/started", item={"id": "message-1", "type": item_type})]


def delta(text="x", **params):
    return event("item/agentMessage/delta", **{"itemId": "message-1", "delta": text, **params})


def finish(text="final analysis"):
    return [event("item/completed", item={"id": "message-1", "type": "agentMessage",
                                        "text": text, "phase": "final_answer"}),
            event("turn/completed", turn={"id": "turn-1", "status": "completed"})]


def wait(tmp_path, events, *, retired=()):
    events = iter(events)
    adapter = CodexAdapter(tmp_path / "runtime")
    adapter._retired_threads.update(retired)
    adapter._transport = SimpleNamespace(wait_notification=lambda **kwargs: next(events))
    return adapter._wait_for_turn("thread-1", "turn-1", module.time.monotonic() + 30,
                                  model="test", effort="medium")


def test_fragmented_output_above_old_limit_completes_with_usage(tmp_path):
    counters = {"inputTokens": 10, "outputTokens": 5, "cachedInputTokens": 4,
                "reasoningOutputTokens": 2, "totalTokens": 15}
    usage = event("thread/tokenUsage/updated", tokenUsage={"total": counters, "last": counters})
    result = wait(tmp_path, chain(start(), repeat(delta(), 10_001), [usage], finish()))
    assert result.text == "final analysis"
    assert result.usage == CodexTokenUsage(10, 5, 4, 2, 15)


@pytest.mark.parametrize("noise", [
    event("warning", message="advisory"), delta(""),
    delta(threadId="retired"), event("ignored", threadId="unrelated", turnId="old"),
    event("item/reasoning/textDelta", itemId="message-1", delta="reasoning"),
])
def test_non_stream_traffic_remains_bounded(tmp_path, monkeypatch, noise):
    monkeypatch.setattr(module, "_MAX_TURN_EVENTS", 5)
    with pytest.raises(CodexInferenceError, match="notification safety limit"):
        wait(tmp_path, chain(start(), repeat(noise, 6)), retired=["retired"])


def test_stream_event_cap_is_separate_and_finite(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_MAX_STREAM_EVENTS", 3)
    assert wait(tmp_path, chain(start(), repeat(delta(), 3), finish())).text
    with pytest.raises(CodexInferenceError, match="streaming event safety limit"):
        wait(tmp_path, chain(start(), repeat(delta(), 4)))


def test_control_cap_boundary_includes_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_MAX_TURN_EVENTS", 4)
    assert wait(tmp_path, chain(start(), repeat(delta(), 20), finish())).text
    with pytest.raises(CodexInferenceError, match="notification safety limit"):
        wait(tmp_path, chain(start(), [delta("")], finish()))


@pytest.mark.parametrize("text", ["abc", "中文😀"])
def test_stream_chars_cumulative_and_not_double_counted(tmp_path, monkeypatch, text):
    monkeypatch.setattr(module, "_MAX_STREAM_CHARS", 6)
    assert wait(tmp_path, chain(start(), repeat(delta(text), 2), finish(text * 2))).text == text * 2
    with pytest.raises(CodexInferenceError, match="streamed output exceeded"):
        wait(tmp_path, chain(start(), repeat(delta(text), 3)))


def test_completed_output_cap_still_applies_without_deltas(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_MAX_OUTPUT_CHARS", 3)
    with pytest.raises(CodexInferenceError, match="output exceeded"):
        wait(tmp_path, chain(start(), finish("long")))


@pytest.mark.parametrize("params,match", [
    ({"itemId": "unknown"}, "invalid agent message delta"),
    ({"itemId": []}, "invalid agent message delta"),
    ({"delta": None}, "invalid agent message delta"),
    ({"turnId": "unknown"}, "unknown turn"),
    ({"threadId": "unknown"}, "unknown thread"),
])
def test_stream_identity_is_validated(tmp_path, params, match):
    with pytest.raises(CodexInferenceError, match=match):
        wait(tmp_path, chain(start(), [delta(**params)]))


@pytest.mark.parametrize("prefix", [[], start("reasoning"), start()[1:], start() + finish()[:1]])
@pytest.mark.parametrize("text", ["x", ""])
def test_delta_requires_live_agent_message_in_started_turn(tmp_path, prefix, text):
    with pytest.raises(CodexInferenceError, match="invalid agent message delta"):
        wait(tmp_path, chain(prefix, [delta(text)]))


def test_item_type_must_not_change_at_completion(tmp_path):
    with pytest.raises(CodexInferenceError, match="invalid item completion"):
        wait(tmp_path, chain(start("reasoning"), finish()))


@pytest.mark.parametrize("bad", [
    event("item/started", item={"id": "tool-1", "type": "commandExecution"}),
    event("model/rerouted"), event("unknownActiveMethod"),
])
def test_streaming_does_not_bypass_other_guards(tmp_path, bad):
    with pytest.raises(CodexInferenceError):
        wait(tmp_path, chain(start(), repeat(delta(), 10_001), [bad]))


def test_streaming_does_not_extend_absolute_deadline(tmp_path, monkeypatch):
    times = iter([0, 0, 0, 1, 31])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with pytest.raises(TransportTimeout, match="deadline"):
        wait(tmp_path, chain(start(), repeat(delta(), 10)))
