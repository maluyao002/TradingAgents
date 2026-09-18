"""Explicit material-limitation dispositions bound to the actual reader text."""

from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, ReviewFinding
from .stages import VerificationOutput
from .storage import digest


class LimitationDisposition(Contract):
    issue_id: str
    decision: Literal["reader_covered", "audit_only_operational", "audit_only_immaterial", "unresolved"]
    rationale: str = Field(min_length=1)
    reader_excerpt: str = ""

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


def check_dispositions(review: ReaderVerification, issues, reader: str) -> ReaderVerification:
    """Require coverage, not blind acceptance of a single reviewed_report boolean.

    Materiality remains the verifier's explicit judgment, not a keyword heuristic.
    Literal excerpts provide traceable reader locations, not proof of entailment.
    """
    expected = {item["issue_id"] for item in issues}
    supplied = [item.issue_id for item in review.limitation_dispositions]
    failures = []
    if len(set(supplied)) != len(supplied) or set(supplied) != expected:
        failures.append("Every supplied limitation needs exactly one explicit disposition.")
    for item in review.limitation_dispositions:
        if item.decision == "unresolved":
            failures.append(f"Unresolved reader limitation: {item.issue_id}")
        if item.decision == "reader_covered" and (
                not item.reader_excerpt.strip() or item.reader_excerpt not in reader):
            failures.append(f"Reader coverage excerpt is absent: {item.issue_id}")
        if item.decision.startswith("audit_only") and item.reader_excerpt:
            failures.append(f"Audit-only disposition has contradictory reader excerpt: {item.issue_id}")
    findings = tuple(ReviewFinding(code="limitation_disposition", severity="critical",
                                  category="editorial", message=message) for message in failures)
    return review.model_copy(update={"findings": (*review.findings, *findings)})
