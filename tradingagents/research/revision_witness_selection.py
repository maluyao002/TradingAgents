"""Select the exact witness generator bound to a numbered revision contract."""

from .review_lifecycle import source_passage_witnesses
from .revision_contracts import V3_CONTRACT, V4_CONTRACT, revision_contract
from .revision_source_witnesses import revision_source_passage_witnesses


def revision_witness_catalog(contract, snapshot, findings, issues):
    revision_contract(contract.policy, contract.sha256)
    if contract.source_witness_policy == V3_CONTRACT.source_witness_policy:
        return source_passage_witnesses(snapshot, findings)
    if contract.source_witness_policy == V4_CONTRACT.source_witness_policy:
        return revision_source_passage_witnesses(snapshot, findings, issues)
    raise ValueError("unknown numbered revision witness policy")
