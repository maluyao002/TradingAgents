import json

import pytest

from scripts import research_diagnostic as diagnostic
from scripts.research_diagnostic import summarize_event
from tradingagents.research.contracts import ResearchRequest, Usage
from tradingagents.research.services import ModelReply


def test_diagnostic_never_copies_upstream_prose():
    result = summarize_event({"method": "error", "params": {"error": {
        "codexErrorInfo": "badRequest", "message": "Invalid schema: required; token-SECRET"}}})
    assert result["error_code"] == "badRequest"
    assert result["error_signatures"] == ["invalid schema", "required"]
    assert "SECRET" not in str(result)


def test_diagnostic_drops_prompt_and_output_fields():
    assert summarize_event({"method": "item/agentMessage/delta", "params": {"delta": "SECRET"}}) == {
        "method": "item/agentMessage/delta"}


@pytest.mark.parametrize("invalid,cleanup_error", [(False, False), (True, False), (False, True)])
def test_one_inference_and_known_usage_survive_validation_or_cleanup_failure(tmp_path, monkeypatch,
                                                                          invalid, cleanup_error):
    calls = []

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if cleanup_error:
                raise diagnostic.CodexAdapterError("Synthetic cleanup failure")

        def complete(self, role, payload, request):
            calls.append(role)
            return ModelReply(data={} if invalid else {"summary": "Synthetic planner response"},
                              usage=Usage(input_tokens=12, output_tokens=3))

    monkeypatch.setattr(diagnostic, "CodexModelService", FakeService)
    monkeypatch.setattr(diagnostic, "load_snapshot", lambda *args: None)
    monkeypatch.setattr(diagnostic, "_prompt_evidence", lambda *args: {})
    request = ResearchRequest(ticker="TEST", cutoff="2026-09-17T00:00:00Z", backend="codex",
                              output_dir=tmp_path / "diagnostic")
    result = diagnostic.DiagnosticWorker(tmp_path / "runtime")(request)
    trace = json.loads((request.output_dir / "diagnostic.json").read_text())
    assert calls == ["planner"]
    assert result.usage.total_tokens == 15
    assert trace["usage"]["input_tokens"] == 12
    assert trace["status"] == ("failed" if invalid or cleanup_error else "succeeded")
    assert "Synthetic planner response" not in json.dumps(trace)
