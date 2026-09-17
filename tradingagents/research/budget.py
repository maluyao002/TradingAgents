"""Conservative model-call admission with explicit incomplete telemetry handling."""

import time
from collections.abc import Callable

from .contracts import Budget, Usage


class BudgetExhausted(RuntimeError):
    pass


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

    @property
    def elapsed_seconds(self):
        return self.previous_elapsed + max(0, self.clock() - self.started)

    def admit(self, *, finalization: bool = False, estimated_tokens: int = 0) -> float:
        if estimated_tokens < 0:
            raise ValueError("token estimate cannot be negative")
        if not self.usage.complete:
            raise BudgetExhausted("usage_incomplete")
        seconds = self.limits.wall_seconds - self.elapsed_seconds
        tokens = self.limits.total_tokens - self.usage.total_tokens
        if not finalization:
            seconds -= self.limits.reserve_seconds
            tokens -= self.limits.reserve_tokens
        if seconds <= 0 or tokens <= estimated_tokens:
            raise BudgetExhausted("budget_exhausted")
        return min(self.limits.call_timeout_seconds, seconds)

    def record(self, usage: Usage):
        self.usage = Usage(
            input_tokens=self.usage.input_tokens + usage.input_tokens,
            output_tokens=self.usage.output_tokens + usage.output_tokens,
            cached_input_tokens=self.usage.cached_input_tokens + usage.cached_input_tokens,
            reasoning_output_tokens=self.usage.reasoning_output_tokens + usage.reasoning_output_tokens,
            complete=self.usage.complete and usage.complete,
        )
