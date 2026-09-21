from hashlib import sha256

import pytest

from tradingagents.research.case_report import (
    CASE_READER_REQUIREMENTS,
    SECTION_PURPOSES,
    CaseReportDraft,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    ReviewFinding,
    SourceDocument,
)
from tradingagents.research.reader import ReaderIssue, render_reader
from tradingagents.research.stages import ReportDraft


def _request(tmp_path, language="English"):
    return ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="api",
        output_dir=tmp_path / "out",
        report_language=language,
    )


def _source(identifier="filing"):
    content = f"Synthetic source {identifier}".encode()
    return SourceDocument(
        id=identifier,
        url=f"https://example.test/{identifier}",
        title=f"Synthetic {identifier}",
        publisher="Fixture publisher",
        retrieved_at="2026-09-16T01:00:00Z",
        published_at="2026-09-16T00:00:00Z",
        content=content.decode(),
        content_sha256=sha256(content).hexdigest(),
    )


def _draft(limitations=(), evidence_ids=()):
    return ReportDraft(
        sections=(
            {
                "title": "Executive conclusion",
                "text": "Evidence-backed conclusion. [filing]",
                "evidence_ids": evidence_ids,
            },
        ),
        limitations=limitations,
        investment_view="unrated",
    )


def test_raw_gaps_are_lossless_in_audit_but_do_not_flood_reader(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    raw_gaps = [f"Research caveat {index}" for index in range(175)]
    raw_gaps.extend(
        [
            "workflow metadata: usage unknown",
            "reviewer instruction: inspect this raw log",
            "Research caveat 9",
        ]
    )
    draft = _draft(
        limitations=(
            "No company-specific valuation is supportable.",
            "No company-specific valuation is supportable.",
            "Customer evidence remains incomplete.",
        ),
        evidence_ids=("filing",),
    )

    rendered = render_reader(request, draft, snapshot, raw_gaps)
    audit = rendered.limitations_audit

    assert "Research caveat 9" not in rendered.reader_text
    assert "workflow metadata" not in rendered.reader_text
    assert "reviewer instruction" not in rendered.reader_text
    assert rendered.reader_text.count("No company-specific valuation is supportable.") == 1
    assert "Customer evidence remains incomplete." in rendered.reader_text
    assert "[reader_limitations.json](reader_limitations.json)" in rendered.reader_text

    occurrences = audit["unresolved_issues"]["occurrences"]
    assert [item["original_text"] for item in occurrences] == raw_gaps
    assert len({item["occurrence_id"] for item in occurrences}) == len(raw_gaps)
    assert all(item["provenance_id"] for item in occurrences)
    assert len(audit["unresolved_issues"]["consolidated_exact_text"]) == len(raw_gaps) - 1
    assert [
        item["original_text"] for item in audit["draft_limitations"]["occurrences"]
    ] == list(draft.limitations)


def test_draft_limitations_have_no_display_cap_and_only_exact_duplicates_merge(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    limitations = tuple(f"Material limitation {index}" for index in range(33)) + (
        "Margin evidence is incomplete.",
        "margin evidence is incomplete.",
        "Material limitation 8",
    )

    rendered = render_reader(
        request, _draft(limitations=limitations, evidence_ids=("filing",)), snapshot, ()
    )

    assert all(f"Material limitation {index}" in rendered.reader_text for index in range(33))
    assert rendered.reader_text.count("Material limitation 8") == 1
    assert "Margin evidence is incomplete." in rendered.reader_text
    assert "margin evidence is incomplete." in rendered.reader_text
    assert len(rendered.limitations_audit["draft_limitations"]["occurrences"]) == 36


def test_missing_draft_is_an_obvious_diagnostic_not_a_final_report(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff)

    rendered = render_reader(request, None, snapshot, ("Evidence collection failed",))

    assert "Diagnostic only — no reader draft" in rendered.reader_text
    assert "not a final research report" in rendered.reader_text
    assert "Needs review / Unrated" in rendered.reader_text
    assert "Evidence collection failed" not in rendered.reader_text
    assert rendered.limitations_audit["draft_limitations"]["draft_available"] is False
    assert rendered.limitations_audit["unresolved_issues"]["occurrences"][0][
        "original_text"
    ] == "Evidence collection failed"


def test_source_footnotes_and_section_evidence_have_stable_machine_mapping(tmp_path):
    request = _request(tmp_path)
    sources = (_source("filing"), _source("competitor"))
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=sources)
    draft = _draft(limitations=("Scope is limited.",), evidence_ids=("filing",))

    rendered = render_reader(request, draft, snapshot, ())

    assert "Evidence-backed conclusion. [^1]" in rendered.reader_text
    assert "[^1]: [Synthetic filing](<https://example.test/filing>)" in rendered.reader_text
    assert "[^2]: [Synthetic competitor](<https://example.test/competitor>)" in rendered.reader_text
    mapping = rendered.limitations_audit["source_footnotes"]
    assert [(item["source_id"], item["footnote_number"]) for item in mapping] == [
        ("filing", 1),
        ("competitor", 2),
    ]
    assert mapping[0]["referenced_by_section_indexes"] == [1]
    assert rendered.limitations_audit["section_evidence"] == [
        {
            "section_index": 1,
            "section_title": "Executive conclusion",
            "evidence_id": "filing",
            "source_ids": ["filing"],
            "source_footnote_numbers": [1],
        }
    ]


def test_explicit_financial_fact_reference_resolves_through_source_ancestry(tmp_path):
    request = _request(tmp_path)
    source = _source("filing")
    fact = FinancialFact(
        id="anchor-revenue",
        source_id="filing",
        metric="revenue",
        value=1,
        unit="USD",
        currency="USD",
        period_end="2026-06-30",
        basis="GAAP",
        location="synthetic",
    )
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff, sources=(source,), facts=(fact,)
    )
    draft = _draft(limitations=("Scope is limited.",), evidence_ids=("anchor-revenue",))
    draft = draft.model_copy(
        update={"sections": (draft.sections[0].model_copy(update={"text": "Revenue anchor. [anchor-revenue]"}),)}
    )

    rendered = render_reader(request, draft, snapshot, ())

    assert "Revenue anchor. [^1]" in rendered.reader_text
    assert "[anchor-revenue]" not in rendered.reader_text


def test_structured_critical_blockers_are_not_hidden_and_operational_logs_stay_out(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    research_blocker = ReviewFinding(
        code="contradicted-margin",
        severity="critical",
        message="The filing contradicts the reported margin claim.",
        category="research",
        affected_ids=("margin-claim",),
    )
    operational_blocker = ReaderIssue(
        message="provider trace SECRET-raw-log; cumulative usage unknown",
        provenance_id="recovery:usage",
        severity="critical",
        category="operational",
        code="usage-unknown",
    )

    rendered = render_reader(
        request,
        _draft(limitations=("Valuation is unavailable.",), evidence_ids=("filing",)),
        snapshot,
        (research_blocker, operational_blocker),
    )

    assert "The filing contradicts the reported margin claim." in rendered.reader_text
    assert "A critical operational issue affected research completeness" in rendered.reader_text
    assert "SECRET-raw-log" not in rendered.reader_text
    assert "audit issue `" not in rendered.reader_text
    issues = rendered.limitations_audit["unresolved_issues"]
    assert [item["original_text"] for item in issues["occurrences"]] == [
        research_blocker.message,
        operational_blocker.message,
    ]
    assert issues["occurrences"][0]["affected_ids"] == ["margin-claim"]
    assert all(item["displayed_in_reader"] for item in issues["consolidated_exact_text"])
    assert issues["unrepresented_issue_ids"] == []


def test_ineligible_or_invented_section_evidence_is_rejected(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    draft = _draft(limitations=("Scope is limited.",), evidence_ids=("invented",))

    with pytest.raises(ValueError, match="ineligible evidence"):
        render_reader(request, draft, snapshot, ())


def test_chinese_rendering_remains_supported_without_changing_audit_semantics(tmp_path):
    request = _request(tmp_path, "Chinese")
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    rendered = render_reader(
        request,
        _draft(limitations=("证据覆盖有限。",), evidence_ids=("filing",)),
        snapshot,
        ("原始缺口",),
    )

    assert "深度研究报告" in rendered.reader_text
    assert "证据覆盖有限。" in rendered.reader_text
    assert "原始缺口" not in rendered.reader_text
    assert rendered.limitations_audit["language"] == "Chinese"


def _case_draft():
    return CaseReportDraft(
        sections=tuple(
            {
                "purpose": purpose,
                "title": purpose.replace("_", " ").title(),
                "text": (
                    "Material uncertainty remains. Draft caveat. Funding gap. [filing]"
                    if purpose == "material_gaps"
                    else "Concise case analysis."
                ),
                "evidence_ids": ("filing", "competitor") if purpose == "material_gaps" else (),
            }
            for purpose in SECTION_PURPOSES
        ),
        limitations=("Draft caveat.",),
        investment_view="unrated",
    )


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("authored", ["[^1]", "[^999]", "[^1]: fabricated source", "[filing] [^1]"])
def test_reader_rejects_authored_numeric_footnotes(tmp_path, compact, authored):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff,
                                sources=(_source(), _source("competitor")))
    draft = _case_draft()
    draft = draft.model_copy(update={"sections": (
        draft.sections[0].model_copy(update={"text": "Unsupported attribution. " + authored}),
        *draft.sections[1:],
    )})
    with pytest.raises(ValueError, match="authored numeric footnotes"):
        render_reader(request, draft, snapshot, (), compact=compact)


def test_displayed_limitations_escape_source_ordinals_without_rewriting_audit(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    caveat = "Unverified source attribution [^1]"
    rendered = render_reader(request, _draft(limitations=(caveat,)), snapshot, (
        ReaderIssue(message=caveat, provenance_id="numeric-test", severity="critical", category="numerical"),
    ))
    assert "- Unverified source attribution \\[^1\\]" in rendered.reader_text
    assert "- " + caveat not in rendered.reader_text
    assert rendered.limitations_audit["draft_limitations"]["occurrences"][0]["original_text"] == caveat
    assert rendered.limitations_audit["unresolved_issues"]["occurrences"][0]["original_text"] == caveat


def test_compact_reader_is_an_unverified_case_candidate_with_only_explicit_footnotes(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff, sources=(_source("filing"), _source("competitor"))
    )
    draft = _case_draft()
    compact = render_reader(request, draft, snapshot, ("Funding gap.",), compact=True)

    assert compact.limitations_audit["reader_compaction"]["active"] is True
    assert compact.limitations_audit["reader_compaction"]["accepted"] is False
    assert (
        compact.limitations_audit["reader_compaction"]
        ["presentation_only_requires_exact_reader_verification"]
        is True
    )
    assert "## Material limitations" not in compact.reader_text
    assert "Funding gap." in compact.reader_text and "Draft caveat." in compact.reader_text
    assert "audit issue `" not in compact.reader_text
    assert "[^1]: [Synthetic filing]" in compact.reader_text
    assert "Section sources (not paragraph-level support): [^2]" in compact.reader_text
    assert "[^2]: [Synthetic competitor]" in compact.reader_text
    assert compact.limitations_audit["citation_scope"] == "mixed"
    assert compact.limitations_audit["source_footnotes"][1]["footnote_number"] == 2
    assert compact.limitations_audit["paragraph_citations"] == [
        {
            "section_index": 8,
            "paragraph_index": 1,
            "source_ids": ["filing"],
            "source_footnote_numbers": [1],
            "citation_scope": "paragraph",
        }
    ]
    assert compact.limitations_audit["section_citations"] == [
        {
            "section_index": 8,
            "citation_scope": "section_only",
            "source_ids": ["competitor"],
            "source_footnote_numbers": [2],
        }
    ]


def test_compact_reader_labels_legacy_section_only_sources_and_flags_uncited_paragraphs(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff, sources=(_source("filing"), _source("competitor"))
    )
    draft = _case_draft()
    material_gaps = draft.sections[-1].model_copy(
        update={"text": "Legacy evidence is declared at the section level only."}
    )
    draft = draft.model_copy(update={"sections": (*draft.sections[:-1], material_gaps)})

    rendered = render_reader(request, draft, snapshot, (), compact=True)

    assert "Section sources (not paragraph-level support): [^1][^2]" in rendered.reader_text
    assert rendered.limitations_audit["citation_scope"] == "section_only"
    assert rendered.limitations_audit["paragraph_citations"] == [
        {
            "section_index": 8,
            "paragraph_index": 1,
            "source_ids": [],
            "source_footnote_numbers": [],
            "citation_scope": "missing_explicit",
        }
    ]
    assert rendered.limitations_audit["section_citations"] == [
        {
            "section_index": 8,
            "citation_scope": "section_only",
            "source_ids": ["filing", "competitor"],
            "source_footnote_numbers": [1, 2],
        }
    ]


def test_repaired_reader_collapses_repeated_sources_per_paragraph_and_table_row(tmp_path):
    request = _request(tmp_path)
    source = _source("filing")
    reported = FinancialFact(
        id="reported-revenue", source_id="filing", metric="revenue", value=1,
        unit="USD", currency="USD", period_end="2026-06-30", basis="GAAP",
        location="synthetic",
    )
    derived = FinancialFact(
        id="derived-revenue", source_id="filing", metric="revenue", value=1,
        unit="USD", currency="USD", period_end="2026-06-30", basis="GAAP",
        location="synthetic", inputs=("reported-revenue",), formula="reported-revenue",
    )
    independent = FinancialFact(
        id="independent-revenue", source_id="competitor", metric="revenue", value=1,
        unit="USD", currency="USD", period_end="2026-06-30", basis="GAAP",
        location="synthetic",
    )
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff,
        sources=(source, _source("competitor")), facts=(reported, derived, independent),
    )
    draft = _case_draft()
    repaired_material_gaps = draft.sections[-1].model_copy(update={
        "text": (
            "| Gap | Evidence |\n| --- | --- |\n"
            "| Funding visibility | [reported-revenue] [derived-revenue] [independent-revenue] |\n\n"
            "Fact A is supported by both evidence records [reported-revenue] [derived-revenue]. "
            "Different claim retains local support [reported-revenue].\n\n"
            "A separate paragraph retains its own local support. [reported-revenue]"
        ),
        "evidence_ids": ("reported-revenue", "derived-revenue", "independent-revenue"),
    })
    draft = draft.model_copy(update={"sections": (*draft.sections[:-1], repaired_material_gaps)})

    rendered = render_reader(request, draft, snapshot, (), compact=True)

    assert "| Funding visibility | [^1][^2] |" in rendered.reader_text
    assert "Fact A is supported by both evidence records [^1]. Different claim retains local support [^1]." in rendered.reader_text
    assert rendered.reader_text.count("[^1]") == 5  # four reader placements plus one definition
    assert rendered.reader_text.count("[^2]") == 2  # table placement plus one definition
    assert [item["evidence_id"] for item in rendered.limitations_audit["section_evidence"][-3:]] == [
        "reported-revenue", "derived-revenue", "independent-revenue"
    ]
    assert [item["source_ids"] for item in rendered.limitations_audit["section_evidence"][-3:]] == [
        ["filing"], ["filing"], ["competitor"]
    ]


def test_calculation_provenance_is_not_classified_as_issuer_support(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff, sources=(_source(), _source("competitor"))
    )
    draft = _case_draft()
    scenarios = draft.sections[4].model_copy(update={
        "text": (
            "Base operating case: [113.40 billion USD]"
            "(model_appendix.md#calculation-operating_scenario.base.q4.revenue%2Fusd)\n\n"
            "The reported revenue input [filing] translates to the analyst scenario "
            "[113.40 billion USD]"
            "(model_appendix.md#calculation-operating_scenario.base.q4.revenue%2Fusd)."
        ),
    })
    draft = draft.model_copy(update={"sections": (*draft.sections[:4], scenarios, *draft.sections[5:])})

    rendered = render_reader(request, draft, snapshot, (), compact=True)

    assert rendered.limitations_audit["paragraph_citations"] == [
        {
            "section_index": 5,
            "paragraph_index": 1,
            "source_ids": [],
            "source_footnote_numbers": [],
            "citation_scope": "calculation_only",
            "calculation_ids": ["operating_scenario.base.q4.revenue/usd"],
        },
        {
            "section_index": 5,
            "paragraph_index": 2,
            "source_ids": ["filing"],
            "source_footnote_numbers": [1],
            "citation_scope": "paragraph_with_calculations",
            "calculation_ids": ["operating_scenario.base.q4.revenue/usd"],
        },
        {
            "section_index": 8,
            "paragraph_index": 1,
            "source_ids": ["filing"],
            "source_footnote_numbers": [1],
            "citation_scope": "paragraph",
        },
    ]


def test_compact_flag_cannot_shorten_a_noncase_reader(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(_source(),))
    rendered = render_reader(
        request,
        _draft(limitations=("Scope is limited.",), evidence_ids=("filing",)),
        snapshot,
        (),
        compact=True,
    )

    assert rendered.limitations_audit["reader_compaction"]["active"] is False
    assert rendered.limitations_audit["reader_compaction"]["reason"] == "authored_material_gaps_section_required"
    assert "## Material limitations" in rendered.reader_text
    assert "{{scenario_table}}" in CASE_READER_REQUIREMENTS
    assert "explicit [source_id] or [fact_id]" in CASE_READER_REQUIREMENTS
    assert "never handwrite a model-appendix provenance link" in CASE_READER_REQUIREMENTS


def test_compact_candidate_keeps_critical_security_and_numerical_warnings(tmp_path):
    request = _request(tmp_path)
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff=request.cutoff, sources=(_source(), _source("competitor"))
    )
    financial = ReaderIssue(
        message="Financial bridge remains unresolved.",
        provenance_id="financial:bridge",
        severity="critical",
        category="financial",
    )
    security = ReaderIssue(
        message="Security control evidence is incomplete.",
        provenance_id="security:control",
        severity="critical",
        category="security",
    )
    numerical = ReaderIssue(
        message="Numerical reconciliation failed.",
        provenance_id="numerical:reconciliation",
        severity="critical",
        category="numerical",
    )

    rendered = render_reader(
        request, _case_draft(), snapshot, (financial, security, numerical), compact=True
    )

    assert "Security control evidence is incomplete." in rendered.reader_text
    assert "Numerical reconciliation failed." in rendered.reader_text
    assert "Financial bridge remains unresolved." not in rendered.reader_text
    financial_audit = next(
        item
        for item in rendered.limitations_audit["unresolved_issues"]["consolidated_exact_text"]
        if item["original_text"] == financial.message
    )
    assert financial_audit["reader_display"] == "authored_material_gaps_pending_verification"
    assert financial_audit["issue_id"] in rendered.limitations_audit["unresolved_issues"][
        "unrepresented_issue_ids"
    ]
