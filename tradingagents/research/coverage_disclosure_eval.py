"""Offline controls for evaluating financial-draft disclosure coverage.

These synthetic inputs evaluate a verifier response.  They are not research
reports, source assertions, or an admission path for a reader report.
"""

from .report_review import check_dispositions, validated_disposition_ids

GENERAL_STATUS_ONLY = "general_status_only"
OPERATING_REVIEW_ONLY = "operating_review_only"
EXPLICIT_FINANCIAL_DRAFT = "explicit_financial_draft"

EVALUATION_CONTROL_HEADING = "### Review scope"
OPERATING_REVIEW_SENTENCE = (
    "The conditional operating package was reviewed only for its operating inputs "
    "and calculations."
)
FINANCIAL_DRAFT_SENTENCE = (
    "The financial schedules remain an unreviewed draft; their ingestion is not approval."
)
GENERAL_STATUS_RATIONALE = "Evaluation-only baseline: general report status is not financial-draft disclosure."
OPERATING_REVIEW_RATIONALE = "Evaluation-only control: operating-only review does not disclose financial-draft status."
FINANCIAL_DRAFT_RATIONALE = "Evaluation-only positive control: the reader explicitly discloses unreviewed financial schedules."

MISSING_DECISION = "missing decision"
DUPLICATE_DECISION = "duplicate decision"
UNREVIEWED_REPORT = "unreviewed report"
UNEXPECTED_EXPECTATION_ID = "unexpected expectation identifier"
UNEXPECTED_EXPECTATION_VALUE = "unexpected expectation value"
UNEXPECTED_DECISION = "unexpected decision"
INVALID_WITNESS = "nonexact witness"
BLOCKING_FINDING = "critical affected finding"
UNVALIDATED_COVERAGE = "reader coverage did not validate"

_ALLOWED_EXPECTED_DECISIONS = frozenset({"unresolved", "reader_covered"})


def _draft_issue(issues: list[dict]) -> dict:
    """Return the one protected issue owned by the financial-draft origin."""
    matched = [
        issue
        for issue in issues
        if any(
            origin.get("origin_id") == "case.review.draft"
            for origin in issue.get("origins", ())
            if isinstance(origin, dict)
        )
    ]
    if len(matched) != 1:
        raise ValueError("expected exactly one case.review.draft issue")
    issue = matched[0]
    if not issue.get("reader_coverage_required"):
        raise ValueError("case.review.draft issue must require reader coverage")
    return issue


def _controlled_reader(reader: str, *sentences: str) -> str:
    return reader + "\n\n" + EVALUATION_CONTROL_HEADING + "\n\n" + " ".join(sentences)


def disclosure_controls(reader: str, issues: list[dict]) -> list[dict]:
    """Create fixed offline disclosure controls without modifying issue evidence."""
    issue_id = _draft_issue(issues)["issue_id"]
    return [
        {
            "id": GENERAL_STATUS_ONLY,
            "reader": reader,
            "expected_decisions": {issue_id: "unresolved"},
            "rationale": GENERAL_STATUS_RATIONALE,
        },
        {
            "id": OPERATING_REVIEW_ONLY,
            "reader": _controlled_reader(reader, OPERATING_REVIEW_SENTENCE),
            "expected_decisions": {issue_id: "unresolved"},
            "rationale": OPERATING_REVIEW_RATIONALE,
        },
        {
            "id": EXPLICIT_FINANCIAL_DRAFT,
            "reader": _controlled_reader(
                reader, OPERATING_REVIEW_SENTENCE, FINANCIAL_DRAFT_SENTENCE
            ),
            "expected_decisions": {issue_id: "reader_covered"},
            "rationale": FINANCIAL_DRAFT_RATIONALE,
        },
    ]


def _expectation_errors(expected_decisions, protected_id: str) -> list[str]:
    if not isinstance(expected_decisions, dict):
        return [UNEXPECTED_EXPECTATION_ID]
    errors = []
    for issue_id, decision in expected_decisions.items():
        if issue_id != protected_id:
            errors.append(f"{UNEXPECTED_EXPECTATION_ID}: {issue_id}")
        if decision not in _ALLOWED_EXPECTED_DECISIONS:
            errors.append(f"{UNEXPECTED_EXPECTATION_VALUE}: {issue_id}")
    if set(expected_decisions) != {protected_id}:
        errors.append(f"{UNEXPECTED_EXPECTATION_ID}: expected only {protected_id}")
    return errors


def _supplied_spans(disposition) -> tuple[str, ...]:
    legacy = (disposition.reader_excerpt,) if disposition.reader_excerpt else ()
    return (*legacy, *disposition.reader_excerpts)


def score_disclosure_control(review, issues: list[dict], reader: str, expected_decisions) -> dict:
    """Score one synthetic control without treating it as report acceptance.

    The returned ``passed`` means only that the supplied response matched this
    offline evaluation case.  In particular, an expected unresolved disposition
    is a successful negative control, not a successful review.
    """
    protected_id = _draft_issue(issues)["issue_id"]
    expected = dict(expected_decisions) if isinstance(expected_decisions, dict) else expected_decisions
    reasons = _expectation_errors(expected_decisions, protected_id)
    dispositions = [
        item for item in review.limitation_dispositions
        if item.issue_id == protected_id
    ]
    observed = {protected_id: tuple(item.decision for item in dispositions)}
    count = len(dispositions)
    if count == 0:
        reasons.append(f"{MISSING_DECISION}: {protected_id}")
    elif count > 1:
        reasons.append(f"{DUPLICATE_DECISION}: {protected_id}")

    expected_decision = (
        expected_decisions.get(protected_id)
        if isinstance(expected_decisions, dict) else None
    )
    if count == 1 and dispositions[0].decision != expected_decision:
        reasons.append(
            f"{UNEXPECTED_DECISION}: {protected_id} expected {expected_decision}, "
            f"observed {dispositions[0].decision}"
        )
    if not review.reviewed_report:
        reasons.append(UNREVIEWED_REPORT)

    checked = check_dispositions(review, issues, reader)
    valid_ids = set(validated_disposition_ids(checked, issues, reader))
    spans = _supplied_spans(dispositions[0]) if count == 1 else ()
    if any(not span.strip() or span not in reader for span in spans):
        reasons.append(f"{INVALID_WITNESS}: {protected_id}")
    if expected_decision == "reader_covered" and protected_id not in valid_ids:
        reasons.append(f"{UNVALIDATED_COVERAGE}: {protected_id}")

    expected_unresolved_finding = (
        expected_decision == "unresolved"
        and count == 1
        and dispositions[0].decision == "unresolved"
    )
    for finding in checked.findings:
        if finding.severity != "critical" or protected_id not in finding.affected_ids:
            continue
        if expected_unresolved_finding:
            continue
        reasons.append(f"{BLOCKING_FINDING}: {protected_id}: {finding.message}")

    return {
        "expected": expected,
        "observed": observed,
        "passed": not reasons,
        "mismatch_reasons": tuple(dict.fromkeys(reasons)),
    }
