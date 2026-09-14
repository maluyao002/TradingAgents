import json
import os
import sys
import time
from pathlib import Path

import pytest

from tradingagents.codex.adapter import (
    CodexAdapter,
    CodexAdapterError,
    CodexAuthenticationError,
    CodexInferenceError,
    CodexSelectionError,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Codex adapter is POSIX-only")


FAKE_ADAPTER_SERVER = r'''
import json
import os
import sys

scenario = sys.argv[1]
log_path = sys.argv[2]
thread_number = 0

def send(message):
    print(json.dumps(message), flush=True)

def model(name, *, hidden=False, modalities=None, efforts=None):
    efforts = ["low", "medium"] if efforts is None else efforts
    return {
        "id": name,
        "model": name,
        "displayName": name.upper(),
        "defaultReasoningEffort": efforts[-1] if efforts else "medium",
        "supportedReasoningEfforts": [
            {"reasoningEffort": effort, "description": "test"} for effort in efforts
        ],
        "inputModalities": ["text"] if modalities is None else modalities,
        "isDefault": name == "gpt-test-terra",
        "hidden": hidden,
    }

features = [
    "apply_patch_freeform", "apps", "code_mode", "memories",
    "multi_agent", "plugins", "shell_tool",
    "unified_exec", "hooks", "plugin_hooks",
]
config_features = {name: False for name in features}
config_features.update(connectors=False, memory_tool=False, skill_search=False,
                       skip_host_skill_discovery=True)

for line in sys.stdin:
    request = json.loads(line)
    with open(log_path, "a") as log:
        log.write(json.dumps(request, sort_keys=True) + "\n")
    if "method" not in request or "id" not in request:
        continue
    method = request["method"]
    request_id = request["id"]
    params = request.get("params", {})

    if method == "initialize":
        codex_home = os.environ["CODEX_HOME"]
        if scenario == "bad-home":
            codex_home = os.path.dirname(codex_home)
        result = {
            "codexHome": codex_home,
            "platformFamily": "unix",
            "platformOs": "macos",
            "userAgent": "private-value",
        }
    elif method == "account/read":
        if scenario == "auth-api":
            result = {"requiresOpenaiAuth": True, "account": {"type": "apiKey"}}
        elif scenario == "auth-absent":
            result = {"requiresOpenaiAuth": True, "account": None}
        elif scenario == "auth-unknown":
            result = {"requiresOpenaiAuth": True, "account": {"type": "futureAuth"}}
        elif scenario == "auth-invalid":
            result = {"requiresOpenaiAuth": "yes", "account": {"type": "chatgpt"}}
        else:
            result = {
                "requiresOpenaiAuth": True,
                "account": {"type": "chatgpt", "email": "private@example.com", "planType": "unknown"},
            }
    elif method == "model/list" and "cursor" not in params:
        first = model("gpt-test-terra")
        hidden = model("gpt-test-hidden", hidden=True)
        image_only = model("gpt-test-image", modalities=["image"])
        if scenario == "bad-catalog":
            first["displayName"] = "/private/secret/<script>"
        result = {"data": [first, hidden, image_only], "nextCursor": "page==2"}
    elif method == "model/list":
        result = {"data": [model("gpt-test-sol", efforts=["high"])], "nextCursor": None}
    elif method == "config/read":
        config = {
            "apps": None,
            "browser_use": None,
            "computer_use": None,
            "developer_instructions": None,
            "forced_login_method": "chatgpt",
            "instructions": None,
            "tools": None,
            "web_search": "disabled",
            "hooks": {},
            "features": dict(config_features),
            "skills": {"bundled": {"enabled": False}, "include_instructions": False},
            "include_environment_context": False,
            "project_doc_max_bytes": 0,
        }
        if scenario == "unsafe-config":
            config["instructions"] = "ambient instruction"
        elif scenario.startswith("missing-config-"):
            config.pop(scenario.removeprefix("missing-config-"))
        elif scenario == "configured-hooks":
            config["hooks"] = {"SessionStart": [{"hooks": [{"type": "command", "command": "unexpected-hook"}]}]}
        elif scenario == "hook-trust-state":
            config["hooks"] = {"state": {"external-hook": {"enabled": True}}}
        elif scenario == "invalid-hooks":
            config["hooks"] = None
        elif scenario == "empty-hook-defaults":
            config["hooks"] = {"SessionStart": [], "state": {}}
        elif scenario.startswith("missing-control-"):
            config["features"].pop(scenario.removeprefix("missing-control-"))
        elif scenario.startswith("unsafe-control-"):
            key = scenario.removeprefix("unsafe-control-")
            config["features"][key] = not config["features"][key]
        elif scenario == "unsafe-skills":
            config["skills"]["bundled"]["enabled"] = True
        elif scenario == "unsafe-skill-instructions":
            config["skills"]["include_instructions"] = True
        result = {"config": config, "origins": {}, "layers": []}
    elif method == "mcpServerStatus/list":
        data = []
        if scenario == "mcp-server":
            data = [{"name": "ambient-server"}]
        result = {"data": data, "nextCursor": None}
    elif method == "experimentalFeature/list":
        advertised = features
        if scenario == "missing-feature":
            advertised = [name for name in features if name != "shell_tool"]
        elif scenario.startswith("missing-hook-feature-"):
            advertised = [name for name in features if name != scenario.removeprefix("missing-hook-feature-")]
        data = [{"name": name, "enabled": False} for name in advertised]
        # The official catalog advertises the execution backend as enabled even
        # when its effective config is false and shell_tool is disabled.
        for feature in data:
            if feature["name"] == "unified_exec":
                feature["enabled"] = True
        if scenario.startswith("enabled-hook-feature-"):
            for feature in data:
                if feature["name"] == scenario.removeprefix("enabled-hook-feature-"):
                    feature["enabled"] = True
        if scenario == "contradictory-catalog":
            data.append({"name": "skill_search", "enabled": True})
        result = {"data": data, "nextCursor": None}
    elif method == "thread/start":
        thread_number += 1
        thread_id = "thread-%s" % thread_number
        result = {
            "thread": {
                "id": thread_id,
                "ephemeral": True,
                "parentThreadId": None,
                "forkedFromId": None,
                "path": None,
            },
            "cwd": params["cwd"],
            "model": params["model"],
            "modelProvider": params["modelProvider"],
            "instructionSources": [],
        }
        if scenario.startswith("warning-"):
            warning_params = {"message": "private runtime detail", "threadId": thread_id}
            if scenario == "warning-global":
                warning_params.pop("threadId")
            elif scenario == "warning-other-thread":
                warning_params["threadId"] = "unknown-thread"
            elif scenario == "warning-malformed":
                warning_params["message"] = {"private": "detail"}
            send({"method": "warning", "params": warning_params})
        if scenario in {"unknown-active-event", "known-active-event"}:
            event_method = "thread/queue/changed" if scenario == "known-active-event" else "future/unsafe"
            send({"method": event_method, "params": {"threadId": thread_id}})
        if scenario == "reused-thread":
            result["thread"]["id"] = "thread-1"
    elif method == "turn/start":
        thread_id = params["threadId"]
        turn_id = "turn-%s" % thread_number
        result = {"turn": {"id": turn_id, "status": "inProgress", "items": []}}
        if scenario.startswith("settings-"):
            settings = {
                "model": params["model"], "effort": params["effort"], "modelProvider": "openai",
                "cwd": params["cwd"], "approvalPolicy": "never", "approvalsReviewer": "user",
                "sandboxPolicy": {"type": "readOnly"},
            }
            if scenario.startswith("settings-wrong-"):
                settings[scenario.removeprefix("settings-wrong-")] = "unexpected"
            event = {"threadId": thread_id, "threadSettings": settings}
            if scenario == "settings-other-thread":
                event["threadId"] = "unknown-thread"
            if scenario == "settings-malformed":
                event["threadSettings"] = None
            send({"method": "thread/settings/updated", "params": event})
        if scenario == "malformed-turn-start":
            result["turn"]["items"] = None
        send({"id": request_id, "result": result})
        if scenario in {"timeout", "malformed-turn-start"}:
            continue
        send({
            "method": "turn/started",
            "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}},
        })
        if scenario == "tool-item":
            item = {"id": "tool-1", "type": "commandExecution", "command": "secret"}
            send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id, "item": item}})
            continue
        item = {"id": "message-1", "type": "agentMessage", "text": "final analysis", "phase": "final_answer"}
        send({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id, "item": item}})
        send({"method": "item/agentMessage/delta", "params": {"threadId": thread_id, "turnId": turn_id, "itemId": "message-1", "delta": "final analysis"}})
        send({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id, "item": item}})
        status = "failed" if scenario == "failed-turn" else "completed"
        send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "items": []}}})
        if scenario == "workspace-write":
            open("PRIVATE_WORKSPACE_MARKER", "w").write("unexpected")
        continue
    elif method == "turn/interrupt":
        result = {}
    elif method == "thread/unsubscribe":
        result = {"status": "unsubscribed"}
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "private error"}})
        continue
    send({"id": request_id, "result": result})
'''


def _command(tmp_path: Path, scenario: str = "normal") -> tuple[list[str], Path]:
    script = tmp_path / "fake_adapter_server.py"
    log = tmp_path / "adapter-requests.jsonl"
    script.write_text(FAKE_ADAPTER_SERVER)
    return [sys.executable, "-u", str(script), scenario, str(log)], log


def _adapter(tmp_path: Path, scenario: str = "normal", timeout: float = 1.0) -> tuple[CodexAdapter, Path]:
    command, log = _command(tmp_path, scenario)
    return CodexAdapter(tmp_path / "runtime", command=command, timeout=timeout), log


def _requests(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_preflight_authenticates_and_paginates_exact_catalog(tmp_path):
    adapter, log = _adapter(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "auth.json").write_text("persistent official login state")

    with adapter:
        models = adapter.list_models()
        assert [entry.model for entry in models] == ["gpt-test-terra", "gpt-test-sol"]
        assert adapter.preflight("gpt-test-sol", "high") == models[1]
        with pytest.raises(CodexSelectionError, match="not advertised"):
            adapter.validate_selection("gpt-test-sol", "medium")

    requests = _requests(log)
    assert [request["method"] for request in requests] == [
        "initialize",
        "initialized",
        "account/read",
        "model/list",
        "model/list",
    ]
    initialize = requests[0]
    assert initialize["params"]["capabilities"] == {
        "experimentalApi": True,
        "requestAttestation": False,
    }
    assert "private@example.com" not in repr(models)


@pytest.mark.parametrize("scenario", ["auth-api", "auth-absent", "auth-unknown", "auth-invalid"])
def test_rejects_non_chatgpt_or_unknown_authentication(tmp_path, scenario):
    adapter, _ = _adapter(tmp_path, scenario)
    with pytest.raises(CodexAuthenticationError, match="ChatGPT"), adapter:
        pass


def test_rejects_wrong_effective_home_without_exposing_it(tmp_path):
    adapter, _ = _adapter(tmp_path, "bad-home")
    with pytest.raises(CodexAdapterError, match="dedicated") as caught, adapter:
        pass
    assert str(tmp_path) not in str(caught.value)


def test_rejects_shared_related_repo_local_and_polluted_homes(tmp_path):
    command, _ = _command(tmp_path)
    with pytest.raises(CodexAdapterError, match="shared"):
        CodexAdapter(Path.home() / ".codex" / "nested", command=command)
    repository_home = Path(__file__).resolve().parents[1] / ".adapter-runtime"
    with pytest.raises(CodexAdapterError, match="outside the repository"):
        CodexAdapter(repository_home, command=command)

    runtime = tmp_path / "polluted"
    (runtime / "nested").mkdir(parents=True)
    (runtime / "nested" / "AGENTS.md").write_text("ambient instructions")
    polluted = CodexAdapter(runtime, command=command)
    with pytest.raises(CodexAdapterError, match="configuration or instruction"), polluted:
        pass


def test_catalog_rejects_unsafe_values_without_echoing_them(tmp_path):
    adapter, _ = _adapter(tmp_path, "bad-catalog")
    with adapter, pytest.raises(CodexAdapterError, match="invalid model") as caught:
        adapter.list_models()
    assert "private" not in str(caught.value).lower()
    assert "script" not in str(caught.value).lower()


def test_complete_uses_isolated_fresh_threads_and_explicit_text_context(tmp_path):
    adapter, log = _adapter(tmp_path)
    instructions = "You are the fundamentals analyst.\nCite each material claim."
    evidence = "Evidence:\nRevenue grew 12%.\nDebt fell 5%."
    with adapter:
        assert adapter.complete(instructions, evidence, "gpt-test-terra", "medium") == "final analysis"
        assert adapter.complete(instructions, evidence, "gpt-test-terra", "medium") == "final analysis"

    requests = _requests(log)
    thread_starts = [request for request in requests if request.get("method") == "thread/start"]
    turns = [request for request in requests if request.get("method") == "turn/start"]
    assert len(thread_starts) == len(turns) == 2
    assert [turn["params"]["threadId"] for turn in turns] == ["thread-1", "thread-2"]
    thread_params = thread_starts[0]["params"]
    assert thread_params["developerInstructions"] == instructions
    assert "Do not call tools" in thread_params["baseInstructions"]
    assert thread_params["dynamicTools"] == []
    assert thread_params["selectedCapabilityRoots"] == []
    assert thread_params["allowProviderModelFallback"] is False
    assert thread_params["ephemeral"] is True
    turn_params = turns[0]["params"]
    assert turn_params["input"] == [{"type": "text", "text": evidence}]
    assert turn_params["model"] == "gpt-test-terra"
    assert turn_params["effort"] == "medium"
    assert len([request for request in requests if request.get("method") == "thread/unsubscribe"]) == 2


def test_complete_forwards_a_copied_output_schema_only_to_turn_start(tmp_path):
    adapter, log = _adapter(tmp_path)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    expected = json.loads(json.dumps(schema))
    with adapter:
        assert adapter.complete(
            "Role",
            "Evidence",
            "gpt-test-terra",
            "medium",
            output_schema=schema,
        ) == "final analysis"

    requests = _requests(log)
    turn = next(request for request in requests if request.get("method") == "turn/start")
    thread = next(request for request in requests if request.get("method") == "thread/start")
    assert turn["params"]["outputSchema"] == expected
    assert "outputSchema" not in thread["params"]
    assert schema == expected


@pytest.mark.parametrize("schema", [{}, {"minimum": float("nan")}, {"type": object()}])
def test_complete_rejects_invalid_output_schema_before_protocol_calls(tmp_path, schema):
    adapter, log = _adapter(tmp_path)
    with adapter, pytest.raises(ValueError, match="output_schema"):
        adapter.complete(
            "Role", "Evidence", "gpt-test-terra", "medium", output_schema=schema
        )
    methods = [request.get("method") for request in _requests(log)]
    assert "model/list" not in methods
    assert "turn/start" not in methods


@pytest.mark.parametrize(
    ("scenario", "match"),
    [
        ("unsafe-config", "not isolated"),
        ("mcp-server", "MCP server"),
        ("missing-feature", "feature isolation"),
    ],
)
def test_complete_fails_closed_when_effective_tool_isolation_is_unproved(tmp_path, scenario, match):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter, pytest.raises(CodexAdapterError, match=match):
        adapter.complete("Role instructions", "Evidence", "gpt-test-terra", "medium")
    assert "thread/start" not in [request.get("method") for request in _requests(log)]


@pytest.mark.parametrize(
    "field",
    [
        "apps",
        "browser_use",
        "computer_use",
        "developer_instructions",
        "forced_login_method",
        "instructions",
        "tools",
        "web_search",
        "hooks",
        "features",
        "skills",
        "include_environment_context",
        "project_doc_max_bytes",
    ],
)
def test_complete_rejects_omitted_effective_config_fields(tmp_path, field):
    adapter, log = _adapter(tmp_path, f"missing-config-{field}")
    with adapter, pytest.raises(CodexAdapterError, match="not isolated"):
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert "thread/start" not in [request.get("method") for request in _requests(log)]


@pytest.mark.parametrize("scenario", [
    "configured-hooks", "hook-trust-state", "invalid-hooks",
    "enabled-hook-feature-hooks", "enabled-hook-feature-plugin_hooks",
    "missing-hook-feature-hooks", "missing-hook-feature-plugin_hooks",
])
def test_hooks_fail_closed_before_starting_a_thread(tmp_path, scenario):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter:
        with pytest.raises(CodexAdapterError):
            adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
        with pytest.raises(CodexAdapterError, match="context manager"):
            adapter.list_models()
    methods = [request.get("method") for request in _requests(log)]
    assert "thread/start" not in methods
    assert "turn/start" not in methods


def test_empty_serialized_hook_defaults_allow_text_completion(tmp_path):
    adapter, _ = _adapter(tmp_path, "empty-hook-defaults")
    with adapter:
        assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"


@pytest.mark.parametrize("name", [
    "connectors", "memory_tool", "skill_search", "skip_host_skill_discovery", "unified_exec",
])
@pytest.mark.parametrize("kind", ["missing-control", "unsafe-control"])
def test_unadvertised_controls_require_explicit_safe_config(tmp_path, name, kind):
    adapter, log = _adapter(tmp_path, f"{kind}-{name}")
    with adapter, pytest.raises(CodexAdapterError, match="feature controls"):
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert "thread/start" not in [request.get("method") for request in _requests(log)]


@pytest.mark.parametrize("scenario", ["unsafe-skills", "unsafe-skill-instructions", "contradictory-catalog"])
def test_equivalent_isolation_controls_cannot_be_enabled(tmp_path, scenario):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter, pytest.raises(CodexAdapterError):
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert "thread/start" not in [request.get("method") for request in _requests(log)]


def test_official_catalog_shape_and_mcp_method_allow_completion(tmp_path):
    adapter, log = _adapter(tmp_path)
    with adapter:
        assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"
    methods = [request.get("method") for request in _requests(log)]
    assert "mcpServerStatus/list" in methods
    assert "mcpServer/status/list" not in methods


@pytest.mark.parametrize(
    ("scenario", "match"),
    [
        ("tool-item", "unexpected tool"),
        ("failed-turn", "did not complete"),
        ("workspace-write", "workspace entry"),
    ],
)
def test_turn_failures_are_safe_interrupt_unsubscribe_and_invalidate(tmp_path, scenario, match):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter:
        with pytest.raises(CodexAdapterError, match=match) as caught:
            adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
        assert str(tmp_path) not in str(caught.value)
        with pytest.raises(CodexAdapterError, match="context manager"):
            adapter.list_models()
    methods = [request.get("method") for request in _requests(log)]
    if scenario != "workspace-write":
        assert "turn/interrupt" in methods
    assert "thread/unsubscribe" in methods


def test_turn_timeout_is_bounded_interrupted_unsubscribed_and_invalidated(tmp_path):
    adapter, log = _adapter(tmp_path, "timeout", timeout=0.2)
    started = time.monotonic()
    with adapter, pytest.raises(CodexInferenceError, match="deadline"):
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert time.monotonic() - started < 1.5
    methods = [request.get("method") for request in _requests(log)]
    assert methods[-2:] == ["turn/interrupt", "thread/unsubscribe"]


def test_malformed_started_turn_is_interrupted_before_unsubscribe(tmp_path):
    adapter, log = _adapter(tmp_path, "malformed-turn-start")
    with adapter:
        with pytest.raises(CodexInferenceError, match="invalid active turn"):
            adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
        with pytest.raises(CodexAdapterError, match="context manager"):
            adapter.list_models()
    requests = _requests(log)
    cleanup = [
        request
        for request in requests
        if request.get("method") in {"turn/interrupt", "thread/unsubscribe"}
    ]
    assert [request["method"] for request in cleanup] == [
        "turn/interrupt",
        "thread/unsubscribe",
    ]
    assert cleanup[0]["params"] == {"threadId": "thread-1", "turnId": "turn-1"}


def test_reused_ephemeral_thread_id_invalidates_adapter(tmp_path):
    adapter, _ = _adapter(tmp_path, "reused-thread")
    with adapter:
        assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"
        with pytest.raises(CodexAdapterError, match="isolation checks"):
            adapter.complete("Role", "More evidence", "gpt-test-terra", "medium")
        with pytest.raises(CodexAdapterError, match="context manager"):
            adapter.list_models()


def test_reentering_adapter_repeats_server_scoped_isolation_checks(tmp_path):
    adapter, log = _adapter(tmp_path)
    for _ in range(2):
        with adapter:
            assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"
    methods = [request.get("method") for request in _requests(log)]
    assert methods.count("config/read") == 2
    assert methods.count("mcpServerStatus/list") == 2
    assert methods.count("experimentalFeature/list") == 2


@pytest.mark.parametrize("scenario", ["warning-thread", "warning-global"])
def test_advisory_warnings_do_not_abort_text_analysis(tmp_path, scenario):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter:
        assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"
    assert "turn/interrupt" not in [request["method"] for request in _requests(log)]


@pytest.mark.parametrize("scenario,match", [
    ("warning-other-thread", "unknown thread"),
    ("warning-malformed", "invalid warning"),
    ("unknown-active-event", "unexpected active-turn"),
])
def test_warning_support_keeps_unknown_and_invalid_events_rejected(tmp_path, scenario, match):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter, pytest.raises(CodexInferenceError, match=match) as caught:
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert "private" not in str(caught.value)
    methods = [request["method"] for request in _requests(log)]
    assert "turn/interrupt" in methods
    assert "thread/unsubscribe" in methods


def test_settings_update_before_turn_response_allows_matching_configuration(tmp_path):
    adapter, _ = _adapter(tmp_path, "settings-valid")
    with adapter:
        assert adapter.complete("Role", "Evidence", "gpt-test-terra", "medium") == "final analysis"


@pytest.mark.parametrize("scenario", [
    "settings-wrong-model", "settings-wrong-effort", "settings-wrong-modelProvider",
    "settings-wrong-cwd", "settings-wrong-approvalPolicy", "settings-wrong-approvalsReviewer",
    "settings-wrong-sandboxPolicy", "settings-other-thread", "settings-malformed",
])
def test_unsafe_settings_updates_abort_and_clean_up(tmp_path, scenario):
    adapter, log = _adapter(tmp_path, scenario)
    with adapter, pytest.raises(CodexInferenceError, match="settings") as caught:
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
    assert str(tmp_path) not in str(caught.value)
    assert "turn/interrupt" in [request["method"] for request in _requests(log)]


def test_known_unhandled_method_is_named_without_payload(tmp_path):
    adapter, _ = _adapter(tmp_path, "known-active-event")
    with adapter, pytest.raises(CodexInferenceError, match=r"notification \(thread/queue/changed\)"):
        adapter.complete("Role", "Evidence", "gpt-test-terra", "medium")
