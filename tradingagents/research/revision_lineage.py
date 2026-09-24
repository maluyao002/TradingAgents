"""Hash-bound ownership of each imported numbered revision generation."""

from .reader_revision import generation_stages
from .revision_contracts import revision_contract
from .storage import digest


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _stage_proofs(stages, generation):
    writer, verifier = generation_stages(generation)
    names = [writer, verifier, *(name for name in stages
                                   if name.startswith(verifier + "-coverage-"))]
    if len(names) < 3 or len(names) != len(set(names)):
        raise ValueError("numbered revision ownership lacks full coverage")
    return {name: {key: stages[name][key] for key in ("role", "inputs_hash", "output_hash")}
            for name in names}


def source_revision_contracts(provenance, provenance_sha256, imported, latest):
    """Validate direct ownership and a linked cumulative map; never infer old policy."""
    if latest == 1:
        return ()
    if not isinstance(provenance, dict) or not _hash(provenance_sha256):
        raise ValueError("numbered revision lacks direct provenance")
    if provenance.get("revision_generation") != latest:
        raise ValueError("numbered revision provenance generation differs")
    policy, contract_hash = provenance.get("reader_revision_policy"), provenance.get(
        "revision_contract_sha256")
    revision_contract(policy, contract_hash)
    stages = {item.stage: {"role": item.role, "inputs_hash": item.inputs_hash,
                           "output_hash": item.output_hash} for item in imported}
    current_proofs = _stage_proofs(stages, latest)
    calls = provenance.get("current_calls")
    if (not isinstance(calls, list) or len(calls) != len(current_proofs)
            or {item.get("stage") for item in calls if isinstance(item, dict)} != set(current_proofs)
            or any(not isinstance(item, dict) or item.get("origin") != "current_live"
                   or item.get("status") != "completed" or item.get("role") != current_proofs[
                       item.get("stage", "")]["role"]
                   or item.get("inputs_hash") != current_proofs[item["stage"]]["inputs_hash"]
                   or not isinstance(item.get("usage"), dict) or item["usage"].get("complete") is not True
                   for item in calls)):
        raise ValueError("numbered revision current calls do not own exact saved stages")
    parent_hash = provenance.get("source_artifact_hashes", {}).get("recovery_provenance.json")
    if not _hash(parent_hash):
        raise ValueError("numbered revision lacks a bound parent provenance")
    prior = provenance.get("prior_revision_contracts", ())
    if not isinstance(prior, (list, tuple)) or len(prior) != latest - 2:
        raise ValueError("numbered revision cumulative contract lineage is incomplete")
    records = []
    for number, record in enumerate(prior, start=2):
        if (not isinstance(record, dict) or set(record) != {
                "generation", "policy", "contract_sha256", "provenance_sha256",
                "parent_provenance_sha256", "stage_proofs"}
                or record["generation"] != number
                or not _hash(record["provenance_sha256"])
                or not _hash(record["parent_provenance_sha256"])
                or record["stage_proofs"] != _stage_proofs(stages, number)):
            raise ValueError("numbered revision cumulative ownership differs from checkpoint")
        revision_contract(record["policy"], record["contract_sha256"])
        if records and record["parent_provenance_sha256"] != records[-1]["provenance_sha256"]:
            raise ValueError("numbered revision ancestor provenance chain is broken")
        records.append(record)
    if records and parent_hash != records[-1]["provenance_sha256"]:
        raise ValueError("numbered revision direct parent provenance differs")
    records.append({"generation": latest, "policy": policy,
                    "contract_sha256": contract_hash,
                    "provenance_sha256": provenance_sha256,
                    "parent_provenance_sha256": parent_hash,
                    "stage_proofs": current_proofs})
    return tuple(records)


def lineage_sha256(records):
    return digest(records)
