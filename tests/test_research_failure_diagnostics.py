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


def test_safe_transport_diagnostic_reaches_metadata_and_resource_without_leak(tmp_path):
    class FailedCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == "verify_report-coverage-1":
                raise CodexInferenceError(
                    "private-provider-prompt-SECRET", reason="transport_rpc_rejected",
                    diagnostic={"kind": "rpc_rejection", "phase": "turn_start",
                                "method": "turn/start", "code": -32000,
                                "server_error": "private-provider-prompt-SECRET"},
                )
            return super().complete(role, payload, request)

    request, _, result = run_fixture(tmp_path, FailedCoverage())
    assert result.stop_reason == "stage_failed"
    assert result.usage.complete is False
    expected = {"kind": "rpc_rejection", "phase": "turn_start",
                "method": "turn/start", "code": -32000}
    metadata = read_json(request.output_dir / "run_metadata.json")
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert metadata["failure_reason"] == resources["failure_reason"] == "transport_rpc_rejected"
    assert metadata["failure_diagnostic"] == resources["failure_diagnostic"] == expected
    assert resources["usage"]["complete"] is False
    assert resources["dispatched"] is True
    for path in request.output_dir.rglob("*"):
        if path.is_file():
            assert b"private-provider-prompt-SECRET" not in path.read_bytes()


def test_input_length_limit_reaches_both_artifacts_without_server_message(tmp_path):
    class FailedCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == "verify_report-coverage-1":
                raise CodexInferenceError(
                    "private-provider-prompt-SECRET", reason="transport_input_length_limit",
                    diagnostic={"kind": "input_length_limit", "phase": "turn_start",
                                "method": "turn/start", "code": -32602,
                                "reported_max_length": 272000,
                                "server_error": "private-provider-prompt-SECRET"},
                )
            return super().complete(role, payload, request)

    request, _, result = run_fixture(tmp_path, FailedCoverage())
    assert result.stop_reason == "stage_failed" and not result.usage.complete
    expected = {"kind": "input_length_limit", "phase": "turn_start",
                "method": "turn/start", "code": -32602, "reported_max_length": 272000}
    metadata = read_json(request.output_dir / "run_metadata.json")
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert metadata["failure_reason"] == resources["failure_reason"] == "transport_input_length_limit"
    assert metadata["failure_diagnostic"] == resources["failure_diagnostic"] == expected
    assert resources["dispatched"] is True and resources["usage"]["complete"] is False
    for path in request.output_dir.rglob("*"):
        if path.is_file():
            assert b"private-provider-prompt-SECRET" not in path.read_bytes()


def test_local_prompt_preflight_failure_keeps_usage_unknown(tmp_path):
    class FailedCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == "verify_report-coverage-1":
                raise CodexInferenceError(
                    "private-prompt-SECRET", reason="local_prompt_size_limit",
                    diagnostic={"kind": "local_prompt_size_limit", "phase": "preflight",
                                "request_bytes": 1_048_578, "limit_bytes": 1_048_576},
                )
            return super().complete(role, payload, request)

    request, _, result = run_fixture(tmp_path, FailedCoverage())
    metadata = read_json(request.output_dir / "run_metadata.json")
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    expected = {"kind": "local_prompt_size_limit", "phase": "preflight",
                "request_bytes": 1_048_578, "limit_bytes": 1_048_576}
    assert result.stop_reason == "stage_failed" and result.usage.complete is False
    assert metadata["failure_reason"] == resources["failure_reason"] == "local_prompt_size_limit"
    assert metadata["failure_diagnostic"] == resources["failure_diagnostic"] == expected
    assert resources["dispatched"] is True and resources["usage"]["complete"] is False
    for path in request.output_dir.rglob("*"):
        if path.is_file():
            assert b"private-prompt-SECRET" not in path.read_bytes()


def test_arbitrary_exception_class_name_is_not_persisted(tmp_path):
    PrivatePromptSECRET = type("PrivatePromptSECRET", (RuntimeError,), {})

    class FailedCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == "verify_report-coverage-1":
                raise PrivatePromptSECRET("private-provider-prompt-SECRET")
            return super().complete(role, payload, request)

    request, _, result = run_fixture(tmp_path, FailedCoverage())
    assert result.stop_reason == "stage_failed"
    metadata = read_json(request.output_dir / "run_metadata.json")
    assert metadata["failure_type"] == "Exception"
    assert metadata["failure_diagnostic"] is None
    for path in request.output_dir.rglob("*"):
        if path.is_file():
            assert b"PrivatePromptSECRET" not in path.read_bytes()
            assert b"private-provider-prompt-SECRET" not in path.read_bytes()
