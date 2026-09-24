"""Current factual correction context for independent coverage, never closure."""

from copy import deepcopy
from hashlib import sha256

from .reader_revision import generic_review_stage
from .review_lifecycle import source_passage_witness_valid
from .storage import canonical_json, digest

CORRECTION_CONTEXT_POLICY = "current_single_issue_accepted_factual_context_v1"
CORRECTION_CONTEXT_FIELD = "current_factual_correction_context"
MAX_CORRECTION_CONTEXT_BYTES = 64_000


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value)


def attach_current_factual_corrections(
    issues, *, accepted_finding_hashes, factual_review, source_terminal_review,
    source_terminal_review_sha256, reader_text, factual_stage, factual_payload, checkpoint,
    resolution_evidence, source_text_witnesses, snapshot,
):
    """Attach bounded fresh context without changing the required issue inventory.

    The caller supplies the successful source-followup branch's hashes, not the
    raw audit's corrected labels. Exact checks are repeated defensively. This
    projection never adjudicates entailment or authorizes retirement. Coverage
    must independently inspect the whole issue, including unresolved residuals.
    """
    projected = deepcopy(list(issues))
    by_id = {item["issue_id"]: item for item in projected}
    if len(by_id) != len(projected) or any(
        CORRECTION_CONTEXT_FIELD in item for item in projected
    ):
        raise ValueError("correction context requires unique fresh issues")
    reader_hash = sha256(reader_text.encode()).hexdigest()
    factual_inputs = factual_payload.get("research", {})
    if (factual_payload.get("stage") != factual_stage
            or digest(factual_payload) != checkpoint.get("inputs_hash")
            or factual_inputs.get("rendered_reader") != reader_text
            or factual_inputs.get("rendered_reader_sha256") != reader_hash
            or factual_inputs.get("source_terminal_review") != source_terminal_review
            or factual_inputs.get("source_terminal_review_sha256") != source_terminal_review_sha256
            or factual_inputs.get("resolution_evidence") != resolution_evidence
            or factual_inputs.get("source_text_witnesses") != source_text_witnesses):
        raise ValueError("correction context differs from the exact factual input checkpoint")
    input_issues = factual_inputs.get("inherited_issues", ())
    original_by_id = {item["issue_id"]: item for item in input_issues}
    if len(original_by_id) != len(input_issues) or any(
        original_by_id.get(item["issue_id"]) != item for item in projected
    ):
        raise ValueError("correction context issue differs from factual input")
    if (not generic_review_stage(factual_stage)
            or not _hash(source_terminal_review_sha256)
            or checkpoint.get("role") != "verifier"
            or not _hash(checkpoint.get("inputs_hash"))
            or checkpoint.get("output") != factual_review
            or checkpoint.get("output_hash") != digest(factual_review)
            or checkpoint.get("usage", {}).get("complete") is not True):
        raise ValueError("correction context lacks a complete current factual checkpoint")
    if (factual_review.get("reviewed_report") is not True
            or factual_review.get("contradicted_claim_ids")
            or source_terminal_review.get("reader_sha256") == reader_hash):
        return projected
    findings = {}
    for item in source_terminal_review["findings"]:
        finding_hash = item["source_finding_sha256"]
        finding = {key: value for key, value in item.items() if key != "source_finding_sha256"}
        if finding_hash != digest(finding) or finding_hash in findings:
            raise ValueError("correction context source finding identity differs")
        findings[finding_hash] = finding
    accepted = set(accepted_finding_hashes)
    if not accepted <= set(findings):
        raise ValueError("accepted correction lacks a source finding")
    followups = factual_review.get("source_finding_followups", ())
    if len({item["source_finding_sha256"] for item in followups}) != len(followups):
        raise ValueError("duplicate current factual correction")
    records = []
    for followup in followups:
        finding_hash = followup["source_finding_sha256"]
        if finding_hash not in accepted or followup.get("disposition") != "corrected":
            continue
        finding = findings[finding_hash]
        affected = finding.get("affected_ids", ())
        if len(affected) != 1 or affected[0] not in by_id:
            continue
        issue = by_id[affected[0]]
        if any(key in issue for key in (
            "compound_obligation", "compound_parent", "coverage_component"
        )):
            continue
        if any(item.get("severity") in {"warning", "critical"} and (
            not item.get("affected_ids") or affected[0] in item["affected_ids"]
        ) for item in factual_review.get("findings", ())):
            continue
        spans = followup.get("reader_excerpts", ())
        witnesses = followup.get("witnesses", ())
        if (not spans or len(set(spans)) != len(spans)
                or any(not isinstance(span, str) or not span.strip()
                       or span not in reader_text for span in spans)
                or not witnesses
                or len({(item["reference"], item["excerpt"]) for item in witnesses}) != len(witnesses)):
            continue
        if any(not witness["excerpt"].strip() or not (
            witness["reference"] in resolution_evidence
            and witness["excerpt"] in resolution_evidence[witness["reference"]]
            or source_passage_witness_valid(
                witness["reference"], witness["excerpt"], source_text_witnesses,
                snapshot, finding_hash)
        ) for witness in witnesses):
            continue
        # Hash the original context, not other corrections added to this issue.
        original = {key: value for key, value in issue.items() if key != CORRECTION_CONTEXT_FIELD}
        record = {
            "policy": CORRECTION_CONTEXT_POLICY,
            "issue_id": issue["issue_id"], "issue_sha256": digest(original),
            "reader_sha256": reader_hash, "factual_stage": factual_stage,
            "factual_inputs_hash": checkpoint["inputs_hash"],
            "factual_output_hash": checkpoint["output_hash"],
            "source_terminal_review_sha256": source_terminal_review_sha256,
            "source_finding_sha256": finding_hash, "source_finding": deepcopy(finding),
            "accepted_followup_sha256": digest(followup),
            "accepted_followup": deepcopy(followup),
            "resolution_evidence_sha256": digest(resolution_evidence),
            "source_text_witnesses_sha256": digest(source_text_witnesses),
        }
        record["context_sha256"] = digest(record)
        records.append(record)
        issue.setdefault(CORRECTION_CONTEXT_FIELD, []).append(record)
    if len(canonical_json(records)) > MAX_CORRECTION_CONTEXT_BYTES:
        raise ValueError("current factual correction context exceeds its admission bound")
    return projected
