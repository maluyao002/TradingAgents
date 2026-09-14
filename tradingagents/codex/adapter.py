"""Bounded, isolated text adapter for the official Codex app-server."""

from __future__ import annotations

import math
import os
import re
import tempfile
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from tradingagents.codex.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    CodexAppServerTransport,
    TransportError,
    TransportTimeout,
)


class CodexAdapterError(RuntimeError):
    """A safe-to-display adapter, authentication, or protocol failure."""


class CodexAuthenticationError(CodexAdapterError):
    """The dedicated runtime is not authenticated with a ChatGPT account."""


class CodexSelectionError(CodexAdapterError):
    """The requested model and reasoning effort were not advertised together."""


class CodexInferenceError(CodexAdapterError):
    """A Codex turn did not produce a valid text response."""


@dataclass(frozen=True, slots=True)
class CodexModel:
    """A validated, text-relevant subset of one app-server catalog entry."""

    id: str
    model: str
    display_name: str
    default_effort: str
    supported_efforts: tuple[str, ...]
    input_modalities: tuple[str, ...]
    is_default: bool


_CLIENT_INFO = {
    "name": "tradingagents",
    "title": "TradingAgents",
    "version": "0.4.0",
}
_BASE_INSTRUCTIONS = (
    "Act as the single text-only analytical role described in the developer "
    "instructions. Use only the evidence in the user message. Do not call tools, "
    "inspect files, access the network, or delegate work. Return the requested "
    "analysis as the final answer."
)
_PASSIVE_ITEM_TYPES = frozenset({"agentMessage", "plan", "reasoning", "userMessage"})
_TURN_EVENT_METHODS = frozenset(
    {
        "turn/started",
        "turn/completed",
        "turn/diff/updated",
        "turn/plan/updated",
        "turn/status/changed",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
        "error",
        "model/rerouted",
        "model/safetyBuffering/updated",
        "model/verification",
        "turn/moderationMetadata",
    }
)
_THREAD_ONLY_NOTIFICATIONS = frozenset(
    {"thread/started", "thread/status/changed", "thread/tokenUsage/updated"}
)
_FORBIDDEN_RUNTIME_NAMES = frozenset({"AGENTS.md", "config.toml"})
_MAX_TURN_EVENTS = 10_000
_MAX_OUTPUT_CHARS = 4_000_000
_EXPECTED_FEATURES = {
    "apply_patch_freeform": False,
    "apps": False,
    "code_mode": False,
    "connectors": False,
    "memories": False,
    "memory_tool": False,
    "multi_agent": False,
    "plugins": False,
    "shell_tool": False,
    "skill_search": False,
    "skip_host_skill_discovery": True,
    "unified_exec": False,
}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,255}$")
_SAFE_DISPLAY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:/()'\N{EN DASH}-]{0,127}$")


def _resolve_path(path: str | os.PathLike[str]) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        raise CodexAdapterError("Unable to resolve the isolated Codex directories") from None


def _relative_entries(root: Path) -> set[str]:
    """Inventory all entries without following symlinks."""

    entries: set[str] = set()
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as directory:
            for entry in directory:
                path = Path(entry.path)
                entries.add(path.relative_to(root).as_posix())
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return entries


def _safe_identifier(value: object, *, limit: int = 256) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    return value if _SAFE_IDENTIFIER.fullmatch(value) else None


def _valid_text(value: object, *, limit: int) -> bool:
    if not isinstance(value, str) or not value or len(value) > limit:
        return False
    return all(character in "\t\n\r" or ord(character) >= 32 for character in value)


def _opaque_cursor(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


class CodexAdapter:
    """Context-managed app-server client with an isolated persistent runtime.

    The supplied ``home`` may retain the official Codex login state between
    runs. Configuration and instruction files are forbidden there. Each adapter
    context uses a separate temporary empty workspace, and each ``complete``
    call creates one new ephemeral thread.
    """

    def __init__(
        self,
        home: str | os.PathLike[str],
        *,
        command: Sequence[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.home = _resolve_path(home)
        self.command = tuple(command) if command is not None else None
        self.timeout = float(timeout)
        self._temporary_workspace: tempfile.TemporaryDirectory[str] | None = None
        self._workspace: Path | None = None
        self._transport: CodexAppServerTransport | None = None
        self._models: tuple[CodexModel, ...] | None = None
        self._retired_threads: set[str] = set()
        self._inference_isolation_verified = False
        self._validate_home_location()

    def _validate_home_location(self) -> None:
        shared_home = _resolve_path(Path.home() / ".codex")
        if (
            self.home == shared_home
            or self.home in shared_home.parents
            or shared_home in self.home.parents
        ):
            raise CodexAdapterError("The shared ~/.codex runtime home is not allowed")
        if self.home == _REPOSITORY_ROOT or _REPOSITORY_ROOT in self.home.parents:
            raise CodexAdapterError("The Codex runtime home must be outside the repository")

    def _check_runtime_files(self) -> None:
        if not self.home.exists():
            return
        if not self.home.is_dir():
            raise CodexAdapterError("The Codex runtime home must be a directory")
        entries = _relative_entries(self.home)
        if any(Path(entry).name in _FORBIDDEN_RUNTIME_NAMES for entry in entries):
            raise CodexAdapterError(
                "The Codex runtime home contains configuration or instruction files"
            )

    def _check_workspace_empty(self) -> None:
        workspace = self._workspace
        if workspace is None or not workspace.is_dir() or _relative_entries(workspace):
            raise CodexAdapterError("Codex created an unexpected workspace entry")

    def __enter__(self) -> CodexAdapter:
        if self._transport is not None:
            raise CodexAdapterError("The Codex adapter is already open")
        self._retired_threads.clear()
        self._inference_isolation_verified = False
        try:
            self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._check_runtime_files()
            temporary = tempfile.TemporaryDirectory(prefix="tradingagents-codex-")
            workspace = _resolve_path(temporary.name)
            if (
                workspace == self.home
                or workspace in self.home.parents
                or self.home in workspace.parents
                or workspace == _REPOSITORY_ROOT
                or _REPOSITORY_ROOT in workspace.parents
            ):
                temporary.cleanup()
                raise CodexAdapterError("The temporary Codex workspace is not isolated")
            self._temporary_workspace = temporary
            self._workspace = workspace
            self._check_workspace_empty()
            transport = CodexAppServerTransport(
                self.command,
                home=self.home,
                cwd=workspace,
                timeout=self.timeout,
            ).start()
            self._transport = transport
            deadline = time.monotonic() + self.timeout
            initialized = transport.request(
                "initialize",
                {
                    "clientInfo": _CLIENT_INFO,
                    "capabilities": {
                        "experimentalApi": True,
                        "requestAttestation": False,
                    },
                },
                timeout=self._remaining(deadline),
            )
            self._verify_initialize(initialized)
            transport.notify("initialized", {})
            account = transport.request(
                "account/read",
                {"refreshToken": False},
                timeout=self._remaining(deadline),
            )
            self._verify_account(account)
            self._check_runtime_files()
            self._check_workspace_empty()
            return self
        except CodexAdapterError:
            self._invalidate()
            raise
        except TransportError as exc:
            self._invalidate()
            raise CodexAdapterError(str(exc)) from None
        except OSError:
            self._invalidate()
            raise CodexAdapterError("Unable to prepare or inspect the isolated Codex runtime") from None

    def _verify_initialize(self, result: object) -> None:
        if not isinstance(result, dict):
            raise CodexAdapterError("initialize returned an invalid result")
        effective_home = result.get("codexHome")
        if not isinstance(effective_home, str) or _resolve_path(effective_home) != self.home:
            raise CodexAdapterError("Codex did not use the dedicated runtime home")

    @staticmethod
    def _verify_account(result: object) -> None:
        if not isinstance(result, dict) or result.get("requiresOpenaiAuth") is not True:
            raise CodexAuthenticationError(
                "The dedicated Codex runtime did not report ChatGPT authentication"
            )
        account = result.get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise CodexAuthenticationError(
                "The dedicated Codex runtime requires a ChatGPT subscription login"
            )

    def _require_transport(self) -> CodexAppServerTransport:
        if self._transport is None:
            raise CodexAdapterError("The Codex adapter must be used as a context manager")
        return self._transport

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportTimeout("Codex operation exceeded its deadline")
        return remaining

    def list_models(self) -> tuple[CodexModel, ...]:
        """Return the complete visible model catalog after strict validation."""

        if self._models is not None:
            return self._models
        transport = self._require_transport()
        deadline = time.monotonic() + self.timeout
        models: list[CodexModel] = []
        seen_models: set[str] = set()
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        try:
            for _ in range(100):
                params: dict[str, object] = {"limit": 100, "includeHidden": False}
                if cursor is not None:
                    params["cursor"] = cursor
                result = transport.request(
                    "model/list", params, timeout=self._remaining(deadline)
                )
                if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                    raise CodexAdapterError("model/list returned an invalid page")
                for raw_model in result["data"]:
                    model = self._parse_model(raw_model)
                    if model.model in seen_models or model.id in seen_ids:
                        raise CodexAdapterError("model/list returned duplicate model entries")
                    seen_models.add(model.model)
                    seen_ids.add(model.id)
                    if (
                        isinstance(raw_model, dict)
                        and raw_model.get("hidden") is False
                        and "text" in model.input_modalities
                        and model.supported_efforts
                        and model.default_effort in model.supported_efforts
                    ):
                        models.append(model)
                if len(models) > 10_000:
                    raise CodexAdapterError("model/list exceeded the adapter safety limit")
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    if not models:
                        raise CodexAdapterError("model/list returned no selectable text models")
                    self._models = tuple(models)
                    return self._models
                cursor_value = _opaque_cursor(next_cursor)
                if cursor_value is None or cursor_value in seen_cursors:
                    raise CodexAdapterError("model/list returned an invalid pagination cursor")
                seen_cursors.add(cursor_value)
                cursor = cursor_value
        except TransportError as exc:
            raise CodexAdapterError(str(exc)) from None
        raise CodexAdapterError("model/list exceeded the adapter page limit")

    @staticmethod
    def _parse_model(raw_model: object) -> CodexModel:
        if not isinstance(raw_model, dict):
            raise CodexAdapterError("model/list returned an invalid model")
        model_id = _safe_identifier(raw_model.get("id"))
        model_name = _safe_identifier(raw_model.get("model"))
        raw_display_name = raw_model.get("displayName")
        display_name = (
            raw_display_name
            if isinstance(raw_display_name, str)
            and _SAFE_DISPLAY_NAME.fullmatch(raw_display_name)
            else None
        )
        default_effort = _safe_identifier(raw_model.get("defaultReasoningEffort"), limit=64)
        effort_entries = raw_model.get("supportedReasoningEfforts")
        if None in (model_id, model_name, display_name, default_effort) or not isinstance(
            effort_entries, list
        ):
            raise CodexAdapterError("model/list returned an invalid model")
        efforts: list[str] = []
        for raw_effort in effort_entries:
            effort = (
                _safe_identifier(raw_effort.get("reasoningEffort"), limit=64)
                if isinstance(raw_effort, dict)
                else None
            )
            if effort is None or effort in efforts:
                raise CodexAdapterError("model/list returned invalid reasoning efforts")
            efforts.append(effort)
        raw_modalities = raw_model.get("inputModalities", ["text", "image"])
        if not isinstance(raw_modalities, list):
            raise CodexAdapterError("model/list returned invalid input modalities")
        modalities: list[str] = []
        for raw_modality in raw_modalities:
            modality = _safe_identifier(raw_modality, limit=32)
            if modality is None or modality in modalities:
                raise CodexAdapterError("model/list returned invalid input modalities")
            modalities.append(modality)
        if not isinstance(raw_model.get("isDefault"), bool) or not isinstance(
            raw_model.get("hidden"), bool
        ):
            raise CodexAdapterError("model/list returned an invalid model")
        return CodexModel(
            id=model_id,
            model=model_name,
            display_name=display_name,
            default_effort=default_effort,
            supported_efforts=tuple(efforts),
            input_modalities=tuple(modalities),
            is_default=raw_model["isDefault"],
        )

    def validate_selection(self, model: str, effort: str) -> CodexModel:
        """Validate one exact model/effort pair without aliases or fallback."""

        if _safe_identifier(model) is None or _safe_identifier(effort, limit=64) is None:
            raise CodexSelectionError("Codex model and effort must be non-empty catalog values")
        for entry in self.list_models():
            if entry.model == model:
                if "text" not in entry.input_modalities:
                    raise CodexSelectionError("The requested Codex model does not support text input")
                if effort not in entry.supported_efforts:
                    raise CodexSelectionError(
                        "The requested reasoning effort is not advertised for this Codex model"
                    )
                return entry
        raise CodexSelectionError("The requested Codex model was not advertised")

    def preflight(self, model: str, effort: str) -> CodexModel:
        """Authenticate, discover capabilities, and validate a CLI selection."""

        return self.validate_selection(model, effort)

    def _verify_inference_isolation(self) -> None:
        """Fail closed unless the effective server configuration is inspectable."""

        if self._inference_isolation_verified:
            return
        transport = self._require_transport()
        workspace = self._workspace
        if workspace is None:
            raise CodexAdapterError("The temporary Codex workspace is unavailable")
        deadline = time.monotonic() + self.timeout
        try:
            config_result = transport.request(
                "config/read",
                {"cwd": str(workspace), "includeLayers": True},
                timeout=self._remaining(deadline),
            )
            config = config_result.get("config") if isinstance(config_result, dict) else None
            if not isinstance(config, dict):
                raise CodexAdapterError("Codex effective configuration could not be verified")
            expected_config = {
                "apps": None,
                "browser_use": None,
                "computer_use": None,
                "developer_instructions": None,
                "forced_login_method": "chatgpt",
                "instructions": None,
                "tools": None,
                "web_search": "disabled",
            }
            if any(
                key not in config or config[key] != value
                for key, value in expected_config.items()
            ):
                raise CodexAdapterError("Codex effective configuration is not isolated")

            mcp_cursor: str | None = None
            seen_mcp_cursors: set[str] = set()
            for _ in range(100):
                params: dict[str, object] = {"limit": 100, "detail": "toolsAndAuthOnly"}
                if mcp_cursor is not None:
                    params["cursor"] = mcp_cursor
                mcp_result = transport.request(
                    "mcpServer/status/list", params, timeout=self._remaining(deadline)
                )
                if not isinstance(mcp_result, dict) or mcp_result.get("data") != []:
                    raise CodexAdapterError("Codex reported an available MCP server")
                next_cursor = mcp_result.get("nextCursor")
                if next_cursor is None:
                    break
                mcp_cursor = _opaque_cursor(next_cursor)
                if mcp_cursor is None or mcp_cursor in seen_mcp_cursors:
                    raise CodexAdapterError("Codex returned an invalid MCP pagination cursor")
                seen_mcp_cursors.add(mcp_cursor)
            else:
                raise CodexAdapterError("Codex MCP inventory exceeded the page limit")

            feature_values: dict[str, bool] = {}
            feature_cursor: str | None = None
            seen_feature_cursors: set[str] = set()
            for _ in range(100):
                params = {"limit": 100}
                if feature_cursor is not None:
                    params["cursor"] = feature_cursor
                feature_result = transport.request(
                    "experimentalFeature/list", params, timeout=self._remaining(deadline)
                )
                data = feature_result.get("data") if isinstance(feature_result, dict) else None
                if not isinstance(data, list):
                    raise CodexAdapterError("Codex feature configuration could not be verified")
                for raw_feature in data:
                    name = (
                        _safe_identifier(raw_feature.get("name"))
                        if isinstance(raw_feature, dict)
                        else None
                    )
                    enabled = raw_feature.get("enabled") if isinstance(raw_feature, dict) else None
                    if name is None or not isinstance(enabled, bool) or name in feature_values:
                        raise CodexAdapterError("Codex returned an invalid feature entry")
                    feature_values[name] = enabled
                next_cursor = feature_result.get("nextCursor")
                if next_cursor is None:
                    break
                feature_cursor = _opaque_cursor(next_cursor)
                if feature_cursor is None or feature_cursor in seen_feature_cursors:
                    raise CodexAdapterError("Codex returned an invalid feature pagination cursor")
                seen_feature_cursors.add(feature_cursor)
            else:
                raise CodexAdapterError("Codex feature inventory exceeded the page limit")
            if any(feature_values.get(name) is not expected for name, expected in _EXPECTED_FEATURES.items()):
                raise CodexAdapterError("Codex tool feature isolation could not be verified")
        except TransportError as exc:
            raise CodexAdapterError(str(exc)) from None
        self._inference_isolation_verified = True

    def complete(self, instructions: str, prompt: str, model: str, effort: str) -> str:
        """Run one text-only request in a fresh isolated ephemeral thread."""

        if not _valid_text(instructions, limit=1_000_000):
            raise ValueError("instructions must be non-empty text")
        if not _valid_text(prompt, limit=4_000_000):
            raise ValueError("prompt must be non-empty text")
        self.validate_selection(model, effort)
        self._require_transport()
        try:
            self._verify_inference_isolation()
        except CodexAdapterError:
            self._invalidate()
            raise
        try:
            self._check_workspace_empty()
        except (CodexAdapterError, OSError):
            self._invalidate()
            raise CodexAdapterError("Unable to verify the isolated Codex workspace") from None
        deadline = time.monotonic() + self.timeout
        thread_id: str | None = None
        turn_id: str | None = None
        turn_completed = False
        result: str | None = None
        failure: BaseException | None = None
        try:
            thread_id = self._start_thread(instructions, model, deadline)
            turn_id, turn_is_valid = self._start_turn(
                thread_id, prompt, model, effort, deadline
            )
            if not turn_is_valid:
                raise CodexInferenceError("turn/start returned an invalid active turn")
            result = self._wait_for_turn(thread_id, turn_id, deadline)
            turn_completed = True
        except BaseException as exc:
            failure = exc
        cleanup_error = self._cleanup_thread(
            thread_id,
            turn_id if not turn_completed else None,
        )
        try:
            self._check_workspace_empty()
            self._check_runtime_files()
        except (CodexAdapterError, OSError) as exc:
            if isinstance(exc, OSError):
                exc = CodexAdapterError("Unable to inspect the isolated Codex directories")
            cleanup_error = cleanup_error or exc

        if failure is not None:
            self._invalidate()
            if isinstance(failure, CodexAdapterError):
                raise failure
            if isinstance(failure, TransportTimeout):
                raise CodexInferenceError("Codex inference did not complete before its deadline") from None
            if isinstance(failure, TransportError):
                raise CodexInferenceError(str(failure)) from None
            raise failure
        if cleanup_error is not None:
            self._invalidate()
            if isinstance(cleanup_error, CodexAdapterError):
                raise cleanup_error
            raise CodexInferenceError("Codex thread cleanup failed") from None
        if result is None:  # Defensive; successful turns always set a result.
            raise CodexInferenceError("Codex completed without a final text response")
        return result

    def _start_thread(self, instructions: str, model: str, deadline: float) -> str:
        transport = self._require_transport()
        workspace = self._workspace
        if workspace is None:
            raise CodexAdapterError("The temporary Codex workspace is unavailable")
        result = transport.request(
            "thread/start",
            {
                "allowProviderModelFallback": False,
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "baseInstructions": _BASE_INSTRUCTIONS,
                "cwd": str(workspace),
                "developerInstructions": instructions,
                "dynamicTools": [],
                "environments": [],
                "ephemeral": True,
                "model": model,
                "modelProvider": "openai",
                "runtimeWorkspaceRoots": [],
                "sandbox": "read-only",
                "selectedCapabilityRoots": [],
            },
            timeout=self._remaining(deadline),
        )
        if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
            raise CodexAdapterError("thread/start returned an invalid result")
        thread = result["thread"]
        thread_id = _safe_identifier(thread.get("id"))
        isolated = (
            thread.get("ephemeral") is True
            and thread.get("parentThreadId") is None
            and thread.get("forkedFromId") is None
            and thread.get("path") is None
            and result.get("cwd") == str(workspace)
            and result.get("model") == model
            and result.get("modelProvider") == "openai"
            and result.get("instructionSources") == []
        )
        if thread_id is None or thread_id in self._retired_threads or not isolated:
            self._invalidate()
            raise CodexAdapterError("Ephemeral Codex thread isolation checks failed")
        return thread_id

    def _start_turn(
        self,
        thread_id: str,
        prompt: str,
        model: str,
        effort: str,
        deadline: float,
    ) -> tuple[str, bool]:
        result = self._require_transport().request(
            "turn/start",
            {
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "cwd": str(self._workspace),
                "effort": effort,
                "environments": [],
                "input": [{"type": "text", "text": prompt}],
                "model": model,
                "runtimeWorkspaceRoots": [],
                "threadId": thread_id,
            },
            timeout=self._remaining(deadline),
        )
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = _safe_identifier(turn.get("id")) if isinstance(turn, dict) else None
        if turn_id is None:
            raise CodexInferenceError("turn/start returned an invalid active turn")
        is_valid = turn.get("status") == "inProgress" and isinstance(
            turn.get("items"), list
        )
        return turn_id, is_valid

    def _wait_for_turn(self, thread_id: str, turn_id: str, deadline: float) -> str:
        transport = self._require_transport()
        turn_started = False
        started_items: set[str] = set()
        completed_items: set[str] = set()
        final_messages: list[str] = []
        unphased_messages: list[str] = []
        output_chars = 0
        for _ in range(_MAX_TURN_EVENTS):
            notification = transport.wait_notification(timeout=self._remaining(deadline))
            method = notification.get("method")
            params = notification.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                raise CodexInferenceError("Codex emitted an invalid turn notification")
            event_thread = params.get("threadId")
            if method in _THREAD_ONLY_NOTIFICATIONS:
                if method == "thread/started" and isinstance(params.get("thread"), dict):
                    event_thread = params["thread"].get("id")
                if event_thread in self._retired_threads:
                    continue
                if event_thread != thread_id:
                    raise CodexInferenceError("Codex emitted an event for an unknown thread")
                continue
            if event_thread in self._retired_threads:
                continue
            if method not in _TURN_EVENT_METHODS:
                if event_thread == thread_id or params.get("turnId") == turn_id:
                    raise CodexInferenceError("Codex emitted an unexpected active-turn notification")
                continue
            event_turn = params.get("turnId")
            if event_thread != thread_id:
                raise CodexInferenceError("Codex emitted a turn event for an unknown thread")
            if method.startswith("turn/"):
                embedded_turn = params.get("turn")
                if isinstance(embedded_turn, dict):
                    event_turn = embedded_turn.get("id")
            if event_turn != turn_id:
                raise CodexInferenceError("Codex emitted an event for an unknown turn")

            if method == "turn/started":
                turn = params.get("turn")
                if (
                    turn_started
                    or not isinstance(turn, dict)
                    or turn.get("status") != "inProgress"
                ):
                    raise CodexInferenceError("Codex emitted an invalid turn start event")
                turn_started = True
            elif method in {"item/started", "item/completed"}:
                item = params.get("item")
                if not isinstance(item, dict):
                    raise CodexInferenceError("Codex emitted an invalid item event")
                item_id = _safe_identifier(item.get("id"))
                item_type = item.get("type")
                if item_id is None or item_type not in _PASSIVE_ITEM_TYPES:
                    raise CodexInferenceError("Codex attempted an unexpected tool or item")
                if method == "item/started":
                    if item_id in started_items:
                        raise CodexInferenceError("Codex emitted a duplicate item start event")
                    started_items.add(item_id)
                else:
                    if item_id not in started_items or item_id in completed_items:
                        raise CodexInferenceError("Codex emitted an invalid item completion event")
                    completed_items.add(item_id)
                    if item_type == "agentMessage":
                        text = item.get("text")
                        phase = item.get("phase")
                        if not isinstance(text, str) or phase not in {
                            None,
                            "commentary",
                            "final_answer",
                        }:
                            raise CodexInferenceError("Codex emitted an invalid agent message")
                        if phase == "final_answer":
                            final_messages.append(text)
                        elif phase is None:
                            unphased_messages.append(text)
                        output_chars += len(text)
                        if output_chars > _MAX_OUTPUT_CHARS:
                            raise CodexInferenceError("Codex output exceeded the adapter safety limit")
            elif method == "item/agentMessage/delta":
                if (
                    _safe_identifier(params.get("itemId")) not in started_items
                    or not isinstance(params.get("delta"), str)
                ):
                    raise CodexInferenceError("Codex emitted an invalid agent message delta")
            elif method == "model/rerouted":
                raise CodexInferenceError("Codex rerouted the requested model")
            elif method == "error":
                raise CodexInferenceError("Codex reported a turn error; details redacted")
            elif method == "turn/completed":
                turn = params.get("turn")
                if not turn_started or not isinstance(turn, dict):
                    raise CodexInferenceError("Codex emitted an invalid turn completion event")
                if turn.get("status") != "completed":
                    raise CodexInferenceError("Codex turn did not complete successfully")
                if started_items != completed_items:
                    raise CodexInferenceError("Codex completed with unfinished output items")
                messages = final_messages or unphased_messages
                if not messages or not any(message.strip() for message in messages):
                    raise CodexInferenceError("Codex completed without a final text response")
                return "\n".join(messages)
        raise CodexInferenceError("Codex turn exceeded the notification safety limit")

    def _cleanup_thread(
        self,
        thread_id: str | None,
        active_turn_id: str | None,
    ) -> BaseException | None:
        if thread_id is None:
            return None
        transport = self._transport
        if transport is None:
            return CodexInferenceError("Codex thread cleanup failed")
        cleanup_timeout = min(1.0, self.timeout)
        error: BaseException | None = None
        if active_turn_id is not None:
            try:
                interrupted = transport.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": active_turn_id},
                    timeout=cleanup_timeout,
                )
                if not isinstance(interrupted, dict):
                    raise CodexInferenceError("turn/interrupt returned an invalid result")
            except (TransportError, CodexAdapterError) as exc:
                error = exc
        try:
            unsubscribe = transport.request(
                "thread/unsubscribe",
                {"threadId": thread_id},
                timeout=cleanup_timeout,
            )
            if not isinstance(unsubscribe, dict) or unsubscribe.get("status") not in {
                "notLoaded",
                "notSubscribed",
                "unsubscribed",
            }:
                raise CodexInferenceError("thread/unsubscribe returned an invalid result")
        except (TransportError, CodexAdapterError) as exc:
            error = error or exc
        self._retired_threads.add(thread_id)
        transport.pop_notifications()
        return error

    def close(self) -> None:
        """Close the server and remove the temporary workspace."""

        transport = self._transport
        temporary = self._temporary_workspace
        self._transport = None
        self._temporary_workspace = None
        self._workspace = None
        self._models = None
        self._inference_isolation_verified = False
        try:
            if transport is not None:
                transport.close()
            if temporary is not None:
                temporary.cleanup()
        except OSError:
            raise CodexAdapterError("Unable to clean up the isolated Codex runtime") from None

    def _invalidate(self) -> None:
        with suppress(CodexAdapterError):
            self.close()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._workspace is not None:
                self._check_workspace_empty()
            self._check_runtime_files()
        except (OSError, CodexAdapterError):
            if exc is None:
                self.close()
                raise CodexAdapterError("Unable to verify the isolated Codex directories") from None
        try:
            self.close()
        except CodexAdapterError:
            if exc is None:
                raise
