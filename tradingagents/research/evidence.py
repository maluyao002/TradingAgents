"""Offline, point-in-time evidence validation and normalization helpers.

These functions accept already-acquired payloads only.  They intentionally do
not fetch data, infer publication times, or silently convert accounting units.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, Inexact, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchEvent,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.sources import is_exact_sec_archive_filing_url
from tradingagents.research.storage import read_json

_AWARE_DATETIME = TypeAdapter(AwareDatetime)
_METRICS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "Revenue",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
}
_BASIS_BY_NAMESPACE = {"us-gaap": "US GAAP", "ifrs-full": "IFRS"}
_DERIVED_DECIMAL_PRECISION = 50


@dataclass(frozen=True)
class NormalizedNews:
    """News retained after cutoff/relevance/deduplication, plus coverage caveats."""

    sources: tuple[SourceDocument, ...]
    events: tuple[ResearchEvent, ...]
    gaps: tuple[str, ...]


def _cutoff_local_date(cutoff: datetime, timezone: str) -> date:
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    return cutoff.astimezone(ZoneInfo(timezone)).date()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _critical(message: str) -> str:
    return f"critical: {message}"


def validate_snapshot(snapshot: EvidenceSnapshot, request: ResearchRequest) -> EvidenceSnapshot:
    """Apply identity and point-in-time policy to a validated snapshot.

    Mutable content retrieved after the cutoff is rejected.  A later retrieval is
    allowed only for an accession-bound SEC filing at its exact public archive
    path.  Unknown-publication sources remain metadata with explicit gaps, while
    their dependent facts, events, and expectations are removed.
    """
    try:
        snapshot = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json())
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"evidence snapshot contract validation failed: {exc}") from exc
    if snapshot.ticker != request.ticker:
        raise ValueError("evidence snapshot ticker must exactly match the request")
    if snapshot.cutoff != request.cutoff:
        raise ValueError("evidence snapshot cutoff must exactly match the request")
    if request.instrument is not None and snapshot.instrument != request.instrument:
        raise ValueError("evidence snapshot instrument must exactly match the request")

    cutoff_date = _cutoff_local_date(request.cutoff, request.timezone)
    if (
        snapshot.instrument is not None
        and snapshot.instrument.adr_ratio_effective_at is not None
        and snapshot.instrument.adr_ratio_effective_at > cutoff_date
    ):
        raise ValueError("evidence snapshot ADR ratio is not effective at the cutoff")
    gaps = list(snapshot.gaps)
    ineligible_source_ids: set[str] = set()
    for source in snapshot.sources:
        if source.content_sha256 != _content_hash(source.content):
            raise ValueError(f"source content hash mismatch: {source.id}")
        immutable_sec_filing = bool(
            request.instrument is not None
            and source.kind == "filing"
            and source.accession is not None
            and is_exact_sec_archive_filing_url(
                source.url,
                cik=request.instrument.cik,
                accession=source.accession,
            )
        )
        if source.retrieved_at > request.cutoff and not immutable_sec_filing:
            raise ValueError(f"mutable source was retrieved after the evidence cutoff: {source.id}")
        if source.published_at is None:
            ineligible_source_ids.add(source.id)
            gaps.append(_critical(f"source {source.id} has unknown publication time"))

    facts: list[FinancialFact] = []
    for fact in snapshot.facts:
        if fact.period_end > cutoff_date:
            raise ValueError(f"fact period is after cutoff local date: {fact.id}")
        if fact.source_id in ineligible_source_ids:
            gaps.append(_critical(f"fact {fact.id} lacks historically eligible source evidence"))
            continue
        facts.append(fact)

    events: list[ResearchEvent] = []
    for event in snapshot.events:
        if event.source_id in ineligible_source_ids:
            gaps.append(_critical(f"event {event.id} lacks historically eligible source evidence"))
            continue
        events.append(event)

    expectations = []
    for expectation in snapshot.expectations:
        if set(expectation.source_ids) & ineligible_source_ids:
            gaps.append(
                _critical(
                    f"expectation {expectation.id} lacks historically eligible source evidence"
                )
            )
            continue
        expectations.append(expectation)

    return EvidenceSnapshot(
        ticker=snapshot.ticker,
        cutoff=snapshot.cutoff,
        sources=snapshot.sources,
        facts=tuple(facts),
        events=tuple(events),
        expectations=tuple(expectations),
        gaps=tuple(dict.fromkeys(gaps)),
        instrument=snapshot.instrument,
    )


def load_snapshot(path: Path, request: ResearchRequest) -> EvidenceSnapshot:
    """Load bounded JSON, validate its contract, then apply point-in-time policy."""

    try:
        payload = read_json(path)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("evidence snapshot must be readable bounded JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("evidence snapshot must be a JSON object")

    try:
        snapshot = EvidenceSnapshot.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"evidence snapshot contract validation failed: {exc}") from exc
    return validate_snapshot(snapshot, request)


def _accession_source(
    accession: object,
    sources: Mapping[str, SourceDocument],
    accession_source_ids: Mapping[str, str],
) -> SourceDocument | None:
    if not isinstance(accession, str):
        return None
    source_id = accession_source_ids.get(accession)
    return sources.get(source_id) if source_id is not None else None


def _fact_id(metric: str, accession: str, start: date | None, end: date, unit: str) -> str:
    start_text = start.isoformat() if start else "instant"
    return f"sec:{metric}:{accession}:{start_text}:{end.isoformat()}:{unit}"


def normalize_company_facts(
    payload: Mapping[str, Any],
    ticker: str,
    cutoff: datetime,
    *,
    sources: Mapping[str, SourceDocument],
    accession_source_ids: Mapping[str, str],
) -> tuple[FinancialFact, ...]:
    """Normalize whole-entity SEC companyfacts using only explicit source mappings.

    The function retains each eligible accession rather than choosing a latest
    restatement. It preserves SEC start/end dates as reported and does not infer
    a Q4 duration.  `sources` and `accession_source_ids` are mandatory so every
    retained fact has a traceable immutable source.
    """
    if not sources or not accession_source_ids:
        raise ValueError("explicit sources and accession_source_ids are required")
    if payload.get("ticker") not in (None, ticker):
        raise ValueError("companyfacts ticker does not match requested ticker")
    facts_root = payload.get("facts")
    if not isinstance(facts_root, Mapping):
        raise ValueError("companyfacts payload requires a facts object")

    records: list[FinancialFact] = []
    seen: dict[str, tuple[Decimal, Decimal]] = {}
    for namespace, basis in _BASIS_BY_NAMESPACE.items():
        taxonomy = facts_root.get(namespace)
        if not isinstance(taxonomy, Mapping):
            continue
        for metric, concepts in _METRICS.items():
            concept_name = next((name for name in concepts if name in taxonomy), None)
            concept = taxonomy.get(concept_name) if concept_name is not None else None
            if not isinstance(concept, Mapping) or not isinstance(concept.get("units"), Mapping):
                continue
            for unit, observations in concept["units"].items():
                if not isinstance(unit, str) or not isinstance(observations, Sequence):
                    continue
                for observation in observations:
                    if (
                        not isinstance(observation, Mapping)
                        or observation.get("segment")
                        or observation.get("dim")
                    ):
                        continue
                    accession = observation.get("accn")
                    source = _accession_source(accession, sources, accession_source_ids)
                    if source is None or source.published_at is None:
                        continue
                    if source.accession is not None and source.accession != accession:
                        raise ValueError("accession mapping disagrees with source accession")
                    if source.published_at > cutoff:
                        continue
                    try:
                        end = date.fromisoformat(str(observation["end"]))
                        start = (
                            date.fromisoformat(str(observation["start"]))
                            if observation.get("start")
                            else None
                        )
                        value = Decimal(str(observation["val"]))
                        scale = Decimal(str(observation.get("scale", 1)))
                    except (KeyError, ValueError, InvalidOperation):
                        continue
                    if end > cutoff.date() or (start is not None and start > end):
                        continue
                    identifier = _fact_id(metric, accession, start, end, unit)
                    source_value = (value, scale)
                    if identifier in seen:
                        if seen[identifier] != source_value:
                            raise ValueError(
                                "conflicting companyfacts values for the same accession period"
                            )
                        continue
                    seen[identifier] = source_value
                    records.append(
                        FinancialFact(
                            id=identifier,
                            source_id=source.id,
                            metric=metric,
                            value=value,
                            unit=unit,
                            currency=unit if len(unit) == 3 and unit.isalpha() else None,
                            scale=scale,
                            period_start=start,
                            period_end=end,
                            period_type="duration" if start is not None else "instant",
                            basis=basis,
                            location=f"SEC companyfacts {namespace}:{concept_name} {accession}",
                        )
                    )
    return tuple(records)


def derive_quarter(current_ytd: FinancialFact, previous_ytd: FinancialFact) -> FinancialFact:
    """Subtract contiguous YTD facts while retaining both operands and provenance."""
    comparable = ("metric", "basis", "currency", "unit", "scale", "period_start", "segment")
    if any(getattr(current_ytd, field) != getattr(previous_ytd, field) for field in comparable):
        raise ValueError(
            "YTD facts must share metric, basis, currency, unit, scale, segment, and fiscal-year start"
        )
    if current_ytd.period_start is None or previous_ytd.period_start is None:
        raise ValueError("YTD facts require fiscal-year period starts")
    if current_ytd.period_type != "duration" or previous_ytd.period_type != "duration":
        raise ValueError("YTD facts must be duration facts")
    if previous_ytd.period_end >= current_ytd.period_end:
        raise ValueError("previous YTD period must end before current YTD period")
    quarter_start = previous_ytd.period_end + timedelta(days=1)
    if quarter_start <= current_ytd.period_start:
        raise ValueError("YTD periods are not contiguous fiscal-year observations")
    duration_days = (current_ytd.period_end - quarter_start).days + 1
    if not 70 <= duration_days <= 105:
        raise ValueError("derived quarter duration must be 70-105 days")
    identifier = (
        "derived:" + hashlib.sha256(f"{current_ytd.id}\x00{previous_ytd.id}".encode()).hexdigest()
    )
    try:
        with localcontext(Context(prec=_DERIVED_DECIMAL_PRECISION)) as context:
            context.traps[Inexact] = True
            derived_value = current_ytd.value - previous_ytd.value
    except Inexact as exc:
        raise ValueError(
            f"derived quarter exceeds {_DERIVED_DECIMAL_PRECISION}-digit precision"
        ) from exc
    return FinancialFact(
        id=identifier,
        source_id=current_ytd.source_id,
        metric=current_ytd.metric,
        value=derived_value,
        unit=current_ytd.unit,
        currency=current_ytd.currency,
        scale=current_ytd.scale,
        period_start=quarter_start,
        period_end=current_ytd.period_end,
        period_type="duration",
        basis=current_ytd.basis,
        location=f"derived from {current_ytd.id} and {previous_ytd.id}",
        inputs=(current_ytd.id, previous_ytd.id),
        formula=f"{current_ytd.id} - {previous_ytd.id}",
    )


def _news_origin(record: Mapping[str, Any]) -> str | None:
    origin = record.get("origin_id") or record.get("url")
    if not isinstance(origin, str) or not origin:
        return None
    return f"origin:{hashlib.sha256(origin.encode('utf-8')).hexdigest()[:32]}"


def _news_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return _AWARE_DATETIME.validate_python(value)
    except ValidationError:
        return None


def normalize_news(
    records: Sequence[Mapping[str, Any]], ticker: str, cutoff: datetime, *, limit: int | None = None
) -> NormalizedNews:
    """Normalize already-fetched news with cutoff-first origin deduplication.

    Entity relevance requires an explicit ticker in `entities`; title matching is
    deliberately avoided. An empty result is a coverage gap, never proof that no
    event occurred.
    """
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")

    eligible: list[tuple[Mapping[str, Any], str, datetime, datetime]] = []
    gaps: list[str] = []
    for record in records:
        entities = record.get("entities")
        published_at = _news_datetime(record.get("published_at"))
        retrieved_at = _news_datetime(record.get("retrieved_at"))
        origin = _news_origin(record)
        if (
            not isinstance(entities, Sequence)
            or isinstance(entities, str)
            or ticker not in entities
        ):
            continue
        if origin is None:
            gaps.append(_critical("news record lacks a stable origin"))
            continue
        if published_at is None:
            gaps.append(_critical(f"news origin {origin} has unknown publication time"))
            continue
        if published_at > cutoff:
            continue
        if retrieved_at is None:
            gaps.append(_critical(f"news origin {origin} has unknown retrieval time"))
            continue
        if retrieved_at > cutoff:
            gaps.append(_critical(f"news origin {origin} was retrieved after the cutoff"))
            continue
        if retrieved_at < published_at:
            gaps.append(_critical(f"news origin {origin} was retrieved before publication"))
            continue
        eligible.append((record, origin, published_at, retrieved_at))

    unique: list[tuple[Mapping[str, Any], str, datetime, datetime]] = []
    origins: set[str] = set()
    for item in eligible:
        if item[1] not in origins:
            origins.add(item[1])
            unique.append(item)
    if limit is not None:
        unique = unique[:limit]

    sources: list[SourceDocument] = []
    events: list[ResearchEvent] = []
    for index, (record, origin, published_at, retrieved_at) in enumerate(unique, start=1):
        url = record.get("url")
        if not isinstance(url, str) or not url:
            gaps.append(_critical(f"news origin {origin} lacks a URL"))
            continue
        content = record.get("content")
        content = content if isinstance(content, str) else ""
        source_id = f"news:{index}:{origin.split(':', 1)[1]}"
        source = SourceDocument(
            id=source_id,
            url=url,
            title=str(record.get("title") or "Untitled news item"),
            publisher=str(record.get("publisher") or "Unknown publisher"),
            retrieved_at=retrieved_at,
            published_at=published_at,
            content=content,
            content_sha256=_content_hash(content),
            origin_id=origin,
            kind="news",
            availability="full_text" if content else "snippet",
        )
        sources.append(source)
        event_at = _news_datetime(record.get("event_at"))
        events.append(
            ResearchEvent(
                id=f"event:{index}:{origin.split(':', 1)[1]}",
                title=source.title,
                source_id=source.id,
                entities=tuple(entities),
                published_at=published_at,
                event_at=event_at,
                origin_id=origin,
                classification=record.get("classification", "reporting"),
            )
        )
    if not events:
        gaps.append(_critical("no eligible news records; empty coverage is not complete"))
    return NormalizedNews(tuple(sources), tuple(events), tuple(dict.fromkeys(gaps)))
