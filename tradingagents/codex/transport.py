"""Synchronous, bounded stdio transport for the Codex app-server protocol."""

from __future__ import annotations

import json
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

# These switches keep the metadata-only probe from discovering host instructions,
# tools, skills, apps, plugins, connectors, memories, or web capabilities.
_DISABLED_CONFIG = (
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

_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class TransportError(RuntimeError):
    """Base class for safe-to-display transport errors."""


class TransportTimeout(TransportError):
    """The app-server did not answer before the request deadline."""


class TransportClosed(TransportError):
    """The app-server stream closed before a response arrived."""


class ProtocolError(TransportError):
    """The app-server emitted an invalid or unexpected protocol message."""


class UnexpectedServerRequest(ProtocolError):
    """The server attempted to ask this metadata-only client to perform work."""


class ServerError(TransportError):
    """A redacted JSON-RPC error returned by the app-server."""

    def __init__(self, method: str, code: object = None) -> None:
        safe_code = _safe_error_code(code)
        suffix = f" (code {safe_code})" if safe_code is not None else ""
        super().__init__(f"Codex app-server rejected {method}{suffix}; details redacted")
        self.method = method
        self.code = safe_code


def _safe_error_code(value: object) -> str | int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return None


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


class CodexAppServerTransport:
    """One-at-a-time JSON-RPC client over a Codex app-server subprocess.

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
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if stderr_limit < 0 or message_limit < 1:
            raise ValueError("buffer limits must be non-negative and message_limit positive")

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

    def _read_stdout(self, stream: BinaryIO) -> None:
        try:
            while True:
                line = stream.readline(self.message_limit + 1)
                if not line:
                    self._messages.put(TransportClosed("Codex app-server closed stdout"))
                    return
                if len(line) > self.message_limit:
                    self._messages.put(ProtocolError("Codex app-server message exceeded size limit"))
                    return
                try:
                    decoded = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._messages.put(ProtocolError("Codex app-server emitted malformed JSON"))
                    return
                if not isinstance(decoded, dict):
                    self._messages.put(ProtocolError("Codex app-server message must be an object"))
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
            raise ProtocolError("Codex app-server request exceeded size limit")
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
        if deadline_seconds <= 0:
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
                    with self._notifications_lock:
                        self._notifications.append(message)
                    continue

                if "id" not in message:
                    raise ProtocolError("Codex app-server response is missing an id")
                if message["id"] != request_id:
                    raise ProtocolError("Codex app-server response id did not match request")
                has_result = "result" in message
                has_error = "error" in message
                if has_result == has_error:
                    raise ProtocolError("Codex app-server response must contain one result or error")
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
        method = message.get("method")
        safe_method = method if isinstance(method, str) and _SAFE_ERROR_CODE.fullmatch(method) else "unknown"
        raise UnexpectedServerRequest(f"Rejected unexpected server request: {safe_method}")

    def pop_notifications(self) -> list[dict[str, Any]]:
        with self._notifications_lock:
            notifications = self._notifications
            self._notifications = []
        return notifications

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

        # A descendant can inherit the pipes after its parent exits. Terminate the
        # process group before joining so buffered stream close cannot wait on a
        # reader that will never see EOF.
        if any(thread is not None and thread.is_alive() for thread in (self._stdout_thread, self._stderr_thread)):
            self._signal_process_tree(signal.SIGTERM)
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
