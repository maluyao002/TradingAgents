"""Material model inputs and an audit of their actual delivered contents.

Integrity and delivery are not factual or human approval. Expected inputs come
from the independently verified packet, never from the model's own payload.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from .assumptions import AssumptionPackage
from .contracts import Contract, EvidenceSnapshot, FinancialFact
from .derivations import TerminalReinvestmentDerivation
from .scenario_compiler import ConditionalScenario
from .storage import canonical_json, digest, parse_json


class ExactSourceMaterial(Contract):
    """An exact contiguous row/table/statement including interpretive context."""

    id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=50_000)
    context: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    observation_date: str = Field(min_length=1)
    classification: Literal["external_reference"] = "external_reference"

    @model_validator(mode="after")
    def exact_length(self):
        if self.end - self.start != len(self.text):
            raise ValueError("source-material character span differs from text")
        return self


class ReviewedModelInputs(Contract):
    ticker: str
    cutoff: AwareDatetime
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_material: tuple[ExactSourceMaterial, ...]
    market_inputs: dict[str, Any]
    economic_derivations: dict[str, Any]
    financial_facts: tuple[FinancialFact, ...]
    assumptions: AssumptionPackage
    scenarios: tuple[ConditionalScenario, ...]
    terminal_derivations: tuple[TerminalReinvestmentDerivation, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    scope: Literal["conditional_inputs_not_human_acceptance"] = "conditional_inputs_not_human_acceptance"

    @model_validator(mode="after")
    def identities(self):
        for values in (self.source_material, self.financial_facts, self.scenarios):
            identifiers = [item.id for item in values]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("reviewed input identifiers must be unique")
        if (self.assumptions.ticker != self.ticker or self.assumptions.cutoff != self.cutoff
                or self.assumptions.evidence_sha256 != self.evidence_sha256):
            raise ValueError("reviewed assumptions identity mismatch")
        return self


class ConditionalEconomicAudit(Contract):
    """Exact vocabulary for the current zero-debt conditional rate/terminal bridge."""

    discount_rate_formula: Literal["Treasury_nominal_reference + analyst_unlevered_beta * held_fixed_ERP"]
    treasury_reference: Decimal
    erp: Decimal
    analyst_beta: Decimal = Field(gt=0)
    discount_rate: Decimal
    target_debt_weight: Decimal = Field(ge=0, le=0)
    beta_reference_not_nvda_beta: Decimal = Field(gt=0)
    terminal_roic_assumption: Decimal = Field(gt=0)
    terminal_growth: Decimal
    terminal_reinvestment_fraction_of_nopat: Decimal
    terminal_capex_pct_revenue: Decimal
    terminal_capex_formula: Literal["DA_ratio + margin*(1-tax)*g/ROIC - WC_ratio*g/(1+g)"]
    probability: None


def validate_material(bundle: ReviewedModelInputs, snapshot: EvidenceSnapshot) -> None:
    """Verify slices against the eligible frozen sources, not just their hashes."""
    if bundle.ticker != snapshot.ticker or bundle.cutoff != snapshot.cutoff:
        raise ValueError("reviewed input snapshot identity mismatch")
    sources = {source.id: source for source in snapshot.sources}
    for item in bundle.source_material:
        source = sources.get(item.source_id)
        if (source is None or source.availability != "full_text"
                or source.published_at is None or source.published_at > snapshot.cutoff
                or source.retrieved_at > snapshot.cutoff
                or source.content_sha256 != item.source_sha256
                or hashlib.sha256(source.content.encode()).hexdigest() != item.source_sha256
                or source.content[item.start:item.end] != item.text):
            raise ValueError(f"ineligible or changed source material: {item.id}")
    if bundle.financial_facts != snapshot.facts:
        raise ValueError("reviewed financial facts differ from the frozen snapshot")
    available_facts = set()
    pending = list(bundle.financial_facts)
    while pending:
        ready = []
        for fact in pending:
            source = sources.get(fact.source_id)
            if (source is not None and source.availability == "full_text"
                    and source.published_at is not None and source.published_at <= snapshot.cutoff
                    and source.retrieved_at <= snapshot.cutoff and set(fact.inputs) <= available_facts):
                ready.append(fact.id)
        if not ready:
            raise ValueError("reviewed financial facts have ineligible sources or ancestry")
        available_facts.update(ready)
        pending = [fact for fact in pending if fact.id not in available_facts]


class MaterialCoverage(Contract):
    complete: bool
    missing_or_changed: tuple[str, ...]
    affected_outputs: tuple[str, ...]
    expected_sha256: str
    delivered_sha256: str
    payload_sha256: str


def audit_model_payload(payload: dict | bytes, expected: ReviewedModelInputs) -> MaterialCoverage:
    """Audit the actual final dictionary or serialized model prompt, not a manifest.

Every material section is mandatory and compared by value. Hash-only references,
omitted rows/headers, changed derivations and truncated assumptions fail closed.
The separately supplied expected bundle must already have passed packet validation.
"""
    actual = parse_json(payload) if isinstance(payload, bytes) else payload
    delivered = actual.get("reviewed_inputs") if isinstance(actual, dict) else None
    wanted = expected.model_dump(mode="json")
    missing = []
    if not isinstance(delivered, dict):
        missing.append("reviewed_inputs")
    else:
        for key, value in wanted.items():
            if key not in delivered or canonical_json(delivered[key]) != canonical_json(value):
                missing.append(key)
        if set(delivered) - set(wanted):
            missing.append("unexpected_fields")
    return MaterialCoverage(
        complete=not missing, missing_or_changed=tuple(missing),
        affected_outputs=("operating_asset_value", "equity_per_share_value") if missing else (),
        expected_sha256=digest(wanted), delivered_sha256=digest(delivered),
        payload_sha256=digest(actual),
    )


def require_material_coverage(payload: dict | bytes, expected: ReviewedModelInputs) -> MaterialCoverage:
    audit = audit_model_payload(payload, expected)
    if not audit.complete:
        raise ValueError("material model inputs omitted or changed: " + ", ".join(audit.missing_or_changed))
    return audit
