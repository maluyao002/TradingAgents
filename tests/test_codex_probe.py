import json
import os
import sys
from pathlib import Path

import pytest

from tradingagents.codex import probe
from tradingagents.codex.probe import ProbeError, run_probe

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Codex probe is POSIX-only")

FAKE_PROBE_SERVER = r"""
import json
import os
import sys

log_path = sys.argv[1]
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    with open(log_path, "a") as log:
        log.write(method + "\n")
    if "id" not in request:
        continue
    request_id = request["id"]
    params = request.get("params", {})
    if method == "initialize":
        assert params["capabilities"]["experimentalApi"] is True
        result = {"codexHome": os.environ["CODEX_HOME"], "platformFamily": "unix", "platformOs": "macos", "userAgent": "secret-device-label"}
    elif method == "account/read":
        result = {"requiresOpenaiAuth": False, "account": {"type": "chatgpt", "email": "private@example.com", "planType": "pro"}}
    elif method == "model/list" and "cursor" not in params:
        result = {"data": [{"id": "terra", "model": "gpt-5.6-terra", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "x"}, {"reasoningEffort": "medium", "description": "x"}], "isDefault": True, "hidden": False}], "nextCursor": "page-2"}
    elif method == "model/list":
        result = {"data": [{"id": "luna", "model": "gpt-5.6-luna", "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [], "isDefault": False, "hidden": False}], "nextCursor": None}
    elif method == "thread/start":
        assert params["ephemeral"] is True
        assert params["environments"] == []
        assert params["allowProviderModelFallback"] is False
        result = {"thread": {"id": "private-thread-id", "ephemeral": True, "parentThreadId": None, "forkedFromId": None, "path": None}, "cwd": params["cwd"], "model": params["model"], "modelProvider": params["modelProvider"], "instructionSources": [], "approvalPolicy": "never", "approvalsReviewer": "user", "sandbox": {}}
    elif method == "thread/list":
        result = {"data": [], "nextCursor": None}
    elif method == "thread/unsubscribe":
        result = {"status": "unsubscribed"}
    else:
        print(json.dumps({"id": request_id, "error": {"code": -32601}}), flush=True)
        continue
    print(json.dumps({"id": request_id, "result": result}), flush=True)
"""


def _command(tmp_path: Path, response_patch: str = "") -> tuple[list[str], Path]:
    script = tmp_path / "fake_probe_server.py"
    log = tmp_path / "methods.log"
    script.write_text(FAKE_PROBE_SERVER.replace(
        '    print(json.dumps({"id": request_id, "result": result}), flush=True)',
        response_patch + '\n    print(json.dumps({"id": request_id, "result": result}), flush=True)',
    ))
    return [sys.executable, "-u", str(script), str(log)], log


@pytest.mark.parametrize("change", [
    'result["modelProvider"] = "other-provider"',
    'result.pop("instructionSources")',
    'result["instructionSources"] = ["unexpected-instructions"]',
    'result["thread"]["ephemeral"] = False',
    'result["model"] = "other-model"',
])
def test_probe_rejects_unverified_thread_isolation(tmp_path, change):
    command, _ = _command(tmp_path, f'    if method == "thread/start":\n        {change}')
    with pytest.raises(ProbeError, match="isolation checks failed"):
        run_probe(home=tmp_path / "runtime", cwd=tmp_path / "workspace",
                  command=command, include_thread=True, timeout=1)


@pytest.mark.parametrize("change", [
    'result.pop("requiresOpenaiAuth")',
    'result["requiresOpenaiAuth"] = "false"',
])
def test_probe_rejects_unknown_authentication_requirement(tmp_path, change):
    command, _ = _command(tmp_path, f'    if method == "account/read":\n        {change}')
    with pytest.raises(ProbeError, match="boolean authentication requirement"):
        run_probe(home=tmp_path / "runtime", cwd=tmp_path / "workspace",
                  command=command, timeout=1)


def test_probe_is_paginated_sanitized_and_never_starts_a_turn(tmp_path):
    command, log = _command(tmp_path)
    home = tmp_path / "isolated-home"
    cwd = tmp_path / "isolated-workspace"
    result = run_probe(
        home=home,
        cwd=cwd,
        command=command,
        include_thread=True,
        timeout=1,
    )
    rendered = json.dumps(result)
    assert "private@example.com" not in rendered
    assert "secret-device-label" not in rendered
    assert "private-thread-id" not in rendered
    assert str(home) not in rendered
    assert result["account"] == {"auth_type": "chatgpt", "requires_openai_auth": False}
    assert [model["id"] for model in result["models"]] == ["terra", "luna"]
    assert result["thread"]["ephemeral"] is True
    methods = log.read_text().splitlines()
    assert methods == [
        "initialize",
        "initialized",
        "account/read",
        "model/list",
        "model/list",
        "thread/start",
        "thread/list",
        "thread/unsubscribe",
    ]
    assert "turn/start" not in methods


def test_probe_requires_fresh_separate_paths(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text("secret")
    with pytest.raises(ProbeError, match="fresh and empty"):
        run_probe(home=home, cwd=tmp_path / "cwd", command=[sys.executable, "-c", ""])

    fresh = tmp_path / "fresh"
    with pytest.raises(ProbeError, match="separate"):
        run_probe(home=fresh, cwd=fresh, command=[sys.executable, "-c", ""])


@pytest.mark.parametrize("include_thread", [False, True])
@pytest.mark.parametrize("mutation", [
    'os.mkdir("unexpected-directory")',
    'os.mkfifo("unexpected-fifo")',
    'import socket; listener = socket.socket(socket.AF_UNIX); listener.bind("unexpected-socket")',
])
def test_probe_rejects_non_file_workspace_entries(tmp_path, include_thread, mutation):
    phase = "thread/start" if include_thread else "initialize"
    command, _ = _command(tmp_path, f'    if method == "{phase}":\n        {mutation}')
    with pytest.raises(ProbeError, match="unexpected workspace entry"):
        run_probe(home=tmp_path / "runtime", cwd=tmp_path / "workspace", command=command,
                  include_thread=include_thread, timeout=1)


def test_entry_inventory_does_not_follow_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "not-in-workspace").touch()
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "empty-directory").mkdir()
    assert probe._relative_entries(root) == {"link", "empty-directory"}


@pytest.mark.parametrize("phase", ["prepare", "inspect"])
def test_probe_redacts_filesystem_failures(tmp_path, monkeypatch, phase):
    command, _ = _command(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError(13, "access denied", "/SYNTHETIC_PRIVATE_PATH/probe")

    if phase == "prepare":
        monkeypatch.setattr(probe, "_empty_directory", denied)
    else:
        monkeypatch.setattr(probe.os, "scandir", denied)
    with pytest.raises(ProbeError, match="Unable to prepare or inspect") as caught:
        run_probe(home=tmp_path / "runtime", cwd=tmp_path / "workspace", command=command)
    assert "SYNTHETIC_PRIVATE_PATH" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("phase", ["resolve", "run"])
def test_cli_redacts_filesystem_failures_before_exit(tmp_path, monkeypatch, capsys, phase):
    def denied(*args, **kwargs):
        raise PermissionError(13, "access denied", "/SYNTHETIC_PRIVATE_PATH/probe")

    if phase == "resolve":
        monkeypatch.setattr(Path, "resolve", denied)
    else:
        monkeypatch.setattr(probe, "run_probe", denied)
    with pytest.raises(SystemExit) as caught:
        probe.main(["--home", str(tmp_path / "runtime")])
    output = capsys.readouterr()
    assert caught.value.code == 1
    assert "SYNTHETIC_PRIVATE_PATH" not in output.err
    assert "Traceback" not in output.err
    assert "probe failed:" in output.err


@pytest.mark.parametrize("use_cli", [False, True])
def test_symlink_loop_resolution_is_path_free(tmp_path, capsys, use_cli):
    loop = tmp_path / "private-symlink-loop"
    loop.symlink_to(loop)
    if use_cli:
        with pytest.raises(SystemExit) as caught:
            probe.main(["--home", str(loop)])
        assert caught.value.code == 1
        output = capsys.readouterr().err
        assert "probe failed:" in output
        assert "private-symlink-loop" not in output
        assert "Traceback" not in output
    else:
        with pytest.raises(ProbeError, match="Unable to resolve") as caught:
            run_probe(home=loop, cwd=tmp_path / "workspace")
        assert "private-symlink-loop" not in str(caught.value)
        assert caught.value.__suppress_context__ is True
