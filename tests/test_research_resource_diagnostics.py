import pytest

from tradingagents.research.resource_diagnostics import valid_source_resource_shape


def resources(**updates):
    return {"usage": {}, "elapsed_seconds": 0, "dispatched": False, "by_stage": {}, **updates}


def timing(**updates):
    return {"stage": "verify_report", "role": "verifier", "model": "test", "effort": "high",
            "usage_origin": "current_live", "service_kind": "codex", "completed": True,
            "stage_accepted": True, "duration_seconds": 100, **updates}


def test_legacy_and_current_resources_have_strict_supported_shapes():
    assert valid_source_resource_shape(resources())
    assert valid_source_resource_shape(resources(call_timings=[timing()], active_stage=None,
                                                failed_stage=None, failure_reason=None))
    assert valid_source_resource_shape(resources(failure_diagnostic=None))
    assert valid_source_resource_shape(resources(
        failure_reason="transport_request_size_limit",
        failure_diagnostic={"kind": "request_size_limit", "phase": "turn_start",
                            "request_bytes": 1_270_000, "limit_bytes": 4_194_304}))
    assert valid_source_resource_shape(resources(
        failure_reason="transport_rpc_rejected",
        failure_diagnostic={"kind": "rpc_rejection", "phase": "turn_start",
                            "method": "turn/start", "code": -32000}))
    assert not valid_source_resource_shape(resources(unknown_field="no"))


@pytest.mark.parametrize("updates", [
    {"call_timings": {}}, {"active_stage": []}, {"failed_stage": 123},
    {"failure_reason": "private provider text"}, {"failure_reason": ["unknown"]},
    {"failure_diagnostic": {"kind": "rpc_rejection", "phase": "turn_start",
                             "method": "private-method-SECRET", "code": -32000}},
    {"failure_diagnostic": {"kind": "rpc_rejection", "phase": "turn_start",
                             "method": "turn/start", "code": "-32000"}},
    {"failure_diagnostic": {"kind": "rpc_rejection", "phase": "turn_start",
                             "method": "turn/start", "code": 2**64}},
    {"failure_diagnostic": {"kind": "request_size_limit", "phase": "turn_start",
                             "request_bytes": 100, "limit_bytes": 99,
                             "raw_error": "private-prompt-SECRET"}},
    {"failure_diagnostic": {"kind": "request_size_limit", "phase": "private-phase-SECRET"}},
    {"failure_diagnostic": {"kind": "request_size_limit", "phase": "turn_start",
                             "request_bytes": True, "limit_bytes": 99}},
    {"call_timings": [timing(duration_seconds=float("nan"))]},
    {"call_timings": [timing(duration_seconds=-1)]},
    {"call_timings": [timing(completed=False)]},
    {"call_timings": [timing(stage_accepted=1)]},
    {"call_timings": [timing(extra="not allowed")]},
])
def test_invalid_diagnostics_do_not_expand_recovery_schema(updates):
    assert not valid_source_resource_shape(resources(**updates))
