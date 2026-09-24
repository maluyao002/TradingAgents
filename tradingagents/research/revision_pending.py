"""Versioned, proof-bound carryover of coverage-response errors.

The saved error is never a waiver of its issue.  A later writer may change its
prose, but only fresh coverage of the exact original obligation earns a receipt.
"""

from copy import deepcopy
from hashlib import sha256

from .review_lifecycle import compound_coverage_issues
from .revision_contracts import PINNED_CONTRACTS, PINNED_POLICIES, V4_CONTRACT
from .revision_coverage_schedule import coverage_delta_admission
from .revision_deferred import deferred_coverage_eligibility, resolve_deferred_coverage
from .storage import canonical_json, digest


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _context(entry, issue, origin_hashes):
    if (not isinstance(entry, dict) or not isinstance(issue, dict)
            or issue.get("issue_id") != entry.get("issue_id")
            or issue.get("text") != entry.get("source_issue_text")
            or issue.get("reader_coverage_required") is not entry.get(
                "source_issue_coverage_required")
            or issue.get("status") != "open" or digest(issue) != entry.get("source_issue_sha256")
            or not isinstance(origin_hashes, dict)
            or set(origin_hashes) != {"finalization_checkpoint.json", "reader_verification.json",
                                      "recovery_provenance.json", "result.json"}
            or not all(_hash(value) for value in origin_hashes.values())):
        raise ValueError("pending coverage lacks its exact proven origin issue")
    return {"eligibility": deepcopy(entry), "origin_issue": deepcopy(issue),
            "origin_artifact_hashes": dict(origin_hashes)}


def _validate_contexts(contexts, entries):
    if not isinstance(contexts, (list, tuple)) or len(contexts) != len(entries):
        raise ValueError("pending coverage context inventory differs from eligibility")
    result = []
    for context, entry in zip(contexts, entries, strict=True):
        if not isinstance(context, dict) or set(context) != {
                "eligibility", "origin_issue", "origin_artifact_hashes"}:
            raise ValueError("pending coverage context shape is invalid")
        expected = _context(entry, context["origin_issue"], context["origin_artifact_hashes"])
        if context != expected or context["eligibility"] != entry:
            raise ValueError("pending coverage context differs from its proof")
        result.append(expected)
    return tuple(result)


def _batch_issues(verification):
    open_issues = [item for item in verification["issue_lifecycle"]["issues"]
                   if item["status"] == "open"]
    issues = {item["issue_id"]: item for item in compound_coverage_issues(open_issues)}
    if len({item["issue_id"] for item in open_issues}) != len(open_issues):
        raise ValueError("duplicate source issue identities")
    return tuple(tuple(issues[identifier] for identifier in batch["issue_ids"])
                 for batch in verification["coverage_batches"])


def pending_contexts_from_source(verification, checkpoint, provenance, reader_text,
                                 *, current_artifact_hashes, origin_proof=None):
    """Validate prior receipts/partition, then return only still-open full contexts.

    ``origin_proof`` is required for a v4 source with inherited pending entries.
    It contains content already checked against the direct parent artifact hashes.
    The locator path itself has no authority and is never included in the digest.
    """
    lifecycle = verification["issue_lifecycle"]
    pending = lifecycle.get("pending_coverage", [])
    if (not isinstance(pending, (list, tuple))
            or list(pending) != provenance.get("deferred_coverage_eligibility", ())
            or digest(pending) != provenance.get("deferred_coverage_eligibility_sha256")
            or ((bool(pending) or "pending_coverage_sha256" in lifecycle)
                and digest(pending) != lifecycle.get("pending_coverage_sha256"))
            or len({item["issue_id"] for item in pending}) != len(pending)
            or len({item["source_finding_sha256"] for item in pending}) != len(pending)):
        raise ValueError("source pending ledger differs from its direct provenance")
    if sha256(reader_text.encode()).hexdigest() != verification["reader_sha256"]:
        raise ValueError("source pending reader hash differs")
    if provenance.get("reader_revision_policy") in PINNED_POLICIES:
        schedule = lifecycle.get("pinned_coverage_schedule")
        if (not isinstance(schedule, dict)
                or digest(schedule) != lifecycle.get("pinned_coverage_schedule_sha256")
                or schedule != coverage_delta_admission(schedule.get("slots", ()))
                or len(schedule["slots"]) != len(verification["coverage_batches"])):
            raise ValueError("v5 pinned coverage schedule audit differs")
        prior_index = -1
        for index, (row, batch) in enumerate(zip(
                schedule["slots"], verification["coverage_batches"], strict=True)):
            saved = checkpoint["stages"].get(batch["stage"])
            if (row["source_slot_index"] <= prior_index
                    or row["batch_index"] != index
                    or row["stage"] != batch["stage"]
                    or row["issue_ids"] != batch["issue_ids"]
                    or saved is None
                    or row["actual_payload_sha256"] != saved.get("inputs_hash")):
                raise ValueError("v5 pinned coverage stage binding differs")
            prior_index = row["source_slot_index"]

    origin_hashes = provenance.get("source_artifact_hashes")
    if pending and provenance.get("reader_revision_policy") == V4_CONTRACT.policy:
        if (not isinstance(origin_proof, dict)
                or origin_proof.get("artifact_hashes") != origin_hashes):
            raise ValueError("v4 pending carry requires its explicit bound origin")
        origin_verification = origin_proof["verification"]
        origin_checkpoint = origin_proof["checkpoint"]
        origin_reader = origin_checkpoint["candidate"]["reader_text"]
        if (origin_checkpoint["candidate"]["reader_sha256"]
                != origin_verification["reader_sha256"]
                or origin_checkpoint["candidate_review_stage"] != origin_verification["stage"]
                or {key: origin_checkpoint["candidate"][key] for key in (
                    "stage", "reader_sha256")} != provenance.get("candidate")
                or origin_checkpoint["stages"] != {
                    name: item for name, item in checkpoint["stages"].items()
                    if name in origin_checkpoint["stages"]}):
            raise ValueError("pending origin checkpoint differs from imported prefix")
        proved = deferred_coverage_eligibility(
            origin_verification, origin_checkpoint["stages"], origin_reader)
        if list(proved) != list(pending):
            raise ValueError("pending origin does not reprove exact eligibility")
        origin_issues = {item["issue_id"]: item for item in
                         origin_verification["issue_lifecycle"]["issues"]}
        contexts = tuple(_context(entry, origin_issues[entry["issue_id"]], origin_hashes)
                         for entry in pending)
    elif pending:
        if (lifecycle.get("pending_coverage_contexts")
                != provenance.get("pending_coverage_contexts")
                or lifecycle.get("pending_coverage_contexts_sha256")
                != provenance.get("pending_coverage_contexts_sha256")):
            raise ValueError("source pending context audit differs from direct provenance")
        contexts = _validate_contexts(provenance.get("pending_coverage_contexts"), pending)
        if digest(contexts) != provenance.get("pending_coverage_contexts_sha256"):
            raise ValueError("source pending context envelope hash differs")
    else:
        if provenance.get("reader_revision_policy") in PINNED_POLICIES and (
                lifecycle.get("pending_coverage_contexts") != []
                or lifecycle.get("pending_coverage_contexts_sha256")
                != provenance.get("pending_coverage_contexts_sha256")
                or provenance.get("pending_coverage_contexts") != []):
            raise ValueError("empty v5 pending context audit differs")
        contexts = ()

    receipts, failures = resolve_deferred_coverage(
        pending, source_stage=provenance["candidate_review_stage"],
        generation=provenance["revision_generation"],
        contract_sha256=provenance["revision_contract_sha256"],
        policy=provenance["reader_revision_policy"],
        reader_sha256=verification["reader_sha256"], reader_text=reader_text,
        issues=lifecycle["issues"], batches=_batch_issues(verification),
        batch_audit=verification["coverage_batches"],
        model_checkpoints=checkpoint["stages"])
    unresolved = [item["source_finding_sha256"] for item in pending
                  if item["source_finding_sha256"] not in {
                      receipt["source_finding_sha256"] for receipt in receipts}]
    terminal = {digest(item) for item in verification["review"]["findings"]}
    if (list(receipts) != lifecycle.get("deferred_coverage_receipts", [])
            or digest(receipts) != lifecycle.get("deferred_coverage_receipts_sha256")
            or unresolved != lifecycle.get("pending_coverage_unresolved", [])
            or [digest(item.model_dump(mode="json")) for item in failures] != unresolved
            or any((entry["source_finding_sha256"] in terminal)
                   != (entry["source_finding_sha256"] in unresolved) for entry in pending)):
        raise ValueError("pending receipt partition or terminal findings differ")

    carried = tuple(context for context in contexts
                    if context["eligibility"]["source_finding_sha256"] in unresolved)
    current = deferred_coverage_eligibility(verification, checkpoint["stages"], reader_text)
    source_issues = {item["issue_id"]: item for item in lifecycle["issues"]}
    local = tuple(_context(entry, source_issues[entry["issue_id"]],
                           current_artifact_hashes) for entry in current)
    combined = (*carried, *local)
    if (len({item["eligibility"]["source_finding_sha256"] for item in combined}) != len(combined)
            or len({item["eligibility"]["issue_id"] for item in combined}) != len(combined)):
        raise ValueError("ambiguous pending coverage identity")
    return combined


def project_pending_issues(issues, contexts):
    """Pin exact original obligations without importing historical decisions."""
    result = [deepcopy(item) for item in issues]
    by_id = {item["issue_id"]: item for item in result}
    if len(by_id) != len(result):
        raise ValueError("duplicate current issue identities")
    for context in contexts:
        original = context["origin_issue"]
        projected = {key: deepcopy(value) for key, value in original.items()
                     if key not in {"status", "decision"}}
        identifier = projected["issue_id"]
        if identifier in by_id:
            if canonical_json(by_id[identifier]) != canonical_json(projected):
                raise ValueError("pending issue collides with changed current context")
        else:
            result.append(projected)
            by_id[identifier] = projected
    return result


def pending_entries(contexts):
    return tuple(context["eligibility"] for context in contexts)


def prior_pending_context_lineage(provenance, prior_contracts):
    """Carry one exact context envelope for each already-owned pinned generation."""
    v5_records = [item for item in prior_contracts
                  if item["contract_sha256"] in {contract.sha256 for contract in PINNED_CONTRACTS}]
    if not v5_records:
        return ()
    if provenance.get("reader_revision_policy") not in PINNED_POLICIES:
        raise ValueError("v5 context lineage ends in a different policy")
    earlier = provenance.get("prior_pending_contexts", ())
    if (not isinstance(earlier, (list, tuple))
            or digest(earlier) != provenance.get("prior_pending_contexts_sha256")
            or len(earlier) != len(v5_records) - 1):
        raise ValueError("prior v5 pending context lineage is incomplete")
    own_contexts = provenance.get("pending_coverage_contexts")
    _validate_contexts(own_contexts, provenance["deferred_coverage_eligibility"])
    if digest(own_contexts) != provenance.get("pending_coverage_contexts_sha256"):
        raise ValueError("source v5 context envelope differs")
    own = {"generation": v5_records[-1]["generation"],
           "contract_sha256": v5_records[-1]["contract_sha256"],
           "provenance_sha256": v5_records[-1]["provenance_sha256"],
           "contexts": own_contexts, "contexts_sha256": digest(own_contexts)}
    result = (*earlier, own)
    for record, item in zip(v5_records, result, strict=True):
        if (not isinstance(item, dict) or set(item) != set(own)
                or item["generation"] != record["generation"]
                or item["contract_sha256"] != record["contract_sha256"]
                or item["provenance_sha256"] != record["provenance_sha256"]
                or not isinstance(item["contexts"], (list, tuple))
                or digest(item["contexts"]) != item["contexts_sha256"]):
            raise ValueError("prior v5 context ownership differs from provenance")
        _validate_contexts(item["contexts"], pending_entries(item["contexts"]))
    return tuple(result)
