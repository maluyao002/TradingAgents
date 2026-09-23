"""Synchronous, bounded stdio transport for the Codex app-server protocol."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import select
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_STDERR_LIMIT = 8 * 1024
DEFAULT_MESSAGE_LIMIT = 4 * 1024 * 1024
MIN_CODEX_CLI_VERSION = (0, 153, 4)
_VERSION_OUTPUT_LIMIT = 1024
_VERSION_ERROR = (
    "TradingAgents requires Codex CLI 0.153.4 or newer; "
    "update the official Codex CLI"
)

# These switches keep the metadata-only probe from discovering host instructions,
# tools, skills, apps, plugins, connectors, memories, or web capabilities.
_DISABLED_CONFIG = (
    'cli_auth_credentials_store="file"',
    'forced_login_method="chatgpt"',
    "features.hooks=false",
    "features.codex_hooks=false",
    "features.plugin_hooks=false",
    "hooks={}",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.apply_patch_freeform=false",
    "features.apps=false",
    "features.plugins=false",
    "features.connectors=false",
    "features.code_mode=false",
    "features.multi_agent=false",
    "features.memories=false",
    "features.memory_tool=false",
    "features.skill_search=false",
    "features.skip_host_skill_discovery=true",
    "skills.bundled.enabled=false",
    "skills.include_instructions=false",
    'web_search="disabled"',
    "project_doc_max_bytes=0",
    "include_environment_context=false",
)

# PATH is needed when ``codex`` is invoked by name. HOME intentionally remains the
# real OS home; Codex runtime state is redirected independently through CODEX_HOME.
_INHERITED_ENV = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
)

_SAFE_METHOD = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
TRANSPORT_DIAGNOSTIC_METHODS = frozenset({
    "initialize", "account/read", "model/list", "config/read",
    "mcpServerStatus/list", "experimentalFeature/list", "thread/start",
    "turn/start", "turn/interrupt", "thread/unsubscribe",
})
_PROTOCOL_DIAGNOSTICS = frozenset({
    "protocol_error", "request_size_limit", "response_size_limit",
    "malformed_json", "invalid_message", "invalid_response",
    "invalid_notification", "unexpected_server_request",
})
_STABLE_CODEX_VERSION = re.compile(
    r"^codex-cli (0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})$"
)


class TransportError(RuntimeError):
    """Base class for safe-to-display transport errors."""


class TransportTimeout(TransportError):
    """The app-server did not answer before the request deadline."""


class TransportClosed(TransportError):
    """The app-server stream closed before a response arrived."""


class ProtocolError(TransportError):
    """The app-server emitted an invalid or unexpected protocol message."""

    def __init__(self, message: str, *, kind: str = "protocol_error",
                 request_bytes: int | None = None, limit_bytes: int | None = None) -> None:
        super().__init__(message)
        self._diagnostic_kind = kind if kind in _PROTOCOL_DIAGNOSTICS else "protocol_error"
        self._request_bytes = request_bytes
        self._limit_bytes = limit_bytes


class UnexpectedServerRequest(ProtocolError):
    """The server attempted to ask this metadata-only client to perform work."""


class ServerError(TransportError):
    """A redacted JSON-RPC error returned by the app-server."""

    def __init__(self, method: str, code: object = None) -> None:
        safe_code = _safe_error_code(code)
        suffix = f" (code {safe_code})" if safe_code is not None else ""
        safe_method = method if type(method) is str and method in TRANSPORT_DIAGNOSTIC_METHODS else "unknown"
        super().__init__(f"Codex app-server rejected {safe_method}{suffix}; details redacted")
        self.method = safe_method
        self.code = safe_code


def _safe_error_code(value: object) -> int | None:
    if type(value) is int and -(2**31) <= value < 2**31:
        return value
    return None


def transport_failure_diagnostic(error: BaseException) -> dict[str, str | int]:
    """Extract only locally classified, bounded transport failure facts."""
    if type(error) is ServerError:
        fields: dict[str, str | int] = {"kind": "rpc_rejection"}
        method = object.__getattribute__(error, "__dict__").get("method")
        code = object.__getattribute__(error, "__dict__").get("code")
        if type(method) is str and method in TRANSPORT_DIAGNOSTIC_METHODS:
            fields["method"] = method
        if _safe_error_code(code) is not None:
            fields["code"] = code
        return fields
    if type(error) is ProtocolError or type(error) is UnexpectedServerRequest:
        state = object.__getattribute__(error, "__dict__")
        kind = state.get("_diagnostic_kind")
        fields = {"kind": kind if type(kind) is str and kind in _PROTOCOL_DIAGNOSTICS
                  else "protocol_error"}
        if fields["kind"] == "request_size_limit":
            for name, key in (("request_bytes", "_request_bytes"),
                              ("limit_bytes", "_limit_bytes")):
                value = state.get(key)
                if type(value) is int and 0 <= value <= 2**31:
                    fields[name] = value
        return fields
    if type(error) is TransportTimeout:
        return {"kind": "timeout"}
    if type(error) is TransportClosed:
        return {"kind": "closed"}
    return {"kind": "transport_error"}


def build_app_server_command(executable: str = "codex") -> tuple[str, ...]:
    """Return the locked-down command used by the compatibility probe."""

    if not executable or "\x00" in executable:
        raise ValueError("Codex executable must be a non-empty path or command name")
    command: list[str] = [executable, "app-server", "--stdio", "--strict-config"]
    for setting in _DISABLED_CONFIG:
        command.extend(("-c", setting))
    return tuple(command)


def build_child_env(
    home: str | os.PathLike[str],
    source_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an allowlisted environment with an isolated ``CODEX_HOME``.

    API keys, custom service endpoints, and instruction injection variables are
    deliberately absent because they are not in the allowlist.
    """

    codex_home = Path(home).expanduser().resolve()
    if not codex_home.is_absolute():  # Defensive: ``resolve`` is absolute on supported Python.
        raise ValueError("Codex runtime home must be absolute")
    if codex_home == (Path.home() / ".codex").resolve():
        raise ValueError("The shared ~/.codex home is not allowed for this transport")

    source = os.environ if source_env is None else source_env
    child = {
        key: str(source[key])
        for key in _INHERITED_ENV
        if key in source and isinstance(source[key], str)
    }
    child["CODEX_HOME"] = str(codex_home)
    return child


class _StderrCapture:
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._buffer = bytearray()
        self.total_bytes = 0

    def run(self) -> None:
        try:
            while chunk := self._stream.read(4096):
                self.total_bytes += len(chunk)
                if self._limit:
                    self._buffer.extend(chunk)
                    del self._buffer[: max(0, len(self._buffer) - self._limit)]
        except (OSError, ValueError):
            return

    @property
    def summary(self) -> str:
        truncated = self.total_bytes > len(self._buffer)
        qualifier = "at least " if truncated else ""
        return f"stderr content redacted ({qualifier}{self.total_bytes} bytes)"

    @property
    def data(self) -> bytes:
        return bytes(self._buffer)


class CodexAppServerTransport:
    """One-at-a-time POSIX JSON-RPC client over a Codex app-server subprocess.

    The wire format is one JSON object per line. Notifications are retained for
    inspection while server-initiated requests are rejected immediately.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        home: str | os.PathLike[str],
        cwd: str | os.PathLike[str],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
        message_limit: int = DEFAULT_MESSAGE_LIMIT,
    ) -> None:
        if os.name != "posix":
            raise TransportError("The Codex compatibility probe currently supports macOS/Linux only")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if stderr_limit < 0 or message_limit < 1:
            raise ValueError("buffer limits must be non-negative and message_limit positive")

        self._uses_default_command = command is None
        chosen = build_app_server_command() if command is None else tuple(command)
        if not chosen or any(not isinstance(part, str) or not part or "\x00" in part for part in chosen):
            raise ValueError("command must contain non-empty string arguments")

        self.command = chosen
        self.home = Path(home).expanduser().resolve()
        self.cwd = Path(cwd).expanduser().resolve()
        build_child_env(self.home)  # Validate before creating anything.
        if not self.cwd.is_dir():
            raise ValueError("cwd must be an existing directory")
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)

        self.timeout = float(timeout)
        self.stderr_limit = stderr_limit
        self.message_limit = message_limit
        self._process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._notifications: list[dict[str, Any]] = []
        self._notifications_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._next_id = 1
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_capture: _StderrCapture | None = None
        self._stdin_fd: int | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_summary(self) -> str:
        if self._stderr_capture is None:
            return "stderr content redacted (not captured)"
        return self._stderr_capture.summary

    def start(self) -> CodexAppServerTransport:
        if self._process is not None:
            if self.is_running:
                return self
            raise TransportClosed("Codex app-server transport cannot be restarted")

        if self._uses_default_command:
            self._verify_default_cli_version()

        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=build_child_env(self.home),
                shell=False,
                start_new_session=(os.name == "posix"),
            )
        except (OSError, ValueError) as exc:
            raise TransportError("Unable to start Codex app-server") from exc

        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise TransportError("Codex app-server pipes were not created")

        self._process = process
        self._stdin_fd = process.stdin.fileno()
        if os.name == "posix":
            os.set_blocking(self._stdin_fd, False)
        self._stderr_capture = _StderrCapture(process.stderr, self.stderr_limit)
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name="codex-app-server-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_capture.run,
            name="codex-app-server-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self

    def _verify_default_cli_version(self) -> None:
        """Reject unsupported Codex binaries before app-server can start."""

        try:
            process = subprocess.Popen(
                [self.command[0], "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=build_child_env(self.home),
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise TransportError(_VERSION_ERROR) from None

        if process.stdout is None or process.stderr is None:
            self._terminate_version_process(process)
            raise TransportError(_VERSION_ERROR)

        stdout_capture = _StderrCapture(process.stdout, _VERSION_OUTPUT_LIMIT)
        stderr_capture = _StderrCapture(process.stderr, _VERSION_OUTPUT_LIMIT)
        stdout_thread = threading.Thread(target=stdout_capture.run, daemon=True)
        stderr_thread = threading.Thread(target=stderr_capture.run, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        return_code: int | None = None
        try:
            return_code = process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            pass
        finally:
            self._terminate_version_process(process)
            for thread in (stdout_thread, stderr_thread):
                thread.join(timeout=min(0.5, self.timeout))
            for stream, thread in (
                (process.stdout, stdout_thread),
                (process.stderr, stderr_thread),
            ):
                if not thread.is_alive() and not stream.closed:
                    with suppress(OSError):
                        stream.close()

        output_is_bounded = (
            stdout_capture.total_bytes <= _VERSION_OUTPUT_LIMIT
            and stderr_capture.total_bytes <= _VERSION_OUTPUT_LIMIT
        )
        try:
            rendered = stdout_capture.data.decode("ascii").rstrip("\r\n")
        except UnicodeDecodeError:
            rendered = ""
        match = _STABLE_CODEX_VERSION.fullmatch(rendered)
        if (
            return_code != 0
            or not output_is_bounded
            or stdout_thread.is_alive()
            or stderr_thread.is_alive()
            or match is None
            or tuple(int(part) for part in match.groups()) < MIN_CODEX_CLI_VERSION
        ):
            raise TransportError(_VERSION_ERROR)

    @staticmethod
    def _terminate_version_process(process: subprocess.Popen[bytes]) -> None:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.5)

    def _read_stdout(self, stream: BinaryIO) -> None:
        try:
            while True:
                line = stream.readline(self.message_limit + 1)
                if not line:
                    self._messages.put(TransportClosed("Codex app-server closed stdout"))
                    return
                if len(line) > self.message_limit:
                    self._messages.put(ProtocolError("Codex app-server message exceeded size limit",
                                                     kind="response_size_limit"))
                    return
                try:
                    decoded = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._messages.put(ProtocolError("Codex app-server emitted malformed JSON",
                                                     kind="malformed_json"))
                    return
                if not isinstance(decoded, dict):
                    self._messages.put(ProtocolError("Codex app-server message must be an object",
                                                     kind="invalid_message"))
                    return
                self._messages.put(decoded)
        except (OSError, ValueError):
            self._messages.put(TransportClosed("Codex app-server stdout became unavailable"))

    def _send(self, payload: Mapping[str, Any], *, deadline: float | None = None) -> None:
        if not self.is_running or self._process is None or self._process.stdin is None:
            raise TransportClosed("Codex app-server is not running")
        try:
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        except (TypeError, ValueError) as exc:
            raise ProtocolError("Request parameters are not JSON serializable") from exc
        if len(encoded) > self.message_limit:
            raise ProtocolError("Codex app-server request exceeded size limit",
                                kind="request_size_limit", request_bytes=len(encoded),
                                limit_bytes=self.message_limit)
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        try:
            with self._write_lock:
                if os.name == "posix" and self._stdin_fd is not None:
                    remaining_bytes = memoryview(encoded)
                    while remaining_bytes:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TransportTimeout("Timed out writing to Codex app-server")
                        _, writable, _ = select.select([], [self._stdin_fd], [], remaining)
                        if not writable:
                            raise TransportTimeout("Timed out writing to Codex app-server")
                        try:
                            written = os.write(self._stdin_fd, remaining_bytes)
                        except BlockingIOError:
                            continue
                        remaining_bytes = remaining_bytes[written:]
                else:  # pragma: no cover - exercised on Windows only
                    self._process.stdin.write(encoded)
                    self._process.stdin.flush()
        except TransportTimeout:
            raise
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise TransportClosed("Codex app-server stdin became unavailable") from exc

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if not method:
            raise ValueError("method must be non-empty")
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = dict(params)
        self._send(payload)

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if not method:
            raise ValueError("method must be non-empty")
        deadline_seconds = self.timeout if timeout is None else float(timeout)
        if not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
            raise ValueError("timeout must be positive")

        with self._request_lock:
            request_id = self._next_id
            self._next_id += 1
            payload: dict[str, Any] = {"id": request_id, "method": method}
            if params is not None:
                payload["params"] = dict(params)
            deadline = time.monotonic() + deadline_seconds
            self._send(payload, deadline=deadline)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportTimeout(f"Timed out waiting for {method}")
                try:
                    message = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TransportTimeout(f"Timed out waiting for {method}") from exc
                if isinstance(message, BaseException):
                    raise message

                if "method" in message:
                    if "id" in message:
                        self._reject_server_request(message)
                    self._validate_notification(message)
                    with self._notifications_lock:
                        self._notifications.append(message)
                    continue

                if "id" not in message:
                    raise ProtocolError("Codex app-server response is missing an id",
                                        kind="invalid_response")
                if message["id"] != request_id:
                    raise ProtocolError("Codex app-server response id did not match request",
                                        kind="invalid_response")
                has_result = "result" in message
                has_error = "error" in message
                if has_result == has_error:
                    raise ProtocolError("Codex app-server response must contain one result or error",
                                        kind="invalid_response")
                if has_error:
                    error = message["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    raise ServerError(method, code)
                return message["result"]

    def _reject_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        with suppress(TransportError):
            self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "Client does not accept server requests",
                    },
                }
            )
        raise UnexpectedServerRequest("Rejected unexpected server request",
                                      kind="unexpected_server_request")

    @staticmethod
    def _validate_notification(message: Mapping[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str) or not _SAFE_METHOD.fullmatch(method):
            raise ProtocolError("Codex app-server notification has an invalid method",
                                kind="invalid_notification")
        if "params" in message and not isinstance(message["params"], dict):
            raise ProtocolError("Codex app-server notification has invalid params",
                                kind="invalid_notification")

    def pop_notifications(self) -> list[dict[str, Any]]:
        with self._notifications_lock:
            notifications = self._notifications
            self._notifications = []
        return notifications

    def wait_notification(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Return the next server notification within a bounded deadline.

        This method must not run concurrently with :meth:`request`. Any
        notifications observed while waiting for a response are returned first,
        preserving their wire order. A response outside an active request is a
        protocol error, and server-initiated requests remain unsupported.
        """

        deadline_seconds = self.timeout if timeout is None else float(timeout)
        if not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
            raise ValueError("timeout must be positive")

        with self._request_lock:
            deadline = time.monotonic() + deadline_seconds
            while True:
                with self._notifications_lock:
                    if self._notifications:
                        notification = self._notifications.pop(0)
                        self._validate_notification(notification)
                        return notification

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportTimeout("Timed out waiting for an app-server notification")
                try:
                    message = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TransportTimeout(
                        "Timed out waiting for an app-server notification"
                    ) from exc
                if isinstance(message, BaseException):
                    raise message
                if "method" not in message:
                    raise ProtocolError(
                        "Codex app-server emitted a response without an active request",
                        kind="invalid_response",
                    )
                if "id" in message:
                    self._reject_server_request(message)
                self._validate_notification(message)
                return message

    def close(self, grace_seconds: float = 1.0) -> None:
        process = self._process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.closed:
            with suppress(OSError):
                process.stdin.close()
        try:
            process.wait(timeout=max(0.0, grace_seconds))
        except subprocess.TimeoutExpired:
            self._signal_process_tree(signal.SIGTERM)
            try:
                process.wait(timeout=max(0.1, grace_seconds))
            except subprocess.TimeoutExpired:
                self._signal_process_tree(signal.SIGKILL)
                process.wait(timeout=max(0.1, grace_seconds))

        # The direct child has exited. Its private group can still contain
        # descendants, including ones with all stdio redirected. Clean the group
        # regardless of pipe state; it belongs only to this subprocess session.
        self._signal_process_tree(signal.SIGKILL)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=max(0.0, grace_seconds))
        if any(thread is not None and thread.is_alive() for thread in (self._stdout_thread, self._stderr_thread)):
            self._signal_process_tree(signal.SIGKILL)
            for thread in (self._stdout_thread, self._stderr_thread):
                if thread is not None:
                    thread.join(timeout=max(0.1, grace_seconds))
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _signal_process_tree(self, sig: signal.Signals) -> None:
        process = self._process
        if process is None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif sig == signal.SIGTERM:  # pragma: no cover - Windows only
                process.terminate()
            else:  # pragma: no cover - Windows only
                process.kill()
        except (ProcessLookupError, PermissionError):
            return

    def __enter__(self) -> CodexAppServerTransport:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
