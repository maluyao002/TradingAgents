"""Offline contract checks for research acceptance, with real evidence validation."""

import copy
import json
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.utils.evidence import HANDOFF_END, HANDOFF_START, build_packet
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree
from tradingagents.research_quality import assess_research_quality, finalize_research_quality


def complete_state(roles=("market",)):
    state = {
        "_selected_analysts": list(roles),
        "_research_backend": "api",
        "prepared_data": {},
        "evidence_packets": {},
        "investment_plan": "Research plan",
        "trader_investment_plan": "Proposed trade",
        "investment_debate_state": {
            "bull_history": "Bull",
            "bear_history": "Bear",
            "history": "Discussion",
            "judge_decision": "Research plan",
        },
        "risk_debate_state": {
            "aggressive_history": "Aggressive",
            "conservative_history": "Conservative",
            "neutral_history": "Neutral",
            "history": "Risk discussion",
            "judge_decision": "**Rating**: Hold\nWait for verified catalysts.",
        },
        "final_trade_decision": "**Rating**: Hold\nWait for verified catalysts.",
    }
    for role in roles:
        source_ids = {
            "news": ["news-company-baseline", "news-global-baseline"],
            "sentiment": ["sentiment-news", "sentiment-stocktwits", "sentiment-reddit"],
        }.get(role, [f"{role}:source:1"])
        prepared = {
            "analysis_date": "2026-09-14",
            "sources": [
                {"id": source_id, "content": "Observed provider evidence."}
                for source_id in source_ids
            ],
            "facts": [],
            "caveats": [],
        }
        ids = list(source_ids)
        if role in {"market", "fundamentals"}:
            prepared["facts"] = [
                {
                    "id": f"{role}:fact:1",
                    "metric": "Reported value",
                    "value": 10,
                    "source_id": source_ids[0],
                    "unit": "USD",
                    "kind": "reported",
                }
            ]
            ids.append(f"{role}:fact:1")
        payload = {
            "conclusions": ["Evidence considered " + " ".join(f"[{i}]" for i in ids)],
            "caveats": [],
            "conflicts": [],
            "evidence_ids": ids,
        }
        packet = build_packet(
            role, f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}", prepared
        )
        assert packet.compacted, packet.validation_errors
        state["prepared_data"][role] = prepared
        state["evidence_packets"][role] = packet.to_dict()
        state[f"{role}_report"] = packet.report
    return state


@pytest.mark.parametrize("backend", ["api", "codex"])
def test_complete_pipeline_accepted_with_same_models_and_no_inference(backend):
    state = complete_state(("market", "sentiment", "news", "fundamentals"))
    quality = finalize_research_quality(
        state, ["market", "social", "news", "fundamentals"], backend
    )
    assert quality["accepted"] is True
    assert quality["signal"] == "Hold"
    assert quality["reasons"] == []
    assert state["research_quality"] == quality


@pytest.mark.parametrize(
    "missing",
    [
        "market_report",
        "investment_plan",
        "investment_debate_state",
        "trader_investment_plan",
        "risk_debate_state",
        "final_trade_decision",
    ],
)
def test_missing_downstream_work_is_degraded(missing):
    state = complete_state()
    del state[missing]
    result = assess_research_quality(state)
    assert result["status"] == "degraded"
    assert result["signal"] == "REVIEW"


@pytest.mark.parametrize(
    "decision", ["Do not buy; decision unavailable", "Rating: Buy\nRating: Sell", "", "No decision"]
)
def test_rating_requires_explicit_unambiguous_label(decision):
    state = complete_state()
    state["final_trade_decision"] = decision
    assert assess_research_quality(state)["signal"] == "REVIEW"


@pytest.mark.parametrize("decision", ["Rating: Hold", "**Rating:** Hold", "**Rating**: **Hold**", "Rating：Hold"])
def test_explicit_rating_supports_rendered_markdown(decision):
    state = complete_state()
    state["final_trade_decision"] = decision
    state["risk_debate_state"]["judge_decision"] = decision
    assert assess_research_quality(state)["signal"] == "Hold"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "<stocktwits unavailable: TimeoutError>",
        "<Reddit unavailable: every source failed>",
        "News unavailable",
        "Error: provider failed",
    ],
)
def test_unavailable_required_evidence_cannot_be_accepted(content):
    state = complete_state(("sentiment",))
    state["prepared_data"]["sentiment"]["sources"][0]["content"] = content
    result = assess_research_quality(state)
    assert result["accepted"] is False
    code = "source_unavailable" if content else "invalid_evidence"
    assert {"role": "sentiment", "code": code} in result["reasons"]


@pytest.mark.parametrize(
    "debate,field",
    [
        ("investment_debate_state", "bull_history"),
        ("investment_debate_state", "bear_history"),
        ("investment_debate_state", "judge_decision"),
        ("risk_debate_state", "aggressive_history"),
        ("risk_debate_state", "conservative_history"),
        ("risk_debate_state", "neutral_history"),
        ("risk_debate_state", "judge_decision"),
    ],
)
def test_partial_debate_cannot_be_accepted(debate, field):
    state = complete_state()
    state[debate][field] = ""
    assert assess_research_quality(state)["accepted"] is False


@pytest.mark.parametrize(
    ("target", "replacement", "code"),
    [
        ("market_report", "Substituted analyst report", "analyst_report_mismatch"),
        ("investment_plan", "Substituted research plan", "investment_plan_mismatch"),
        (
            "final_trade_decision",
            "**Rating**: Buy\nSubstituted decision",
            "final_decision_mismatch",
        ),
    ],
)
def test_mismatched_stage_copies_cannot_be_accepted(target, replacement, code):
    state = complete_state()
    state[target] = replacement
    result = assess_research_quality(state)
    assert result["accepted"] is False
    assert {"code": code} in result["reasons"] or {
        "role": "market",
        "code": code,
    } in result["reasons"]


def test_normalized_equivalent_stage_copies_remain_accepted():
    state = complete_state()
    state["market_report"] = "  " + state["market_report"].replace("\n", "\r\n") + "\r\n"
    state["investment_plan"] = "  Research plan\r\n"
    state["final_trade_decision"] = state["final_trade_decision"].replace("\n", "\r\n")
    assert assess_research_quality(state)["accepted"] is True


@pytest.mark.parametrize("mutation", ["packet", "facts", "sources", "ids", "malformed"])
def test_corrupt_evidence_cannot_be_bypassed_by_saved_accepted_flag(mutation):
    state = complete_state()
    state["research_quality"] = {"accepted": True, "signal": "Buy"}
    if mutation == "packet":
        state["evidence_packets"]["market"]["compacted"] = False
    elif mutation == "facts":
        state["prepared_data"]["market"]["facts"] = []
    elif mutation == "sources":
        state["prepared_data"]["market"]["sources"] = []
    elif mutation == "ids":
        state["evidence_packets"]["market"]["evidence_ids"] = ["market:invented"]
    else:
        state["prepared_data"]["market"]["sources"] = None
    assert assess_research_quality(state)["accepted"] is False


def test_codex_requires_global_baseline_but_api_keeps_company_baseline_contract():
    state = complete_state(("news",))
    prepared = state["prepared_data"]["news"]
    prepared["sources"] = prepared["sources"][:1]
    payload = {
        "conclusions": ["Coverage assessed [news-company-baseline]."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": ["news-company-baseline"],
    }
    packet = build_packet(
        "news", f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}", prepared
    )
    assert packet.compacted, packet.validation_errors
    state["evidence_packets"]["news"] = packet.to_dict()
    state["news_report"] = packet.report
    assert assess_research_quality(state, backend="api")["accepted"] is True
    assert assess_research_quality(state, backend="codex")["signal"] == "REVIEW"


def test_selection_is_required_and_unselected_roles_do_not_block():
    state = complete_state()
    assert assess_research_quality(state)["accepted"] is True
    assert assess_research_quality(state, ["bogus"])["accepted"] is False
    assert assess_research_quality(state, [])["accepted"] is False
    del state["_selected_analysts"]
    assert assess_research_quality(state)["accepted"] is False


def test_export_config_cannot_narrow_finalized_scope_or_switch_backend(tmp_path):
    state = complete_state(("market", "news"))
    state["evidence_packets"]["news"]["compacted"] = False
    state["_research_backend"] = "codex"
    write_report_tree(state, "AAPL", tmp_path, {
        "selected_analysts": ["market"], "llm_backend": "api",
    })
    quality = json.loads((tmp_path / "run_metadata.json").read_text())["research_quality"]
    assert quality["accepted"] is False
    assert quality["selected_analysts"] == ["market", "news"]
    assert {"code": "analyst_scope_mismatch"} in quality["reasons"]
    assert {"code": "backend_scope_mismatch"} in quality["reasons"]
    assert {"role": "news", "code": "invalid_evidence"} in quality["reasons"]


@pytest.mark.parametrize("accepted", [True, False])
def test_programmatic_gate_controls_signal_and_memory_while_preserving_diagnostics(accepted):
    state = complete_state()
    if not accepted:
        state["evidence_packets"] = {}
    graph = MagicMock()
    graph.selected_analysts = ["market"]
    graph.config = {"llm_backend": "api"}
    graph.debug = False
    graph.graph.invoke.return_value = state
    graph.propagator.get_graph_args.return_value = {}
    result, signal = TradingAgentsGraph._run_graph(graph, "AAPL", "2026-09-14")
    assert signal == ("Hold" if accepted else "REVIEW")
    assert result["final_trade_decision"] == state["final_trade_decision"]
    assert graph.memory_log.store_decision.call_count == int(accepted)
    graph._log_state.assert_called_once()


@pytest.mark.parametrize("accepted", [True, False])
def test_report_and_metadata_recompute_gate_without_mutating_diagnostics(tmp_path, accepted):
    state = complete_state()
    if not accepted:
        state["evidence_packets"] = {}
    original = copy.deepcopy(state)
    path = write_report_tree(state, "AAPL", tmp_path)
    metadata = json.loads((tmp_path / "run_metadata.json").read_text())
    quality = metadata["research_quality"]
    assert quality["accepted"] is accepted
    assert ("ACCEPTED" if accepted else "DEGRADED") in path.read_text()
    assert state == original


@pytest.mark.parametrize("role,source_index,content", [
    ("news", 1, "No global news found for 2026-09-14"),
    ("news", 1, "No global news found between 2026-09-07 and 2026-09-14"),
    ("news", 0, "Error fetching news for AAPL: provider failed"),
    ("news", 1, "Error fetching global news: provider failed"),
    ("sentiment", 1, "<no StockTwits messages found for $AAPL>"),
    ("sentiment", 1, "<no StockTwits messages for $AAPL within 2026-09-07..2026-09-14 (public stream serves only recent messages)>"),
    ("sentiment", 2, "<no Reddit posts found mentioning AAPL across r/stocks in the past 7 days>"),
])
def test_production_no_data_sentinels_withhold_signal_and_memory(role, source_index, content):
    state = complete_state((role,))
    state["prepared_data"][role]["sources"][source_index]["content"] = content
    graph = MagicMock()
    graph.selected_analysts = [role]
    graph.config = {"llm_backend": "api"}
    graph.debug = False
    graph.graph.invoke.return_value = state
    graph.propagator.get_graph_args.return_value = {}
    result, signal = TradingAgentsGraph._run_graph(graph, "AAPL", "2026-09-14")
    assert signal == "REVIEW"
    assert {"role": role, "code": "source_unavailable"} in result["research_quality"]["reasons"]
    graph.memory_log.store_decision.assert_not_called()


def test_provider_error_words_inside_usable_content_are_not_a_no_data_sentinel():
    state = complete_state(("news",))
    state["prepared_data"]["news"]["sources"][0]["content"] = (
        "Company earnings update. A historical service incident showed Error fetching news."
    )
    assert assess_research_quality(state)["accepted"] is True


@pytest.mark.parametrize("structured_failure", ["unavailable", "parse"])
def test_localized_portfolio_fallback_preserves_machine_rating(monkeypatch, structured_failure):
    from tradingagents.agents.managers import portfolio_manager

    monkeypatch.setattr(portfolio_manager, "get_language_instruction", lambda: "Write the entire response in Chinese.")
    llm = MagicMock()
    if structured_failure == "unavailable":
        llm.with_structured_output.side_effect = NotImplementedError("unsupported")
    else:
        llm.with_structured_output.return_value.invoke.side_effect = ValueError("invalid JSON")
    llm.invoke.return_value.content = "Rating: Hold\n评级：持有。证据尚不足以改变仓位。"
    state = complete_state()
    state.update(company_of_interest="AAPL", trade_date="2026-09-14")
    state["risk_debate_state"].update(
        current_aggressive_response="", current_conservative_response="",
        current_neutral_response="", count=3,
    )
    state.update(portfolio_manager.create_portfolio_manager(llm)(state))
    prompt = llm.invoke.call_args.args[0]
    assert prompt.index("Machine-readable rating exception") > prompt.index("Write the entire response in Chinese.")
    assert "Never translate" in prompt
    assert "Rating: <value>" in prompt
    assert assess_research_quality(state)["signal"] == "Hold"
    assert "评级：持有" in state["final_trade_decision"]
    assert llm.invoke.call_count == 1
