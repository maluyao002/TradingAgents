"""Pinned v5 coverage slots and complete-boundary growth accounting.

The factual verifier may retire an issue, but its accepted source corrections
cannot create a new coverage call or move an obligation to another call.
"""

from copy import deepcopy

from .budget import BudgetExhausted
from .coverage_policy import packed_issue_context
from .reader_revision import (
    GENERIC_V5_COVERAGE_DELTA_MAX_BYTES,
    GENERIC_V5_COVERAGE_SCHEDULE_POLICY,
)
from .review_batches import MAX_COVERAGE_BYTES
from .revision_correction_context import CORRECTION_CONTEXT_FIELD
from .storage import canonical_json, digest


def pinned_coverage_slots(batches):
    """Capture the exact ordered atomic inventory admitted before factual review."""
    slots = tuple(tuple(deepcopy(item) for item in batch) for batch in batches)
    identifiers = [item["issue_id"] for batch in slots for item in batch]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("v5 pinned coverage inventory repeats an issue")
    return slots


def project_pinned_coverage(slots, surviving):
    """Prune only exact retired atoms, then decorate within their original slots."""
    by_id = {item["issue_id"]: item for item in surviving}
    if len(by_id) != len(surviving):
        raise ValueError("v5 surviving coverage inventory repeats an issue")
    original = {item["issue_id"]: item for batch in slots for item in batch}
    if set(by_id) - set(original):
        raise ValueError("v5 factual review created a new coverage issue")
    for identifier, item in by_id.items():
        undecorated = {key: value for key, value in item.items()
                       if key != CORRECTION_CONTEXT_FIELD}
        if canonical_json(undecorated) != canonical_json(original[identifier]):
            raise ValueError("v5 factual review changed a pinned coverage issue")
    projected = []
    for source_index, batch in enumerate(slots):
        items = tuple(deepcopy(by_id[item["issue_id"]]) for item in batch
                      if item["issue_id"] in by_id)
        if items:
            projected.append((source_index, items))
    if len(projected) > len(slots):
        raise ValueError("v5 coverage call count increased")
    return tuple(projected)


def decorated_packet_bytes(items):
    """Keep a separate explicit cap; no truncation or fallback rebatching."""
    packet = packed_issue_context(items)
    size = len(canonical_json(packet))
    if size > MAX_COVERAGE_BYTES + GENERIC_V5_COVERAGE_DELTA_MAX_BYTES:
        raise BudgetExhausted("v5_decorated_coverage_packet_limit")
    if any(len(canonical_json([item])) >
           MAX_COVERAGE_BYTES + GENERIC_V5_COVERAGE_DELTA_MAX_BYTES for item in items):
        raise BudgetExhausted("v5_decorated_coverage_item_limit")
    return size


def coverage_delta_admission(rows):
    """Charge positive complete-boundary deltas, never netting shrinkage.

    Each row pairs one surviving slot with its ORIGINAL pre-factual baseline.
    Both model-input and prompt byte counts are separately exact. Caller retains
    the original slot index even if empty slots were pruned and stages reindexed.
    """
    positive_input = sum(max(0, row["actual_input_bytes"] - row["base_input_bytes"])
                         for row in rows)
    positive_prompt = sum(max(0, row["actual_prompt_bytes"] - row["base_prompt_bytes"])
                          for row in rows)
    if (positive_input > GENERIC_V5_COVERAGE_DELTA_MAX_BYTES
            or positive_prompt > GENERIC_V5_COVERAGE_DELTA_MAX_BYTES):
        raise BudgetExhausted("v5_coverage_complete_wire_delta_limit")
    return {
        "policy": GENERIC_V5_COVERAGE_SCHEDULE_POLICY,
        "max_positive_delta_bytes": GENERIC_V5_COVERAGE_DELTA_MAX_BYTES,
        "positive_input_delta_bytes": positive_input,
        "positive_prompt_delta_bytes": positive_prompt,
        "slots": [dict(row) for row in rows],
        "slots_sha256": digest(rows),
    }


def reserve_global_coverage_delta(workload, *, has_slots):
    """Expose the one global allowance without fabricating per-call prompts."""
    result = deepcopy(workload)
    allowance = GENERIC_V5_COVERAGE_DELTA_MAX_BYTES if has_slots else 0
    result["base_conservative_reserve_tokens"] = result["conservative_reserve_tokens"]
    result["conservative_reserve_tokens"] += allowance
    result["v5_complete_wire_delta_allowance_bytes"] = allowance
    result["v5_future_coverage_assumed_uncached"] = True
    cap = result["hard_provider_spend_cap_tokens"]
    result["hard_provider_cap_covers_reserve"] = (
        None if cap is None else result["conservative_reserve_tokens"] <= cap)
    return result
