"""Regression coverage for timeout cleanup across process sessions."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tradingagents.weekly import _run_isolated


def detached_sleep_worker(_ticker, _analysis_date, request, _state_path):
    """Start a child outside the worker session, then wait to be terminated."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(15)"],
        start_new_session=True,
    )
    Path(request["child_pid_path"]).write_text(str(child.pid), encoding="utf-8")
    while True:
        time.sleep(60)


def _gone_or_zombie(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        timeout=1,
    ).stdout.strip()
    return not status or status.startswith("Z")


@pytest.mark.unit
def test_timeout_kills_detached_worker_descendant(tmp_path):
    pid_path = tmp_path / "detached-child.pid"
    started = time.monotonic()
    result = _run_isolated(
        detached_sleep_worker,
        "AMD",
        "2026-09-16",
        {"child_pid_path": str(pid_path)},
        tmp_path / "state.json",
        timeout=2.0,
        grace_seconds=0.2,
    )

    assert result == {"ok": False, "category": "timeout", "code": "company_timeout"}
    assert time.monotonic() - started < 5
    assert pid_path.is_file()
    pid = int(pid_path.read_text(encoding="utf-8"))

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _gone_or_zombie(pid):
        time.sleep(0.05)
    assert _gone_or_zombie(pid)
