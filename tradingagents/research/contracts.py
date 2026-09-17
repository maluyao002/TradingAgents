"""Versioned research contracts. Validation is not a claim of factual truth."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w.:/-]+$")]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
Severity = Literal["info", "warning", "critical"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1


class ModelSetting(Contract):
    model: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"] = "high"


class InstrumentIdentity(Contract):
    issuer: str = Field(min_length=1)
    cik: str = Field(pattern=r"^\d{10}$")
    exchange: str = Field(min_length=1)
    share_class: str = Field(min_length=1)
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    ordinary_shares_per_adr: Decimal | None = Field(default=None, gt=0)
    adr_ratio_effective_at: date | None = None
    identity_source: str = Field(min_length=1)

    @model_validator(mode="after")
    def adr_effective_date(self):
        if (self.ordinary_shares_per_adr is None) != (self.adr_ratio_effective_at is None):
            raise ValueError("ADR ratios require an effective date")
        return self


def default_roles() -> dict[str, ModelSetting]:
    # Model configuration is neutral infrastructure; do not import legacy agents.
    from tradingagents.model_profiles import MODEL_PROFILES

    assignments = {
        "planner": "research_manager", "business": "fundamentals",
        "accounting": "fundamentals", "expectations": "fundamentals",
        "management": "fundamentals", "valuation": "fundamentals",
        "events": "news", "challenger": "portfolio_manager",
        "verifier": "fundamentals", "editor": "portfolio_manager",
    }
    profile = MODEL_PROFILES["balanced"]["agents"]
    return {role: ModelSetting(model=profile[old]["model"], effort=profile[old]["reasoning_effort"])
            for role, old in assignments.items()}


class Budget(Contract):
    wall_seconds: Annotated[StrictInt, Field(gt=0)] = 5400
    total_tokens: Annotated[StrictInt, Field(gt=0)] = 1_500_000
    reserve_seconds: NonnegativeInt = 1200
    reserve_tokens: NonnegativeInt = 300_000
    call_timeout_seconds: Annotated[StrictInt, Field(gt=0)] = 300
    followup_cycles: Annotated[StrictInt, Field(ge=0, le=3)] = 3

    @model_validator(mode="after")
    def valid_reserves(self):
        if self.reserve_seconds >= self.wall_seconds or self.reserve_tokens >= self.total_tokens:
            raise ValueError("reserves must be smaller than the run allowance")
        if self.call_timeout_seconds > self.wall_seconds:
            raise ValueError("call timeout exceeds run allowance")
        return self


class ResearchRequest(Contract):
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,19}$")
    cutoff: AwareDatetime
    timezone: str = "America/Los_Angeles"
    backend: Literal["replay", "codex", "api"]
    output_dir: Path
    mandate: str = "Long-term fundamental company research"
    valuation_months: Annotated[StrictInt, Field(gt=0)] = 12
    return_months: Annotated[StrictInt, Field(gt=0)] = 36
    internal_language: Literal["English", "Chinese"] = "English"
    report_language: Literal["English", "Chinese"] = "Chinese"
    source_policy: Literal["public_first"] = "public_first"
    models: dict[str, ModelSetting] = Field(default_factory=default_roles)
    budget: Budget = Field(default_factory=Budget)
    evidence_path: Path | None = None
    prior_dossier_path: Path | None = None
    dossier_dir: Path | None = None
    instrument: InstrumentIdentity | None = None

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown analysis timezone") from exc
        return value

    @model_validator(mode="after")
    def required_roles(self):
        if set(self.models) != set(default_roles()):
            raise ValueError("explicit assignments required for every research role")
        if self.backend == "replay" and self.evidence_path is None:
            raise ValueError("replay requires a frozen evidence snapshot")
        return self


class SourceDocument(Contract):
    id: Identifier
    url: str = Field(min_length=1)
    title: str
    publisher: str
    retrieved_at: AwareDatetime
    published_at: AwareDatetime | None = None
    content: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin_id: str | None = None
    accession: str | None = None
    kind: Literal["filing", "ir", "news", "regulatory", "market", "other"] = "other"
    availability: Literal["full_text", "snippet", "unavailable"] = "full_text"

    @model_validator(mode="after")
    def content_bound(self):
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("source content hash mismatch (UTF-8 extracted text)")
        return self


class FinancialFact(Contract):
    """A sourced value, not an LLM interpretation of a narrative."""

    id: Identifier
    source_id: Identifier
    metric: str
    value: Decimal
    unit: str
    currency: str | None = None
    scale: Decimal = Decimal(1)
    period_start: date | None = None
    period_end: date
    basis: str
    location: str
    segment: str | None = None
    inputs: tuple[str, ...] = ()
    formula: str | None = None
    period_type: Literal["instant", "duration"] = "instant"
    supersedes_id: str | None = None

    @property
    def normalized_value(self) -> Decimal:
        """value retains the source number; scale converts to unscaled base units."""
        return self.value * self.scale

    @model_validator(mode="after")
    def valid_period_and_value(self):
        if not self.value.is_finite() or not self.scale.is_finite() or self.scale <= 0:
            raise ValueError("financial values must be finite with a positive scale")
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("financial period ends before it begins")
        if self.period_type == "duration" and self.period_start is None:
            raise ValueError("duration facts require a start date")
        if bool(self.inputs) != bool(self.formula):
            raise ValueError("derived facts require both operands and a formula")
        return self


class ExtractedClaim(Contract):
    id: Identifier
    text: str
    source_ids: tuple[str, ...]
    source_locations: tuple[str, ...] = ()
    kind: Literal["reported", "interpretation", "assumption", "calculation"]
    verification: Literal["unverified", "supported", "contradicted"] = "unverified"
    material: bool = True


class ResearchEvent(Contract):
    id: Identifier
    title: str
    source_id: Identifier
    entities: tuple[str, ...]
    published_at: AwareDatetime | None
    event_at: AwareDatetime | None = None
    origin_id: str
    classification: Literal["announcement", "assertion", "reporting", "commentary", "rumor"]
    materiality_reason: str | None = None


class Expectation(Contract):
    id: Identifier
    metric: str
    kind: Literal["guidance", "consensus", "analyst", "own_forecast", "price_implied"]
    period_end: date
    basis: str
    unit: str
    value: Decimal | None = None
    source_ids: tuple[str, ...] = ()
    as_of: AwareDatetime
    contributors: NonnegativeInt | None = None
    limitation: str | None = None

    @model_validator(mode="after")
    def evidence_or_limit(self):
        if self.value is None and not self.limitation:
            raise ValueError("unavailable expectations require a limitation")
        if self.kind == "consensus" and self.contributors == 1:
            raise ValueError("one contributor is not consensus")
        if (self.value is not None and self.kind in {"guidance", "consensus", "analyst"}
                and not self.source_ids):
            raise ValueError("external expectations require evidence")
        return self


class ResearchQuestion(Contract):
    id: Identifier
    question: str
    consequence: str
    resolvability: Literal["high", "medium", "low"]
    status: Literal["open", "resolved", "unresolved"] = "open"


class Finding(Contract):
    id: Identifier
    question_id: Identifier
    conclusion: str
    evidence_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...] = ()
    uncertainty: str
    economic_consequence: str
    invalidation: str


class ReviewFinding(Contract):
    code: str
    severity: Severity
    message: str
    affected_ids: tuple[str, ...] = ()
    category: Literal["data", "numerical", "research", "editorial", "security"] = "research"


class EvidenceSnapshot(Contract):
    ticker: str
    cutoff: AwareDatetime
    sources: tuple[SourceDocument, ...] = ()
    facts: tuple[FinancialFact, ...] = ()
    events: tuple[ResearchEvent, ...] = ()
    expectations: tuple[Expectation, ...] = ()
    gaps: tuple[str, ...] = ()
    instrument: InstrumentIdentity | None = None

    @model_validator(mode="after")
    def referential_integrity(self):
        records = (*self.sources, *self.facts, *self.events, *self.expectations)
        ids = [record.id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence identifiers must be unique")
        source_ids = {source.id for source in self.sources}
        sources = {source.id: source for source in self.sources}
        fact_ids = {fact.id for fact in self.facts}
        for source in self.sources:
            if source.published_at is not None and source.published_at > self.cutoff:
                raise ValueError("source publication is after evidence cutoff")
        for item in (*self.facts, *self.events):
            if item.source_id not in source_ids:
                raise ValueError("unknown source reference")
        for fact in self.facts:
            if set(fact.inputs) - fact_ids or fact.id in fact.inputs:
                raise ValueError("invalid calculated fact operands")
            if sources[fact.source_id].published_at is None:
                raise ValueError("financial facts require known source publication availability")
            if fact.period_end > self.cutoff.date():
                raise ValueError("reported financial period is after evidence cutoff")
        edges = {fact.id: fact.inputs for fact in self.facts}
        visiting, visited = set(), set()

        def visit(identifier):
            if identifier in visiting:
                raise ValueError("calculated fact operands form a cycle")
            if identifier in visited:
                return
            visiting.add(identifier)
            for operand in edges[identifier]:
                visit(operand)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in edges:
            visit(identifier)
        for event in self.events:
            if event.published_at is not None and event.published_at > self.cutoff:
                raise ValueError("event publication is after evidence cutoff")
        for expectation in self.expectations:
            if set(expectation.source_ids) - source_ids:
                raise ValueError("unknown expectation source")
            if expectation.as_of > self.cutoff:
                raise ValueError("expectation is after evidence cutoff")
            if any(sources[sid].published_at is None for sid in expectation.source_ids):
                raise ValueError("expectations require known source publication availability")
        return self


class Usage(Contract):
    input_tokens: NonnegativeInt = 0
    output_tokens: NonnegativeInt = 0
    cached_input_tokens: NonnegativeInt = 0
    reasoning_output_tokens: NonnegativeInt = 0
    complete: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @model_validator(mode="after")
    def subset_counters(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input exceeds input tokens")
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning output exceeds output tokens")
        return self


class Assessment(Contract):
    status: Literal["accepted", "needs_review", "incomplete"] = "needs_review"
    investment_view: Literal["favorable", "neutral", "cautious", "unrated"] = "unrated"
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    findings: tuple[ReviewFinding, ...] = ()
    reviewed_claim_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def fail_closed(self):
        if self.status != "accepted" and self.investment_view != "unrated":
            raise ValueError("unaccepted research must remain unrated")
        if self.status == "accepted" and any(f.severity == "critical" for f in self.findings):
            raise ValueError("critical findings block acceptance")
        return self


class ResearchResult(Contract):
    ticker: str
    cutoff: AwareDatetime
    artifacts: dict[str, str]
    artifact_hashes: dict[str, str]
    assessment: Assessment
    usage: Usage = Field(default_factory=Usage)
    unresolved_gaps: tuple[str, ...] = ()
    promoted_dossier_id: str | None = None
    stop_reason: str

    @model_validator(mode="after")
    def artifacts_consistent(self):
        import re

        if self.artifacts.keys() != self.artifact_hashes.keys():
            raise ValueError("artifact and hash keys must match")
        if any(not re.fullmatch(r"[a-f0-9]{64}", value)
               for value in self.artifact_hashes.values()):
            raise ValueError("invalid artifact hash")
        required = {"reader_report.md", "audit_report.md", "evidence.json", "research.json",
                    "valuation_inputs.json", "valuation_results.json", "quality.json",
                    "run_metadata.json"}
        if self.assessment.status == "accepted" and (
            not required <= self.artifacts.keys() or not self.usage.complete
        ):
            raise ValueError("accepted research requires complete artifacts and telemetry")
        return self


class Dossier(Contract):
    id: Identifier
    ticker: str
    cutoff: AwareDatetime
    created_at: AwareDatetime
    parent_id: str | None = None
    evidence_hash: str
    assessment: Assessment
    findings: tuple[Finding, ...] = ()
    claims: tuple[ExtractedClaim, ...] = ()
    open_questions: tuple[ResearchQuestion, ...] = ()
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    coverage_watermark: datetime | None = None

    @model_validator(mode="after")
    def watermark_not_future(self):
        if self.coverage_watermark is not None and (
            self.coverage_watermark.tzinfo is None or self.coverage_watermark > self.cutoff
        ):
            raise ValueError("coverage watermark must be aware and no later than cutoff")
        return self
