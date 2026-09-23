"""Strict additive diagnostics shape shared by legacy recovery readers.

These records are not proof of paid usage and do not relax recovery admission.
"""

from math import isfinite

from tradingagents.codex.adapter import (
    CODEX_FAILURE_REASONS,
    valid_codex_failure_diagnostic,
)

_REQUIRED = {"usage", "elapsed_seconds", "dispatched", "by_stage"}
_DIAGNOSTICS = {"call_timings", "active_stage", "failure_reason",
                "failure_diagnostic", "failed_stage"}
_TIMING_FIELDS = {"stage", "role", "model", "effort", "usage_origin", "service_kind",
                  "completed", "stage_accepted", "duration_seconds"}


def valid_source_resource_shape(resources):
    if (not isinstance(resources, dict) or not _REQUIRED.issubset(resources)
            or resources.keys() - (_REQUIRED | _DIAGNOSTICS)):
        return False
    for key in ("active_stage", "failed_stage"):
        value = resources.get(key)
        if value is not None and (type(value) is not str or not value or len(value) > 256):
            return False
    reason = resources.get("failure_reason")
    if reason is not None and (type(reason) is not str or reason not in CODEX_FAILURE_REASONS):
        return False
    diagnostic = resources.get("failure_diagnostic")
    if diagnostic is not None and not valid_codex_failure_diagnostic(diagnostic):
        return False
    timings = resources.get("call_timings", [])
    if not isinstance(timings, list):
        return False
    for timing in timings:
        if not isinstance(timing, dict) or set(timing) != _TIMING_FIELDS:
            return False
        for key in _TIMING_FIELDS - {"duration_seconds", "completed", "stage_accepted"}:
            if type(timing[key]) is not str or not timing[key] or len(timing[key]) > 256:
                return False
        if type(timing["completed"]) is not bool or type(timing["stage_accepted"]) is not bool:
            return False
        if timing["stage_accepted"] and not timing["completed"]:
            return False
        duration = timing["duration_seconds"]
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not isfinite(duration) or duration < 0):
            return False
    return True
