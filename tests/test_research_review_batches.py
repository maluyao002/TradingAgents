from copy import deepcopy
from hashlib import sha256

import pytest

from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.report_review import ReaderVerification, limitation_packet
from tradingagents.research.review_batches import (
    CoverageBatchResult,
    block_reader_contradictions,
    combine_coverage,
    coverage_batches,
    finalization_allowance,
)
from tradingagents.research.stages import VerificationOutput


def reviewed(issues, reader):
    return ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": item["issue_id"], "decision": "reader_covered",
        "rationale": "Synthetic coverage judgment for this distinct issue.",
        "reader_excerpt": reader,
    } for item in issues])


def test_234_issues_survive_complete_bounded_coverage_with_shared_prose():
    issues = limitation_packet([f"Distinct unresolved economic issue {i}" for i in range(234)])
    reader = "Synthetic common caveat; this test does not prove semantic coverage."
    hashed = sha256(reader.encode()).hexdigest()
    batches = coverage_batches(issues)
    assert len(batches) == 20
    assert max(map(len, batches)) == 12
    assert [item for batch in batches for item in batch] == issues
    results = tuple(CoverageBatchResult(hashed, batch, reviewed(batch, reader)) for batch in batches)
    combined = combine_coverage(VerificationOutput(reviewed_report=True), results, issues, reader, hashed)
    assert combined.reviewed_report and not combined.findings
    assert len(combined.limitation_dispositions) == 234


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "unresolved", "no_excerpt"])
def test_bad_member_disposition_never_passes_via_other_members(mutation):
    issues = limitation_packet(["first", "minority issue"])
    reader = "Common paragraph"
    hashed = sha256(reader.encode()).hexdigest()
    payload = reviewed(issues, reader).model_dump(mode="json")
    if mutation == "missing":
        payload["limitation_dispositions"].pop()
    elif mutation == "duplicate":
        payload["limitation_dispositions"].append(deepcopy(payload["limitation_dispositions"][0]))
    elif mutation == "foreign":
        payload["limitation_dispositions"][1]["issue_id"] = "foreign"
    elif mutation == "unresolved":
        payload["limitation_dispositions"][1]["decision"] = "unresolved"
    else:
        payload["limitation_dispositions"][1]["reader_excerpt"] = "Not in reader"
    result = combine_coverage(VerificationOutput(reviewed_report=True), (
        CoverageBatchResult(hashed, tuple(issues), ReaderVerification.model_validate(payload)),
    ), issues, reader, hashed)
    assert any(finding.severity == "critical" for finding in result.findings)


@pytest.mark.parametrize("bad", ["changed_reader", "changed_hash", "missing_batch", "duplicate_batch", "claim_decision"])
def test_batch_identity_and_assignment_are_enforced(bad):
    reader = "Original reader"
    hashed = sha256(reader.encode()).hexdigest()
    issues = limitation_packet(["material issue"])
    review = reviewed(issues, reader)
    results = [CoverageBatchResult(hashed, tuple(issues), review)]
    if bad == "changed_reader":
        reader += " extra claim"
    elif bad == "changed_hash":
        results = [CoverageBatchResult("0" * 64, tuple(issues), review)]
    elif bad == "missing_batch":
        results = []
    elif bad == "duplicate_batch":
        results *= 2
    else:
        results = [CoverageBatchResult(hashed, tuple(issues), review.model_copy(update={"supported_claim_ids": ("c",)}))]
    with pytest.raises(ValueError):
        combine_coverage(VerificationOutput(reviewed_report=True), results, issues, reader, hashed)


def test_oversized_items_are_not_truncated_or_marked_immaterial():
    issues = limitation_packet(["é" * 8000])
    with pytest.raises(BudgetExhausted, match="review_bound"):
        coverage_batches(issues)


def test_planning_keeps_repair_and_is_not_a_spend_guarantee():
    plan = finalization_allowance(limitation_packet([str(i) for i in range(25)]), call_timeout_seconds=300)
    assert plan["coverage_batches_per_pass"] == 3
    assert plan["planned_call_seconds"] == (2 + 2 * 5) * 300
    assert plan["is_hard_spend_guarantee"] is False
    translated = finalization_allowance(limitation_packet([str(i) for i in range(25)]),
                                       call_timeout_seconds=300, language_count=2)
    assert translated["planned_call_seconds"] == (2 + 2 * 2 * 5) * 300
    assert translated["estimated_tokens"] > plan["estimated_tokens"]


@pytest.mark.parametrize("severity", ["info", "warning", "critical"])
def test_existing_finding_cannot_weaken_contradiction_and_promotion_is_idempotent(severity):
    review = VerificationOutput(reviewed_report=True, contradicted_claim_ids=("c",), findings=[{
        "code": "reader_contradicted_claims", "affected_ids": ("c",),
        "severity": severity, "message": "Provider finding with matching label.",
    }])
    promoted = block_reader_contradictions(review)
    assert any(item.severity == "critical" for item in promoted.findings)
    assert block_reader_contradictions(promoted) == promoted
