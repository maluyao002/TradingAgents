"""Conservative model-call admission with explicit incomplete telemetry handling."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import Budget, Usage


class BudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    id: str
    tokens: int
    timeout_seconds: float


class BudgetTracker:
    def __init__(self, limits: Budget, *, clock: Callable[[], float] = time.monotonic,
                 previous_usage: Usage | None = None, elapsed_seconds: float = 0):
        if elapsed_seconds < 0:
            raise ValueError("elapsed time cannot be negative")
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.previous_elapsed = elapsed_seconds
        self.usage = previous_usage or Usage()
        self._lock = threading.RLock()
        self._pending: dict[str, Reservation] = {}

    @property
    def elapsed_seconds(self):
        return self.previous_elapsed + max(0, self.clock() - self.started)

    def admit(self, *, finalization: bool = False, estimated_tokens: int = 0) -> float:
        if estimated_tokens < 0:
            raise ValueError("token estimate cannot be negative")
        if not self.usage.complete:
            raise BudgetExhausted("usage_incomplete")
        seconds = self.limits.wall_seconds - self.elapsed_seconds
        tokens = (self.limits.total_tokens - self.usage.total_tokens
                  - sum(item.tokens for item in self._pending.values()))
        if not finalization:
            seconds -= self.limits.reserve_seconds
            tokens -= self.limits.reserve_tokens
        if seconds <= 0 or tokens <= estimated_tokens:
            raise BudgetExhausted("budget_exhausted")
        return min(self.limits.call_timeout_seconds, seconds)

    def reserve(self, estimated_input_and_max_output: int, *, finalization: bool = False):
        """Atomically reserve a conservative per-call envelope before dispatch."""
        if isinstance(estimated_input_and_max_output, bool) or estimated_input_and_max_output <= 0:
            raise ValueError("a positive per-call token envelope is required")
        with self._lock:
            timeout = self.admit(finalization=finalization,
                                 estimated_tokens=estimated_input_and_max_output)
            permit = Reservation(uuid.uuid4().hex, estimated_input_and_max_output, timeout)
            self._pending[permit.id] = permit
            return permit

    def complete(self, permit: Reservation, usage: Usage):
        with self._lock:
            if self._pending.get(permit.id) != permit:
                raise ValueError("unknown or already completed reservation")
            del self._pending[permit.id]
            self.record(usage)

    def cancel(self, permit: Reservation, *, dispatched: bool):
        # A dispatched call with missing telemetry is not a free retry.
        self.complete(permit, Usage(complete=not dispatched))

    def record(self, usage: Usage):
        with self._lock:
            self.usage = Usage(
                input_tokens=self.usage.input_tokens + usage.input_tokens,
                output_tokens=self.usage.output_tokens + usage.output_tokens,
                cached_input_tokens=self.usage.cached_input_tokens + usage.cached_input_tokens,
                reasoning_output_tokens=self.usage.reasoning_output_tokens + usage.reasoning_output_tokens,
                complete=self.usage.complete and usage.complete,
            )
