import copy
import fcntl
import hashlib
import os

import pytest

from scripts import research_case_recovery
from tests.test_research_case_engine import CaseFixture, case_setup
from tradingagents.research.case_recovery import (
    CASE_RECOVERY_IMPORTED_STAGES,
    CaseRecoveryAuthorization,
    CaseRecoveryModelService,
    authorize_case_recovery,
    continuation_request_identity,
    prepare_case_recovery,
    verify_case_recovery_prefix,
    write_case_recovery_capsule,
)
from tradingagents.research.contracts import Budget, EvidenceSnapshot, Usage
from tradingagents.research.engine import run_research
from tradingagents.research.models import CodexModelService
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_json,
)


def _checkpoint(identity, output):
    return canonical_json(
        {
            "schema_version": 1,
            "identity": identity,
            "inputs_hash": digest({}),
            "output": output,
            "output_hash": digest(output),
        }
    )


def build_case_recovery_source(tmp_path, *, policy="legacy-12"):
    fixture_root = tmp_path / "fixture"
    request, services = case_setup(fixture_root)
    home = tmp_path / "codex-home"
    home.mkdir()
    source_model_identity = CodexModelService(home).identity
    services.models.identity = source_model_identity
    full_run = tmp_path / "full-run"
    full_request = request.model_copy(
        update={"backend": "codex", "output_dir": full_run, "dossier_dir": None,
                "coverage_batch_policy": policy}
    )
    result = run_research(full_request, services)
    assert result.stop_reason == "completed_needs_review"

    source_run = tmp_path / "source-run"
    (source_run / "stages").mkdir(parents=True)
    (source_run / ".research.lock").touch()
    for name in (
        "research_checkpoint.json",
        "stages/evidence.json",
        *(f"stages/{stage}.json" for stage in CASE_RECOVERY_IMPORTED_STAGES),
    ):
        atomic_write(source_run / name, (full_run / name).read_bytes())

    identity = read_json(source_run / "research_checkpoint.json")["identity"]
    by_stage = {}
    for stage in CASE_RECOVERY_IMPORTED_STAGES:
        role = "challenger" if stage == "independent_challenge" else stage
        setting = full_request.models[role]
        by_stage[stage] = [
            {
                "role": role,
                "model": setting.model,
                "effort": setting.effort,
                "usage_origin": "current_live",
                **Usage(input_tokens=100, output_tokens=30).model_dump(mode="json"),
            }
        ]
    resources = {
        "usage": Usage(input_tokens=600, output_tokens=180).model_dump(mode="json"),
        "elapsed_seconds": 1138.7482698748354,
        "dispatched": True,
        "by_stage": by_stage,
    }
    atomic_write(source_run / "stages/resources.json", _checkpoint(identity, resources))
    source_request = full_request.model_copy(update={"output_dir": source_run})
    return source_request, source_run, home


@pytest.fixture
def case_recovery_source(tmp_path):
    return build_case_recovery_source(tmp_path)


def _verified(case_recovery_source):
    request, source_run, home = case_recovery_source
    prepared = prepare_case_recovery(request, source_run, home)
    return verify_case_recovery_prefix(prepared)


def test_case_recovery_accepts_safe_diagnostic_and_rejects_extra_fields(case_recovery_source):
    request, source_run, home = case_recovery_source
    path = source_run / "stages/resources.json"
    record = read_json(path)
    assert "failure_diagnostic" not in record["output"]  # Legacy source remains valid.
    prepare_case_recovery(request, source_run, home)

    record["output"]["failure_reason"] = "transport_rpc_rejected"
    record["output"]["failure_diagnostic"] = {
        "kind": "rpc_rejection", "phase": "turn_start", "method": "turn/start", "code": -32000,
    }
    record["output_hash"] = digest(record["output"])
    atomic_write(path, canonical_json(record))
    prepare_case_recovery(request, source_run, home)

    record["output"]["failure_diagnostic"]["raw_error"] = "private-provider-SECRET"
    record["output_hash"] = digest(record["output"])
    atomic_write(path, canonical_json(record))
    with pytest.raises(ValueError, match="resource checkpoint shape"):
        prepare_case_recovery(request, source_run, home)


def _continuation(verified, tmp_path):
    source_request = verified.prepared.source_request
    budget = Budget(
        wall_seconds=20_000,
        total_tokens=5_000_000,
        reserve_seconds=1200,
        reserve_tokens=300_000,
        call_timeout_seconds=600,
        followup_cycles=0,
    )
    return source_request.model_copy(
        update={"output_dir": tmp_path / "continued", "budget": budget}
    )


def _authorization(verified, request):
    return CaseRecoveryAuthorization(
        authorization_id="synthetic-new-incremental-budget",
        authorized_at="2026-09-19T12:00:00Z",
        plan_sha256=verified.plan_sha256,
        continuation_request_identity=continuation_request_identity(verified, request),
        incremental_budget=request.budget,
        acknowledge_source_usage_incomplete=True,
        authorize_live_continuation=True,
    )


def _source_bytes(verified):
    prepared = verified.prepared
    return {
        **{
            f"run/{name}": (prepared.source_run / name).read_bytes()
            for name in prepared.source_artifact_hashes
        },
        **{
            f"input/{name}": getattr(prepared.source_request, name).read_bytes()
            for name in prepared.frozen_inputs
        },
    }


class UnsettledContinuationService:
    supports_hard_output_cap = False

    def __init__(self, mode):
        self.mode = mode
        self.identity = digest({"service": "synthetic-unsettled-continuation", "mode": mode})
        self.calls = []

    def complete(self, role, payload, request):
        self.calls.append((role, copy.deepcopy(payload)))
        if self.mode == "raises":
            raise RuntimeError("synthetic continuation failure")
        return ModelReply(
            data={},
            usage=Usage(input_tokens=7, output_tokens=2, complete=False),
        )


def test_current_engine_verifies_exact_six_stage_case_prefix(case_recovery_source):
    request, source_run, home = case_recovery_source
    prepared = prepare_case_recovery(request, source_run, home)
    verified = verify_case_recovery_prefix(prepared)

    assert tuple(item.stage for item in prepared.imported_stages) == (
        "independent_challenge",
        "planner",
        "business",
        "accounting",
        "expectations",
        "management",
    )
    assert verified.next_stage == "reconcile_challenge"
    assert verified.next_role == "challenger"
    assert prepared.source_known_usage.total_tokens == 780
    assert prepared.source_known_usage.complete
    assert not prepared.source_usage.complete
    assert not (source_run / "result.json").exists()


def test_capsule_copies_exact_inputs_and_checkpoints_and_requests_authorization(
    case_recovery_source, tmp_path
):
    verified = _verified(case_recovery_source)
    destination = tmp_path / "capsule"
    capsule = write_case_recovery_capsule(verified, destination)
    manifest = read_json(destination / "manifest.json")
    request = read_json(destination / "authorization_request.json")

    assert capsule.plan_sha256 == verified.plan_sha256
    assert manifest["plan_sha256"] == verified.plan_sha256
    assert request["status"] == "explicit_authorization_required"
    assert request["incremental_budget"] is None
    assert request["continuation_request_identity"] is None
    assert request["unknown_dispatched_call_usage"] == "unknown_not_zero"
    assert "upper_bound" not in canonical_json(request).decode()
    for name, expected_hash in manifest["capsule_files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected_hash
    for name, expected in verified.prepared.source_checkpoint_bytes.items():
        assert (destination / "source" / name).read_bytes() == expected
    for name, expected in verified.prepared.frozen_inputs.items():
        assert (destination / "inputs" / f"{name}.json").read_bytes() == expected


def test_authorized_service_uses_zero_incremental_seed_and_preserves_unknown_total(
    case_recovery_source, tmp_path
):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)
    authorized = authorize_case_recovery(verified, authorization, request)
    live = CaseFixture()
    live.identity = digest("synthetic-live-continuation")
    service = CaseRecoveryModelService(authorized, live)
    context = service.case_recovery_context

    assert context["initial_budget_usage"] == Usage().model_dump(mode="json")
    assert context["previous_elapsed_seconds"] == 0
    assert context["previous_usage"]["input_tokens"] == 600
    assert context["previous_usage"]["output_tokens"] == 180
    assert context["previous_usage"]["complete"] is False
    assert context["source_known_usage"]["complete"] is True
    assert context["authorization"]["incremental_budget"] == request.budget.model_dump(mode="json")
    assert not hasattr(service, "recovery_context")

    snapshot = EvidenceSnapshot.model_validate(
        parse_json(verified.prepared.frozen_inputs["evidence_path"])
    )
    result = run_research(
        request,
        ResearchServices(SnapshotEvidenceService(snapshot), service),
    )
    assert result.stop_reason == "completed_needs_review"
    assert not result.usage.complete
    assert result.usage.total_tokens == 780 + len(live.calls) * 130
    assert live.calls[0][1]["stage"] == "reconcile_challenge"
    assert all(payload["stage"] not in CASE_RECOVERY_IMPORTED_STAGES for _, payload in live.calls)
    metadata = read_json(request.output_dir / "run_metadata.json")
    assert metadata["known_token_lower_bound"] == result.usage.total_tokens
    assert metadata["usage_total_unknown"] is True
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert resources["budget_usage"]["input_tokens"] == len(live.calls) * 100
    assert resources["budget_usage"]["output_tokens"] == len(live.calls) * 30
    assert resources["usage"]["input_tokens"] == 600 + len(live.calls) * 100
    assert resources["usage"]["output_tokens"] == 180 + len(live.calls) * 30
    assert resources["usage"]["complete"] is False


def test_completed_continuation_replay_returns_saved_result_without_recharging_prefix(
    case_recovery_source, tmp_path
):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)
    authorized = authorize_case_recovery(verified, authorization, request)
    source_before = _source_bytes(verified)
    live_identity = digest("synthetic-completed-continuation")
    first_live = CaseFixture()
    first_live.identity = live_identity
    first = run_research(
        request,
        ResearchServices(
            SnapshotEvidenceService(
                EvidenceSnapshot.model_validate(
                    parse_json(verified.prepared.frozen_inputs["evidence_path"])
                )
            ),
            CaseRecoveryModelService(authorized, first_live),
        ),
    )
    resources_before = (request.output_dir / "stages/resources.json").read_bytes()

    replay_live = CaseFixture()
    replay_live.identity = live_identity
    replayed = run_research(
        request,
        ResearchServices(
            SnapshotEvidenceService(
                EvidenceSnapshot.model_validate(
                    parse_json(verified.prepared.frozen_inputs["evidence_path"])
                )
            ),
            CaseRecoveryModelService(authorized, replay_live),
        ),
    )

    assert first.stop_reason == "completed_needs_review"
    assert replayed == first
    assert not replay_live.calls
    assert (request.output_dir / "stages/resources.json").read_bytes() == resources_before
    resources = parse_json(resources_before)["output"]
    for stage in CASE_RECOVERY_IMPORTED_STAGES:
        assert len(resources["by_stage"][stage]) == 1
        assert resources["by_stage"][stage][0]["usage_origin"] == "imported_historical"
    assert _source_bytes(verified) == source_before


@pytest.mark.parametrize(
    ("mode", "first_stop_reason"),
    [("unknown_usage", "usage_incomplete"), ("raises", "stage_failed")],
)
def test_unsettled_continuation_cannot_dispatch_again_on_same_authorized_resume(
    case_recovery_source, tmp_path, mode, first_stop_reason
):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)
    authorized = authorize_case_recovery(verified, authorization, request)
    snapshot = EvidenceSnapshot.model_validate(
        parse_json(verified.prepared.frozen_inputs["evidence_path"])
    )
    source_before = _source_bytes(verified)
    first_live = UnsettledContinuationService(mode)
    first = run_research(
        request,
        ResearchServices(
            SnapshotEvidenceService(snapshot),
            CaseRecoveryModelService(authorized, first_live),
        ),
    )

    assert first.stop_reason == first_stop_reason
    assert not first.usage.complete
    assert len(first_live.calls) == 1
    assert first_live.calls[0][1]["stage"] == "reconcile_challenge"

    resumed_live = UnsettledContinuationService(mode)
    resumed = run_research(
        request,
        ResearchServices(
            SnapshotEvidenceService(snapshot),
            CaseRecoveryModelService(authorized, resumed_live),
        ),
    )

    assert resumed.stop_reason == "usage_incomplete"
    assert not resumed.usage.complete
    assert not resumed_live.calls
    assert _source_bytes(verified) == source_before


def test_source_tamper_and_unsettled_flag_changes_are_rejected(case_recovery_source):
    request, source_run, home = case_recovery_source
    stage = source_run / "stages/business.json"
    original = stage.read_bytes()
    record = parse_json(original)
    record["output_hash"] = "0" * 64
    atomic_write(stage, canonical_json(record))
    with pytest.raises(ValueError, match="checkpoint identity or hash"):
        prepare_case_recovery(request, source_run, home)

    atomic_write(stage, original)
    resources_path = source_run / "stages/resources.json"
    resources = read_json(resources_path)
    resources["output"]["dispatched"] = False
    resources["output_hash"] = digest(resources["output"])
    atomic_write(resources_path, canonical_json(resources))
    with pytest.raises(ValueError, match="unsettled dispatch"):
        prepare_case_recovery(request, source_run, home)


def test_changed_case_model_or_budget_authorization_is_rejected(case_recovery_source, tmp_path):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)

    changed_case = read_json(request.financial_case_path)
    changed_case["case"]["schedules"][0]["rationale"] += " changed after review"
    changed_path = tmp_path / "changed-case.json"
    atomic_write(changed_path, canonical_json(changed_case))
    changed_request = request.model_copy(update={"financial_case_path": changed_path})
    with pytest.raises(ValueError, match="frozen evidence or reviewed case"):
        authorize_case_recovery(verified, authorization, changed_request)

    settings = dict(request.models)
    settings["business"] = settings["business"].model_copy(update={"effort": "medium"})
    changed_model = request.model_copy(update={"models": settings})
    with pytest.raises(ValueError, match="research settings"):
        authorize_case_recovery(verified, authorization, changed_model)

    changed_budget = request.model_copy(
        update={"budget": request.budget.model_copy(update={"total_tokens": 4_999_999})}
    )
    with pytest.raises(ValueError, match="incremental budget"):
        authorize_case_recovery(verified, authorization, changed_budget)

    other_destination = request.model_copy(update={"output_dir": tmp_path / "continued-again"})
    with pytest.raises(ValueError, match="identity"):
        authorize_case_recovery(verified, authorization, other_destination)


def test_strict_authorization_and_mutated_plan_copies_are_rejected(case_recovery_source, tmp_path):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    payload = {
        "authorization_id": "strict-booleans",
        "authorized_at": "2026-09-19T12:00:00Z",
        "plan_sha256": verified.plan_sha256,
        "continuation_request_identity": continuation_request_identity(verified, request),
        "incremental_budget": request.budget.model_dump(mode="json"),
        "acknowledge_source_usage_incomplete": 1,
        "authorize_live_continuation": True,
    }
    with pytest.raises(ValueError, match="boolean true"):
        CaseRecoveryAuthorization.model_validate(payload)

    authorization = _authorization(verified, request)
    invalid_copy = authorization.model_copy(update={"authorize_live_continuation": False})
    with pytest.raises(ValueError, match="boolean true"):
        authorize_case_recovery(verified, invalid_copy, request)

    changed_plan = copy.deepcopy(verified)
    changed_plan.prepared.imported_stages[0].output["unresolved_gaps"] = [
        "mutated after verification"
    ]
    with pytest.raises(ValueError, match="plan content changed"):
        authorize_case_recovery(changed_plan, authorization, request)


def test_capsule_revalidates_nested_source_request_before_writing(case_recovery_source, tmp_path):
    verified = _verified(case_recovery_source)
    original_plan_sha256 = verified.plan_sha256
    changed_plan = copy.deepcopy(verified)
    changed_plan.prepared.source_request.models["business"] = (
        changed_plan.prepared.source_request.models["business"].model_copy(
            update={"effort": "medium"}
        )
    )
    destination = tmp_path / "mutated-request-capsule"

    assert changed_plan.plan_sha256 == original_plan_sha256
    with pytest.raises(ValueError, match="source identity|plan content changed"):
        write_case_recovery_capsule(changed_plan, destination)
    assert not destination.exists()


def test_capsule_and_continuation_outputs_cannot_overlap_sources(case_recovery_source, tmp_path):
    verified = _verified(case_recovery_source)
    request, source_run, _ = case_recovery_source
    with pytest.raises(ValueError, match="under source"):
        write_case_recovery_capsule(verified, source_run / "capsule")
    with pytest.raises(ValueError, match="under source"):
        write_case_recovery_capsule(verified, request.financial_case_path.parent / "capsule")
    existing = tmp_path / "existing-capsule"
    existing.mkdir()
    with pytest.raises(ValueError, match="must not exist"):
        write_case_recovery_capsule(verified, existing)

    nested_output = request.model_copy(
        update={
            "output_dir": source_run / "continued",
            "budget": _continuation(verified, tmp_path).budget,
        }
    )
    nested_identity = digest(
        {
            "request_identity": digest("irrelevant"),
            "output_dir": str(nested_output.output_dir.resolve()),
        }
    )
    authorization = CaseRecoveryAuthorization(
        authorization_id="nested-output",
        authorized_at="2026-09-19T12:00:00Z",
        plan_sha256=verified.plan_sha256,
        continuation_request_identity=nested_identity,
        incremental_budget=nested_output.budget,
        acknowledge_source_usage_incomplete=True,
        authorize_live_continuation=True,
    )
    with pytest.raises(ValueError, match="overlaps source"):
        authorize_case_recovery(verified, authorization, nested_output)


def test_source_rechecked_before_any_current_live_call(case_recovery_source, tmp_path):
    verified = _verified(case_recovery_source)
    request = _continuation(verified, tmp_path)
    authorization = _authorization(verified, request)
    authorized = authorize_case_recovery(verified, authorization, request)
    live = CaseFixture()
    live.identity = digest("synthetic-live-continuation")
    service = CaseRecoveryModelService(authorized, live)
    source_stage = verified.prepared.source_run / "stages/management.json"
    record = read_json(source_stage)
    record["output_hash"] = "0" * 64
    atomic_write(source_stage, canonical_json(record))
    snapshot = EvidenceSnapshot.model_validate(
        parse_json(verified.prepared.frozen_inputs["evidence_path"])
    )

    with pytest.raises(ValueError, match="source or frozen inputs changed"):
        run_research(
            request,
            ResearchServices(SnapshotEvidenceService(snapshot), service),
        )
    assert not live.calls


def test_source_lock_is_respected(case_recovery_source):
    request, source_run, home = case_recovery_source
    descriptor = os.open(source_run / ".research.lock", os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="in use"):
            prepare_case_recovery(request, source_run, home)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_source_lock_fifo_is_opened_nonblocking_and_rejected(case_recovery_source, monkeypatch):
    request, source_run, home = case_recovery_source
    lock_path = source_run / ".research.lock"
    lock_path.unlink()
    os.mkfifo(lock_path)
    real_open = os.open

    def require_nonblocking(path, flags, *args, **kwargs):
        if os.fspath(path) == os.fspath(lock_path):
            assert flags & os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", require_nonblocking)
    with pytest.raises(ValueError, match="lock must be a regular file"):
        prepare_case_recovery(request, source_run, home)


def test_offline_capsule_cli_dry_run_and_prepare(case_recovery_source, tmp_path, capsys):
    request, source_run, home = case_recovery_source
    config = tmp_path / "source-request.json"
    atomic_write(config, canonical_json(request))
    dry_output = tmp_path / "dry-capsule"
    common = [
        "--config",
        str(config),
        "--source-run",
        str(source_run),
        "--codex-home",
        str(home),
    ]
    assert research_case_recovery.main([*common, "--output", str(dry_output), "--dry-run"]) == 0
    dry_payload = parse_json(capsys.readouterr().out.encode())
    assert dry_payload["status"] == "validated_offline_no_capsule_written"
    assert dry_payload["source_usage_complete"] is False
    assert not dry_output.exists()

    output = tmp_path / "prepared-capsule"
    assert research_case_recovery.main([*common, "--output", str(output)]) == 0
    payload = parse_json(capsys.readouterr().out.encode())
    assert payload["status"] == "capsule_prepared_authorization_required"
    assert payload["live_dispatch"] is False
    assert (output / "manifest.json").is_file()
