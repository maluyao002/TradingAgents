import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cached_tokens_in = 0
        self.reasoning_tokens_out = 0
        self.total_tokens = 0
        self._usage_seen = {
            "input_tokens": False,
            "output_tokens": False,
            "cached_input_tokens": False,
            "reasoning_output_tokens": False,
            "total_tokens": False,
        }
        self._runs: dict[str, str] = {}
        self._per_model: dict[str, dict[str, int | bool]] = {}

    @staticmethod
    def _empty_model_stats() -> dict[str, int | bool]:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
            "input_seen": False,
            "output_seen": False,
            "cached_seen": False,
            "reasoning_seen": False,
            "total_seen": False,
            "calls_started": 0,
            "calls_finished": 0,
            "calls_with_usage": 0,
        }

    @staticmethod
    def _model_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str:
        """Return only a model identifier; callback metadata may contain URLs/keys."""
        candidates = (
            kwargs.get("invocation_params", {}),
            serialized.get("kwargs", {}) if isinstance(serialized, dict) else {},
            serialized,
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("model", "model_name", "model_id"):
                value = candidate.get(key)
                if isinstance(value, str) and value and "://" not in value and "\n" not in value:
                    return value
        return "unknown"

    def _record_start(self, serialized: dict[str, Any], kwargs: dict[str, Any]) -> None:
        run_id = kwargs.get("run_id")
        model = self._model_name(serialized, kwargs)
        with self._lock:
            self.llm_calls += 1
            self._per_model.setdefault(model, self._empty_model_stats())["calls_started"] += 1
            if run_id is not None:
                self._runs[str(run_id)] = model

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when an LLM starts."""
        self._record_start(serialized, kwargs)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when a chat model starts."""
        self._record_start(serialized, kwargs)

    def _finish_model(self, run_id: Any) -> tuple[str, dict[str, int | bool]]:
        with self._lock:
            model = self._runs.pop(str(run_id), "unknown")
            stats = self._per_model.setdefault(model, self._empty_model_stats())
            stats["calls_finished"] += 1
            return model, stats

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response."""
        _, per_model = self._finish_model(kwargs.get("run_id"))
        try:
            generation = response.generations[0][0]
        except (AttributeError, IndexError, KeyError, TypeError):
            return

        usage_metadata: dict[str, Any] | None = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if not isinstance(usage_metadata, dict):
            return

        def number(*keys: str) -> int | None:
            for key in keys:
                value = usage_metadata.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
            return None

        details = usage_metadata.get("input_token_details")
        cached = number("cached_input_tokens", "cache_read_input_tokens", "cached_tokens")
        if cached is None and isinstance(details, dict):
            value = details.get("cache_read")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                cached = value
        input_tokens = number("input_tokens", "prompt_tokens")
        output_tokens = number("output_tokens", "completion_tokens")
        total_tokens = number("total_tokens")
        reasoning = number("reasoning_output_tokens")
        output_details = usage_metadata.get("output_token_details")
        if reasoning is None and isinstance(output_details, dict):
            value = output_details.get("reasoning")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                reasoning = value
        with self._lock:
            if input_tokens is not None and output_tokens is not None:
                per_model["calls_with_usage"] += 1
            if input_tokens is not None:
                self.tokens_in += input_tokens
                self._usage_seen["input_tokens"] = True
                per_model["input_tokens"] += input_tokens
                per_model["input_seen"] = True
            if output_tokens is not None:
                self.tokens_out += output_tokens
                self._usage_seen["output_tokens"] = True
                per_model["output_tokens"] += output_tokens
                per_model["output_seen"] = True
            if cached is not None:
                self.cached_tokens_in += cached
                self._usage_seen["cached_input_tokens"] = True
                per_model["cached_input_tokens"] += cached
                per_model["cached_seen"] = True
            if reasoning is not None:
                self.reasoning_tokens_out += reasoning
                self._usage_seen["reasoning_output_tokens"] = True
                per_model["reasoning_output_tokens"] += reasoning
                per_model["reasoning_seen"] = True
            if total_tokens is not None:
                self.total_tokens += total_tokens
                self._usage_seen["total_tokens"] = True
                per_model["total_tokens"] += total_tokens
                per_model["total_seen"] = True

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Close failed calls so persisted completeness reports their missing usage."""
        self._finish_model(kwargs.get("run_id"))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> dict[str, Any]:
        """Return current statistics."""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }

    def get_persistence_stats(self) -> dict[str, Any]:
        """Return observed usage without representing unavailable provider data as zero.

        ``tool_calls`` counts LangChain agent tool callbacks only; deterministic
        prefetch/data preparation occurs outside that callback path.
        """
        with self._lock:
            per_model = {
                model: {
                    "input_tokens": values["input_tokens"] if values["input_seen"] else None,
                    "output_tokens": values["output_tokens"] if values["output_seen"] else None,
                    "cached_input_tokens": (
                        values["cached_input_tokens"] if values["cached_seen"] else None
                    ),
                    "calls_started": values["calls_started"],
                    "reasoning_output_tokens": (
                        values["reasoning_output_tokens"] if values["reasoning_seen"] else None
                    ),
                    "total_tokens": values["total_tokens"] if values["total_seen"] else None,
                    "calls_finished": values["calls_finished"],
                    "calls_with_usage": values["calls_with_usage"],
                    "usage_complete": (
                        values["calls_started"] == values["calls_finished"] == values["calls_with_usage"]
                    ),
                }
                for model, values in sorted(self._per_model.items())
            }
            calls_finished = sum(values["calls_finished"] for values in self._per_model.values())
            calls_with_usage = sum(values["calls_with_usage"] for values in self._per_model.values())
            return {
                "llm_calls": self.llm_calls,
                "input_tokens": self.tokens_in if self._usage_seen["input_tokens"] else None,
                "output_tokens": self.tokens_out if self._usage_seen["output_tokens"] else None,
                "cached_input_tokens": (
                    self.cached_tokens_in if self._usage_seen["cached_input_tokens"] else None
                ),
                "per_model": per_model,
                "reasoning_output_tokens": (
                    self.reasoning_tokens_out if self._usage_seen["reasoning_output_tokens"] else None
                ),
                "total_tokens": self.total_tokens if self._usage_seen["total_tokens"] else None,
                "usage_completeness": {
                    "calls_started": self.llm_calls,
                    "calls_finished": calls_finished,
                    "calls_with_usage": calls_with_usage,
                    "calls_missing_usage": calls_finished - calls_with_usage,
                    "calls_unfinished": self.llm_calls - calls_finished,
                    "usage_complete": self.llm_calls == calls_finished == calls_with_usage,
                },
                "tool_calls": self.tool_calls,
                "tool_calls_scope": (
                    "LangChain agent tool callbacks; excludes deterministic prefetch and data preparation."
                ),
                "billed_cost": None,
            }
