import os
import sys
import time
from pathlib import Path

import pytest

from tradingagents.codex.transport import (
    CodexAppServerTransport,
    ProtocolError,
    ServerError,
    TransportClosed,
    TransportTimeout,
    UnexpectedServerRequest,
    build_app_server_command,
    build_child_env,
)

FAKE_SERVER = r"""
import json
import os
import subprocess
import sys
import time

mode = sys.argv[1]
if mode == "blocked":
    time.sleep(60)
elif mode == "descendant":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
else:
    for line in sys.stdin:
        request = json.loads(line)
        if mode == "normal":
            print(json.dumps({"method": "server/note", "params": {"ok": True}}), flush=True)
            print(json.dumps({"id": request["id"], "result": {"ok": True}}), flush=True)
        elif mode == "env":
            print(json.dumps({"id": request["id"], "result": dict(os.environ)}), flush=True)
        elif mode == "malformed":
            print("not-json", flush=True)
        elif mode == "wrong-id":
            print(json.dumps({"id": 999, "result": {}}), flush=True)
        elif mode == "server-request":
            print(json.dumps({"id": 44, "method": "item/commandExecution/requestApproval", "params": {"secret": "x"}}), flush=True)
        elif mode == "error":
            sys.stderr.write("TOP-SECRET-SERVER-DETAIL\n" * 10000)
            sys.stderr.flush()
            print(json.dumps({"id": request["id"], "error": {"code": -32000, "message": "TOP-SECRET-SERVER-DETAIL"}}), flush=True)
        elif mode == "eof":
            sys.exit(0)
"""


def _command(tmp_path: Path, mode: str) -> list[str]:
    script = tmp_path / "fake_app_server.py"
    script.write_text(FAKE_SERVER)
    return [sys.executable, "-u", str(script), mode]


def _transport(tmp_path: Path, mode: str, **kwargs) -> CodexAppServerTransport:
    home = tmp_path / "codex-home"
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return CodexAppServerTransport(
        _command(tmp_path, mode), home=home, cwd=cwd, timeout=0.5, **kwargs
    )


def test_correlates_response_and_collects_notification(tmp_path):
    with _transport(tmp_path, "normal") as transport:
        assert transport.request("test", {}) == {"ok": True}
        assert transport.pop_notifications() == [
            {"method": "server/note", "params": {"ok": True}}
        ]
        assert transport.pop_notifications() == []


def test_child_environment_is_allowlisted_and_home_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-leak.invalid")
    monkeypatch.setenv("CODEX_HOME", "/must/not/leak")
    monkeypatch.setenv("CODEX_INSTRUCTIONS", "must-not-leak")
    with _transport(tmp_path, "env") as transport:
        child_env = transport.request("env")
    assert child_env["CODEX_HOME"] == str((tmp_path / "codex-home").resolve())
    assert child_env.get("HOME") == os.environ.get("HOME")
    assert "OPENAI_API_KEY" not in child_env
    assert "OPENAI_BASE_URL" not in child_env
    assert "CODEX_INSTRUCTIONS" not in child_env


def test_build_child_env_rejects_shared_codex_home():
    with pytest.raises(ValueError, match="shared"):
        build_child_env(Path.home() / ".codex")


def test_safe_command_disables_discovery_and_uses_strict_config():
    command = build_app_server_command("codex-test")
    assert command[:4] == ("codex-test", "app-server", "--stdio", "--strict-config")
    joined = " ".join(command)
    assert "features.skip_host_skill_discovery=true" in joined
    assert "skills.include_instructions=false" in joined
    assert 'web_search="disabled"' in joined


@pytest.mark.parametrize(
    ("mode", "error_type"),
    [
        ("malformed", ProtocolError),
        ("wrong-id", ProtocolError),
        ("server-request", UnexpectedServerRequest),
        ("eof", TransportClosed),
    ],
)
def test_protocol_failures_are_bounded_and_typed(tmp_path, mode, error_type):
    with _transport(tmp_path, mode) as transport, pytest.raises(error_type):
        transport.request("test")


def test_server_errors_and_stderr_do_not_expose_raw_details(tmp_path):
    with _transport(tmp_path, "error", stderr_limit=32) as transport:
        with pytest.raises(ServerError) as caught:
            transport.request("test")
        assert "TOP-SECRET" not in str(caught.value)
        assert "TOP-SECRET" not in transport.stderr_summary
        assert len(transport._stderr_capture._buffer) <= 32


def test_request_deadline_includes_blocked_pipe_write(tmp_path):
    transport = _transport(tmp_path, "blocked", message_limit=2 * 1024 * 1024).start()
    started = time.monotonic()
    try:
        with pytest.raises(TransportTimeout):
            transport.request("test", {"payload": "x" * (1024 * 1024)}, timeout=0.1)
        assert time.monotonic() - started < 1.0
    finally:
        transport.close(grace_seconds=0.1)


@pytest.mark.skipif(os.name != "posix", reason="process-group behavior is POSIX-specific")
def test_close_terminates_descendants_holding_stdio(tmp_path):
    transport = _transport(tmp_path, "descendant").start()
    time.sleep(0.1)
    started = time.monotonic()
    transport.close(grace_seconds=0.1)
    assert time.monotonic() - started < 1.0
