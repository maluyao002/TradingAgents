"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a consolidated report and optional per-section markdown (analysts,
research, trading, risk, portfolio) under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _safe_model_id(value: Any) -> str | None:
    """Allow a model label, never an endpoint or multiline runtime value."""
    if not isinstance(value, str) or not value or "://" in value or "\n" in value:
        return None
    return value


def _valid_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _usage_per_model(value: Any) -> dict[str, dict[str, int | None]]:
    if not isinstance(value, dict):
        return {}
    safe = {}
    for model, metrics in value.items():
        model_id = _safe_model_id(model)
        if model_id is None or not isinstance(metrics, dict):
            continue
        safe[model_id] = {
            key: _valid_count(metrics.get(key))
            for key in ("input_tokens", "output_tokens", "cached_input_tokens")
        }
        safe[model_id].update({
            key: _valid_count(metrics.get(key))
            for key in ("calls_started", "calls_finished", "calls_with_usage")
        })
        safe[model_id]["usage_complete"] = metrics.get("usage_complete") is True
    return safe


def _usage_completeness(value: Any) -> dict[str, int | bool | None]:
    if not isinstance(value, dict):
        value = {}
    return {
        key: _valid_count(value.get(key))
        for key in ("calls_started", "calls_finished", "calls_with_usage", "calls_missing_usage", "calls_unfinished")
    } | {"usage_complete": value.get("usage_complete") is True}


def build_run_metadata(
    final_state: dict,
    ticker: str,
    config: dict | None = None,
) -> dict[str, Any]:
    """Create the intentionally small, non-secret run metadata sidecar."""
    config = config or {}
    runtime = final_state.get("_run_metadata", {})
    if not isinstance(runtime, dict):
        runtime = {}
    configured_roles = config.get("agent_models", {})
    roles = {}
    if isinstance(configured_roles, dict):
        for role, setting in sorted(configured_roles.items()):
            if not isinstance(role, str) or not isinstance(setting, dict):
                continue
            model = setting.get("model")
            effort = setting.get("reasoning_effort")
            roles[role] = {
                "model": _safe_model_id(model),
                "reasoning_effort": effort if isinstance(effort, str) else None,
            }
    if not roles:
        global_effort = config.get("openai_reasoning_effort")
        for role, key in (("quick_think", "quick_think_llm"), ("deep_think", "deep_think_llm")):
            roles[role] = {
                "model": _safe_model_id(config.get(key)),
                "reasoning_effort": global_effort if isinstance(global_effort, str) else None,
            }

    usage = runtime.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    return {
        "schema_version": 1,
        "backend": "codex" if config.get("llm_backend") == "codex" else "api",
        "ticker": ticker,
        "analysis_date": final_state.get("trade_date"),
        "model_profile": next(
            (
                value
                for value in (config.get("openai_model_profile"), config.get("model_profile"))
                if isinstance(value, str)
            ),
            None,
        ),
        "roles": roles,
        "debate_rounds": (
            config.get("max_debate_rounds")
            if isinstance(config.get("max_debate_rounds"), int)
            else None
        ),
        "risk_debate_rounds": (
            config.get("max_risk_discuss_rounds")
            if isinstance(config.get("max_risk_discuss_rounds"), int) else None
        ),
        "elapsed_seconds": (
            runtime.get("elapsed_seconds")
            if isinstance(runtime.get("elapsed_seconds"), (int, float))
            else None
        ),
        "usage": {
            "llm_calls": _valid_count(usage.get("llm_calls")),
            "input_tokens": _valid_count(usage.get("input_tokens")),
            "output_tokens": _valid_count(usage.get("output_tokens")),
            "cached_input_tokens": _valid_count(usage.get("cached_input_tokens")),
            "per_model": _usage_per_model(usage.get("per_model")),
            "usage_completeness": _usage_completeness(usage.get("usage_completeness")),
            "tool_calls": _valid_count(usage.get("tool_calls")),
            "tool_calls_scope": (
                "LangChain agent tool callbacks; excludes deterministic prefetch and data preparation."
            ),
            "billed_cost": None,
        },
    }


def write_report_tree(
    final_state: dict, ticker: str, save_path, config: dict | None = None
) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path."""
    save_path = Path(save_path)
    if save_path.exists() and any(save_path.iterdir()):
        raise FileExistsError("Report destination is not empty. Choose a new directory.")
    save_path.mkdir(parents=True, exist_ok=True)
    (save_path / "run_metadata.json").write_text(
        json.dumps(build_run_metadata(final_state, ticker, config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Source data and machine-readable handoffs accompany the unchanged reports.
    if final_state.get("evidence_packets") or final_state.get("prepared_data"):
        (save_path / "evidence.json").write_text(json.dumps({
            "analysis_date": final_state.get("trade_date"),
            "evidence_packets": final_state.get("evidence_packets", {}),
            "prepared_data": final_state.get("prepared_data", {}),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    sections = []
    split_files = (config or {}).get("report_split_files", False)

    def write_section(path: Path, content: str) -> None:
        if split_files:
            path.parent.mkdir(exist_ok=True)
            path.write_text(content, encoding="utf-8")

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        write_section(analysts_dir / "market.md", final_state["market_report"])
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        write_section(analysts_dir / "sentiment.md", final_state["sentiment_report"])
        analyst_parts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        write_section(analysts_dir / "news.md", final_state["news_report"])
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        write_section(analysts_dir / "fundamentals.md", final_state["fundamentals_report"])
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            write_section(research_dir / "bull.md", debate["bull_history"])
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            write_section(research_dir / "bear.md", debate["bear_history"])
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            write_section(research_dir / "manager.md", debate["judge_decision"])
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        write_section(trading_dir / "trader.md", final_state["trader_investment_plan"])
        sections.append(f"## Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            write_section(risk_dir / "aggressive.md", risk["aggressive_history"])
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            write_section(risk_dir / "conservative.md", risk["conservative_history"])
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            write_section(risk_dir / "neutral.md", risk["neutral_history"])
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            write_section(portfolio_dir / "decision.md", risk["judge_decision"])
            sections.append(f"## Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # Put the final decision first without shortening or re-generating analysis.
    sections.sort(key=lambda section: not section.startswith("## Portfolio Manager Decision"))

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")
    return save_path / "complete_report.md"
