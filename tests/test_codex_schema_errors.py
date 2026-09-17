import pytest

from tradingagents.codex.adapter import (
    CodexInferenceError,
    CodexStructuredOutputError,
    _turn_error,
)


@pytest.mark.parametrize("signature", ["Invalid schema for response_format", "invalid_json_schema"])
def test_schema_error_is_actionable_without_echoing_raw_detail(signature):
    error = _turn_error({"message": signature + " private prompt / token-SECRET"})
    assert isinstance(error, CodexStructuredOutputError)
    assert "SECRET" not in str(error)
    assert "private" not in str(error)


def test_unrecognized_error_stays_redacted():
    error = _turn_error({"message": "Something else token-SECRET"})
    assert type(error) is CodexInferenceError
    assert "SECRET" not in str(error)
