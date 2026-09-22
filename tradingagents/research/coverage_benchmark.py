"""Offline packing comparison. Never consumes or produces a review attestation."""

from hashlib import sha256

from .coverage_policy import (
    coverage_batches_for_policy,
    coverage_output_envelope,
    packed_issue_context,
)
from .review_batches import compact_issue_groups, expand_coverage_context, group_equivalent_issues
from .storage import canonical_json, digest


def compare_coverage_packing(issues, reader):
    """Measure issue packets and repeated reader bytes, not full provider prompts.

    This is a deterministic workload comparison, not token telemetry, a live
    latency estimate or evidence that a larger batch preserves model quality.
    """
    originals = tuple(issues)
    groups = group_equivalent_issues(originals)  # Validate the complete inventory.
    raw_ids = [item["issue_id"] for item in originals]
    reader_bytes = len(reader.encode("utf-8"))
    policies = {}
    for policy in ("legacy-12", "packed-24"):
        batches = coverage_batches_for_policy(originals, policy)
        if [item for batch in batches for item in batch] != list(originals):
            raise ValueError("coverage packing changed original issues")
        rows = []
        for batch in batches:
            packet = packed_issue_context(batch)
            expanded = expand_coverage_context(packet)
            # Exact grouping is independently validated by the packer; compare
            # its expansion with the untouched original groups, not a summary.
            if expanded != compact_issue_groups(group_equivalent_issues(batch)):
                raise ValueError("coverage context did not round-trip exactly")
            rows.append({
                "raw_issue_count": len(batch),
                "group_count": len(group_equivalent_issues(batch)),
                "raw_issue_bytes": len(canonical_json(batch)),
                "packed_issue_packet_bytes": len(canonical_json(packet)),
                "issue_ids_sha256": digest([item["issue_id"] for item in batch]),
            })
        policies[policy] = {
            "call_count": len(batches),
            "raw_issue_count": sum(row["raw_issue_count"] for row in rows),
            "packed_issue_packet_bytes": sum(row["packed_issue_packet_bytes"] for row in rows),
            "repeated_reader_bytes": len(batches) * reader_bytes,
            "output_token_envelope": len(batches) * coverage_output_envelope(policy),
            "batches": rows,
        }
    return {
        "benchmark": "coverage-packing-v1",
        "scope": "issue_packets_and_reader_repetition_only",
        "issues_sha256": digest(originals), "issue_ids_sha256": digest(raw_ids),
        "raw_issue_count": len(originals), "exact_group_count": len(groups),
        "reader_sha256": sha256(reader.encode("utf-8")).hexdigest(),
        "reader_bytes": reader_bytes, "policies": policies,
        "live_calls": 0, "measured_tokens": None, "measured_live_latency_seconds": None,
        "full_provider_prompt_bytes": None, "quality_accepted": False,
    }
