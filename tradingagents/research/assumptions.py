"""Offline, bounded FCFF assumption-package contracts.

An assumption package separates eligible historical facts, unavailable external
inputs, analyst choices, and model conventions.  Validation establishes
provenance and bounded model-input readiness only; it never accepts an
investment recommendation or claims that an assumption is economically true.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from tradingagents.research.contracts import (
    Contract,
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
)
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.storage import parse_json

AssumptionCategory = Literal[
    "historical_anchor",
    "external_input",
    "analyst_assumption",
    "model_convention",
]
AssumptionStatus = Literal["missing", "draft", "reviewed"]
ValuationMethod = Literal["fcff"]

ASSUMPTION_PACKAGE_SCOPE = (
    "Prepared FCFF model inputs only; not an accepted investment recommendation."
)

_OPENING_PATHS: dict[str, str] = {
    "current_revenue": "revenue",
    "current_working_capital": "working_capital",
    "net_debt": "net_debt",
    "current_diluted_shares": "diluted_shares",
}
_EXTERNAL_PATHS = {"discount_rate", "terminal_growth"}
_PERIOD_NUMERIC_PATHS = {
    "periods.*.revenue_growth",
    "periods.*.operating_margin",
    "periods.*.tax_rate",
    "periods.*.depreciation_amortization_pct_revenue",
    "periods.*.capex_pct_revenue",
    "periods.*.working_capital_pct_revenue",
    "periods.*.sbc_pct_revenue",
}
_CONVENTION_PATHS = {
    "as_of_date",
    "units.currency",
    "units.amount_scale",
    "units.share_scale",
    "periods.*.operating_margin_basis",
    "periods.*.external_funding_required",
    "forecast_schedule",
}
_NUMERIC_PATHS = set(_OPENING_PATHS) | _EXTERNAL_PATHS | _PERIOD_NUMERIC_PATHS
REQUIRED_FCFF_PATHS = frozenset(_NUMERIC_PATHS | _CONVENTION_PATHS)

_MAX_AMOUNT = Decimal("1e50")
_ONE = Decimal("1")
_RATE_BOUNDS: dict[str, tuple[Decimal, Decimal, bool]] = {
    "discount_rate": (Decimal("0"), _ONE, True),
    "terminal_growth": (Decimal("-0.999999"), Decimal("0.25"), False),
    "periods.*.revenue_growth": (Decimal("-0.999999"), Decimal("10"), False),
    "periods.*.operating_margin": (Decimal("-10"), _ONE, False),
    "periods.*.tax_rate": (Decimal("0"), _ONE, False),
    "periods.*.depreciation_amortization_pct_revenue": (
        Decimal("0"),
        Decimal("10"),
        False,
    ),
    "periods.*.capex_pct_revenue": (Decimal("0"), Decimal("10"), False),
    "periods.*.working_capital_pct_revenue": (
        Decimal("-10"),
        Decimal("10"),
        False,
    ),
    "periods.*.sbc_pct_revenue": (Decimal("0"), _ONE, False),
}


class AssumptionRange(Contract):
    """A finite, ordered low/base/high numeric assumption."""

    low: Decimal
    base: Decimal
    high: Decimal

    @model_validator(mode="after")
    def finite_and_ordered(self):
        if not all(value.is_finite() for value in (self.low, self.base, self.high)):
            raise ValueError("assumption range values must be finite")
        if not self.low <= self.base <= self.high:
            raise ValueError("assumption range must satisfy low <= base <= high")
        return self


class AssumptionEntry(Contract):
    """One explicitly classified FCFF input or convention."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:/*-]+$")
    model_path: str
    category: AssumptionCategory
    status: AssumptionStatus
    unit: str
    rationale: str
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    value: str | None = None
    range: AssumptionRange | None = None
    fact_id: str | None = None

    @field_validator("model_path", "unit", "rationale")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("assumption text fields must be nonblank")
        return value

    @field_validator("evidence_ids", "limitations")
    @classmethod
    def nonblank_unique_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("assumption identifiers and limitations must be nonblank")
        if len(values) != len(set(values)):
            raise ValueError("assumption identifiers and limitations must be unique")
        return values

    @model_validator(mode="after")
    def valid_shape_and_classification(self):
        if self.model_path not in REQUIRED_FCFF_PATHS:
            raise ValueError(f"unknown FCFF model path: {self.model_path}")
        if self.model_path in _OPENING_PATHS and self.category != "historical_anchor":
            raise ValueError("opening model paths must be historical_anchor entries")
        if self.model_path in _CONVENTION_PATHS and self.category != "model_convention":
            raise ValueError("convention model paths must be model_convention entries")
        if self.model_path in (_EXTERNAL_PATHS | _PERIOD_NUMERIC_PATHS) and self.category not in {
            "external_input",
            "analyst_assumption",
        }:
            raise ValueError("forecast model paths must identify external or analyst inputs")
        if self.model_path in (_EXTERNAL_PATHS | _PERIOD_NUMERIC_PATHS) and self.unit != "fraction":
            raise ValueError("nonhistorical numeric assumptions must use fraction units")

        if self.status == "missing":
            if self.value is not None or self.range is not None or self.fact_id is not None:
                raise ValueError("missing assumptions cannot contain value, range, or fact_id")
            return self

        if self.category == "model_convention":
            if self.value is None or not self.value.strip() or self.range is not None:
                raise ValueError("nonmissing model conventions require value and no range")
        else:
            if self.range is None or self.value is not None:
                raise ValueError("nonmissing numeric assumptions require range and no value")

        if self.category == "historical_anchor":
            if self.fact_id is None or self.fact_id not in self.evidence_ids:
                raise ValueError("historical anchors require a referenced fact_id")
        elif self.fact_id is not None:
            raise ValueError("nonhistorical assumptions cannot contain fact_id")

        if self.category in {"external_input", "analyst_assumption"} and not self.evidence_ids:
            raise ValueError("nonmissing external and analyst assumptions require evidence_ids")
        return self


class AssumptionPackage(Contract):
    """A hash-bound FCFF assumption packet, which may deliberately be incomplete."""

    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,19}$")
    cutoff: AwareDatetime
    created_at: AwareDatetime
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    valuation_method: ValuationMethod
    value_basis: Literal["normalized_base_units"] = "normalized_base_units"
    entries: tuple[AssumptionEntry, ...]
    limitations: tuple[str, ...]
    reviewer: str | None = None
    reviewed_at: AwareDatetime | None = None

    @field_validator("entries")
    @classmethod
    def entries_nonempty(cls, entries: tuple[AssumptionEntry, ...]) -> tuple[AssumptionEntry, ...]:
        if not entries:
            raise ValueError("assumption package entries must be nonempty")
        return entries

    @field_validator("limitations")
    @classmethod
    def limitations_nonempty(cls, limitations: tuple[str, ...]) -> tuple[str, ...]:
        if not limitations or any(not limitation.strip() for limitation in limitations):
            raise ValueError("assumption package limitations must be nonempty and nonblank")
        return limitations

    @field_validator("reviewer")
    @classmethod
    def reviewer_nonblank(cls, reviewer: str | None) -> str | None:
        if reviewer is not None and not reviewer.strip():
            raise ValueError("reviewer must be nonblank when supplied")
        return reviewer

    @model_validator(mode="after")
    def unique_entries(self):
        ids = [entry.id for entry in self.entries]
        paths = [entry.model_path for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("assumption entry IDs must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("assumption model paths must be unique")
        return self


def _validated_contracts(
    package: AssumptionPackage,
    snapshot: EvidenceSnapshot,
) -> tuple[AssumptionPackage, EvidenceSnapshot]:
    try:
        checked_package = AssumptionPackage.model_validate_json(package.model_dump_json())
        checked_snapshot = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json())
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"assumption package contract validation failed: {exc}") from exc
    return checked_package, checked_snapshot


def _parse_evidence_bytes(evidence_bytes: bytes) -> EvidenceSnapshot:
    if not isinstance(evidence_bytes, bytes):
        raise ValueError("evidence_bytes must be exact raw bytes")
    try:
        return EvidenceSnapshot.model_validate(parse_json(evidence_bytes))
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("evidence_bytes must contain the supplied evidence snapshot") from exc


def _validate_range_bounds(entry: AssumptionEntry) -> None:
    if entry.range is None:
        return
    values = (entry.range.low, entry.range.base, entry.range.high)
    if any(abs(value) > _MAX_AMOUNT for value in values):
        raise ValueError(f"assumption range exceeds the bounded numeric domain: {entry.model_path}")
    if entry.model_path in {"current_revenue", "current_diluted_shares"} and entry.range.low <= 0:
        raise ValueError(f"{entry.model_path} must be positive across its range")
    bounds = _RATE_BOUNDS.get(entry.model_path)
    if bounds is None:
        return
    low, high, strict_low = bounds
    if entry.range.low < low or entry.range.high > high:
        raise ValueError(f"assumption range is outside FCFF bounds: {entry.model_path}")
    if strict_low and entry.range.low <= low:
        raise ValueError(f"assumption range must be above {low}: {entry.model_path}")


def _expected_historical_fact(
    entry: AssumptionEntry,
    facts: dict[str, FinancialFact],
    request: ResearchRequest,
    cutoff_date,
) -> FinancialFact:
    assert entry.fact_id is not None and entry.range is not None
    fact = facts.get(entry.fact_id)
    if fact is None:
        raise ValueError(f"historical fact_id is not eligible: {entry.fact_id}")
    if set(fact.inputs) - set(entry.evidence_ids):
        raise ValueError(f"derived historical fact ancestry is not referenced: {entry.model_path}")

    expected_metric = _OPENING_PATHS[entry.model_path]
    share_proxy = (
        entry.model_path == "current_diluted_shares"
        and request.share_count_basis == "latest_quarter_diluted_proxy"
    )
    if share_proxy:
        expected_metric = "weighted_average_diluted_shares"
    if fact.metric != expected_metric:
        raise ValueError(f"historical fact metric does not match {entry.model_path}")
    if fact.segment is not None or fact.basis not in {"US GAAP", "IFRS"}:
        raise ValueError(f"historical fact scope or basis does not match {entry.model_path}")

    if entry.model_path == "current_diluted_shares":
        if fact.unit != "shares" or fact.currency is not None or entry.unit != "shares":
            raise ValueError("current_diluted_shares requires an unscaled shares fact")
    elif fact.currency is None or fact.unit != fact.currency or entry.unit != fact.currency:
        raise ValueError(f"historical monetary unit does not match {entry.model_path}")

    if entry.range.low != fact.normalized_value or entry.range.high != fact.normalized_value:
        raise ValueError(f"historical range must equal normalized fact value: {entry.model_path}")

    age_days = (cutoff_date - fact.period_end).days
    if age_days < 0 or age_days > 120:
        raise ValueError(f"historical fact is outside the opening-date window: {entry.model_path}")
    if entry.model_path == "current_revenue":
        if fact.period_type != "duration" or fact.period_start is None:
            raise ValueError("current_revenue requires an annual duration fact")
        duration = (fact.period_end - fact.period_start).days + 1
        if not 360 <= duration <= 371:
            raise ValueError("current_revenue requires an annual duration fact")
    elif share_proxy:
        if fact.period_type != "duration" or fact.period_start is None:
            raise ValueError("latest-quarter diluted-share proxy requires a duration fact")
        duration = (fact.period_end - fact.period_start).days + 1
        if not 75 <= duration <= 105:
            raise ValueError("latest-quarter diluted-share proxy requires a quarterly fact")
        newer = [
            candidate
            for candidate in facts.values()
            if candidate.metric == expected_metric
            and candidate.basis == fact.basis
            and candidate.segment is None
            and candidate.unit == "shares"
            and candidate.period_end <= cutoff_date
            and candidate.period_end > fact.period_end
            and candidate.period_type == "duration"
            and candidate.period_start is not None
            and 75 <= (candidate.period_end - candidate.period_start).days + 1 <= 105
        ]
        if newer:
            raise ValueError("diluted-share proxy is not the latest eligible quarter")
    elif fact.period_type != "instant":
        raise ValueError(f"{entry.model_path} requires an instant fact")
    return fact


def _parse_positive_scale(entry: AssumptionEntry) -> Decimal:
    assert entry.value is not None
    try:
        value = Decimal(entry.value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid scale convention: {entry.model_path}") from exc
    if not value.is_finite() or value <= 0 or value > _MAX_AMOUNT:
        raise ValueError(f"invalid scale convention: {entry.model_path}")
    return value


def _validate_conventions(
    entries: dict[str, AssumptionEntry],
    request: ResearchRequest,
    historical_facts: tuple[FinancialFact, ...],
) -> None:
    as_of = entries.get("as_of_date")
    if as_of is not None and as_of.status != "missing":
        expected = request.cutoff.astimezone(ZoneInfo(request.timezone)).date().isoformat()
        if as_of.value != expected:
            raise ValueError("as_of_date convention must equal the request cutoff local date")

    currencies = {fact.currency for fact in historical_facts if fact.currency is not None}
    currency = entries.get("units.currency")
    if currency is not None and currency.status != "missing":
        expected_currency = (
            request.instrument.reporting_currency
            if request.instrument is not None
            else next(iter(currencies), None)
        )
        if expected_currency is not None and currency.value != expected_currency:
            raise ValueError("units.currency does not match eligible historical evidence")
    if len(currencies) > 1:
        raise ValueError("historical monetary anchors use inconsistent currencies")

    for path in ("units.amount_scale", "units.share_scale"):
        scale = entries.get(path)
        if scale is not None and scale.status != "missing":
            _parse_positive_scale(scale)

    margin_basis = entries.get("periods.*.operating_margin_basis")
    if (
        margin_basis is not None
        and margin_basis.status != "missing"
        and margin_basis.value not in {"after_sbc", "before_sbc"}
    ):
        raise ValueError("unsupported operating_margin_basis convention")

    funding = entries.get("periods.*.external_funding_required")
    if (
        funding is not None
        and funding.status != "missing"
        and funding.value not in {"true", "false"}
    ):
        raise ValueError("external_funding_required convention must be true or false")


def validate_assumption_package(
    package: AssumptionPackage,
    snapshot: EvidenceSnapshot,
    request: ResearchRequest,
    evidence_bytes: bytes,
) -> AssumptionPackage:
    """Validate an FCFF packet against exact raw evidence and request identity."""

    package, supplied_snapshot = _validated_contracts(package, snapshot)
    if request.valuation_method != "fcff":
        raise ValueError("assumption packages currently support FCFF only")
    if package.valuation_method != request.valuation_method:
        raise ValueError("assumption package valuation_method must match the request")

    parsed_snapshot = _parse_evidence_bytes(evidence_bytes)
    if parsed_snapshot != supplied_snapshot:
        raise ValueError("evidence_bytes parsed snapshot does not match the supplied snapshot")
    if hashlib.sha256(evidence_bytes).hexdigest() != package.evidence_sha256:
        raise ValueError("assumption package evidence_sha256 does not match raw evidence bytes")

    validated_snapshot = validate_snapshot(parsed_snapshot, request)
    if package.ticker != request.ticker or package.ticker != validated_snapshot.ticker:
        raise ValueError("assumption package ticker must match request and evidence")
    if package.cutoff != request.cutoff or package.cutoff != validated_snapshot.cutoff:
        raise ValueError("assumption package cutoff must match request and evidence")

    now = datetime.now(timezone.utc)
    if package.created_at > now:
        raise ValueError("assumption package created_at cannot be in the future")
    if package.reviewed_at is not None and package.reviewed_at > now:
        raise ValueError("assumption package reviewed_at cannot be in the future")
    reviewed_entries = [entry for entry in package.entries if entry.status == "reviewed"]
    if reviewed_entries:
        if package.reviewer is None or package.reviewed_at is None:
            raise ValueError("reviewed entries require package reviewer and reviewed_at")
        if package.reviewed_at < package.created_at:
            raise ValueError("reviewed_at cannot precede created_at")

    eligible_source_ids = {
        source.id for source in validated_snapshot.sources if source.published_at is not None
    }
    eligible_ids = eligible_source_ids | {
        record.id
        for record in (
            *validated_snapshot.facts,
            *validated_snapshot.events,
            *validated_snapshot.expectations,
        )
    }
    for entry in package.entries:
        unknown = set(entry.evidence_ids) - eligible_ids
        if unknown:
            raise ValueError(
                f"assumption entry references ineligible evidence IDs: {entry.model_path}"
            )
        _validate_range_bounds(entry)

    facts = {fact.id: fact for fact in validated_snapshot.facts}
    entries = {entry.model_path: entry for entry in package.entries}
    cutoff_date = request.cutoff.astimezone(ZoneInfo(request.timezone)).date()
    historical_facts: list[FinancialFact] = []
    for path in _OPENING_PATHS:
        entry = entries.get(path)
        if entry is None or entry.status == "missing":
            continue
        historical_facts.append(
            _expected_historical_fact(entry, facts, request, cutoff_date)
        )
    if historical_facts:
        if len({fact.period_end for fact in historical_facts}) != 1:
            raise ValueError("historical opening anchors require a common period end")
        if len({fact.basis for fact in historical_facts}) != 1:
            raise ValueError("historical opening anchors require a common accounting basis")

    _validate_conventions(entries, request, tuple(historical_facts))

    discount = entries.get("discount_rate")
    terminal = entries.get("terminal_growth")
    if (
        discount is not None
        and terminal is not None
        and discount.status == terminal.status == "reviewed"
        and discount.range is not None
        and terminal.range is not None
        and discount.range.low <= terminal.range.high
    ):
        raise ValueError(
            "reviewed discount_rate range must exceed the entire terminal_growth range"
        )
    return package


def assumption_blockers(package: AssumptionPackage) -> tuple[str, ...]:
    """Return structural-review blockers without asserting executable-model readiness.

    A package with no blockers has reviewed coverage of the required paths only.
    It is not an assembled dated FCFF model, a valuation, or an investment view.
    Disclosed caveats remain limitations rather than automatic structural blockers.
    """

    entries = {entry.model_path: entry for entry in package.entries}
    blockers: list[str] = []
    for path in sorted(REQUIRED_FCFF_PATHS):
        entry = entries.get(path)
        if entry is None:
            blockers.append(f"missing required path: {path}")
        elif entry.status == "missing":
            blockers.append(f"missing required assumption: {path}")
        elif entry.status == "draft":
            blockers.append(f"draft required assumption: {path}")
    if any(entry.status == "reviewed" for entry in package.entries):
        if package.reviewer is None:
            blockers.append("missing review metadata: reviewer")
        if package.reviewed_at is None:
            blockers.append("missing review metadata: reviewed_at")
    return tuple(blockers)
