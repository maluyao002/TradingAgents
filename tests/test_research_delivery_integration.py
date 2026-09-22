from hashlib import sha256

import pytest

from tests.test_research_case_scenarios import ScenarioFixture, operating_setup
from tradingagents.research.case_report import _unclassified_guidance_witnesses
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.report_review import (
    ReaderVerification,
    check_dispositions,
    validated_disposition_ids,
)
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


class DeliveryFixture(ScenarioFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if role == "editor":
            section = next(s for s in reply.data["sections"] if s["purpose"] == "scenarios")
            section["text"] += "\n\n{{scenario_assumptions_table}}"
        return reply


def generate(tmp_path):
    request, snapshot, _ = operating_setup(tmp_path)
    models = DeliveryFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    assert result.stop_reason == "completed_needs_review"
    return request, models


def test_new_table_delivery_is_bound_to_frozen_case_and_reproduced_by_preview(tmp_path):
    request, models = generate(tmp_path / "source")
    writer = next(payload for _, payload in models.calls if payload["stage"] == "editor")
    assert writer["case_reader_delivery"]["financial_case_review"]["status"] == "draft_unreviewed"
    before = {path: sha256(path.read_bytes()).hexdigest()
              for path in request.output_dir.rglob("*") if path.is_file()}
    record = read_json(request.output_dir / "stages/verify_report-rendering-provenance.json")["output"]
    assert record["rendering_inputs"]["case_reader_delivery_sha256"]
    assert any(binding["authored_marker"] == "{{scenario_assumptions_table}}"
               for section in record["sections"] for block in section["paragraphs"]
               for binding in block["bindings"])
    preview_saved_reader(request.output_dir, request, tmp_path / "preview")
    assert all(sha256(path.read_bytes()).hexdigest() == value for path, value in before.items())
    assert "Inputs / provenance" in (tmp_path / "preview/reader_preview.md").read_text()


def test_preview_does_not_trust_edited_case_context(tmp_path):
    request, _ = generate(tmp_path / "source")
    path = request.output_dir / "case_context.json"
    context = read_json(path)
    context["review_status"] = "reviewed"
    path.write_bytes(canonical_json(context))
    with pytest.raises(ValueError, match="case context differs"):
        preview_saved_reader(request.output_dir, request, tmp_path / "preview")


def test_nonliteral_capitalization_is_rejected_without_erasing_other_valid_dispositions():
    reader = "Uncertainty: the extent of that dependence remains unresolved."
    issues = [{"issue_id": name, "text": "Disclose dependency uncertainty."} for name in ("bad", "good")]
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[
        {"issue_id": "bad", "decision": "reader_covered", "rationale": "Not an exact quote",
         "reader_excerpts": ["The extent of that dependence remains unresolved."]},
        {"issue_id": "good", "decision": "reader_covered", "rationale": "Exact witness",
         "reader_excerpts": ["the extent of that dependence remains unresolved."]},
    ])
    assert validated_disposition_ids(review, issues, reader) == ("good",)
    checked = check_dispositions(review, issues, reader)
    assert any("not an exact substring" in f.message for f in checked.findings)


@pytest.mark.parametrize("text", [
    "Revenue is expected to be $100 billion, plus or minus 2% for a different fiscal quarter only.",
    "Prior quarter only\nRevenue is expected to be $100 billion, plus or minus ..%.",
    "Revenue is expected to be $100 billion, plus or minus 2%. This does not apply to Q3.",
])
def test_full_guidance_context_is_preserved_without_typed_period_or_numeric_claim(text):
    assumption = {"value": "100000000000", "classification": "management_guidance_anchor", "evidence_ids": ["m"]}
    material = {"m": {"source_id": "source", "text": text}}
    assert _unclassified_guidance_witnesses("revenue", assumption, material) == [
        {"source_id": "source", "exact_excerpt": text}]


def test_margin_passages_are_never_retrieved_as_opex_guidance():
    assumption = {"value": "1", "classification": "management_guidance_anchor", "evidence_ids": ["m"]}
    material = {"m": {"source_id": "source", "text":
        "GAAP and non-GAAP gross margins are expected to be 75%, plus or minus 50 basis points."}}
    assert _unclassified_guidance_witnesses("opex", assumption, material) == []
