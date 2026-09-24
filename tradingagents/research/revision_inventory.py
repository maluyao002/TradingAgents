"""Exact batch-inventory supersession for a missing/foreign coverage-ID pair.

The foreign response ID is never interpreted as an alias for the missing ID.
An eligible source batch retains all its original issue contexts, and only a
complete, independently valid fresh inventory earns one atomic receipt for
both historical response errors.
"""

import re
from collections import Counter
from copy import deepcopy
from hashlib import sha256

from .contracts import ReviewFinding
from .coverage_policy import packed_issue_context
from .reader_revision import generation_stages
from .report_review import ReaderVerification, check_dispositions, validated_disposition_ids
from .review_lifecycle import compound_coverage_issues
from .revision_correction_context import CORRECTION_CONTEXT_FIELD
from .storage import canonical_json, digest

INVENTORY_POLICY = "one_missing_one_foreign_full_batch_inventory_v1"
_ARTIFACT_NAMES = frozenset({
    "finalization_checkpoint.json", "reader_verification.json",
    "recovery_provenance.json", "result.json",
})
_DYNAMIC_ISSUE_FIELDS = frozenset({"status", "decision", "current_factual_correction_context"})


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value)


def _origin_issue(issue):
    if not isinstance(issue, dict) or issue.get("status") != "open":
        raise ValueError("inventory requires a full open origin issue")
    return deepcopy(issue)


def _projected(issue):
    return {key: deepcopy(value) for key, value in issue.items()
            if key not in _DYNAMIC_ISSUE_FIELDS}


def _entry(entry):
    if not isinstance(entry, dict) or not _hash(entry.get("envelope_sha256")):
        raise ValueError("inventory envelope is absent or malformed")
    core = {key: value for key, value in entry.items() if key != "envelope_sha256"}
    if (digest(core) != entry["envelope_sha256"]
            or core.get("kind") != INVENTORY_POLICY
            or not isinstance(core.get("source_generation"), int)
            or core["source_generation"] < 2
            or not _hash(core.get("source_contract_sha256"))
            or not _hash(core.get("source_reader_sha256"))
            or not _hash(core.get("source_terminal_review_sha256"))
            or not _hash(core.get("source_coverage_inputs_hash"))
            or not _hash(core.get("source_coverage_output_hash"))
            or set(core.get("source_artifact_hashes", ())) != _ARTIFACT_NAMES
            or not all(_hash(value) for value in core["source_artifact_hashes"].values())):
        raise ValueError("inventory envelope proof differs")
    stage_prefix = f"{generation_stages(core['source_generation'])[1]}-coverage-"
    source_stage = core.get("source_coverage_stage")
    suffix = source_stage.removeprefix(stage_prefix) if isinstance(source_stage, str) else ""
    if (not isinstance(source_stage, str) or not source_stage.startswith(stage_prefix)
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", suffix) is None):
        raise ValueError("inventory source coverage stage is noncanonical")
    expected = core.get("expected_issue_ids")
    issues = core.get("origin_issues")
    if (not isinstance(expected, list) or not expected or len(expected) != len(set(expected))
            or any(not isinstance(identifier, str) or not identifier for identifier in expected)
            or not isinstance(issues, list) or len(issues) != len(expected)
            or [item.get("issue_id") for item in issues] != expected
            or any(item.get("status") != "open" for item in issues)
            or any(CORRECTION_CONTEXT_FIELD in item for item in issues)
            or len(core.get("source_findings", ())) != 2
            or len({item.get("source_finding_sha256")
                    for item in core["source_findings"]}) != 2
            or {item.get("source_finding_sha256") for item in core["source_findings"]}
            != {digest({key: value for key, value in item.items()
                       if key != "source_finding_sha256"})
                for item in core["source_findings"]}):
        raise ValueError("inventory issue or finding ownership differs")
    observed = core.get("observed_counts")
    if (core.get("missing_issue_id") not in expected
            or core.get("foreign_issue_id") in expected
            or not isinstance(observed, list)
            or len(observed) != len(expected)
            or len({item.get("issue_id") for item in observed}) != len(observed)
            or {item.get("issue_id") for item in observed}
            != set(expected) - {core["missing_issue_id"]} | {core["foreign_issue_id"]}
            or any(item.get("count") != 1 for item in observed)
            or {item.get("message") for item in core["source_findings"]} != {
                f"Missing reader limitation disposition: {core['missing_issue_id']}",
                f"Unknown reader limitation disposition: {core['foreign_issue_id']}",
            }):
        raise ValueError("inventory response pair differs from its exact identities")
    return core


def _batch_issues(verification):
    open_issues = [item for item in verification["issue_lifecycle"]["issues"]
                   if item["status"] == "open"]
    if len({item["issue_id"] for item in open_issues}) != len(open_issues):
        raise ValueError("duplicate source issue identity")
    atomic = compound_coverage_issues(open_issues)
    by_id = {item["issue_id"]: item for item in atomic}
    if len(by_id) != len(atomic):
        raise ValueError("duplicate source atomic issue identity")
    return by_id


def _source_pair(raw, issues, reader_text):
    """Return the exact pair only when every other obligation independently passes."""
    if (not raw.reviewed_report or raw.findings or raw.contradicted_claim_ids
            or raw.supported_claim_ids):
        return None
    expected = [item["issue_id"] for item in issues]
    observed = [item.issue_id for item in raw.limitation_dispositions]
    counts = Counter(observed)
    missing = [identifier for identifier in expected if counts[identifier] == 0]
    foreign = [identifier for identifier in counts if identifier not in expected]
    if (len(missing) != 1 or len(foreign) != 1 or counts[foreign[0]] != 1
            or any(counts[identifier] != 1 for identifier in expected if identifier != missing[0])
            or len(raw.limitation_dispositions) != len(expected)):
        return None
    checked = check_dispositions(raw, issues, reader_text)
    pair = tuple(checked.findings)
    messages = {
        f"Missing reader limitation disposition: {missing[0]}",
        f"Unknown reader limitation disposition: {foreign[0]}",
    }
    if (len(pair) != 2 or {item.message for item in pair} != messages
            or any(item.code != "limitation_disposition" or item.severity != "critical"
                   or item.category != "editorial" for item in pair)
            or set(validated_disposition_ids(checked, issues, reader_text))
            != set(expected) - set(missing)):
        return None
    return missing[0], foreign[0], pair, counts


def inventory_eligibility(verification, stages, reader_text, *, source_generation,
                          source_contract_sha256, source_artifact_hashes):
    """Prove at most one exact saved missing/foreign response pair.

    ``stages`` must be the saved model-checkpoint mapping. The historical
    *input* hash is also checked against the reconstructed payload by the
    caller's exact-prefix replay before any new dispatch.
    """
    if (type(source_generation) is not int or source_generation < 2
            or not _hash(source_contract_sha256)
            or not isinstance(source_artifact_hashes, dict)
            or set(source_artifact_hashes) != _ARTIFACT_NAMES
            or not all(_hash(value) for value in source_artifact_hashes.values())
            or sha256(reader_text.encode("utf-8")).hexdigest()
            != verification.get("reader_sha256")):
        raise ValueError("inventory source identity differs")
    source_stage = generation_stages(source_generation)[1]
    if verification.get("stage") != source_stage:
        raise ValueError("inventory source generation differs")
    source_review = verification["review"]
    terminal = [ReviewFinding.model_validate(item) for item in source_review["findings"]]
    terminal_counts = Counter(digest(item.model_dump(mode="json")) for item in terminal)
    all_batches = verification["coverage_batches"]
    all_expected = [identifier for batch in all_batches for identifier in batch["issue_ids"]]
    if len(all_expected) != len(set(all_expected)):
        raise ValueError("source coverage inventory has duplicate issue IDs")
    issues_by_id = _batch_issues(verification)
    candidates = []
    for index, batch in enumerate(all_batches):
        expected = batch["issue_ids"]
        if (not expected or len(expected) != len(set(expected))
                or any(identifier not in issues_by_id for identifier in expected)
                or any("compound_parent" in issues_by_id[identifier]
                       or "coverage_component" in issues_by_id[identifier]
                       for identifier in expected)
                or batch.get("equivalent_groups") != {
                    identifier: [identifier] for identifier in expected}):
            continue
        stage = f"{source_stage}-coverage-{index}"
        saved = stages.get(stage)
        if (batch.get("stage") != stage or batch.get("reader_sha256")
                != verification["reader_sha256"] or not isinstance(saved, dict)
                or saved.get("role") != "verifier"
                or saved.get("usage", {}).get("complete") is not True
                or not _hash(saved.get("inputs_hash"))
                or digest(saved.get("output")) != saved.get("output_hash")
                or saved["output"] != batch.get("review")):
            continue
        ordered_issues = [issues_by_id[identifier] for identifier in expected]
        raw = ReaderVerification.model_validate(saved["output"])
        proved = _source_pair(raw, ordered_issues, reader_text)
        if proved is None:
            continue
        missing, foreign, pair, counts = proved
        correction_contexts = verification["issue_lifecycle"].get(
            "current_factual_correction_contexts", ())
        if (not isinstance(correction_contexts, (list, tuple))
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("issue_id"), str)
                       for item in correction_contexts)):
            raise ValueError("source factual correction context audit is malformed")
        correction_ids = {item["issue_id"] for item in correction_contexts}
        if any(identifier in correction_ids
               or CORRECTION_CONTEXT_FIELD in issues_by_id[identifier]
               for identifier in expected):
            raise ValueError("source inventory overlaps current factual correction context")
        if foreign in all_expected or any(terminal_counts[digest(item.model_dump(mode="json"))] != 1
                                       for item in pair):
            continue
        core = {
            "kind": INVENTORY_POLICY,
            "source_generation": source_generation,
            "source_contract_sha256": source_contract_sha256,
            "source_reader_sha256": verification["reader_sha256"],
            "source_terminal_review_sha256": digest(source_review),
            "source_coverage_stage": stage,
            "source_coverage_inputs_hash": saved["inputs_hash"],
            "source_coverage_output_hash": saved["output_hash"],
            "source_artifact_hashes": dict(source_artifact_hashes),
            "expected_issue_ids": list(expected),
            "origin_issues": [_origin_issue(item) for item in ordered_issues],
            "observed_counts": [{"issue_id": identifier, "count": count}
                                for identifier, count in counts.items()],
            "missing_issue_id": missing,
            "foreign_issue_id": foreign,
            "source_findings": [{"source_finding_sha256": digest(item.model_dump(mode="json")),
                                 **item.model_dump(mode="json")} for item in pair],
        }
        candidates.append({**core, "envelope_sha256": digest(core)})
    response_errors = [item for item in terminal if item.code == "limitation_disposition"
                       and item.message.startswith(("Missing reader limitation disposition:",
                                                    "Unknown reader limitation disposition:"))]
    paired = {item["source_finding_sha256"] for candidate in candidates
              for item in candidate["source_findings"]}
    if (len(candidates) > 1 or {digest(item.model_dump(mode="json"))
                               for item in response_errors} != paired):
        raise ValueError("coverage response errors lack one exact eligible batch")
    return tuple(candidates)


def assert_inventory_prefix_proof(entries, model_checkpoints, coverage_payloads):
    """Recheck exact historical input, reader, issue packet, and saved output.

    ``coverage_payloads`` maps each origin coverage stage to the *raw engine*
    payload reconstructed during imported ``call_origin``. Transport-only
    timeout/output-cap fields must already be absent; otherwise its digest does
    not match the saved input hash. Each carried entry uses its own old reader.
    """
    for entry in entries:
        core = _entry(entry)
        stage = core["source_coverage_stage"]
        saved = model_checkpoints.get(stage)
        payload = coverage_payloads.get(stage)
        if (not isinstance(saved, dict) or saved.get("role") != "verifier"
                or saved.get("inputs_hash") != core["source_coverage_inputs_hash"]
                or saved.get("output_hash") != core["source_coverage_output_hash"]
                or saved.get("usage", {}).get("complete") is not True
                or digest(saved.get("output")) != saved.get("output_hash")):
            raise ValueError("inventory prefix stage proof differs")
        if (not isinstance(payload, dict) or payload.get("stage") != stage
                or payload.get("revision_contract_sha256") != core["source_contract_sha256"]
                or digest(payload) != saved["inputs_hash"]):
            raise ValueError("inventory reconstructed coverage payload differs")
        research = payload.get("research")
        if not isinstance(research, dict):
            raise ValueError("inventory reconstructed research packet is missing")
        reader_text = research.get("rendered_reader")
        if (not isinstance(reader_text, str)
                or sha256(reader_text.encode("utf-8")).hexdigest()
                != core["source_reader_sha256"]
                or research.get("rendered_reader_sha256") != core["source_reader_sha256"]):
            raise ValueError("inventory prefix reader differs")
        packet = packed_issue_context([_projected(item) for item in core["origin_issues"]])
        if any(canonical_json(research.get(key)) != canonical_json(value)
               for key, value in packet.items()):
            raise ValueError("inventory prefix full issue packet differs")
        raw = ReaderVerification.model_validate(saved["output"])
        proved = _source_pair(raw, core["origin_issues"], reader_text)
        if proved is None:
            raise ValueError("inventory prefix validator no longer reproduces response errors")
        missing, foreign, pair, counts = proved
        if (missing != core["missing_issue_id"] or foreign != core["foreign_issue_id"]
                or [{"issue_id": identifier, "count": count} for identifier, count in counts.items()]
                != core["observed_counts"]
                or [{"source_finding_sha256": digest(item.model_dump(mode="json")),
                     **item.model_dump(mode="json")} for item in pair]
                != core["source_findings"]):
            raise ValueError("inventory prefix finding proof differs")


def project_inventory_issues(issues, entries):
    """Pin every original issue context, even when a writer omits its prose."""
    result = [deepcopy(item) for item in issues]
    by_id = {item["issue_id"]: item for item in result}
    if len(by_id) != len(result):
        raise ValueError("duplicate current issue identity")
    for entry in entries:
        core = _entry(entry)
        for original in core["origin_issues"]:
            projected = _projected(original)
            identifier = projected["issue_id"]
            if identifier in by_id:
                if canonical_json(_projected(by_id[identifier])) != canonical_json(projected):
                    raise ValueError("inventory issue collides with changed current context")
            else:
                result.append(projected)
                by_id[identifier] = projected
    return result


def inventory_finding_hashes(entries):
    """Both response findings are deferred together, never as factual repairs."""
    return frozenset(item["source_finding_sha256"] for entry in entries
                     for item in _entry(entry)["source_findings"])


def resolve_inventory(entries, *, generation, contract_sha256, reader_sha256, reader_text,
                      issues, batches, batch_audit, model_checkpoints):
    """Return one receipt or both immutable findings; never a partial receipt."""
    if len(entries) > 1:
        raise ValueError("multiple pending inventory envelopes are ambiguous")
    if not entries:
        return (), ()
    core = _entry(entries[0])
    source_findings = tuple(ReviewFinding.model_validate({
        key: value for key, value in item.items() if key != "source_finding_sha256"})
        for item in core["source_findings"])

    def failed():
        return (), source_findings

    if (type(generation) is not int or generation <= core["source_generation"]
            or not _hash(contract_sha256) or not _hash(reader_sha256)
            or sha256(reader_text.encode("utf-8")).hexdigest() != reader_sha256
            or len(batches) != len(batch_audit)):
        return failed()
    by_id = {item["issue_id"]: item for item in issues}
    if len(by_id) != len(issues):
        return failed()
    expected = core["expected_issue_ids"]
    for identifier, original in zip(expected, core["origin_issues"], strict=True):
        if (identifier not in by_id or by_id[identifier].get("status", "open") != "open"
                or canonical_json(_projected(by_id[identifier]))
                != canonical_json(_projected(original))):
            return failed()
    appearances = Counter(item["issue_id"] for batch in batches for item in batch)
    if any(appearances[identifier] != 1 for identifier in expected):
        return failed()
    receipt_rows = []
    for index, (items, audit) in enumerate(zip(batches, batch_audit, strict=True)):
        relevant = [item for item in items if item["issue_id"] in expected]
        if not relevant:
            continue
        stage = f"{generation_stages(generation)[1]}-coverage-{index}"
        checkpoint = model_checkpoints.get(stage)
        if (audit.get("stage") != stage or audit.get("reader_sha256") != reader_sha256
                or audit.get("issue_ids") != [item["issue_id"] for item in items]
                or not isinstance(checkpoint, dict) or checkpoint.get("role") != "verifier"
                or checkpoint.get("usage", {}).get("complete") is not True
                or not _hash(checkpoint.get("inputs_hash"))
                or digest(checkpoint.get("output")) != checkpoint.get("output_hash")
                or checkpoint["output"] != audit.get("review")):
            return failed()
        groups = audit.get("equivalent_groups", {})
        if groups != {item["issue_id"]: [item["issue_id"]] for item in items}:
            return failed()
        review = ReaderVerification.model_validate(audit["review"])
        checked = check_dispositions(review, items, reader_text)
        valid = set(validated_disposition_ids(checked, items, reader_text))
        if (not review.reviewed_report or review.supported_claim_ids
                or review.contradicted_claim_ids
                or Counter(value.issue_id for value in review.limitation_dispositions)
                != Counter(item["issue_id"] for item in items)
                or any(item["issue_id"] not in valid for item in relevant)
                or any(finding.severity in {"warning", "critical"}
                       for finding in checked.findings)):
            return failed()
        for item in relevant:
            decision = [value for value in review.limitation_dispositions
                        if value.issue_id == item["issue_id"]]
            if len(decision) != 1:
                return failed()
            receipt_rows.append({"issue_id": item["issue_id"], "coverage_stage": stage,
                                 "coverage_inputs_hash": checkpoint["inputs_hash"],
                                 "coverage_output_hash": checkpoint["output_hash"],
                                 "validated_disposition": decision[0].model_dump(mode="json")})
    if {row["issue_id"] for row in receipt_rows} != set(expected):
        return failed()
    receipt = {
        "kind": INVENTORY_POLICY,
        "source_envelope_sha256": entries[0]["envelope_sha256"],
        "source_finding_hashes": [item["source_finding_sha256"]
                                  for item in core["source_findings"]],
        "source_coverage_stage": core["source_coverage_stage"],
        "source_coverage_inputs_hash": core["source_coverage_inputs_hash"],
        "source_coverage_output_hash": core["source_coverage_output_hash"],
        "new_generation": generation,
        "new_contract_sha256": contract_sha256,
        "new_reader_sha256": reader_sha256,
        "expected_issue_ids": list(expected),
        "coverage": [next(row for row in receipt_rows if row["issue_id"] == identifier)
                     for identifier in expected],
    }
    return ({**receipt, "receipt_sha256": digest(receipt)},), ()
