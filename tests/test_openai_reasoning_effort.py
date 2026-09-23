"""OpenAI ``reasoning_effort`` is gated to reasoning models.

Non-reasoning OpenAI models (gpt-4.1, gpt-4o, ...) 400 with "Unsupported
parameter: 'reasoning.effort'". The client must drop the kwarg for those rather
than forward it and crash the run. GPT-6 Astra/Sol/Luna, the GPT-5 family,
and the o-series accept it.
"""

import pytest

from tradingagents.llm_clients.openai_client import (
    OpenAIClient,
    _supports_reasoning_effort,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-6-astra", True),
        ("gpt-6-sol", True),
        ("gpt-6-luna", True),
        ("gpt-6-sol-2026-09-22", True),
        ("gpt-6-luna-2026-09-22", True),
        ("gpt-6-solar", False),
        ("gpt-6-lunar", False),
        ("gpt-5.6-sol", True),
        ("gpt-5.6", True),  # Documented alias for Sol.
        ("gpt-5.6-terra", True),
        ("gpt-5.6-luna", True),
        ("gpt-5.5-pro", True),
        ("gpt-5.5", True),
        ("gpt-5.4", True),
        ("gpt-5.4-mini", True),
        ("o3-mini", True),
        ("o1", True),
        ("gpt-4.1", False),
        ("gpt-4o", False),
        ("gpt-4o-mini", False),
        ("gpt-3.5-turbo", False),
    ],
)
def test_supports_reasoning_effort(model, expected):
    assert _supports_reasoning_effort(model) is expected


def _effort_on(model, monkeypatch):
    # A fake key lets get_llm() construct the client without a network call.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = OpenAIClient(model, provider="openai", reasoning_effort="low").get_llm()
    return getattr(llm, "reasoning_effort", None)


def test_reasoning_model_receives_effort(monkeypatch):
    assert _effort_on("gpt-5.4-mini", monkeypatch) == "low"


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6"])
def test_current_models_receive_effort_and_use_responses(model, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = OpenAIClient(model, provider="openai", reasoning_effort="low").get_llm()
    assert llm.reasoning_effort == "low"
    assert llm.use_responses_api is True


def test_non_reasoning_model_drops_effort(monkeypatch):
    # gpt-4.1 would 400 with reasoning_effort — it must be dropped.
    assert _effort_on("gpt-4.1", monkeypatch) is None


def test_named_profile_efforts_reach_native_api_client(monkeypatch):
    from tradingagents.model_profiles import MODEL_PROFILES

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    selections = {
        (role["model"], role["reasoning_effort"])
        for profile in MODEL_PROFILES.values() for role in profile["agents"].values()
    }
    for model, effort in selections:
        llm = OpenAIClient(model, provider="openai", reasoning_effort=effort).get_llm()
        assert llm.reasoning_effort == effort
        assert llm.use_responses_api is True
