import hashlib
from collections import defaultdict
from copy import deepcopy

import pytest

from scripts import research_recover
from tests.test_research_engine import replies
from tradingagents.codex.adapter import CodexStructuredOutputError
from tradingagents.research.budget import BudgetExhausted, BudgetTracker
from tradingagents.research.contracts import (
    Budget,
    EvidenceSnapshot,
    ResearchRequest,
    SourceDocument,
    Usage,
)
from tradingagents.research.engine import run_research
from tradingagents.research.recovery import (
    RecoveryModelService,
    assert_source_unchanged,
    source_artifact_hashes,
    validate_recovery_source,
)
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import (
    CheckpointStore,
    atomic_write,
    canonical_json,
    digest,
    read_json,
    request_identity,
)
from tradingagents.research.wire import WIRE_SCHEMA_VERSION


class SourceFailureModel:
    kind = "codex"
    supports_hard_output_cap = False

    def __init__(self, request, home):
        self.identity = digest(
            {
                "service": "isolated-codex-v1",
                "wire": "research-wire-v1",
                "home": str(home.resolve()),
            }
        )
        self.responses = replies()
        self.responses["editor"].append(deepcopy(self.responses["editor"][0]))
        self.responses["verifier"].append(deepcopy(self.responses["verifier"][1]))
        self.counts = defaultdict(int)
        self.calls = []

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        if role == "valuation":
            raise CodexStructuredOutputError("synthetic schema rejection")
        index = self.counts[role]
        self.counts[role] += 1
        return ModelReply.model_validate(self.responses[role][index])


class CurrentModels:
    supports_hard_output_cap = False

    def __init__(self, *, unknown_valuation=False):
        self.identity = digest({"service": "synthetic-current-models-v1"})
        self.calls = []
        self.counts = defaultdict(int)
        self.responses = replies()
        self.responses["editor"].append(deepcopy(self.responses["editor"][0]))
        self.responses["verifier"].append(deepcopy(self.responses["verifier"][1]))
        self.unknown_valuation = unknown_valuation

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        if role == "valuation" and self.unknown_valuation:
            return ModelReply(
                data={"unsupported_inputs": ["unknown current usage"]},
                usage=Usage(input_tokens=7, output_tokens=2, complete=False),
            )
        index = payload.get("role_call_index", self.counts[role])
        self.counts[role] += 1
        return ModelReply.model_validate(self.responses[role][index])


def recovery_models(plan, live):
    return RecoveryModelService(
        plan,
        live,
        allow_live=True,
        acknowledge_unknown_usage=True,
    )


@pytest.fixture
def recovery_source(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    evidence_path = tmp_path / "evidence.json"
    source = SourceDocument(
        id="source",
        url="https://example.com/source",
        title="Source",
        publisher="Synthetic",
        retrieved_at="2026-09-16T00:00:00Z",
        published_at="2026-09-15T00:00:00Z",
        content="Eligible source text",
        content_sha256=hashlib.sha256(b"Eligible source text").hexdigest(),
    )
    snapshot = EvidenceSnapshot(
        ticker="NVDA",
        cutoff="2026-09-17T00:00:00Z",
        sources=(source,),
    )
    atomic_write(evidence_path, canonical_json(snapshot))
    source_run = tmp_path / "source-run"
    request = ResearchRequest(
        ticker="NVDA",
        cutoff=snapshot.cutoff,
        backend="codex",
        output_dir=source_run,
        evidence_path=evidence_path,
        report_language="English",
        additional_report_languages=("Chinese",),
        budget=Budget(
            wall_seconds=5400,
            total_tokens=1_500_000,
            reserve_seconds=1200,
            reserve_tokens=300_000,
            call_timeout_seconds=300,
            followup_cycles=0,
        ),
    )
    model = SourceFailureModel(request, home)
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), model))
    assert result.stop_reason == "stage_failed"
    assert not result.usage.complete
    assert [role for role, _ in model.calls] == [
        "planner",
        "challenger",
        "business",
        "accounting",
        "expectations",
        "management",
        "valuation",
    ]
    return request, source_run, home


def make_diagnostic(tmp_path, plan, *, elapsed=17.5):
    directory = tmp_path / "valuation-diagnostic"
    directory.mkdir()
    reply = ModelReply(
        data={"unsupported_inputs": ["Synthetic fixture has no financials"]},
        usage=Usage(input_tokens=40, output_tokens=10),
    )
    reply_bytes = canonical_json(reply)
    atomic_write(directory / "valuation_reply.json", reply_bytes)
    atomic_write(
        directory / "diagnostic.json",
        canonical_json(
            {
                "mode": "one_valuation_call",
                "status": "succeeded",
                "usage": reply.usage.model_dump(mode="json"),
                "elapsed_seconds": elapsed,
            }
        ),
    )
    atomic_write(
        directory / "provenance.json",
        canonical_json(
            {
                "schema_version": 1,
                "role": "valuation",
                "payload_hash": digest(plan.valuation_payload),
                "request_identity": plan.source_request_identity,
                "source_artifact_hashes": plan.source_artifact_hashes,
                "wire_schema_version": WIRE_SCHEMA_VERSION,
                "reply_sha256": hashlib.sha256(reply_bytes).hexdigest(),
            }
        ),
    )
    return directory


def test_recovery_service_requires_explicit_api_authorization(recovery_source):
    request, source_run, home = recovery_source
    plan = validate_recovery_source(request, source_run, home)
    with pytest.raises(ValueError, match="explicit live and unknown-usage authorization"):
        RecoveryModelService(plan, CurrentModels())


def test_legacy_recovery_accepts_safe_diagnostic_and_rejects_extra_fields(recovery_source):
    request, source_run, home = recovery_source
    path = source_run / "stages/resources.json"
    record = read_json(path)
    assert validate_recovery_source(request, source_run, home)

    record["output"]["failure_reason"] = "transport_request_size_limit"
    record["output"]["failure_diagnostic"] = {
        "kind": "request_size_limit", "phase": "turn_start",
        "request_bytes": 5_000_000, "limit_bytes": 4_194_304,
    }
    record["output_hash"] = digest(record["output"])
    atomic_write(path, canonical_json(record))
    assert validate_recovery_source(request, source_run, home)

    record["output"]["failure_diagnostic"]["raw_error"] = "private-provider-SECRET"
    record["output_hash"] = digest(record["output"])
    atomic_write(path, canonical_json(record))
    with pytest.raises(ValueError, match="resources checkpoint shape"):
        validate_recovery_source(request, source_run, home)


def test_recovery_imports_prefix_without_live_calls_and_preserves_unknown_usage(
    recovery_source, tmp_path
):
    request, source_run, home = recovery_source
    before = source_artifact_hashes(source_run)
    initial = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, initial)
    plan = validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic)
    live = CurrentModels()
    recovered_request = request.model_copy(update={"output_dir": tmp_path / "recovered"})
    result = run_research(
        recovered_request,
        ResearchServices(SnapshotEvidenceService(plan.snapshot), recovery_models(plan, live)),
    )

    assert result.stop_reason == "completed_needs_review"
    assert not result.usage.complete
    assert result.usage.total_tokens == plan.source_usage.total_tokens + 50 + 6 * 130
    assert [role for role, _ in live.calls] == [
        "challenger",
        "verifier",
        "editor",
        "verifier",
        "editor",
        "verifier",
    ]
    assert source_artifact_hashes(source_run) == before
    assert_source_unchanged(plan)

    metadata = read_json(recovered_request.output_dir / "run_metadata.json")
    assert not metadata["usage"]["complete"]
    assert metadata["elapsed_seconds"] >= plan.source_elapsed_seconds + 17.5
    assert metadata["known_token_lower_bound"] == result.usage.total_tokens
    assert metadata["usage_total_unknown"] is True
    resources = read_json(recovered_request.output_dir / "stages" / "resources.json")["output"]
    assert resources["budget_usage"]["input_tokens"] >= (
        plan.source_usage.input_tokens + plan.valuation_reply.usage.input_tokens
    )
    assert resources["budget_usage"]["complete"] is True
    for stage in (
        "planner",
        "independent_challenge",
        "business",
        "accounting",
        "expectations",
        "management",
    ):
        assert metadata["usage_by_stage"][stage][0]["usage_origin"] == "imported_historical"
    assert metadata["usage_by_stage"]["valuation"][0]["usage_origin"] == "validated_diagnostic"
    provenance = read_json(recovered_request.output_dir / "recovery_provenance.json")
    assert provenance["previous_usage"] == plan.source_usage.model_dump(mode="json")
    assert provenance["aggregate_usage"] == result.usage.model_dump(mode="json")
    assert provenance["valuation_diagnostic"]["elapsed_seconds"] == 17.5
    assert "recovery_provenance.json" in result.artifact_hashes
    assert "reader_report_zh.md" in result.artifact_hashes
    assert (
        hashlib.sha256(
            (recovered_request.output_dir / "recovery_provenance.json").read_bytes()
        ).hexdigest()
        == result.artifact_hashes["recovery_provenance.json"]
    )
    assert (
        "unmeasured dispatched call"
        in (recovered_request.output_dir / "reader_report.md").read_text()
    )


def test_current_unknown_usage_blocks_retry_without_another_live_call(recovery_source, tmp_path):
    request, source_run, home = recovery_source
    plan = validate_recovery_source(request, source_run, home)
    recovered_request = request.model_copy(update={"output_dir": tmp_path / "unknown"})
    first_live = CurrentModels(unknown_valuation=True)
    first = run_research(
        recovered_request,
        ResearchServices(SnapshotEvidenceService(plan.snapshot), recovery_models(plan, first_live)),
    )
    assert first.stop_reason == "usage_incomplete"
    assert [role for role, _ in first_live.calls] == ["valuation"]

    second_live = CurrentModels()
    second = run_research(
        recovered_request,
        ResearchServices(
            SnapshotEvidenceService(plan.snapshot), recovery_models(plan, second_live)
        ),
    )
    assert second.stop_reason == "usage_incomplete"
    assert not second_live.calls
    assert not second.usage.complete


def test_diagnostic_usage_is_seeded_before_first_imported_admission(
    recovery_source, tmp_path, monkeypatch
):
    request, source_run, home = recovery_source
    base = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, base)
    plan = validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic)
    expected_known = plan.source_usage.total_tokens + plan.valuation_reply.usage.total_tokens
    observed = []

    def stop_at_first_admission(self, **kwargs):
        observed.append(self.usage.total_tokens)
        raise BudgetExhausted("synthetic_early_budget_stop")

    monkeypatch.setattr(BudgetTracker, "admit", stop_at_first_admission)
    live = CurrentModels()
    recovered_request = request.model_copy(update={"output_dir": tmp_path / "early-stop"})
    result = run_research(
        recovered_request,
        ResearchServices(SnapshotEvidenceService(plan.snapshot), recovery_models(plan, live)),
    )
    assert observed == [expected_known]
    assert result.usage.total_tokens == expected_known
    assert not result.usage.complete
    assert result.stop_reason == "synthetic_early_budget_stop"
    assert not live.calls


def test_consumed_diagnostic_is_not_recharged_when_stage_cache_write_fails(
    recovery_source, tmp_path, monkeypatch
):
    request, source_run, home = recovery_source
    base = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, base)
    plan = validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic)
    recovered_request = request.model_copy(update={"output_dir": tmp_path / "cache-failure"})
    original_save = CheckpointStore.save_stage
    failed = False

    def fail_valuation_once(self, stage, inputs, output):
        nonlocal failed
        if stage == "valuation" and not failed:
            failed = True
            raise OSError("synthetic valuation cache failure")
        return original_save(self, stage, inputs, output)

    monkeypatch.setattr(CheckpointStore, "save_stage", fail_valuation_once)
    first_live = CurrentModels()
    first = run_research(
        recovered_request,
        ResearchServices(SnapshotEvidenceService(plan.snapshot), recovery_models(plan, first_live)),
    )
    assert first.stop_reason == "stage_failed"
    expected_known = plan.source_usage.total_tokens + plan.valuation_reply.usage.total_tokens
    assert first.usage.total_tokens == expected_known
    assert not first_live.calls

    monkeypatch.setattr(CheckpointStore, "save_stage", original_save)
    second_live = CurrentModels()
    second = run_research(
        recovered_request,
        ResearchServices(
            SnapshotEvidenceService(plan.snapshot), recovery_models(plan, second_live)
        ),
    )
    assert second.stop_reason == "stage_failed"
    assert second.usage.total_tokens == expected_known
    assert not second_live.calls
    resources = read_json(recovered_request.output_dir / "stages" / "resources.json")["output"]
    assert len(resources["by_stage"]["valuation"]) == 1


def test_completed_recovery_manifest_resumes_without_calls(recovery_source, tmp_path):
    request, source_run, home = recovery_source
    base = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, base)
    plan = validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic)
    recovered_request = request.model_copy(update={"output_dir": tmp_path / "complete"})
    first_live = CurrentModels()
    first = run_research(
        recovered_request,
        ResearchServices(SnapshotEvidenceService(plan.snapshot), recovery_models(plan, first_live)),
    )
    second_live = CurrentModels()
    second = run_research(
        recovered_request,
        ResearchServices(
            SnapshotEvidenceService(plan.snapshot), recovery_models(plan, second_live)
        ),
    )
    assert second == first
    assert not second_live.calls


def test_source_tamper_and_diagnostic_mismatch_fail_validation(recovery_source, tmp_path):
    request, source_run, home = recovery_source
    plan = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, plan)
    provenance_path = diagnostic / "provenance.json"
    provenance = read_json(provenance_path)
    provenance["payload_hash"] = "0" * 64
    atomic_write(provenance_path, canonical_json(provenance))
    with pytest.raises(ValueError, match="diagnostic provenance"):
        validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic)

    stage_path = source_run / "stages" / "business.json"
    stage = read_json(stage_path)
    stage["output"]["summary"] = "tampered"
    atomic_write(stage_path, canonical_json(stage))
    with pytest.raises(ValueError):
        validate_recovery_source(request, source_run, home)


def test_source_and_diagnostic_directory_symlinks_are_rejected(recovery_source, tmp_path):
    request, source_run, home = recovery_source
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source_run, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        validate_recovery_source(request, source_link, home)

    plan = validate_recovery_source(request, source_run, home)
    diagnostic = make_diagnostic(tmp_path, plan)
    diagnostic_link = tmp_path / "diagnostic-link"
    diagnostic_link.symlink_to(diagnostic, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        validate_recovery_source(request, source_run, home, valuation_diagnostic=diagnostic_link)


def test_request_or_model_settings_mismatch_rejected_before_service(recovery_source):
    request, source_run, home = recovery_source
    changed_models = deepcopy(request.models)
    changed_models["business"] = changed_models["business"].model_copy(update={"effort": "low"})
    changed = request.model_copy(update={"models": changed_models})
    with pytest.raises(ValueError, match="identity"):
        validate_recovery_source(changed, source_run, home)
    assert request_identity(request) != request_identity(changed)


def test_source_artifact_hash_contract_contains_exact_recovery_set(recovery_source):
    _, source_run, _ = recovery_source
    result = read_json(source_run / "result.json")
    expected = {
        "result.json",
        "research_checkpoint.json",
        "stages/resources.json",
        "stages/planner.json",
        "stages/independent_challenge.json",
        "stages/business.json",
        "stages/accounting.json",
        "stages/expectations.json",
        "stages/management.json",
        *result["artifact_hashes"],
    }
    assert set(source_artifact_hashes(source_run)) == expected


@pytest.mark.parametrize(
    "authorization",
    [("--allow-live",), ("--acknowledge-unknown-usage",), ()],
)
def test_cli_requires_both_authorizations_before_provider_or_source_access(
    tmp_path, monkeypatch, authorization
):
    touched = []

    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("provider or source validation should not run")

    monkeypatch.setattr(research_recover, "validate_recovery_source", forbidden)
    monkeypatch.setattr(research_recover, "CodexModelService", forbidden)
    args = [
        "--config",
        str(tmp_path / "missing-config.json"),
        "--source-run",
        str(tmp_path / "missing-source"),
        "--output",
        str(tmp_path / "new-output"),
        "--codex-home",
        str(tmp_path / "missing-home"),
        *authorization,
    ]
    assert research_recover.main(args) == 2
    assert not touched
