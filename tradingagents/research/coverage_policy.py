"""Opt-in lossless coverage packing; fewer calls are not a latency guarantee."""

from .budget import BudgetExhausted
from .review_batches import (
    MAX_COVERAGE_BYTES,
    compact_coverage_context,
    compact_issue_groups,
    coverage_batches,
    group_equivalent_issues,
)
from .storage import canonical_json

LEGACY_COVERAGE_POLICY = "legacy-12"
PACKED_COVERAGE_POLICY = "packed-24"


def coverage_output_envelope(policy):
    if policy == LEGACY_COVERAGE_POLICY:
        return 6_000
    if policy == PACKED_COVERAGE_POLICY:
        return 12_000
    raise ValueError("unknown coverage batching policy")


def packed_issue_context(issues):
    return compact_coverage_context(compact_issue_groups(group_equivalent_issues(issues)))


def coverage_batches_for_policy(issues, policy):
    """Preserve raw issues/order; cap packed context and raw fanout independently.

    The legacy path is byte-for-byte unchanged. The opt-in limits a call to 24
    original obligations and 12 KB of the *actual packed issue packet*, including
    shared context and alias IDs. It never truncates an issue, reader or context.
    The complete model boundary is still priced separately before dispatch.
    """
    coverage_output_envelope(policy)  # Reject unrecognized policies even for no issues.
    originals = tuple(issues)
    if policy == LEGACY_COVERAGE_POLICY:
        return coverage_batches(originals)
    # Also validates uniqueness/nonempty IDs before any batch can be dispatched.
    group_equivalent_issues(originals)
    batches, current = [], []
    for item in originals:
        # Retain the old single-obligation bound; packing is not permission to
        # smuggle one overlarge obligation into a multi-item packet.
        if (len(canonical_json([item])) > MAX_COVERAGE_BYTES
                or len(canonical_json(packed_issue_context([item]))) > MAX_COVERAGE_BYTES):
            raise BudgetExhausted("limitation_item_exceeds_review_bound")
        candidate = [*current, item]
        if current and (len(candidate) > 24
                        or len(canonical_json(packed_issue_context(candidate))) > MAX_COVERAGE_BYTES):
            batches.append(tuple(current))
            current = []
        current.append(item)
    if current:
        batches.append(tuple(current))
    return tuple(batches)
