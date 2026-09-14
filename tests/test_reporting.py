"""Report parity and privacy-safe run metadata for CLI/API reports."""

import json
from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"judge_decision": "RM PLAN"},
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"judge_decision": "PM DECISION"},
    }


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete
    metadata = json.loads((tmp_path / "run_metadata.json").read_text())
    assert metadata["ticker"] == "AAPL"
    assert metadata["elapsed_seconds"] is None
    assert metadata["usage"]["input_tokens"] is None
    assert "excludes deterministic prefetch" in metadata["usage"]["tool_calls_scope"]


@pytest.mark.unit
def test_run_metadata_persists_allowlisted_config_and_observed_usage(tmp_path):
    state = _state() | {
        "trade_date": "2026-09-13",
        "_run_metadata": {
            "elapsed_seconds": 12.5,
            "usage": {
                "llm_calls": 2,
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_input_tokens": None,
                "per_model": {
                    "gpt-test": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_input_tokens": None,
                    },
                    "https://private.example/v1": {"input_tokens": 99},
                },
                "tool_calls": 3,
                "billed_cost": 999,
            },
        },
    }
    config = {
        "model_profile": "balanced",
        "max_debate_rounds": 2,
        "max_risk_discuss_rounds": 2,
        "backend_url": "https://private.example/v1",
        "api_key": "secret",
        "agent_models": {
            "market": {
                "model": "gpt-test",
                "reasoning_effort": "medium",
                "api_key": "secret",
            }
        },
    }
    write_report_tree(state, "AAPL", tmp_path, config=config)
    metadata_text = (tmp_path / "run_metadata.json").read_text()
    metadata = json.loads(metadata_text)
    assert metadata["roles"] == {"market": {"model": "gpt-test", "reasoning_effort": "medium"}}
    assert metadata["usage"]["input_tokens"] == 100
    assert metadata["usage"]["cached_input_tokens"] is None
    assert metadata["usage"]["per_model"]["gpt-test"].items() >= {
        "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": None,
    }.items()
    assert metadata["usage"]["billed_cost"] is None
    assert "private.example" not in metadata_text
    assert "secret" not in metadata_text


@pytest.mark.unit
def test_run_metadata_uses_custom_global_models_and_profile_alias(tmp_path):
    config = {
        "openai_model_profile": "custom",
        "quick_think_llm": "custom-fast",
        "deep_think_llm": "custom-deep",
        "openai_reasoning_effort": "high",
    }
    write_report_tree(_state(), "AAPL", tmp_path, config=config)
    metadata = json.loads((tmp_path / "run_metadata.json").read_text())
    assert metadata["model_profile"] == "custom"
    assert metadata["roles"] == {
        "quick_think": {"model": "custom-fast", "reasoning_effort": "high"},
        "deep_think": {"model": "custom-deep", "reasoning_effort": "high"},
    }


@pytest.mark.unit
def test_run_metadata_rejects_boolean_or_negative_usage_counts(tmp_path):
    state = _state() | {
        "_run_metadata": {
            "usage": {
                "llm_calls": True,
                "input_tokens": -1,
                "output_tokens": 0,
                "cached_input_tokens": False,
                "tool_calls": -2,
            }
        }
    }
    write_report_tree(state, "AAPL", tmp_path)
    usage = json.loads((tmp_path / "run_metadata.json").read_text())["usage"]
    assert usage["llm_calls"] is None
    assert usage["input_tokens"] is None
    assert usage["cached_input_tokens"] is None
    assert usage["tool_calls"] is None
    assert usage["output_tokens"] == 0


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    # Unbound: with an explicit save_path, the method doesn't touch self/config.
    out = TradingAgentsGraph.save_reports(None, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    mock_self = SimpleNamespace(config={"results_dir": str(tmp_path)})
    out = TradingAgentsGraph.save_reports(mock_self, _state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")
