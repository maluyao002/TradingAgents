"""Named OpenAI analysis profiles; no network calls or global config mutation."""

from copy import deepcopy

DEFAULT_MODEL_PROFILE = "balanced"


def _agent(tier, effort):
    return {"model": {"luna": "gpt-5.6-luna", "terra": "gpt-5.6-terra",
                      "sol": "gpt-5.6-sol", "astra": "gpt-6-astra"}[tier],
            "reasoning_effort": effort}


def _profile(label, rounds, base, overrides):
    roles = ("market", "social", "news", "fundamentals", "bull", "bear",
             "research_manager", "trader", "aggressive", "conservative",
             "neutral", "portfolio_manager")
    agents = {role: _agent(*base) for role in roles}
    agents.update({role: _agent(*setting) for role, setting in overrides.items()})
    agents["signal"] = _agent("luna", "low")
    agents["reflection"] = _agent("sol", "high") if rounds == 3 else _agent("terra", "medium")
    return {"label": label, "rounds": rounds, "agents": agents}


MODEL_PROFILES = {
    "quick": _profile("Quick", 1, ("luna", "low"), dict.fromkeys(("fundamentals", "research_manager", "trader", "portfolio_manager"), ("terra", "medium"))),
    "balanced": _profile("Balanced", 2, ("terra", "medium"), {
        "fundamentals": ("sol", "high"), "research_manager": ("sol", "high"),
        "trader": ("sol", "medium"), "portfolio_manager": ("astra", "high"),
    }),
    "deep": _profile("Deep", 3, ("sol", "medium"), {
        "market": ("terra", "high"), "social": ("terra", "high"),
        "news": ("terra", "high"), "fundamentals": ("sol", "high"),
        "research_manager": ("astra", "high"), "trader": ("sol", "high"),
        "portfolio_manager": ("astra", "high"),
    }),
}


def apply_model_profile(config, profile_name=DEFAULT_MODEL_PROFILE):
    """Return an independent config; callers apply explicit round overrides after this.

    Profiles use OpenAI models and must not silently redirect another provider.
    Legacy quick/deep fields remain representative values for older integrations.
    """
    if profile_name not in MODEL_PROFILES:
        raise ValueError(f"Unknown model profile: {profile_name!r}")
    if config.get("llm_provider", "openai").lower() != "openai":
        raise ValueError("Model profiles require the OpenAI provider; use Custom for other providers.")
    result = deepcopy(config)
    profile = MODEL_PROFILES[profile_name]
    result.update(model_profile=profile_name, llm_provider="openai",
                  agent_models=deepcopy(profile["agents"]),
                  max_debate_rounds=profile["rounds"],
                  max_risk_discuss_rounds=profile["rounds"],
                  quick_think_llm=profile["agents"]["signal"]["model"],
                  deep_think_llm=profile["agents"]["portfolio_manager"]["model"],
                  openai_reasoning_effort=None)
    return result
