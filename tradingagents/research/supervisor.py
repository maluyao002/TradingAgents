"""POSIX lifecycle supervisor for trusted callables, not an arbitrary-code sandbox.
Polled parentage retains observed detached/reparented descendants, but cannot track
double-forks before observation. The ps fallback has one-second identity precision.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import re
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from .contracts import ResearchRequest, ResearchResult
from .storage import CheckpointStore, atomic_write, canonical_json, parse_json

try:
    import psutil
except ImportError:
    psutil = None

_POLL = 0.05
_MAX_MESSAGE = 1024 * 1024
_MAX_ARTIFACT = 32 * 1024 * 1024


@dataclass(frozen=True)
class SupervisorResult:
    status: Literal["completed", "timeout", "failed", "interrupted"]
    result: ResearchResult | None
    code: str


def _read_bounded(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("invalid artifact file")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("oversized artifact")
    return content


def remaining_wall_seconds(
    request: ResearchRequest, *, expected_identity: str | None = None
) -> float:
    """Verify manifest-bound resources; engine still validates its service identity.

    Missing resources mean no recorded elapsed, not zero uncheckpointed time.
    Optional identity pins the caller's engine/service identity; worker is opaque.
    """
    directory = request.output_dir.resolve()
    manifest_path = directory / "research_checkpoint.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        if (directory / "stages").exists() or (directory / "stages").is_symlink():
            raise ValueError("resources lack checkpoint manifest")
        return float(request.budget.wall_seconds)
    manifest = parse_json(_read_bounded(manifest_path, _MAX_MESSAGE))
    identity = manifest.get("identity") if isinstance(manifest, dict) else None
    if (
        not isinstance(identity, str)
        or re.fullmatch(r"[0-9a-f]{64}", identity) is None
        or manifest != {"schema_version": 1, "identity": identity}
        or (expected_identity is not None and identity != expected_identity)
    ):
        raise ValueError("invalid checkpoint manifest")
    with CheckpointStore(directory, identity).lock() as store:
        path = store._stage_path("resources")
        if not path.exists():
            return float(request.budget.wall_seconds)
        resources = store.load_stage("resources", {})  # Bounded, identity/hash checked.
        if not isinstance(resources, dict):
            raise ValueError("invalid resources checkpoint inputs")
        elapsed = resources.get("elapsed_seconds")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid recorded elapsed time")
        return max(0.0, request.budget.wall_seconds - elapsed)


def _process_table() -> dict[int, tuple[int, str]]:
    """Read parentage and start identity; never select processes by name."""
    if psutil is not None:
        table = {}
        for process in psutil.process_iter():
            try:
                table[process.pid] = (process.ppid(), str(process.create_time()))
            except psutil.NoSuchProcess:
                continue
        return table
    output = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,lstart="],
        capture_output=True,
        text=True,
        check=True,
        timeout=0.3,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout
    rows = (line.split(maxsplit=2) for line in output.splitlines())
    return {int(pid): (int(parent), birth) for pid, parent, birth in rows}


def _discover(owned: dict[int, str], table: dict[int, tuple[int, str]]) -> None:
    live = {pid for pid, birth in owned.items() if table.get(pid, (None, None))[1] == birth}
    while True:
        additions = {
            pid for pid, (parent, _) in table.items() if parent in live and pid not in live
        }
        if not additions:
            return
        for pid in additions:
            owned[pid] = table[pid][1]
        live.update(additions)


def _signal_owned(owned: dict[int, str], sig: int) -> bool:
    # Refresh immediately before signaling. Never signal a recycled PID or a group.
    table = _process_table()
    success = True
    for pid, birth in owned.items():
        if table.get(pid, (None, None))[1] != birth or pid == os.getpid():
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            success = False
    return success


def _cleanup(process, owned: dict[int, str]) -> bool:
    success = True
    try:
        # Stop spawning before a final discovery, then kill the owned tree.
        _discover(owned, _process_table())
        success = _signal_owned(owned, signal.SIGSTOP)
        _discover(owned, _process_table())
    except Exception:
        success = False
    finally:
        try:
            success = _signal_owned(owned, signal.SIGKILL) and success
        except Exception:
            success = False
        # multiprocessing owns this direct child and has not reaped/reused its PID.
        try:
            if process.is_alive():
                process.kill()
            process.join(timeout=0.5)
        except Exception:
            success = False
    return success and not process.is_alive()


def _worker_entry(worker, request_json: str, status_path: str) -> None:
    # Child messages are fixed codes plus validated JSON; never exception repr/tracebacks.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    try:
        os.setsid()
        request = ResearchRequest.model_validate_json(request_json)
        result = worker(request)
        if not isinstance(result, ResearchResult):
            raise ValueError("invalid worker result")
        result = ResearchResult.model_validate_json(result.model_dump_json())
        payload = result.model_dump(mode="json")
        content = canonical_json({"status": "completed", "code": "completed", "result": payload})
        if len(content) > _MAX_MESSAGE:
            raise ValueError("oversized result")
    except KeyboardInterrupt:
        content = canonical_json({"status": "interrupted", "code": "worker_interrupted"})
    except BaseException:
        content = canonical_json({"status": "failed", "code": "worker_failed"})
    atomic_write(Path(status_path), content)
    # Keep ancestry intact until the parent observes completion and cleans up.
    while True:
        signal.pause()


def _read_status(path: Path, request: ResearchRequest, output: Path) -> SupervisorResult:
    message = parse_json(_read_bounded(path, _MAX_MESSAGE))
    if message == {"status": "failed", "code": "worker_failed"}:
        return SupervisorResult("failed", None, "worker_failed")
    if message == {"status": "interrupted", "code": "worker_interrupted"}:
        return SupervisorResult("interrupted", None, "worker_interrupted")
    if (
        not isinstance(message, dict)
        or set(message) != {"status", "code", "result"}
        or message["status"] != "completed"
        or message["code"] != "completed"
    ):
        raise ValueError("invalid status message")
    result = ResearchResult.model_validate(message["result"])
    if result.ticker != request.ticker or result.cutoff != request.cutoff:
        raise ValueError("result identity mismatch")
    if output.resolve() != output:
        raise ValueError("output directory changed")
    for name, target in result.artifacts.items():
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None:
            raise ValueError("invalid artifact name")
        expected = output / name
        if Path(target).absolute() != expected or expected.resolve() != expected:
            raise ValueError("unsafe artifact path")
        actual_hash = sha256(_read_bounded(expected, _MAX_ARTIFACT)).hexdigest()
        if actual_hash != result.artifact_hashes[name]:
            raise ValueError("artifact hash mismatch")
    return SupervisorResult("completed", result, "completed")


def run_supervised(
    worker: Callable[[ResearchRequest], ResearchResult],
    request: ResearchRequest,
    timeout_seconds: float | None = None,
) -> SupervisorResult:
    """Spawn importable workers (callers need the spawn main guard); preserve checkpoints.
    Invalid arguments raise ValueError; runtime failures return fixed codes. Resume
    uses verified remaining budget; bounded cleanup adds latency after the deadline.
    """
    request = ResearchRequest.model_validate_json(request.model_dump_json())
    timeout = request.budget.wall_seconds if timeout_seconds is None else timeout_seconds
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= request.budget.wall_seconds
    ):
        raise ValueError("timeout must be positive and no larger than request wall budget")
    if os.name != "posix":
        return SupervisorResult("failed", None, "unsupported_platform")
    try:
        timeout = min(timeout, remaining_wall_seconds(request))
    except Exception:
        return SupervisorResult("failed", None, "invalid_checkpoint")
    if timeout <= 0:
        return SupervisorResult("timeout", None, "wall_budget_exhausted")
    try:
        _process_table()  # Fail before spawning if inspection is unavailable.
    except Exception:
        return SupervisorResult("failed", None, "process_inspection_unavailable")
    output = request.output_dir.resolve()
    request = request.model_copy(update={"output_dir": output})
    context = multiprocessing.get_context("spawn")
    owned: dict[int, str] = {}
    outcome = SupervisorResult("failed", None, "worker_failed")
    with tempfile.TemporaryDirectory(prefix="research-supervisor-") as temporary:
        status_path = Path(temporary) / "status.json"
        process = context.Process(
            target=_worker_entry, args=(worker, request.model_dump_json(), str(status_path))
        )
        deadline = time.monotonic() + timeout
        try:
            process.start()
            while True:
                if time.monotonic() >= deadline:
                    outcome = SupervisorResult("timeout", None, "wall_timeout")
                    break
                table = _process_table()
                if not owned and process.pid in table:
                    if table[process.pid][0] != os.getpid():
                        raise ValueError("worker identity mismatch")
                    owned[process.pid] = table[process.pid][1]
                _discover(owned, table)
                if time.monotonic() >= deadline:
                    outcome = SupervisorResult("timeout", None, "wall_timeout")
                    break
                ready = status_path.exists() or status_path.is_symlink()
                if ready or not process.is_alive():
                    if ready or status_path.exists():  # Publication may race worker exit.
                        outcome = _read_status(status_path, request, output)
                    break
                time.sleep(min(_POLL, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            outcome = SupervisorResult("interrupted", None, "parent_interrupted")
        except Exception:
            outcome = SupervisorResult("failed", None, "supervisor_failed")
        finally:
            if process.pid is not None and not _cleanup(process, owned):
                outcome = SupervisorResult(
                    outcome.status if outcome.status != "completed" else "failed",
                    None,
                    "cleanup_failed",
                )
            with suppress(ValueError):
                process.close()
    return outcome
