"""Deterministic, non-secret identity for resumable analysis runs."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tradingagents.agents.utils.analysis_time import analysis_calendar

CHECKPOINT_IDENTITY_VERSION = "checkpoint-config-v2"
CODEX_BRIDGE_VERSION = "codex-bridge-v1"

# These query parameters select API behavior rather than authenticate a caller.
# All other query values are omitted from endpoint identity so credentials cannot
# influence, or be recovered from, a checkpoint signature.
_SEMANTIC_ENDPOINT_QUERY_KEYS = frozenset({"api-version", "api_version", "version"})


def _stable_value(value: Any) -> Any:
    """Return a JSON-safe value with deterministic mapping/set ordering."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        items = (_stable_value(item) for item in value)
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_stable_value(item) for item in value]
    # Allow harmless programmatic scalar types (for example an enum) without
    # making the identity helper dependent on their implementation details.
    return str(value)


def _endpoint_identity(value: Any) -> str | None:
    """Hash an endpoint after removing credentials and non-semantic query values."""
    if value in (None, ""):
        return None
    raw = str(value)
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        # A local provider may accept a host/path without a URL scheme. Hashing
        # still keeps that potentially sensitive runtime detail out of metadata.
        sanitized = raw.split("?", 1)[0].split("#", 1)[0]
    else:
        hostname = parsed.hostname.lower()
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{hostname}:{port}" if port is not None else hostname
        semantic_query = sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() in _SEMANTIC_ENDPOINT_QUERY_KEYS
        )
        sanitized = urlunsplit(
            (
                parsed.scheme.lower(),
                netloc,
                parsed.path.rstrip("/"),
                urlencode(semantic_query),
                "",
            )
        )
    return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()


def _model_identity(config: Mapping[str, Any], backend: str) -> dict[str, Any]:
    """Select only model settings that the active backend consumes."""
    configured_agents = config.get("agent_models")
    agents: dict[str, dict[str, Any]] = {}
    if isinstance(configured_agents, Mapping):
        for role, setting in sorted(configured_agents.items(), key=lambda pair: str(pair[0])):
            if isinstance(setting, Mapping):
                agents[str(role)] = {
                    "model": _stable_value(setting.get("model")),
                    "reasoning_effort": _stable_value(setting.get("reasoning_effort")),
                }

    if backend == "codex":
        return {"agents": agents}

    provider = str(config.get("llm_provider", "openai")).lower()
    endpoint = config.get("backend_url")
    if provider == "ollama":
        from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

        spec = OPENAI_COMPATIBLE_PROVIDERS["ollama"]
        endpoint = endpoint or os.environ.get(spec.base_url_env) or spec.base_url
    elif provider == "azure":
        # AzureOpenAIClient does not forward backend_url. The Azure SDK reads
        # its endpoint/version from the environment; deployment falls back to
        # each configured model when the deployment variable is absent.
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    result: dict[str, Any] = {
        "provider": provider,
        "endpoint": _endpoint_identity(endpoint),
    }
    if provider == "azure":
        result["azure"] = {
            "deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME"),
            "api_version": os.environ.get("OPENAI_API_VERSION"),
            "legacy_endpoint": _endpoint_identity(os.environ.get("OPENAI_API_BASE")),
        }
    if agents:
        result["agents"] = agents
    else:
        result.update(
            {
                "quick": _stable_value(config.get("quick_think_llm")),
                "deep": _stable_value(config.get("deep_think_llm")),
            }
        )
        effort_key = {
            "openai": "openai_reasoning_effort",
            "google": "google_thinking_level",
            "anthropic": "anthropic_effort",
        }.get(provider)
        if effort_key is not None:
            result["reasoning"] = _stable_value(config.get(effort_key))
    result["generation"] = {
        "temperature": _stable_value(config.get("temperature")),
        "max_tokens": _stable_value(config.get("max_tokens")),
        "max_retries": _stable_value(config.get("llm_max_retries")),
    }
    return result


def _calendar_identity(analysis_date: Any) -> dict[str, Any] | None:
    """Capture the local date boundary used by prompts and evidence filtering."""
    if analysis_date is None:
        return None
    calendar = analysis_calendar(str(analysis_date))
    return {
        "day_start": calendar.get("day_start"),
        "day_end_exclusive": calendar.get("day_end_exclusive"),
        "decision_cutoff": calendar.get("decision_cutoff"),
    }


def checkpoint_run_signature(
    config: Mapping[str, Any],
    selected_analysts: Sequence[str],
    asset_type: str,
    prompt_policy_version: str,
    analysis_date: Any = None,
) -> str:
    """Return a versioned digest of settings that can change resumed behavior.

    The explicit allowlist deliberately excludes credentials, callbacks,
    filesystem destinations, and display-only profile labels. The digest is
    the only representation returned to checkpoint code, so raw configuration
    is never written into LangGraph's SQLite metadata.
    """
    backend = str(config.get("llm_backend", "api")).lower()
    payload = {
        "version": CHECKPOINT_IDENTITY_VERSION,
        "backend": backend,
        "backend_protocol": CODEX_BRIDGE_VERSION if backend == "codex" else "api",
        "analysts": list(selected_analysts),
        "asset_type": asset_type,
        "prompt_policy": prompt_policy_version,
        "calendar": _calendar_identity(analysis_date),
        "language": _stable_value(config.get("output_language", "English")),
        "graph": {
            "max_debate_rounds": _stable_value(config.get("max_debate_rounds")),
            "max_risk_discuss_rounds": _stable_value(config.get("max_risk_discuss_rounds")),
            "max_recur_limit": _stable_value(config.get("max_recur_limit", 100)),
        },
        "models": _model_identity(config, backend),
        "data": {
            "data_vendors": _stable_value(config.get("data_vendors", {})),
            "tool_vendors": _stable_value(config.get("tool_vendors", {})),
            "news_article_limit": _stable_value(config.get("news_article_limit")),
            "global_news_article_limit": _stable_value(config.get("global_news_article_limit")),
            "global_news_lookback_days": _stable_value(config.get("global_news_lookback_days")),
            "global_news_queries": _stable_value(config.get("global_news_queries", [])),
        },
        "reflection": {
            "benchmark_ticker": _stable_value(config.get("benchmark_ticker")),
            "benchmark_map": _stable_value(config.get("benchmark_map", {})),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    protocol = f"{CODEX_BRIDGE_VERSION}|" if backend == "codex" else ""
    return (
        f"{backend}|{protocol}{CHECKPOINT_IDENTITY_VERSION}|"
        f"prompts={prompt_policy_version}|{digest}"
    )
