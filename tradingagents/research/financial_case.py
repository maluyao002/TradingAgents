"""Evidence-bound Stage 2 financial reconciliation mechanics.

The contracts in this module reconcile already-frozen evidence.  They do not
acquire evidence, decide whether funding is adequate, or turn an analyst
convention into a reported fact.  Subtotals are deliberately schedule-local:
a partial cash or debt schedule is never presented as a complete equity bridge.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .contracts import Contract, EvidenceSnapshot, Identifier
from .result_scope import ComponentEligibility, ModelResultScope
from .sources import is_exact_sec_archive_filing_url
from .storage import digest

ScheduleKind = Literal[
    "operating_working_capital",
    "cash_and_securities",
    "debt_and_leases",
    "shares",
]
ScheduleStatus = Literal["complete", "partial", "not_assessed"]
ComponentEffect = Literal["add", "subtract", "exclude", "unresolved"]
ComponentClassification = Literal[
    "operating_current_asset",
    "operating_current_liability",
    "available_cash",
    "available_security",
    "restricted_cash",
    "unavailable_security",
    "other_cash_or_security",
    "borrowed_debt",
    "finance_lease",
    "operating_lease",
    "other_financing",
    "diluted_shares",
    "share_adjustment",
]
OutputName = Literal[
    "operating_asset_value",
    "equity_per_share_value",
    "funding_assessment",
    "opening_date_alignment",
]

_SCHEDULE_KINDS = {
    "operating_working_capital",
    "cash_and_securities",
    "debt_and_leases",
    "shares",
}
_CLASSIFICATIONS_BY_SCHEDULE = {
    "operating_working_capital": {
        "operating_current_asset",
        "operating_current_liability",
    },
    "cash_and_securities": {
        "available_cash",
        "available_security",
        "restricted_cash",
        "unavailable_security",
        "other_cash_or_security",
    },
    "debt_and_leases": {
        "borrowed_debt",
        "finance_lease",
        "operating_lease",
        "other_financing",
    },
    "shares": {"diluted_shares", "share_adjustment"},
}


class FinancialConvention(Contract):
    """An explicit analyst convention or assumption, never a financial fact."""

    id: Identifier
    kind: Literal["convention", "assumption"]
    description: str = Field(min_length=1)
    affected_component_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("convention descriptions cannot be blank")
        return value


class ScheduleLine(Contract):
    """One normalized schedule row whose amount is copied from a FinancialFact."""

    id: Identifier
    fact_id: Identifier
    classification: ComponentClassification
    effect: ComponentEffect
    normalized_value: Decimal
    unit: str = Field(min_length=1)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period_start: date | None = None
    period_end: date
    convention_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def finite_value_and_treatment(self):
        if not self.normalized_value.is_finite():
            raise ValueError("component normalized values must be finite")
        if self.classification == "operating_current_asset" and self.effect not in {
            "add",
            "exclude",
            "unresolved",
        }:
            raise ValueError("operating current assets cannot subtract from working capital")
        if self.classification == "operating_current_liability" and self.effect not in {
            "subtract",
            "exclude",
            "unresolved",
        }:
            raise ValueError("operating current liabilities cannot add to working capital")
        if self.classification in {"available_cash", "available_security"} and self.effect not in {
            "add",
            "unresolved",
        }:
            raise ValueError("available cash and securities must be added or unresolved")
        if self.classification in {"restricted_cash", "unavailable_security"} and self.effect not in {
            "exclude",
            "unresolved",
        }:
            raise ValueError("restricted or unavailable amounts must be excluded or unresolved")
        if self.classification == "diluted_shares" and self.effect not in {"add", "unresolved"}:
            raise ValueError("diluted shares must be added or unresolved")
        return self


class ReconciliationSchedule(Contract):
    """A schedule-local assessment; ``included_subtotal`` is computed downstream."""

    id: Identifier
    kind: ScheduleKind
    status: ScheduleStatus
    rationale: str = Field(min_length=1)
    components: tuple[ScheduleLine, ...] = ()
    share_basis: Literal[
        "point_in_time_diluted",
        "latest_quarter_diluted_proxy",
        "not_assessed",
    ] | None = None

    @model_validator(mode="after")
    def coherent_schedule(self):
        if not self.rationale.strip():
            raise ValueError("schedule rationale cannot be blank")
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("schedule component identifiers must be unique")
        allowed = _CLASSIFICATIONS_BY_SCHEDULE[self.kind]
        if any(component.classification not in allowed for component in self.components):
            raise ValueError("component classification does not match its schedule")
        if self.kind == "shares":
            if any(
                component.unit != "shares" or component.currency is not None
                for component in self.components
            ):
                raise ValueError("share schedules require shares units without a currency")
        elif any(
            component.currency is None or component.unit != component.currency
            for component in self.components
        ):
            raise ValueError("monetary schedules require matching ISO currency and unit")
        if self.status == "complete":
            if not self.components or not any(
                component.effect in {"add", "subtract"} for component in self.components
            ):
                raise ValueError("complete schedules require an included component")
            if any(component.effect == "unresolved" for component in self.components):
                raise ValueError("complete schedules cannot contain unresolved components")
        if self.kind == "shares" and self.share_basis is None:
            raise ValueError("share schedules require an explicit share basis")
        if self.kind != "shares" and self.share_basis is not None:
            raise ValueError("share basis is only valid for the share schedule")
        if self.kind == "shares" and self.status == "complete" and self.share_basis == "not_assessed":
            raise ValueError("a complete share schedule requires an assessed share basis")
        return self


class CommitmentItem(Contract):
    """A commitment row with explicit timing and double-counting treatment."""

    id: Identifier
    fact_id: Identifier
    normalized_value: Decimal
    unit: str = Field(min_length=1)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period_start: date | None = None
    period_end: date
    kind: Literal["purchase", "cloud", "lease", "guarantee", "other"] = "other"
    amount_basis: Literal["contractual_cash", "maximum_exposure", "other"] = "contractual_cash"
    timing: Literal["known", "unknown"]
    disclosed_timing: str | None = Field(default=None, min_length=1)
    due_start: date | None = None
    due_end: date | None = None
    overlap: Literal["none", "opex", "capex", "working_capital", "multiple", "unknown"]
    treatment: Literal["deduct_incrementally", "already_reflected", "not_assessed"]
    convention_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def timing_and_overlap_are_conservative(self):
        if not self.normalized_value.is_finite() or self.normalized_value < 0:
            raise ValueError("commitment values must be finite and nonnegative")
        if self.timing == "known":
            if self.due_start is None or self.due_end is None:
                raise ValueError("known commitment timing requires a date range")
            if self.due_start > self.due_end:
                raise ValueError("commitment timing ends before it begins")
        elif self.due_start is not None or self.due_end is not None:
            raise ValueError("unknown commitment timing must remain undated")

        if self.amount_basis == "maximum_exposure":
            if self.treatment != "not_assessed":
                raise ValueError("maximum exposure is not an expected cash commitment")
        elif self.timing == "unknown" or self.overlap == "unknown":
            if self.treatment != "not_assessed":
                raise ValueError("unknown timing or overlap cannot be deducted")
        elif self.overlap == "none":
            if self.treatment != "deduct_incrementally":
                raise ValueError("known non-overlapping commitments use incremental treatment")
        elif self.treatment != "already_reflected":
            raise ValueError("opex, capex, or working-capital overlap cannot be deducted twice")
        return self


class CommitmentSchedule(Contract):
    id: Identifier
    status: ScheduleStatus
    rationale: str = Field(min_length=1)
    items: tuple[CommitmentItem, ...] = ()

    @model_validator(mode="after")
    def coherent_commitments(self):
        if not self.rationale.strip():
            raise ValueError("commitment rationale cannot be blank")
        identifiers = [item.id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("commitment identifiers must be unique")
        if self.status == "complete" and any(
            item.treatment == "not_assessed" for item in self.items
        ):
            raise ValueError("complete commitment schedules cannot contain unassessed items")
        return self


class EvidenceGap(Contract):
    id: Identifier
    area: Literal[
        "operating_working_capital",
        "cash_and_securities",
        "debt_and_leases",
        "shares",
        "opening_date",
        "commitments",
        "expectations",
    ]
    description: str = Field(min_length=1)
    blocks: tuple[OutputName, ...] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def nonblank_unique_gap(self):
        if not self.description.strip():
            raise ValueError("evidence gap descriptions cannot be blank")
        if len(self.blocks) != len(set(self.blocks)):
            raise ValueError("evidence gap outputs must be unique")
        return self


class ConclusionAssessment(Contract):
    """A reviewed assessment declaration, not an assertion that an outcome is safe."""

    id: Identifier
    output: Literal["equity_bridge", "funding"]
    status: Literal["assessed", "not_assessed"]
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = ()
    convention_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def supported_assessment(self):
        if not self.rationale.strip():
            raise ValueError("assessment rationale cannot be blank")
        if self.status == "assessed" and not self.evidence_ids:
            raise ValueError("assessed conclusions require eligible evidence identifiers")
        return self


class FinancialCase(Contract):
    ticker: str = Field(min_length=1)
    cutoff: AwareDatetime
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    opening_date: date
    maximum_age_days: int = Field(default=0, ge=0)
    schedules: tuple[ReconciliationSchedule, ...]
    commitments: CommitmentSchedule
    conventions: tuple[FinancialConvention, ...]
    gaps: tuple[EvidenceGap, ...] = ()
    assessments: tuple[ConclusionAssessment, ...]

    @model_validator(mode="after")
    def complete_case_shape(self):
        if self.opening_date > self.cutoff.date():
            raise ValueError("opening date cannot be after the evidence cutoff")
        schedule_kinds = [schedule.kind for schedule in self.schedules]
        if set(schedule_kinds) != _SCHEDULE_KINDS or len(schedule_kinds) != len(
            _SCHEDULE_KINDS
        ):
            raise ValueError("exactly one schedule is required for each financial area")
        assessment_outputs = [assessment.output for assessment in self.assessments]
        if set(assessment_outputs) != {"equity_bridge", "funding"} or len(
            assessment_outputs
        ) != 2:
            raise ValueError("equity and funding assessments are both required")

        records = [
            *self.schedules,
            *[component for schedule in self.schedules for component in schedule.components],
            self.commitments,
            *self.commitments.items,
            *self.conventions,
            *self.gaps,
            *self.assessments,
        ]
        identifiers = [record.id for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("financial case identifiers must be globally unique")

        selected_fact_ids = [
            component.fact_id for schedule in self.schedules for component in schedule.components
        ] + [item.fact_id for item in self.commitments.items]
        if len(selected_fact_ids) != len(set(selected_fact_ids)):
            raise ValueError(
                "a financial fact cannot be selected more than once across schedules and commitments"
            )

        financial_items = {
            component.id for schedule in self.schedules for component in schedule.components
        } | {item.id for item in self.commitments.items}
        conventions = {convention.id: convention for convention in self.conventions}
        for convention in self.conventions:
            if set(convention.affected_component_ids) - financial_items:
                raise ValueError("convention references an unknown financial item")
        for item in [
            *[component for schedule in self.schedules for component in schedule.components],
            *self.commitments.items,
        ]:
            unknown = set(item.convention_ids) - set(conventions)
            if unknown:
                raise ValueError("financial item references an unknown convention")
            for convention_id in item.convention_ids:
                if item.id not in conventions[convention_id].affected_component_ids:
                    raise ValueError("convention does not declare the financial item it affects")
        for assessment in self.assessments:
            if set(assessment.convention_ids) - set(conventions):
                raise ValueError("assessment references an unknown convention")
        return self


class ReconciledSchedule(Contract):
    id: Identifier
    kind: ScheduleKind
    status: ScheduleStatus
    included_subtotal: Decimal | None
    unit: str | None
    currency: str | None
    included_component_ids: tuple[Identifier, ...]
    excluded_component_ids: tuple[Identifier, ...]
    unresolved_component_ids: tuple[Identifier, ...]
    observation_dates: tuple[date, ...]
    opening_date_status: Literal["aligned", "stale", "mixed", "not_assessed"]
    share_basis: Literal[
        "point_in_time_diluted",
        "latest_quarter_diluted_proxy",
        "not_assessed",
    ] | None = None


class ReconciledCommitments(Contract):
    id: Identifier
    status: ScheduleStatus
    incremental_subtotal: Decimal | None
    already_reflected_subtotal: Decimal | None
    unit: str | None
    currency: str | None
    incremental_ids: tuple[Identifier, ...]
    already_reflected_ids: tuple[Identifier, ...]
    unassessed_ids: tuple[Identifier, ...]


class FinancialReconciliation(Contract):
    ticker: str
    cutoff: AwareDatetime
    opening_date: date
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    schedules: tuple[ReconciledSchedule, ...]
    commitments: ReconciledCommitments
    conventions: tuple[FinancialConvention, ...]
    evidence_gaps: tuple[EvidenceGap, ...]
    output_eligibility: ModelResultScope


def evidence_snapshot_sha256(snapshot: EvidenceSnapshot) -> str:
    """Return the canonical validated-contract hash used by this boundary."""

    checked = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json(warnings="error"))
    return digest(checked.model_dump(mode="json"))


def _exact_decimal_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply without inheriting a caller's mutable Decimal context."""

    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    precision = max(50, len(left_tuple.digits) + len(right_tuple.digits) + 1)
    with localcontext(Context(prec=precision)):
        return left * right


def _exact_decimal_sum(values) -> Decimal:
    """Add finite Decimals exactly under a locally sized context."""

    values = tuple(values)
    if not values:
        return Decimal(0)
    minimum_exponent = min(value.as_tuple().exponent for value in values)
    aligned_digits = max(
        len(value.as_tuple().digits) + value.as_tuple().exponent - minimum_exponent
        for value in values
    )
    precision = max(50, aligned_digits + len(str(len(values))) + 1)
    with localcontext(Context(prec=precision)):
        return sum(values, Decimal(0))


def _eligible_source_ids(snapshot: EvidenceSnapshot) -> set[str]:
    eligible = set()
    for source in snapshot.sources:
        exact_late_sec_filing = bool(
            snapshot.instrument is not None
            and source.kind == "filing"
            and source.accession is not None
            and is_exact_sec_archive_filing_url(
                source.url,
                cik=snapshot.instrument.cik,
                accession=source.accession,
            )
        )
        if (
            source.availability == "full_text"
            and source.published_at is not None
            and source.published_at <= snapshot.cutoff
            and (source.retrieved_at <= snapshot.cutoff or exact_late_sec_filing)
        ):
            eligible.add(source.id)
    return eligible


def _eligible_fact_ids(snapshot: EvidenceSnapshot, source_ids: set[str]) -> set[str]:
    eligible: set[str] = set()
    pending = list(snapshot.facts)
    while pending:
        ready = [
            fact
            for fact in pending
            if fact.source_id in source_ids and set(fact.inputs) <= eligible
        ]
        if not ready:
            break
        eligible.update(fact.id for fact in ready)
        pending = [fact for fact in pending if fact.id not in eligible]
    return eligible


def _verify_fact_copy(item: ScheduleLine | CommitmentItem, fact) -> None:
    copied = (
        item.normalized_value,
        item.unit,
        item.currency,
        item.period_start,
        item.period_end,
    )
    actual = (
        _exact_decimal_product(fact.value, fact.scale),
        fact.unit,
        fact.currency,
        fact.period_start,
        fact.period_end,
    )
    if copied != actual:
        raise ValueError(f"normalized fact fields differ from frozen evidence: {item.id}")


def _common_units(items: list[ScheduleLine | CommitmentItem]) -> tuple[str | None, str | None]:
    if not items:
        return None, None
    units = {item.unit for item in items}
    currencies = {item.currency for item in items}
    if len(units) != 1 or len(currencies) != 1:
        raise ValueError("included financial rows must have one unit and currency")
    return next(iter(units)), next(iter(currencies))


def _opening_status(
    components: list[ScheduleLine], opening_date: date, maximum_age_days: int
) -> Literal["aligned", "stale", "mixed", "not_assessed"]:
    if not components:
        return "not_assessed"
    dates = {component.period_end for component in components}
    if len(dates) != 1:
        return "mixed"
    observation_date = next(iter(dates))
    earliest = opening_date - timedelta(days=maximum_age_days)
    return "aligned" if earliest <= observation_date <= opening_date else "stale"


def _reconcile_schedule(
    schedule: ReconciliationSchedule, opening_date: date, maximum_age_days: int
) -> ReconciledSchedule:
    included = [
        component for component in schedule.components if component.effect in {"add", "subtract"}
    ]
    unit, currency = _common_units(included)
    subtotal = None
    if included:
        subtotal = _exact_decimal_sum(
            (
                component.normalized_value
                if component.effect == "add"
                else component.normalized_value.copy_negate()
            )
            for component in included
        )
    return ReconciledSchedule(
        id=schedule.id,
        kind=schedule.kind,
        status=schedule.status,
        included_subtotal=subtotal,
        unit=unit,
        currency=currency,
        included_component_ids=tuple(component.id for component in included),
        excluded_component_ids=tuple(
            component.id for component in schedule.components if component.effect == "exclude"
        ),
        unresolved_component_ids=tuple(
            component.id for component in schedule.components if component.effect == "unresolved"
        ),
        observation_dates=tuple(sorted({component.period_end for component in included})),
        opening_date_status=_opening_status(included, opening_date, maximum_age_days),
        share_basis=schedule.share_basis,
    )


def _reconcile_commitments(schedule: CommitmentSchedule) -> ReconciledCommitments:
    measured = [item for item in schedule.items if item.treatment != "not_assessed"]
    unit, currency = _common_units(measured)
    incremental = [item for item in schedule.items if item.treatment == "deduct_incrementally"]
    reflected = [item for item in schedule.items if item.treatment == "already_reflected"]
    return ReconciledCommitments(
        id=schedule.id,
        status=schedule.status,
        incremental_subtotal=(
            _exact_decimal_sum(item.normalized_value for item in incremental)
            if incremental
            else None
        ),
        already_reflected_subtotal=(
            _exact_decimal_sum(item.normalized_value for item in reflected)
            if reflected
            else None
        ),
        unit=unit,
        currency=currency,
        incremental_ids=tuple(item.id for item in incremental),
        already_reflected_ids=tuple(item.id for item in reflected),
        unassessed_ids=tuple(
            item.id for item in schedule.items if item.treatment == "not_assessed"
        ),
    )


def _eligibility(
    case: FinancialCase,
    schedules: tuple[ReconciledSchedule, ...],
    commitments: ReconciledCommitments,
) -> ModelResultScope:
    by_kind = {schedule.kind: schedule for schedule in schedules}
    gaps_by_output = {
        output: tuple(gap.id for gap in case.gaps if output in gap.blocks)
        for output in (
            "operating_asset_value",
            "equity_per_share_value",
            "funding_assessment",
            "opening_date_alignment",
        )
    }
    assessments = {assessment.output: assessment for assessment in case.assessments}

    operating_reasons = []
    working_capital = by_kind["operating_working_capital"]
    if working_capital.status != "complete":
        operating_reasons.append("The operating working-capital schedule is not complete.")
    if working_capital.opening_date_status != "aligned":
        operating_reasons.append("Operating working capital is not aligned to the opening date.")
    if gaps_by_output["operating_asset_value"]:
        operating_reasons.append(
            "Open evidence gaps block operating output: "
            + ", ".join(gaps_by_output["operating_asset_value"])
            + "."
        )
    operating = ComponentEligibility(
        status="blocked" if operating_reasons else "conditional",
        reasons=tuple(operating_reasons)
        or ("Operating output is evidence-bound and conditional, not an economic approval.",),
        evidence_ids=tuple(
            component.fact_id
            for schedule in case.schedules
            if schedule.kind == "operating_working_capital"
            for component in schedule.components
            if component.effect in {"add", "subtract"}
        ),
    )

    opening_reasons = []
    unaligned = [
        schedule.id for schedule in schedules if schedule.opening_date_status != "aligned"
    ]
    if unaligned:
        opening_reasons.append("Schedules are not opening-date aligned: " + ", ".join(unaligned) + ".")
    if gaps_by_output["opening_date_alignment"]:
        opening_reasons.append(
            "Open evidence gaps block date alignment: "
            + ", ".join(gaps_by_output["opening_date_alignment"])
            + "."
        )
    opening = ComponentEligibility(
        status="blocked" if opening_reasons else "conditional",
        reasons=tuple(opening_reasons)
        or ("Opening-date alignment satisfies the stated maximum-age convention only.",),
    )

    equity_reasons = []
    equity_schedules = [
        by_kind["cash_and_securities"],
        by_kind["debt_and_leases"],
        by_kind["shares"],
    ]
    incomplete_equity = [schedule.id for schedule in equity_schedules if schedule.status != "complete"]
    if incomplete_equity:
        equity_reasons.append("Equity schedules are incomplete: " + ", ".join(incomplete_equity) + ".")
    stale_equity = [
        schedule.id for schedule in equity_schedules if schedule.opening_date_status != "aligned"
    ]
    if stale_equity:
        equity_reasons.append(
            "Equity inputs are stale, mixed, or unassessed: " + ", ".join(stale_equity) + "."
        )
    if by_kind["shares"].share_basis != "point_in_time_diluted":
        equity_reasons.append("A share proxy or unassessed share basis cannot support per-share output.")
    if gaps_by_output["equity_per_share_value"]:
        equity_reasons.append(
            "Open evidence gaps block the equity bridge: "
            + ", ".join(gaps_by_output["equity_per_share_value"])
            + "."
        )
    equity_assessment = assessments["equity_bridge"]
    if equity_assessment.status == "not_assessed":
        equity_reasons.append("The equity bridge has not been explicitly assessed.")
    else:
        equity_reasons.append(
            "Stage 2 reconciliation does not by itself authorize equity or per-share output."
        )
    equity = ComponentEligibility(
        status="blocked",
        reasons=tuple(equity_reasons),
        evidence_ids=equity_assessment.evidence_ids,
    )

    funding_reasons = []
    funding_schedules = list(by_kind.values())
    incomplete_funding = [
        schedule.id for schedule in funding_schedules if schedule.status != "complete"
    ]
    if incomplete_funding:
        funding_reasons.append(
            "Funding inputs have incomplete schedules: " + ", ".join(incomplete_funding) + "."
        )
    stale_funding = [
        schedule.id for schedule in funding_schedules if schedule.opening_date_status != "aligned"
    ]
    if stale_funding:
        funding_reasons.append(
            "Funding inputs are stale, mixed, or unassessed: "
            + ", ".join(stale_funding)
            + "."
        )
    if commitments.status != "complete" or commitments.unassessed_ids:
        funding_reasons.append("Commitment timing and overlap are not completely reconciled.")
    if gaps_by_output["funding_assessment"]:
        funding_reasons.append(
            "Open evidence gaps block funding assessment: "
            + ", ".join(gaps_by_output["funding_assessment"])
            + "."
        )
    funding_assessment = assessments["funding"]
    if funding_assessment.status == "not_assessed":
        funding_reasons.append("Company-wide funding has not been explicitly assessed.")
    else:
        funding_reasons.append(
            "Stage 2 reconciliation does not by itself authorize a funding conclusion."
        )
    funding = ComponentEligibility(
        status="blocked",
        reasons=tuple(funding_reasons),
        evidence_ids=funding_assessment.evidence_ids,
    )

    return ModelResultScope(
        operating_asset_value=operating,
        equity_per_share_value=equity,
        funding_assessment=funding,
        opening_date_alignment=opening,
    )


def reconcile_financial_case(
    case: FinancialCase, snapshot: EvidenceSnapshot
) -> FinancialReconciliation:
    """Revalidate, bind, recompute, and conservatively scope one financial case."""

    # ``model_copy`` and ``model_construct`` can bypass nested validation.  JSON
    # round-trips also create an independently owned boundary object.
    checked_case = FinancialCase.model_validate_json(
        case.model_dump_json(warnings="error")
    )
    checked_snapshot = EvidenceSnapshot.model_validate_json(
        snapshot.model_dump_json(warnings="error")
    )
    if checked_case.ticker != checked_snapshot.ticker or checked_case.cutoff != checked_snapshot.cutoff:
        raise ValueError("financial case identity differs from the frozen snapshot")
    snapshot_hash = evidence_snapshot_sha256(checked_snapshot)
    if checked_case.snapshot_sha256 != snapshot_hash:
        raise ValueError("financial case snapshot hash mismatch")

    evidence_records = (
        *checked_snapshot.sources,
        *checked_snapshot.facts,
        *checked_snapshot.events,
        *checked_snapshot.expectations,
    )
    evidence_ids = {record.id for record in evidence_records}
    case_ids = {
        schedule.id for schedule in checked_case.schedules
    } | {
        component.id
        for schedule in checked_case.schedules
        for component in schedule.components
    } | {
        checked_case.commitments.id,
        *[item.id for item in checked_case.commitments.items],
        *[item.id for item in checked_case.conventions],
        *[item.id for item in checked_case.gaps],
        *[item.id for item in checked_case.assessments],
    }
    if evidence_ids & case_ids:
        raise ValueError("financial case identifiers cannot collide with evidence identifiers")

    eligible_sources = _eligible_source_ids(checked_snapshot)
    eligible_facts = _eligible_fact_ids(checked_snapshot, eligible_sources)
    facts = {fact.id: fact for fact in checked_snapshot.facts}
    selected_source_rows: set[tuple] = set()
    for item in [
        *[
            component
            for schedule in checked_case.schedules
            for component in schedule.components
        ],
        *checked_case.commitments.items,
    ]:
        if item.fact_id not in eligible_facts:
            raise ValueError(f"financial item references an ineligible fact: {item.id}")
        _verify_fact_copy(item, facts[item.fact_id])
        fact = facts[item.fact_id]
        source_row = (
            fact.source_id,
            fact.location,
            fact.metric,
            _exact_decimal_product(fact.value, fact.scale),
            fact.unit,
            fact.currency,
            fact.period_start,
            fact.period_end,
        )
        if source_row in selected_source_rows:
            raise ValueError("multiple selected fact IDs duplicate the same source row")
        selected_source_rows.add(source_row)

    eligible_expectations = {
        expectation.id
        for expectation in checked_snapshot.expectations
        if set(expectation.source_ids) <= eligible_sources
    }
    eligible_evidence = eligible_sources | eligible_facts | eligible_expectations
    for record in (*checked_case.gaps, *checked_case.assessments):
        if set(record.evidence_ids) - eligible_evidence:
            raise ValueError("gap or assessment references ineligible evidence")

    reconciled_schedules = tuple(
        _reconcile_schedule(
            schedule,
            checked_case.opening_date,
            checked_case.maximum_age_days,
        )
        for schedule in checked_case.schedules
    )
    reconciled_commitments = _reconcile_commitments(checked_case.commitments)
    eligibility = _eligibility(checked_case, reconciled_schedules, reconciled_commitments)
    return FinancialReconciliation(
        ticker=checked_case.ticker,
        cutoff=checked_case.cutoff,
        opening_date=checked_case.opening_date,
        snapshot_sha256=snapshot_hash,
        case_sha256=digest(checked_case.model_dump(mode="json")),
        schedules=reconciled_schedules,
        commitments=reconciled_commitments,
        conventions=checked_case.conventions,
        evidence_gaps=checked_case.gaps,
        output_eligibility=eligibility,
    )


# Compatibility names for the initial Stage 2 adapter sketch.
ScheduleComponent = ScheduleLine
FinancialReconciliationInput = FinancialCase
