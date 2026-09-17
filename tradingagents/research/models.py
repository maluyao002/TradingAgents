"""Explicit Codex service over the repository's isolated, tool-disabled adapter.

No client starts at import/construction. Output-token limits are advisory for this
adapter; actual overshoot is accounted for and stops subsequent admissions.
"""

from __future__ import annotations

import math
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from tradingagents.codex.adapter import CodexAdapter

from .contracts import ResearchRequest, Usage
from .services import ModelReply
from .storage import canonical_json, digest, parse_json


class ModelCallTimeout(TimeoutError):
    pass


class _ClosingSafeAdapter(CodexAdapter):
    def close(self):
        # The neutral adapter relinquishes its transport reference before its
        # bounded shutdown finishes. Deliver an expiring alarm only after shutdown,
        # so a mid-close signal cannot orphan that transport. The outer process
        # supervisor still enforces the whole-run deadline during cleanup.
        # A thread-local mask is insufficient: adapter reader threads can receive
        # SIGALRM and queue Python's handler on the main thread. Ignore/disarm it
        # process-wide during shutdown and restore the remaining allowance after.
        previous_handler = signal.getsignal(signal.SIGALRM)
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            super().close()
        finally:
            signal.signal(signal.SIGALRM, previous_handler)
            if remaining:
                remaining -= time.monotonic() - started
                if remaining <= 0:
                    raise ModelCallTimeout("research model deadline exceeded during cleanup")
                signal.setitimer(signal.ITIMER_REAL, remaining, interval)


@contextmanager
def _call_deadline(seconds: float):
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        raise RuntimeError("Codex research calls require a POSIX main-thread worker")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("model timeout must be positive and finite")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("research worker already has an active alarm")

    def expire(*_):
        raise ModelCallTimeout("research model deadline exceeded")

    signal.signal(signal.SIGALRM, expire)
    deadline = time.monotonic() + seconds
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
        if time.monotonic() >= deadline:
            raise ModelCallTimeout("research model deadline exceeded")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class CodexModelService:
    kind = "codex"
    supports_hard_output_cap = False

    def __init__(self, home: Path, *, adapter_factory=_ClosingSafeAdapter):
        self.home = Path(home).resolve()
        self.identity = digest({"service": "isolated-codex-v1", "home": str(self.home)})
        self._factory = adapter_factory
        self._adapter = None
        self._preflighted = None

    def __enter__(self):
        return self

    def close(self):
        adapter, self._adapter = self._adapter, None
        self._preflighted = None
        if adapter is not None:
            adapter.close()

    def __exit__(self, *args):
        self.close()

    def complete(self, role: str, payload: dict, request: ResearchRequest) -> ModelReply:
        if request.backend != "codex" or role not in request.models:
            raise ValueError("Codex service requires an explicit Codex backend and role")
        timeout = float(payload["timeout_seconds"])
        output_limit = payload["max_output_tokens"]
        if type(output_limit) is not int or output_limit <= 0:
            raise ValueError("output allowance must be a positive integer")
        setting = request.models[role]
        choices = tuple(sorted({(item.model, item.effort) for item in request.models.values()}))
        with _call_deadline(timeout):
            if self._adapter is None:
                adapter = self._factory(home=self.home, timeout=timeout)
                try:
                    adapter.__enter__()
                except BaseException:
                    adapter.close()
                    raise
                self._adapter = adapter
            self._adapter.timeout = timeout
            if choices != self._preflighted:
                for model, effort in choices:
                    self._adapter.preflight(model, effort)
                self._preflighted = choices
            instructions = payload["system"] + (
                f" Keep the final JSON within the requested {output_limit}-token output allowance."
            )
            prompt = canonical_json({key: value for key, value in payload.items()
                                     if key not in {"system", "response_schema", "timeout_seconds"}}).decode()
            completion = self._adapter.complete_with_usage(
                instructions, prompt, setting.model, setting.effort,
                output_schema=payload["response_schema"])
        usage = Usage(complete=False) if completion.usage is None else Usage(
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            cached_input_tokens=completion.usage.cached_input_tokens,
            reasoning_output_tokens=completion.usage.reasoning_output_tokens)
        try:
            if len(completion.text.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("model response exceeds size allowance")
            data = parse_json(completion.text.encode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("response must be a JSON object")
        except (ValueError, UnicodeError):
            # Preserve known spend, then let the stage contract fail closed. Raw
            # malformed text is never echoed into diagnostics or silently repaired.
            data = {"_invalid_model_response": True}
        return ModelReply(data=data, usage=usage)
