"""Select the exact witness generator bound to a numbered revision contract."""

from .review_lifecycle import source_passage_witnesses
from .revision_contracts import V3_CONTRACT, V4_CONTRACT, V6_CONTRACT, revision_contract
from .revision_source_witnesses import revision_source_passage_witnesses


def revision_witness_catalog(contract, snapshot, findings, issues, *, case_context=None):
    revision_contract(contract.policy, contract.sha256)
    if contract.source_witness_policy == V3_CONTRACT.source_witness_policy:
        return source_passage_witnesses(snapshot, findings)
    if contract.source_witness_policy == V4_CONTRACT.source_witness_policy:
        return revision_source_passage_witnesses(snapshot, findings, issues)
    if contract.source_witness_policy == V6_CONTRACT.source_witness_policy:
        from .revision_source_witnesses_v6 import (
            SOURCE_WITNESS_POLICY,
            revision_source_passage_witnesses_v6,
        )

        if contract.source_witness_policy != SOURCE_WITNESS_POLICY:
            raise ValueError("v6 source witness policy differs from its contract")
        operating = case_context.operating_scenarios if case_context is not None else None
        material = operating.model_context.get("source_material", ()) if operating is not None else ()
        return revision_source_passage_witnesses_v6(
            snapshot, findings, issues, source_material=material)
    raise ValueError("unknown numbered revision witness policy")
