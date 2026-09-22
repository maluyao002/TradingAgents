"""Execution-binding and pre-dispatch failure regressions for coverage diagnostics."""

from pathlib import Path

import pytest

from scripts import research_coverage_diagnostic as diagnostic
from tests.test_research_coverage_diagnostic import (
    Clock,
    FixtureService,
    cli_plan,
    request_and_plan,
    trace,
)
from tradingagents.research.storage import canonical_json, digest


def test_runtime_binding_covers_all_first_party_python_sources():
    root = Path(diagnostic.__file__).resolve().parents[1]
    expected = {
        str(path.relative_to(root))
        for directory in ("cli", "scripts", "tradingagents")
        for path in (root / directory).rglob("*.py")
    }

    assert set(diagnostic.runtime_binding()) == expected


def test_verify_runtime_rejects_changed_first_party_source_bytes(tmp_path, monkeypatch):
    _request, plan = request_and_plan(tmp_path)
    root = Path(diagnostic.__file__).resolve().parents[1]
    plan.update(
        code_revision=diagnostic.subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        runtime_sha256=diagnostic.runtime_binding(),
    )
    real_read_bytes = diagnostic.read_bytes

    def altered_source(path):
        path = Path(path)
        if path.as_posix().endswith("/cli/research.py"):
            return real_read_bytes(path) + b"\n# synthetic post-approval change\n"
        return real_read_bytes(path)

    monkeypatch.setattr(diagnostic, "read_bytes", altered_source)

    with pytest.raises(ValueError, match="implementation changed"):
        diagnostic.verify_runtime(plan)


def test_cli_rejects_unbound_codex_home_before_consuming_attempt(tmp_path, monkeypatch):
    approved_home = (tmp_path / "approved-home").resolve()
    other_home = (tmp_path / "other-home").resolve()
    capsule, path, _request, plan = cli_plan(tmp_path)
    plan.update(codex_home=str(approved_home), model_provider="openai")
    path.write_bytes(canonical_json(plan))

    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(
        diagnostic,
        "run_supervised",
        lambda *_args, **_kwargs: pytest.fail("mismatched runtime reached worker startup"),
    )

    with pytest.raises(ValueError, match="Codex home"):
        diagnostic.main(
            [
                "run",
                "--plan",
                str(path),
                "--approved-plan-sha256",
                digest(plan),
                "--codex-home",
                str(other_home),
                "--allow-live",
                "--allow-advisory-token-cap",
            ]
        )

    assert not (capsule / "attempt_started").exists()


def test_cli_rejects_read_only_v1_before_consuming_attempt(tmp_path, monkeypatch):
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    path = capsule / "plan.json"
    path.write_text("{}")
    request, _plan = request_and_plan(tmp_path)
    plan = {"kind": "disclosure-control-diagnostic-v1"}

    monkeypatch.setattr(diagnostic, "read_json", lambda _path: plan)
    monkeypatch.setattr(diagnostic, "validate_plan", lambda _plan: request)
    monkeypatch.setattr(
        diagnostic,
        "run_supervised",
        lambda *_args, **_kwargs: pytest.fail("read-only plan reached supervision"),
    )

    with pytest.raises(ValueError, match="read-only"):
        diagnostic.main(
            [
                "run",
                "--plan",
                str(path),
                "--approved-plan-sha256",
                digest(plan),
                "--codex-home",
                str(tmp_path / "home"),
                "--allow-live",
                "--allow-advisory-token-cap",
            ]
        )

    assert not (capsule / "attempt_started").exists()


def test_worker_rechecks_home_binding_before_constructing_service(tmp_path, monkeypatch):
    approved_home = (tmp_path / "approved-home").resolve()
    request, plan = request_and_plan(tmp_path)
    plan.update(diagnostic.execution_binding(approved_home))
    worker = diagnostic.CoverageDiagnosticWorker(plan, (tmp_path / "other-home").resolve())

    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(
        diagnostic,
        "CodexModelService",
        lambda *_args, **_kwargs: pytest.fail("mismatched worker constructed a model service"),
    )

    with pytest.raises(ValueError, match="Codex home"):
        worker(request)

    assert not request.output_dir.exists()


@pytest.mark.parametrize("provider", [None, "azure", "OpenAI"])
def test_cli_requires_the_fixed_openai_provider_binding_before_attempt(
    tmp_path, monkeypatch, provider
):
    capsule, path, _request, plan = cli_plan(tmp_path)
    plan["codex_home"] = str((tmp_path / "approved-home").resolve())
    if provider is None:
        plan.pop("model_provider", None)
    else:
        plan["model_provider"] = provider
    path.write_bytes(canonical_json(plan))

    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(
        diagnostic,
        "run_supervised",
        lambda *_args, **_kwargs: pytest.fail("invalid provider reached worker startup"),
    )

    with pytest.raises(ValueError, match="provider"):
        diagnostic.main(
            [
                "run",
                "--plan",
                str(path),
                "--approved-plan-sha256",
                digest(plan),
                "--codex-home",
                plan["codex_home"],
                "--allow-live",
                "--allow-advisory-token-cap",
            ]
        )

    assert not (capsule / "attempt_started").exists()


@pytest.mark.parametrize(
    ("failed_write", "completed_calls", "known_tokens"),
    [(1, 0, 0), (2, 0, 0), (6, 1, 24)],
)
def test_predispatch_trace_write_failure_keeps_usage_known_and_returns_failure(
    tmp_path, monkeypatch, failed_write, completed_calls, known_tokens
):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService()
    real_write = diagnostic.atomic_write
    writes = 0

    def fail_durable_intent_once(path, content):
        nonlocal writes
        writes += 1
        if writes == failed_write:
            raise OSError("synthetic durable-intent write failure")
        return real_write(path, content)

    monkeypatch.setattr(diagnostic, "atomic_write", fail_durable_intent_once)

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed"
    assert result.usage.complete and result.usage.total_tokens == known_tokens
    assert len(service.calls) == completed_calls and service.closed == 1
    saved = trace(request)
    assert saved["status"] == "failed"
    assert not saved["pending_dispatch"]
    assert saved["usage"]["complete"]
    if saved["calls"]:
        assert saved["calls"][-1]["status"] == "failed"


def test_actual_call_failure_retains_unknown_usage_and_failed_trace(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService([RuntimeError("synthetic provider failure")])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed"
    assert not result.usage.complete and result.usage.total_tokens == 0
    assert len(service.calls) == 1 and service.closed == 1
    saved = trace(request)
    assert saved["status"] == "failed"
    assert saved["pending_dispatch"]
    assert not saved["usage"]["complete"]
    assert saved["calls"][-1]["status"] == "failed"
