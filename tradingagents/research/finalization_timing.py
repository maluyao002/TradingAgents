"""Observed-latency admission for sequential finalization, not a time guarantee.

Cold starts remain explicitly unknown. Only measured, completed calls with the
same role/model/effort contribute; imported replies and cache hits are not free
latency observations. Provider timeouts are bounds, never duration predictions.
"""

import re
from math import isfinite


def call_family(stage):
    if not isinstance(stage, str):
        return None
    if re.fullmatch(r"verify_(?:(?:repaired|revised)_)?report-coverage-\d+", stage):
        return "coverage"
    if stage in {"verify_report", "verify_repaired_report", "verify_revised_report"}:
        return "factual"
    if stage in {"editor", "repair_report", "revise_report"}:
        return "writer"
    return None


def finalization_time_plan(calls, timings, models, *, remaining_seconds):
    """Project remaining paid work using max(last 5 samples) plus 25% headroom.

Two coverage samples are required; writer/factual estimates use one. Unknown
calls stay unknown rather than being priced at zero. An already-unaffordable
known subtotal can stop dispatch even when some durations are unknown. Cached
calls stay visible and require no new provider time. This does not skip reviews,
authorize retries, or infer that token budget alone can finish a campaign.
"""
    if (isinstance(remaining_seconds, bool) or not isinstance(remaining_seconds, (int, float))
            or not isfinite(remaining_seconds)):
        raise ValueError("remaining wall time must be finite")
    rows = []
    for call in calls:
        family = call_family(call["call_id"])
        role = "editor" if family == "writer" else "verifier"
        selection = models[role]
        model, effort = selection.model, selection.effort
        samples = []
        for sample in timings:
            if (not isinstance(sample, dict) or sample.get("usage_origin") != "current_live"
                    or sample.get("service_kind") == "replay"
                    or sample.get("completed") is not True
                    or sample.get("stage_accepted") is not True
                    or sample.get("role") != role or sample.get("model") != model
                    or sample.get("effort") != effort
                    or call_family(sample.get("stage", "")) != family):
                continue
            value = sample.get("duration_seconds")
            if (not isinstance(value, bool) and isinstance(value, (int, float))
                    and isfinite(value) and value > 0):
                samples.append(value)
        samples = samples[-5:]
        minimum = 2 if family == "coverage" else 1
        cached = call["cache_hit"]
        estimate = (0.0 if cached else max(1.0, max(samples) * 1.25)
                    if family and len(samples) >= minimum else None)
        rows.append({"call_id": call["call_id"], "family": family,
                     "cache_hit": cached, "sample_count": len(samples),
                     "estimated_seconds": estimate,
                     "timeout_seconds": call["timeout_seconds"]})
    paid = [row for row in rows if not row["cache_hit"]]
    overhead = 10.0 if paid else 0.0
    unknown = sum(row["estimated_seconds"] is None for row in paid)
    known = sum(row["estimated_seconds"] or 0 for row in paid) + overhead
    infeasible = bool(paid) and known >= remaining_seconds
    estimate = None if unknown else known
    return {
        "policy": "sequential_observed_max5_headroom25_v1",
        "remaining_seconds": remaining_seconds,
        "estimated_seconds": estimate,
        "known_estimated_subtotal_seconds": known,
        "unknown_call_count": unknown,
        "completion_overhead_seconds": overhead,
        "fits_observed_estimate": False if infeasible else None if unknown else True,
        "stop_before_dispatch": infeasible,
        "worst_case_timeout_seconds": sum(row["timeout_seconds"] for row in paid),
        "is_completion_guarantee": False,
        "calls": rows,
    }
