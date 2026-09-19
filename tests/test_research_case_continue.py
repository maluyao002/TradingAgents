"""Explicit continuation CLI boundaries; synthetic responses only, never providers."""

import pytest

from scripts import research_case_continue as command
from tests.test_research_case_engine import CaseFixture
from tests.test_research_case_recovery import (
    _authorization,
    _continuation,
    _verified,
    case_recovery_source as shared_case_recovery_source,
)
from tradingagents.research.storage import canonical_json, digest
from tradingagents.research.supervisor import SupervisorResult


@pytest.fixture
def case_recovery_source(tmp_path):
    return shared_case_recovery_source.__wrapped__(tmp_path)


def configuration(source, tmp_path):
    verified = _verified(source)
    request = _continuation(verified, tmp_path)
    auth = _authorization(verified, request)
    paths = [tmp_path / name for name in ("source.json", "continuation.json", "authorization.json")]
    for path, value in zip(paths, (source[0], request, auth), strict=True):
        path.write_bytes(canonical_json(value))
    args = ["--source-config", str(paths[0]), "--config", str(paths[1]),
            "--authorization", str(paths[2]), "--codex-home", str(source[2])]
    return args, request, paths


@pytest.mark.parametrize("flags", [[], ["--allow-live"], ["--acknowledge-unknown-usage"]])
def test_cli_requires_both_live_flags_before_reading_inputs(monkeypatch, flags):
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized command reached configuration loading")
    monkeypatch.setattr(command, "load_request", forbidden)
    assert command.main(["--source-config", "/missing/source", "--config", "/missing/new",
                         "--authorization", "/missing/auth", "--codex-home", "/missing/home", *flags]) == 2


def test_dry_run_validates_without_constructing_live_service(case_recovery_source, tmp_path, monkeypatch):
    args, request, _ = configuration(case_recovery_source, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("dry run dispatched a provider or supervised worker")
    monkeypatch.setattr(command, "CodexModelService", forbidden)
    monkeypatch.setattr(command, "run_supervised", forbidden)
    assert command.main([*args, "--dry-run"]) == 0
    assert not request.output_dir.exists()


def test_invalid_authorization_is_redacted_and_never_dispatches(
    case_recovery_source, tmp_path, monkeypatch, capsys,
):
    args, request, paths = configuration(case_recovery_source, tmp_path)
    paths[2].write_text('{"private-key": "do-not-display-secret"}')
    def forbidden(*args, **kwargs):
        pytest.fail("invalid authorization reached dispatch")
    monkeypatch.setattr(command, "run_supervised", forbidden)
    assert command.main([*args, "--allow-live", "--acknowledge-unknown-usage"]) == 2
    captured = capsys.readouterr()
    assert "do-not-display-secret" not in captured.out + captured.err
    assert not request.output_dir.exists()


def test_explicit_command_worker_completes_with_synthetic_continuation_only(
    case_recovery_source, tmp_path, monkeypatch,
):
    args, request, _ = configuration(case_recovery_source, tmp_path)
    calls = []
    class SyntheticLive(CaseFixture):
        def __init__(self, home):
            super().__init__()
            self.identity = digest("synthetic-continuation-cli")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.extend(payload["stage"] for _, payload in self.calls)
    def offline_supervisor(worker, target):
        return SupervisorResult("completed", worker(target), "completed")
    monkeypatch.setattr(command, "CodexModelService", SyntheticLive)
    monkeypatch.setattr(command, "run_supervised", offline_supervisor)
    assert command.main([*args, "--allow-live", "--acknowledge-unknown-usage"]) == 0
    assert calls[0] == "reconcile_challenge"
    assert "planner" not in calls and "business" not in calls
    assert (request.output_dir / "result.json").exists()
