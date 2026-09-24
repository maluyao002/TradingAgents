"""Immutable numbered-revision contracts selected by policy and content hash."""

from dataclasses import dataclass

from .reader_revision import (
    GENERIC_CASHFLOW_POLICY,
    GENERIC_FOLLOWUP_REQUIREMENTS,
    GENERIC_REVISION_POLICY,
    GENERIC_REVISION_POLICY_V4,
    GENERIC_REVISION_POLICY_V5,
    GENERIC_REVISION_REQUIREMENTS,
    GENERIC_SHARED_CONTEXT_MIN_BYTES,
    GENERIC_V4_COVERAGE_REQUIREMENTS,
    GENERIC_V4_DEFERRED_POLICY,
    GENERIC_V4_FOLLOWUP_REQUIREMENTS,
    GENERIC_V4_SOURCE_WITNESS_POLICY,
    GENERIC_V4_WRITER_REQUIREMENTS,
    GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES,
    GENERIC_V5_COVERAGE_DELTA_MAX_BYTES,
    GENERIC_V5_COVERAGE_REQUIREMENTS,
    GENERIC_V5_COVERAGE_SCHEDULE_POLICY,
    GENERIC_V5_FOLLOWUP_REQUIREMENTS,
    GENERIC_V5_PENDING_POLICY,
    GENERIC_V5_WRITER_REQUIREMENTS,
)
from .storage import digest


@dataclass(frozen=True)
class RevisionContract:
    policy: str
    sha256: str
    writer_requirements: str
    factual_requirements: str
    coverage_requirements: str
    source_witness_policy: str
    deferred_coverage: bool


# Do not add keys to this dict: its digest is the already-issued v3 contract.
_V3_HASH_CONTENT = {
    "policy": GENERIC_REVISION_POLICY,
    "writer_requirements": GENERIC_REVISION_REQUIREMENTS,
    "factual_requirements": GENERIC_FOLLOWUP_REQUIREMENTS,
    "cashflow_scope": GENERIC_CASHFLOW_POLICY,
    "shared_context_min_bytes": GENERIC_SHARED_CONTEXT_MIN_BYTES,
    "cost_aware_min_shared_bytes": 128,
    "packing_policy": "original_value_row_tables_and_cost_aware_shared_v1",
    "source_witness_policy": "finding_bound_exact_eligible_source_passage_v2",
}
V3_CONTRACT = RevisionContract(
    GENERIC_REVISION_POLICY, digest(_V3_HASH_CONTENT), GENERIC_REVISION_REQUIREMENTS,
    GENERIC_FOLLOWUP_REQUIREMENTS, "", _V3_HASH_CONTENT["source_witness_policy"], False,
)
_V4_HASH_CONTENT = {
    **_V3_HASH_CONTENT,
    "policy": GENERIC_REVISION_POLICY_V4,
    "packing_policy": "exact_indexed_shared_context_v1",
    "writer_requirements": GENERIC_V4_WRITER_REQUIREMENTS,
    "factual_requirements": GENERIC_V4_FOLLOWUP_REQUIREMENTS,
    "coverage_requirements": GENERIC_V4_COVERAGE_REQUIREMENTS,
    "source_witness_policy": GENERIC_V4_SOURCE_WITNESS_POLICY,
    "deferred_coverage_policy": GENERIC_V4_DEFERRED_POLICY,
}
V4_CONTRACT = RevisionContract(
    GENERIC_REVISION_POLICY_V4, digest(_V4_HASH_CONTENT), GENERIC_V4_WRITER_REQUIREMENTS,
    GENERIC_V4_FOLLOWUP_REQUIREMENTS, GENERIC_V4_COVERAGE_REQUIREMENTS,
    GENERIC_V4_SOURCE_WITNESS_POLICY, True,
)
_V5_HASH_CONTENT = {
    **_V4_HASH_CONTENT,
    "policy": GENERIC_REVISION_POLICY_V5,
    "writer_requirements": GENERIC_V5_WRITER_REQUIREMENTS,
    "factual_requirements": GENERIC_V5_FOLLOWUP_REQUIREMENTS,
    "coverage_requirements": GENERIC_V5_COVERAGE_REQUIREMENTS,
    "pending_policy": GENERIC_V5_PENDING_POLICY,
    "issue_witness_policy": "resolution_evidence_only_field_scoped_v1",
    "source_followup_excerpt_policy": "exact_catalog_substring_v1",
    "accepted_correction_context_max_bytes": GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES,
    "accepted_correction_context_policy": "current_single_issue_accepted_factual_context_v1",
    "coverage_schedule_policy": GENERIC_V5_COVERAGE_SCHEDULE_POLICY,
    "complete_coverage_delta_max_bytes": GENERIC_V5_COVERAGE_DELTA_MAX_BYTES,
}
V5_CONTRACT = RevisionContract(
    GENERIC_REVISION_POLICY_V5, digest(_V5_HASH_CONTENT), GENERIC_V5_WRITER_REQUIREMENTS,
    GENERIC_V5_FOLLOWUP_REQUIREMENTS, GENERIC_V5_COVERAGE_REQUIREMENTS,
    GENERIC_V4_SOURCE_WITNESS_POLICY, True,
)


def revision_contract(policy: str, sha256: str) -> RevisionContract:
    """Reject unknown or mixed policy/hash pairs before any dispatch."""
    for contract in (V3_CONTRACT, V4_CONTRACT, V5_CONTRACT):
        if (policy, sha256) == (contract.policy, contract.sha256):
            return contract
    raise ValueError("unknown numbered revision contract")
