"""Opt-in revision boundaries; fixtures never perform live research."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tests.test_research_case_engine import CaseFixture, case_setup
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryAuthorization,
    FinalizationRecoveryModelService,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
)
from tradingagents.research.reader_revision import READER_REVISION_POLICY
from tradingagents.research.report_review import (
    ReaderVerification,
    fan_in_compound_dispositions,
    limitation_packet,
    validated_disposition_ids,
)
from tradingagents.research.review_lifecycle import (
    _RECONCILIATION_LINEAGE,
    compound_coverage_issues,
    split_compound_obligations,
)
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


class FailedRepair(CaseFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] in {"verify_report", "verify_repaired_report"}:
            reply.data["findings"] = [{"code": "needs_precision", "severity": "warning",
                                        "message": "Synthetic wording must be rechecked."}]
        return reply


def _revision(tmp_path, provider=None, *, tokens=4_000_000):
    request, services = case_setup(tmp_path / "source", FailedRepair())
    result = run_research(request, services)
    assert result.stop_reason == "verification_failed"
    destination = request.model_copy(update={
        "output_dir": tmp_path / "revised",
        "budget": request.budget.model_copy(update={"total_tokens": tokens, "reserve_tokens": 0}),
    })
    plan = prepare_finalization_continuation(request.output_dir, destination, revise_reader=True)
    authorization = FinalizationRecoveryAuthorization(
        authorization_id="offline-test-only", authorized_at=datetime.now(timezone.utc),
        plan_sha256=plan.plan_sha256, new_request_identity=plan.new_request_identity,
        incremental_budget=destination.budget, authorize_live_continuation=True,
    )
    provider = provider or CaseFixture()
    recovery = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    return request, destination, services, plan, recovery, provider


def test_revision_fresh_checks_even_for_identical_candidate(tmp_path):
    source, destination, services, plan, recovery, provider = _revision(tmp_path)
    before = {path: path.read_bytes() for path in source.output_dir.rglob("*") if path.is_file()}
    result = run_research(destination, ResearchServices(services.evidence, recovery))
    assert result.stop_reason == "completed_needs_review"
    stages = [payload["stage"] for _, payload in provider.calls]
    assert stages[:2] == ["revise_report", "verify_revised_report"]
    assert all(stage.startswith("verify_revised_report-coverage-") for stage in stages[2:])
    assert len(stages) > 2
    assert all(payload["reader_revision_policy"] == READER_REVISION_POLICY
               for _, payload in provider.calls)
    old = read_json(source.output_dir / "reader_verification.json")["English"]
    new = read_json(destination.output_dir / "reader_verification.json")["English"]
    assert old["reader_sha256"] == new["reader_sha256"]  # fresh, even if byte-identical
    assert new["exported"] and new["stage"] == "verify_revised_report"
    assert len(new["coverage_batches"]) == len(stages) - 2
    assert result.usage.total_tokens == plan.source_usage.total_tokens + len(stages) * 130
    assert {path: path.read_bytes() for path in before} == before
    assert read_json(destination.output_dir / "report_admission.json")["production_activation"] is False
    with pytest.raises(ValueError, match="invalid factual-reviewed"):
        prepare_finalization_continuation(destination.output_dir,
                                         destination.model_copy(update={"output_dir": tmp_path / "again"}),
                                         revise_reader=True)


@pytest.mark.parametrize("failure", ["warning", "excerpt", "timeout"])
def test_revision_never_waives_remaining_failures(tmp_path, failure):
    class BadRevision(CaseFixture):
        def complete(self, role, payload, request):
            if failure == "timeout" and payload["stage"] == "revise_report":
                raise TimeoutError("synthetic timeout")
            reply = super().complete(role, payload, request)
            if failure == "warning" and payload["stage"] == "verify_revised_report":
                reply.data["findings"] = [{"code": "still_wrong", "severity": "warning",
                                            "message": "Still unsupported."}]
            if failure == "excerpt" and "-coverage-" in payload["stage"]:
                reply.data["limitation_dispositions"][0]["reader_excerpt"] = "Invented excerpt"
            return reply

    _, destination, services, _, recovery, provider = _revision(tmp_path, BadRevision())
    result = run_research(destination, ResearchServices(services.evidence, recovery))
    assert result.stop_reason != "completed_needs_review"
    verification = read_json(destination.output_dir / "reader_verification.json")["English"]
    assert not verification["exported"]
    assert sum(payload["stage"] == "revise_report" for _, payload in provider.calls) <= 1
    if failure == "timeout":
        assert not result.usage.complete
    else:
        assert result.stop_reason == "verification_failed"
        assert verification["stage"] == "verify_revised_report"


def test_revision_path_budget_stop_before_any_paid_call(tmp_path):
    _, destination, services, _, recovery, provider = _revision(tmp_path, tokens=30_000)
    result = run_research(destination, ResearchServices(services.evidence, recovery))
    assert result.stop_reason == "revision_path_budget_insufficient"
    assert not provider.calls


def test_revision_opt_in_bound_to_authorization_and_terminal_artifacts(tmp_path):
    source, destination, _, revision, _, _ = _revision(tmp_path)
    ordinary = prepare_finalization_continuation(source.output_dir, destination)
    assert "reader_revision_policy" not in ordinary.manifest()
    assert revision.plan_sha256 != ordinary.plan_sha256
    assert {"result.json", "reader_verification.json"} <= revision.source_artifact_hashes.keys()
    reader_path = source.output_dir / "reader_verification.json"
    reader = read_json(reader_path)
    reader["English"]["exported"] = True
    reader_path.write_bytes(canonical_json(reader))
    with pytest.raises(ValueError, match="settled bound"):
        prepare_finalization_continuation(source.output_dir, destination, revise_reader=True)


def test_revision_rejects_incomplete_prefix_before_live_boundary(tmp_path):
    _, destination, _, plan, recovery, provider = _revision(tmp_path)
    recovery.validate_request(destination, plan.frozen_inputs)
    recovery._imported_stage_names = {plan.candidate_review_stage}
    with pytest.raises(ValueError, match="complete exact source prefix"):
        recovery.call_origin("editor", {"stage": "revise_report"}, destination)
    recovery._imported_stage_names = {item.stage for item in plan.imported_stages}
    with pytest.raises(ValueError, match="explicit revision boundary"):
        recovery.call_origin("editor", {"stage": "arbitrary_retry"}, destination)
    assert not provider.calls


def test_revision_lost_paid_output_cannot_redispatch_or_reset_usage(tmp_path):
    from tradingagents.research.storage import CheckpointStore

    class LostOutput(CheckpointStore):
        def save_stage(self, stage, inputs, output):
            if stage == "revise_report":
                raise OSError("synthetic lost paid output")
            return super().save_stage(stage, inputs, output)

    _, destination, services, plan, recovery, provider = _revision(tmp_path)
    first = run_research(destination, ResearchServices(services.evidence, recovery, storage=LostOutput))
    assert first.stop_reason == "stage_failed"
    assert len(provider.calls) == 1
    assert first.usage.total_tokens == plan.source_usage.total_tokens + 130
    next_provider = CaseFixture()
    resumed = run_research(destination, ResearchServices(
        services.evidence, FinalizationRecoveryModelService(recovery.plan, next_provider)))
    assert resumed.stop_reason == "stage_failed"
    assert not next_provider.calls and resumed.usage == first.usage


@pytest.mark.parametrize("mutation", ["partial_prefix", "wrong_stage", "wrong_role"])
def test_revision_rejects_malformed_restored_live_boundary(tmp_path, mutation):
    _, destination, services, _, recovery, _ = _revision(tmp_path)
    run_research(destination, ResearchServices(services.evidence, recovery))
    saved = deepcopy(recovery.candidate_recovery_context)
    state = saved["replay_state"]
    if mutation == "partial_prefix":
        dropped = state["imported_calls"].pop(0)["stage"]
        state["imported_stage_names"].remove(dropped)
    else:
        key, value = ("stage", "arbitrary_retry") if mutation == "wrong_stage" else ("role", "verifier")
        state["live_boundary"][key] = value
        saved["current_calls"][0][key] = value
    restored = FinalizationRecoveryModelService(recovery.plan, CaseFixture())
    with pytest.raises(ValueError, match="complete prefix/revision boundary"):
        restored.restore_recovery_context(saved)


@pytest.mark.parametrize("mutation", ["null_review", "empty_batches", "missing_obligation"])
def test_revision_rejects_incomplete_or_malformed_terminal_review(tmp_path, mutation):
    source, destination, _, _, _, _ = _revision(tmp_path)
    path = source.output_dir / "reader_verification.json"
    verification = read_json(path)
    reader = verification["English"]
    if mutation == "null_review":
        reader["review"] = None
    elif mutation == "missing_obligation":
        reader["coverage_batches"][0]["issue_ids"].pop()
    else:
        reader["coverage_batches"] = []
        checkpoint_path = source.output_dir / "finalization_checkpoint.json"
        checkpoint = read_json(checkpoint_path)
        checkpoint["stages"] = {name: value for name, value in checkpoint["stages"].items()
                                if not name.startswith("verify_repaired_report-coverage-")}
        checkpoint_path.write_bytes(canonical_json(checkpoint))
    path.write_bytes(canonical_json(verification))
    with pytest.raises(ValueError):
        prepare_finalization_continuation(source.output_dir, destination, revise_reader=True)


def test_lineage_policy_is_exact_opt_in_and_never_resolves_history():
    original = limitation_packet([_RECONCILIATION_LINEAGE])
    original[0]["reader_coverage_required"] = True
    before = deepcopy(original)
    assert split_compound_obligations(original, {}) == original
    issues = split_compound_obligations(original, {}, reader_revision=True)
    assert original == before
    assert issues[0]["resolution_protected"] and not issues[0]["reader_coverage_required"]
    child = compound_coverage_issues(issues)[0]
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": child["issue_id"], "decision": "audit_only_operational",
        "rationale": "Immutable historical lineage and review reexecution control only.",
    }])
    combined = fan_in_compound_dispositions(review, issues, "Reader")
    assert validated_disposition_ids(combined, issues, "Reader") == (original[0]["issue_id"],)
    for material in (_RECONCILIATION_LINEAGE + " Funding remains uncertain.",
                     "Source excerpts are truncated and PDF extraction may lose table lineage."):
        packet = limitation_packet([material])
        packet[0]["reader_coverage_required"] = True
        assert split_compound_obligations(packet, {}, reader_revision=True) == packet
    for category in ("security", "numerical"):
        protected = deepcopy(original)
        protected[0]["prior_findings"] = [{"category": category, "severity": "critical"}]
        assert split_compound_obligations(protected, {}, reader_revision=True) == protected
