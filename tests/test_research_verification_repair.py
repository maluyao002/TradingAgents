"""Frozen-reader continuation: new attestations, no new writing or waivers."""

from copy import deepcopy

import pytest

from tests.test_research_case_engine import CaseFixture
from tests.test_research_reader_revision import _revision
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryModelService,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
)
from tradingagents.research.reader_revision import FROZEN_REVIEW_STAGE, VERIFICATION_REPAIR_POLICY
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


class FailedCoverage(CaseFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"].startswith("verify_revised_report-coverage-"):
            reply.data["findings"] = [{"code": "coverage_incomplete", "severity": "warning",
                                      "message": "Synthetic coverage must be independently rechecked."}]
        return reply


def _verification(tmp_path, provider=None, *, tokens=4_000_000):
    _, revised, evidence, _, recovery, _ = _revision(tmp_path, FailedCoverage())
    result = run_research(revised, ResearchServices(evidence.evidence, recovery))
    assert result.stop_reason == "verification_failed"
    destination = revised.model_copy(update={"output_dir": tmp_path / "reverified",
        "budget": revised.budget.model_copy(update={"total_tokens": tokens})})
    plan = prepare_finalization_continuation(revised.output_dir, destination, repair_verification=True)
    auth = recovery.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget})
    provider = provider or CaseFixture()
    service = FinalizationRecoveryModelService(authorize_finalization_continuation(plan, auth), provider)
    return revised, destination, evidence.evidence, service, provider


def test_frozen_verification_new_complete_attestations_without_writer(tmp_path):
    source, request, evidence, service, provider = _verification(tmp_path)
    before = {p: p.read_bytes() for p in source.output_dir.rglob("*") if p.is_file()}
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review"
    stages = [p["stage"] for _, p in provider.calls]
    assert stages[0] == FROZEN_REVIEW_STAGE
    assert len(stages) > 1 and all(s.startswith(FROZEN_REVIEW_STAGE + "-coverage-") for s in stages[1:])
    assert all(role == "verifier" for role, _ in provider.calls)
    assert all(p["verification_repair_policy"] == VERIFICATION_REPAIR_POLICY for _, p in provider.calls)
    assert all(p["research"]["rendered_reader"] == service.plan.plan.candidate["reader_text"]
               for _, p in provider.calls)
    review = read_json(request.output_dir / "reader_verification.json")["English"]
    assert review["exported"] and review["reader_sha256"] == service.plan.plan.candidate["reader_sha256"]
    assert result.usage.total_tokens == service.plan.plan.source_usage.total_tokens + 130 * len(stages)
    assert {p: p.read_bytes() for p in before} == before
    assert not read_json(request.output_dir / "report_admission.json")["production_activation"]
    with pytest.raises(ValueError):
        prepare_finalization_continuation(request.output_dir,
            request.model_copy(update={"output_dir": tmp_path / "again"}), repair_verification=True)


@pytest.mark.parametrize("failure", ["factual", "coverage", "audit_spans", "timeout"])
def test_frozen_verification_failures_remain_blocking(tmp_path, failure):
    class BadVerification(CaseFixture):
        def complete(self, role, payload, request):
            if failure == "timeout":
                raise TimeoutError("synthetic verifier failure")
            reply = super().complete(role, payload, request)
            coverage = "-coverage-" in payload["stage"]
            if (failure == "factual" and not coverage) or (failure == "coverage" and coverage):
                reply.data["findings"] = [{"code": "unresolved", "severity": "warning", "message": "Still open."}]
            if failure == "audit_spans" and coverage:
                reply.data["limitation_dispositions"][0].update(decision="audit_only_operational")
            return reply
    _, request, evidence, service, _ = _verification(tmp_path, BadVerification())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason != "completed_needs_review"
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]
    if failure == "timeout":
        assert not result.usage.complete


def test_frozen_verification_reserves_full_path_before_first_call(tmp_path):
    _, request, evidence, service, provider = _verification(tmp_path, tokens=30_000)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "reverification_path_budget_insufficient"
    assert not provider.calls and result.usage == service.plan.plan.source_usage


@pytest.mark.parametrize("mutation", ["writer", "changed_reader", "missing_prefix", "wrong_policy"])
def test_frozen_service_rejects_unauthorized_calls(tmp_path, mutation):
    _, request, _, service, provider = _verification(tmp_path)
    plan = service.plan.plan
    service.validate_request(request, plan.frozen_inputs)
    service._imported_stage_names = {i.stage for i in plan.imported_stages}
    payload = {"stage": FROZEN_REVIEW_STAGE, "verification_repair_policy": VERIFICATION_REPAIR_POLICY,
               "research": {"rendered_reader": plan.candidate["reader_text"],
                            "rendered_reader_sha256": plan.candidate["reader_sha256"]}}
    role = "verifier"
    if mutation == "writer":
        role = "editor"
    elif mutation == "changed_reader":
        payload["research"]["rendered_reader"] += "changed"
    elif mutation == "missing_prefix":
        service._imported_stage_names.pop()
    else:
        payload["verification_repair_policy"] = "unknown"
    with pytest.raises(ValueError):
        service.call_origin(role, payload, request)
    assert not provider.calls


def test_frozen_repair_rejects_unclean_factual_source_and_conflicting_modes(tmp_path):
    source, request, _, _, _ = _verification(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        prepare_finalization_continuation(source.output_dir, request,
                                         repair_verification=True, revise_reader=True)
    path = source.output_dir / "finalization_checkpoint.json"
    checkpoint = read_json(path)
    from tradingagents.research.storage import digest
    stage = checkpoint["stages"]["verify_revised_report"]
    stage["output"]["findings"] = [{"code": "still_wrong", "severity": "warning", "message": "Wrong"}]
    stage["output_hash"] = digest(stage["output"])
    path.write_bytes(canonical_json(checkpoint))
    with pytest.raises(ValueError, match="clean factual"):
        prepare_finalization_continuation(source.output_dir, request, repair_verification=True)


def test_frozen_repair_lost_paid_output_is_not_redispatched(tmp_path):
    from tradingagents.research.storage import CheckpointStore
    class LostOutput(CheckpointStore):
        def save_stage(self, stage, inputs, output):
            if stage == FROZEN_REVIEW_STAGE:
                raise OSError("synthetic lost paid output")
            return super().save_stage(stage, inputs, output)
    _, request, evidence, service, provider = _verification(tmp_path)
    first = run_research(request, ResearchServices(evidence, service, storage=LostOutput))
    assert first.stop_reason == "stage_failed" and len(provider.calls) == 1
    next_provider = CaseFixture()
    resumed = run_research(request, ResearchServices(evidence,
        FinalizationRecoveryModelService(service.plan, next_provider)))
    assert resumed.stop_reason == "stage_failed" and not next_provider.calls
    assert resumed.usage == first.usage


def test_frozen_repair_restoration_requires_full_prefix_and_verifier_boundary(tmp_path):
    _, request, evidence, service, _ = _verification(tmp_path)
    run_research(request, ResearchServices(evidence, service))
    saved = deepcopy(service.candidate_recovery_context)
    saved["replay_state"]["live_boundary"]["role"] = "editor"
    saved["current_calls"][0]["role"] = "editor"
    restored = FinalizationRecoveryModelService(service.plan, CaseFixture())
    with pytest.raises(ValueError, match="frozen verifier boundary"):
        restored.restore_recovery_context(saved)
