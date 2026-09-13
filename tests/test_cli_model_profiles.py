"""CLI profile selection stays separate from the custom configuration path."""

from unittest import mock

import pytest


@pytest.mark.unit
@pytest.mark.parametrize("launcher", [False, True])
def test_balanced_profile_skips_custom_openai_controls(monkeypatch, launcher):
    import cli.main as main

    for name in (
        "TRADINGAGENTS_OPENAI_REASONING_EFFORT",
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_QUICK_THINK_LLM",
        "TRADINGAGENTS_DEEP_THINK_LLM",
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS",
        "TRADINGAGENTS_MAX_RISK_ROUNDS",
    ):
        monkeypatch.delenv(name, raising=False)
    if launcher:
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "openai")
        monkeypatch.setitem(main.DEFAULT_CONFIG, "llm_provider", "openai")

    with mock.patch.object(main, "fetch_announcements", return_value=None), \
         mock.patch.object(main, "display_announcements"), \
         mock.patch.object(main, "get_ticker", return_value="AAPL"), \
         mock.patch.object(main, "get_analysis_date", return_value="2026-05-29"), \
         mock.patch.object(main, "ask_output_language", return_value="English"), \
         mock.patch.object(main, "select_analysts", return_value=[]), \
         mock.patch.object(main, "select_llm_provider", return_value=("openai", None)), \
         mock.patch.object(main, "ensure_api_key"), \
         mock.patch.object(main, "select_model_profile", return_value="balanced") as profile, \
         mock.patch.object(main, "select_research_depth") as depth, \
         mock.patch.object(main, "select_shallow_thinking_agent") as quick, \
         mock.patch.object(main, "select_deep_thinking_agent") as deep, \
         mock.patch.object(main, "ask_openai_reasoning_effort") as effort:
        selections = main.get_user_selections()

    profile.assert_called_once_with("balanced")
    depth.assert_not_called()
    quick.assert_not_called()
    deep.assert_not_called()
    effort.assert_not_called()
    assert selections["model_profile"] == "balanced"
    assert selections["research_depth"] == 2


@pytest.mark.unit
def test_profile_config_uses_agent_models_and_honors_round_env(monkeypatch):
    import cli.main as main

    selections = {
        "model_profile": "balanced",
        "research_depth": 2,
        "shallow_thinker": None,
        "deep_thinker": None,
        "backend_url": None,
        "llm_provider": "openai",
        "output_language": "English",
    }
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1")
    monkeypatch.setenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "3")
    configured_default = dict(
        main.DEFAULT_CONFIG, max_debate_rounds=1, max_risk_discuss_rounds=3
    )
    with mock.patch.object(main, "DEFAULT_CONFIG", configured_default):
        config = main._build_run_config(selections, checkpoint=None)

    assert config["model_profile"] == "balanced"
    assert config["agent_models"]["portfolio_manager"]["model"] == "gpt-6-astra"
    assert config["max_debate_rounds"] == 1
    assert config["max_risk_discuss_rounds"] == 3
    assert config["openai_reasoning_effort"] is None


@pytest.mark.unit
def test_profile_rounds_do_not_depend_on_a_stale_selection(monkeypatch):
    import cli.main as main

    for name in ("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS"):
        monkeypatch.delenv(name, raising=False)
    config = main._build_run_config(
        {
            "model_profile": "balanced",
            "research_depth": 5,
            "shallow_thinker": None,
            "deep_thinker": None,
            "backend_url": None,
            "llm_provider": "openai",
            "output_language": "English",
        },
        checkpoint=None,
    )

    assert config["max_debate_rounds"] == config["max_risk_discuss_rounds"] == 2


@pytest.mark.unit
def test_cancelling_profile_selection_exits(monkeypatch):
    import cli.utils as utils

    prompt = mock.Mock()
    prompt.ask.return_value = None
    monkeypatch.setattr(utils.questionary, "select", mock.Mock(return_value=prompt))

    with pytest.raises(SystemExit, match="1"):
        utils.select_model_profile()
