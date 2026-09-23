"""Offline regression coverage for bounded Codex adapter failure reasons."""

import time
from unittest.mock import Mock

import pytest

import tradingagents.codex.adapter as adapter_module
from tradingagents.codex.adapter import (
    CODEX_FAILURE_REASONS,
    CodexAdapter,
    CodexInferenceError,
    CodexStructuredOutputError,
    CodexUsageLimitError,
    _turn_error,
    codex_failure_diagnostic,
    codex_failure_reason,
    safe_failure_type,
)


@pytest.mark.parametrize(("payload", "expected_type", "reason"), [
    ({"message": "invalid_json_schema private-prompt-SECRET"}, CodexStructuredOutputError, "provider_schema_rejected"),
    ({"codexErrorInfo": "usageLimitExceeded", "message": "private-prompt-SECRET"}, CodexUsageLimitError, "provider_usage_limited"),
    ({"codexErrorInfo": "unrecognized-private-code", "message": "private-prompt-SECRET"}, CodexInferenceError, "provider_error"),
])
def test_provider_reason_codes_are_allowlisted_and_redacted(payload, expected_type, reason):
    error = _turn_error(payload)

    assert type(error) is expected_type
    assert codex_failure_reason(error) == reason
    assert error.reason == reason
    assert reason in CODEX_FAILURE_REASONS
    assert "SECRET" not in str(error)
    assert "SECRET" not in error.reason


def test_helper_rejects_arbitrary_reason_attributes_and_preserves_legacy_constructor():
    class UntrustedError(RuntimeError):
        reason = "provider message: token-SECRET"

    legacy = CodexInferenceError("same legacy message")
    supplied = CodexInferenceError("redacted", reason="provider message: token-SECRET")

    assert str(legacy) == "same legacy message"
    assert codex_failure_reason(legacy) == "unknown"
    assert supplied.reason == "unknown"
    assert codex_failure_reason(supplied) == "unknown"
    with pytest.raises(AttributeError):
        supplied.reason = "provider message: token-SECRET"
    assert codex_failure_reason(UntrustedError()) == "unknown"
    assert "SECRET" not in codex_failure_reason(UntrustedError())


def test_protocol_guard_and_missing_output_have_specific_safe_reasons(tmp_path, monkeypatch):
    adapter = CodexAdapter(home=tmp_path / "runtime")
    transport = Mock()
    adapter._transport = transport
    monkeypatch.setattr(adapter_module, "_MAX_TURN_EVENTS", 0)
    transport.wait_notification.return_value = {
        "method": "warning",
        "params": {"message": "private warning", "threadId": "thread"},
    }

    with pytest.raises(CodexInferenceError) as guard:
        adapter._wait_for_turn("thread", "turn", time.monotonic() + 1, model="test", effort="low")
    assert codex_failure_reason(guard.value) == "protocol_notification_limit"

    monkeypatch.setattr(adapter_module, "_MAX_TURN_EVENTS", 10_000)
    transport.wait_notification.side_effect = [
        {"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "inProgress"}}},
        {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}},
    ]
    with pytest.raises(CodexInferenceError) as output:
        adapter._wait_for_turn("thread", "turn", time.monotonic() + 1, model="test", effort="low")
    assert codex_failure_reason(output.value) == "missing_output"


def test_cleanup_failure_has_a_bounded_reason(tmp_path):
    adapter = CodexAdapter(home=tmp_path / "runtime")
    adapter._transport = Mock()
    adapter._transport.request.return_value = None
    adapter._transport.pop_notifications.return_value = None

    error = adapter._cleanup_thread("thread", "turn")

    assert isinstance(error, CodexInferenceError)
    assert codex_failure_reason(error) == "cleanup_interrupt"


def test_reason_lookup_never_invokes_untrusted_property_or_accepts_spoofed_category():
    class Untrusted(RuntimeError):
        @property
        def reason(self):
            raise ValueError("private error must not escape failure handler")

    class Spoofed(RuntimeError):
        reason = "provider_usage_limited"

    assert codex_failure_reason(Untrusted()) == "unknown"
    assert codex_failure_reason(Spoofed()) == "unknown"


def test_failure_type_and_diagnostic_helpers_discard_untrusted_exception_data():
    PrivatePromptSECRET = type("PrivatePromptSECRET", (RuntimeError,), {})
    assert safe_failure_type(PrivatePromptSECRET()) == "Exception"
    assert safe_failure_type(CodexInferenceError("redacted")) == "CodexInferenceError"
    assert safe_failure_type(ValueError()) == "ValueError"

    error = CodexInferenceError(
        "private-prompt-SECRET", reason="transport_rpc_rejected",
        diagnostic={"kind": "rpc_rejection", "phase": "turn_start",
                    "method": "private-method-SECRET", "code": True,
                    "server_error": "private-provider-SECRET"},
    )
    assert codex_failure_diagnostic(error) == {"kind": "rpc_rejection", "phase": "turn_start"}
    error._diagnostic = {"kind": "rpc_rejection", "phase": "turn_start",
                         "method": "turn/start", "code": 2**64,
                         "server_error": "private-provider-SECRET"}
    assert codex_failure_diagnostic(error) == {"kind": "rpc_rejection", "phase": "turn_start",
                                               "method": "turn/start"}
