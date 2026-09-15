import hashlib
import json
import math
import re
import threading
from collections.abc import Callable
from time import perf_counter
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

_PROFILE_ROLES = {
    "market", "social", "news", "fundamentals", "bull", "bear",
    "research_manager", "trader", "aggressive", "conservative", "neutral",
    "portfolio_manager", "signal", "reflection",
}
_ROLE_ALIASES = {
    "market_analyst": "market", "sentiment": "social", "sentiment_analyst": "social",
    "social_analyst": "social", "news_analyst": "news",
    "fundamentals_analyst": "fundamentals", "bull_researcher": "bull",
    "bear_researcher": "bear", "research_manager": "research_manager",
    "trader": "trader", "aggressive_analyst": "aggressive",
    "conservative_analyst": "conservative", "neutral_analyst": "neutral",
    "portfolio_manager": "portfolio_manager",
}
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,127}\Z")
_ANALYST_EVIDENCE = re.compile(r"<analyst_evidence>.*?</analyst_evidence>", re.DOTALL)
_RUNTIME_SECONDS_FIELDS = {
    "bridge_lock_wait_seconds", "adapter_seconds", "bridge_seconds", "validation_seconds",
    "thread_start_seconds", "turn_start_seconds", "turn_wait_seconds", "cleanup_seconds",
}
_RUNTIME_CHARACTER_FIELDS = {
    "instructions_characters", "serialized_prompt_characters", "output_schema_characters",
}


class StatsCallbackHandler(BaseCallbackHandler):
    """Track LLM calls, tool calls, timing, prompt repetition, and token usage."""

    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._clock = clock
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cached_tokens_in = 0
        self.reasoning_tokens_out = 0
        self.total_tokens = 0
        self._usage_seen = dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "total_tokens"), False)
        self._runs: dict[tuple[str, str], dict[str, Any]] = {}
        self._anonymous_runs: dict[int, list[tuple[str, str]]] = {}
        self._next_anonymous_run = 0
        self._per_model: dict[str, dict[str, int | float | bool]] = {}
        self._per_role: dict[str, dict[str, int | float | bool]] = {}
        self._calls: list[dict[str, Any]] = []
        self._completed_intervals: list[tuple[float, float]] = []
        # Prompt contents never leave callback scope. Only hashes survive between calls.
        self._seen_messages: set[bytes] = set()
        self._seen_analyst_evidence: set[bytes] = set()

    @staticmethod
    def _empty_model_stats() -> dict[str, int | float | bool]:
        return {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "reasoning_output_tokens": 0, "total_tokens": 0,
            "input_seen": False, "output_seen": False, "cached_seen": False,
            "reasoning_seen": False, "total_seen": False,
            "calls_started": 0, "calls_finished": 0, "calls_with_usage": 0,
        }

    @classmethod
    def _empty_role_stats(cls) -> dict[str, int | float | bool]:
        return {
            **cls._empty_model_stats(), "calls_failed": 0, "elapsed_seconds": 0.0,
            "prompt_characters": 0, "repeated_message_characters": 0,
            "analyst_evidence_characters": 0,
            "repeated_analyst_evidence_characters": 0,
        }

    @staticmethod
    def _model_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str:
        """Return only a short model label; callback metadata may contain URLs or keys."""
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
                if (isinstance(value, str) and "://" not in value
                        and _SAFE_MODEL.fullmatch(value) is not None):
                    return value
        return "unknown"

    @staticmethod
    def _canonical_role(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        return normalized if normalized in _PROFILE_ROLES else _ROLE_ALIASES.get(normalized)

    @classmethod
    def _role_name(cls, serialized: dict[str, Any], kwargs: dict[str, Any]) -> str:
        metadata = kwargs.get("metadata")
        if isinstance(metadata, dict):
            for key in ("tradingagents_role", "langgraph_node"):
                role = cls._canonical_role(metadata.get(key))
                if role is not None:
                    return role
        candidates = (
            kwargs.get("invocation_params", {}),
            serialized.get("kwargs", {}) if isinstance(serialized, dict) else {}, serialized,
        )
        for candidate in candidates:
            if isinstance(candidate, dict):
                role = cls._canonical_role(candidate.get("role"))
                if role is not None:
                    return role
        return "unknown"

    @staticmethod
    def _content_characters(content: Any) -> int:
        if isinstance(content, str):
            return len(content)
        if isinstance(content, (list, tuple)):
            return sum(StatsCallbackHandler._content_characters(value) for value in content)
        if isinstance(content, dict):
            return sum(StatsCallbackHandler._content_characters(value) for value in content.values())
        return 0

    @staticmethod
    def _message_fingerprint(message_type: str, content: Any) -> bytes:
        try:
            encoded = json.dumps(
                [message_type, content], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded = json.dumps(
                [message_type, repr(content)], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        return hashlib.sha256(encoded).digest()

    @staticmethod
    def _evidence_items(content: Any) -> list[tuple[bytes, int]]:
        if isinstance(content, str):
            return [
                (hashlib.sha256(block.encode("utf-8")).digest(), len(block))
                for block in _ANALYST_EVIDENCE.findall(content)
            ]
        if isinstance(content, (list, tuple)):
            return [item for value in content for item in StatsCallbackHandler._evidence_items(value)]
        if isinstance(content, dict):
            return [
                item for value in content.values()
                for item in StatsCallbackHandler._evidence_items(value)
            ]
        return []

    @classmethod
    def _prompt_items_from_prompts(
        cls, prompts: list[str]
    ) -> list[tuple[bytes, int, list[tuple[bytes, int]]]]:
        return [(cls._message_fingerprint("prompt", prompt), len(prompt), cls._evidence_items(prompt))
                for prompt in prompts if isinstance(prompt, str)]

    @classmethod
    def _prompt_items_from_messages(
        cls, messages: list[list[Any]]
    ) -> list[tuple[bytes, int, list[tuple[bytes, int]]]]:
        items: list[tuple[bytes, int, list[tuple[bytes, int]]]] = []
        for batch in messages:
            if not isinstance(batch, (list, tuple)):
                continue
            for message in batch:
                content = getattr(message, "content", None)
                message_type = f"{type(message).__module__}.{type(message).__qualname__}"
                items.append((cls._message_fingerprint(message_type, content),
                              cls._content_characters(content), cls._evidence_items(content)))
        return items

    def _record_start(self, serialized: dict[str, Any], kwargs: dict[str, Any],
                      prompt_items: list[tuple[bytes, int, list[tuple[bytes, int]]]]) -> None:
        started_at = float(self._clock())
        if not math.isfinite(started_at):
            started_at = 0.0
        run_id = kwargs.get("run_id")
        model = self._model_name(serialized, kwargs)
        role = self._role_name(serialized, kwargs)
        thread_id = threading.get_ident()
        with self._lock:
            if run_id is not None:
                run_key = ("id", str(run_id))
                if run_key in self._runs:
                    return
            else:
                self._next_anonymous_run += 1
                run_key = ("anonymous", str(self._next_anonymous_run))
                self._anonymous_runs.setdefault(thread_id, []).append(run_key)

            repeated_characters = 0
            prompt_characters = 0
            evidence_characters = 0
            repeated_evidence_characters = 0
            for fingerprint, characters, evidence_items in prompt_items:
                prompt_characters += characters
                if fingerprint in self._seen_messages:
                    repeated_characters += characters
                self._seen_messages.add(fingerprint)
                for evidence_fingerprint, evidence_length in evidence_items:
                    evidence_characters += evidence_length
                    if evidence_fingerprint in self._seen_analyst_evidence:
                        repeated_evidence_characters += evidence_length
                    self._seen_analyst_evidence.add(evidence_fingerprint)

            self.llm_calls += 1
            self._per_model.setdefault(model, self._empty_model_stats())["calls_started"] += 1
            role_stats = self._per_role.setdefault(role, self._empty_role_stats())
            role_stats["calls_started"] += 1
            role_stats["prompt_characters"] += prompt_characters
            role_stats["repeated_message_characters"] += repeated_characters
            role_stats["analyst_evidence_characters"] += evidence_characters
            role_stats["repeated_analyst_evidence_characters"] += repeated_evidence_characters
            self._runs[run_key] = {
                "model": model, "role": role, "started_at": started_at,
                "prompt_characters": prompt_characters,
                "repeated_message_characters": repeated_characters,
                "analyst_evidence_characters": evidence_characters,
                "repeated_analyst_evidence_characters": repeated_evidence_characters,
            }

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        """Increment the LLM call counter and start its timer."""
        self._record_start(serialized, kwargs, self._prompt_items_from_prompts(prompts))

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[Any]],
                            **kwargs: Any) -> None:
        """Increment the chat-model call counter and start its timer."""
        self._record_start(serialized, kwargs, self._prompt_items_from_messages(messages))

    @staticmethod
    def _number(usage_metadata: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = usage_metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    @classmethod
    def _usage_from_response(cls, response: LLMResult) -> tuple[dict[str, int | None], dict[str, int | float]]:
        usage = dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "total_tokens"))
        try:
            generation = response.generations[0][0]
        except (AttributeError, IndexError, KeyError, TypeError):
            return usage, {}
        message = getattr(generation, "message", None)
        usage_metadata = message.usage_metadata if isinstance(message, AIMessage) else None
        if isinstance(usage_metadata, dict):
            usage["input_tokens"] = cls._number(usage_metadata, "input_tokens", "prompt_tokens")
            usage["output_tokens"] = cls._number(usage_metadata, "output_tokens", "completion_tokens")
            usage["total_tokens"] = cls._number(usage_metadata, "total_tokens")
            cached = cls._number(
                usage_metadata, "cached_input_tokens", "cache_read_input_tokens", "cached_tokens")
            details = usage_metadata.get("input_token_details")
            if cached is None and isinstance(details, dict):
                cached = cls._number(details, "cache_read")
            usage["cached_input_tokens"] = cached
            reasoning = cls._number(usage_metadata, "reasoning_output_tokens")
            output_details = usage_metadata.get("output_token_details")
            if reasoning is None and isinstance(output_details, dict):
                reasoning = cls._number(output_details, "reasoning")
            usage["reasoning_output_tokens"] = reasoning

        runtime: dict[str, int | float] = {}
        response_metadata = message.response_metadata if isinstance(message, AIMessage) else None
        raw_runtime = response_metadata.get("tradingagents_runtime") if isinstance(response_metadata, dict) else None
        if isinstance(raw_runtime, dict):
            for key in sorted(_RUNTIME_SECONDS_FIELDS):
                value = raw_runtime.get(key)
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and value >= 0):
                    runtime[key] = value
            for key in sorted(_RUNTIME_CHARACTER_FIELDS):
                value = raw_runtime.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    runtime[key] = value
        return usage, runtime

    def _pop_run_locked(self, run_id: Any) -> dict[str, Any] | None:
        if run_id is not None:
            return self._runs.pop(("id", str(run_id)), None)
        thread_id = threading.get_ident()
        anonymous = self._anonymous_runs.get(thread_id)
        if not anonymous:
            return None
        run_key = anonymous.pop()
        if not anonymous:
            self._anonymous_runs.pop(thread_id, None)
        return self._runs.pop(run_key, None)

    def _record_finish(self, run_id: Any, status: str, usage: dict[str, int | None],
                       runtime: dict[str, int | float]) -> None:
        finished_at = float(self._clock())
        with self._lock:
            run = self._pop_run_locked(run_id)
            if run is None:
                return
            started_at = run["started_at"]
            if not math.isfinite(finished_at):
                finished_at = started_at
            interval_end = max(started_at, finished_at)
            elapsed = interval_end - started_at
            model, role = run["model"], run["role"]
            per_model = self._per_model.setdefault(model, self._empty_model_stats())
            per_role = self._per_role.setdefault(role, self._empty_role_stats())
            per_model["calls_finished"] += 1
            per_role["calls_finished"] += 1
            per_role["elapsed_seconds"] += elapsed
            if status == "failed":
                per_role["calls_failed"] += 1
            if usage["input_tokens"] is not None and usage["output_tokens"] is not None:
                per_model["calls_with_usage"] += 1
                per_role["calls_with_usage"] += 1

            token_fields = (
                ("input_tokens", "tokens_in", "input_seen"),
                ("output_tokens", "tokens_out", "output_seen"),
                ("cached_input_tokens", "cached_tokens_in", "cached_seen"),
                ("reasoning_output_tokens", "reasoning_tokens_out", "reasoning_seen"),
                ("total_tokens", "total_tokens", "total_seen"),
            )
            for field, total_attribute, seen_field in token_fields:
                value = usage[field]
                if value is None:
                    continue
                setattr(self, total_attribute, getattr(self, total_attribute) + value)
                self._usage_seen[field] = True
                per_model[field] += value
                per_model[seen_field] = True
                per_role[field] += value
                per_role[seen_field] = True

            call = {
                "role": role, "model": model, "status": status,
                "elapsed_seconds": elapsed, **usage,
                "prompt_characters": run["prompt_characters"],
                "repeated_message_characters": run["repeated_message_characters"],
                "analyst_evidence_characters": run["analyst_evidence_characters"],
                "repeated_analyst_evidence_characters": run[
                    "repeated_analyst_evidence_characters"
                ],
            }
            if runtime:
                call["runtime"] = dict(runtime)
            self._calls.append(call)
            self._completed_intervals.append((started_at, interval_end))

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Finish a successful call and extract provider usage and safe runtime metrics."""
        usage, runtime = self._usage_from_response(response)
        self._record_finish(kwargs.get("run_id"), "succeeded", usage, runtime)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Finish a failed call while leaving unavailable usage unknown."""
        del error
        usage = dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "total_tokens"))
        self._record_finish(kwargs.get("run_id"), "failed", usage, {})

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> dict[str, Any]:
        """Return the compact statistics used by the live CLI display."""
        with self._lock:
            return {"llm_calls": self.llm_calls, "tool_calls": self.tool_calls,
                    "tokens_in": self.tokens_in, "tokens_out": self.tokens_out}

    @staticmethod
    def _public_group_stats(grouped: dict[str, dict[str, int | float | bool]], *,
                            include_role_metrics: bool) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, values in sorted(grouped.items()):
            public = {
                "input_tokens": values["input_tokens"] if values["input_seen"] else None,
                "output_tokens": values["output_tokens"] if values["output_seen"] else None,
                "cached_input_tokens": values["cached_input_tokens"] if values["cached_seen"] else None,
                "calls_started": values["calls_started"],
                "reasoning_output_tokens": values["reasoning_output_tokens"] if values["reasoning_seen"] else None,
                "total_tokens": values["total_tokens"] if values["total_seen"] else None,
                "calls_finished": values["calls_finished"],
                "calls_with_usage": values["calls_with_usage"],
                "usage_complete": values["calls_started"] == values["calls_finished"] == values["calls_with_usage"],
            }
            if include_role_metrics:
                public.update(
                    calls_failed=values["calls_failed"], elapsed_seconds=values["elapsed_seconds"],
                    prompt_characters=values["prompt_characters"],
                    repeated_message_characters=values["repeated_message_characters"],
                    analyst_evidence_characters=values["analyst_evidence_characters"],
                    repeated_analyst_evidence_characters=values[
                        "repeated_analyst_evidence_characters"
                    ],
                )
            result[name] = public
        return result

    @staticmethod
    def _interval_union_seconds(intervals: list[tuple[float, float]]) -> float:
        if not intervals:
            return 0.0
        ordered = sorted(intervals)
        start, end = ordered[0]
        total = 0.0
        for next_start, next_end in ordered[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                total += end - start
                start, end = next_start, next_end
        return total + end - start

    def get_persistence_stats(self) -> dict[str, Any]:
        """Return observed telemetry without representing unavailable usage as zero.

        ``tool_calls`` counts LangChain agent tool callbacks only; deterministic
        prefetch/data preparation occurs outside that callback path.
        """
        with self._lock:
            per_model = self._public_group_stats(self._per_model, include_role_metrics=False)
            per_role = self._public_group_stats(self._per_role, include_role_metrics=True)
            calls_finished = sum(values["calls_finished"] for values in self._per_model.values())
            calls_with_usage = sum(values["calls_with_usage"] for values in self._per_model.values())
            calls = [{**call, **({"runtime": dict(call["runtime"])} if "runtime" in call else {})}
                     for call in self._calls]
            llm_elapsed_seconds = sum(call["elapsed_seconds"] for call in self._calls)
            return {
                "llm_calls": self.llm_calls,
                "input_tokens": self.tokens_in if self._usage_seen["input_tokens"] else None,
                "output_tokens": self.tokens_out if self._usage_seen["output_tokens"] else None,
                "cached_input_tokens": self.cached_tokens_in if self._usage_seen["cached_input_tokens"] else None,
                "per_model": per_model, "per_role": per_role, "calls": calls,
                "llm_elapsed_seconds": llm_elapsed_seconds,
                "model_active_seconds": self._interval_union_seconds(self._completed_intervals),
                "reasoning_output_tokens": self.reasoning_tokens_out if self._usage_seen["reasoning_output_tokens"] else None,
                "total_tokens": self.total_tokens if self._usage_seen["total_tokens"] else None,
                "usage_completeness": {
                    "calls_started": self.llm_calls, "calls_finished": calls_finished,
                    "calls_with_usage": calls_with_usage,
                    "calls_missing_usage": calls_finished - calls_with_usage,
                    "calls_unfinished": self.llm_calls - calls_finished,
                    "usage_complete": self.llm_calls == calls_finished == calls_with_usage,
                },
                "tool_calls": self.tool_calls,
                "tool_calls_scope": "LangChain agent tool callbacks; excludes deterministic prefetch and data preparation.",
                "billed_cost": None,
            }
