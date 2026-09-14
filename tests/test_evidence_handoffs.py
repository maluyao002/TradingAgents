"""Contracts for lossless, source-grounded specialist handoffs."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.schemas import PortfolioRating, ResearchPlan
from tradingagents.agents.utils.evidence import (
    HANDOFF_END,
    HANDOFF_START,
    build_packet,
    render_analyst_context,
    render_prepared_evidence,
)


def _prepared(role: str = "market") -> dict:
    return {
        "role": role,
        "analysis_date": "2026-09-12",
        "sources": [
            {
                "id": f"{role}:source:1",
                "label": "Provider snapshot",
                "content": "RAW PROVIDER CONTENT",
                "vendor": "Example Data",
                "retrieved_at": "2026-09-12T18:30:00Z",
                "published_at": "2026-09-11",
                "period": "FY2025",
                "basis": "GAAP",
            }
        ],
        "facts": [
            {
                "id": f"{role}:fact:1",
                "kind": "reported",
                "metric": "diluted EPS",
                "value": 4.2,
                "unit": "USD/share",
                "period": "FY2025",
                "basis": "GAAP",
                "source_id": f"{role}:source:1",
                "published_at": "2026-09-11",
                "retrieved_at": "2026-09-12T18:30:00Z",
                "inputs": [],
                "caveats": ["Restatement risk remains."],
            }
        ],
        "caveats": ["Coverage excludes the latest quarter."],
    }


def _report(role: str = "market", narrative: str | None = None) -> str:
    handoff = {
        "conclusions": [f"{role.title()} reported EPS is positive on the disclosed basis."],
        "caveats": ["Coverage excludes the latest quarter."],
        "conflicts": ["Forward estimates use a different basis."],
        "evidence_ids": [f"{role}:fact:1"],
    }
    block = (
        HANDOFF_START
        + "\n"
        + json.dumps(handoff)
        + "\n"
        + HANDOFF_END
    )
    return f"{narrative}\n\n{block}" if narrative else block


@pytest.mark.unit
def test_valid_handoff_strips_only_machine_block_and_keeps_fact_metadata():
    original = _report()
    packet = build_packet("market", original, _prepared())

    assert packet.compacted is True
    assert packet.report.startswith("## Market Analysis")
    assert "Market reported EPS is positive" in packet.report
    assert HANDOFF_START not in packet.report
    assert packet.original_report == original
    assert packet.facts[0].period == "FY2025"
    assert packet.facts[0].basis == "GAAP"
    assert packet.facts[0].source_id == "market:source:1"
    assert "Coverage excludes the latest quarter." in packet.caveats
    assert packet.to_dict()["facts"][0]["caveats"] == ["Restatement risk remains."]

    rendered = render_analyst_context({"evidence_packets": {"market": packet.to_dict()}}, ["market"])
    assert "Market reported EPS is positive" in rendered
    assert "RAW PROVIDER CONTENT" not in rendered
    assert rendered.count("## Source legend") == 1
    for value in ("FY2025", "GAAP", "Restatement risk remains", "2026-09-11"):
        assert value in rendered


@pytest.mark.unit
def test_sentiment_preserves_only_its_validated_structured_header():
    header = "**Overall Sentiment:** **Mixed** (Score: 5.5/10)\n**Confidence:** Low"
    packet = build_packet("sentiment", f"{header}\n\n{_report('sentiment')}", _prepared("sentiment"))

    assert packet.compacted is True
    assert packet.report_header == header
    assert packet.report.startswith(header + "\n\n## Sentiment Analysis")
    rendered = render_analyst_context(
        {"evidence_packets": {"sentiment": packet.to_dict()}},
        ["sentiment"],
    )
    assert rendered.index(header) < rendered.index("Conclusions:")


@pytest.mark.unit
def test_packet_selects_dependency_closure_and_caveated_facts():
    prepared = _prepared()
    prepared["facts"][0]["caveats"] = []
    prepared["facts"].extend(
        [
            {
                **prepared["facts"][0],
                "id": "market:fact:2",
                "metric": "derived margin",
                "inputs": ["market:fact:1"],
            },
            {
                **prepared["facts"][0],
                "id": "market:fact:3",
                "metric": "caveated estimate",
                "caveats": ["Estimate definition changed."],
            },
            {
                **prepared["facts"][0],
                "id": "market:fact:4",
                "metric": "unreferenced value",
            },
        ]
    )
    payload = {
        "conclusions": ["The derived margin is supported by its disclosed input."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": ["market:fact:2"],
    }
    report = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"

    packet = build_packet("market", report, prepared)
    selected_ids = {fact.id for fact in packet.facts}
    assert selected_ids == {"market:fact:1", "market:fact:2", "market:fact:3"}

    payload["evidence_ids"] = ["market:source:1"]
    source_packet = build_packet(
        "market",
        f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}",
        prepared,
    )
    assert {fact.id for fact in source_packet.facts} == {"market:fact:3"}


@pytest.mark.parametrize("section", ["conclusions", "caveats", "conflicts"])
@pytest.mark.parametrize("include_prepared_on_resume", [False, True])
def test_inline_numeric_citations_preserve_both_periods_and_dependencies(
    section, include_prepared_on_resume,
):
    prepared = _prepared("fundamentals")
    base = {**prepared["facts"][0], "caveats": [], "inputs": []}
    prepared["facts"] = [
        {**base, "id": "fundamentals:fact:prior", "value": 10, "period": "FY2024"},
        {**base, "id": "fundamentals:fact:current", "value": 20},
        {
            **base, "id": "fundamentals:fact:change", "kind": "calculated",
            "metric": "EPS change", "value": 1, "unit": "ratio",
            "inputs": ["fundamentals:fact:prior", "fundamentals:fact:current"],
        },
        {**base, "id": "fundamentals:fact:unrelated", "metric": "inventory", "value": 500},
    ]
    payload = {
        "conclusions": ["Comparison on the disclosed basis."], "caveats": [], "conflicts": [],
        # Simulate the model forgetting the numeric claim IDs in its final index.
        "evidence_ids": ["fundamentals:source:1"],
    }
    claim = (
        "EPS was 20 versus 10 [fundamentals:fact:current, fundamentals:fact:prior]; "
        "growth 100% [fundamentals:fact:change]."
    )
    payload[section] = [claim]
    packet = build_packet(
        "fundamentals", f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}", prepared,
    )
    assert packet.compacted
    assert {f.id for f in packet.facts} == {
        "fundamentals:fact:prior", "fundamentals:fact:current", "fundamentals:fact:change",
    }
    assert "fundamentals:fact:prior" in packet.evidence_ids
    assert claim in packet.report
    serialized = packet.to_dict()
    serialized["evidence_ids"] = ["fundamentals:source:1"]
    state = {"evidence_packets": {"fundamentals": serialized}}
    if include_prepared_on_resume:
        state["prepared_data"] = {"fundamentals": prepared}
    rendered = render_analyst_context(state, ["fundamentals"])
    assert "fundamentals:fact:prior |" in rendered
    assert "fundamentals:fact:current |" in rendered
    assert "fundamentals:fact:change |" in rendered
    assert "fundamentals:fact:unrelated" not in rendered
    assert "RAW PROVIDER CONTENT" not in rendered


@pytest.mark.parametrize("citation", ["market:fact:missing", "market:fact:bad!", "news:fact:1"])
def test_unknown_inline_citation_disables_compaction_including_on_resume(citation):
    prepared = _prepared()
    payload = {
        "conclusions": [f"Reported EPS is 4.2 [{citation}]."],
        "caveats": [], "conflicts": [], "evidence_ids": ["market:source:1"],
    }
    original = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"
    packet = build_packet("market", original, prepared)
    assert not packet.compacted
    assert packet.report == original
    assert any("unknown evidence IDs" in e for e in packet.validation_errors)

    valid = build_packet("market", _report(), prepared).to_dict()
    valid["conclusions"] = payload["conclusions"]
    rendered = render_analyst_context({"evidence_packets": {"market": valid}}, ["market"])
    assert "Complete report (compact handoff unavailable)" in rendered


def test_bracketed_numbers_links_and_uncited_values_do_not_select_facts():
    prepared = _prepared()
    prepared["facts"][0]["caveats"] = []
    payload = {
        "conclusions": ["Value 4.2 [1] [market-summary](https://example.com)."],
        "caveats": [], "conflicts": [], "evidence_ids": ["market:source:1"],
    }
    packet = build_packet(
        "market", f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}", prepared,
    )
    assert packet.compacted
    assert packet.facts == ()
    assert packet.evidence_ids == ("market:source:1",)


@pytest.mark.unit
def test_source_citation_does_not_expand_all_source_facts_even_after_resume():
    prepared = _prepared()
    template = {**prepared["facts"][0], "caveats": [], "inputs": []}
    prepared["facts"] = [
        {
            **template,
            "id": f"market:fact:{index:03d}",
            "metric": f"metric_{index:03d}",
            "value": index,
        }
        for index in range(318)
    ]
    payload = {
        "conclusions": ["The provider narrative supplies contextual provenance only."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": ["market:source:1"],
    }
    report = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"

    packet = build_packet("market", report, prepared)
    assert packet.compacted is True
    assert packet.sources[0].id == "market:source:1"
    assert packet.facts == ()

    resumed = render_analyst_context(
        {
            "market_report": packet.report,
            "prepared_data": {"market": prepared},
            "evidence_packets": {"market": packet.to_dict()},
        },
        ["market"],
    )
    assert "market:source:1" in resumed
    assert "Factual records:" not in resumed
    assert "metric_317" not in resumed


@pytest.mark.unit
def test_required_fact_and_transitive_dependency_survive_initial_and_resumed_packets():
    prepared = _prepared()
    base = {**prepared["facts"][0], "caveats": [], "inputs": []}
    prepared["facts"] = [
        {**base, "id": "market:fact:base", "metric": "revenue"},
        {
            **base,
            "id": "market:fact:required",
            "metric": "revenue_growth",
            "kind": "calculated",
            "inputs": ["market:fact:base"],
        },
        {**base, "id": "market:fact:unselected", "metric": "immaterial_detail"},
    ]
    prepared["required_evidence_ids"] = ["market:fact:required"]
    payload = {
        "conclusions": ["The source narrative provides context for the comparison."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": ["market:source:1"],
    }
    report = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"

    packet = build_packet("market", report, prepared)
    assert packet.required_evidence_ids == ("market:fact:required",)
    assert {fact.id for fact in packet.facts} == {
        "market:fact:base",
        "market:fact:required",
    }

    resumed = render_analyst_context(
        {
            "market_report": packet.report,
            "prepared_data": {"market": prepared},
            "evidence_packets": {"market": packet.to_dict()},
        },
        ["market"],
    )
    assert "market:fact:base" in resumed
    assert "market:fact:required" in resumed
    assert "market:fact:unselected" not in resumed


@pytest.mark.unit
@pytest.mark.parametrize(
    "required_ids",
    ["market:fact:1", ["bad required id!"], ["market:missing"], ["market:source:1"]],
)
def test_malformed_or_non_fact_required_ids_force_safe_full_report_fallback(required_ids):
    prepared = _prepared()
    prepared["required_evidence_ids"] = required_ids
    original = _report()

    packet = build_packet("market", original, prepared)
    assert packet.compacted is False
    assert packet.report == original
    assert packet.validation_errors


@pytest.mark.unit
def test_stale_serialized_required_ids_force_resume_fallback():
    prepared = _prepared()
    prepared["required_evidence_ids"] = ["market:fact:1"]
    packet = build_packet("market", _report(), prepared)
    serialized = packet.to_dict()
    serialized["required_evidence_ids"] = ["market:missing"]

    resumed = render_analyst_context(
        {
            "market_report": packet.report,
            "prepared_data": {"market": prepared},
            "evidence_packets": {"market": serialized},
        },
        ["market"],
    )
    assert "Complete report (compact handoff unavailable)" in resumed
    assert packet.report in resumed


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_report",
    [
        "Unchanged report with no handoff.",
        f"Report\n{HANDOFF_START}\nnot-json\n{HANDOFF_END}",
        _report().replace("market:fact:1", "market:unknown:9"),
        _report(narrative="Substantive prose outside the handoff."),
    ],
)
def test_invalid_or_unknown_handoff_falls_back_to_complete_original(bad_report):
    packet = build_packet("market", bad_report, _prepared())

    assert packet.compacted is False
    assert packet.report == bad_report
    assert packet.original_report == bad_report
    assert packet.validation_errors


@pytest.mark.unit
def test_invalid_fact_keeps_raw_source_and_missing_metadata_explicit():
    prepared = _prepared()
    prepared["sources"][0].pop("published_at")
    prepared["sources"][0].pop("basis")
    prepared["facts"][0]["source_id"] = "market:missing"

    rendered = render_prepared_evidence(prepared)
    packet = build_packet("market", _report(), prepared)

    assert "RAW PROVIDER CONTENT" in rendered
    assert "published_at: unknown" in rendered
    assert "basis: unknown" in rendered
    assert "Coverage excludes the latest quarter." in rendered
    assert "Unvalidated raw fact payloads" in rendered
    assert packet.compacted is False
    assert packet.facts == ()


@pytest.mark.unit
def test_future_publication_is_excluded_from_specialist_and_packet():
    prepared = _prepared()
    prepared["sources"][0]["published_at"] = "2026-09-13"
    prepared["facts"][0]["value"] = "FUTURE FACT VALUE"

    rendered = render_prepared_evidence(prepared)
    packet = build_packet("market", _report(), prepared)

    assert "RAW PROVIDER CONTENT" not in rendered
    assert "after analysis date and was excluded" in rendered
    assert "FUTURE FACT VALUE" not in rendered
    assert packet.sources == ()
    assert packet.facts == ()
    assert packet.compacted is False


@pytest.mark.unit
def test_explicit_no_data_handoff_renders_readable_report_and_revalidates():
    unavailable = (
        "Point-in-time fundamentals are unavailable for 2026-09-12; no raw or derived "
        "values were exposed."
    )
    prepared = {
        "role": "fundamentals",
        "analysis_date": "2026-09-12",
        "sources": [],
        "facts": [],
        "caveats": [unavailable],
    }
    payload = {
        "conclusions": ["No point-in-time fundamental conclusion can be supported."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": [],
    }
    original = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"

    packet = build_packet("fundamentals", original, prepared)
    assert packet.compacted is True
    assert HANDOFF_START not in packet.report
    assert "## Fundamentals Analysis" in packet.report
    assert unavailable in packet.report

    rendered = render_analyst_context(
        {
            "fundamentals_report": packet.report,
            "prepared_data": {"fundamentals": prepared},
            "evidence_packets": {"fundamentals": packet.to_dict()},
        },
        ["fundamentals"],
    )
    assert "No point-in-time fundamental conclusion" in rendered
    assert unavailable in rendered
    assert HANDOFF_START not in rendered


@pytest.mark.unit
def test_empty_evidence_ids_without_explicit_unavailability_still_fall_back():
    prepared = {
        "role": "fundamentals",
        "analysis_date": "2026-09-12",
        "sources": [],
        "facts": [],
        "caveats": ["Coverage may be incomplete."],
    }
    payload = {
        "conclusions": ["A conclusion was offered without evidence."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": [],
    }
    original = f"{HANDOFF_START}\n{json.dumps(payload)}\n{HANDOFF_END}"

    packet = build_packet("fundamentals", original, prepared)
    assert packet.compacted is False
    assert packet.report == original


@pytest.mark.unit
def test_stale_serialized_compact_packet_is_revalidated_before_rendering():
    packet = build_packet("market", _report(), _prepared()).to_dict()
    full_report = packet["report"]
    packet["evidence_ids"] = ["market:unknown:9"]

    rendered = render_analyst_context(
        {
            "market_report": full_report,
            "evidence_packets": {"market": packet},
        },
        ["market"],
    )

    assert "Complete report (compact handoff unavailable)" in rendered
    assert "## Market Analysis" in rendered
    assert "Conclusions:\n- Market reported EPS is positive" not in rendered


def _capturing_research_llm():
    captured = {}
    structured = MagicMock()

    def capture(prompt):
        captured["prompt"] = prompt
        return ResearchPlan(
            recommendation=PortfolioRating.HOLD,
            rationale="Evidence remains mixed.",
            strategic_actions="Refresh the inputs.",
        )

    structured.invoke.side_effect = capture
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm, captured


@pytest.mark.unit
def test_research_manager_uses_all_compact_packets_without_full_narrative_duplication():
    roles = ("market", "sentiment", "news", "fundamentals")
    packets = {}
    state = {
        "company_of_interest": "NVDA",
        "trade_date": "2026-09-12",
        "investment_debate_state": {
            "history": "Bull and bear disagree.",
            "bull_history": "bull",
            "bear_history": "bear",
            "current_response": "",
            "judge_decision": "",
            "count": 2,
        },
    }
    full_narratives = []
    for role in roles:
        prepared = _prepared(role)
        prepared["sources"][0]["content"] = f"RAW {role.upper()} CONTENT " + role * 5000
        full_narratives.append(prepared["sources"][0]["content"])
        packet = build_packet(role, _report(role), prepared)
        packets[role] = packet.to_dict()
        state[f"{role}_report"] = packet.report
    state["evidence_packets"] = packets

    llm, captured = _capturing_research_llm()
    create_research_manager(llm)(state)
    prompt = captured["prompt"]

    assert len(prompt) < sum(len(item) for item in full_narratives)
    assert "RAW MARKET CONTENT" not in prompt
    assert prompt.count("## Source legend") == 1
    for role in roles:
        assert f"{role}:fact:1" in prompt
        assert f"{role.title()} reported EPS is positive" in prompt
        assert full_narratives[roles.index(role)] not in prompt
