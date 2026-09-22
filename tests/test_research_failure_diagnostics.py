from tests.test_research_bounded_finalization import BoundedFixture, run_fixture
from tradingagents.codex.adapter import CodexInferenceError
from tradingagents.research.storage import read_json


def test_failed_stage_reason_and_duration_are_saved_without_raw_error_or_free_retry(tmp_path):
    class FailedCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == "verify_report-coverage-1":
                raise CodexInferenceError("private-provider-prompt-SECRET", reason="protocol_stream_event_limit")
            return super().complete(role, payload, request)

    request, services, result = run_fixture(tmp_path, FailedCoverage())
    assert result.stop_reason == "stage_failed"
    assert not result.usage.complete
    metadata = read_json(request.output_dir / "run_metadata.json")
    assert metadata["failure_reason"] == "protocol_stream_event_limit"
    assert metadata["failed_stage"] == "verify_report-coverage-1"
    assert metadata["call_timings"][-1]["completed"] is False
    assert metadata["call_timings"][-1]["duration_seconds"] >= 0
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert resources["dispatched"]
    assert not resources["usage"]["complete"]
    for path in request.output_dir.rglob("*"):
        if path.is_file():
            assert b"private-provider-prompt-SECRET" not in path.read_bytes()
    count = len(services.models.calls)
    from tradingagents.research.engine import run_research
    repeated = run_research(request, services)
    assert not repeated.usage.complete
    assert len(services.models.calls) == count


def test_response_with_known_usage_but_invalid_stage_is_not_latency_training_data(tmp_path):
    class InvalidReply(BoundedFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == "verify_report-coverage-0":
                reply.data["unexpected_field"] = "invalid stage reply"
            return reply

    request, _, result = run_fixture(tmp_path, InvalidReply())
    assert result.stop_reason == "stage_failed"
    assert result.usage.complete
    last = read_json(request.output_dir / "run_metadata.json")["call_timings"][-1]
    assert last["completed"] is True
    assert last["stage_accepted"] is False
