"""Explicit material-limitation dispositions bound to the actual reader text."""

from collections import Counter
from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, ReviewFinding
from .review_lifecycle import (
    compound_coverage_component_valid,
    compound_coverage_issues,
    compound_obligation_contract_valid,
)
from .stages import VerificationOutput
from .storage import digest


class LimitationDisposition(Contract):
    issue_id: str
    decision: Literal["reader_covered", "audit_only_operational", "audit_only_immaterial", "unresolved"]
    rationale: str = Field(min_length=1)
    # ``reader_excerpt`` is retained for persisted legacy verifier replies.  New
    # replies may provide several independent literal spans instead.
    reader_excerpt: str = ""
    reader_excerpts: tuple[str, ...] = ()

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("limitation disposition requires a rationale")
        return value


class ReaderVerification(VerificationOutput):
    limitation_dispositions: tuple[LimitationDisposition, ...] = ()


def limitation_packet(gaps):
    return [{"issue_id": "limitation-" + digest(text), "text": text}
            for text in dict.fromkeys(gaps)]


def nonmandatory_review_finding_texts(findings) -> frozenset[str]:
    """Return exact structured review findings eligible to remain audit-only."""

    eligible, nonexempt = set(), set()
    for finding in findings:
        value = finding if isinstance(finding, dict) else finding.model_dump(mode="json")
        category, severity = value.get("category"), value.get("severity")
        text = (
            f"Independent review finding [{severity}] "
            f"{value['code']}: {value['message']}"
        )
        if (
            (severity == "info" or category == "operational")
            and category != "security"
            and not (category == "numerical" and severity == "critical")
        ):
            eligible.add(text)
            continue
        nonexempt.add(text)
    return frozenset(eligible - nonexempt)


def requires_reader_coverage(issue, *, financial_prerequisite_texts=()) -> bool:
    """Select caveats that compact presentation must retain in reader prose.

    Lifecycle protection and reader visibility are separate decisions.  A
    non-retirable informational or operational record may remain in the audit;
    deterministic financial prerequisites, security issues, and critical
    numerical issues may not.
    """

    if "compound_obligation" in issue:
        if not compound_obligation_contract_valid(issue):
            return True
        return any(
            component["reader_treatment"] == "reader_required"
            for component in issue["compound_obligation"]["components"]
        )
    if issue.get("text") in frozenset(financial_prerequisite_texts):
        return True
    for finding in issue.get("prior_findings", ()):
        category = finding.get("category")
        if category == "security":
            return True
        if category == "numerical" and finding.get("severity") == "critical":
            return True
    return False


def _provided_spans(disposition: LimitationDisposition) -> tuple[str, ...]:
    """Return literal spans without joining, normalizing, or inferring any text."""
    legacy = (disposition.reader_excerpt,) if disposition.reader_excerpt else ()
    return (*legacy, *disposition.reader_excerpts)


def _cannot_determine_proposition(issue) -> bool:
    """Fail closed for incomplete enriched context while accepting legacy packets."""
    if "coverage_component" in issue:
        return not compound_coverage_component_valid(issue)
    if "compound_obligation" in issue:
        return not compound_obligation_contract_valid(issue)
    context_fields = {"claims", "missing_claim_ids", "prior_findings", "resolution_protected"}
    if not context_fields.intersection(issue):
        return False
    missing_claim_ids = issue.get("missing_claim_ids")
    return missing_claim_ids is None or bool(missing_claim_ids)


def _disposition_validation(review: ReaderVerification, issues, reader: str):
    """Return valid IDs and deterministic per-ID validation messages.

    A bad disposition must fail closed for its own issue, but must not erase a
    separate issue's independently traceable coverage.
    """
    expected_ids = [item["issue_id"] for item in issues]
    expected_counts = Counter(expected_ids)
    expected_set = set(expected_ids)
    issue_by_id = {item["issue_id"]: item for item in issues}
    by_id: dict[str, list[LimitationDisposition]] = {}
    for disposition in review.limitation_dispositions:
        by_id.setdefault(disposition.issue_id, []).append(disposition)

    failures: list[tuple[str, str]] = []
    valid_ids: list[str] = []
    for issue_id in dict.fromkeys(expected_ids):
        dispositions = by_id.get(issue_id, [])
        if expected_counts[issue_id] != 1:
            failures.append((issue_id, f"Duplicate required limitation identifier: {issue_id}"))
            continue
        if not dispositions:
            failures.append((issue_id, f"Missing reader limitation disposition: {issue_id}"))
            continue
        if len(dispositions) != 1:
            failures.append((issue_id, f"Duplicate reader limitation disposition: {issue_id}"))
            continue

        disposition = dispositions[0]
        spans = _provided_spans(disposition)
        invalid_span = any(not span.strip() or span not in reader for span in spans)
        if disposition.decision == "unresolved":
            failures.append((issue_id, f"Unresolved reader limitation: {issue_id}"))
            if invalid_span:
                failures.append((issue_id, f"Reader span is not an exact substring: {issue_id}"))
            continue
        if _cannot_determine_proposition(issue_by_id[issue_id]):
            failures.append((issue_id, f"Cannot determine limitation proposition: {issue_id}"))
            continue
        if disposition.decision.startswith("audit_only"):
            if issue_by_id[issue_id].get("reader_coverage_required"):
                failures.append((issue_id, f"Protected limitation requires reader coverage: {issue_id}"))
            elif (
                issue_by_id[issue_id].get("coverage_component", {}).get("reader_treatment")
                == "audit_only_procedural"
                and disposition.decision != "audit_only_operational"
            ):
                failures.append((issue_id, f"Procedural limitation must remain operational audit: {issue_id}"))
            elif spans:
                failures.append((issue_id, f"Audit-only disposition has reader spans: {issue_id}"))
            else:
                valid_ids.append(issue_id)
            continue
        if not spans:
            failures.append((issue_id, f"Reader coverage needs an exact span: {issue_id}"))
            continue
        if invalid_span:
            failures.append((issue_id, f"Reader span is not an exact substring: {issue_id}"))
            continue
        valid_ids.append(issue_id)

    for issue_id in by_id:
        if issue_id not in expected_set:
            failures.append((issue_id, f"Unknown reader limitation disposition: {issue_id}"))
    return tuple(valid_ids), tuple(failures)


def validated_disposition_ids(review: ReaderVerification, issues, reader: str) -> tuple[str, ...]:
    """Return independently valid limitation-disposition IDs.

    An unreviewed report does not attest to any coverage, even if it includes
    otherwise well-formed disposition data.
    """
    if not review.reviewed_report:
        return ()
    valid_ids, _ = _disposition_validation(review, issues, reader)
    blocked_ids = {
        identifier
        for finding in review.findings
        if finding.severity in {"warning", "critical"}
        for identifier in finding.affected_ids
    }
    return tuple(issue_id for issue_id in valid_ids if issue_id not in blocked_ids)


def check_dispositions(review: ReaderVerification, issues, reader: str) -> ReaderVerification:
    """Require literal coverage, not blind acceptance of a reviewed-report boolean.

    Materiality remains the verifier's explicit judgment, not a keyword heuristic.
    Literal spans provide traceable reader locations, not proof of entailment.
    """
    _, failures = _disposition_validation(review, issues, reader)
    generated = tuple(
        ReviewFinding(
            code="limitation_disposition",
            severity="critical",
            category="editorial",
            message=message,
            affected_ids=(issue_id,),
        )
        for issue_id, message in failures
    )
    # Do not remove or rewrite provider findings.  Exact matching keeps repeated
    # deterministic validation (including batch then combined validation) stable.
    additions = tuple(finding for finding in generated if finding not in review.findings)
    return review.model_copy(update={"findings": (*review.findings, *additions)})


def fan_in_compound_dispositions(review, issues, reader: str) -> ReaderVerification:
    """Map independently validated child decisions back to exact parent IDs.

    The raw child review should be retained by the caller as batch audit. This
    adapter does not infer semantic coverage: it emits a parent disposition only
    after every non-satisfied component has independently passed the ordinary
    exact-span/materiality checks. Child findings are rebound to their immutable
    parent so they cannot disappear during parent-level admission.
    """
    review = ReaderVerification.model_validate(review.model_dump(mode="json"))
    parents = tuple(issues)
    atomic = tuple(compound_coverage_issues(parents))
    checked = check_dispositions(review, atomic, reader)
    valid_ids = set(validated_disposition_ids(checked, atomic, reader))
    disposition_counts = Counter(
        disposition.issue_id for disposition in checked.limitation_dispositions
    )
    disposition_by_id = {
        disposition.issue_id: disposition
        for disposition in checked.limitation_dispositions
    }

    child_to_parent = {
        item["issue_id"]: item["compound_parent"]["issue_id"]
        for item in atomic if "compound_parent" in item
    }
    parent_dispositions = []
    for parent in parents:
        if not compound_obligation_contract_valid(parent):
            candidates = [
                disposition for disposition in checked.limitation_dispositions
                if disposition.issue_id == parent["issue_id"]
            ]
            parent_dispositions.extend(candidates)
            continue

        children = [
            item for item in atomic
            if item.get("compound_parent", {}).get("issue_id") == parent["issue_id"]
        ]
        child_ids = [item["issue_id"] for item in children]
        if (
            not child_ids
            or any(disposition_counts[identifier] != 1 for identifier in child_ids)
            or not set(child_ids) <= valid_ids
        ):
            continue
        spans = []
        rationales = []
        for child in children:
            disposition = disposition_by_id[child["issue_id"]]
            rationales.append(disposition.rationale)
            if child["reader_coverage_required"]:
                spans.extend(_provided_spans(disposition))
        spans = list(dict.fromkeys(spans))
        if any(child["reader_coverage_required"] for child in children) and not spans:
            continue
        parent_dispositions.append(LimitationDisposition(
            issue_id=parent["issue_id"],
            decision="reader_covered" if spans else "audit_only_operational",
            rationale=(
                "Every code-owned current component passed independently: "
                + " | ".join(rationales)
            ),
            reader_excerpts=tuple(spans),
        ))

    findings = tuple(finding.model_copy(update={
        "affected_ids": tuple(dict.fromkeys(
            child_to_parent.get(identifier, identifier)
            for identifier in finding.affected_ids
        )),
    }) for finding in checked.findings)
    parent_review = ReaderVerification(
        reviewed_report=checked.reviewed_report,
        supported_claim_ids=checked.supported_claim_ids,
        contradicted_claim_ids=checked.contradicted_claim_ids,
        findings=findings,
        limitation_dispositions=tuple(parent_dispositions),
    )
    return check_dispositions(parent_review, parents, reader)
