from hashlib import sha256

import pytest

from tradingagents.research.contracts import (
    EvidenceSnapshot,
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
