from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

import pytest

from cli import research_finalize
from tradingagents.research.contracts import Budget, ResearchRequest, Usage
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryAuthorization,
    FinalizationRecoveryModelService,
    assert_finalization_source_unchanged,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
    write_finalization_continuation_plan,
)
from tradingagents.research.services import ModelReply
from tradingagents.research.storage import (
    ENGINE_VERSION,
    canonical_json,
    digest,
    request_identity,
)
from tradingagents.research.wire import WIRE_SCHEMA_VERSION


def _budget(tokens=1_500_000):
    return Budget(total_tokens=tokens, followup_cycles=0)


def _stage(role, payload, output, usage=None):
    usage = usage or Usage(input_tokens=11, output_tokens=3)
    return {
        "role": role,
        "inputs_hash": digest(payload),
        "output_hash": digest(output),
        "output": output,
        "usage": usage.model_dump(mode="json"),
    }


def _source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".research.lock").touch()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    evidence = inputs / "evidence.json"
    case = inputs / "case.json"
    evidence.write_bytes(b'{"frozen":"evidence"}')
    case.write_bytes(b'{"frozen":"case"}')
    request = ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="codex",
        output_dir=source,
        quality_revision="evidence-led-bounded",
        report_language="English",
        budget=_budget(),
        evidence_path=evidence,
        financial_case_path=case,
    )
    reader = "# Exact candidate\n"
    reader_hash = sha256(reader.encode()).hexdigest()
    payloads = {
        "editor": {"stage": "editor", "draft_input": "exact"},
        "verify_report": {
            "stage": "verify_report",
            "research": {
                "rendered_reader": reader,
                "rendered_reader_sha256": reader_hash,
            },
        },
        "verify_report-coverage-0": {
            "stage": "verify_report-coverage-0",
            "research": {"rendered_reader_sha256": reader_hash, "issues": ["a"]},
        },
    }
    outputs = {
        "editor": {"sections": [], "limitations": []},
        "verify_report": {"reviewed_report": True, "findings": []},
        "verify_report-coverage-0": {"reviewed_report": True, "findings": []},
    }
    stages = {
        stage: _stage(
            "editor" if stage == "editor" else "verifier",
            payload,
            outputs[stage],
        )
        for stage, payload in payloads.items()
    }
    frozen = {
        "evidence_path": evidence.read_bytes(),
        "financial_case_path": case.read_bytes(),
    }
    model_identity = digest("synthetic-finalization-provider")
    source_request_identity = request_identity(request, frozen)
    checkpoint = {
        "schema_version": 1,
        "engine_version": ENGINE_VERSION,
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "request": request.model_dump(mode="json"),
        "model_service_identity": model_identity,
        "run_identity": digest(
            {"request": source_request_identity, "model_service": model_identity}
        ),
        "frozen_input_hashes": {
            name: sha256(content).hexdigest() for name, content in frozen.items()
        },
        "stages": stages,
        "usage": Usage(input_tokens=33, output_tokens=9).model_dump(mode="json"),
        "dispatched": False,
        "candidate": {
            "stage": "verify_report",
            "reader_sha256": reader_hash,
            "reader_text": reader,
        },
        "candidate_review_stage": "verify_report",
    }
    (source / "finalization_checkpoint.json").write_bytes(canonical_json(checkpoint))
    destination = request.model_copy(
        update={"output_dir": tmp_path / "continued", "budget": _budget(2_000_000)}
    )
    return source, destination, model_identity, payloads


def _authorization(plan):
    return FinalizationRecoveryAuthorization(
        authorization_id="explicit-incremental-budget",
        authorized_at="2026-09-19T12:00:00Z",
        plan_sha256=plan.plan_sha256,
        new_request_identity=plan.new_request_identity,
        incremental_budget=plan.destination_request.budget,
        authorize_live_continuation=True,
    )


class _Live:
    supports_hard_output_cap = False

    def __init__(self, identity):
        self.identity = identity
        self.calls = []

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        return ModelReply(data={"live": payload["stage"]}, usage=Usage(input_tokens=7, output_tokens=2))


def _service(tmp_path):
    source, request, identity, payloads = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    authorized = authorize_finalization_continuation(plan, _authorization(plan))
    live = _Live(identity)
    service = FinalizationRecoveryModelService(authorized, live)
    service.validate_request(request, dict(plan.frozen_inputs))
    return plan, service, live, payloads


def test_exact_saved_stages_are_replayed_without_incremental_calls(tmp_path):
    plan, service, live, payloads = _service(tmp_path)
    before = deepcopy(service.candidate_recovery_context)

    assert service.has_saved_reply(
        "verify_report", {**payloads["verify_report"], "timeout_seconds": 1, "max_output_tokens": 2}
    )
    assert service.candidate_recovery_context == before
    reply = service.complete(
        "verifier",
        {**payloads["verify_report"], "timeout_seconds": 1, "max_output_tokens": 2},
        plan.destination_request,
    )

    assert reply.data["reviewed_report"] is True
    assert reply.usage.total_tokens == 14
    assert not live.calls
    assert service.candidate_recovery_context["replay_state"]["imported_stage_names"] == [
        "verify_report"
    ]


def test_first_missing_stage_switches_permanently_to_live(tmp_path):
    plan, service, live, payloads = _service(tmp_path)
    missing = {"stage": "verify_report-coverage-1", "research": {"issues": ["b"]}}

    service.complete("verifier", payloads["verify_report"], plan.destination_request)

    assert not service.has_saved_reply("verify_report-coverage-1", missing)
    reply = service.complete("verifier", missing, plan.destination_request)
    assert reply.data == {"live": "verify_report-coverage-1"}
    assert len(live.calls) == 1

    # A later exact saved reply is deliberately not imported after live begins.
    assert service.call_origin(
        "editor", payloads["editor"], plan.destination_request
    ) == "current_live"


def test_saved_stage_payload_mismatch_rejects_without_live_fallback(tmp_path):
    plan, service, live, payloads = _service(tmp_path)
    changed = deepcopy(payloads["verify_report"])
    changed["research"]["rendered_reader"] += "tampered"

    assert not service.has_saved_reply("verify_report", changed)
    with pytest.raises(ValueError, match="payload differs"):
        service.complete("verifier", changed, plan.destination_request)
    assert not live.calls


def test_live_delegation_is_blocked_until_exact_factual_candidate_is_replayed(tmp_path):
    plan, service, live, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="factual-review stage"):
        service.complete(
            "verifier",
            {"stage": "verify_report-coverage-1", "research": {"issues": ["b"]}},
            plan.destination_request,
        )
    assert not live.calls


def test_source_tampering_and_unsettled_usage_are_rejected(tmp_path):
    source, request, _, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    checkpoint = source / "finalization_checkpoint.json"
    checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
    with pytest.raises(ValueError, match="source or frozen inputs changed"):
        assert_finalization_source_unchanged(plan)

    other = tmp_path / "other"
    other.mkdir()
    source2, request2, _, _ = _source(other)
    raw = __import__("json").loads((source2 / "finalization_checkpoint.json").read_text())
    raw["dispatched"] = True
    raw["usage"]["complete"] = False
    (source2 / "finalization_checkpoint.json").write_bytes(canonical_json(raw))
    with pytest.raises(ValueError, match="unsettled dispatch"):
        prepare_finalization_continuation(source2, request2)


def test_incremental_budget_and_provider_identity_are_bound(tmp_path):
    source, request, identity, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    authorization = _authorization(plan)
    changed_request = request.model_copy(update={"budget": _budget(2_000_001)})

    with pytest.raises(ValueError, match="request|budget"):
        authorize_finalization_continuation(plan, authorization, changed_request)
    authorized = authorize_finalization_continuation(plan, authorization)
    with pytest.raises(ValueError, match="provider identity"):
        FinalizationRecoveryModelService(authorized, _Live(digest(identity + "changed")))


def test_nested_plan_mutation_is_rejected_before_authorization(tmp_path):
    source, request, _, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    changed = deepcopy(plan)
    changed.imported_stages[0].output["sections"].append({"tampered": True})

    with pytest.raises(ValueError, match="content changed"):
        authorize_finalization_continuation(changed, _authorization(plan))


def test_factual_payload_must_bind_the_declared_candidate_bytes(tmp_path):
    source, request, identity, payloads = _source(tmp_path)
    checkpoint_path = source / "finalization_checkpoint.json"
    raw = __import__("json").loads(checkpoint_path.read_text())
    wrong_payload = deepcopy(payloads["verify_report"])
    wrong_reader = "# Different candidate\n"
    wrong_payload["research"] = {
        "rendered_reader": wrong_reader,
        "rendered_reader_sha256": sha256(wrong_reader.encode()).hexdigest(),
    }
    raw["stages"]["verify_report"]["inputs_hash"] = digest(wrong_payload)
    checkpoint_path.write_bytes(canonical_json(raw))
    plan = prepare_finalization_continuation(source, request)
    authorized = authorize_finalization_continuation(plan, _authorization(plan))
    service = FinalizationRecoveryModelService(authorized, _Live(identity))
    service.validate_request(request, dict(plan.frozen_inputs))

    with pytest.raises(ValueError, match="bound reader candidate"):
        service.complete("verifier", wrong_payload, request)


def test_prepare_cli_is_offline_and_writes_reviewable_bound_plan(tmp_path, capsys):
    source, request, _, _ = _source(tmp_path)
    config = tmp_path / "destination.json"
    output = tmp_path / "plan.json"
    config.write_bytes(canonical_json(request))

    assert research_finalize.main(
        [
            "prepare",
            "--source-dir",
            str(source),
            "--config",
            str(config),
            "--output",
            str(output),
        ]
    ) == 0
    printed = __import__("json").loads(capsys.readouterr().out)
    saved = __import__("json").loads(output.read_text())
    assert printed["live_dispatch"] is False
    assert saved["plan_sha256"] == printed["plan_sha256"]
    assert saved["authorization_required"]["incremental_budget"] == request.budget.model_dump(
        mode="json"
    )

    assert research_finalize.main(
        [
            "run",
            "--source-dir",
            str(source),
            "--config",
            str(config),
            "--authorization-file",
            str(tmp_path / "missing-authorization.json"),
            "--codex-home",
            str(tmp_path),
        ]
    ) == 2
    assert "--allow-live" in capsys.readouterr().err


def test_plan_writer_cannot_overwrite_history_or_existing_files(tmp_path):
    source, request, _, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    existing = tmp_path / "existing.json"
    existing.write_text("preserve this")
    for path in (source / "plan.json", source / "finalization_checkpoint.json", existing,
                 request.financial_case_path):
        with pytest.raises(ValueError, match="fresh path"):
            write_finalization_continuation_plan(plan, path)
    assert existing.read_text() == "preserve this"
    assert not (source / "plan.json").exists()
    assert_finalization_source_unchanged(plan)


def test_cumulative_usage_cannot_drop_known_saved_stage_spend(tmp_path):
    from tradingagents.research.storage import read_json

    source, request, _, _ = _source(tmp_path)
    path = source / "finalization_checkpoint.json"
    checkpoint = read_json(path)
    checkpoint["usage"] = Usage().model_dump(mode="json")
    path.write_bytes(canonical_json(checkpoint))
    with pytest.raises(ValueError, match="omits saved stage usage"):
        prepare_finalization_continuation(source, request)


@pytest.mark.parametrize("mutation", ["candidate", "calls", "boundary", "usage", "non_live", "duplicate"])
def test_restored_live_state_requires_candidate_and_consistent_call_proofs(tmp_path, mutation):
    plan, service, _, payloads = _service(tmp_path)
    service.complete("verifier", payloads["verify_report"], plan.destination_request)
    service.complete("verifier", {"stage": "verify_report-coverage-1"}, plan.destination_request)
    saved = deepcopy(service.candidate_recovery_context)
    state = saved["replay_state"]
    if mutation == "candidate":
        state["imported_stage_names"] = []
        state["imported_calls"] = []
    elif mutation == "calls":
        saved["current_calls"] = []
    elif mutation == "boundary":
        state["live_boundary"] = {}
    elif mutation == "usage":
        saved["current_calls"][0]["usage"]["complete"] = False
    elif mutation == "non_live":
        state["live_started"] = False
    else:
        saved["current_calls"].append(deepcopy(saved["current_calls"][0]))
    live = _Live(plan.current_provider_identity)
    fresh = FinalizationRecoveryModelService(authorize_finalization_continuation(plan, _authorization(plan)), live)
    with pytest.raises(ValueError):
        fresh.restore_recovery_context(saved)
    assert not live.calls


def test_valid_restored_live_state_preserves_candidate_boundary(tmp_path):
    plan, service, _, payloads = _service(tmp_path)
    service.complete("verifier", payloads["verify_report"], plan.destination_request)
    service.complete("verifier", {"stage": "verify_report-coverage-1"}, plan.destination_request)
    fresh = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, _authorization(plan)), _Live(plan.current_provider_identity))
    fresh.restore_recovery_context(service.candidate_recovery_context)
    assert fresh.candidate_recovery_context == service.candidate_recovery_context


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("changed_payload", [False, True])
def test_recorded_current_call_never_redispatches_without_cached_output(
        tmp_path, restore, changed_payload):
    plan, service, live, payloads = _service(tmp_path)
    service.complete("verifier", payloads["verify_report"], plan.destination_request)
    payload = {"stage": "verify_report-coverage-1"}
    service.complete("verifier", payload, plan.destination_request)
    recorded = deepcopy(service.candidate_recovery_context)
    if restore:
        live = _Live(plan.current_provider_identity)
        service = FinalizationRecoveryModelService(
            authorize_finalization_continuation(plan, _authorization(plan)), live)
        service.validate_request(plan.destination_request, dict(plan.frozen_inputs))
        service.restore_recovery_context(recorded)
    before_calls = len(live.calls)
    if changed_payload:
        payload = {**payload, "research": {"changed": True}}
    with pytest.raises(ValueError, match="already dispatched"):
        service.complete("verifier", payload, plan.destination_request)
    assert len(live.calls) == before_calls
    assert service.candidate_recovery_context == recorded


def test_plan_writer_does_not_replace_concurrently_created_target(tmp_path, monkeypatch):
    from tradingagents.research import finalization_recovery as recovery

    source, request, _, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    destination = tmp_path / "new-plan.json"
    link = recovery.os.link

    def create_then_link(src, dst, **kwargs):
        descriptor = recovery.os.open(dst, recovery.os.O_WRONLY | recovery.os.O_CREAT | recovery.os.O_EXCL,
                                      0o600, dir_fd=kwargs["dst_dir_fd"])
        with recovery.os.fdopen(descriptor, "wb") as stream:
            stream.write(b"concurrent content")
        return link(src, dst, **kwargs)

    monkeypatch.setattr(recovery.os, "link", create_then_link)
    with pytest.raises(FileExistsError):
        write_finalization_continuation_plan(plan, destination)
    assert destination.read_bytes() == b"concurrent content"
    assert not list(tmp_path.glob(".finalization-plan-*"))


def test_plan_writer_rejects_parent_symlink_swap(tmp_path, monkeypatch):
    from tradingagents.research import finalization_recovery as recovery

    source, request, _, _ = _source(tmp_path)
    plan = prepare_finalization_continuation(source, request)
    parent = tmp_path / "plans"
    parent.mkdir()
    writer = recovery._write_new_plan

    def swap_then_write(path, content):
        parent.rename(tmp_path / "moved-plans")
        parent.symlink_to(source, target_is_directory=True)
        return writer(path, content)

    monkeypatch.setattr(recovery, "_write_new_plan", swap_then_write)
    with pytest.raises(OSError):
        write_finalization_continuation_plan(plan, parent / "plan.json")
    assert not (source / "plan.json").exists()
    assert_finalization_source_unchanged(plan)
