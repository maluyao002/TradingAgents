"""Bound state rendering and mandatory review boundaries, not semantic gold labels."""

from dataclasses import replace
from hashlib import sha256

import pytest

from tests.test_research_case_engine import case_setup
from tests.test_research_case_scenarios import ScenarioFixture, operating_setup
from tradingagents.research.case_context import load_case_context
from tradingagents.research.engine import run_research
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.reader import render_reader
from tradingagents.research.reader_provenance import reader_provenance
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.review_disclosures import review_disclosure
from tradingagents.research.services import ResearchServices
from tradingagents.research.stages import ReportDraft
from tradingagents.research.storage import canonical_json, digest, read_json


def setup_context(tmp_path, *, financial_review=False, operating="reviewed"):
    if operating == "absent":
        request, services = case_setup(tmp_path)
        snapshot = services.evidence.snapshot
    else:
        request, snapshot, _ = operating_setup(tmp_path)
    envelope = read_json(request.financial_case_path)
    if operating == "draft":
        envelope["operating_scenarios"]["review"] = None
    if financial_review:
        envelope["review"] = {"status": "reviewed", "reviewer_id": "fixture-independent",
            "case_sha256": digest(envelope["case"]),
            "snapshot_sha256": evidence_snapshot_sha256(snapshot)}
    return request, snapshot, load_case_context(canonical_json(envelope), request, snapshot)


def draft(text="Authored narrative without a review-status claim."):
    return ReportDraft(sections=[{"title": "Thesis", "text": text}], investment_view="unrated", limitations=[])


@pytest.mark.parametrize("financial_review", [False, True])
@pytest.mark.parametrize("operating", ["absent", "draft", "reviewed"])
def test_financial_and_operating_review_states_are_independent(tmp_path, financial_review, operating):
    request, snapshot, context = setup_context(tmp_path, financial_review=financial_review, operating=operating)
    result = review_disclosure(request, snapshot, context, "English")
    assert result["financial_status"] == ("reviewed" if financial_review else "draft_unreviewed")
    assert result["operating_status"] == {
        "absent": "absent", "draft": "draft_unreviewed", "reviewed": "reviewed_operating_only"}[operating]
    assert ("financial schedules remain an unreviewed draft" in result["text"]) == (not financial_review)
    assert "calendar-period cash flows or economic underwriting" in result["text"]
    assert "funding conclusions remain withheld" in result["text"]
    assert result["text_sha256"] == sha256(result["text"].encode()).hexdigest()


def test_missing_authored_disclosure_is_supplied_once_without_changing_prose(tmp_path):
    request, snapshot, context = setup_context(tmp_path)
    authored = draft()
    rendered = render_reader(request, authored, snapshot, (), bind_case_state=True, case_context=context)
    assert rendered.reader_text.count("## Review status and model scope") == 1
    assert authored.sections[0].text in rendered.reader_text
    assert "financial schedules remain an unreviewed draft" in rendered.reader_text
    provenance = reader_provenance(authored, authored, snapshot.facts, (), "English", rendered.reader_text,
        request=request, snapshot=snapshot, case_context=context, bind_case_state=True)
    assert provenance["rendering_inputs"]["case_review_disclosure"] == rendered.limitations_audit["case_review_disclosure"]
    with pytest.raises(ValueError, match="prepared-to-reader"):
        reader_provenance(authored, authored, snapshot.facts, (), "English", rendered.reader_text.replace(
            "unreviewed draft", "approved model"), request=request, snapshot=snapshot,
            case_context=context, bind_case_state=True)


def test_absent_or_changed_case_state_cannot_claim_review(tmp_path):
    request, snapshot, context = setup_context(tmp_path)
    with pytest.raises(ValueError, match="requires validated"):
        render_reader(request, draft(), snapshot, (), bind_case_state=True)
    with pytest.raises(ValueError, match="differs from frozen"):
        review_disclosure(request, snapshot, replace(context, reviewed=True), "English")
    stale = snapshot.model_copy(update={"gaps": ("changed snapshot",)})
    with pytest.raises(ValueError, match="snapshot hash"):
        render_reader(request, draft(), stale, (), bind_case_state=True, case_context=context)
    diagnostic = render_reader(request, None, stale, (), bind_case_state=True, case_context=context)
    assert "review status cannot be established" in diagnostic.reader_text
    assert "separately reviewed" not in diagnostic.reader_text
    assert diagnostic.limitations_audit["case_review_disclosure"] is None


def test_review_change_changes_reader_and_provenance_binding(tmp_path):
    request, snapshot, unreviewed = setup_context(tmp_path / "draft")
    _request, _snapshot, reviewed = setup_context(tmp_path / "reviewed", financial_review=True)
    before = render_reader(request, draft(), snapshot, (), bind_case_state=True, case_context=unreviewed)
    after = render_reader(request, draft(), snapshot, (), bind_case_state=True, case_context=reviewed)
    assert before.reader_text != after.reader_text
    assert before.limitations_audit["case_review_disclosure"]["case_context_sha256"] != after.limitations_audit["case_review_disclosure"]["case_context_sha256"]


def test_chinese_disclosure_is_localized_without_changing_state(tmp_path):
    request, snapshot, context = setup_context(tmp_path)
    result = review_disclosure(request, snapshot, context, "Chinese")
    assert "财务明细仍为未经复核的草稿" in result["text"]
    assert "calendar" not in result["text"]
    assert result["financial_status"] == "draft_unreviewed"


def test_generated_boundary_does_not_hide_contradictory_authored_prose(tmp_path):
    request, snapshot, context = setup_context(tmp_path)
    contradiction = "The financial schedules have been approved as a complete valuation model."
    rendered = render_reader(request, draft(contradiction), snapshot, (),
                             bind_case_state=True, case_context=context)
    # Preserve contradictory prose for mandatory factual review, never silently
    # rewrite the author or pretend deterministic rendering detects all semantics.
    assert contradiction in rendered.reader_text
    assert "financial schedules remain an unreviewed draft" in rendered.reader_text


def test_factual_contradiction_still_blocks_export_despite_generated_boundary(tmp_path):
    class ContradictingWriter(ScenarioFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["text"] += " Financial schedules are approved."
            if payload["stage"] in {"verify_report", "verify_repaired_report"}:
                assert "financial schedules remain an unreviewed draft" in payload["research"]["rendered_reader"]
                assert "contradictions" in payload["research"]["rendered_reader_policy"]
                reply.data["findings"] = [{"code": "contradictory_review_claim", "severity": "critical",
                    "category": "research", "message": "Authored prose contradicts validated review status."}]
            return reply
    request, snapshot, _ = operating_setup(tmp_path)
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), ContradictingWriter()))
    assert result.stop_reason != "completed_needs_review"
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_preview_seven_keeps_its_original_disclosure_free_rendering(tmp_path, monkeypatch):
    from tradingagents.research import engine, storage
    from tradingagents.research.reader_preview import preview_saved_reader

    original_render = engine.render_reader
    original_provenance = engine.reader_provenance

    def prior_render(*args, **kwargs):
        kwargs["bind_case_state"] = False
        return original_render(*args, **kwargs)

    def prior_provenance(*args, **kwargs):
        kwargs["bind_case_state"] = False
        return original_provenance(*args, **kwargs)

    request, services = case_setup(tmp_path / "source")
    with monkeypatch.context() as prior:
        prior.setattr(engine, "render_reader", prior_render)
        prior.setattr(engine, "reader_provenance", prior_provenance)
        prior.setattr(engine, "ENGINE_VERSION", "research-v2-preview-7")
        prior.setattr(storage, "ENGINE_VERSION", "research-v2-preview-7")
        result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    preview_saved_reader(request.output_dir, request, tmp_path / "preview")
    assert "## Review status and model scope" not in (tmp_path / "preview/reader_preview.md").read_text()
