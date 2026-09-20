"""Offline finalization invariants; no fixture claims investment acceptance."""

from tests.test_research_bounded_finalization import BoundedFixture, run_fixture
from tradingagents.research.storage import read_json


def test_clean_coverage_reused_only_for_identical_reader_and_issue_payload(tmp_path):
    class SameReader(BoundedFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["text"] = "Unchanged reader."
            return reply

    models = SameReader(count=15, warning_once=True)
    request, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "completed_needs_review"
    assert any(p["stage"] == "verify_repaired_report" for _, p in models.calls)
    assert not any(p["stage"].startswith("verify_repaired_report-coverage-") for _, p in models.calls)
    metadata = read_json(request.output_dir / "run_metadata.json")
    reused = [row for stage, rows in metadata["usage_by_stage"].items()
              if stage.startswith("verify_repaired_report-coverage-") for row in rows]
    assert reused and all(row["usage_origin"] == "validated_reuse" for row in reused)
    assert all(row["input_tokens"] == row["output_tokens"] == 0 for row in reused)
    assert result.usage.total_tokens == len(models.calls) * 130


def test_invalid_coverage_is_not_reused_even_for_identical_reader(tmp_path):
    class BadCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["text"] = "Unchanged reader."
            if payload["stage"] == "verify_report-coverage-0":
                reply.data["limitation_dispositions"][0]["decision"] = "unresolved"
            return reply

    models = BadCoverage(count=15)
    _, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "completed_needs_review"
    repaired = [p["stage"] for _, p in models.calls if p["stage"].startswith("verify_repaired_report-coverage-")]
    assert repaired == ["verify_repaired_report-coverage-0"]


def test_saved_cost_plan_accounts_for_repair_and_exact_cached_coverage(tmp_path):
    models = BoundedFixture(count=15, warning_once=True)
    request, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "completed_needs_review"
    plans = read_json(request.output_dir / "finalization_plan.json")
    initial = plans["verify_report"]
    assert initial["reader_sha256"] != plans["verify_repaired_report"]["reader_sha256"]
    assert set(initial["remaining_workload"]["phases"]) == {
        "current_coverage", "possible_repair", "possible_repair_review", "possible_repair_coverage",
    }
    assert initial["remaining_workload"]["is_hard_spend_guarantee"] is False
    assert initial["remaining_workload"]["conservative_reserve_tokens"] > initial["current_pass"][
        "conservative_reserve_tokens"]
    assert plans["verify_repaired_report"]["current_pass"]["cached_call_count"] == 0


def _budget_stopped_case(tmp_path):
    from tests.test_research_case_engine import CaseFixture, case_setup
    from tradingagents.research.engine import run_research
    from tradingagents.research.storage import canonical_json

    class ManyGaps(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "business":
                reply.data["unresolved_gaps"] = [
                    f"Unresolved economic relationship {i}: " + "dated supporting evidence required. " * 4
                    for i in range(80)]
            return reply

    baseline_models = ManyGaps()
    baseline, services = case_setup(tmp_path / "baseline", baseline_models)
    run_research(baseline, services)
    prefix = [payload for _, payload in baseline_models.calls
              if not payload["stage"].startswith("verify_report-coverage-")]
    # Admit each known prefix payload, but not the entire much larger coverage pass.
    largest = max(len(canonical_json({k: v for k, v in payload.items()
                                     if k not in {"timeout_seconds", "max_output_tokens"}})) + 16_000
                  for payload in prefix)
    models = ManyGaps()
    request, services = case_setup(tmp_path / "bounded", models)
    request = request.model_copy(update={"budget": request.budget.model_copy(update={
        "total_tokens": largest + 10_000, "reserve_tokens": 0,
    })})
    result = run_research(request, services)
    return request, services, models, result


def test_coverage_pass_budget_stop_preserves_candidate_without_partial_dispatch(tmp_path):
    request, _, models, result = _budget_stopped_case(tmp_path)
    assert result.stop_reason == "finalization_pass_budget_insufficient"
    assert result.usage.complete
    assert not any("-coverage-" in payload["stage"] for _, payload in models.calls)
    checkpoint = read_json(request.output_dir / "finalization_checkpoint.json")
    assert checkpoint["candidate_review_stage"] == "verify_report"
    assert checkpoint["stages"]["verify_report"]["output"]["reviewed_report"]
    assert checkpoint["usage"]["complete"] and not checkpoint["dispatched"]
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "incomplete"


def test_candidate_continuation_reuses_analysis_editor_and_factual_review(tmp_path):
    from datetime import datetime, timezone

    from tests.test_research_case_engine import CaseFixture
    from tradingagents.research.engine import run_research
    from tradingagents.research.finalization_recovery import (
        FinalizationRecoveryAuthorization,
        FinalizationRecoveryModelService,
        authorize_finalization_continuation,
        prepare_finalization_continuation,
    )
    from tradingagents.research.services import ResearchServices

    source, services, _, stopped = _budget_stopped_case(tmp_path)
    destination = source.model_copy(update={
        "output_dir": tmp_path / "continued",
        "budget": source.budget.model_copy(update={"total_tokens": 3_000_000}),
    })
    before = (source.output_dir / "finalization_checkpoint.json").read_bytes()
    plan = prepare_finalization_continuation(source.output_dir, destination)
    authorization = FinalizationRecoveryAuthorization(
        authorization_id="offline-fixture-not-live-approval", authorized_at=datetime.now(timezone.utc),
        plan_sha256=plan.plan_sha256, new_request_identity=plan.new_request_identity,
        incremental_budget=destination.budget, authorize_live_continuation=True,
    )
    # A fake provider proves dispatch selection without performing any live call.
    provider = CaseFixture()
    continuation = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    result = run_research(destination, ResearchServices(services.evidence, continuation))
    assert result.stop_reason == "completed_needs_review"
    stages = [payload["stage"] for _, payload in provider.calls]
    assert stages and all(stage.startswith("verify_report-coverage-") for stage in stages)
    assert result.usage.total_tokens == stopped.usage.total_tokens + len(stages) * 130
    assert result.usage.complete
    assert (source.output_dir / "finalization_checkpoint.json").read_bytes() == before
    metadata = read_json(destination.output_dir / "run_metadata.json")
    assert metadata["incremental_usage"]["input_tokens"] == len(stages) * 100
    assert metadata["usage_total_unknown"] is False


def test_section_only_sources_cannot_pass_as_paragraph_citations(tmp_path):
    from tests.test_research_case_engine import CaseFixture, case_setup
    from tradingagents.research.engine import run_research

    class NoParagraphSources(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["evidence_ids"] = ["filing"]
            return reply

    request, services = case_setup(tmp_path, NoParagraphSources())
    result = run_research(request, services)
    assert result.stop_reason == "verification_failed"
    assert any(f.code == "paragraph_citations_missing" for f in result.assessment.findings)
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "incomplete"


def test_compact_author_limitations_get_final_reader_bound_audit_dispositions(tmp_path):
    from tests.test_research_case_engine import case_setup
    from tradingagents.research.engine import run_research

    request, services = case_setup(tmp_path)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    audit = read_json(request.output_dir / "reader_limitations.json")
    authored = audit["draft_limitations"]["consolidated_exact_text"]
    assert authored and all(item["reader_display"] == "verified_editorial_representation" for item in authored)
    assert all(item["verified_disposition"]["decision"] == "reader_covered" for item in authored)


def test_continuation_resume_after_paid_reply_before_stage_save_never_redispatches(tmp_path):
    from datetime import datetime, timezone

    from tests.test_research_case_engine import CaseFixture
    from tradingagents.research.engine import run_research
    from tradingagents.research.finalization_recovery import (
        FinalizationRecoveryAuthorization,
        FinalizationRecoveryModelService,
        authorize_finalization_continuation,
        prepare_finalization_continuation,
    )
    from tradingagents.research.services import ResearchServices
    from tradingagents.research.storage import CheckpointStore

    class LostStageOutput(CheckpointStore):
        def save_stage(self, stage, inputs, output):
            if stage.startswith("verify_report-coverage-"):
                raise OSError("synthetic interruption after paid reply")
            return super().save_stage(stage, inputs, output)

    source, services, _, stopped = _budget_stopped_case(tmp_path)
    destination = source.model_copy(update={
        "output_dir": tmp_path / "continued",
        "budget": source.budget.model_copy(update={"total_tokens": 3_000_000}),
    })
    plan = prepare_finalization_continuation(source.output_dir, destination)
    authorization = FinalizationRecoveryAuthorization(
        authorization_id="offline-crash-fixture", authorized_at=datetime.now(timezone.utc),
        plan_sha256=plan.plan_sha256, new_request_identity=plan.new_request_identity,
        incremental_budget=destination.budget, authorize_live_continuation=True,
    )
    authorized = authorize_finalization_continuation(plan, authorization)
    first_provider = CaseFixture()
    interrupted = run_research(destination, ResearchServices(
        services.evidence, FinalizationRecoveryModelService(authorized, first_provider),
        storage=LostStageOutput,
    ))
    assert interrupted.stop_reason == "stage_failed"
    assert len(first_provider.calls) == 1
    assert interrupted.usage.total_tokens == stopped.usage.total_tokens + 130
    resumed_provider = CaseFixture()
    resumed = run_research(destination, ResearchServices(
        services.evidence, FinalizationRecoveryModelService(authorized, resumed_provider),
    ))
    assert resumed.stop_reason == "stage_failed"
    assert not resumed_provider.calls
    assert resumed.usage == interrupted.usage


def test_compact_protected_prerequisites_cannot_be_moved_to_audit_only(tmp_path):
    from tests.test_research_case_engine import CaseFixture, case_setup
    from tradingagents.research.engine import run_research

    class HidePrerequisites(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if "-coverage-" in payload["stage"]:
                for disposition in reply.data["limitation_dispositions"]:
                    disposition.update(decision="audit_only_immaterial", reader_excerpt="")
            return reply

    request, services = case_setup(tmp_path, HidePrerequisites())
    result = run_research(request, services)
    assert result.stop_reason == "verification_failed"
    assert any("Protected limitation requires reader coverage" in f.message for f in result.assessment.findings)
