"""Stage 3 offline integration: case material, reader gates and immutable replay."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from cli.research import load_request, load_responses
from tests.test_research_engine import replies
from tests.test_research_financial_case import _case, _snapshot
from tradingagents.research.case_report import SECTION_PURPOSES, CaseReportDraft
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import canonical_json, digest, read_json, request_identity


class CaseFixture:
    """Mechanics-only synthetic responses, never a real NVDA research report."""

    kind = "replay"
    identity = digest("case-reader-fixture-1")

    def __init__(self, *, timeout=None, missing_section=False, forbidden_calculation=False,
                 repair_warning=False, claim_warning=False):
        self.calls = []
        self.timeout = timeout
        self.missing_section = missing_section
        self.forbidden_calculation = forbidden_calculation
        self.repair_warning = repair_warning
        self.claim_warning = claim_warning

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        stage = payload["stage"]
        if stage == self.timeout:
            raise TimeoutError("synthetic fixture failure")
        assert role != "valuation", "case-backed runs must not invent unbound forecasts"
        data = deepcopy(replies()[role][0]["data"])
        if role == "editor":
            data = {
                "sections": [{"purpose": purpose, "title": purpose.replace("_", " ").title(),
                              "text": f"Synthetic {purpose} mechanics fixture; no investment conclusion."}
                             for purpose in SECTION_PURPOSES],
                "limitations": payload["research"]["limitations"], "investment_view": "unrated",
            }
            if self.missing_section:
                data["sections"].pop()
            if self.forbidden_calculation:
                data["sections"][0]["text"] = "Target {{calc:valuation.value_per_current_diluted_share}}"
        if role == "verifier":
            data = {"reviewed_report": stage != "verify_claims"}
            if (self.repair_warning and stage == "verify_report") or (self.claim_warning and stage == "verify_claims"):
                data["findings"] = [{"code": "fixture_warning", "severity": "warning",
                                     "message": "Synthetic qualification required"}]
            if "limitation_review" in payload["research"]:
                reader = payload["research"]["rendered_reader"]
                data["limitation_dispositions"] = [{
                    "issue_id": issue["issue_id"], "decision": "reader_covered",
                    "rationale": "Synthetic case limitations are explicitly retained.",
                    "reader_excerpt": issue["text"],
                } for issue in payload["research"]["limitation_review"] if issue["text"] in reader]
        return ModelReply(data=data, usage={"input_tokens": 100, "output_tokens": 30})


def case_setup(tmp_path, models=None, *, reviewed=False):
    snapshot = _snapshot()
    case = _case(snapshot)
    envelope = {"case": case.model_dump(mode="json")}
    # The production review contract is exercised separately; draft is deliberate.
    assert not reviewed
    case_path = tmp_path / "case.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    case_path.write_bytes(canonical_json(envelope))
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(canonical_json(snapshot))
    request = ResearchRequest(
        ticker="NVDA", cutoff=snapshot.cutoff, timezone=case.timezone, backend="replay",
        output_dir=tmp_path / "out", evidence_path=evidence_path, financial_case_path=case_path,
        quality_revision="evidence-led-bounded", report_language="English",
        budget={"followup_cycles": 0},
    )
    return request, ResearchServices(SnapshotEvidenceService(snapshot), models or CaseFixture())


def test_case_replay_delivers_material_without_unbound_valuation(tmp_path):
    request, services = case_setup(tmp_path)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    assert result.assessment.status == "needs_review"
    assert result.assessment.investment_view == "unrated"
    assert "report_admission.json" in result.artifacts
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Diagnostic only" not in reader
    assert all(purpose.replace("_", " ").title() in reader for purpose in SECTION_PURPOSES)
    assert read_json(request.output_dir / "calculated_values.json") == []
    assert read_json(request.output_dir / "valuation_results.json")["status"] == "unavailable"
    case_context = read_json(request.output_dir / "case_context.json")
    delivered = read_json(request.output_dir / "case_material_delivery.json")["stages"]
    for _role, payload in services.models.calls:
        if payload["stage"] in {"planner", "independent_challenge"} or "-coverage-" in payload["stage"]:
            assert "financial_case" not in payload
        else:
            assert payload["financial_case"] == case_context
            assert delivered[payload["stage"]]["case_context_sha256"] == digest(case_context)
            without_runtime = {k: v for k, v in payload.items() if k not in {"timeout_seconds", "max_output_tokens"}}
            assert delivered[payload["stage"]]["payload_sha256"] == digest(without_runtime)
    assert "frozen evidence for filing" in canonical_json(case_context).decode()
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["reader_sha256"] == result.artifact_hashes["reader_report.md"]
    assert not read_json(request.output_dir / "run_metadata.json")["production_accepted"]
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["report_completion"] == "complete"
    assert admission["qualitative_analysis"]["status"] == "eligible"
    assert admission["acceptance_eligibility"]["status"] == "blocked"
    assert not admission["production_activation"]
    for name, expected in result.artifact_hashes.items():
        assert sha256((request.output_dir / name).read_bytes()).hexdigest() == expected
    fresh = CaseFixture()
    assert run_research(request, ResearchServices(services.evidence, fresh)) == result
    assert not fresh.calls


@pytest.mark.parametrize("mutation", ["case", "evidence"])
def test_changed_case_or_evidence_invalidates_completed_run(tmp_path, mutation):
    request, services = case_setup(tmp_path)
    run_research(request, services)
    path = request.financial_case_path if mutation == "case" else request.evidence_path
    data = read_json(path)
    if mutation == "case":
        data["case"]["schedules"][0]["rationale"] += " changed assumption"
    else:
        data["gaps"] = ["new evidence gap"]
    path.write_bytes(canonical_json(data))
    fresh = CaseFixture()
    with pytest.raises(ValueError, match="incompatible"):
        run_research(request, ResearchServices(services.evidence, fresh))
    assert not fresh.calls


@pytest.mark.parametrize("name", ["case_context.json", "report_admission.json", "model_appendix.md"])
def test_case_artifact_tampering_blocks_completed_replay(tmp_path, name):
    request, services = case_setup(tmp_path)
    run_research(request, services)
    (request.output_dir / name).write_text("tampered")
    with pytest.raises(ValueError, match="artifact mismatch"):
        run_research(request, services)


def test_bad_case_stops_before_model_calls(tmp_path):
    request, services = case_setup(tmp_path)
    data = read_json(request.financial_case_path)
    data["case"]["snapshot_sha256"] = "0" * 64
    request.financial_case_path.write_bytes(canonical_json(data))
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert result.assessment.status == "incomplete"
    assert not services.models.calls
    assert "financial_reconciliation.json" not in result.artifacts


@pytest.mark.parametrize("options", [{"missing_section": True}, {"forbidden_calculation": True}])
def test_incomplete_structure_and_withheld_calculation_fail_closed(tmp_path, options):
    request, services = case_setup(tmp_path, CaseFixture(**options))
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert result.assessment.status == "incomplete"
    assert "Diagnostic only" in (request.output_dir / "reader_report.md").read_text()


def test_case_verification_timeout_keeps_candidate_and_unknown_usage(tmp_path):
    request, services = case_setup(tmp_path, CaseFixture(timeout="verify_report-coverage-0"))
    result = run_research(request, services)
    assert not result.usage.complete
    assert result.assessment.status == "incomplete"
    candidate = read_json(request.output_dir / "stages/verify_report-reader-candidate.json")["output"]
    assert candidate["verification_status"] == "unverified"
    fresh = CaseFixture()
    repeated = run_research(request, ResearchServices(services.evidence, fresh))
    assert repeated.stop_reason == "usage_incomplete"
    assert not fresh.calls


def test_opt_in_does_not_change_legacy_request_identity(tmp_path):
    request, _ = case_setup(tmp_path)
    legacy = request.model_copy(update={"financial_case_path": None, "quality_revision": "foundation"})
    settings = legacy.model_dump(mode="json", exclude={"output_dir", "dossier_dir", "financial_case_path",
                                                     "quality_revision", "valuation_method", "share_count_basis"})
    settings["evidence_path"] = sha256(legacy.evidence_path.read_bytes()).hexdigest()
    settings["prior_dossier_path"] = None
    assert request_identity(legacy) == digest({"engine": "research-v2-preview-4", "settings": settings})
    assert request_identity(legacy) != request_identity(request)


def test_case_config_paths_and_optional_valuation_replay(tmp_path):
    request, _ = case_setup(tmp_path)
    config = request.model_dump(mode="json")
    config["financial_case_path"] = "case.json"
    path = tmp_path / "request.json"
    path.write_bytes(canonical_json(config))
    assert load_request(path).financial_case_path == request.financial_case_path
    saved = replies()
    saved.pop("valuation")
    response_path = tmp_path / "responses.json"
    response_path.write_bytes(canonical_json(saved))
    assert "valuation" not in load_responses(response_path, case_backed=True)
    with pytest.raises(ValueError, match="required initial"):
        load_responses(response_path)


@pytest.mark.parametrize("revision,method", [("foundation", "fcff"), ("evidence-led", "fcff"),
                                            ("evidence-led-bounded", "equity_fcfe")])
def test_case_boundary_requires_supported_workflow(tmp_path, revision, method):
    request, services = case_setup(tmp_path)
    invalid = request.model_copy(update={"quality_revision": revision, "valuation_method": method})
    with pytest.raises(ValueError, match="financial-case ingestion"):
        run_research(invalid, services)
    assert not services.models.calls


def test_duplicate_case_section_purpose_is_rejected():
    with pytest.raises(ValueError, match="each analytical purpose"):
        CaseReportDraft(sections=[{"purpose": "executive_thesis", "title": "T", "text": "X"}] * 8,
                        limitations=[])


def test_new_followup_evidence_stops_before_using_stale_case(tmp_path):
    request, services = case_setup(tmp_path)
    request = request.model_copy(update={"budget": request.budget.model_copy(update={
        "followup_cycles": 1, "wall_seconds": 30000, "total_tokens": 30000000})})

    class NewEvidence(SnapshotEvidenceService):
        def followup(self, request, snapshot, questions):
            return snapshot.model_copy(update={"gaps": ("New source gap",)})

    result = run_research(request, ResearchServices(NewEvidence(services.evidence.snapshot), services.models))
    assert result.stop_reason == "stage_failed"
    assert result.assessment.status == "incomplete"
    assert not any(payload["stage"].startswith("revision-") for _, payload in services.models.calls)


def test_historical_prefix_import_is_not_a_case_recovery_path(tmp_path):
    request, services = case_setup(tmp_path)
    services.models.recovery_context = {"schema_version": 1}
    with pytest.raises(ValueError, match="historical-prefix recovery"):
        run_research(request, services)
    assert not services.models.calls


def test_untrusted_case_recovery_context_cannot_bypass_the_typed_service(tmp_path):
    request, services = case_setup(tmp_path)
    services.models.case_recovery_context = {"schema_version": 1}
    with pytest.raises(ValueError, match="validated case-recovery service"):
        run_research(request, services)
    assert not services.models.calls


def test_successful_reader_repair_is_not_blocked_by_historical_finding(tmp_path):
    request, services = case_setup(tmp_path, CaseFixture(repair_warning=True))
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["report_completion"] == "complete"
    assert any(item.code == "fixture_warning" for item in result.assessment.findings)
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["stage"] == "verify_repaired_report"
    assert verification["reader_sha256"] == result.artifact_hashes["reader_report.md"]


def test_unresolved_claim_review_fails_admission_even_when_reader_review_passes(tmp_path):
    request, services = case_setup(tmp_path, CaseFixture(claim_warning=True))
    result = run_research(request, services)
    assert result.stop_reason == "admission_failed"
    assert result.assessment.status == "incomplete"
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "incomplete"
    assert not (request.output_dir / "stages/completed-result.json").exists()


def test_optional_frozen_nvda_packet_mechanics_replay(tmp_path):
    """Real evidence, synthetic prose/reviews: exercises size/delivery, not quality."""
    config = Path("reports/RESEARCH_STAGE3_20260919/preflight_1/request.json")
    if not config.exists():
        pytest.skip("optional local frozen NVDA preflight packet is absent")
    request = load_request(config).model_copy(update={"output_dir": tmp_path / "mechanics-only"})
    models = CaseFixture()
    # The engine reads frozen evidence bytes; this empty injected provider is never collected.
    result = run_research(request, ResearchServices(SnapshotEvidenceService(_snapshot()), models))
    assert result.stop_reason == "completed_needs_review"
    assert result.assessment.investment_view == "unrated"
    assert len(read_json(request.output_dir / "evidence.json")["facts"]) == 182
    context = read_json(request.output_dir / "case_context.json")
    assert context["supplemental_source_material"]["passages"]
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "complete"
    assert all(getattr(result.usage, field) >= 0 for field in ("input_tokens", "output_tokens"))
    assert "Synthetic" in (request.output_dir / "reader_report.md").read_text()


def test_provider_review_boolean_must_be_strict_before_admission(tmp_path):
    class MalformedReview(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == "verify_report":
                reply = reply.model_copy(update={"data": {"reviewed_report": "true"}})
            return reply

    request, services = case_setup(tmp_path, MalformedReview())
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert result.assessment.status == "incomplete"
    assert result.usage.complete  # The response was measured, but its schema was invalid.


def test_blinded_challenge_has_no_case_derived_question_channel(tmp_path):
    request, services = case_setup(tmp_path)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    planner = next(payload for _, payload in services.models.calls if payload["stage"] == "planner")
    challenge = next(payload for _, payload in services.models.calls if payload["stage"] == "independent_challenge")
    assert "financial_case" not in planner and "financial_case" not in challenge
    assert planner["research"] == {}
    assert challenge["research"] == {}
    assert services.models.calls[0][1]["stage"] == "independent_challenge"
