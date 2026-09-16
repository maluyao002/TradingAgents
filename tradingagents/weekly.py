"""Durable, unattended weekly watchlist execution.

The parent process owns the manifest and never performs inference.  Each
company runs in a disposable child process so stalled data collection or model
work can be terminated without wedging the rest of the batch.
"""

from __future__ import annotations

import copy
import fcntl
import json
import multiprocessing
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset({"completed", "degraded"})
ALL_STATUSES = frozenset(
    {"pending", "running", "completed", "degraded", "failed", "blocked"}
)
DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals")
_SAFE_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.^=-]{0,19}\Z")
_SAFE_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


class WeeklyRunError(RuntimeError):
    """A privacy-safe error suitable for an unattended CLI response."""

    code = "weekly_run_failed"


class WeeklyConfigError(WeeklyRunError):
    code = "invalid_config"


class BatchLockedError(WeeklyRunError):
    code = "batch_locked"


class ManifestMismatchError(WeeklyRunError):
    code = "manifest_config_mismatch"


class PreflightError(WeeklyRunError):
    def __init__(self, code: str, *, shared: bool = True):
        super().__init__(code)
        self.code = code
        self.shared = shared


def _json_bytes(data: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WeeklyRunError("JSON state is not serializable") from exc
    return (text + "\n").encode("utf-8")


def write_json(path: str | os.PathLike[str], data: Mapping[str, Any]) -> Path:
    """Atomically replace a JSON file and fsync both file and directory."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(data)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def read_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and minimally validate a weekly manifest."""

    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyRunError("Manifest is unavailable or invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("companies"), dict)
        or not isinstance(value.get("publication"), dict)
    ):
        raise WeeklyRunError("Manifest schema is invalid")
    return value


@contextmanager
def batch_lock(directory: str | os.PathLike[str]) -> Iterator[Path]:
    """Hold a non-blocking advisory lock for one batch directory."""

    batch_directory = Path(directory)
    batch_directory.mkdir(parents=True, exist_ok=True)
    lock_path = batch_directory / ".lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchLockedError("This weekly batch is already running") from exc
        yield lock_path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WeeklyConfigError(f"{label} must be an object")
    return copy.deepcopy(value)


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise WeeklyConfigError(f"{label} must be positive")
    return float(value)


def _resolve_path(value: Any, *, config_directory: Path, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeeklyConfigError(f"{label} must be a path")
    expanded = Path(os.path.expanduser(value))
    if not expanded.is_absolute():
        expanded = config_directory / expanded
    return str(expanded.resolve())


def _normalize_config(raw: Mapping[str, Any], config_path: Path) -> dict[str, Any]:
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise WeeklyConfigError("Unsupported weekly config schema")

    tickers = raw.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise WeeklyConfigError("tickers must be a non-empty list")
    normalized_tickers: list[str] = []
    for value in tickers:
        ticker = value.strip().upper() if isinstance(value, str) else ""
        if not _SAFE_TICKER.fullmatch(ticker) or ticker in normalized_tickers:
            raise WeeklyConfigError("tickers must be unique safe symbols")
        normalized_tickers.append(ticker)

    analysts = raw.get("analysts", list(DEFAULT_ANALYSTS))
    if (
        not isinstance(analysts, list)
        or not analysts
        or any(value not in DEFAULT_ANALYSTS for value in analysts)
        or len(set(analysts)) != len(analysts)
    ):
        raise WeeklyConfigError("analysts contains an unsupported selection")
    analysts = [value for value in DEFAULT_ANALYSTS if value in analysts]

    backend = raw.get("backend", "codex")
    if backend != "codex":
        raise WeeklyConfigError("The weekly runner requires the Codex backend")
    profile = raw.get("model_profile", "balanced")
    output_language = raw.get("output_language", "English")
    if not isinstance(profile, str) or not isinstance(output_language, str):
        raise WeeklyConfigError("model_profile and output_language must be text")

    timezone = raw.get("timezone", "America/Los_Angeles")
    try:
        ZoneInfo(timezone)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise WeeklyConfigError("timezone is invalid") from exc

    checkpoint_enabled = raw.get("checkpoint_enabled", True)
    if not isinstance(checkpoint_enabled, bool):
        raise WeeklyConfigError("checkpoint_enabled must be a boolean")
    retries = raw.get("transient_retries", 1)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0 or retries > 3:
        raise WeeklyConfigError("transient_retries must be an integer from 0 to 3")

    paths = _require_object(raw.get("paths"), "paths")
    config_directory = config_path.parent.resolve()
    normalized_paths = {
        name: _resolve_path(paths.get(name), config_directory=config_directory, label=name)
        for name in ("output_root", "cache_dir", "memory_log_path", "codex_home")
    }
    data_vendors = _require_object(raw.get("data_vendors", {}), "data_vendors")
    tool_vendors = _require_object(raw.get("tool_vendors", {}), "tool_vendors")
    credentials = _require_object(raw.get("credentials", {}), "credentials")
    for key, value in credentials.items():
        if value is not None and not isinstance(value, str):
            raise WeeklyConfigError(f"credentials.{key} must be text or null")

    return {
        "schema_version": SCHEMA_VERSION,
        "config_path": str(config_path.resolve()),
        "timezone": timezone,
        "tickers": normalized_tickers,
        "analysts": analysts,
        "model_profile": profile,
        "output_language": output_language,
        "backend": backend,
        "checkpoint_enabled": checkpoint_enabled,
        "call_timeout_seconds": _positive_number(
            raw.get("call_timeout_seconds", 300), "call_timeout_seconds"
        ),
        "company_timeout_seconds": _positive_number(
            raw.get("company_timeout_seconds", 1800), "company_timeout_seconds"
        ),
        "termination_grace_seconds": _positive_number(
            raw.get("termination_grace_seconds", 5), "termination_grace_seconds"
        ),
        "transient_retries": retries,
        "paths": normalized_paths,
        "credentials": credentials,
        "data_vendors": data_vendors,
        "tool_vendors": tool_vendors,
        "temperature": raw.get("temperature"),
        "max_tokens": raw.get("max_tokens"),
        "llm_max_retries": raw.get("llm_max_retries", 0),
    }


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load, validate, and resolve a portable weekly JSON configuration."""

    config_path = Path(path).expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeeklyConfigError("Weekly config is unavailable or invalid") from exc
    if not isinstance(raw, dict):
        raise WeeklyConfigError("Weekly config must be a JSON object")
    return _normalize_config(raw, config_path)


def _graph_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build a profile snapshot without inheriting mutable runtime env knobs."""

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.model_profiles import apply_model_profile

    base = copy.deepcopy(DEFAULT_CONFIG)
    base.update(
        llm_provider="openai",
        llm_backend="codex",
        backend_url=None,
        output_language=config["output_language"],
        checkpoint_enabled=config["checkpoint_enabled"],
        data_cache_dir=config["paths"]["cache_dir"],
        results_dir=config["paths"]["output_root"],
        memory_log_path=config["paths"]["memory_log_path"],
        data_vendors=copy.deepcopy(config["data_vendors"]),
        tool_vendors=copy.deepcopy(config["tool_vendors"]),
        temperature=config.get("temperature"),
        max_tokens=config.get("max_tokens"),
        llm_max_retries=config.get("llm_max_retries", 0),
        openai_reasoning_effort=None,
        google_thinking_level=None,
        anthropic_effort=None,
    )
    result = apply_model_profile(base, config["model_profile"])
    result["selected_analysts"] = list(config["analysts"])
    result["report_split_files"] = False
    return result


def _snapshot(config: Mapping[str, Any], graph_config: Mapping[str, Any]) -> dict[str, Any]:
    credential_selectors = {
        key: value
        for key, value in config["credentials"].items()
        if key.endswith(("_service", "_account", "_label", "_env"))
    }
    return {
        "tickers": list(config["tickers"]),
        "analysts": list(config["analysts"]),
        "model_profile": config["model_profile"],
        "model_profile_snapshot": {
            "agent_models": copy.deepcopy(graph_config.get("agent_models", {})),
            "debate_rounds": graph_config["max_debate_rounds"],
            "risk_debate_rounds": graph_config["max_risk_discuss_rounds"],
        },
        "output_language": config["output_language"],
        "backend": config["backend"],
        "checkpoint_enabled": config["checkpoint_enabled"],
        "call_timeout_seconds": config["call_timeout_seconds"],
        "company_timeout_seconds": config["company_timeout_seconds"],
        "termination_grace_seconds": config["termination_grace_seconds"],
        "transient_retries": config["transient_retries"],
        "timezone": config["timezone"],
        "data_vendors": copy.deepcopy(config["data_vendors"]),
        "tool_vendors": copy.deepcopy(config["tool_vendors"]),
        "paths": copy.deepcopy(config["paths"]),
        "credential_selectors": credential_selectors,
        "temperature": config.get("temperature"),
        "max_tokens": config.get("max_tokens"),
        "llm_max_retries": config.get("llm_max_retries"),
        # This contains only application settings and resolved paths. Provider
        # credentials live in the process environment and are never copied in.
        "graph_config": copy.deepcopy(dict(graph_config)),
    }


def _config_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the immutable execution choices of an in-progress batch."""

    required = {
        "tickers",
        "analysts",
        "model_profile",
        "output_language",
        "backend",
        "checkpoint_enabled",
        "call_timeout_seconds",
        "company_timeout_seconds",
        "termination_grace_seconds",
        "transient_retries",
        "timezone",
        "data_vendors",
        "tool_vendors",
        "paths",
        "credential_selectors",
        "graph_config",
    }
    if not isinstance(snapshot, Mapping) or not required.issubset(snapshot):
        raise WeeklyRunError("Manifest config snapshot is incomplete")
    return {
        "schema_version": SCHEMA_VERSION,
        "config_path": None,
        "timezone": snapshot["timezone"],
        "tickers": copy.deepcopy(snapshot["tickers"]),
        "analysts": copy.deepcopy(snapshot["analysts"]),
        "model_profile": snapshot["model_profile"],
        "output_language": snapshot["output_language"],
        "backend": snapshot["backend"],
        "checkpoint_enabled": snapshot["checkpoint_enabled"],
        "call_timeout_seconds": snapshot["call_timeout_seconds"],
        "company_timeout_seconds": snapshot["company_timeout_seconds"],
        "termination_grace_seconds": snapshot["termination_grace_seconds"],
        "transient_retries": snapshot["transient_retries"],
        "paths": copy.deepcopy(snapshot["paths"]),
        "credentials": copy.deepcopy(snapshot["credential_selectors"]),
        "data_vendors": copy.deepcopy(snapshot["data_vendors"]),
        "tool_vendors": copy.deepcopy(snapshot["tool_vendors"]),
        "temperature": snapshot.get("temperature"),
        "max_tokens": snapshot.get("max_tokens"),
        "llm_max_retries": snapshot.get("llm_max_retries", 0),
    }


def _timestamp(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds")


def _analysis_date(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def default_batch_id(timezone: str = "America/Los_Angeles") -> str:
    """Return the most recent Saturday in the configured timezone."""

    today = datetime.now(ZoneInfo(timezone)).date()
    saturday = today - timedelta(days=(today.weekday() - 5) % 7)
    return f"weekly-{saturday.isoformat()}"


def _empty_company() -> dict[str, Any]:
    return {
        "status": "pending",
        "analysis_date": None,
        "report_dir": None,
        "quality": {"accepted": None, "signal": None, "reasons": []},
        "error": None,
        "attempts": [],
    }


def _new_manifest(
    batch_id: str, config: Mapping[str, Any], graph_config: Mapping[str, Any]
) -> dict[str, Any]:
    now = _timestamp(config["timezone"])
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": now,
        "updated_at": now,
        "timezone": config["timezone"],
        "config": _snapshot(config, graph_config),
        "companies": {ticker: _empty_company() for ticker in config["tickers"]},
        "publication": {"folder_id": None, "digest": {}, "companies": {}},
    }


def _touch_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = _timestamp(manifest["timezone"])
    write_json(path, manifest)


def _safe_error(code: str) -> dict[str, str]:
    messages = {
        "authentication_unavailable": "Codex authentication is unavailable.",
        "usage_limit": "Codex usage capacity is unavailable; resume this batch later.",
        "transient_failure": "A temporary Codex failure exhausted the retry allowance.",
        "company_timeout": "The company analysis exceeded its time budget.",
        "worker_crashed": "The isolated company worker exited unexpectedly.",
        "analysis_failed": "The company analysis failed; provider details were redacted.",
        "export_failed": "The saved analysis could not be exported.",
        "preflight_failed": "Weekly preflight failed; details were redacted.",
        "invalid_saved_state": "Saved analysis state is unavailable or invalid.",
    }
    return {"code": code, "message": messages.get(code, "Weekly analysis failed.")}


def _configure_credential_selectors(config: Mapping[str, Any]) -> None:
    credentials = config["credentials"]
    mapping = {
        "fred_keychain_service": "FRED_KEYCHAIN_SERVICE",
        "fred_keychain_label": "FRED_KEYCHAIN_LABEL",
        "fred_keychain_account": "FRED_KEYCHAIN_ACCOUNT",
    }
    for key, env_name in mapping.items():
        value = credentials.get(key)
        if value:
            os.environ[env_name] = value


def _selected_vendor(config: Mapping[str, Any], vendor: str) -> bool:
    values = list(config["data_vendors"].values()) + list(config["tool_vendors"].values())
    return any(
        vendor in {part.strip() for part in value.split(",")}
        for value in values
        if isinstance(value, str)
    )


def _check_writable(path: Path, *, directory: bool) -> None:
    target = path if directory else path.parent
    target.mkdir(parents=True, exist_ok=True)
    descriptor, test_name = tempfile.mkstemp(prefix=".weekly-write-test-", dir=target)
    try:
        os.write(descriptor, b"ok")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        Path(test_name).unlink(missing_ok=True)


def preflight(config: Mapping[str, Any], graph_config: Mapping[str, Any]) -> None:
    """Validate output, selected data credentials, auth, and every model pair."""

    _configure_credential_selectors(config)
    try:
        _check_writable(Path(config["paths"]["output_root"]), directory=True)
        _check_writable(Path(config["paths"]["cache_dir"]), directory=True)
        _check_writable(Path(config["paths"]["memory_log_path"]), directory=False)
        _check_writable(Path(config["paths"]["codex_home"]), directory=True)
    except OSError as exc:
        raise PreflightError("output_unavailable") from exc

    if _selected_vendor(config, "fred"):
        try:
            from tradingagents.dataflows.fred import get_api_key

            get_api_key()
        except Exception as exc:
            raise PreflightError("data_credentials_unavailable") from exc
    if _selected_vendor(config, "alpha_vantage"):
        env_name = config["credentials"].get(
            "alpha_vantage_env", "ALPHA_VANTAGE_API_KEY"
        )
        if not isinstance(env_name, str) or not os.environ.get(env_name):
            raise PreflightError("data_credentials_unavailable")

    from tradingagents.codex.adapter import (
        CodexAdapter,
        CodexAuthenticationError,
        CodexUsageLimitError,
    )

    try:
        with CodexAdapter(
            home=config["paths"]["codex_home"],
            timeout=config["call_timeout_seconds"],
        ) as adapter:
            pairs = {
                (setting["model"], setting["reasoning_effort"])
                for setting in graph_config["agent_models"].values()
            }
            for model, effort in sorted(pairs):
                adapter.preflight(model, effort)
    except CodexAuthenticationError as exc:
        raise PreflightError("authentication_unavailable") from exc
    except CodexUsageLimitError as exc:
        raise PreflightError("usage_limit") from exc
    except Exception as exc:
        raise PreflightError("preflight_failed") from exc


def _report_state(final_state: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "company_of_interest",
        "trade_date",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_debate_state",
        "investment_plan",
        "trader_investment_plan",
        "risk_debate_state",
        "final_trade_decision",
        "research_quality",
        "evidence_packets",
        "prepared_data",
        "_selected_analysts",
        "_research_backend",
        "_run_metadata",
    )
    return {key: copy.deepcopy(final_state[key]) for key in keys if key in final_state}


class _TerminationRequested(BaseException):
    pass


def _default_company_worker(
    ticker: str,
    analysis_date: str,
    request: Mapping[str, Any],
    state_path: str,
) -> dict[str, Any]:
    from cli.stats_handler import StatsCallbackHandler
    from tradingagents.codex.adapter import CodexAdapter
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    stats = StatsCallbackHandler()
    graph_config = copy.deepcopy(request["graph_config"])
    graph_config["results_dir"] = str(Path(state_path).parent / "graph")
    with CodexAdapter(
        home=request["codex_home"], timeout=request["call_timeout_seconds"]
    ) as adapter:
        graph_started = time.perf_counter()
        graph = TradingAgentsGraph(
            request["analysts"],
            config=graph_config,
            callbacks=[stats],
            codex_adapter=adapter,
        )
        graph_setup_seconds = time.perf_counter() - graph_started
        started = time.perf_counter()
        final_state, signal = graph.propagate(ticker, analysis_date)
        final_state["_run_metadata"] = {
            "elapsed_seconds": max(0.0, time.perf_counter() - started),
            "graph_setup_seconds": graph_setup_seconds,
            "codex_startup_seconds": adapter.startup_seconds,
            "usage": stats.get_persistence_stats(),
        }
        # Persist before adapter shutdown: a cleanup failure must not discard a
        # completed and graded research state.
        saved = _report_state(final_state)
        write_json(state_path, saved)
        quality = saved.get("research_quality", {})
    return {
        "ok": True,
        "state_path": str(Path(state_path).resolve()),
        "quality": {
            "accepted": quality.get("accepted") is True,
            "signal": signal,
            "reasons": quality.get("reasons", []),
        },
    }


def _worker_entry(
    connection: Any,
    worker: Callable[[str, str, Mapping[str, Any], str], Mapping[str, Any]],
    ticker: str,
    analysis_date: str,
    request: Mapping[str, Any],
    state_path: str,
) -> None:
    # Third-party/provider diagnostics must never bypass the parent's redacted
    # result protocol or the CLI's one-line JSON contract.
    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    finally:
        os.close(null_fd)
    with suppress(OSError):
        os.setsid()

    def terminate(_signum: int, _frame: Any) -> None:
        raise _TerminationRequested()

    signal.signal(signal.SIGTERM, terminate)
    try:
        result = worker(ticker, analysis_date, request, state_path)
        if not isinstance(result, Mapping):
            raise TypeError("worker result")
        connection.send(dict(result))
    except _TerminationRequested:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"ok": False, "category": "timeout", "code": "company_timeout"})
    except BaseException as exc:
        from tradingagents.codex.adapter import (
            CodexAuthenticationError,
            CodexTransientError,
            CodexUsageLimitError,
        )
        from tradingagents.codex.transport import TransportTimeout

        if isinstance(exc, CodexAuthenticationError):
            category, code = "shared", "authentication_unavailable"
        elif isinstance(exc, CodexUsageLimitError):
            category, code = "shared", "usage_limit"
        elif isinstance(exc, (CodexTransientError, TransportTimeout)):
            category, code = "transient", "transient_failure"
        else:
            category, code = "company", "analysis_failed"
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send({"ok": False, "category": category, "code": code})
    finally:
        connection.close()


def _descendant_processes(root_pid: int) -> set[int]:
    """Best-effort descendant discovery for children that started new sessions."""

    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, list[int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = map(int, fields)
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
    result: set[int] = set()
    pending = list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        result.add(pid)
        pending.extend(children.get(pid, []))
    return result


def _signal_process(pid: int, sig: int, *, group: bool = False) -> None:
    try:
        if group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _stop_worker(process: multiprocessing.Process, grace_seconds: float) -> None:
    descendants = _descendant_processes(process.pid) if process.pid else set()
    if process.pid:
        _signal_process(process.pid, signal.SIGTERM)
    process.join(grace_seconds)
    if process.is_alive():
        for pid in descendants:
            _signal_process(pid, signal.SIGTERM, group=True)
            _signal_process(pid, signal.SIGTERM)
        if process.pid:
            _signal_process(process.pid, signal.SIGKILL)
        process.join(min(grace_seconds, 2.0))
    for pid in descendants:
        _signal_process(pid, signal.SIGKILL, group=True)
        _signal_process(pid, signal.SIGKILL)


def _run_isolated(
    worker: Callable[[str, str, Mapping[str, Any], str], Mapping[str, Any]],
    ticker: str,
    analysis_date: str,
    request: Mapping[str, Any],
    state_path: Path,
    timeout: float,
    grace_seconds: float,
) -> dict[str, Any]:
    # macOS networking/security libraries may abort in a post-fork child before
    # Python can run cleanup. Spawn is slower but safe and is also used in tests
    # so the production process boundary is never masked.
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker_entry,
        args=(child_connection, worker, ticker, analysis_date, request, str(state_path)),
        name=f"weekly-{ticker}",
    )
    process.start()
    child_connection.close()
    deadline = time.monotonic() + timeout
    result: dict[str, Any] | None = None
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if parent_connection.poll(min(0.2, max(0.0, remaining))):
                try:
                    value = parent_connection.recv()
                except EOFError:
                    break
                if isinstance(value, dict):
                    result = value
                break
            if not process.is_alive():
                break
        if result is None and process.is_alive():
            _stop_worker(process, grace_seconds)
            return {"ok": False, "category": "timeout", "code": "company_timeout"}
        process.join(grace_seconds)
        if process.is_alive():
            _stop_worker(process, grace_seconds)
        if result is None:
            exit_code = process.exitcode
            return {
                "ok": False,
                "category": "company",
                "code": "worker_crashed",
                "exit_code": exit_code if isinstance(exit_code, int) else None,
            }
        return result
    finally:
        parent_connection.close()
        if process.is_alive():
            _stop_worker(process, grace_seconds)
        process.close()


def _next_attempt(company: Mapping[str, Any], kind: str) -> int:
    return 1 + sum(
        1 for attempt in company.get("attempts", []) if attempt.get("kind") == kind
    )


def _valid_report(company: Mapping[str, Any]) -> bool:
    report_dir = company.get("report_dir")
    if not isinstance(report_dir, str):
        return False
    directory = Path(report_dir)
    return all((directory / name).is_file() for name in ("complete_report.md", "run_metadata.json"))


def _load_saved_state(company: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Path | None]:
    state_path = company.get("state_path")
    if not isinstance(state_path, str):
        for attempt in reversed(company.get("attempts", [])):
            candidate = attempt.get("state_path") if isinstance(attempt, Mapping) else None
            if isinstance(candidate, str):
                state_path = candidate
                break
    if not isinstance(state_path, str):
        return None, None
    path = Path(state_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, path
    return (value, path) if isinstance(value, dict) else (None, path)


def _quality_from_state(
    state: Mapping[str, Any], graph_config: Mapping[str, Any]
) -> dict[str, Any]:
    from tradingagents.research_quality import assess_research_quality

    quality = assess_research_quality(
        state,
        graph_config.get("selected_analysts"),
        graph_config.get("llm_backend"),
    )
    return {
        "accepted": quality.get("accepted") is True,
        "signal": quality.get("signal", "REVIEW"),
        "reasons": quality.get("reasons", []),
    }


def _export_saved_state(
    manifest_path: Path,
    manifest: dict[str, Any],
    ticker: str,
    batch_root: Path,
    state: Mapping[str, Any],
    graph_config: Mapping[str, Any],
    exporter: Callable[[Mapping[str, Any], str, Path, Mapping[str, Any]], Any],
) -> bool:
    company = manifest["companies"][ticker]
    number = _next_attempt(company, "export")
    destination = batch_root / "companies" / ticker / "reports" / f"attempt-{number}"
    attempt = {
        "kind": "export",
        "number": number,
        "started_at": _timestamp(manifest["timezone"]),
        "finished_at": None,
        "status": "running",
        "output_dir": str(destination.resolve()),
        "error": None,
    }
    company["attempts"].append(attempt)
    _touch_manifest(manifest_path, manifest)
    try:
        exporter(state, ticker, destination, graph_config)
        if not all(
            (destination / name).is_file()
            for name in ("complete_report.md", "run_metadata.json")
        ):
            raise OSError("incomplete export")
    except Exception:
        attempt.update(
            finished_at=_timestamp(manifest["timezone"]),
            status="failed",
            error=_safe_error("export_failed"),
        )
        company.update(status="failed", report_dir=None, error=_safe_error("export_failed"))
        _touch_manifest(manifest_path, manifest)
        return False
    attempt.update(
        finished_at=_timestamp(manifest["timezone"]), status="completed", error=None
    )
    company.update(
        status="completed" if company["quality"].get("accepted") else "degraded",
        report_dir=str(destination.resolve()),
        error=None,
    )
    _touch_manifest(manifest_path, manifest)
    return True


def _default_exporter(
    state: Mapping[str, Any],
    ticker: str,
    destination: Path,
    graph_config: Mapping[str, Any],
) -> Any:
    from tradingagents.reporting import write_report_tree

    return write_report_tree(dict(state), ticker, destination, config=dict(graph_config))


def _block_remaining(
    manifest: dict[str, Any], tickers: list[str], start: int, error: dict[str, str]
) -> None:
    for ticker in tickers[start:]:
        company = manifest["companies"][ticker]
        if company["status"] not in TERMINAL_STATUSES:
            company.update(status="blocked", error=copy.deepcopy(error))


def run_batch(
    config: Mapping[str, Any],
    *,
    dry_run: bool = False,
    preflight_only: bool = False,
    resume: bool = False,
    batch_id: str | None = None,
    tickers: list[str] | tuple[str, ...] | None = None,
    max_companies: int | None = None,
    worker: Callable[[str, str, Mapping[str, Any], str], Mapping[str, Any]] | None = None,
    preflight_check: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
    exporter: Callable[[Mapping[str, Any], str, Path, Mapping[str, Any]], Any] | None = None,
    date_provider: Callable[[str], str] | None = None,
) -> Path:
    """Run a weekly batch and return its absolute manifest path.

    ``worker``, ``preflight_check``, and ``exporter`` are dependency-injection
    seams for offline tests. Production callers should leave them unset.
    """

    normalized = copy.deepcopy(dict(config))
    required = {
        "timezone",
        "tickers",
        "analysts",
        "model_profile",
        "output_language",
        "backend",
        "checkpoint_enabled",
        "call_timeout_seconds",
        "company_timeout_seconds",
        "termination_grace_seconds",
        "transient_retries",
        "paths",
        "credentials",
        "data_vendors",
        "tool_vendors",
    }
    if not required.issubset(normalized):
        raise WeeklyConfigError("Use load_config before run_batch")
    if tickers is not None:
        requested = [value.strip().upper() for value in tickers]
        if (
            not requested
            or len(set(requested)) != len(requested)
            or any(value not in normalized["tickers"] for value in requested)
        ):
            raise WeeklyConfigError("--tickers must be a unique subset of configured tickers")
        normalized["tickers"] = requested
    if (
        max_companies is not None
        and (isinstance(max_companies, bool) or not isinstance(max_companies, int) or max_companies <= 0)
    ):
        raise WeeklyConfigError("max_companies must be a positive integer")
    graph_config = _graph_config(normalized)
    effective_batch_id = batch_id or default_batch_id(normalized["timezone"])
    if not _SAFE_BATCH_ID.fullmatch(effective_batch_id):
        raise WeeklyConfigError("batch_id contains unsupported characters")
    batch_root = (
        Path(normalized["paths"]["output_root"]) / "batches" / effective_batch_id
    ).resolve()
    manifest_path = batch_root / "manifest.json"
    worker = worker or _default_company_worker
    preflight_check = preflight_check or preflight
    exporter = exporter or _default_exporter
    date_provider = date_provider or _analysis_date

    # The output-root lock prevents two different batch IDs from racing the
    # shared Codex home, cache, memory log, or subscription capacity. The
    # batch lock also coordinates manifest updates with publication helpers.
    with ExitStack() as locks:
        locks.enter_context(batch_lock(Path(normalized["paths"]["output_root"])))
        locks.enter_context(batch_lock(batch_root))
        if manifest_path.exists():
            if not resume:
                raise WeeklyRunError("Batch already exists; use --resume")
            manifest = read_manifest(manifest_path)
            if manifest.get("batch_id") != effective_batch_id:
                raise ManifestMismatchError("Manifest batch identity does not match")
            normalized = _config_from_snapshot(manifest.get("config", {}))
            graph_config = copy.deepcopy(manifest["config"]["graph_config"])
            # A process death can leave a company marked running. The saved
            # state, when present, is authoritative and will be exported below.
            for company in manifest["companies"].values():
                if company.get("status") == "running":
                    company["status"] = "pending"
                    company["error"] = _safe_error("worker_crashed")
            _touch_manifest(manifest_path, manifest)
        else:
            manifest = _new_manifest(effective_batch_id, normalized, graph_config)
            _touch_manifest(manifest_path, manifest)

        if dry_run:
            manifest["dry_run"] = True
            _touch_manifest(manifest_path, manifest)
            return manifest_path

        _configure_credential_selectors(normalized)
        if preflight_only:
            try:
                preflight_check(normalized, graph_config)
            except PreflightError as exc:
                error = _safe_error(exc.code if exc.code in {
                    "authentication_unavailable", "usage_limit", "preflight_failed"
                } else "preflight_failed")
                _block_remaining(manifest, normalized["tickers"], 0, error)
                _touch_manifest(manifest_path, manifest)
                return manifest_path
            except Exception:
                error = _safe_error("preflight_failed")
                _block_remaining(manifest, normalized["tickers"], 0, error)
                _touch_manifest(manifest_path, manifest)
                return manifest_path
            manifest.pop("dry_run", None)
            manifest["preflight"] = {
                "status": "passed",
                "checked_at": _timestamp(normalized["timezone"]),
            }
            _touch_manifest(manifest_path, manifest)
            return manifest_path

        tickers_to_run = list(normalized["tickers"])
        inference_tickers: list[str] = []
        eligible = 0
        for ticker in tickers_to_run:
            company = manifest["companies"][ticker]
            if company.get("status") in TERMINAL_STATUSES and _valid_report(company):
                continue
            if max_companies is not None and eligible >= max_companies:
                break
            eligible += 1

            saved_state, saved_path = _load_saved_state(company)
            if saved_state is not None:
                company["state_path"] = str(saved_path.resolve())
                company["quality"] = _quality_from_state(saved_state, graph_config)
                company["phase"] = "state_saved"
                _touch_manifest(manifest_path, manifest)
                _export_saved_state(
                    manifest_path,
                    manifest,
                    ticker,
                    batch_root,
                    saved_state,
                    graph_config,
                    exporter,
                )
                continue
            if saved_path is not None:
                company.pop("state_path", None)
                company["error"] = _safe_error("invalid_saved_state")
                _touch_manifest(manifest_path, manifest)
            inference_tickers.append(ticker)

        if not inference_tickers:
            return manifest_path

        try:
            preflight_check(normalized, graph_config)
        except PreflightError as exc:
            error = _safe_error(exc.code if exc.code in {
                "authentication_unavailable", "usage_limit", "preflight_failed"
            } else "preflight_failed")
            _block_remaining(manifest, tickers_to_run, 0, error)
            _touch_manifest(manifest_path, manifest)
            return manifest_path
        except Exception:
            error = _safe_error("preflight_failed")
            _block_remaining(manifest, tickers_to_run, 0, error)
            _touch_manifest(manifest_path, manifest)
            return manifest_path

        manifest.pop("dry_run", None)
        for ticker in inference_tickers:
            index = tickers_to_run.index(ticker)
            company = manifest["companies"][ticker]

            company_started = time.monotonic()
            successful = False
            for retry in range(normalized["transient_retries"] + 1):
                remaining = normalized["company_timeout_seconds"] - (
                    time.monotonic() - company_started
                )
                if remaining <= 0:
                    result = {
                        "ok": False,
                        "category": "timeout",
                        "code": "company_timeout",
                    }
                    break
                analysis_date = date_provider(normalized["timezone"])
                try:
                    datetime.strptime(analysis_date, "%Y-%m-%d")
                except (TypeError, ValueError) as exc:
                    raise WeeklyRunError("date_provider returned an invalid date") from exc
                number = _next_attempt(company, "analysis")
                state_directory = (
                    batch_root
                    / "companies"
                    / ticker
                    / "state"
                    / f"attempt-{number}"
                )
                state_path = state_directory / "state.json"
                attempt = {
                    "kind": "analysis",
                    "number": number,
                    "retry": retry,
                    "analysis_date": analysis_date,
                    "started_at": _timestamp(normalized["timezone"]),
                    "finished_at": None,
                    "status": "running",
                    "state_path": str(state_path.resolve()),
                    "error": None,
                }
                company["attempts"].append(attempt)
                company.update(
                    status="running",
                    analysis_date=analysis_date,
                    report_dir=None,
                    state_path=str(state_path.resolve()),
                    error=None,
                )
                _touch_manifest(manifest_path, manifest)
                request = {
                    "analysts": list(normalized["analysts"]),
                    "codex_home": normalized["paths"]["codex_home"],
                    "call_timeout_seconds": normalized["call_timeout_seconds"],
                    "graph_config": graph_config,
                }
                result = _run_isolated(
                    worker,
                    ticker,
                    analysis_date,
                    request,
                    state_path,
                    remaining,
                    normalized["termination_grace_seconds"],
                )
                attempt["finished_at"] = _timestamp(normalized["timezone"])
                # The worker writes state before reporting success. Recover it
                # even if its result message or adapter shutdown was interrupted.
                recovered_state, _ = _load_saved_state(company)
                if recovered_state is not None and result.get("ok") is not True:
                    result = {
                        "ok": True,
                        "state_path": str(state_path.resolve()),
                    }
                if result.get("ok") is True:
                    # The parent chooses the attempt path; never let child IPC
                    # redirect persistence to another ticker or an older run.
                    if not state_path.is_file():
                        result = {
                            "ok": False,
                            "category": "company",
                            "code": "analysis_failed",
                        }
                    else:
                        company["state_path"] = str(state_path.resolve())
                        state, _ = _load_saved_state(company)
                        if state is None:
                            result = {
                                "ok": False,
                                "category": "company",
                                "code": "invalid_saved_state",
                            }
                        else:
                            company["quality"] = _quality_from_state(state, graph_config)
                            company["phase"] = "state_saved"
                            attempt.update(status="completed", error=None)
                            # This durable write must happen before report export.
                            _touch_manifest(manifest_path, manifest)
                            successful = _export_saved_state(
                                manifest_path,
                                manifest,
                                ticker,
                                batch_root,
                                state,
                                graph_config,
                                exporter,
                            )
                            break
                if result.get("ok") is not True:
                    code = result.get("code")
                    if code not in {
                        "authentication_unavailable",
                        "usage_limit",
                        "transient_failure",
                        "company_timeout",
                        "worker_crashed",
                        "analysis_failed",
                        "invalid_saved_state",
                    }:
                        code = "analysis_failed"
                    error = _safe_error(code)
                    attempt.update(status="failed", error=error)
                    exit_code = result.get("exit_code")
                    if code == "worker_crashed" and isinstance(exit_code, int):
                        attempt["diagnostics"] = {"worker_exit_code": exit_code}
                        company["diagnostics"] = {"worker_exit_code": exit_code}
                    category = result.get("category")
                    if category == "shared":
                        company.update(status="blocked", error=error)
                        _block_remaining(manifest, tickers_to_run, index + 1, error)
                        _touch_manifest(manifest_path, manifest)
                        return manifest_path
                    if category == "transient" and retry < normalized["transient_retries"]:
                        company.update(status="pending", error=error)
                        _touch_manifest(manifest_path, manifest)
                        continue
                    company.update(status="failed", error=error)
                    _touch_manifest(manifest_path, manifest)
                    break
            if not successful and company["status"] in {"running", "pending"}:
                error = _safe_error(result.get("code", "analysis_failed"))
                company.update(status="failed", error=error)
                _touch_manifest(manifest_path, manifest)
    return manifest_path


__all__ = [
    "BatchLockedError",
    "ManifestMismatchError",
    "PreflightError",
    "WeeklyConfigError",
    "WeeklyRunError",
    "batch_lock",
    "default_batch_id",
    "load_config",
    "preflight",
    "read_manifest",
    "run_batch",
    "write_json",
]
