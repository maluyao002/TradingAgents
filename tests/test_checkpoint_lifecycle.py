"""The checkpoint lifecycle is reusable so --checkpoint works on the CLI path (#1249).

Checkpoint setup previously lived only inside ``propagate``; the CLI streamed the
checkpointer-less graph, so ``--checkpoint`` neither saved nor resumed. The
lifecycle is now ``begin_checkpoint`` / ``end_checkpoint`` /
``clear_checkpoint_on_success`` on TradingAgentsGraph, used by both paths. These
tests drive that lifecycle exactly as the CLI does (begin -> stream self.graph ->
clear/end) and prove state is saved and resumed.
"""

from __future__ import annotations

import copy
import os
import tempfile
import time
from pathlib import Path
from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import checkpoint_step
from tradingagents.graph.trading_graph import TradingAgentsGraph

_should_crash = False


class _State(TypedDict):
    count: int


def _node_a(state: _State) -> dict:
    return {"count": state["count"] + 1}


def _node_b(state: _State) -> dict:
    if _should_crash:
        raise RuntimeError("simulated mid-stream crash")
    return {"count": state["count"] + 10}


def _workflow() -> StateGraph:
    b = StateGraph(_State)
    b.add_node("analyst", _node_a)
    b.add_node("trader", _node_b)
    b.set_entry_point("analyst")
    b.add_edge("analyst", "trader")
    b.add_edge("trader", END)
    return b


def _bare_graph(tmpdir, *, enabled=True, config=None):
    g = object.__new__(TradingAgentsGraph)
    g.config = copy.deepcopy(DEFAULT_CONFIG)
    g.config.update(
        {
            "checkpoint_enabled": enabled,
            "data_cache_dir": tmpdir,
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
        }
    )
    if config:
        g.config.update(config)
    g.selected_analysts = ("market",)
    g.workflow = _workflow()
    g.graph = g.workflow.compile()
    g._checkpointer_ctx = None
    return g


@pytest.mark.unit
def test_disabled_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        g = _bare_graph(tmp, enabled=False)
        plain = g.graph
        assert g.begin_checkpoint("AAPL", "2026-05-08", "stock") is None
        assert g.graph is plain  # graph not recompiled
        g.end_checkpoint()  # safe no-op


@pytest.mark.unit
def test_begin_returns_thread_id_and_recompiles():
    with tempfile.TemporaryDirectory() as tmp:
        g = _bare_graph(tmp)
        plain = g.graph
        tid = g.begin_checkpoint("AAPL", "2026-05-08", "stock")
        try:
            assert tid  # a real thread_id
            assert g.graph is not plain  # recompiled with a checkpointer
        finally:
            g.end_checkpoint()
        assert g._checkpointer_ctx is None  # restored


@pytest.mark.unit
def test_checkpoint_input_is_none_only_when_resuming():
    global _should_crash
    with tempfile.TemporaryDirectory() as tmp:
        init = {"count": 0}
        args = ("AAPL", "2026-05-08", "stock")
        # Fresh run: no checkpoint yet -> stream the initial state, then crash.
        g1 = _bare_graph(tmp)
        tid = g1.begin_checkpoint(*args)
        try:
            assert g1._resuming is False
            assert g1.checkpoint_input(init) is init  # not resuming -> initial state
            _should_crash = True
            with pytest.raises(RuntimeError):
                for _ in g1.graph.stream(init, config={"configurable": {"thread_id": tid}}):
                    pass
        finally:
            g1.end_checkpoint()
        assert g1.checkpoint_input(init) is init  # reset after teardown

        # A later run finds the checkpoint -> resume by feeding None, not the
        # initial state (re-passing it would duplicate messages, #1249).
        _should_crash = False
        g2 = _bare_graph(tmp)
        g2.begin_checkpoint(*args)
        try:
            assert g2._resuming is True
            assert g2.checkpoint_input(init) is None
        finally:
            g2.end_checkpoint()


@pytest.mark.unit
def test_cli_style_usage_saves_then_resumes():
    global _should_crash
    with tempfile.TemporaryDirectory() as tmp:
        cfg_args = ("AAPL", "2026-05-08", "stock")

        # Run 1 (the CLI path): begin -> stream self.graph -> crash at 'trader'.
        _should_crash = True
        g1 = _bare_graph(tmp)
        tid = g1.begin_checkpoint(*cfg_args)
        args = {"config": {"configurable": {"thread_id": tid}}}
        try:
            with pytest.raises(RuntimeError):
                for _ in g1.graph.stream({"count": 0}, **args):
                    pass
        finally:
            g1.end_checkpoint()

        # A checkpoint was saved for this run signature (so --checkpoint works).

        sig = g1._run_signature("stock", "2026-05-08")
        assert checkpoint_step(tmp, "AAPL", "2026-05-08", sig) is not None

        # Run 2 (fresh graph, as a new CLI invocation): resume and finish.
        _should_crash = False
        g2 = _bare_graph(tmp)
        tid2 = g2.begin_checkpoint(*cfg_args)
        assert tid2 == tid  # stable id -> same thread resumes
        try:
            result = g2.graph.invoke(None, config={"configurable": {"thread_id": tid2}})
            assert result["count"] == 11  # analyst(+1) resumed into trader(+10)
            g2.clear_checkpoint_on_success(*cfg_args)
        finally:
            g2.end_checkpoint()

        # Cleared on success -> a later run starts fresh.
        assert checkpoint_step(tmp, "AAPL", "2026-05-08", sig) is None


@pytest.mark.unit
def test_behavior_change_does_not_resume_mixed_checkpoint():
    global _should_crash
    with tempfile.TemporaryDirectory() as tmp:
        run = ("AAPL", "2026-05-08", "stock")
        _should_crash = True
        original = _bare_graph(tmp, config={"output_language": "English"})
        original_tid = original.begin_checkpoint(*run)
        try:
            with pytest.raises(RuntimeError):
                original.graph.invoke(
                    {"count": 0}, config={"configurable": {"thread_id": original_tid}}
                )
        finally:
            original.end_checkpoint()
            _should_crash = False

        changed = _bare_graph(tmp, config={"output_language": "Japanese"})
        changed_tid = changed.begin_checkpoint(*run)
        try:
            assert changed_tid != original_tid
            assert changed._resuming is False
            assert changed.checkpoint_input({"count": 0}) == {"count": 0}
        finally:
            changed.end_checkpoint()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "changed"),
    [
        ("output_language", "Japanese"),
        ("llm_provider", "anthropic"),
        ("quick_think_llm", "different-quick-model"),
        ("deep_think_llm", "different-deep-model"),
        ("openai_reasoning_effort", "high"),
        ("temperature", 0.2),
        ("max_tokens", 4096),
        ("llm_max_retries", 8),
        ("max_recur_limit", 250),
        ("news_article_limit", 50),
        ("global_news_article_limit", 25),
        ("global_news_lookback_days", 14),
        ("global_news_queries", ["different macro query"]),
        ("data_vendors", {"news_data": "alpha_vantage"}),
        ("tool_vendors", {"get_news": "alpha_vantage"}),
        ("benchmark_ticker", "QQQ"),
        ("benchmark_map", {"": "QQQ"}),
    ],
)
def test_material_config_changes_checkpoint_identity(key, changed):
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(tmp)
        baseline = graph._run_signature("stock", "2026-05-08")
        graph.config[key] = changed
        assert graph._run_signature("stock", "2026-05-08") != baseline


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "changed"),
    [
        ("results_dir", "/different/results"),
        ("data_cache_dir", "/different/cache"),
        ("memory_log_path", "/different/memory.md"),
        ("project_dir", "/different/project"),
        ("checkpoint_enabled", False),
        ("model_profile", "display-label-only"),
        ("callbacks", ["runtime callback"]),
        ("api_key", "TOP-SECRET-KEY"),
        ("unrelated_future_setting", "ignored"),
    ],
)
def test_nonsemantic_and_secret_config_do_not_change_checkpoint_identity(key, changed):
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(tmp)
        baseline = graph._run_signature("stock", "2026-05-08")
        graph.config[key] = changed
        assert graph._run_signature("stock", "2026-05-08") == baseline


@pytest.mark.unit
def test_endpoint_credentials_are_excluded_but_endpoint_behavior_is_identified():
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(
            tmp,
            config={
                "backend_url": "https://first:secret-one@example.test/v1?api_key=hidden-one&api-version=2026-01-01"
            },
        )
        baseline = graph._run_signature("stock", "2026-05-08")
        assert "secret-one" not in baseline
        assert "hidden-one" not in baseline

        graph.config["backend_url"] = (
            "https://second:secret-two@example.test/v1?api_key=hidden-two&api-version=2026-01-01"
        )
        assert graph._run_signature("stock", "2026-05-08") == baseline

        graph.config["backend_url"] = "https://example.test/v2?api-version=2026-01-01"
        assert graph._run_signature("stock", "2026-05-08") != baseline


@pytest.mark.unit
def test_checkpoint_storage_does_not_persist_raw_config_or_endpoint_secrets():
    global _should_crash
    secret = "CHECKPOINT-SECRET-MUST-NOT-PERSIST"
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(
            tmp,
            config={
                "backend_url": f"https://user:{secret}@example.test/v1?api_key={secret}",
                "api_key": secret,
            },
        )
        _should_crash = True
        tid = graph.begin_checkpoint("AAPL", "2026-05-08", "stock")
        try:
            with pytest.raises(RuntimeError):
                graph.graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid}})
        finally:
            graph.end_checkpoint()
            _should_crash = False

        database = Path(tmp, "checkpoints", "AAPL.db").read_bytes()
        assert secret.encode() not in database


@pytest.mark.unit
def test_mapping_order_does_not_change_checkpoint_identity():
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(
            tmp,
            config={"tool_vendors": {"get_news": "yfinance", "get_stock_data": "alpha_vantage"}},
        )
        baseline = graph._run_signature("stock", "2026-05-08")
        graph.config["tool_vendors"] = {"get_stock_data": "alpha_vantage", "get_news": "yfinance"}
        assert graph._run_signature("stock", "2026-05-08") == baseline


@pytest.mark.unit
def test_local_calendar_change_invalidates_checkpoint_identity():
    if not hasattr(time, "tzset"):
        pytest.skip("system-local timezone override requires tzset")
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(tmp)
        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            utc_signature = graph._run_signature("stock", "2026-05-08")
            os.environ["TZ"] = "America/Los_Angeles"
            time.tzset()
            pacific_signature = graph._run_signature("stock", "2026-05-08")
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()
        assert utc_signature != pacific_signature


@pytest.mark.unit
def test_codex_checkpoint_identity_preserves_bridge_protocol_version():
    with tempfile.TemporaryDirectory() as tmp:
        graph = _bare_graph(tmp, config={"llm_backend": "codex"})
        signature = graph._run_signature("stock", "2026-05-08")
        assert signature.startswith("codex|codex-bridge-v1|checkpoint-config-v2|")
