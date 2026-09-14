"""Offline checks for prompt output contracts and structured-output recovery."""

from unittest.mock import MagicMock

import httpx2
import pytest
from openai import APITimeoutError, AuthenticationError, BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.schemas import (
    PortfolioDecision,
    ResearchPlan,
    TraderProposal,
)
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.utils.prompt_policy import (
    debate_policy,
    output_policy,
    specialist_policy,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "target"),
    [
        ("market", "400–600"),
        ("news", "400–600"),
        ("social", "300–450"),
        ("sentiment", "300–450"),
        ("fundamentals", "600–900"),
        ("research_manager", "250–400"),
        ("trader", "150–250"),
        ("portfolio_manager", "400–600"),
    ],
)
def test_output_policy_has_the_role_target_and_preserves_material_caveats(role, target):
    policy = output_policy(role)
    assert target in policy
    assert "soft target" in policy
    assert "preserve material evidence, caveats" in policy
    assert "Do not truncate" in policy
    assert "extra LLM rewrite" in policy


@pytest.mark.unit
def test_output_policy_normalizes_common_role_spelling_and_limits_triggers_for_trader():
    assert output_policy("portfolio manager") == output_policy("portfolio_manager")
    assert "at most two principal triggers" in output_policy("trader")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "required_headings", "rating_values"),
    [
        (
            "research_manager",
            ("**Recommendation**:", "**Rationale**:", "**Strategic Actions**:"),
            ("Buy", "Overweight", "Hold", "Underweight", "Sell"),
        ),
        (
            "trader",
            (
                "**Action**:",
                "**Reasoning**:",
                "**Entry Price**:",
                "**Stop Loss**:",
                "**Position Sizing**:",
                "FINAL TRANSACTION PROPOSAL: **<BUY | HOLD | SELL>**",
            ),
            ("Buy", "Hold", "Sell"),
        ),
        (
            "portfolio_manager",
            (
                "**Rating**:",
                "**Executive Summary**:",
                "**Investment Thesis**:",
                "**Price Target**:",
                "**Time Horizon**:",
            ),
            ("Buy", "Overweight", "Hold", "Underweight", "Sell"),
        ),
    ],
)
def test_decision_output_policy_has_stable_renderer_headings_and_allowed_values(
    role, required_headings, rating_values
):
    policy = output_policy(role)
    for heading in required_headings:
        assert heading in policy
    for value in rating_values:
        assert value in policy
    normalized_policy = " ".join(policy.split())
    assert "populate only the corresponding schema fields" in normalized_policy
    assert "do not append text after the structured response" in normalized_policy


def _fallback_llm():
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError("unsupported")
    llm.invoke.return_value = MagicMock(content="ordinary fallback response")
    return llm


def _decision_agent_state():
    return {
        "company_of_interest": "NVDA",
        "trade_date": "2026-09-12",
        "market_report": "Market report.",
        "sentiment_report": "Sentiment report.",
        "news_report": "News report.",
        "fundamentals_report": "Fundamentals report.",
        "investment_plan": "Research plan.",
        "trader_investment_plan": "Trader plan.",
        "investment_debate_state": {
            "history": "Investment debate.",
            "bull_history": "bull",
            "bear_history": "bear",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
        "risk_debate_state": {
            "history": "Risk debate.",
            "aggressive_history": "aggressive",
            "conservative_history": "conservative",
            "neutral_history": "neutral",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 1,
        },
    }


def _prompt_text(prompt):
    if isinstance(prompt, str):
        return prompt
    return "\n".join(message["content"] for message in prompt)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("create_agent", "headings"),
    [
        (
            create_research_manager,
            ("**Recommendation**:", "**Rationale**:", "**Strategic Actions**:"),
        ),
        (
            create_trader,
            (
                "**Action**:",
                "**Reasoning**:",
                "**Entry Price**:",
                "**Stop Loss**:",
                "**Position Sizing**:",
                "FINAL TRANSACTION PROPOSAL: **<BUY | HOLD | SELL>**",
            ),
        ),
        (
            create_portfolio_manager,
            (
                "**Rating**:",
                "**Executive Summary**:",
                "**Investment Thesis**:",
                "**Price Target**:",
                "**Time Horizon**:",
            ),
        ),
    ],
)
def test_actual_fallback_prompt_keeps_the_parseable_renderer_contract(
    create_agent, headings
):
    llm = _fallback_llm()
    create_agent(llm)(_decision_agent_state())

    prompt = _prompt_text(llm.invoke.call_args.args[0])
    for heading in headings:
        assert heading in prompt


@pytest.mark.unit
def test_unknown_output_policy_role_is_rejected():
    with pytest.raises(ValueError, match="Unknown output-policy role"):
        output_policy("risk_manager")


@pytest.mark.unit
def test_specialist_policy_delegates_to_the_requested_role_target():
    policy = specialist_policy("fundamentals")
    assert output_policy("fundamentals") in policy
    assert "400–700" not in policy


@pytest.mark.unit
def test_debate_lengths_remain_opening_then_rebuttal():
    assert "300–450" in debate_policy(count=0, participants=3)
    assert "150–250" in debate_policy(count=3, participants=3)


@pytest.mark.unit
def test_structured_descriptions_keep_trigger_metadata_without_hard_length_rules():
    trader_description = TraderProposal.model_fields["reasoning"].description
    actions_description = ResearchPlan.model_fields["strategic_actions"].description
    assert "Two to four sentences" not in trader_description
    for description in (trader_description, actions_description):
        assert "rationale" in description
        assert "as-of date" in description
        assert "horizon" in description
        assert "refresh or expiry" in description
        assert "invalidation" in description
    assert "400–600 words" not in PortfolioDecision.model_fields["investment_thesis"].description


class _ExampleSchema(BaseModel):
    value: int


def _status_error(error_type, status_code, message):
    request = httpx2.Request("POST", "https://example.test/v1/responses")
    response = httpx2.Response(status_code, request=request)
    return error_type(message, response=response, body={"error": {"message": message}})


@pytest.mark.unit
def test_parse_failure_retries_once_with_the_identical_prompt_and_contract():
    prompt = "Decision request\n" + output_policy("trader")
    structured = MagicMock()
    structured.invoke.side_effect = ValueError("bad JSON from model")
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="free-text proposal")

    result = invoke_structured_or_freetext(
        structured, plain, prompt, render=str, agent_name="Trader"
    )

    assert result == "free-text proposal"
    structured.invoke.assert_called_once_with(prompt)
    plain.invoke.assert_called_once_with(prompt)
    assert "150–250" in plain.invoke.call_args.args[0]


@pytest.mark.unit
def test_validation_failure_retries_as_free_text():
    structured = MagicMock()
    with pytest.raises(ValidationError) as captured:
        _ExampleSchema(value="not-an-integer")
    structured.invoke.side_effect = captured.value
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="free-text proposal")

    assert invoke_structured_or_freetext(
        structured, plain, "prompt", render=str, agent_name="Trader"
    ) == "free-text proposal"
    plain.invoke.assert_called_once_with("prompt")


@pytest.mark.unit
def test_explicit_unsupported_format_400_retries_as_free_text():
    structured = MagicMock()
    structured.invoke.side_effect = _status_error(
        BadRequestError, 400, "response_format json_schema is not supported"
    )
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="free-text proposal")

    assert invoke_structured_or_freetext(
        structured, plain, "prompt", render=str, agent_name="Trader"
    ) == "free-text proposal"
    plain.invoke.assert_called_once_with("prompt")


@pytest.mark.unit
def test_generic_400_does_not_hide_the_provider_error():
    provider_error = _status_error(BadRequestError, 400, "model parameter is invalid")
    structured = MagicMock()
    structured.invoke.side_effect = provider_error
    plain = MagicMock()

    with pytest.raises(BadRequestError):
        invoke_structured_or_freetext(
            structured, plain, "prompt", render=str, agent_name="Trader"
        )
    plain.invoke.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider_error",
    [
        _status_error(AuthenticationError, 401, "invalid API key"),
        _status_error(RateLimitError, 429, "quota exceeded"),
        APITimeoutError(httpx2.Request("POST", "https://example.test/v1/responses")),
    ],
)
def test_auth_quota_and_timeout_errors_propagate(provider_error):
    structured = MagicMock()
    structured.invoke.side_effect = provider_error
    plain = MagicMock()

    with pytest.raises(type(provider_error)):
        invoke_structured_or_freetext(
            structured, plain, "prompt", render=str, agent_name="Trader"
        )
    plain.invoke.assert_not_called()


@pytest.mark.unit
def test_binding_retries_only_after_an_explicit_unsupported_format_error():
    llm = MagicMock()
    llm.with_structured_output.side_effect = _status_error(
        BadRequestError, 400, "structured output is not supported for this model"
    )
    assert bind_structured(llm, _ExampleSchema, "Trader") is None

    llm.with_structured_output.side_effect = _status_error(
        BadRequestError, 400, "model parameter is invalid"
    )
    with pytest.raises(BadRequestError):
        bind_structured(llm, _ExampleSchema, "Trader")
