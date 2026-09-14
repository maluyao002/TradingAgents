"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. A parse, validation, or explicit unsupported-format failure
   gets one free-text retry with the identical prompt. Provider transport,
   authentication, quota, timeout, and programming failures propagate so
   callers can handle them accurately.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

try:  # langchain-core is a required dependency, but keep this helper portable.
    from langchain_core.exceptions import OutputParserException
except ImportError:  # pragma: no cover - defensive for downstream embedders
    OutputParserException = ()  # type: ignore[assignment]

try:
    from pydantic import ValidationError
except ImportError:  # pragma: no cover - pydantic is required by this package
    ValidationError = ()  # type: ignore[assignment]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Schema-only structured output binds exactly one tool (the schema itself), so a
# model that reaches for a search tool emits an unknown tool call and the whole
# structured attempt is discarded for a free-text retry. Agents on this path
# state the constraint explicitly rather than relying on the binding alone
# (#1130).
NO_EXTERNAL_TOOLS = (
    "Use only the evidence provided in this prompt. Do not call external tools "
    "or search the web; if something is missing, say so explicitly."
)


_FORMAT_TERMS = (
    "structured output",
    "structured-output",
    "response_format",
    "response format",
    "json_schema",
    "json schema",
    "function_calling",
    "function calling",
    "tool calling",
    "tool_call",
)
_UNSUPPORTED_TERMS = (
    "unsupported",
    "not supported",
    "does not support",
    "not available",
    "not implemented",
)
_PARSE_TERMS = (
    "bad json",
    "invalid json",
    "malformed json",
    "failed to parse",
    "parse error",
    "parsed result",
    "schema validation",
    "validation error",
)


def _is_explicit_unsupported_format_error(exc: Exception) -> bool:
    """Whether a provider specifically rejected the structured-output format.

    Providers commonly expose this as an HTTP 400.  A generic 400 can also
    mean a bad credential, invalid model, or request bug, so it must not
    trigger a second model call.  Require both a structured-format reference
    and an explicit lack-of-support phrase before treating it as recoverable.
    """
    if getattr(exc, "status_code", None) != 400:
        return False
    message = str(exc).lower()
    return any(term in message for term in _FORMAT_TERMS) and any(
        term in message for term in _UNSUPPORTED_TERMS
    )


def _is_recoverable_structured_error(exc: Exception) -> bool:
    """Return true only for malformed structured output or unsupported format."""
    if isinstance(exc, (OutputParserException, ValidationError)):
        return True
    if _is_explicit_unsupported_format_error(exc):
        return True
    # Some provider wrappers surface parser failures as a plain ValueError.
    # Keep this text check narrow so unrelated ValueErrors still propagate.
    if isinstance(exc, ValueError):
        message = str(exc).lower()
        return any(term in message for term in _PARSE_TERMS)
    return False


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None
    except Exception as exc:
        if not _is_explicit_unsupported_format_error(exc):
            raise
        logger.warning(
            "%s: provider explicitly rejected the structured-output format (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """Run and render structured output; retry only recoverable format failures.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
        except Exception as exc:
            if not _is_recoverable_structured_error(exc):
                raise
            logger.warning(
                "%s: structured-output parse/format failed (%s); retrying once as free text",
                agent_name, exc,
            )
        else:
            if result is not None:
                # Keep rendering outside the recovery block: a renderer bug or
                # unexpected return type is a programming error, not a reason
                # to issue an unstructured second request.
                return render(result)
            logger.warning(
                "%s: structured output returned no parsed result; retrying once as free text",
                agent_name,
            )

    response = plain_llm.invoke(prompt)
    return response.content
