import hashlib

import pytest

from scripts import research_writer_diagnostic as diagnostic
from tests.test_research_coverage_diagnostic import Clock, FixtureService
from tradingagents.research.contracts import ResearchRequest, Usage
from tradingagents.research.storage import canonical_json, digest, read_json

FIXTURE_BINDINGS = [
    {"stage": f"fixture-{index}", "output_sha256": f"{index:064x}"} for index in range(8)
]


def request(tmp_path):
    roles = ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="codex",
        output_dir=tmp_path / "run_1",
        report_language="English",
        quality_revision="evidence-led-bounded",
        budget=diagnostic.diagnostic_budget(),
    ).models
    roles["editor"] = roles["editor"].model_copy(update={"model": "gpt-6-astra", "effort": "high"})
    roles["verifier"] = roles["verifier"].model_copy(
        update={"model": "gpt-5.6-sol", "effort": "high"}
    )
    return ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="codex",
        output_dir=tmp_path / "run_1",
        report_language="English",
        quality_revision="evidence-led-bounded",
        budget=diagnostic.diagnostic_budget(),
        models=roles,
    )


def plan_for(req, tmp_path, *, second_estimate=None):
    writer_payload = {"stage": "editor", "system": "writer"}
    writer = diagnostic._call(writer_payload, "editor", req, diagnostic.WRITER_OUTPUT_ENVELOPE)
    second_estimate = (
        diagnostic.second_phase_reserve(writer) if second_estimate is None else second_estimate
    )
    return {
        "kind": "writer-factual-diagnostic-v1",
        "request": req.model_dump(mode="json"),
        "writer": writer,
        "fixture_policy": diagnostic.FIXTURE_POLICY,
        "fixture_bindings": [dict(item) for item in FIXTURE_BINDINGS],
        "fixture_bindings_sha256": digest(FIXTURE_BINDINGS),
        "second_phase_algorithm": diagnostic.SECOND_PHASE_ALGORITHM,
        "initial_reserve_tokens": writer["reserve_tokens"],
        "second_phase_reserve_estimate": second_estimate,
        "codex_home": str(tmp_path.resolve()),
        "model_provider": "openai",
        "source_run": str(tmp_path),
        "formal_sol_review_required": True,
        "worker_seconds": diagnostic.WORKER_SECONDS,
        "supervisor_seconds": diagnostic.SUPERVISOR_SECONDS,
        "historical_total_usage_unknown": True,
        "report_export_authorized": False,
        "automatic_retry_authorized": False,
    }


def offline_capture(monkeypatch, writer_payload=None):
    writer_payload = writer_payload or {"stage": "editor", "system": "writer"}
    reader = "Diagnostic candidate reader."
    factual_payload = {
        "stage": "verify_report",
        "system": "factual",
        "research": {
            "rendered_reader": reader,
            "rendered_reader_sha256": hashlib.sha256(reader.encode()).hexdigest(),
        },
    }
    monkeypatch.setattr(
        diagnostic,
        "capture_payload",
        lambda source, request, destination, writer_reply=None: (
            writer_payload if writer_reply is None else factual_payload
        ),
    )
    monkeypatch.setattr(diagnostic, "verify_source", lambda _plan: None)


def test_plan_binds_initial_payload_and_provider_home(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    req = request(tmp_path)
    payload = {"stage": "editor", "system": "x"}
    call = diagnostic._call(payload, "editor", req, diagnostic.WRITER_OUTPUT_ENVELOPE)
    plan = {
        "kind": "writer-factual-diagnostic-v1",
        "request": req.model_dump(mode="json"),
        "writer": call,
        "fixture_policy": diagnostic.FIXTURE_POLICY,
        "fixture_bindings": [dict(item) for item in FIXTURE_BINDINGS],
        "fixture_bindings_sha256": digest(FIXTURE_BINDINGS),
        "second_phase_algorithm": diagnostic.SECOND_PHASE_ALGORITHM,
        "initial_reserve_tokens": call["reserve_tokens"],
        "second_phase_reserve_estimate": diagnostic.second_phase_reserve(call),
        "codex_home": str(tmp_path.resolve()),
        "model_provider": "openai",
        "formal_sol_review_required": True,
        "worker_seconds": diagnostic.WORKER_SECONDS,
        "supervisor_seconds": diagnostic.SUPERVISOR_SECONDS,
        "historical_total_usage_unknown": True,
        "report_export_authorized": False,
        "automatic_retry_authorized": False,
    }
    path = tmp_path / "plan.json"
    path.write_bytes(canonical_json(plan))
    plan = read_json(path)
    assert diagnostic.validate_plan(plan, home=tmp_path) == req
    plan["writer"]["payload"]["system"] = "changed"
    try:
        diagnostic.validate_plan(plan, home=tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("payload drift accepted")


def test_execute_persists_dynamic_factual_payload_before_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    monkeypatch.setattr(diagnostic, "verify_source", lambda _plan: None)
    req = request(tmp_path)
    plan = plan_for(req, tmp_path)
    offline_capture(monkeypatch)
    service = FixtureService(
        [
            {"data": {}, "usage": {"input_tokens": 1, "output_tokens": 0}},
            {"data": {"reviewed_report": False}, "usage": {"input_tokens": 1, "output_tokens": 0}},
        ]
    )
    result = diagnostic.execute_plan(plan, req, service, clock=Clock())
    assert result.stop_reason == "writer_diagnostic_completed"
    assert (req.output_dir / "factual-payload.json").exists() and len(service.calls) == 2
    assert (req.output_dir / "reader_candidate.md").read_text() == "Diagnostic candidate reader."
    assert result.assessment.status == "needs_review"


def test_pre_dispatch_persist_failure_has_known_zero_usage_and_no_provider_call(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    req, plan = request(tmp_path), None
    plan = plan_for(req, tmp_path)
    offline_capture(monkeypatch)
    original = diagnostic.atomic_write
    writes = 0

    def fail_only_dispatch(path, content):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("synthetic pre-dispatch persistence failure")
        return original(path, content)

    monkeypatch.setattr(diagnostic, "atomic_write", fail_only_dispatch)
    service = FixtureService()
    result = diagnostic.execute_plan(plan, req, service, clock=Clock())
    assert result.stop_reason == "writer_diagnostic_failed"
    assert result.usage == Usage() and not service.calls


def test_writer_failure_or_incomplete_usage_blocks_factual_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    for reply in (RuntimeError("synthetic"), {"data": {}, "usage": {"complete": False}}):
        case = tmp_path / str(len(str(reply)))
        req = request(case)
        plan = plan_for(req, case)
        offline_capture(monkeypatch)
        service = FixtureService([reply])
        result = diagnostic.execute_plan(plan, req, service, clock=Clock())
        assert result.stop_reason == "writer_diagnostic_failed"
        assert len(service.calls) == 1
        assert not result.usage.complete


def test_malformed_writer_or_factual_reply_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    req = request(tmp_path / "writer")
    plan = plan_for(req, tmp_path / "writer")
    monkeypatch.setattr(diagnostic, "verify_source", lambda _plan: None)
    monkeypatch.setattr(
        diagnostic,
        "capture_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad writer")),
    )
    assert diagnostic.execute_plan(
        plan, req, FixtureService([{"data": {}, "usage": {}}]), clock=Clock()
    ).stop_reason.endswith("failed")

    req = request(tmp_path / "factual")
    plan = plan_for(req, tmp_path / "factual")
    offline_capture(monkeypatch)
    service = FixtureService([{"data": {}, "usage": {}}, {"data": {"unknown": True}, "usage": {}}])
    assert diagnostic.execute_plan(plan, req, service, clock=Clock()).stop_reason.endswith("failed")
    assert len(service.calls) == 2


def test_actual_overshoot_deadline_or_second_admission_blocks_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    for name, reply, clock in (
        (
            "overshoot",
            {"data": {}, "usage": {"input_tokens": diagnostic.TOKEN_CAP + 1}},
            Clock(),
        ),
        ("deadline", {"data": {}, "usage": {}}, Clock()),
    ):
        if name == "deadline":
            clock.advance(diagnostic.WORKER_SECONDS)
        req = request(tmp_path / name)
        plan = plan_for(req, tmp_path / name)
        offline_capture(monkeypatch)
        result = diagnostic.execute_plan(plan, req, FixtureService([reply]), clock=clock)
        assert result.stop_reason.endswith("failed")


def test_source_change_before_second_capture_and_home_provider_mismatch_prevent_dispatch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    req = request(tmp_path)
    plan = plan_for(req, tmp_path)
    offline_capture(monkeypatch)
    checks = iter((None, ValueError("source changed")))

    def source_check(_plan):
        result = next(checks)
        if isinstance(result, Exception):
            raise result

    monkeypatch.setattr(diagnostic, "verify_source", source_check)
    service = FixtureService([{"data": {}, "usage": {}}])
    result = diagnostic.execute_plan(plan, req, service, clock=Clock())
    assert result.stop_reason.endswith("failed") and len(service.calls) == 1

    plan["model_provider"] = "other"
    with pytest.raises(ValueError, match="provider binding"):
        diagnostic.validate_plan(plan, home=tmp_path)


def test_second_estimate_tampering_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    req = request(tmp_path)
    plan = plan_for(req, tmp_path)
    plan["second_phase_reserve_estimate"] = diagnostic.TOKEN_CAP
    with pytest.raises(ValueError, match="initial writer payload"):
        diagnostic.validate_plan(plan)


def test_fixture_binding_digest_tampering_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "model_input_bytes", lambda *_args, **_kwargs: 100)
    plan = plan_for(request(tmp_path), tmp_path)
    plan["fixture_bindings"][0]["output_sha256"] = "tampered"

    with pytest.raises(ValueError, match="plan contract"):
        diagnostic.validate_plan(plan)
