import json
import sys
from pathlib import Path

import pytest

from tradingagents.codex.probe import ProbeError, run_probe

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


def _command(tmp_path: Path) -> tuple[list[str], Path]:
    script = tmp_path / "fake_probe_server.py"
    log = tmp_path / "methods.log"
    script.write_text(FAKE_PROBE_SERVER)
    return [sys.executable, "-u", str(script), str(log)], log


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
