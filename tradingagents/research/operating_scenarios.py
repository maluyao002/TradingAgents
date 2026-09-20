"""Reviewed, evidence-bound operating scenarios without valuation outputs.

This boundary accepts a small set of explicit revenue and operating assumptions,
revalidates their frozen evidence, and performs only operating arithmetic.  It
does not produce cash flow, valuation, funding, per-share, or terminal outputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .calculated_values import CalculatedValue
from .contracts import Contract, EvidenceSnapshot, FinancialFact, Identifier, ReviewFinding
from .financial_case import FinancialCase, evidence_snapshot_sha256, reconcile_financial_case
from .sources import is_exact_sec_archive_filing_url
from .storage import canonical_json, digest

_DATE_CONVENTION = "explicit_period_dates_control_fiscal_labels_are_descriptive"
_EARNINGS_BASIS = "US_GAAP_after_SBC"
_MAX_AMOUNT = Decimal("1e50")
_MAX_RESULT = Decimal("1e51")
_MAX_SOURCE_CHARS = 100_000
_BOUNDARY_LIMITATION = (
    "Operating scenarios stop at GAAP operating income after stock-based compensation; "
    "they do not provide cash-flow, valuation, funding, per-share, or terminal outputs."
)
_DRAFT_LIMITATION = (
    "The operating-scenario package lacks a current independent conditional review; "
    "its numerical inputs remain audit-only and cannot be presented as a forecast."
)


class ForecastSourceMaterial(Contract):
    """An exact, bounded slice of one eligible frozen source."""

    id: Identifier
    source_id: Identifier
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=50_000)
    context: str = Field(min_length=1)

    @model_validator(mode="after")
    def exact_span(self):
        if self.end - self.start != len(self.text):
            raise ValueError("forecast source-material span differs from exact text")
        if not self.context.strip():
            raise ValueError("forecast source-material context cannot be blank")
        return self


class OperatingScenarioAssumption(Contract):
    """One source-linked numeric input, explicitly classified as an assumption."""

    value: Decimal
    classification: Literal["analyst_assumption", "management_guidance_anchor"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def finite_supported_value(self):
        if not self.value.is_finite():
            raise ValueError("operating-scenario assumptions must be finite")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("assumption evidence IDs must be unique")
        if not self.rationale.strip():
            raise ValueError("assumption rationale cannot be blank")
        return self


class OperatingScenarioPeriod(Contract):
    """A dated future fiscal period; labels never substitute for exact dates."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[\w.:-]+$")
    fiscal_label: str = Field(min_length=1, max_length=80)
    period_start: date
    period_end: date
    currency: Literal["USD"] = "USD"
    unit: Literal["USD"] = "USD"
    amount_scale: Decimal = Decimal(1)
    accounting_basis: str = Field(min_length=1)
    earnings_basis: Literal["US_GAAP_after_SBC"] = _EARNINGS_BASIS
    revenue: OperatingScenarioAssumption
    gross_margin: OperatingScenarioAssumption
    opex: OperatingScenarioAssumption
    tax_rate: OperatingScenarioAssumption | None = None

    @model_validator(mode="after")
    def coherent_period(self):
        if self.period_start > self.period_end:
            raise ValueError("operating-scenario period ends before it begins")
        if self.amount_scale != Decimal(1):
            raise ValueError("operating-scenario amounts must be unscaled base USD")
        if not self.fiscal_label.strip() or not self.accounting_basis.strip():
            raise ValueError("fiscal labels and accounting basis cannot be blank")
        return self


class HistoricalOperatingAnchor(Contract):
    """Copied reported duration facts defining the model's observed opening date."""

    fiscal_label: str = Field(min_length=1, max_length=80)
    case_opening_date: date
    revenue_fact: FinancialFact
    operating_income_fact: FinancialFact

    @model_validator(mode="after")
    def same_duration_and_basis(self):
        revenue = self.revenue_fact
        operating_income = self.operating_income_fact
        if revenue.metric != "revenue" or operating_income.metric != "operating_income":
            raise ValueError("historical anchor requires revenue and operating_income facts")
        if revenue.period_type != "duration" or operating_income.period_type != "duration":
            raise ValueError("historical operating anchors must be duration facts")
        if revenue.period_start is None or operating_income.period_start is None:
            raise ValueError("historical operating anchors require explicit start dates")
        comparable = (
            revenue.period_start == operating_income.period_start,
            revenue.period_end == operating_income.period_end,
            revenue.basis == operating_income.basis == "US GAAP",
            revenue.unit == operating_income.unit == "USD",
            revenue.currency == operating_income.currency == "USD",
            revenue.segment is None,
            operating_income.segment is None,
        )
        if not all(comparable):
            raise ValueError(
                "historical operating anchors must be whole-entity US GAAP facts in USD"
            )
        if self.case_opening_date != revenue.period_end:
            raise ValueError("case opening date must equal the historical anchor end date")
        if not self.fiscal_label.strip():
            raise ValueError("historical fiscal label cannot be blank")
        return self


class OperatingScenario(Contract):
    """One conditional case with source-linked rationale and falsifier."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[\w.:-]+$")
    label: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1)
    rationale_evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    falsifier_evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    fiscal_year_end: date
    periods: tuple[OperatingScenarioPeriod, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def unique_nonblank_fields(self):
        if not self.label.strip() or not self.rationale.strip() or not self.falsifier.strip():
            raise ValueError("scenario label, rationale, and falsifier cannot be blank")
        for values in (self.rationale_evidence_ids, self.falsifier_evidence_ids):
            if len(values) != len(set(values)):
                raise ValueError("scenario evidence IDs must be unique")
        period_ids = [period.id for period in self.periods]
        labels = [period.fiscal_label for period in self.periods]
        if len(period_ids) != len(set(period_ids)) or len(labels) != len(set(labels)):
            raise ValueError("scenario period IDs and fiscal labels must be unique")
        return self


class OperatingScenarioReview(Contract):
    """Independent review bound to the exact package, case, and evidence snapshot."""

    reviewer_id: Identifier
    reviewed_at: AwareDatetime
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["conditional_operating_scenarios"]
    findings: tuple[ReviewFinding, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator("limitations")
    @classmethod
    def nonblank_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("operating-scenario review limitations cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("operating-scenario review limitations must be unique")
        return values


class OperatingScenarioPackage(Contract):
    """A case-bound operating forecast package, optionally carrying its review."""

    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,19}$")
    cutoff: AwareDatetime
    author_id: Identifier
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    date_convention: Literal["explicit_period_dates_control_fiscal_labels_are_descriptive"] = (
        _DATE_CONVENTION
    )
    historical_anchor: HistoricalOperatingAnchor
    source_material: tuple[ForecastSourceMaterial, ...] = Field(min_length=1)
    scenarios: tuple[OperatingScenario, ...] = Field(min_length=1, max_length=3)
    limitations: tuple[str, ...] = Field(min_length=1)
    review: OperatingScenarioReview | None = None

    @field_validator("limitations")
    @classmethod
    def nonblank_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("operating-scenario limitations cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("operating-scenario limitations must be unique")
        return values

    @model_validator(mode="after")
    def unique_identifiers_and_source_links(self):
        material_ids = [item.id for item in self.source_material]
        scenario_ids = [scenario.id for scenario in self.scenarios]
        labels = [scenario.label for scenario in self.scenarios]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("forecast source-material IDs must be unique")
        if len(scenario_ids) != len(set(scenario_ids)) or len(labels) != len(set(labels)):
            raise ValueError("operating scenario IDs and labels must be unique")
        known = set(material_ids)
        for scenario in self.scenarios:
            references = (*scenario.rationale_evidence_ids, *scenario.falsifier_evidence_ids)
            for period in scenario.periods:
                assumptions = (period.revenue, period.gross_margin, period.opex)
                if period.tax_rate is not None:
                    assumptions = (*assumptions, period.tax_rate)
                references = (
                    *references,
                    *(item for assumption in assumptions for item in assumption.evidence_ids),
                )
            if set(references) - known:
                raise ValueError("operating scenario references unknown source material")
        return self


class LimitationOrigin(Contract):
    """A machine-readable limitation source; text alone never authorizes retirement."""

    origin_id: Identifier
    retirable: bool = False
    required_witness_reference: Literal["review:operating_scenarios"] | None = None
    package_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def retirement_metadata_is_complete(self):
        if self.retirable:
            if self.required_witness_reference != "review:operating_scenarios" or not self.package_sha256:
                raise ValueError("retirable limitation origins require reviewed operating metadata")
        elif self.required_witness_reference is not None or self.package_sha256 is not None:
            raise ValueError("protected limitation origins cannot carry retirement metadata")
        return self


@dataclass(frozen=True)
class OperatingScenarioResult:
    reviewed: bool
    limitations: tuple[str, ...]
    model_context: dict
    artifacts: dict[str, bytes]
    calculated_values: tuple[CalculatedValue, ...]
    limitation_origins: dict[str, list[dict[str, object]]] = field(default_factory=dict)


def operating_scenario_package_sha256(package: OperatingScenarioPackage) -> str:
    """Hash every package field except the attached independent review."""

    if not isinstance(package, OperatingScenarioPackage):
        raise TypeError("package must be an OperatingScenarioPackage")
    return digest(package.model_dump(mode="json", exclude={"review"}))


def _checked_contracts(package, case, snapshot):
    if not isinstance(package, OperatingScenarioPackage):
        raise TypeError("package must be an OperatingScenarioPackage")
    if not isinstance(case, FinancialCase):
        raise TypeError("case must be a FinancialCase")
    if not isinstance(snapshot, EvidenceSnapshot):
        raise TypeError("snapshot must be an EvidenceSnapshot")
    return (
        OperatingScenarioPackage.model_validate_json(package.model_dump_json(warnings="error")),
        FinancialCase.model_validate_json(case.model_dump_json(warnings="error")),
        EvidenceSnapshot.model_validate_json(snapshot.model_dump_json(warnings="error")),
    )


def _decimal_shape(value: Decimal, name: str) -> None:
    decimal_tuple = value.as_tuple()
    if (
        not value.is_finite()
        or len(decimal_tuple.digits) > 80
        or decimal_tuple.exponent < -30
        or decimal_tuple.exponent > 50
    ):
        raise ValueError(f"{name} is outside the bounded decimal domain")


def _sum_context(*values: Decimal) -> Context:
    minimum_exponent = min(value.as_tuple().exponent for value in values)
    aligned_digits = max(
        len(value.as_tuple().digits) + value.as_tuple().exponent - minimum_exponent
        for value in values
    )
    precision = max(80, aligned_digits + len(str(len(values))) + 1)
    return Context(prec=precision, Emin=-999999, Emax=999999)


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    coefficient_digits = len(left.as_tuple().digits) + len(right.as_tuple().digits)
    with localcontext(Context(prec=max(80, coefficient_digits), Emin=-999999, Emax=999999)):
        result = left * right
    if not result.is_finite() or result.copy_abs() > _MAX_RESULT:
        raise ValueError("operating-scenario multiplication exceeds the bounded domain")
    return result


def _exact_sum(*values: Decimal) -> Decimal:
    with localcontext(_sum_context(*values)):
        result = sum(values, Decimal(0))
    if not result.is_finite() or result.copy_abs() > _MAX_RESULT:
        raise ValueError("operating-scenario addition exceeds the bounded domain")
    return result


def _normalized_fact_value(fact: FinancialFact) -> Decimal:
    _decimal_shape(fact.value, f"fact {fact.id} value")
    _decimal_shape(fact.scale, f"fact {fact.id} scale")
    return _exact_product(fact.value, fact.scale)


def _eligible_source(source, snapshot: EvidenceSnapshot) -> bool:
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
    return bool(
        source.availability == "full_text"
        and source.published_at is not None
        and source.published_at <= snapshot.cutoff
        and (source.retrieved_at <= snapshot.cutoff or exact_late_sec_filing)
        and hashlib.sha256(source.content.encode("utf-8")).hexdigest() == source.content_sha256
    )


def _eligible_fact_ids(snapshot: EvidenceSnapshot, eligible_sources: set[str]) -> set[str]:
    eligible: set[str] = set()
    pending = list(snapshot.facts)
    while pending:
        ready = [
            fact
            for fact in pending
            if fact.source_id in eligible_sources and set(fact.inputs) <= eligible
        ]
        if not ready:
            break
        eligible.update(fact.id for fact in ready)
        pending = [fact for fact in pending if fact.id not in eligible]
    return eligible


def _validate_identity_and_evidence(
    package: OperatingScenarioPackage,
    case: FinancialCase,
    snapshot: EvidenceSnapshot,
) -> None:
    snapshot_hash = evidence_snapshot_sha256(snapshot)
    case_hash = digest(case.model_dump(mode="json"))
    if package.ticker != case.ticker or package.ticker != snapshot.ticker:
        raise ValueError("operating-scenario ticker differs from case or snapshot")
    if package.cutoff != case.cutoff or package.cutoff != snapshot.cutoff:
        raise ValueError("operating-scenario cutoff differs from case or snapshot")
    if case.snapshot_sha256 != snapshot_hash or package.evidence_sha256 != snapshot_hash:
        raise ValueError("operating-scenario evidence hash differs from the frozen snapshot")
    if package.case_sha256 != case_hash:
        raise ValueError("operating-scenario case hash differs from the supplied case")
    if package.historical_anchor.case_opening_date != case.opening_date:
        raise ValueError("historical anchor does not bind the observed case opening date")

    reconciliation = reconcile_financial_case(case, snapshot)
    if reconciliation.case_sha256 != case_hash or reconciliation.snapshot_sha256 != snapshot_hash:
        raise ValueError("operating-scenario case reconciliation identity mismatch")

    sources = {source.id: source for source in snapshot.sources}
    eligible_sources = {
        source_id for source_id, source in sources.items() if _eligible_source(source, snapshot)
    }
    facts = {fact.id: fact for fact in snapshot.facts}
    eligible_facts = _eligible_fact_ids(snapshot, eligible_sources)
    for copied in (
        package.historical_anchor.revenue_fact,
        package.historical_anchor.operating_income_fact,
    ):
        frozen = facts.get(copied.id)
        if frozen is None or copied.id not in eligible_facts:
            raise ValueError(f"historical anchor fact is not eligible: {copied.id}")
        if copied.model_dump(mode="json") != frozen.model_dump(mode="json"):
            raise ValueError(f"historical anchor fact differs from frozen evidence: {copied.id}")
        _normalized_fact_value(copied)

    if sum(len(item.text) for item in package.source_material) > _MAX_SOURCE_CHARS:
        raise ValueError("forecast source material exceeds the bounded text allowance")
    for item in package.source_material:
        source = sources.get(item.source_id)
        if (
            source is None
            or item.source_id not in eligible_sources
            or source.content_sha256 != item.source_sha256
            or item.end > len(source.content)
            or source.content[item.start : item.end] != item.text
        ):
            raise ValueError(
                f"forecast source material differs from eligible frozen text: {item.id}"
            )


def _validate_scenario_inputs(package: OperatingScenarioPackage) -> None:
    anchor = package.historical_anchor
    anchor_revenue = _normalized_fact_value(anchor.revenue_fact)
    if anchor_revenue < 0 or anchor_revenue > _MAX_AMOUNT:
        raise ValueError("historical revenue anchor is outside the bounded amount domain")
    if _normalized_fact_value(anchor.operating_income_fact).copy_abs() > _MAX_AMOUNT:
        raise ValueError("historical operating-income anchor is outside the bounded amount domain")

    for scenario in package.scenarios:
        expected_start = anchor.case_opening_date + timedelta(days=1)
        for period in scenario.periods:
            if period.period_start != expected_start:
                raise ValueError("future operating periods must be contiguous after the anchor")
            expected_start = period.period_end + timedelta(days=1)
            if period.accounting_basis != anchor.revenue_fact.basis:
                raise ValueError("future operating periods must retain the historical GAAP basis")
            assumptions = {
                "revenue": period.revenue,
                "gross_margin": period.gross_margin,
                "opex": period.opex,
            }
            if period.tax_rate is not None:
                assumptions["tax_rate"] = period.tax_rate
            for name, assumption in assumptions.items():
                _decimal_shape(assumption.value, f"{scenario.id}.{period.id}.{name}")
            if period.revenue.value < 0 or period.revenue.value > _MAX_AMOUNT:
                raise ValueError("scenario revenue must be nonnegative bounded unscaled USD")
            if period.opex.value < 0 or period.opex.value > _MAX_AMOUNT:
                raise ValueError("scenario opex must be nonnegative bounded unscaled USD")
            if not Decimal("-1") <= period.gross_margin.value <= Decimal(1):
                raise ValueError("scenario gross margin must be between -1 and 1")
            if period.tax_rate is not None and not Decimal(0) <= period.tax_rate.value <= Decimal(
                1
            ):
                raise ValueError("scenario tax rate must be between 0 and 1")
            gross_profit = _exact_product(period.revenue.value, period.gross_margin.value)
            _exact_sum(gross_profit, period.opex.value.copy_negate())
        if scenario.periods[-1].period_end != scenario.fiscal_year_end:
            raise ValueError(
                "a supplied fiscal-year boundary must equal the final future period end"
            )
        assert anchor.revenue_fact.period_start is not None
        fiscal_days = (scenario.fiscal_year_end - anchor.revenue_fact.period_start).days + 1
        if not 360 <= fiscal_days <= 371:
            raise ValueError("fiscal total requires an explicit 360-to-371-day fiscal year")


def _review_errors(package: OperatingScenarioPackage) -> tuple[str, ...]:
    review = package.review
    if review is None:
        return ("Independent operating-scenario review is absent.",)
    errors = []
    if review.reviewer_id == package.author_id:
        errors.append("Operating-scenario author and independent reviewer must differ.")
    if review.package_sha256 != operating_scenario_package_sha256(package):
        errors.append("Operating-scenario review package hash is stale or mismatched.")
    if review.case_sha256 != package.case_sha256:
        errors.append("Operating-scenario review case hash is mismatched.")
    if review.evidence_sha256 != package.evidence_sha256:
        errors.append("Operating-scenario review evidence hash is mismatched.")
    if review.reviewed_at < package.cutoff:
        errors.append("Operating-scenario review cannot predate the frozen evidence cutoff.")
    if any(finding.severity in {"warning", "critical"} for finding in review.findings):
        errors.append("Operating-scenario review contains unresolved warning or critical findings.")
    return tuple(errors)


def _limitation_origins(
    package: OperatingScenarioPackage,
    review: OperatingScenarioReview | None,
    review_errors: tuple[str, ...],
    *,
    reviewed: bool,
    package_sha256: str,
) -> dict[str, list[dict[str, object]]]:
    """Retain all sources for duplicate text; deterministic origins stay protected."""
    origins: dict[str, list[LimitationOrigin]] = {}

    def add(text: str, origin_id: str, *, retirable: bool) -> None:
        origins.setdefault(text, []).append(LimitationOrigin(
            origin_id=origin_id,
            retirable=retirable,
            required_witness_reference="review:operating_scenarios" if retirable else None,
            package_sha256=package_sha256 if retirable else None,
        ))

    if reviewed:
        for index, text in enumerate(package.limitations):
            add(text, f"operating.package.limitation.{index}", retirable=True)
    if review is not None and reviewed:
        for index, text in enumerate(review.limitations):
            add(text, f"operating.review.limitation.{index}", retirable=True)
        for index, finding in enumerate(review.findings):
            if finding.severity == "info":
                add(
                    f"Independent review finding [info] {finding.code}: {finding.message}",
                    f"operating.review.info.{index}",
                    retirable=True,
                )
    for index, text in enumerate(review_errors):
        add(text, f"operating.review.error.{index}", retirable=False)
    if not reviewed:
        add(_DRAFT_LIMITATION, "operating.review.draft", retirable=False)
    add(_BOUNDARY_LIMITATION, "operating.boundary", retirable=False)
    return {
        text: [record.model_dump(mode="json") for record in records]
        for text, records in origins.items()
    }


def _evidence_union(*values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in values for item in group))


def _snapshot_source_ids(
    package: OperatingScenarioPackage, material_ids: tuple[str, ...]
) -> tuple[str, ...]:
    source_by_material = {item.id: item.source_id for item in package.source_material}
    return tuple(dict.fromkeys(source_by_material[item] for item in material_ids))


def _numerical_outputs(package: OperatingScenarioPackage):
    scenarios = []
    anchor_revenue = _normalized_fact_value(package.historical_anchor.revenue_fact)
    anchor_operating_income = _normalized_fact_value(
        package.historical_anchor.operating_income_fact
    )
    for scenario in package.scenarios:
        periods = []
        for period in scenario.periods:
            gross_profit = _exact_product(period.revenue.value, period.gross_margin.value)
            operating_income = _exact_sum(gross_profit, period.opex.value.copy_negate())
            periods.append(
                {
                    "id": period.id,
                    "fiscal_label": period.fiscal_label,
                    "period_start": period.period_start,
                    "period_end": period.period_end,
                    "currency": period.currency,
                    "amount_scale": period.amount_scale,
                    "accounting_basis": period.accounting_basis,
                    "earnings_basis": period.earnings_basis,
                    "inputs": {
                        "revenue": period.revenue.model_dump(mode="json"),
                        "gross_margin": period.gross_margin.model_dump(mode="json"),
                        "opex": period.opex.model_dump(mode="json"),
                        "tax_rate": (
                            period.tax_rate.model_dump(mode="json")
                            if period.tax_rate is not None
                            else None
                        ),
                    },
                    "calculated": {
                        "gross_profit": gross_profit,
                        "operating_income": operating_income,
                    },
                }
            )
        fiscal_total = {
            "fiscal_label": f"FY ending {scenario.fiscal_year_end.isoformat()}",
            "period_start": package.historical_anchor.revenue_fact.period_start,
            "period_end": scenario.fiscal_year_end,
            "revenue": _exact_sum(
                anchor_revenue, *(period.revenue.value for period in scenario.periods)
            ),
            "operating_income": _exact_sum(
                anchor_operating_income,
                *(
                    _exact_sum(
                        _exact_product(period.revenue.value, period.gross_margin.value),
                        period.opex.value.copy_negate(),
                    )
                    for period in scenario.periods
                ),
            ),
        }
        scenarios.append(
            {
                "id": scenario.id,
                "label": scenario.label,
                "rationale": scenario.rationale,
                "rationale_evidence_ids": scenario.rationale_evidence_ids,
                "falsifier": scenario.falsifier,
                "falsifier_evidence_ids": scenario.falsifier_evidence_ids,
                "periods": periods,
                "fiscal_total": fiscal_total,
            }
        )
    return scenarios


def _calculated_values(package: OperatingScenarioPackage, scenarios) -> tuple[CalculatedValue, ...]:
    input_hash = operating_scenario_package_sha256(package)
    result_hash = digest(scenarios)
    values: list[CalculatedValue] = []

    def add(identifier, value, unit, evidence_ids, classification):
        values.append(
            CalculatedValue(
                id=identifier,
                value=value,
                unit=unit,
                currency="USD" if unit == "USD" else None,
                classification=classification,
                valuation_method="operating_scenario",
                share_count_basis="not_applicable",
                model_input_sha256=input_hash,
                model_result_sha256=result_hash,
                evidence_ids=evidence_ids,
            )
        )

    scenario_by_id = {item["id"]: item for item in scenarios}
    for scenario in package.scenarios:
        rendered = scenario_by_id[scenario.id]
        rendered_periods = {item["id"]: item for item in rendered["periods"]}
        for period in scenario.periods:
            prefix = f"operating_scenario.{scenario.id}.{period.id}"
            calculated = rendered_periods[period.id]["calculated"]
            add(
                f"{prefix}.revenue",
                period.revenue.value,
                "USD",
                _snapshot_source_ids(package, period.revenue.evidence_ids),
                "operating_scenario_assumption_not_reported_fact",
            )
            add(
                f"{prefix}.gross_margin",
                period.gross_margin.value,
                "fraction",
                _snapshot_source_ids(package, period.gross_margin.evidence_ids),
                "operating_scenario_assumption_not_reported_fact",
            )
            add(
                f"{prefix}.gross_profit",
                calculated["gross_profit"],
                "USD",
                _snapshot_source_ids(
                    package,
                    _evidence_union(period.revenue.evidence_ids, period.gross_margin.evidence_ids),
                ),
                "conditional_operating_scenario_calculation_not_reported_fact",
            )
            add(
                f"{prefix}.opex",
                period.opex.value,
                "USD",
                _snapshot_source_ids(package, period.opex.evidence_ids),
                "operating_scenario_assumption_not_reported_fact",
            )
            add(
                f"{prefix}.operating_income",
                calculated["operating_income"],
                "USD",
                _snapshot_source_ids(
                    package,
                    _evidence_union(
                        period.revenue.evidence_ids,
                        period.gross_margin.evidence_ids,
                        period.opex.evidence_ids,
                    ),
                ),
                "conditional_operating_scenario_calculation_not_reported_fact",
            )
        evidence_ids = _evidence_union(
            (package.historical_anchor.revenue_fact.id,),
            (package.historical_anchor.operating_income_fact.id,),
            _snapshot_source_ids(
                package,
                _evidence_union(
                    *(
                        _evidence_union(
                            period.revenue.evidence_ids,
                            period.gross_margin.evidence_ids,
                            period.opex.evidence_ids,
                        )
                        for period in scenario.periods
                    ),
                ),
            ),
        )
        add(
            f"operating_scenario.{scenario.id}.fiscal_total.revenue",
            rendered["fiscal_total"]["revenue"],
            "USD",
            evidence_ids,
            "conditional_operating_scenario_calculation_not_reported_fact",
        )
        add(
            f"operating_scenario.{scenario.id}.fiscal_total.operating_income",
            rendered["fiscal_total"]["operating_income"],
            "USD",
            evidence_ids,
            "conditional_operating_scenario_calculation_not_reported_fact",
        )
    return tuple(values)


def evaluate_operating_scenarios(
    package: OperatingScenarioPackage,
    case: FinancialCase,
    snapshot: EvidenceSnapshot,
) -> OperatingScenarioResult:
    """Revalidate and conditionally expose operating-only numerical scenarios."""

    package, case, snapshot = _checked_contracts(package, case, snapshot)
    _validate_identity_and_evidence(package, case, snapshot)
    _validate_scenario_inputs(package)
    review_errors = _review_errors(package)
    reviewed = not review_errors
    review = package.review
    package_hash = operating_scenario_package_sha256(package)
    limitations = tuple(
        dict.fromkeys(
            (
                *(package.limitations if reviewed else ()),
                *(review.limitations if reviewed and review is not None else ()),
                *(
                    f"Independent review finding [info] {finding.code}: {finding.message}"
                    for finding in (review.findings if reviewed and review is not None else ())
                    if finding.severity == "info"
                ),
                *review_errors,
                *(() if reviewed else (_DRAFT_LIMITATION,)),
                _BOUNDARY_LIMITATION,
            )
        )
    )
    limitation_origins = _limitation_origins(
        package, review, review_errors, reviewed=reviewed, package_sha256=package_hash,
    )
    if reviewed:
        scenarios = _numerical_outputs(package)
        calculated_values = _calculated_values(package, scenarios)
        model_context = {
            "schema_version": 1,
            "context_kind": "reviewed_conditional_operating_scenarios",
            "reviewed": True,
            "decision": "conditional_operating_scenarios",
            "package_sha256": package_hash,
            "case_sha256": package.case_sha256,
            "evidence_sha256": package.evidence_sha256,
            "date_convention": package.date_convention,
            "temporal_basis": {
                "observed_case_opening_date": package.historical_anchor.case_opening_date,
                "evidence_cutoff": package.cutoff,
                "cutoff_is_not_a_rolled_historical_anchor": True,
            },
            "historical_anchor": {
                "fiscal_label": package.historical_anchor.fiscal_label,
                "period_start": package.historical_anchor.revenue_fact.period_start,
                "period_end": package.historical_anchor.revenue_fact.period_end,
                "currency": "USD",
                "accounting_basis": package.historical_anchor.revenue_fact.basis,
                "revenue": _normalized_fact_value(package.historical_anchor.revenue_fact),
                "operating_income": _normalized_fact_value(
                    package.historical_anchor.operating_income_fact
                ),
                "fact_ids": (
                    package.historical_anchor.revenue_fact.id,
                    package.historical_anchor.operating_income_fact.id,
                ),
            },
            "source_material": [item.model_dump(mode="json") for item in package.source_material],
            "review": review.model_dump(mode="json") if review is not None else None,
            "scenarios": scenarios,
            "limitations": limitations,
        }
    else:
        calculated_values = ()
        model_context = {
            "schema_version": 1,
            "context_kind": "operating_scenario_audit_only",
            "reviewed": False,
            "audit": {
                "classification": "draft_inputs_not_for_numeric_presentation",
                "package_sha256": package_hash,
                "case_sha256": package.case_sha256,
                "evidence_sha256": package.evidence_sha256,
                "scenario_ids": [scenario.id for scenario in package.scenarios],
                "review_present": review is not None,
                "review_errors": review_errors,
            },
            "limitations": limitations,
        }

    artifacts = {
        "operating_scenario_package.json": canonical_json(package),
        "operating_scenario_context.json": canonical_json(model_context),
    }
    if review is not None:
        artifacts["operating_scenario_review.json"] = canonical_json(review)
    if reviewed:
        artifacts["operating_scenario_calculated_values.json"] = canonical_json(calculated_values)
    return OperatingScenarioResult(
        reviewed=reviewed,
        limitations=limitations,
        model_context=model_context,
        artifacts=artifacts,
        calculated_values=calculated_values,
        limitation_origins=limitation_origins,
    )
