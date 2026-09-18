"""Lossless, bounded reader-coverage work; never infer materiality from size."""

from dataclasses import dataclass
from hashlib import sha256

from .budget import BudgetExhausted
from .contracts import ReviewFinding
from .report_review import ReaderVerification, check_dispositions
from .storage import canonical_json

MAX_COVERAGE_ITEMS = 12
MAX_COVERAGE_BYTES = 12_000


def coverage_batches(issues, *, max_items=MAX_COVERAGE_ITEMS, max_bytes=MAX_COVERAGE_BYTES):
    """Keep each original issue intact; an oversized item fails before dispatch."""
    if type(max_items) is not int or type(max_bytes) is not int or min(max_items, max_bytes) <= 0:
        raise ValueError("review bounds must be positive integers")
    ids = [item["issue_id"] for item in issues]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate limitation identifiers")
    batches, current = [], []
    for item in issues:
        if len(canonical_json([item])) > max_bytes:
            raise BudgetExhausted("limitation_item_exceeds_review_bound")
        if current and (len(current) == max_items or len(canonical_json([*current, item])) > max_bytes):
            batches.append(tuple(current))
            current = []
        current.append(item)
    if current:
        batches.append(tuple(current))
    return tuple(batches)


@dataclass(frozen=True)
class CoverageBatchResult:
    reader_sha256: str
    issues: tuple[dict, ...]
    review: ReaderVerification


def combine_coverage(main_review, results, issues, reader, reader_sha256):
    """Every raw ID gets its own judgment against identical immutable reader bytes."""
    if sha256(reader.encode("utf-8")).hexdigest() != reader_sha256:
        raise ValueError("reader bytes do not match their declared hash")
    expected = [item["issue_id"] for item in issues]
    if len(expected) != len(set(expected)):
        raise ValueError("duplicate required limitation identifiers")
    assigned = [item["issue_id"] for batch in results for item in batch.issues]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(expected):
        raise ValueError("coverage batch assignment is incomplete or duplicated")
    if any(batch.reader_sha256 != reader_sha256 for batch in results):
        raise ValueError("coverage batch belongs to a different reader")
    reviews = [check_dispositions(batch.review, batch.issues, reader) for batch in results]
    main_review = block_reader_contradictions(main_review)
    combined = ReaderVerification(
        reviewed_report=main_review.reviewed_report and all(item.reviewed_report for item in reviews),
        supported_claim_ids=main_review.supported_claim_ids,
        contradicted_claim_ids=main_review.contradicted_claim_ids,
        findings=(*main_review.findings, *(finding for review in reviews for finding in review.findings)),
        limitation_dispositions=tuple(item for review in reviews for item in review.limitation_dispositions),
    )
    # A batch cannot silently adjudicate claims without source context.
    if any(review.supported_claim_ids or review.contradicted_claim_ids for review in reviews):
        raise ValueError("coverage-only batch supplied factual claim decisions")
    return check_dispositions(combined, issues, reader)


def block_reader_contradictions(review):
    """An explicit contradictory claim decision is never neutralized by empty findings."""
    if not review.contradicted_claim_ids:
        return review
    if any(item.code == "reader_contradicted_claims"
           and item.severity == "critical"
           and item.affected_ids == review.contradicted_claim_ids for item in review.findings):
        return review
    finding = ReviewFinding(
        code="reader_contradicted_claims", severity="critical", category="research",
        message="Final reader verification contradicts claims: " + ", ".join(review.contradicted_claim_ids),
        affected_ids=review.contradicted_claim_ids,
    )
    return review.model_copy(update={"findings": (*review.findings, finding)})


def finalization_allowance(issues, *, call_timeout_seconds, reader_bytes=24_000,
                           context_bytes=48_000, language_count=1):
    """Conservative planning signal for optional work, not a spend guarantee.

    Include two complete passes (initial plus one repair), with an editor and a
    global factual review in each. Actual prompts still require normal admission;
    a future reader can be larger than this declared planning assumption.
    """
    batches = coverage_batches(issues)
    if min(context_bytes, reader_bytes, language_count, call_timeout_seconds) <= 0:
        raise ValueError("finalization planning inputs must be positive")
    coverage = sum(reader_bytes + len(canonical_json(items)) + 10_096 for items in batches)
    # Still outstanding before editing: challenge reconciliation and claim review.
    pre_editor = 2 * (context_bytes + 16_000)
    editor = context_bytes + len(canonical_json(issues)) + 16_000
    factual_review = context_bytes + reader_bytes + 16_000
    return {
        "coverage_batches_per_pass": len(batches),
        "reader_bytes_planning_assumption": reader_bytes,
        "language_count": language_count,
        "estimated_tokens": pre_editor + 2 * language_count * (coverage + editor + factual_review),
        "planned_call_seconds": (2 + 2 * language_count * (len(batches) + 2)) * call_timeout_seconds,
        "is_hard_spend_guarantee": False,
    }
