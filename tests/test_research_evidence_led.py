"""Integration contracts for the opt-in evidence-led workflow."""

from copy import deepcopy
from hashlib import sha256

import pytest

from tests.test_research_engine import replies, setup
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.engine import _prompt_evidence, run_research
from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import digest, read_json, request_identity


class ExplicitFixtureReviews(ReplayModelService):
    """Synthetic fixture dispositions; never used for real research review."""

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        issues = payload.get("research", {}).get("limitation_review")
        if issues is not None and "limitation_dispositions" not in reply.data:
            reader = payload["research"]["rendered_reader"]
            dispositions = [{
                "issue_id": item["issue_id"],
                "decision": "reader_covered" if item["text"] in reader else "audit_only_operational",
                "rationale": "Explicit synthetic fixture disposition, not an investment review.",
                "reader_excerpt": item["text"] if item["text"] in reader else "",
            } for item in issues]
            reply = reply.model_copy(update={"data": {**reply.data, "limitation_dispositions": dispositions}})
        return reply


def enhanced(tmp_path, data=None):
    request, services = setup(tmp_path, data)
    request = request.model_copy(update={
        "quality_revision": "evidence-led", "report_language": "English",
    })
    return request, ResearchServices(services.evidence, ExplicitFixtureReviews(data or replies()))


def test_foundation_identity_is_byte_compatible_and_revision_is_distinct(tmp_path):
    request, _ = setup(tmp_path)
    settings = request.model_dump(mode="json", exclude={
        "output_dir", "dossier_dir", "quality_revision", "valuation_method", "share_count_basis",
        "financial_case_path",
    })
    assert request_identity(request) == digest({
        "engine": "research-v2-preview-4", "settings": settings,
    })
    assert request_identity(request.model_copy(update={
        "quality_revision": "evidence-led",
    })) != request_identity(request)


def test_generated_followup_lists_are_bounded_without_failing_the_stage(tmp_path):
    request, _ = enhanced(tmp_path)
    snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff)
    packet = _prompt_evidence(snapshot, tuple("Unanswered retention " * 100 for _ in range(30)))
    assert packet["context_version"] == 2
    assert any("Question delivery was bounded" in gap for gap in packet["context_gaps"])


def test_exact_export_is_reviewed_and_all_gaps_audited(tmp_path):
    request, services = enhanced(tmp_path)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    review_payload = next(payload for _, payload in services.models.calls
                          if payload["stage"] == "verify_report")
    assert review_payload["research"]["rendered_reader"] == reader
    assert review_payload["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
    # Missing valuation cannot be hidden by an optimistic editor.
    assert "Synthetic fixture has no financials" in reader
    assert "reader_limitations.json" in reader
    audit = read_json(request.output_dir / "reader_limitations.json")
    texts = {item["original_text"] for item in audit["unresolved_issues"]["occurrences"]}
    assert "V2 source coverage and company-specific model acceptance remain pending." in texts
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["exported"] is True
    assert verification["reader_sha256"] == result.artifact_hashes["reader_report.md"]
    assert not read_json(request.output_dir / "run_metadata.json")["production_accepted"]


def test_one_repair_then_reverify_exact_repaired_export(tmp_path):
    data = replies()
    data["verifier"][1]["data"]["findings"] = [{
        "code": "wording", "severity": "warning", "message": "Qualify causal wording",
    }]
    data["verifier"].append({"data": {"reviewed_report": True}, "usage": {}})
    repaired = deepcopy(data["editor"][0])
    repaired["data"]["sections"][0]["text"] = "Repaired and qualified reader text."
    data["editor"].append(repaired)
    request, services = enhanced(tmp_path, data)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    stages = [payload["stage"] for _, payload in services.models.calls]
    assert stages[-2:] == ["repair_report", "verify_repaired_report"]
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Repaired and qualified" in reader
    assert services.models.calls[-1][1]["research"]["rendered_reader"] == reader
    assert read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_failed_repair_withholds_reader_without_unbounded_retry(tmp_path):
    data = replies()
    finding = {"code": "unsupported", "severity": "critical", "message": "Unsupported claim"}
    data["verifier"][1]["data"]["findings"] = [finding]
    data["verifier"].append(deepcopy(data["verifier"][1]))
    data["editor"].append(deepcopy(data["editor"][0]))
    request, services = enhanced(tmp_path, data)
    result = run_research(request, services)
    assert result.stop_reason == "verification_failed"
    stages = [payload["stage"] for _, payload in services.models.calls]
    assert stages.count("repair_report") == 1
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Diagnostic only" in reader
    assert "测试研究" not in reader
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_audit_tamper_prevents_immutable_resume(tmp_path):
    request, services = enhanced(tmp_path)
    run_research(request, services)
    (request.output_dir / "reader_limitations.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact mismatch"):
        run_research(request, ResearchServices(services.evidence, ReplayModelService(replies())))


def test_valuation_blocker_triggers_local_retrieval_but_keyword_match_cannot_close_it(tmp_path):
    data = replies()
    question = "Orion covenant retention"
    data["valuation"][0]["data"]["unsupported_inputs"] = [question]
    data["valuation"].append(deepcopy(data["valuation"][0]))
    data["planner"].append(deepcopy(data["planner"][0]))
    for role in ("business", "accounting", "expectations", "management"):
        revision = deepcopy(data[role][0])
        revision["data"]["summary"] = "Revised after reading the covenant passage."
        data[role].append(revision)
    # Explicit closure review is separate from ordinary claim/report verification.
    data["verifier"].insert(0, {"data": {"decisions": []}, "usage": {}})
    request, services = enhanced(tmp_path, data)
    content = "Unrelated introductory material. " * 6000 + "\n\nOrion covenant retention cannot be zero.\n\n"
    source = SourceDocument(id="source", url="https://example.com/source", title="Synthetic",
                            publisher="Synthetic", published_at=request.cutoff, retrieved_at=request.cutoff,
                            content=content, content_sha256=sha256(content.encode()).hexdigest())
    snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff, sources=(source,))
    services = ResearchServices(SnapshotEvidenceService(snapshot), services.models)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    retrieval = read_json(request.output_dir / "stages/retrieval-0.json")["output"]
    assert any("Orion covenant" in excerpt["text"] for item in retrieval["sources"] for excerpt in item["excerpts"])
    assert read_json(request.output_dir / "research.json")["business"]["summary"].startswith("Revised")
    ledger = read_json(request.output_dir / "investigation.json")
    assert len(ledger["cycles"]) == 1
    assert ledger["cycles"][0]["external_acquisition_performed"] is False
    assert any(entry["text"] == question and entry["status"] == "still_open" for entry in ledger["ledger"]["entries"])
    assert run_research(request, services) == result
