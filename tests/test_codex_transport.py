import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.codex import transport as transport_module
from tradingagents.codex.transport import (
    CodexAppServerTransport,
    ProtocolError,
    ServerError,
    TransportClosed,
    TransportError,
    TransportTimeout,
    UnexpectedServerRequest,
    build_app_server_command,
    build_child_env,
    transport_failure_diagnostic,
)


@pytest.fixture(autouse=True)
def _posix_transport_tests(request):
    if os.name != "posix" and request.node.name != "test_unsupported_platform_fails_before_launch":
        pytest.skip("Codex transport currently supports macOS/Linux only")


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
elif mode == "redirected-descendant":
    code = "from pathlib import Path\nimport time\ni=0\nwhile True:\n Path('heartbeat').write_text(str(i))\n i+=1\n time.sleep(0.01)"
    child = subprocess.Popen([sys.executable, "-c", code],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists('heartbeat'):
            break
        time.sleep(0.01)
    print(json.dumps({"id": 1, "result": {"pid": child.pid}}), flush=True)
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
        elif mode == "oversized":
            print("x" * 256, flush=True)
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


def _install_fake_codex(
    tmp_path: Path,
    monkeypatch,
    *,
    version: str,
    exit_code: int = 0,
    stderr: str = "",
    delay: float = 0,
) -> Path:
    log = tmp_path / "codex-invocations.jsonl"
    version_script = tmp_path / "fake_codex_version.py"
    version_script.write_text(
        f"""import sys
import time

time.sleep({delay!r})
sys.stdout.write({version!r})
sys.stderr.write({stderr!r})
raise SystemExit({exit_code!r})
"""
    )
    app_command = tuple(_command(tmp_path, "blocked"))
    real_popen = subprocess.Popen

    def intercept_popen(command, *args, **kwargs):
        with log.open("a") as stream:
            stream.write(json.dumps(list(command)) + "\n")
        if list(command) == [app_command[0], "--version"]:
            return real_popen(
                [sys.executable, "-u", str(version_script)], *args, **kwargs
            )
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(transport_module, "build_app_server_command", lambda: app_command)
    monkeypatch.setattr(transport_module.subprocess, "Popen", intercept_popen)
    return log


def test_correlates_response_and_collects_notification(tmp_path):
    with _transport(tmp_path, "normal") as transport:
        assert transport.request("test", {}) == {"ok": True}
        assert transport.pop_notifications() == [
            {"method": "server/note", "params": {"ok": True}}
        ]
        assert transport.pop_notifications() == []


def test_wait_notification_returns_queued_notification_in_wire_order(tmp_path):
    with _transport(tmp_path, "normal") as transport:
        assert transport.request("test", {}) == {"ok": True}
        assert transport.wait_notification(timeout=0.1) == {
            "method": "server/note",
            "params": {"ok": True},
        }
        with pytest.raises(TransportTimeout):
            transport.wait_notification(timeout=0.05)


def test_wait_notification_revalidates_queued_message_shape(tmp_path):
    with _transport(tmp_path, "normal") as transport:
        transport._notifications.append({"method": 7, "params": {}})
        with pytest.raises(ProtocolError, match="invalid method"):
            transport.wait_notification(timeout=0.1)


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), 0, -1])
def test_rejects_non_finite_or_non_positive_deadlines(tmp_path, timeout):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="positive"):
        CodexAppServerTransport(
            _command(tmp_path, "normal"),
            home=tmp_path / "runtime",
            cwd=workspace,
            timeout=timeout,
        )


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
    assert 'cli_auth_credentials_store="file"' in joined
    assert 'forced_login_method="chatgpt"' in joined
    assert "features.hooks=false" in joined
    assert "features.codex_hooks=false" in joined
    assert "features.plugin_hooks=false" in joined
    assert "hooks={}" in joined


@pytest.mark.parametrize("version", ["codex-cli 0.153.4\n", "codex-cli 0.154.0\n", "codex-cli 1.0.0\n"])
def test_default_command_accepts_supported_stable_cli_before_app_server(
    tmp_path, monkeypatch, version
):
    log = _install_fake_codex(tmp_path, monkeypatch, version=version)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = CodexAppServerTransport(
        home=tmp_path / "runtime", cwd=workspace, timeout=0.2
    ).start()
    try:
        assert transport.is_running
        assert [json.loads(line) for line in log.read_text().splitlines()] == [
            [transport.command[0], "--version"],
            list(transport.command),
        ]
    finally:
        transport.close(grace_seconds=0.05)


def test_default_command_allows_bounded_stderr_with_valid_version(tmp_path, monkeypatch):
    log = _install_fake_codex(
        tmp_path,
        monkeypatch,
        version="codex-cli 0.153.4\n",
        stderr="benign launcher warning\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = CodexAppServerTransport(
        home=tmp_path / "runtime", cwd=workspace, timeout=0.2
    ).start()
    try:
        assert transport.is_running
        assert [json.loads(line) for line in log.read_text().splitlines()] == [
            [transport.command[0], "--version"],
            list(transport.command),
        ]
    finally:
        transport.close(grace_seconds=0.05)


@pytest.mark.parametrize(
    "version",
    [
        "codex-cli 0.153.3\n",
        "codex-cli 0.153.4-alpha.1\n",
        "codex-cli 0.200.0-beta.2\n",
    ],
)
def test_default_command_rejects_old_or_prerelease_cli_before_app_server(
    tmp_path, monkeypatch, version
):
    log = _install_fake_codex(tmp_path, monkeypatch, version=version)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = CodexAppServerTransport(
        home=tmp_path / "runtime", cwd=workspace, timeout=0.2
    )
    with pytest.raises(TransportError, match=r"requires Codex CLI 0\.153\.4 or newer; update"):
        transport.start()
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        [transport.command[0], "--version"]
    ]
    assert transport._process is None


@pytest.mark.parametrize(
    ("version", "exit_code", "stderr"),
    [
        ("private malformed /Users/example\n", 0, ""),
        ("x" * 2048, 0, ""),
        ("codex-cli 0.153.4\n", 1, "private failure /Users/example\n"),
        ("codex-cli 0.153.4-alpha.1\n", 0, ""),
    ],
)
def test_default_version_failures_are_actionable_and_redacted(
    tmp_path, monkeypatch, version, exit_code, stderr
):
    _install_fake_codex(
        tmp_path,
        monkeypatch,
        version=version,
        exit_code=exit_code,
        stderr=stderr,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = CodexAppServerTransport(
        home=tmp_path / "runtime", cwd=workspace, timeout=0.2
    )
    with pytest.raises(TransportError) as caught:
        transport.start()
    rendered = str(caught.value)
    assert rendered == (
        "TradingAgents requires Codex CLI 0.153.4 or newer; "
        "update the official Codex CLI"
    )
    assert "private" not in rendered
    assert str(tmp_path) not in rendered


def test_default_version_check_timeout_is_bounded_and_redacted(tmp_path, monkeypatch):
    log = _install_fake_codex(
        tmp_path, monkeypatch, version="codex-cli 0.153.4\n", delay=60
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transport = CodexAppServerTransport(
        home=tmp_path / "runtime", cwd=workspace, timeout=0.1
    )
    started = time.monotonic()
    with pytest.raises(TransportError, match="requires Codex CLI"):
        transport.start()
    assert time.monotonic() - started < 1.0
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        [transport.command[0], "--version"]
    ]
    assert transport._process is None


def test_injected_test_server_command_bypasses_version_detection(tmp_path, monkeypatch):
    def fail_if_called(self):
        raise AssertionError("version detection should be bypassed")

    transport = _transport(tmp_path, "normal")
    monkeypatch.setattr(transport, "_verify_default_cli_version", fail_if_called)
    with transport:
        assert transport.request("test") == {"ok": True}


@pytest.mark.parametrize(
    ("mode", "error_type", "kind"),
    [
        ("malformed", ProtocolError, "malformed_json"),
        ("wrong-id", ProtocolError, "invalid_response"),
        ("server-request", UnexpectedServerRequest, "unexpected_server_request"),
        ("eof", TransportClosed, "closed"),
    ],
)
def test_protocol_failures_are_bounded_and_typed(tmp_path, mode, error_type, kind):
    with _transport(tmp_path, mode) as transport, pytest.raises(error_type) as caught:
        transport.request("test")
    assert transport_failure_diagnostic(caught.value) == {"kind": kind}


def test_oversized_server_message_is_classified_without_payload(tmp_path):
    with _transport(tmp_path, "oversized", message_limit=128) as transport, pytest.raises(ProtocolError) as caught:
        transport.request("test")
    assert transport_failure_diagnostic(caught.value) == {"kind": "response_size_limit"}


def test_server_errors_and_stderr_do_not_expose_raw_details(tmp_path):
    with _transport(tmp_path, "error", stderr_limit=32) as transport:
        with pytest.raises(ServerError) as caught:
            transport.request("test")
        assert "TOP-SECRET" not in str(caught.value)
        assert "TOP-SECRET" not in transport.stderr_summary
        assert len(transport._stderr_capture._buffer) <= 32


def test_transport_diagnostics_are_finite_and_exclude_server_prose(tmp_path):
    with _transport(tmp_path, "error", stderr_limit=32) as transport, pytest.raises(ServerError) as caught:
        transport.request("turn/start", {"secret": "private-prompt-SECRET"})
    assert transport_failure_diagnostic(caught.value) == {
        "kind": "rpc_rejection", "method": "turn/start", "code": -32000,
    }
    assert transport_failure_diagnostic(ServerError("private-method-SECRET", 2**64)) == {
        "kind": "rpc_rejection",
    }
    assert transport_failure_diagnostic(ServerError("turn/start", "private-code-SECRET")) == {
        "kind": "rpc_rejection", "method": "turn/start",
    }


def test_request_size_diagnostic_counts_serialized_bytes_without_payload(tmp_path):
    with _transport(tmp_path, "normal", message_limit=128) as transport, pytest.raises(ProtocolError) as caught:
        transport.request("turn/start", {"text": "private-prompt-SECRET" * 10})
    diagnostic = transport_failure_diagnostic(caught.value)
    assert diagnostic["kind"] == "request_size_limit"
    assert diagnostic["request_bytes"] > diagnostic["limit_bytes"] == 128
    assert set(diagnostic) == {"kind", "request_bytes", "limit_bytes"}
    assert "SECRET" not in str(diagnostic)


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


def test_close_cleans_private_group_even_after_all_pipes_close(tmp_path):
    transport = _transport(tmp_path, "redirected-descendant").start()
    # Receive the child PID before the fake server exits, then let the readers
    # reach EOF. The cleanup must not depend on reader-thread liveness.
    reply = transport._messages.get(timeout=1)
    assert reply["result"]["pid"] > 0
    transport._process.wait(timeout=1)
    transport._stdout_thread.join(timeout=1)
    transport._stderr_thread.join(timeout=1)
    transport.close(grace_seconds=0.1)
    heartbeat = tmp_path / "workspace" / "heartbeat"
    time.sleep(0.05)  # Allow delivery of the process-group kill.
    after_close = heartbeat.read_text()
    time.sleep(0.1)
    assert heartbeat.read_text() == after_close, "Descendant survived transport cleanup"


def test_unsupported_platform_fails_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(transport_module, "os", SimpleNamespace(name="nt"))
    with pytest.raises(transport_module.TransportError, match="macOS/Linux"):
        CodexAppServerTransport(home=tmp_path / "runtime", cwd=tmp_path)
