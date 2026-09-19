"""Spawned synthetic workers only; no providers, accounts, or live calls."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pytest

from tradingagents.research import supervisor
from tradingagents.research.contracts import Assessment, Budget, ResearchRequest, ResearchResult
from tradingagents.research.storage import CheckpointStore, atomic_write, canonical_json, digest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX supervision")
SECRET = "private-provider-token-do-not-display"


def request(tmp_path):
    return ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="api",
        output_dir=tmp_path / "output",
        budget=Budget(wall_seconds=10, reserve_seconds=1, call_timeout_seconds=1),
    )


def success_worker(request):
    artifact = request.output_dir / "audit_report.md"
    atomic_write(artifact, b"synthetic research")
    return ResearchResult(
        ticker=request.ticker,
        cutoff=request.cutoff,
        artifacts={artifact.name: str(artifact)},
        artifact_hashes={artifact.name: sha256(artifact.read_bytes()).hexdigest()},
        assessment=Assessment(),
        stop_reason="completed_needs_review",
    )


def error_worker(request):
    print(SECRET, flush=True)
    print(SECRET, file=sys.stderr, flush=True)
    raise RuntimeError(SECRET)


def invalid_worker(request):
    return {"status": "completed", "path": SECRET}


def crash_worker(request):
    os._exit(17)


def interrupted_worker(request):
    raise KeyboardInterrupt(SECRET)


def unsafe_path_worker(request):
    result = success_worker(request)
    return result.model_copy(
        update={"artifacts": {"audit_report.md": str(request.output_dir.parent)}}
    )


def symlink_worker(request):
    result = success_worker(request)
    artifact = request.output_dir / "audit_report.md"
    artifact.unlink()
    artifact.symlink_to(request.output_dir.parent)
    return result


def tampered_artifact_worker(request):
    result = success_worker(request)
    atomic_write(request.output_dir / "audit_report.md", b"changed after hashing")
    return result


def sleeping_worker(request):
    time.sleep(30)


def sleeping_tree_worker(request):
    # A separate session is still owned; a detached grandchild is recorded before
    # its intermediate parent exits, exercising cleanup after reparenting too.
    atomic_write(request.output_dir / "resources.json", canonical_json({"dispatched": True}))
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    grandchild_path = request.output_dir / "grandchild.json"
    intermediate_code = (
        "import subprocess,sys,time,json; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
        "start_new_session=True); "
        "Path(sys.argv[1]).write_text(json.dumps(child.pid)); time.sleep(0.7)"
    )
    intermediate = subprocess.Popen([sys.executable, "-c", intermediate_code, str(grandchild_path)])
    atomic_write(
        request.output_dir / "children.json",
        canonical_json({"child": child.pid, "intermediate": intermediate.pid}),
    )
    time.sleep(30)


def successful_tree_worker(request):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    atomic_write(request.output_dir / "child.json", canonical_json(child.pid))
    return success_worker(request)


def _assert_gone(pid):
    # Zombies have already been killed and cannot execute; init owns their reap.
    for _ in range(30):
        outcome = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=0.3,
        )
        if not outcome.stdout.strip() or outcome.stdout.strip().startswith("Z"):
            return
        time.sleep(0.02)
    pytest.fail(f"owned fixture process {pid} remains alive")


def test_spawn_success_returns_validated_result(tmp_path):
    result = supervisor.run_supervised(success_worker, request(tmp_path), timeout_seconds=5)
    assert result.status == result.code == "completed"
    assert result.result is not None
    assert result.result.assessment.status == "needs_review"
    assert Path(result.result.artifacts["audit_report.md"]).read_bytes() == b"synthetic research"


def test_success_cleans_leftover_detached_child(tmp_path):
    result = supervisor.run_supervised(successful_tree_worker, request(tmp_path), 5)
    assert result.status == "completed"
    _assert_gone(json.loads((request(tmp_path).output_dir / "child.json").read_bytes()))


@pytest.mark.parametrize("worker", [error_worker, invalid_worker, crash_worker])
def test_worker_failure_is_sanitized(tmp_path, capfd, worker):
    result = supervisor.run_supervised(worker, request(tmp_path), timeout_seconds=5)
    assert result.status == "failed" and result.result is None
    assert result.code == "worker_failed"
    assert SECRET not in json.dumps(asdict(result))
    captured = capfd.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("worker", [unsafe_path_worker, symlink_worker, tampered_artifact_worker])
def test_parent_rejects_unsafe_artifact_targets(tmp_path, worker):
    result = supervisor.run_supervised(worker, request(tmp_path), timeout_seconds=5)
    assert result.status == "failed" and result.result is None
    assert result.code == "status_validation_failed"


def test_timeout_kills_detached_and_reparented_descendants_preserves_checkpoint(tmp_path):
    # A unrelated process in this same test parent must survive supervisor cleanup.
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    started = time.monotonic()
    try:
        result = supervisor.run_supervised(sleeping_tree_worker, request(tmp_path), 2.5)
        assert result.status == "timeout" and result.code == "wall_timeout"
        assert time.monotonic() - started < 5
        output = request(tmp_path).output_dir
        assert json.loads((output / "resources.json").read_bytes()) == {"dispatched": True}
        child = json.loads((output / "children.json").read_bytes())["child"]
        grandchild = json.loads((output / "grandchild.json").read_bytes())
        _assert_gone(child)
        _assert_gone(grandchild)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=2)


def test_parent_interrupt_cleans_its_tree(tmp_path, monkeypatch):
    original = supervisor._process_table
    interrupted = False

    def interrupt_after_tracking(timeout_seconds=None):
        nonlocal interrupted
        table = original(timeout_seconds)
        if (request(tmp_path).output_dir / "grandchild.json").exists() and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return table

    monkeypatch.setattr(supervisor, "_process_table", interrupt_after_tracking)
    result = supervisor.run_supervised(sleeping_tree_worker, request(tmp_path), 5)
    assert result.status == "interrupted" and result.code == "parent_interrupted"
    output = request(tmp_path).output_dir
    child = json.loads((output / "children.json").read_bytes())["child"]
    grandchild = json.loads((output / "grandchild.json").read_bytes())
    _assert_gone(child)
    _assert_gone(grandchild)


def test_child_interrupt_is_sanitized(tmp_path):
    result = supervisor.run_supervised(interrupted_worker, request(tmp_path), 5)
    assert result.status == "interrupted" and result.code == "worker_interrupted"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 11, True])
def test_invalid_timeout_cannot_exceed_request_budget(tmp_path, timeout):
    with pytest.raises(ValueError, match="request wall budget"):
        supervisor.run_supervised(success_worker, request(tmp_path), timeout)


def test_inspection_unavailable_fails_before_spawning(tmp_path, monkeypatch):
    calls = 0

    def denied(timeout_seconds=None):
        nonlocal calls
        calls += 1
        raise PermissionError(SECRET)

    monkeypatch.setattr(supervisor, "_process_table", denied)
    result = supervisor.run_supervised(success_worker, request(tmp_path))
    assert result.code == "process_inspection_unavailable"
    assert calls == supervisor._INSPECTION_ATTEMPTS
    assert not request(tmp_path).output_dir.exists()


def test_transient_post_spawn_inspection_recovers(tmp_path, monkeypatch):
    original = supervisor._process_table
    calls = 0

    def inspect(timeout_seconds=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError(SECRET)
        return original(timeout_seconds)

    monkeypatch.setattr(supervisor, "_process_table", inspect)
    result = supervisor.run_supervised(success_worker, request(tmp_path), 5)
    assert result.status == result.code == "completed"
    assert calls >= 3
    assert not (request(tmp_path).output_dir / supervisor._DIAGNOSTIC_NAME).exists()


def test_psutil_access_denial_fails_the_whole_snapshot(monkeypatch):
    class AccessDenied(Exception):
        pass

    class NoSuchProcess(Exception):
        pass

    class ZombieProcess(NoSuchProcess):
        pass

    class DeniedProcess:
        pid = 910001

        def ppid(self):
            raise AccessDenied(SECRET)

        def create_time(self):
            pytest.fail("denied process row should not be retained")

    class FakePsutil:
        @staticmethod
        def process_iter():
            return [DeniedProcess()]

    FakePsutil.AccessDenied = AccessDenied
    FakePsutil.NoSuchProcess = NoSuchProcess
    FakePsutil.ZombieProcess = ZombieProcess
    monkeypatch.setattr(supervisor, "psutil", FakePsutil)
    with pytest.raises(supervisor._TransientInspectionError) as error:
        supervisor._process_table()
    assert SECRET not in str(error.value)


def test_deadline_bound_inspection_never_calls_uninterruptible_psutil(monkeypatch):
    class StalledPsutil:
        @staticmethod
        def process_iter():
            pytest.fail("deadline-bound inspection entered in-process psutil")

    observed = []

    def bounded_run(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args[0], 0, stdout="123 1 Sat Sep 19 08:00:00 2026\n")

    monkeypatch.setattr(supervisor, "psutil", StalledPsutil)
    monkeypatch.setattr(supervisor.subprocess, "run", bounded_run)
    assert supervisor._process_table(0.04) == {123: (1, "Sat Sep 19 08:00:00 2026")}
    assert observed == [0.04]


def test_persistent_post_spawn_inspection_failure_cleans_and_records_fixed_codes(
    tmp_path, monkeypatch
):
    original = supervisor._process_table
    calls = 0
    research_request = request(tmp_path)
    checkpoint(research_request, 1)
    checkpoint_path = research_request.output_dir / "stages/resources.json"
    original_checkpoint = checkpoint_path.read_bytes()

    def inspect(timeout_seconds=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(timeout_seconds)
        raise PermissionError(SECRET)

    monkeypatch.setattr(supervisor, "_process_table", inspect)
    result = supervisor.run_supervised(success_worker, research_request, 5)
    assert result.status == "failed" and result.code == "cleanup_failed"
    assert calls >= 1 + 2 * supervisor._INSPECTION_ATTEMPTS
    diagnostic = json.loads(
        (request(tmp_path).output_dir / supervisor._DIAGNOSTIC_NAME).read_bytes()
    )
    assert diagnostic == {
        "schema_version": 1,
        "status": "failed",
        "code": "cleanup_failed",
        "primary_code": "process_inspection_failed",
    }
    assert SECRET not in json.dumps(diagnostic) + repr(result)
    assert checkpoint_path.read_bytes() == original_checkpoint


def test_inspection_does_not_start_or_retry_past_deadline(monkeypatch):
    calls = 0

    def inspect(timeout_seconds=None):
        nonlocal calls
        calls += 1
        raise AssertionError("inspection ran past deadline")

    monkeypatch.setattr(supervisor, "_process_table", inspect)
    with pytest.raises(supervisor._InspectionDeadlineExceeded):
        supervisor._inspect_process_table(time.monotonic() - 0.001)
    assert calls == 0


def test_malformed_post_spawn_process_table_fails_closed_without_retry(tmp_path, monkeypatch):
    original = supervisor._process_table
    calls = 0

    def inspect(timeout_seconds=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            return supervisor._parse_process_table(f"not-a-pid 1 {SECRET}")
        return original(timeout_seconds)

    monkeypatch.setattr(supervisor, "_process_table", inspect)
    result = supervisor.run_supervised(success_worker, request(tmp_path), 5)
    assert result.status == "failed" and result.code == "process_inspection_invalid"
    diagnostic = json.loads(
        (request(tmp_path).output_dir / supervisor._DIAGNOSTIC_NAME).read_bytes()
    )
    assert diagnostic == {
        "schema_version": 1,
        "status": "failed",
        "code": "process_inspection_invalid",
        "primary_code": "process_inspection_invalid",
    }
    assert SECRET not in json.dumps(diagnostic) + repr(result)


def test_diagnostic_never_overwrites_existing_owned_name(tmp_path):
    output = request(tmp_path).output_dir.resolve()
    output.mkdir(parents=True)
    diagnostic = output / supervisor._DIAGNOSTIC_NAME
    diagnostic.write_bytes(b"original-audit-record")
    outcome = supervisor.SupervisorResult("failed", None, "process_inspection_failed")
    supervisor._write_diagnostic(output, outcome, outcome.code)
    assert diagnostic.read_bytes() == b"original-audit-record"


def test_missing_explicit_timeout_uses_request_wall_budget(tmp_path):
    research_request = request(tmp_path).model_copy(
        update={"budget": Budget(wall_seconds=2, reserve_seconds=0, call_timeout_seconds=1)}
    )
    started = time.monotonic()
    result = supervisor.run_supervised(sleeping_tree_worker, research_request)
    assert result.status == "timeout"
    assert time.monotonic() - started < 4
    output = research_request.output_dir
    _assert_gone(json.loads((output / "children.json").read_bytes())["child"])
    _assert_gone(json.loads((output / "grandchild.json").read_bytes()))


def test_pid_reuse_is_not_signaled_and_unrelated_parentage_is_not_adopted(monkeypatch):
    table = {910001: (1, "new"), 910002: (910001, "child-new"), 910003: (1, "tracked")}
    owned = {910001: "old", 910003: "tracked"}
    supervisor._discover(owned, table)
    assert 910002 not in owned
    monkeypatch.setattr(supervisor, "_process_table", lambda: table)
    signals = []
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    assert supervisor._signal_owned(owned, signal.SIGKILL)
    assert signals == [(910003, signal.SIGKILL)]


def test_protocol_rejects_arbitrary_result_path_and_oversized_status(tmp_path):
    research_request = request(tmp_path)
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps({"status": "completed", "path": SECRET}))
    with pytest.raises(ValueError, match="invalid status"):
        supervisor._read_status(status_path, research_request, research_request.output_dir)
    status_path.write_bytes(b"x" * (supervisor._MAX_MESSAGE + 1))
    with pytest.raises(ValueError, match="oversized"):
        supervisor._read_status(status_path, research_request, research_request.output_dir)


def checkpoint(research_request, elapsed):
    identity = digest({"synthetic": "engine-service-identity"})
    with CheckpointStore(research_request.output_dir, identity).lock() as store:
        store.save_stage("resources", {}, {"elapsed_seconds": elapsed, "dispatched": True})
    return identity


def test_remaining_budget_verified_and_does_not_modify_dispatched_ledger(tmp_path):
    research_request = request(tmp_path)
    identity = checkpoint(research_request, 7.25)
    path = research_request.output_dir / "stages/resources.json"
    original = path.read_bytes()
    assert supervisor.remaining_wall_seconds(research_request, expected_identity=identity) == 2.75
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="manifest"):
        supervisor.remaining_wall_seconds(research_request, expected_identity="0" * 64)


@pytest.mark.parametrize("timeout", [None, 5, 0.3])
def test_resume_caps_runtime_by_recorded_remaining_wall(tmp_path, timeout):
    research_request = request(tmp_path)
    checkpoint(research_request, 9.25)
    started = time.monotonic()
    result = supervisor.run_supervised(sleeping_worker, research_request, timeout)
    assert result.status == "timeout" and result.code == "wall_timeout"
    assert time.monotonic() - started < 1.6


@pytest.mark.parametrize("elapsed", [10, 11])
def test_exhausted_budget_refuses_to_spawn(tmp_path, monkeypatch, elapsed):
    research_request = request(tmp_path)
    checkpoint(research_request, elapsed)
    monkeypatch.setattr(supervisor.multiprocessing, "get_context", lambda *_: pytest.fail("spawn"))
    result = supervisor.run_supervised(success_worker, research_request)
    assert (result.status, result.code, result.result) == ("timeout", "wall_budget_exhausted", None)


@pytest.mark.parametrize("elapsed", [-1, True, "1", None])
def test_invalid_recorded_elapsed_fails_closed(tmp_path, elapsed):
    research_request = request(tmp_path)
    checkpoint(research_request, elapsed)
    result = supervisor.run_supervised(success_worker, research_request)
    assert result.status == "failed" and result.code == "invalid_checkpoint"


@pytest.mark.parametrize("field", ["identity", "inputs_hash", "output_hash", "output"])
def test_corrupt_resources_refuse_fresh_budget(tmp_path, field):
    research_request = request(tmp_path)
    checkpoint(research_request, 9)
    path = research_request.output_dir / "stages/resources.json"
    record = json.loads(path.read_bytes())
    record[field] = {"elapsed_seconds": 0} if field == "output" else "0" * 64
    atomic_write(path, canonical_json(record))
    result = supervisor.run_supervised(success_worker, research_request)
    assert result.status == "failed" and result.code == "invalid_checkpoint"


@pytest.mark.parametrize("target", ["research_checkpoint.json", "stages/resources.json", "stages"])
def test_checkpoint_symlinks_fail_closed(tmp_path, target):
    research_request = request(tmp_path)
    checkpoint(research_request, 9)
    path = research_request.output_dir / target
    moved = tmp_path / "moved"
    path.rename(moved)
    path.symlink_to(moved)
    result = supervisor.run_supervised(success_worker, research_request)
    assert result.code == "invalid_checkpoint"


def test_resources_without_manifest_or_with_locked_destination_are_rejected(tmp_path):
    research_request = request(tmp_path)
    identity = checkpoint(research_request, 9)
    with CheckpointStore(research_request.output_dir, identity).lock():
        assert (
            supervisor.run_supervised(success_worker, research_request).code == "invalid_checkpoint"
        )
    (research_request.output_dir / "research_checkpoint.json").unlink()
    assert supervisor.run_supervised(success_worker, research_request).code == "invalid_checkpoint"


def test_fresh_and_manifest_only_have_full_recorded_budget(tmp_path):
    research_request = request(tmp_path)
    assert supervisor.remaining_wall_seconds(research_request) == 10
    with CheckpointStore(research_request.output_dir, digest({})).lock():
        pass
    assert supervisor.remaining_wall_seconds(research_request) == 10


def test_artifact_reader_is_bounded_and_rejects_nonregular_files(tmp_path, monkeypatch):
    research_request = request(tmp_path)
    result = success_worker(research_request)
    path = tmp_path / "status.json"
    atomic_write(
        path, canonical_json({"status": "completed", "code": "completed", "result": result})
    )
    monkeypatch.setattr(supervisor, "_MAX_ARTIFACT", 3)
    with pytest.raises(ValueError, match="oversized"):
        supervisor._read_status(path, research_request, research_request.output_dir)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="invalid artifact file"):
        supervisor._read_bounded(fifo, 100)


@pytest.mark.parametrize("permanent_denial", [False, True])
def test_cleanup_attempts_kill_after_post_stop_discovery_error(monkeypatch, permanent_denial):
    table = {910001: (os.getpid(), "root"), 910002: (910001, "child")}
    calls = 0
    signals = []

    def inspect(timeout_seconds=None):
        nonlocal calls
        calls += 1
        if calls == 3 or (permanent_denial and calls >= 3):
            raise PermissionError(SECRET)
        return table

    class Child:
        alive = True

        def is_alive(self):
            return self.alive

        def kill(self):
            self.alive = False

        def join(self, timeout):
            assert timeout <= 0.5

    monkeypatch.setattr(supervisor, "_process_table", inspect)
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    child = Child()
    assert supervisor._cleanup(child, {910001: "root"}) is (not permanent_denial)
    assert not child.alive
    assert calls >= 5  # Transient discovery and KILL identity inspection are both retried.
    assert (910002, signal.SIGSTOP) in signals
    assert ((910002, signal.SIGKILL) in signals) is not permanent_denial


@pytest.mark.parametrize("recycled_descendant", [False, True])
def test_cleanup_reserves_fresh_kill_inspection_after_stop_phase_expires(
    monkeypatch, recycled_descendant
):
    table = {910001: (os.getpid(), "root"), 910002: (910001, "child")}
    now = [0.0]
    deadlines = []
    signals = []

    def inspect(deadline):
        deadlines.append(deadline)
        if len(deadlines) == 3:
            # The descendant has already received STOP, then discovery consumes
            # its entire allowance. Final KILL must still get a fresh inspection.
            now[0] = deadline
            raise supervisor._InspectionDeadlineExceeded("discovery expired")
        assert deadline > now[0]
        if len(deadlines) == 4 and recycled_descendant:
            return {**table, 910002: (os.getpid(), "replacement")}
        return table

    class Child:
        alive = True

        def is_alive(self):
            return self.alive

        def kill(self):
            self.alive = False

        def join(self, timeout):
            assert 0 <= timeout <= 0.5
            assert now[0] + timeout <= supervisor._CLEANUP_SECONDS

    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(supervisor, "_inspect_process_table", inspect)
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    child = Child()
    assert not supervisor._cleanup(child, {910001: "root"})
    assert not child.alive
    assert len(deadlines) == 4 and deadlines[-1] > deadlines[-2]
    assert (910002, signal.SIGSTOP) in signals
    assert ((910002, signal.SIGKILL) in signals) is (not recycled_descendant)


def test_cleanup_failure_is_reported_without_secret(tmp_path, monkeypatch):
    original = supervisor._cleanup

    def failed_cleanup(process, owned):
        original(process, owned)
        return False

    monkeypatch.setattr(supervisor, "_cleanup", failed_cleanup)
    result = supervisor.run_supervised(success_worker, request(tmp_path), 5)
    assert result.status == "failed" and result.code == "cleanup_failed"
    assert result.result is None and SECRET not in repr(result)
