"""Strict Stage 3 ingestion boundary for evidence-bound financial cases.

This module accepts schedules and independent review metadata, not a valuation.
Stage 2 schedules are not linked to the assumptions of a forecast model, so the
effective output scope remains blocked even when deterministic reconciliation
finds an operating schedule conditionally eligible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    Contract,
    EvidenceSnapshot,
    FinancialFact,
    Identifier,
    ResearchRequest,
    ReviewFinding,
)
from .financial_case import (
    FinancialCase,
    FinancialReconciliation,
    evidence_snapshot_sha256,
    reconcile_financial_case,
)
from .operating_scenarios import (
    OperatingScenarioPackage,
    OperatingScenarioResult,
    evaluate_operating_scenarios,
)
from .result_scope import ComponentEligibility, ModelResultScope
from .storage import canonical_json, digest, parse_json

_MODEL_LINKAGE_LIMITATION = (
    "Financial-case schedules are not bound to reviewed forecast assumptions or to the inputs "
    "of a valuation model; all valuation-derived numerical outputs remain unavailable pending "
    "reviewed model-bound linkage."
)
_DRAFT_LIMITATION = (
    "The financial case is an unreviewed Stage 2 draft; draft ingestion is not approval."
)
_MAX_SOURCE_CHARS = 6_000
_MAX_TOTAL_SOURCE_CHARS = 100_000
_PASSAGE_RADIUS = 700


class FinancialCaseReview(Contract):
    """Independent review metadata bound to exact canonical case and snapshot values.

    ``reviewed`` records completion of a review.  It is deliberately not named
    ``approved`` and cannot change deterministic reconciliation or output scope.
    """

    status: Literal["draft", "reviewed"]
    reviewer_id: Identifier
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_passages_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    findings: tuple[ReviewFinding, ...] = ()
    limitations: tuple[str, ...] = ()

    @field_validator("limitations")
    @classmethod
    def nonblank_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("review limitations cannot be blank")
        return value


class CaseSourcePassage(Contract):
    """An exact bounded slice of frozen source text; no verification claim is implied."""

    source_id: Identifier
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)
    fact_ids: tuple[Identifier, ...] = Field(min_length=1)
    location_hints: tuple[str, ...] = Field(min_length=1)
    selection_basis: Literal[
        "authored_exact",
        "full_source",
        "exact_location_match",
        "metric_match",
        "bounded_fallback",
    ]

    @model_validator(mode="after")
    def exact_span(self):
        if self.end < self.start or self.end - self.start != len(self.text):
            raise ValueError("source passage span differs from exact text")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("source passage fact IDs must be unique")
        if any(not hint.strip() for hint in self.location_hints):
            raise ValueError("source passage location hints cannot be blank")
        return self


class CaseSourceMaterial(Contract):
    """Provenance and exact text retained for facts selected by the case."""

    source_id: Identifier
    url: str
    title: str
    publisher: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_char_count: int = Field(ge=0)
    fact_ids: tuple[Identifier, ...] = Field(min_length=1)
    passages: tuple[CaseSourcePassage, ...]


class FinancialCaseEnvelope(Contract):
    """The only accepted serialized Stage 3 input shape."""

    case: FinancialCase
    review: FinancialCaseReview | None = None
    source_passages: tuple[CaseSourcePassage, ...] = ()
    operating_scenarios: OperatingScenarioPackage | None = None


@dataclass(frozen=True)
class CaseContext:
    """Validated case material and the conservative scope delivered to models."""

    case: FinancialCase
    review: FinancialCaseReview | None
    reconciliation: FinancialReconciliation
    selected_facts: tuple[FinancialFact, ...]
    source_passages: tuple[CaseSourcePassage, ...]
    source_material: tuple[CaseSourceMaterial, ...]
    limitations: tuple[str, ...]
    artifacts: dict[str, bytes]
    scope: ModelResultScope
    reviewed: bool
    operating_scenarios: OperatingScenarioResult | None = None

    def model_context(self) -> dict:
        """Return JSON-native schedules, evidence, review, limitations, and scope."""

        return {
            "schema_version": 1,
            "context_kind": "financial_case_reader",
            "review_status": "reviewed" if self.reviewed else "draft_unreviewed",
            "reviewed": self.reviewed,
            "review_meaning": "independent_review_completed_not_approval",
            "financial_case": self.case.model_dump(mode="json"),
            "financial_case_review": (
                self.review.model_dump(mode="json") if self.review is not None else None
            ),
            "selected_financial_facts": [
                fact.model_dump(mode="json") for fact in self.selected_facts
            ],
            "supplemental_source_material": {
                "classification": "exact_selected_text_not_semantic_verification",
                "source_passages_sha256": (
                    source_passages_sha256(self.source_passages) if self.source_passages else None
                ),
                "passages": [item.model_dump(mode="json") for item in self.source_passages],
            },
            "source_material": [item.model_dump(mode="json") for item in self.source_material],
            "financial_reconciliation": self.reconciliation.model_dump(mode="json"),
            "output_scope": self.scope.model_dump(mode="json"),
            "limitations": list(self.limitations),
            **({"operating_scenarios": self.operating_scenarios.model_context}
               if self.operating_scenarios is not None else {}),
        }


def _checked_request(request: ResearchRequest) -> ResearchRequest:
    if not isinstance(request, ResearchRequest):
        raise TypeError("request must be a ResearchRequest")
    return ResearchRequest.model_validate_json(request.model_dump_json(warnings="error"))


def _checked_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    if not isinstance(snapshot, EvidenceSnapshot):
        raise TypeError("snapshot must be an EvidenceSnapshot")
    return EvidenceSnapshot.model_validate_json(snapshot.model_dump_json(warnings="error"))


def _selected_facts(case: FinancialCase, snapshot: EvidenceSnapshot) -> tuple[FinancialFact, ...]:
    by_id = {fact.id: fact for fact in snapshot.facts}
    selected = {
        component.fact_id for schedule in case.schedules for component in schedule.components
    } | {item.fact_id for item in case.commitments.items}
    pending = list(selected)
    while pending:
        fact_id = pending.pop()
        fact = by_id[fact_id]
        for parent_id in fact.inputs:
            if parent_id not in selected:
                selected.add(parent_id)
                pending.append(parent_id)
    return tuple(fact for fact in snapshot.facts if fact.id in selected)


def source_passages_sha256(passages: tuple[CaseSourcePassage, ...]) -> str:
    """Hash the ordered canonical supplemental passage tuple."""

    return digest([passage.model_dump(mode="json") for passage in passages])


def _checked_source_passages(
    passages: tuple[CaseSourcePassage, ...],
    facts: tuple[FinancialFact, ...],
    snapshot: EvidenceSnapshot,
) -> tuple[CaseSourcePassage, ...]:
    selected = {fact.id: fact for fact in facts}
    sources = {source.id: source for source in snapshot.sources}
    total_chars = sum(len(passage.text) for passage in passages)
    if total_chars > _MAX_TOTAL_SOURCE_CHARS:
        raise ValueError("supplemental source passages exceed the bounded text allowance")
    identities = [
        (passage.source_id, passage.start, passage.end, passage.fact_ids) for passage in passages
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("supplemental source passages must be unique")
    for passage in passages:
        if passage.selection_basis != "authored_exact":
            raise ValueError("supplied source passages must use authored_exact selection")
        source = sources.get(passage.source_id)
        if source is None:
            raise ValueError("supplemental source passage references an unknown source")
        if (
            source.availability != "full_text"
            or source.published_at is None
            or source.published_at > snapshot.cutoff
            or source.content_sha256 != passage.source_sha256
            or hashlib.sha256(source.content.encode("utf-8")).hexdigest() != passage.source_sha256
            or passage.end > len(source.content)
            or source.content[passage.start : passage.end] != passage.text
        ):
            raise ValueError("supplemental source passage differs from eligible frozen text")
        for fact_id in passage.fact_ids:
            fact = selected.get(fact_id)
            if fact is None or fact.source_id != passage.source_id:
                raise ValueError(
                    "supplemental source passage fact IDs must be selected from the same source"
                )
    return passages


def _match_span(content: str, fact: FinancialFact) -> tuple[int, int, str]:
    lowered = content.casefold()
    for needle, basis in (
        (fact.location, "exact_location_match"),
        (fact.metric, "metric_match"),
    ):
        position = lowered.find(needle.casefold()) if needle else -1
        if position >= 0:
            start = max(0, position - _PASSAGE_RADIUS)
            end = min(len(content), position + len(needle) + _PASSAGE_RADIUS)
            return start, end, basis
    return 0, min(len(content), _PASSAGE_RADIUS * 2), "bounded_fallback"


def _source_material(
    facts: tuple[FinancialFact, ...],
    snapshot: EvidenceSnapshot,
    supplied: tuple[CaseSourcePassage, ...],
) -> tuple[tuple[CaseSourceMaterial, ...], tuple[str, ...]]:
    facts_by_source: dict[str, list[FinancialFact]] = {}
    for fact in facts:
        facts_by_source.setdefault(fact.source_id, []).append(fact)
    sources = {source.id: source for source in snapshot.sources}
    supplied_by_source: dict[str, list[CaseSourcePassage]] = {}
    for passage in supplied:
        supplied_by_source.setdefault(passage.source_id, []).append(passage)
    remaining = _MAX_TOTAL_SOURCE_CHARS - sum(len(passage.text) for passage in supplied)
    materials: list[CaseSourceMaterial] = []
    limitations: list[str] = []
    for source_id, source_facts in facts_by_source.items():
        source = sources[source_id]
        passages = list(supplied_by_source.get(source_id, ()))
        covered_ids = {fact_id for passage in passages for fact_id in passage.fact_ids}
        uncovered_facts = [fact for fact in source_facts if fact.id not in covered_ids]
        source_budget = min(_MAX_SOURCE_CHARS, remaining)
        if not uncovered_facts:
            pass
        elif not source.content:
            limitations.extend(
                f"No source text is available for selected fact {fact.id}."
                for fact in uncovered_facts
            )
        elif not passages and len(source.content) <= source_budget:
            passages.append(
                CaseSourcePassage(
                    source_id=source.id,
                    source_sha256=source.content_sha256,
                    start=0,
                    end=len(source.content),
                    text=source.content,
                    fact_ids=tuple(fact.id for fact in uncovered_facts),
                    location_hints=tuple(fact.location for fact in uncovered_facts),
                    selection_basis="full_source",
                )
            )
            source_budget -= len(source.content)
            remaining -= len(source.content)
        else:
            for fact in uncovered_facts:
                if source_budget <= 0:
                    break
                start, end, basis = _match_span(source.content, fact)
                end = min(end, start + source_budget)
                text = source.content[start:end]
                if not text:
                    limitations.append(
                        f"No source text could be retained for selected fact {fact.id}."
                    )
                    continue
                passages.append(
                    CaseSourcePassage(
                        source_id=source.id,
                        source_sha256=source.content_sha256,
                        start=start,
                        end=end,
                        text=text,
                        fact_ids=(fact.id,),
                        location_hints=(fact.location,),
                        selection_basis=basis,
                    )
                )
                used = len(text)
                source_budget -= used
                remaining -= used
                if basis == "bounded_fallback":
                    limitations.append(
                        f"Selected fact {fact.id} retains an exact fallback excerpt because its "
                        "location and metric text were not found verbatim in source {source.id}."
                    )
            retained_ids = {fact_id for passage in passages for fact_id in passage.fact_ids}
            for fact in uncovered_facts:
                if fact.id not in retained_ids:
                    limitations.append(
                        f"The bounded source-material budget omitted text for selected fact {fact.id}."
                    )
        materials.append(
            CaseSourceMaterial(
                source_id=source.id,
                url=source.url,
                title=source.title,
                publisher=source.publisher,
                content_sha256=source.content_sha256,
                source_char_count=len(source.content),
                fact_ids=tuple(fact.id for fact in source_facts),
                passages=tuple(passages),
            )
        )
    return tuple(materials), tuple(limitations)


def _blocked_scope(reconciliation: FinancialReconciliation) -> ModelResultScope:
    linkage = _MODEL_LINKAGE_LIMITATION

    def blocked(component: ComponentEligibility) -> ComponentEligibility:
        reasons = tuple(dict.fromkeys((*component.reasons, linkage)))
        return ComponentEligibility(
            status="blocked", reasons=reasons, evidence_ids=component.evidence_ids
        )

    deterministic = reconciliation.output_eligibility
    return ModelResultScope(
        operating_asset_value=blocked(deterministic.operating_asset_value),
        equity_per_share_value=blocked(deterministic.equity_per_share_value),
        funding_assessment=blocked(deterministic.funding_assessment),
        opening_date_alignment=blocked(deterministic.opening_date_alignment),
    )


def _limitations(
    case: FinancialCase,
    review: FinancialCaseReview | None,
    reconciliation: FinancialReconciliation,
    source_limitations: tuple[str, ...],
) -> tuple[str, ...]:
    values = [_MODEL_LINKAGE_LIMITATION]
    if review is None or review.status == "draft":
        values.append(_DRAFT_LIMITATION)
    if review is not None:
        values.extend(review.limitations)
        values.extend(
            f"Independent review finding [{finding.severity}] {finding.code}: {finding.message}"
            for finding in review.findings
        )
    values.extend(f"Open financial-case gap {gap.id}: {gap.description}" for gap in case.gaps)
    for name in (
        "operating_asset_value",
        "equity_per_share_value",
        "funding_assessment",
        "opening_date_alignment",
    ):
        component = getattr(reconciliation.output_eligibility, name)
        if component.status == "blocked":
            values.extend(component.reasons)
    values.extend(source_limitations)
    return tuple(dict.fromkeys(values))


def load_case_context(
    content: bytes, request: ResearchRequest, snapshot: EvidenceSnapshot
) -> CaseContext:
    """Parse, revalidate, bind, and conservatively expose a financial case.

    The caller supplies the exact bytes already frozen into request identity.
    This function never reads the request path and never trusts serialized
    reconciliation or review claims to alter deterministic blockers.
    """

    if type(content) is not bytes:
        raise TypeError("financial-case content must be bytes")
    checked_request = _checked_request(request)
    checked_snapshot = _checked_snapshot(snapshot)
    if checked_request.financial_case_path is None:
        raise ValueError("financial-case ingestion requires an opted-in request path")
    if (
        checked_request.quality_revision != "evidence-led-bounded"
        or checked_request.valuation_method != "fcff"
    ):
        raise ValueError("financial-case ingestion requires the bounded evidence-led FCFF workflow")
    if (
        checked_snapshot.ticker != checked_request.ticker
        or checked_snapshot.cutoff != checked_request.cutoff
    ):
        raise ValueError("frozen snapshot identity differs from the research request")

    envelope = FinancialCaseEnvelope.model_validate(parse_json(content))
    case = envelope.case
    if (
        case.ticker != checked_request.ticker
        or case.cutoff != checked_request.cutoff
        or case.timezone != checked_request.timezone
    ):
        raise ValueError("financial case ticker, cutoff, or timezone differs from the request")

    reconciliation = reconcile_financial_case(case, checked_snapshot)
    case_hash = digest(case.model_dump(mode="json"))
    snapshot_hash = evidence_snapshot_sha256(checked_snapshot)
    if reconciliation.case_sha256 != case_hash or reconciliation.snapshot_sha256 != snapshot_hash:
        raise ValueError("recomputed financial reconciliation identity mismatch")
    review = envelope.review
    if review is not None and (
        review.case_sha256 != case_hash or review.snapshot_sha256 != snapshot_hash
    ):
        raise ValueError("financial-case review hash mismatch")

    selected_facts = _selected_facts(case, checked_snapshot)
    source_passages = _checked_source_passages(
        envelope.source_passages, selected_facts, checked_snapshot
    )
    passages_hash = source_passages_sha256(source_passages) if source_passages else None
    if review is not None and review.source_passages_sha256 != passages_hash:
        raise ValueError("financial-case review supplemental source-passages hash mismatch")
    source_material, source_limitations = _source_material(
        selected_facts, checked_snapshot, source_passages
    )
    scope = _blocked_scope(reconciliation)
    limitations = _limitations(case, review, reconciliation, source_limitations)
    reviewed = review is not None and review.status == "reviewed"
    operating = (evaluate_operating_scenarios(envelope.operating_scenarios, case, checked_snapshot)
                 if envelope.operating_scenarios is not None else None)
    if operating is not None:
        limitations = tuple(dict.fromkeys((*limitations, *operating.limitations)))

    provisional = CaseContext(
        case=case,
        review=review,
        reconciliation=reconciliation,
        selected_facts=selected_facts,
        source_passages=source_passages,
        source_material=source_material,
        limitations=limitations,
        artifacts={},
        scope=scope,
        reviewed=reviewed,
        operating_scenarios=operating,
    )
    artifacts = {
        "financial_case.json": canonical_json(case),
        "financial_reconciliation.json": canonical_json(reconciliation),
        "case_context.json": canonical_json(provisional.model_context()),
    }
    if review is not None:
        artifacts["financial_case_review.json"] = canonical_json(review)
    if operating is not None:
        artifacts.update(operating.artifacts)
    return CaseContext(
        case=case,
        review=review,
        reconciliation=reconciliation,
        selected_facts=selected_facts,
        source_passages=source_passages,
        source_material=source_material,
        limitations=limitations,
        artifacts=artifacts,
        scope=scope,
        reviewed=reviewed,
        operating_scenarios=operating,
    )
