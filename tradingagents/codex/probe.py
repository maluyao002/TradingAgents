"""Metadata-only compatibility probe for the official Codex app-server."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tradingagents.codex.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    CodexAppServerTransport,
    TransportError,
    build_app_server_command,
)


class ProbeError(RuntimeError):
    """A safe-to-display compatibility or isolation failure."""


_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ -]{0,127}$")


def _safe_value(value: object) -> str:
    if isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
        return value
    return "unknown"


def _empty_directory(path: Path, label: str) -> None:
    if path.exists():
        if not path.is_dir():
            raise ProbeError(f"{label} must be a directory")
        if any(path.iterdir()):
            raise ProbeError(f"{label} must be fresh and empty")
    else:
        path.mkdir(parents=True, mode=0o700)


def _prepare_paths(home: str | Path, cwd: str | Path) -> tuple[Path, Path]:
    resolved_home = Path(home).expanduser().resolve()
    resolved_cwd = Path(cwd).expanduser().resolve()
    if resolved_home == (Path.home() / ".codex").resolve():
        raise ProbeError("Refusing to use the shared ~/.codex runtime home")
    if resolved_home == resolved_cwd or resolved_home in resolved_cwd.parents or resolved_cwd in resolved_home.parents:
        raise ProbeError("Probe runtime home and workspace must be separate directories")
    _empty_directory(resolved_home, "Probe runtime home")
    _empty_directory(resolved_cwd, "Probe workspace")
    return resolved_home, resolved_cwd


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def _sanitize_account(result: Any) -> dict[str, object]:
    if not isinstance(result, dict):
        raise ProbeError("account/read returned an invalid result")
    if not isinstance(result.get("requiresOpenaiAuth"), bool):
        raise ProbeError("account/read did not report a boolean authentication requirement")
    account = result.get("account")
    account_type: object = None
    if isinstance(account, dict):
        account_type = account.get("type")
    allowed = {"apiKey", "chatgpt", "amazonBedrock"}
    return {
        "auth_type": account_type if account_type in allowed else None,
        "requires_openai_auth": result["requiresOpenaiAuth"],
    }


def _sanitize_model(model: Any) -> dict[str, object]:
    if not isinstance(model, dict):
        raise ProbeError("model/list returned an invalid model")
    efforts = model.get("supportedReasoningEfforts", [])
    if not isinstance(efforts, list):
        raise ProbeError("model/list returned invalid reasoning efforts")
    safe_efforts = []
    for effort in efforts:
        value = effort.get("reasoningEffort") if isinstance(effort, dict) else None
        safe_efforts.append(_safe_value(value))
    return {
        "id": _safe_value(model.get("id")),
        "model": _safe_value(model.get("model")),
        "default_reasoning_effort": _safe_value(model.get("defaultReasoningEffort")),
        "supported_reasoning_efforts": safe_efforts,
        "is_default": model.get("isDefault") is True,
        "hidden": model.get("hidden") is True,
    }


def _list_models(
    transport: CodexAppServerTransport,
) -> tuple[list[dict[str, object]], list[dict[str, Any]]]:
    sanitized: list[dict[str, object]] = []
    raw_models: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(100):
        params: dict[str, object] = {"limit": 100, "includeHidden": False}
        if cursor is not None:
            params["cursor"] = cursor
        result = transport.request("model/list", params)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ProbeError("model/list returned an invalid page")
        for model in result["data"]:
            sanitized.append(_sanitize_model(model))
            if isinstance(model, dict):
                raw_models.append(model)
        if len(sanitized) > 10_000:
            raise ProbeError("model/list exceeded the probe safety limit")
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            return sanitized, raw_models
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise ProbeError("model/list returned an invalid pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ProbeError("model/list exceeded the probe page limit")


def _choose_model(raw_models: list[dict[str, Any]], requested: str | None) -> str:
    if requested:
        return requested
    for model in raw_models:
        if model.get("isDefault") is True and isinstance(model.get("model"), str):
            return model["model"]
    raise ProbeError("No default model was advertised for the ephemeral thread check")


def _probe_thread(
    transport: CodexAppServerTransport,
    *,
    cwd: Path,
    raw_models: list[dict[str, Any]],
    model: str | None,
    model_provider: str | None,
) -> dict[str, object]:
    selected_model = _choose_model(raw_models, model)
    before_files = _relative_files(cwd)
    params: dict[str, object] = {
        "allowProviderModelFallback": False,
        "cwd": str(cwd),
        "environments": [],
        "ephemeral": True,
        "model": selected_model,
        "modelProvider": model_provider or "openai",
        "approvalPolicy": "never",
        "sandbox": "read-only",
    }
    result = transport.request("thread/start", params)
    if not isinstance(result, dict) or not isinstance(result.get("thread"), dict):
        raise ProbeError("thread/start returned an invalid result")
    thread = result["thread"]
    thread_id = thread.get("id")
    checks = {
        "ephemeral": thread.get("ephemeral") is True,
        "no_parent": thread.get("parentThreadId") is None and thread.get("forkedFromId") is None,
        "no_persisted_path": thread.get("path") is None,
        "cwd_matches": result.get("cwd") == str(cwd),
        "model_matches": result.get("model") == selected_model,
        "provider_matches": result.get("modelProvider") == params["modelProvider"],
        "instruction_sources_empty": result.get("instructionSources") == [],
    }
    if not isinstance(thread_id, str) or not all(checks.values()):
        raise ProbeError("Ephemeral thread isolation checks failed")
    listed = transport.request("thread/list", {"limit": 100})
    if not isinstance(listed, dict) or not isinstance(listed.get("data"), list):
        raise ProbeError("thread/list returned an invalid result")
    if any(isinstance(item, dict) and item.get("id") == thread_id for item in listed["data"]):
        raise ProbeError("Ephemeral thread appeared in the persisted thread list")
    unsubscribe = transport.request("thread/unsubscribe", {"threadId": thread_id})
    if not isinstance(unsubscribe, dict) or unsubscribe.get("status") not in {
        "unsubscribed",
        "notSubscribed",
    }:
        raise ProbeError("thread/unsubscribe returned an invalid result")
    if _relative_files(cwd) != before_files:
        raise ProbeError("Ephemeral thread created an unexpected workspace file")
    return {
        **checks,
        "absent_from_persisted_list": True,
        "model": _safe_value(result.get("model")),
        "model_provider": _safe_value(result.get("modelProvider")),
        "unsubscribed": True,
    }


def run_probe(
    *,
    home: str | Path,
    cwd: str | Path,
    command: Sequence[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    include_thread: bool = False,
    model: str | None = None,
    model_provider: str | None = None,
) -> dict[str, object]:
    """Run metadata-only calls; optionally create and unsubscribe an empty thread."""

    probe_home, probe_cwd = _prepare_paths(home, cwd)
    output: dict[str, object] = {
        "probe_version": 1,
        "paid_model_calls": 0,
        "turns_started": 0,
    }
    with CodexAppServerTransport(
        command,
        home=probe_home,
        cwd=probe_cwd,
        timeout=timeout,
    ) as transport:
        initialized = transport.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "tradingagents-codex-probe",
                    "title": "TradingAgents Codex compatibility probe",
                    "version": "1",
                },
                "capabilities": {
                    # Explicitly disabling environment access on thread/start is
                    # an experimental field in CLI 0.153.4.
                    "experimentalApi": True,
                    "requestAttestation": False,
                },
            },
        )
        if not isinstance(initialized, dict):
            raise ProbeError("initialize returned an invalid result")
        if Path(str(initialized.get("codexHome", ""))).resolve() != probe_home:
            raise ProbeError("app-server did not use the isolated runtime home")
        transport.notify("initialized")
        output["server"] = {
            "runtime_home_isolated": True,
            "platform_family": _safe_value(initialized.get("platformFamily")),
            "platform_os": _safe_value(initialized.get("platformOs")),
        }
        output["account"] = _sanitize_account(
            transport.request("account/read", {"refreshToken": False})
        )
        sanitized_models, raw_models = _list_models(transport)
        output["models"] = sanitized_models
        if include_thread:
            output["thread"] = _probe_thread(
                transport,
                cwd=probe_cwd,
                raw_models=raw_models,
                model=model,
                model_provider=model_provider,
            )

    workspace_files = _relative_files(probe_cwd)
    if workspace_files:
        raise ProbeError("Probe created an unexpected workspace file")
    runtime_files = _relative_files(probe_home)
    if any(Path(name).name in {"auth.json", "config.toml", "AGENTS.md"} for name in runtime_files):
        raise ProbeError("Probe runtime contains an unexpected credential or instruction file")
    output["filesystem"] = {
        "workspace_clean": True,
        "runtime_file_count": len(runtime_files),
        "credential_or_instruction_files": False,
    }
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="fresh isolated CODEX_HOME")
    parser.add_argument("--cwd", type=Path, help="fresh isolated workspace")
    parser.add_argument("--codex-bin", default="codex", help="Codex executable")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--include-thread", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--model-provider")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    cwd = args.cwd if args.cwd is not None else home.parent / f"{home.name}-workspace"
    try:
        result = run_probe(
            home=home,
            cwd=cwd,
            command=build_app_server_command(args.codex_bin),
            timeout=args.timeout,
            include_thread=args.include_thread,
            model=args.model,
            model_provider=args.model_provider,
        )
    except (ProbeError, TransportError, OSError, ValueError) as exc:
        parser.exit(1, f"probe failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
