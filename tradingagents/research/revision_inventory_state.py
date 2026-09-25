"""Immutable lineage and receipt partition for v6 batch-inventory obligations."""

from .review_lifecycle import compound_coverage_issues
from .revision_contracts import INVENTORY_CONTRACTS, INVENTORY_POLICIES, revision_contract
from .revision_inventory import inventory_eligibility, inventory_finding_hashes, resolve_inventory
from .storage import digest


def _batches(verification):
    issues = compound_coverage_issues([
        item for item in verification["issue_lifecycle"]["issues"] if item["status"] == "open"
    ])
    by_id = {item["issue_id"]: item for item in issues}
    if len(by_id) != len(issues):
        raise ValueError("duplicate inventory source obligation")
    return tuple(tuple(by_id[identifier] for identifier in batch["issue_ids"])
                 for batch in verification["coverage_batches"])


def inventory_contexts_from_source(verification, checkpoint, provenance, reader_text,
                                   *, current_artifact_hashes):
    """Reprove a direct source's receipt partition before carrying any envelope."""
    contract = revision_contract(provenance["reader_revision_policy"],
                                 provenance["revision_contract_sha256"])
    lifecycle = verification["issue_lifecycle"]
    terminal = {digest(item) for item in verification["review"]["findings"]}
    if contract in INVENTORY_CONTRACTS:
        pending = provenance.get("pending_inventory")
        if (not isinstance(pending, list)
                or digest(pending) != provenance.get("pending_inventory_sha256")
                or lifecycle.get("pending_inventory") != pending
                or lifecycle.get("pending_inventory_sha256") != digest(pending)):
            raise ValueError("inventory source envelope differs from direct provenance")
        deferred_hashes = inventory_finding_hashes(pending)
        receipts, failures = resolve_inventory(
            pending, generation=provenance["revision_generation"],
            contract_sha256=contract.sha256, reader_sha256=verification["reader_sha256"],
            reader_text=reader_text, issues=lifecycle["issues"], batches=_batches(verification),
            batch_audit=verification["coverage_batches"], model_checkpoints=checkpoint["stages"],
        )
        unresolved = {digest(item.model_dump(mode="json")) for item in failures}
        if (list(receipts) != lifecycle.get("inventory_receipts")
                or digest(receipts) != lifecycle.get("inventory_receipts_sha256")
                or sorted(unresolved) != lifecycle.get("pending_inventory_unresolved")
                or deferred_hashes.intersection(terminal) != unresolved):
            raise ValueError("inventory source receipt partition differs")
        if unresolved:
            response_errors = {
                digest(item) for item in verification["review"]["findings"]
                if item["code"] == "limitation_disposition" and item["message"].startswith((
                    "Missing reader limitation disposition:", "Unknown reader limitation disposition:"
                ))
            }
            if response_errors != unresolved:
                raise ValueError("additional inventory errors require a separate reviewed policy")
            return tuple(pending)
    return inventory_eligibility(
        verification, checkpoint["stages"], reader_text,
        source_generation=provenance["revision_generation"],
        source_contract_sha256=contract.sha256, source_artifact_hashes=current_artifact_hashes,
    )


def prior_inventory_lineage(provenance, prior_contracts):
    """Carry exactly one hash-bound envelope set for every imported v6 generation."""
    inventory_hashes = {contract.sha256 for contract in INVENTORY_CONTRACTS}
    records = [item for item in prior_contracts if item["contract_sha256"] in inventory_hashes]
    if not records:
        return ()
    if provenance.get("reader_revision_policy") not in INVENTORY_POLICIES:
        raise ValueError("inventory lineage ends in a different contract")
    earlier = provenance.get("prior_pending_inventory")
    own = provenance.get("pending_inventory")
    if (not isinstance(earlier, list) or len(earlier) != len(records) - 1
            or digest(earlier) != provenance.get("prior_pending_inventory_sha256")
            or not isinstance(own, list) or digest(own) != provenance.get("pending_inventory_sha256")):
        raise ValueError("inventory lineage is incomplete")
    inventory_finding_hashes(own)
    current = {
        "generation": records[-1]["generation"],
        "contract_sha256": records[-1]["contract_sha256"],
        "provenance_sha256": records[-1]["provenance_sha256"],
        "envelopes": own, "envelopes_sha256": digest(own),
    }
    result = (*earlier, current)
    for record, entry in zip(records, result, strict=True):
        if (not isinstance(entry, dict) or set(entry) != set(current)
                or entry["generation"] != record["generation"]
                or entry["contract_sha256"] != record["contract_sha256"]
                or entry["provenance_sha256"] != record["provenance_sha256"]
                or not isinstance(entry["envelopes"], list)
                or digest(entry["envelopes"]) != entry["envelopes_sha256"]):
            raise ValueError("inventory lineage ownership differs")
        inventory_finding_hashes(entry["envelopes"])
    return tuple(result)
