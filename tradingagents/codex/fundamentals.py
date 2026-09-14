"""Fundamentals-only pilot shared by the API and Codex backends."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from copy import deepcopy
from datetime import date as date_type
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)
from tradingagents.agents.utils.evidence import _normalize_prepared
from tradingagents.dataflows.preparation import prepare_fundamentals
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.model_profiles import MODEL_PROFILES


class FundamentalsRunError(RuntimeError):
    """A safe-to-display failure from the fundamentals pilot."""


def _canonical_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("analysis date must use YYYY-MM-DD")
    try:
        parsed = date_type.fromisoformat(value)
    except ValueError:
        raise ValueError("analysis date must use YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("analysis date must use YYYY-MM-DD")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(
        ord(character) < 32 and character not in "\t\n\r" for character in value
    ):
        raise ValueError(f"{label} must be non-empty text")
    return value


def _validate_prepared(prepared: object, ticker: str, analysis_date: str) -> dict:
    if not isinstance(prepared, Mapping):
        raise ValueError("prepared evidence must be an object")
    snapshot = deepcopy(dict(prepared))
    if snapshot.get("symbol") != ticker:
        raise ValueError("prepared evidence symbol does not match the requested ticker")
    if snapshot.get("analysis_date") != analysis_date:
        raise ValueError("prepared evidence date does not match the requested analysis date")

    normalized = _normalize_prepared(snapshot, role="fundamentals")
    if normalized.errors:
        raise ValueError("prepared evidence failed structural validation")

    try:
        json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("prepared evidence must be JSON serializable") from None
    return snapshot


def validate_inputs(
    ticker: object,
    analysis_date: object,
    prepared: object | None = None,
) -> tuple[str, str, dict | None]:
    """Validate replay identity and shape before starting any model backend."""

    if not isinstance(ticker, str):
        raise ValueError("ticker must be a non-empty string")
    normalized_ticker = safe_ticker_component(ticker.strip().upper())
    normalized_date = _canonical_date(analysis_date)
    snapshot = (
        _validate_prepared(prepared, normalized_ticker, normalized_date)
        if prepared is not None
        else None
    )
    return normalized_ticker, normalized_date, snapshot


def _messages(value: object) -> list:
    if hasattr(value, "to_messages"):
        messages = value.to_messages()
    elif isinstance(value, (list, tuple)):
        messages = list(value)
    else:
        raise ValueError("fundamentals prompt must be a message sequence")
    if not isinstance(messages, list) or not messages:
        raise ValueError("fundamentals prompt must contain messages")
    return messages


class _CodexChatModel:
    """Translate one analyst invocation to the adapter's isolated text call."""

    def __init__(self, adapter: Any, model: str, effort: str):
        self._adapter = adapter
        self._model = model
        self._effort = effort

    def invoke(self, prompt: object) -> AIMessage:
        messages = _messages(prompt)
        if not isinstance(messages[0], SystemMessage):
            raise ValueError("fundamentals prompt must begin with one system message")
        instructions = _text(messages[0].content, "system instructions")
        history: list[str] = []
        for message in messages[1:]:
            if isinstance(message, SystemMessage):
                raise ValueError("additional system messages are not allowed")
            if isinstance(message, HumanMessage):
                role = "human"
            elif isinstance(message, AIMessage):
                if message.tool_calls or message.invalid_tool_calls:
                    raise ValueError("tool calls are not supported by the fundamentals pilot")
                role = "ai"
            else:
                raise ValueError("only human and AI text history is supported")
            history.append(f"<{role}>\n{_text(message.content, role + ' message')}\n</{role}>")
        if not history:
            raise ValueError("fundamentals prompt must contain human evidence")
        result = self._adapter.complete(
            instructions,
            "\n\n".join(history),
            self._model,
            self._effort,
        )
        return AIMessage(content=_text(result, "Codex response"))


class _APIChatModel:
    """Require the API backend to honor the same text-only response contract."""

    def __init__(self, llm: Any):
        self._llm = llm

    def invoke(self, prompt: object) -> AIMessage:
        try:
            result = self._llm.invoke(prompt)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise FundamentalsRunError(
                f"OpenAI fundamentals inference failed ({type(exc).__name__})."
            ) from None
        if not isinstance(result, AIMessage):
            raise FundamentalsRunError("OpenAI fundamentals response was not a text message.")
        if result.tool_calls or result.invalid_tool_calls:
            raise FundamentalsRunError("OpenAI fundamentals response requested unsupported tools.")
        try:
            content = _text(result.content, "OpenAI response")
        except ValueError:
            raise FundamentalsRunError("OpenAI fundamentals response was not plain text.") from None
        return result.model_copy(update={"content": content})


def _state(ticker: str, analysis_date: str, prepared: dict) -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=ticker)],
        "company_of_interest": ticker,
        "asset_type": "stock",
        "instrument_context": "",
        "trade_date": analysis_date,
        "prepared_data": {"fundamentals": prepared},
        "evidence_packets": {},
        "fundamentals_report": "",
    }


def _invoke(llm: Any, ticker: str, analysis_date: str, prepared: dict) -> dict:
    try:
        return create_fundamentals_analyst(llm)(_state(ticker, analysis_date, prepared))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # Adapter errors are already deliberately sanitized and are handled by
        # the pilot CLI with specific Codex guidance.
        from tradingagents.codex.adapter import CodexAdapterError

        if isinstance(exc, (CodexAdapterError, FundamentalsRunError)):
            raise
        raise FundamentalsRunError(
            f"Fundamentals analysis failed ({type(exc).__name__})."
        ) from None


def _api_model(model: str, effort: str) -> Any:
    try:
        from tradingagents.llm_clients.factory import create_llm_client

        return create_llm_client(
            provider="openai",
            model=model,
            reasoning_effort=effort,
            max_retries=0,
        ).get_llm()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FundamentalsRunError(
            f"OpenAI fundamentals client could not start ({type(exc).__name__})."
        ) from None


def _prepare(ticker: str, analysis_date: str) -> dict:
    try:
        snapshot = deepcopy(prepare_fundamentals(ticker, analysis_date))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FundamentalsRunError(
            f"Fundamentals evidence preparation failed ({type(exc).__name__})."
        ) from None
    if not isinstance(snapshot, dict):
        raise FundamentalsRunError("Fundamentals evidence preparation returned invalid data.")
    snapshot["symbol"] = ticker
    return _validate_prepared(snapshot, ticker, analysis_date)


def _snapshot_hash(prepared: dict) -> str:
    serialized = json.dumps(
        prepared,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_profile_selection(model: str, effort: str) -> None:
    allowed = {
        (profile["agents"]["fundamentals"]["model"],
         profile["agents"]["fundamentals"]["reasoning_effort"])
        for profile in MODEL_PROFILES.values()
    }
    if (model, effort) not in allowed:
        raise ValueError("model and effort must match a fundamentals pilot profile")


def run_fundamentals(
    ticker: str,
    analysis_date: str,
    *,
    backend: str,
    model: str,
    effort: str,
    prepared: dict | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Run the existing fundamentals analyst once with an explicit backend."""

    started = time.monotonic()
    ticker, analysis_date, snapshot = validate_inputs(ticker, analysis_date, prepared)
    if backend not in {"api", "codex"}:
        raise ValueError("backend must be 'api' or 'codex'")
    model = _text(model, "model")
    effort = _text(effort, "effort")
    if backend == "api" and adapter is not None:
        raise ValueError("adapter is only valid for the Codex backend")
    _validate_profile_selection(model, effort)

    if backend == "api":
        snapshot = snapshot if snapshot is not None else _prepare(ticker, analysis_date)
        update = _invoke(
            _APIChatModel(_api_model(model, effort)),
            ticker,
            analysis_date,
            snapshot,
        )
    elif adapter is not None:
        adapter.validate_selection(model, effort)
        snapshot = snapshot if snapshot is not None else _prepare(ticker, analysis_date)
        update = _invoke(
            _CodexChatModel(adapter, model, effort), ticker, analysis_date, snapshot
        )
    else:
        from tradingagents.codex.adapter import CodexAdapter

        home = os.environ.get("TRADINGAGENTS_CODEX_HOME", "~/.tradingagents/codex")
        with CodexAdapter(home=home, timeout=300) as owned_adapter:
            owned_adapter.validate_selection(model, effort)
            snapshot = snapshot if snapshot is not None else _prepare(ticker, analysis_date)
            update = _invoke(
                _CodexChatModel(owned_adapter, model, effort),
                ticker,
                analysis_date,
                snapshot,
            )

    packet = deepcopy(update["evidence_packets"]["fundamentals"])
    packet["status"] = (
        "validated_handoff" if packet.get("compacted") else "review_required"
    )
    return {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "backend": backend,
        "model": model,
        "effort": effort,
        "prepared_data": deepcopy(update["prepared_data"]["fundamentals"]),
        "prepared_sha256": _snapshot_hash(snapshot),
        "fundamentals_report": update["fundamentals_report"],
        "evidence_packet": packet,
        "elapsed_seconds": max(0.0, time.monotonic() - started),
    }
