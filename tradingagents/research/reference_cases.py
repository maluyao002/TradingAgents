"""Offline registration of editorial/comparator research references.

These records establish content identity and review scope only.  They do not
turn a supplied report into factual evidence, human-reviewed labels, or a
measured benchmark result.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Identifier
from .storage import read_bytes

UnknownCutoff = Literal["unknown"]
ReferenceRole = Literal["editorial_comparator"]
PendingReview = Literal["pending_human_review"]
UnselectedWindow = Literal["unselected"]


class ReferenceCase(Contract):
    """Immutable metadata for a local report retained for comparison only."""

    id: Identifier
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,19}$")
    report_basename: str = Field(min_length=1)
    reference_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_date: date
    evidence_cutoff: date | UnknownCutoff = "unknown"
    reference_role: ReferenceRole = "editorial_comparator"
    questions: tuple[str, ...] = Field(min_length=1)
    required_claim_areas: tuple[str, ...] = Field(min_length=1)
    source_backed_reference_facts: PendingReview = "pending_human_review"
    heldout_window: UnselectedWindow = "unselected"
    human_reviewed: Literal[False] = False
    quality_certified: Literal[False] = False

    @field_validator("report_basename")
    @classmethod
    def basename_only(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("report_basename must not contain a path")
        return value

    @model_validator(mode="after")
    def no_gold_by_registration(self):
        # These constants make a future manifest edit fail closed rather than
        # silently upgrading an editorial report into validated reference facts.
        if self.reference_role != "editorial_comparator":
            raise ValueError("reference reports are editorial comparators only")
        if self.source_backed_reference_facts != "pending_human_review":
            raise ValueError("reference facts require separate human review")
        if self.heldout_window != "unselected":
            raise ValueError("heldout window has not been selected")
        return self


class ReferenceCaseManifest(Contract):
    """A registration manifest, explicitly not a completed benchmark."""

    reference_cases: tuple[ReferenceCase, ...] = Field(min_length=1)
    measured_quality_baseline: Literal["pending_matched_model_runs"]
    benchmark_status: Literal["reference_registration_only"]

    @model_validator(mode="after")
    def unique_case_identity(self):
        ids = [case.id for case in self.reference_cases]
        names = [case.report_basename for case in self.reference_cases]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("reference case ids and basenames must be unique")
        return self


class ReferenceValidation(Contract):
    id: Identifier
    ticker: str
    report_basename: str
    content_identity_verified: bool
    report_after_analysis_cutoff: bool
    factual_evidence_eligible: Literal[False] = False
    gold_label_eligible: Literal[False] = False
    status: Literal["registered_for_editorial_comparison_only"] = (
        "registered_for_editorial_comparison_only"
    )


def validate_reference_files(
    manifest: ReferenceCaseManifest,
    reference_paths: tuple[Path, ...],
    *,
    analysis_cutoff: date,
) -> tuple[ReferenceValidation, ...]:
    """Hash supplied bytes and validate identity without parsing or executing HTML.

    The function intentionally has no networking, browser, upload, or artifact
    writing behavior.  A matching file is still never eligible as factual
    evidence or as a gold label; in particular, future-dated reports fail closed.
    """
    by_name = {case.report_basename: case for case in manifest.reference_cases}
    supplied = {}
    for path in reference_paths:
        name = path.name
        if name in supplied:
            raise ValueError(f"duplicate supplied reference basename: {name}")
        if name not in by_name:
            raise ValueError(f"reference basename is not registered: {name}")
        supplied[name] = path
    if set(supplied) != set(by_name):
        missing = sorted(set(by_name) - set(supplied))
        raise ValueError("missing registered reference files: " + ", ".join(missing))

    results = []
    for case in manifest.reference_cases:
        raw = read_bytes(supplied[case.report_basename])
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != case.reference_sha256:
            raise ValueError(f"reference content hash mismatch: {case.report_basename}")
        results.append(ReferenceValidation(
            id=case.id,
            ticker=case.ticker,
            report_basename=case.report_basename,
            content_identity_verified=True,
            report_after_analysis_cutoff=case.report_date > analysis_cutoff,
        ))
    return tuple(results)
