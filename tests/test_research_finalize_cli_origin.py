"""The explicit pending-origin locator survives CLI and supervisor boundaries."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import cli.research_finalize as cli


@pytest.mark.parametrize("command", ["prepare", "run"])
def test_parser_keeps_explicit_pending_origin(command):
    args = [command, "--source-dir", "source", "--config", "request.json",
            "--revise-reader", "--pending-origin-dir", "origin"]
    args += (["--output", "plan.json"] if command == "prepare" else
             ["--authorization-file", "auth.json", "--codex-home", "runtime"])
    parsed = cli._parser().parse_args(args)
    assert parsed.pending_origin_dir == Path("origin")
    assert parsed.revise_reader is True


def test_main_passes_origin_into_preparation_and_supervised_worker(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    origin = tmp_path / "origin"
    request = SimpleNamespace(backend="codex")
    plan = object()
    prepare = MagicMock(return_value=plan)
    monkeypatch.setattr(cli, "load_request", lambda _: request)
    monkeypatch.setattr(cli, "prepare_finalization_continuation", prepare)
    monkeypatch.setattr(cli, "load_finalization_authorization", lambda _: object())
    monkeypatch.setattr(cli, "authorize_finalization_continuation", MagicMock())
    captured = []

    def supervise(worker, actual_request):
        captured.append(worker)
        assert actual_request is request
        return SimpleNamespace(status="completed", result=SimpleNamespace(
            stop_reason="completed_needs_review", artifacts={}))

    monkeypatch.setattr(cli, "run_supervised", supervise)
    assert cli.main(["run", "--source-dir", str(tmp_path / "source"),
                     "--config", str(tmp_path / "request.json"),
                     "--authorization-file", str(tmp_path / "auth.json"),
                     "--codex-home", str(runtime), "--allow-live", "--revise-reader",
                     "--pending-origin-dir", str(origin)]) == 0
    assert prepare.call_args.kwargs["pending_origin_dir"] == origin
    assert captured[0].pending_origin_dir == origin.resolve()


def test_worker_reprepares_with_the_same_origin(monkeypatch, tmp_path):
    request, result = object(), object()
    plan = SimpleNamespace(frozen_inputs={"evidence_path": b"frozen"})
    prepare = MagicMock(return_value=plan)
    monkeypatch.setattr(cli, "prepare_finalization_continuation", prepare)
    monkeypatch.setattr(cli, "load_finalization_authorization", lambda _: object())
    monkeypatch.setattr(cli, "authorize_finalization_continuation", lambda *args: object())
    monkeypatch.setattr(cli, "parse_json", lambda _: {})
    monkeypatch.setattr(cli, "EvidenceSnapshot", SimpleNamespace(model_validate=lambda _: object()))
    monkeypatch.setattr(cli, "CodexModelService", MagicMock())
    monkeypatch.setattr(cli, "FinalizationRecoveryModelService", lambda *args: object())
    monkeypatch.setattr(cli, "SnapshotEvidenceService", lambda _: object())
    monkeypatch.setattr(cli, "ResearchServices", lambda *args: object())
    monkeypatch.setattr(cli, "run_research", lambda *args: result)
    origin = tmp_path / "origin"
    worker = cli._FinalizationWorker(tmp_path / "source", tmp_path / "auth.json",
                                    tmp_path / "runtime", revise_reader=True,
                                    pending_origin_dir=origin)
    assert worker(request) is result
    assert prepare.call_args.kwargs == {
        "revise_reader": True, "repair_verification": False, "pending_origin_dir": origin}


def test_invalid_origin_proof_stops_before_authorization_or_supervision(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_request", lambda _: object())
    monkeypatch.setattr(cli, "prepare_finalization_continuation",
                        MagicMock(side_effect=ValueError("origin hash mismatch")))
    authorize, supervise = MagicMock(), MagicMock()
    monkeypatch.setattr(cli, "load_finalization_authorization", authorize)
    monkeypatch.setattr(cli, "run_supervised", supervise)
    assert cli.main(["run", "--source-dir", str(tmp_path / "source"),
                     "--config", str(tmp_path / "request.json"),
                     "--authorization-file", str(tmp_path / "auth.json"),
                     "--codex-home", str(tmp_path / "runtime"), "--allow-live",
                     "--revise-reader", "--pending-origin-dir", str(tmp_path / "origin")]) == 2
    authorize.assert_not_called()
    supervise.assert_not_called()
