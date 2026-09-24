"""Code-owned, narrow supersession of saved coverage-response format failures.

This never retires an issue or a substantive source finding. Eligibility is
reconstructed from the saved reply and deterministic validator, then a new
coverage reply must independently pass the same obligation.
"""

from collections import Counter
from hashlib import sha256

from .contracts import ReviewFinding
from .report_review import ReaderVerification, check_dispositions, validated_disposition_ids
from .revision_contracts import V4_CONTRACT, revision_contract
from .storage import digest


def deferred_coverage_eligibility(verification, stages, reader_text):
    """Prove exact historical `audit_only_has_reader_spans` instances only."""
    review = verification["review"]
    reader_hash = verification["reader_sha256"]
    if sha256(reader_text.encode("utf-8")).hexdigest() != reader_hash:
        raise ValueError("deferred source reader hash differs")
    source_review_hash = digest(review)
    issues = {item["issue_id"]: item for item in verification["issue_lifecycle"]["issues"]}
    terminal = [ReviewFinding.model_validate(item) for item in review["findings"]]
    finding_counts = Counter(identifier for item in terminal for identifier in item.affected_ids)
    batches = verification["coverage_batches"]
    appearances = Counter(identifier for batch in batches for identifier in batch["issue_ids"])
    eligible = []
    for finding in terminal:
        if (finding.code != "limitation_disposition" or finding.severity != "critical"
                or finding.category != "editorial" or len(finding.affected_ids) != 1):
            continue
        issue_id = finding.affected_ids[0]
        issue = issues.get(issue_id)
        if (finding_counts[issue_id] != 1 or appearances[issue_id] != 1
                or issue is None or issue.get("status") != "open"
                or issue.get("reader_coverage_required") is not False
                or issue.get("resolution_protected") is not False
                or "compound_parent" in issue or "coverage_component" in issue
                or any(item.get("category") == "security" or
                       (item.get("category") == "numerical" and
                        item.get("severity") == "critical")
                       for item in issue.get("prior_findings", ()))):
            continue
        batch_index, batch = next((index, item) for index, item in enumerate(batches)
                                  if issue_id in item["issue_ids"])
        stage = batch["stage"]
        saved = stages.get(stage)
        if (stage != f"{verification['stage']}-coverage-{batch_index}"
                or not isinstance(saved, dict) or saved.get("role") != "verifier"
                or batch["reader_sha256"] != reader_hash
                or batch["review"] != saved.get("output")
                or digest(saved["output"]) != saved.get("output_hash")
                or batch.get("equivalent_groups", {}).get(issue_id) != [issue_id]
                or any(len(group) != 1 for group in batch["equivalent_groups"].values())):
            continue
        raw = ReaderVerification.model_validate(saved["output"])
        decisions = [item for item in raw.limitation_dispositions if item.issue_id == issue_id]
        if (not raw.reviewed_report or len(decisions) != 1
                or any(issue_id in item.affected_ids for item in raw.findings)):
            continue
        decision = decisions[0]
        if (not decision.decision.startswith("audit_only")
                or not (decision.reader_excerpt or decision.reader_excerpts)):
            continue
        isolated = ReaderVerification(reviewed_report=True,
                                      limitation_dispositions=(decision,))
        generated = check_dispositions(isolated, (issue,), reader_text).findings
        # Equality to an arbitrary deterministic limitation_disposition is not
        # enough: missing proposition context and protected issues have their
        # own failures and must never enter this response-format route.
        if (len(generated) != 1
                or generated[0].message != f"Audit-only disposition has reader spans: {issue_id}"
                or generated[0] != finding):
            continue
        eligible.append({
            "source_finding_sha256": digest(finding.model_dump(mode="json")),
            "source_finding": finding.model_dump(mode="json"),
            "issue_id": issue_id,
            "source_reader_sha256": reader_hash,
            "source_terminal_review_sha256": source_review_hash,
            "source_issue_sha256": digest(issue),
            "source_issue_text": issue["text"],
            "source_issue_coverage_required": issue["reader_coverage_required"],
            "source_coverage_stage": stage,
            "source_coverage_inputs_hash": saved["inputs_hash"],
            "source_coverage_output_hash": saved["output_hash"],
            "source_disposition_sha256": digest(decision.model_dump(mode="json")),
            "kind": "audit_only_has_reader_spans",
        })
    return tuple(eligible)


def resolve_deferred_coverage(pending, *, source_stage, generation, contract_sha256,
                              reader_sha256, reader_text, issues, batches, batch_audit,
                              model_checkpoints, policy=V4_CONTRACT.policy):
    """Return receipts and unchanged source findings for every unproved entry."""
    if not revision_contract(policy, contract_sha256).deferred_coverage:
        raise ValueError("deferred coverage requires its exact registered contract")
    issue_by_id = {item["issue_id"]: item for item in issues}
    receipts, still_open = [], []
    for entry in pending:
        identifier = entry["issue_id"]
        matches = [(index, items, result) for index, (items, result) in
                   enumerate(zip(batches, batch_audit, strict=True))
                   if any(item["issue_id"] == identifier for item in items)]
        if (len(matches) != 1 or identifier not in issue_by_id
                or sum(item["issue_id"] == identifier for item in issues) != 1):
            still_open.append(ReviewFinding.model_validate(entry["source_finding"]))
            continue
        index, items, result = matches[0]
        stage = result["stage"]
        checkpoint = model_checkpoints.get(stage)
        review = ReaderVerification.model_validate(result["review"])
        relevant = [item for item in review.limitation_dispositions if item.issue_id == identifier]
        issue = next(item for item in items if item["issue_id"] == identifier)
        checked = check_dispositions(review, items, reader_text)
        valid = validated_disposition_ids(checked, items, reader_text)
        if (stage != f"verify_revised_report-{generation}-coverage-{index}"
                or sha256(reader_text.encode("utf-8")).hexdigest() != reader_sha256
                or issue.get("issue_id") != identifier
                or issue.get("text") != entry["source_issue_text"]
                or issue.get("reader_coverage_required") is not entry[
                    "source_issue_coverage_required"]
                or len(relevant) != 1
                or identifier not in valid or not review.reviewed_report
                or result["reader_sha256"] != reader_sha256
                or checkpoint is None or checkpoint.get("role") != "verifier"
                or checkpoint.get("usage", {}).get("complete") is not True
                or digest(checkpoint.get("output")) != checkpoint.get("output_hash")
                or checkpoint.get("output") != result["review"]
                or any(identifier in finding.affected_ids and finding.severity in {"warning", "critical"}
                       for finding in checked.findings)):
            still_open.append(ReviewFinding.model_validate(entry["source_finding"]))
            continue
        receipt = {
            "kind": entry["kind"], "source_finding_sha256": entry["source_finding_sha256"],
            "source_terminal_review_sha256": entry["source_terminal_review_sha256"],
            "source_reader_sha256": entry["source_reader_sha256"],
            "source_coverage_stage": entry["source_coverage_stage"],
            "source_coverage_inputs_hash": entry["source_coverage_inputs_hash"],
            "source_coverage_output_hash": entry["source_coverage_output_hash"],
            "source_issue_sha256": entry["source_issue_sha256"],
            "source_issue_text": entry["source_issue_text"],
            "source_stage": source_stage, "new_generation": generation,
            "new_contract_sha256": contract_sha256, "new_reader_sha256": reader_sha256,
            "issue_id": identifier, "coverage_stage": stage,
            "coverage_inputs_hash": checkpoint["inputs_hash"],
            "coverage_output_hash": checkpoint["output_hash"],
            "validated_disposition": relevant[0].model_dump(mode="json"),
        }
        receipts.append({**receipt, "receipt_sha256": digest(receipt)})
    return tuple(receipts), tuple(still_open)
