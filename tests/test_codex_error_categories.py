"""Unattended retries distinguish capacity failures without exporting private errors."""

import time
from unittest.mock import Mock

import pytest

from tradingagents.codex.adapter import (
    CodexAdapter,
    CodexAuthenticationError,
    CodexInferenceError,
    CodexTransientError,
    CodexUsageLimitError,
    _turn_error,
)


@pytest.mark.parametrize(("info", "expected"), [
    ("usageLimitExceeded", CodexUsageLimitError),
    ("sessionBudgetExceeded", CodexUsageLimitError),
    ("unauthorized", CodexAuthenticationError),
    ("rateLimitExceeded", CodexTransientError),
    ("serverOverloaded", CodexTransientError),
    ({"httpConnectionFailed": {"httpStatusCode": 503}}, CodexTransientError),
    ({"responseStreamDisconnected": {"httpStatusCode": None}}, CodexTransientError),
    ({"httpConnectionFailed": {"httpStatusCode": 401}}, CodexAuthenticationError),
    ({"httpConnectionFailed": {"httpStatusCode": 400}}, CodexInferenceError),
    ({"httpConnectionFailed": {"httpStatusCode": "503"}}, CodexInferenceError),
    ({"httpConnectionFailed": {"httpStatusCode": True}}, CodexInferenceError),
    ({"private-value": {"httpStatusCode": 503}}, CodexInferenceError),
    ("contextWindowExceeded", CodexInferenceError),
    ("private-value", CodexInferenceError),
    (["usageLimitExceeded"], CodexInferenceError),
    (None, CodexInferenceError),
])
def test_codes_control_retry_policy_without_leaking_details(info, expected):
    error = _turn_error({"codexErrorInfo": info, "message": "PRIVATE_KEY", "additionalDetails": "PRIVATE_KEY"})
    assert type(error) is expected
    assert "PRIVATE_KEY" not in str(error)
    assert "private-value" not in str(error)


@pytest.mark.parametrize("event", ["error", "turn/completed"])
def test_capacity_error_survives_both_protocol_paths(tmp_path, event):
    adapter = CodexAdapter(home=tmp_path / "runtime")
    transport = Mock()
    adapter._transport = transport
    error = {"codexErrorInfo": "usageLimitExceeded", "message": "PRIVATE_KEY"}
    final_params = {"threadId": "t", "turnId": "r", "error": error}
    if event == "turn/completed":
        final_params = {"threadId": "t", "turn": {"id": "r", "status": "failed", "error": error}}
    transport.wait_notification.side_effect = [
        {"method": "turn/started", "params": {"threadId": "t", "turn": {"id": "r", "status": "inProgress"}}},
        {"method": event, "params": final_params},
    ]
    with pytest.raises(CodexUsageLimitError, match="usage limit") as exc:
        adapter._wait_for_turn("t", "r", time.monotonic() + 1, model="test", effort="low")
    assert "PRIVATE_KEY" not in str(exc.value)
