"""Rendered-prompt contracts for evidence-grounded debate roles."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
from tradingagents.agents.utils.prompt_policy import debate_policy, evidence_policy

_REPORTS = {
    "company_of_interest": "AAPL",
    "asset_type": "stock",
    "market_report": "market evidence",
    "sentiment_report": "sentiment evidence",
    "news_report": "news evidence",
    "fundamentals_report": "fundamental evidence",
}


def _capturing_llm(captured: dict):
    llm = MagicMock()
    llm.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or MagicMock(content="argument")
    )
    return llm


def _investment_state(*, history="", current_response="", count=0):
    return {
        **_REPORTS,
        "investment_debate_state": {
            "history": history,
            "bull_history": "",
            "bear_history": "",
            "current_response": current_response,
            "count": count,
        },
    }


def _risk_state(*, history="", count=0, **responses):
    debate = {
        "history": history,
        "aggressive_history": "",
        "conservative_history": "",
        "neutral_history": "",
        "current_aggressive_response": "",
        "current_conservative_response": "",
        "current_neutral_response": "",
        "count": count,
    }
    debate.update(responses)
    return {**_REPORTS, "trader_investment_plan": "plan evidence", "risk_debate_state": debate}


@pytest.mark.unit
@pytest.mark.parametrize("factory", [create_bull_researcher, create_bear_researcher])
def test_research_debate_prompt_requires_evidence_and_calibration(factory):
    captured = {}
    factory(_capturing_llm(captured))(_investment_state())
    prompt = captured["prompt"].lower()

    for required in (
        "supported claims",
        "weakest assumption",
        "valid concession",
        "falsify",
        "<sources>",
        "</sources>",
        "untrusted reference data",
    ):
        assert required in prompt
    assert "persuad" not in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory", [create_aggressive_debator, create_conservative_debator, create_neutral_debator]
)
def test_risk_debate_prompt_requires_evidence_and_calibration(factory):
    captured = {}
    factory(_capturing_llm(captured))(_risk_state())
    prompt = captured["prompt"].lower()

    for required in (
        "weakest assumption",
        "valid concession",
        "falsify",
        "<sources>",
        "</sources>",
        "untrusted reference data",
    ):
        assert required in prompt
    assert "supported" in prompt and "risk" in prompt and "upside" in prompt
    assert "persuad" not in prompt


@pytest.mark.unit
def test_debate_policy_sets_round_specific_length_and_delta_requirements():
    opening = debate_policy(0, 2).lower()
    rebuttal = debate_policy(2, 2).lower()
    risk_rebuttal = debate_policy(3, 3).lower()

    assert "opening" in opening and "300" in opening and "450" in opening
    assert "rebuttal" in rebuttal and "150" in rebuttal and "250" in rebuttal
    assert "changes only" in rebuttal
    assert "rebuttal" in risk_rebuttal
    assert "untrusted data" in evidence_policy().lower()


@pytest.mark.unit
def test_research_prompt_does_not_repeat_current_argument_in_history():
    argument = "Bear Analyst: valuation evidence"
    captured = {}
    create_bull_researcher(_capturing_llm(captured))(
        _investment_state(history=argument, current_response=argument, count=1)
    )
    assert captured["prompt"].count(argument) == 1


@pytest.mark.unit
def test_risk_prompt_does_not_repeat_current_arguments_in_history():
    aggressive = "Aggressive Analyst: upside evidence"
    neutral = "Neutral Analyst: sizing evidence"
    captured = {}
    create_conservative_debator(_capturing_llm(captured))(
        _risk_state(
            history=f"{aggressive}\n{neutral}",
            count=2,
            current_aggressive_response=aggressive,
            current_neutral_response=neutral,
        )
    )
    assert captured["prompt"].count(aggressive) == 1
    assert captured["prompt"].count(neutral) == 1
