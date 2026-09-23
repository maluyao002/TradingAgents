"""Deterministic Stage 3 admission for reviewed research artifacts.

This is deliberately a pure policy boundary.  The engine must calculate and
attest to the exported reader hash and limitation coverage before calling it;
this module never reads an artifact from disk and never treats a reviewer as
an override for deterministic prerequisites.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, StrictBool, ValidationError, field_validator

from .contracts import Contract, ReviewFinding, Usage
from .report_review import ReaderVerification
from .result_scope import ModelResultScope

AdmissionStatus = Literal["eligible", "blocked"]
CompletionStatus = Literal["complete", "incomplete"]
ModelStatus = Literal["conditional", "blocked", "not_assessed"]


class AdmissionDecision(Contract):
    """A fail-closed decision with machine-readable, human-safe reasons."""

    status: AdmissionStatus
    reasons: tuple[str, ...] = Field(min_length=1)

    @field_validator("reasons")
    @classmethod
    def nonblank_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("admission reasons cannot be blank")
        return value


class ModelConclusionAdmission(Contract):
    """Presentation status for independently scoped model conclusions.

    ``conditional`` is intentionally retained as conditional.  It is never
    upgraded to an accepted investment conclusion at this boundary.
    """

    status: ModelStatus
    reasons: tuple[str, ...] = Field(min_length=1)

    @field_validator("reasons")
    @classmethod
    def nonblank_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("model conclusion reasons cannot be blank")
        return value


class ModelConclusionsAdmission(Contract):
    operating_asset_value: ModelConclusionAdmission
    equity_per_share_value: ModelConclusionAdmission
    funding_assessment: ModelConclusionAdmission
    opening_date_alignment: ModelConclusionAdmission


class ReportAdmission(Contract):
    """Stage 3 outcome; this does not activate production use.

    ``assessment_status`` describes the run: incomplete runs are incomplete;
    completed runs still need a later release decision.  It is therefore not
    synonymous with ``acceptance_eligibility`` and is never ``accepted`` here.
    """

    report_completion: CompletionStatus
    qualitative_analysis: AdmissionDecision
    model_conclusions: ModelConclusionsAdmission
    acceptance_eligibility: AdmissionDecision
    assessment_status: Literal["incomplete", "needs_review"]
    production_activation: Literal[False] = False
    recommendation_status: Literal["withheld"] = "withheld"
    target_status: Literal["withheld"] = "withheld"
    operating_scenarios: ModelConclusionAdmission | None = None
    cashflow_bridge: ModelConclusionAdmission | None = None


class _VerificationAttestation(Contract):
    """The engine's structured attestation for one exact exported reader.

    Coverage is proven by matching the engine's required IDs, its validated
    IDs, and the review dispositions.  A ``reviewed_report`` boolean (or a
    coverage boolean) alone is intentionally insufficient.
    """

    exported: StrictBool
    reader_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review: ReaderVerification
    # Required even when empty: absence means the engine did not attest that it
    # evaluated limitation coverage, whereas ``()`` explicitly attests there
    # were no exact limitation IDs to cover.
    required_limitation_ids: tuple[str, ...]
    validated_limitation_ids: tuple[str, ...]

    @field_validator("required_limitation_ids", "validated_limitation_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("limitation IDs must be unique and nonblank")
        return value


def _revalidate(model, model_type):
    """Defend this boundary from ``model_construct`` and forged model copies."""
    return model_type.model_validate_json(model.model_dump_json(warnings="error"))


def _blocked(*reasons: str) -> AdmissionDecision:
    return AdmissionDecision(status="blocked", reasons=tuple(dict.fromkeys(reasons)))


def _eligible(reason: str) -> AdmissionDecision:
    return AdmissionDecision(status="eligible", reasons=(reason,))


def _completed_stop(stop_reason: str) -> bool:
    # Keep the explicit terminal reason used by the evidence-led workflow, and
    # permit a future ordinary completed terminal reason without treating a
    # failure merely containing the word "complete" as success.
    return stop_reason in {"completed", "completed_needs_review"}


def _attestation(verification: dict) -> tuple[_VerificationAttestation | None, tuple[str, ...]]:
    if not isinstance(verification, dict):
        return None, ("Reader verification is malformed.",)
    try:
        # The engine packet also retains audit-only metadata (for example,
        # stage and coverage batches).  Normalize its exact policy surface
        # rather than weakening the frozen contract with arbitrary extras.
        policy_fields = {
            "exported", "reader_sha256", "review", "required_limitation_ids",
            "validated_limitation_ids",
        }
        parsed = _VerificationAttestation.model_validate({
            name: value for name, value in verification.items() if name in policy_fields
        })
        return _revalidate(parsed, _VerificationAttestation), ()
    except (ValidationError, TypeError, ValueError):
        # ReaderVerification is nested above, so malformed review payloads are
        # rejected by its strict schema rather than treated as a reviewer pass.
        return None, ("Reader verification is malformed.",)


def _verification_failures(reader_sha256: str, attestation: _VerificationAttestation | None,
                           parse_failures: tuple[str, ...]) -> tuple[str, ...]:
    if parse_failures:
        return parse_failures
    assert attestation is not None
    failures: list[str] = []
    if not re.fullmatch(r"[a-f0-9]{64}", reader_sha256):
        failures.append("Exported reader hash is malformed.")
    if not attestation.exported:
        failures.append("Reader report was not exported.")
    if reader_sha256 != attestation.reader_sha256:
        failures.append("Exported reader hash does not match the verified reader hash.")
    if not attestation.review.reviewed_report:
        failures.append("Reader report was not reviewed.")
    required = set(attestation.required_limitation_ids)
    validated = set(attestation.validated_limitation_ids)
    dispositions = attestation.review.limitation_dispositions
    disposition_ids = [item.issue_id for item in dispositions]
    if required != validated or required != set(disposition_ids) or len(disposition_ids) != len(set(disposition_ids)):
        failures.append("Reader limitation coverage was not engine-validated for every exact issue.")
    if any(item.decision == "unresolved" for item in dispositions):
        failures.append("Reader review contains unresolved limitation dispositions.")
    if attestation.review.contradicted_claim_ids:
        failures.append("Reader review contains contradicted claims.")
    return tuple(failures)


def _model_conclusions(scope: ModelResultScope | None) -> ModelConclusionsAdmission:
    if scope is None:
        unknown = ModelConclusionAdmission(
            status="blocked", reasons=("Model result scope is unknown; output is withheld.",)
        )
        return ModelConclusionsAdmission(
            operating_asset_value=unknown, equity_per_share_value=unknown,
            funding_assessment=unknown, opening_date_alignment=unknown,
        )
    scope = _revalidate(scope, ModelResultScope)
    return ModelConclusionsAdmission(**{
        field: ModelConclusionAdmission(
            status=getattr(scope, field).status,
            reasons=getattr(scope, field).reasons,
        )
        for field in (
            "operating_asset_value", "equity_per_share_value",
            "funding_assessment", "opening_date_alignment",
        )
    })


def evaluate_admission(*, stop_reason: str, reader_exported: bool, reader_sha256: str,
                       verification: dict, usage: Usage,
                       findings: tuple[ReviewFinding, ...],
                       scope: ModelResultScope | None,
                       case_reviewed: bool = False,
                       operating_scenarios_reviewed: bool = False,
                       cashflow_bridge_reviewed: bool = False) -> ReportAdmission:
    """Evaluate Stage 3 admission from engine-owned, already-frozen evidence.

    The ``reader_exported`` argument is an independent engine attestation.  It
    must agree with the verification packet; neither can be replaced by review
    status.  Incomplete telemetry blocks acceptance only, so a verified report
    remains useful for diagnosis and human review.
    """
    if not isinstance(stop_reason, str) or not stop_reason.strip():
        raise ValueError("stop_reason must be a nonblank string")
    if (type(reader_exported) is not bool or type(case_reviewed) is not bool
            or type(operating_scenarios_reviewed) is not bool or type(cashflow_bridge_reviewed) is not bool):
        raise TypeError("reader_exported and review flags must be booleans")
    if not isinstance(reader_sha256, str):
        raise TypeError("reader_sha256 must be a string")
    if not isinstance(findings, tuple):
        raise TypeError("findings must be a tuple")

    usage = _revalidate(usage, Usage)
    try:
        checked_findings = tuple(_revalidate(item, ReviewFinding) for item in findings)
    except (AttributeError, ValidationError, TypeError, ValueError) as exc:
        raise ValueError("findings must contain valid ReviewFinding contracts") from exc
    attestation, parse_failures = _attestation(verification)
    verification_failures = list(_verification_failures(reader_sha256, attestation, parse_failures))
    if not reader_exported:
        verification_failures.append("Reader report was not exported by the engine.")

    review_findings = attestation.review.findings if attestation is not None else ()
    all_findings = (*checked_findings, *review_findings)
    material_findings = tuple(item for item in all_findings if item.severity in {"warning", "critical"})
    if material_findings:
        verification_failures.append("Reader review contains warning or critical findings.")

    completed = _completed_stop(stop_reason) and not verification_failures
    completion_reasons = (
        "Report completed with an exported, exact-hash, engine-validated reader review."
        if completed else "Report completion prerequisites failed: " + " ".join(dict.fromkeys(verification_failures or (
            "Research did not reach a completed terminal state.",
        )))
    )
    qualitative = _eligible("Qualitative analysis is substantively verified.") if completed else _blocked(completion_reasons)
    conclusions = _model_conclusions(scope)

    acceptance_failures: list[str] = []
    if not completed:
        acceptance_failures.append(completion_reasons)
    if not usage.complete:
        acceptance_failures.append("Complete usage telemetry is required for acceptance.")
    if not case_reviewed:
        acceptance_failures.append("The financial case has not been reviewed.")
    for name in (
        "operating_asset_value",
        "equity_per_share_value",
        "funding_assessment",
        "opening_date_alignment",
    ):
        component = getattr(conclusions, name)
        if component.status in {"blocked", "not_assessed"}:
            acceptance_failures.append(
                f"{name.replace('_', ' ').capitalize()} is {component.status}; "
                "full research acceptance is blocked."
            )
    acceptance = (_eligible("Report is eligible for a later release decision; activation remains withheld.")
                  if not acceptance_failures else _blocked(*acceptance_failures))
    return ReportAdmission(
        report_completion="complete" if completed else "incomplete",
        qualitative_analysis=qualitative,
        model_conclusions=conclusions,
        acceptance_eligibility=acceptance,
        # A completed report stays needs_review even when eligible: this policy
        # never supplies the later release/activation decision.
        assessment_status="needs_review" if completed else "incomplete",
        operating_scenarios=ModelConclusionAdmission(
            status="conditional" if completed and operating_scenarios_reviewed else "blocked",
            reasons=("Reviewed conditional operating scenarios; not cash flows, valuation or targets.",)
            if completed and operating_scenarios_reviewed else
            ("Operating scenarios need a source-bound independent review and a completed verified reader.",),
        ),
        cashflow_bridge=ModelConclusionAdmission(
            status="conditional" if completed and cashflow_bridge_reviewed else "blocked",
            reasons=("Separately reviewed conditional fiscal cash-flow bridge; not economic approval, "
                     "valuation, equity value or funding clearance.",)
            if completed and cashflow_bridge_reviewed else
            ("Cash-flow bridge needs a source-bound independent review and a completed verified reader.",),
        ),
    )
