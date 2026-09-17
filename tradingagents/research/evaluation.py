"""Offline benchmark scoring; no model judge, network, or fabricated gold labels."""

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, EvidenceSnapshot, FinancialFact, NonnegativeInt
from .storage import digest


class BenchmarkCase(Contract):
    id: str
    ticker: str
    window: Literal["earnings", "quiet", "holdout", "synthetic"]
    evidence_hash: str
    reference_facts: tuple[FinancialFact, ...] = ()
    material_event_ids: tuple[str, ...] = ()
    must_capture_event_ids: tuple[str, ...] = ()
    human_reviewed: bool = False

    @model_validator(mode="after")
    def must_capture_subset(self):
        if set(self.must_capture_event_ids) - set(self.material_event_ids):
            raise ValueError("must-capture events must belong to the material reference set")
        return self


class BenchmarkScore(Contract):
    true_positives: NonnegativeInt
    false_positives: NonnegativeInt
    false_negatives: NonnegativeInt
    precision: float | None = Field(ge=0, le=1)
    recall: float | None = Field(ge=0, le=1)
    missing_must_capture: tuple[str, ...]
    incorrect_fact_ids: tuple[str, ...]
    eligible_for_release: bool
    scope: str = "Fixture scoring only; no certification of report quality or broad coverage."


def score_case(case: BenchmarkCase, snapshot: EvidenceSnapshot,
               reported_event_ids: tuple[str, ...]) -> BenchmarkScore:
    if snapshot.ticker != case.ticker or digest(snapshot) != case.evidence_hash:
        raise ValueError("evaluation evidence differs from frozen benchmark")
    predicted, expected = set(reported_event_ids), set(case.material_event_ids)
    true = len(predicted & expected)
    precision = true / len(predicted) if predicted else None
    recall = true / len(expected) if expected else None
    actual_facts = {fact.id: fact for fact in snapshot.facts}
    incorrect = tuple(fact.id for fact in case.reference_facts
                      if actual_facts.get(fact.id) != fact)
    missing = tuple(sorted(set(case.must_capture_event_ids) - predicted))
    # Empty labels are not evidence of perfect coverage.
    eligible = (case.human_reviewed and case.window != "synthetic" and not incorrect
                and not missing and precision is not None and recall is not None
                and precision >= .9 and recall >= .9)
    return BenchmarkScore(true_positives=true, false_positives=len(predicted - expected),
                          false_negatives=len(expected - predicted), precision=precision,
                          recall=recall, missing_must_capture=missing,
                          incorrect_fact_ids=incorrect, eligible_for_release=eligible)


REVIEW_RUBRIC = {
    "factual_support": "Material claims match source scope, period, units and accounting basis.",
    "causal_reasoning": "Driver-based explanations address independent counterevidence.",
    "expectations": "Guidance, consensus, forecasts and conditional price-implied cases differ.",
    "model_integrity": "Financial forecasts reconcile and economic assumptions are justified.",
    "uncertainty": "Critical gaps block dependent conclusions; no forced conviction.",
    "readability": "One coherent thesis; readable sources; no repetitive role transcripts.",
}
