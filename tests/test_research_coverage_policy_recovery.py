from datetime import datetime, timezone

import pytest

from tests.test_research_case_engine import CaseFixture
from tests.test_research_case_recovery import (
    _authorization,
    _continuation,
    _verified,
    build_case_recovery_source,
)
from tests.test_research_finalization_engine import _budget_stopped_case
from tests.test_research_packed_coverage_engine import run_policy
from tradingagents.research.case_recovery import (
    CaseRecoveryModelService,
    authorize_case_recovery,
)
from tradingagents.research.contracts import EvidenceSnapshot
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryAuthorization,
    FinalizationRecoveryModelService,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
)
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import atomic_write, canonical_json, read_json

POLICIES = [("legacy-12", "packed-24"), ("packed-24", "legacy-12")]


@pytest.mark.parametrize("policy,changed", POLICIES)
def test_ordinary_resume_rejects_policy_change_before_model_call(tmp_path, policy, changed):
    from tests.test_research_bounded_finalization import BoundedFixture

    source, services, result = run_policy(tmp_path, policy, BoundedFixture(count=25))
    assert result.stop_reason == "completed_needs_review"
    live = BoundedFixture()
    with pytest.raises(ValueError, match="incompatible research checkpoint"):
        run_research(source.model_copy(update={"coverage_batch_policy": changed}),
                     ResearchServices(services.evidence, live))
    assert not live.calls


@pytest.mark.parametrize("policy,changed", POLICIES)
def test_finalization_plan_cannot_mix_policies(tmp_path, policy, changed):
    source, _, _, result = _budget_stopped_case(tmp_path, policy=policy)
    assert result.stop_reason == "finalization_pass_budget_insufficient"
    with pytest.raises(ValueError, match="settings"):
        prepare_finalization_continuation(source.output_dir, source.model_copy(update={
            "output_dir": tmp_path / "continued", "coverage_batch_policy": changed,
        }))
    # Planning has no provider dependency and cannot cross a live boundary.


@pytest.mark.parametrize("policy", ["legacy-12", "packed-24"])
def test_same_policy_continuation_reuses_verified_prefix(tmp_path, policy):
    source, services, _, result = _budget_stopped_case(tmp_path, policy=policy)
    assert result.stop_reason == "finalization_pass_budget_insufficient"
    if policy == "legacy-12":
        # Historical request JSON predates the opt-in; absence must mean legacy.
        path = source.output_dir / "finalization_checkpoint.json"
        old = read_json(path)
        old["request"].pop("coverage_batch_policy")
        atomic_write(path, canonical_json(old))
    destination = source.model_copy(update={"output_dir": tmp_path / "continued",
        "budget": source.budget.model_copy(update={"total_tokens": 3_000_000})})
    plan = prepare_finalization_continuation(source.output_dir, destination)
    authorization = FinalizationRecoveryAuthorization(
        authorization_id="synthetic-policy-test-not-live-approval",
        authorized_at=datetime.now(timezone.utc), plan_sha256=plan.plan_sha256,
        new_request_identity=plan.new_request_identity, incremental_budget=destination.budget,
        authorize_live_continuation=True,
    )
    live = CaseFixture()
    recovery = FinalizationRecoveryModelService(authorize_finalization_continuation(plan, authorization), live)
    continued = run_research(destination, ResearchServices(services.evidence, recovery))
    assert continued.stop_reason == "completed_needs_review"
    assert live.calls and all(p["stage"].startswith("verify_report-coverage-") for _, p in live.calls)
    assert continued.usage.total_tokens == result.usage.total_tokens + len(live.calls) * 130


@pytest.mark.parametrize("policy,changed", POLICIES)
def test_case_recovery_blocks_policy_change_before_live_dispatch(tmp_path, policy, changed):
    source = build_case_recovery_source(tmp_path, policy=policy)
    verified = _verified(source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)
    authorized = authorize_case_recovery(verified, authorization, request)
    live = CaseFixture()
    recovery = CaseRecoveryModelService(authorized, live)
    evidence = SnapshotEvidenceService(EvidenceSnapshot.model_validate(read_json(request.evidence_path)))
    with pytest.raises(ValueError, match="settings"):
        run_research(request.model_copy(update={"coverage_batch_policy": changed}),
                     ResearchServices(evidence, recovery))
    assert not live.calls
