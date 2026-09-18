"""Strict offline HOOD model-fact enrichment from frozen page-marked evidence.

The adapter accepts an already-frozen :class:`EvidenceSnapshot`.  It performs no
network access, PDF extraction, or model calls.  Only the two expected Robinhood
issuer releases are read, their UTF-8 content hashes and page topology are
rechecked, and exact reported table rows are parsed.  Changed headers, periods,
row labels, duplicate matches, missing values, and failed accounting
reconciliations all fail closed.

The output is a new snapshot.  Existing sources and facts are preserved in their
original order and form; the destination must not already exist.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.storage import canonical_json, read_json

Q2_SOURCE_ID = "hood-q2-2026-release"
FY2025_SOURCE_ID = "hood-fy2025-release"
REQUIRED_SOURCE_IDS = (Q2_SOURCE_ID, FY2025_SOURCE_ID)

ACCOUNTING_BASIS = "US GAAP"
USD_MILLIONS = Decimal("1000000")
SHARES_MILLIONS = Decimal("1000000")

FY2025_START = date(2025, 1, 1)
FY2025_END = date(2025, 12, 31)
H1_FY2025_END = date(2025, 6, 30)
H1_FY2026_START = date(2026, 1, 1)
Q2_FY2026_START = date(2026, 4, 1)
Q2_FY2026_END = date(2026, 6, 30)
TTM_START = date(2025, 7, 1)

Q2_PUBLICATION_DATE = date(2026, 7, 29)
FY2025_PUBLICATION_DATE = date(2026, 2, 10)
Q2_PUBLISHED_AT = datetime(2026, 7, 29, 20, 5, tzinfo=timezone.utc)

MODEL_FACT_CAVEATS = (
    "Valuation input caveat: hood-net-income-common-ttm-q2-fy2026 is a mechanical "
    "FY2025 minus H1 FY2025 plus H1 FY2026 bridge of reported US GAAP common net "
    "income, not a forecast or a normalization.",
    "Valuation input caveat: hood-diluted-shares-q2-fy2026 is a 91-day weighted-average "
    "diluted-share duration fact and may be used only as an explicit latest-quarter "
    "proxy, never as a point-in-time share count.",
    "The frozen releases do not supply a valuation-ready future forecast or required "
    "regulatory-capital-retention series; neither is inferred by this adapter.",
    "The disclosed $129 million RVI-related gain is not converted from pretax to after-tax "
    "or used to normalize common net income because the frozen tables do not supply that "
    "attribution.",
)

_PAGE_MARKER = re.compile(r"(?m)^\[PDF page ([1-9]\d*)\]\r?\n")
_NUMBER_TOKEN = r"(?:—|--|-|\(?\d{1,3}(?:,\d{3})*\)?)"
_PERCENT_TOKEN = r"(?:NM|(?:\(\s*\d+\s*\)|\d+)\s*%)"


class ModelFactExtractionError(ValueError):
    """Frozen evidence does not support an exact required HOOD model fact."""


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    snapshot: EvidenceSnapshot
    destination: Path
    added_fact_ids: tuple[str, ...]
    reused_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PeriodValues:
    revenues: dict[str, Decimal]
    consolidated_net_income: Decimal
    noncontrolling_income: Decimal
    common_net_income: Decimal


def _canonical_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u202f", " ")
    value = re.sub(r"\.{2,}", " ", value)
    return " ".join(value.split())


def _pages(source: SourceDocument, expected_count: int) -> dict[int, str]:
    matches = list(_PAGE_MARKER.finditer(source.content))
    if not matches or matches[0].start() != 0:
        raise ModelFactExtractionError(
            f"{source.id}: content must begin with a one-based [PDF page N] marker"
        )
    numbers = [int(match.group(1)) for match in matches]
    expected = list(range(1, expected_count + 1))
    if numbers != expected:
        raise ModelFactExtractionError(
            f"{source.id}: expected PDF page markers {expected!r}, found {numbers!r}"
        )
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source.content)
        page_text = source.content[match.end() : end]
        if not page_text.strip():
            raise ModelFactExtractionError(f"{source.id}: PDF page {numbers[index]} is empty")
        pages[numbers[index]] = _canonical_text(page_text)
    return pages


def _require_once(text: str, marker: str, *, source_id: str, page: int) -> None:
    count = text.count(_canonical_text(marker))
    if count != 1:
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} marker {marker!r} expected once, found {count}"
        )


def _require_order(
    text: str, fragments: Sequence[str], *, source_id: str, page: int, description: str
) -> None:
    position = 0
    for fragment in fragments:
        normalized = _canonical_text(fragment)
        found = text.find(normalized, position)
        if found < 0:
            raise ModelFactExtractionError(
                f"{source_id}: PDF page {page} {description} changed; "
                f"required fragment {fragment!r} is unavailable or out of order"
            )
        position = found + len(normalized)


def _unique_match(
    text: str,
    pattern: str,
    *,
    source_id: str,
    page: int,
    description: str,
) -> re.Match[str]:
    matches = list(re.finditer(pattern, text))
    if not matches:
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} required {description} is unavailable"
        )
    if len(matches) != 1:
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} required {description} is ambiguous"
        )
    return matches[0]


def _section(
    text: str,
    start: str,
    end: str,
    *,
    source_id: str,
    page: int,
) -> str:
    match = _unique_match(
        text,
        re.escape(_canonical_text(start)) + r"\s+(.*?)\s+" + re.escape(_canonical_text(end)),
        source_id=source_id,
        page=page,
        description=f"section {start!r}",
    )
    return match.group(1)


def _number(token: str, *, source_id: str, page: int, field: str, dash_is_zero: bool = False) -> Decimal:
    compact = token.replace(" ", "")
    if compact in {"—", "--", "-"}:
        if dash_is_zero:
            return Decimal(0)
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} required field {field!r} is unavailable"
        )
    negative = compact.startswith("(") and compact.endswith(")")
    digits = compact[1:-1] if negative else compact
    try:
        value = Decimal(digits.replace(",", ""))
    except Exception as exc:
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} field {field!r} has invalid value {token!r}"
        ) from exc
    if negative:
        value = -value
    if not value.is_finite():
        raise ModelFactExtractionError(
            f"{source_id}: PDF page {page} field {field!r} is non-finite"
        )
    return value


def _row_values(
    text: str,
    label: str,
    value_count: int,
    percent_count: int,
    *,
    source_id: str,
    page: int,
) -> tuple[Decimal, ...]:
    pieces = [re.escape(label)]
    for index in range(value_count):
        pieces.append(r"\s+\$?\s*(" + _NUMBER_TOKEN + r")")
        if index >= value_count - percent_count:
            pieces.append(r"\s+(?:" + _PERCENT_TOKEN + r")")
    match = _unique_match(
        text,
        "".join(pieces) + r"(?=\s+[A-Za-z]|\s+ROBINHOOD|$)",
        source_id=source_id,
        page=page,
        description=f"row {label!r}",
    )
    return tuple(
        _number(token, source_id=source_id, page=page, field=f"{label} column {index + 1}")
        for index, token in enumerate(match.groups())
    )


def _nci_row_values(
    text: str,
    label: str,
    value_count: int,
    percent_count: int,
    *,
    source_id: str,
    page: int,
) -> tuple[Decimal, ...]:
    pieces = [re.escape(label)]
    for index in range(value_count):
        pieces.append(r"\s+\$?\s*(" + _NUMBER_TOKEN + r")")
        if index >= value_count - percent_count:
            pieces.append(r"\s+(?:" + _PERCENT_TOKEN + r")")
    match = _unique_match(
        text,
        "".join(pieces) + r"(?=\s+[A-Za-z]|\s+ROBINHOOD|$)",
        source_id=source_id,
        page=page,
        description=f"row {label!r}",
    )
    return tuple(
        _number(
            token,
            source_id=source_id,
            page=page,
            field=f"{label} column {index + 1}",
            dash_is_zero=True,
        )
        for index, token in enumerate(match.groups())
    )


def _plain_row_values(
    text: str,
    label: str,
    value_count: int,
    *,
    source_id: str,
    page: int,
) -> tuple[Decimal, ...]:
    pattern = re.escape(label) + "".join(
        r"\s+\$?\s*(" + _NUMBER_TOKEN + r")" for _ in range(value_count)
    )
    match = _unique_match(
        text,
        pattern + r"(?=\s+[A-Za-z]|\s+ROBINHOOD|$)",
        source_id=source_id,
        page=page,
        description=f"row {label!r}",
    )
    return tuple(
        _number(token, source_id=source_id, page=page, field=f"{label} column {index + 1}")
        for index, token in enumerate(match.groups())
    )


def _validate_reconciliation(
    period: str,
    revenues: dict[str, Decimal],
    consolidated: Decimal,
    nci: Decimal,
    attributable: Decimal,
    basic_common: Decimal,
    diluted_common: Decimal,
) -> None:
    component_sum = (
        revenues["transaction_based_revenue"]
        + revenues["net_interest_revenue"]
        + revenues["other_revenue"]
    )
    if component_sum != revenues["revenue"]:
        raise ModelFactExtractionError(
            f"{period}: revenue components do not reconcile to Total net revenues"
        )
    if consolidated - nci != attributable:
        raise ModelFactExtractionError(
            f"{period}: consolidated net income less non-controlling interests "
            "does not reconcile to net income attributable to Robinhood"
        )
    if basic_common != attributable or diluted_common != attributable:
        raise ModelFactExtractionError(
            f"{period}: common-stockholder net income rows do not reconcile to "
            "net income attributable to Robinhood"
        )


def _extract_annual(page: str) -> _PeriodValues:
    source_id, page_number = FY2025_SOURCE_ID, 7
    _require_once(
        page,
        "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS",
        source_id=source_id,
        page=page_number,
    )
    _require_order(
        page,
        (
            "Year Ended",
            "December 31,",
            "YOY%",
            "Change",
            "(in millions, except share, per share, and percentage data)",
            "2024 2025",
            "Revenues:",
        ),
        source_id=source_id,
        page=page_number,
        description="annual table headers and period columns",
    )
    row_specs = {
        "transaction_based_revenue": "Transaction-based revenues",
        "net_interest_revenue": "Net interest revenues",
        "other_revenue": "Other revenues",
        "revenue": "Total net revenues",
    }
    revenues = {
        metric: _row_values(
            page, label, 2, 1, source_id=source_id, page=page_number
        )[1]
        for metric, label in row_specs.items()
    }
    consolidated = _row_values(
        page, "Net income", 2, 1, source_id=source_id, page=page_number
    )[1]
    nci = _nci_row_values(
        page,
        "Net income (loss) attributable to non-controlling interest",
        2,
        1,
        source_id=source_id,
        page=page_number,
    )[1]
    attributable = _row_values(
        page,
        "Net income attributable to Robinhood",
        2,
        1,
        source_id=source_id,
        page=page_number,
    )[1]
    common_section = _section(
        page,
        "Net income attributable to Robinhood common stockholders:",
        "Net income per share attributable to Robinhood common stockholders:",
        source_id=source_id,
        page=page_number,
    )
    basic_common = _plain_row_values(
        common_section, "Basic", 2, source_id=source_id, page=page_number
    )[1]
    diluted_common = _plain_row_values(
        common_section, "Diluted", 2, source_id=source_id, page=page_number
    )[1]
    _validate_reconciliation(
        "FY2025", revenues, consolidated, nci, attributable, basic_common, diluted_common
    )
    return _PeriodValues(revenues, consolidated, nci, diluted_common)


def _extract_q2(page4: str, page5: str) -> tuple[_PeriodValues, Decimal]:
    source_id = Q2_SOURCE_ID
    _require_once(
        page4,
        "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS",
        source_id=source_id,
        page=4,
    )
    _require_order(
        page4,
        (
            "Three Months Ended",
            "June 30,",
            "YOY% Change",
            "Three Months Ended March 31,",
            "QOQ% Change",
            "(in millions, except per share and percentage data)",
            "2025 2026 2026",
            "Revenues:",
        ),
        source_id=source_id,
        page=4,
        description="quarterly table headers and period columns",
    )
    row_specs = {
        "transaction_based_revenue": "Transaction-based revenues",
        "net_interest_revenue": "Net interest revenues",
        "other_revenue": "Other revenues",
        "revenue": "Total net revenues",
    }
    revenues = {
        metric: _row_values(page4, label, 3, 2, source_id=source_id, page=4)[1]
        for metric, label in row_specs.items()
    }
    consolidated = _row_values(
        page5, "Net income", 3, 2, source_id=source_id, page=5
    )[1]
    nci = _nci_row_values(
        page5,
        "Less: Net income (loss) attributable to non-controlling interests",
        3,
        2,
        source_id=source_id,
        page=5,
    )[1]
    attributable = _row_values(
        page5,
        "Net income attributable to Robinhood",
        3,
        2,
        source_id=source_id,
        page=5,
    )[1]
    common_section = _section(
        page5,
        "Net income attributable to Robinhood common stockholders:",
        "Net income per share attributable to Robinhood common stockholders:",
        source_id=source_id,
        page=5,
    )
    basic_common = _plain_row_values(common_section, "Basic", 3, source_id=source_id, page=5)[1]
    diluted_common = _plain_row_values(
        common_section, "Diluted", 3, source_id=source_id, page=5
    )[1]
    shares_section = _section(
        page5,
        "Weighted-average shares used to compute net income per share attributable to Robinhood common stockholders:",
        "ROBINHOOD MARKETS, INC.",
        source_id=source_id,
        page=5,
    )
    basic_shares = _plain_row_values(
        shares_section, "Basic", 3, source_id=source_id, page=5
    )
    diluted_shares = _plain_row_values(
        shares_section, "Diluted", 3, source_id=source_id, page=5
    )
    if basic_shares[1] == diluted_shares[1]:
        raise ModelFactExtractionError(
            f"{source_id}: PDF page 5 basic and diluted Q2 share rows are indistinguishable"
        )
    _validate_reconciliation(
        "Q2 FY2026", revenues, consolidated, nci, attributable, basic_common, diluted_common
    )
    return _PeriodValues(revenues, consolidated, nci, diluted_common), diluted_shares[1]


def _extract_h1(page5: str, page6: str) -> tuple[_PeriodValues, _PeriodValues]:
    source_id = Q2_SOURCE_ID
    _require_once(
        page5,
        "Six Months Ended",
        source_id=source_id,
        page=5,
    )
    _require_order(
        page5,
        (
            "Six Months Ended",
            "June 30,",
            "YOY% Change",
            "(in millions, except per share and percentage data)",
            "2025 2026",
            "Revenues:",
        ),
        source_id=source_id,
        page=5,
        description="half-year table headers and period columns",
    )
    _require_once(
        page6,
        "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS",
        source_id=source_id,
        page=6,
    )
    row_specs = {
        "transaction_based_revenue": "Transaction-based revenues",
        "net_interest_revenue": "Net interest revenues",
        "other_revenue": "Other revenues",
        "revenue": "Total net revenues",
    }
    revenue_columns = {
        metric: _row_values(page5, label, 2, 1, source_id=source_id, page=5)
        for metric, label in row_specs.items()
    }
    consolidated = _row_values(
        page6, "Net income", 2, 1, source_id=source_id, page=6
    )
    nci = _nci_row_values(
        page6,
        "Less: Net income (loss) attributable to non-controlling interests",
        2,
        1,
        source_id=source_id,
        page=6,
    )
    attributable = _row_values(
        page6,
        "Net income attributable to Robinhood",
        2,
        1,
        source_id=source_id,
        page=6,
    )
    common_section = _section(
        page6,
        "Net income attributable to Robinhood common stockholders:",
        "Net income per share attributable to Robinhood common stockholders:",
        source_id=source_id,
        page=6,
    )
    basic_common = _plain_row_values(common_section, "Basic", 2, source_id=source_id, page=6)
    diluted_common = _plain_row_values(
        common_section, "Diluted", 2, source_id=source_id, page=6
    )
    shares_section = _section(
        page6,
        "Weighted-average shares used to compute net income per share attributable to Robinhood common stockholders:",
        "ROBINHOOD MARKETS, INC.",
        source_id=source_id,
        page=6,
    )
    _plain_row_values(shares_section, "Basic", 2, source_id=source_id, page=6)
    _plain_row_values(shares_section, "Diluted", 2, source_id=source_id, page=6)

    prior = _PeriodValues(
        {metric: values[0] for metric, values in revenue_columns.items()},
        consolidated[0],
        nci[0],
        diluted_common[0],
    )
    current = _PeriodValues(
        {metric: values[1] for metric, values in revenue_columns.items()},
        consolidated[1],
        nci[1],
        diluted_common[1],
    )
    _validate_reconciliation(
        "H1 FY2025",
        prior.revenues,
        prior.consolidated_net_income,
        prior.noncontrolling_income,
        attributable[0],
        basic_common[0],
        diluted_common[0],
    )
    _validate_reconciliation(
        "H1 FY2026",
        current.revenues,
        current.consolidated_net_income,
        current.noncontrolling_income,
        attributable[1],
        basic_common[1],
        diluted_common[1],
    )
    return prior, current


def _source_fact(
    *,
    identifier: str,
    source_id: str,
    metric: str,
    value: Decimal,
    period_start: date,
    period_end: date,
    page: int,
    row: str,
    column: str,
    unit: str = "USD",
    currency: str | None = "USD",
    scale: Decimal = USD_MILLIONS,
) -> FinancialFact:
    return FinancialFact(
        id=identifier,
        source_id=source_id,
        metric=metric,
        value=value,
        scale=scale,
        unit=unit,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            f"PDF page {page}, CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS, "
            f"row {row!r}, {column}"
        ),
    )


def _derived_fact(
    *,
    identifier: str,
    metric: str,
    operands: Sequence[FinancialFact],
) -> FinancialFact:
    if len(operands) != 3:
        raise ModelFactExtractionError(f"{identifier}: expected exactly three TTM operands")
    first, removed, added = operands
    if any(
        (fact.unit, fact.currency, fact.scale, fact.basis)
        != (first.unit, first.currency, first.scale, first.basis)
        for fact in operands[1:]
    ):
        raise ModelFactExtractionError(f"{identifier}: derived operands are incompatible")
    with localcontext(Context(prec=50)):
        value = first.value - removed.value + added.value
    formula = f"{first.id} - {removed.id} + {added.id}"
    return FinancialFact(
        id=identifier,
        source_id=Q2_SOURCE_ID,
        metric=metric,
        value=value,
        scale=first.scale,
        unit=first.unit,
        currency=first.currency,
        period_start=TTM_START,
        period_end=Q2_FY2026_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            "derived TTM reported value: FY2025 annual less H1 FY2025 plus H1 FY2026; "
            "operands retain exact issuer-table PDF page and period ancestry"
        ),
        inputs=tuple(fact.id for fact in operands),
        formula=formula,
    )


def _period_facts(
    values: _PeriodValues,
    *,
    suffix: str,
    source_id: str,
    period_start: date,
    period_end: date,
    revenue_page: int,
    income_page: int,
    column: str,
    include_nci: bool,
) -> list[FinancialFact]:
    revenue_rows = {
        "transaction_based_revenue": "Transaction-based revenues",
        "net_interest_revenue": "Net interest revenues",
        "other_revenue": "Other revenues",
        "revenue": "Total net revenues",
    }
    result = [
        _source_fact(
            identifier=f"hood-{metric.replace('_', '-')}-{suffix}",
            source_id=source_id,
            metric=metric,
            value=values.revenues[metric],
            period_start=period_start,
            period_end=period_end,
            page=revenue_page,
            row=row,
            column=f"{column}; USD millions",
        )
        for metric, row in revenue_rows.items()
    ]
    result.append(
        _source_fact(
            identifier=f"hood-net-income-consolidated-{suffix}",
            source_id=source_id,
            metric="net_income",
            value=values.consolidated_net_income,
            period_start=period_start,
            period_end=period_end,
            page=income_page,
            row="Net income",
            column=f"{column}; USD millions; consolidated amount, not common income",
        )
    )
    if include_nci:
        result.append(
            _source_fact(
                identifier=f"hood-net-income-nci-{suffix}",
                source_id=source_id,
                metric="net_income_attributable_noncontrolling_interests",
                value=values.noncontrolling_income,
                period_start=period_start,
                period_end=period_end,
                page=income_page,
                row="Less: Net income (loss) attributable to non-controlling interests",
                column=f"{column}; USD millions",
            )
        )
    result.append(
        _source_fact(
            identifier=f"hood-net-income-common-{suffix}",
            source_id=source_id,
            metric="net_income_common",
            value=values.common_net_income,
            period_start=period_start,
            period_end=period_end,
            page=income_page,
            row="Diluted - Net income attributable to Robinhood common stockholders",
            column=f"{column}; USD millions; common-stockholder amount",
        )
    )
    return result


def _validate_snapshot(snapshot: EvidenceSnapshot) -> dict[str, SourceDocument]:
    if snapshot.ticker != "HOOD":
        raise ModelFactExtractionError(
            f"ticker must be 'HOOD', found {snapshot.ticker!r}"
        )
    if snapshot.cutoff.date() < Q2_PUBLICATION_DATE:
        raise ModelFactExtractionError(
            "evidence cutoff predates the Q2 2026 issuer release"
        )
    sources = {source.id: source for source in snapshot.sources}
    missing = [identifier for identifier in REQUIRED_SOURCE_IDS if identifier not in sources]
    if missing:
        raise ModelFactExtractionError(
            "required evidence sources are unavailable: " + ", ".join(missing)
        )
    expected_publication_dates = {
        Q2_SOURCE_ID: Q2_PUBLICATION_DATE,
        FY2025_SOURCE_ID: FY2025_PUBLICATION_DATE,
    }
    for identifier in REQUIRED_SOURCE_IDS:
        source = sources[identifier]
        actual_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
        if actual_hash != source.content_sha256:
            raise ModelFactExtractionError(
                f"{identifier}: extracted text content hash mismatch"
            )
        if source.published_at is None:
            raise ModelFactExtractionError(f"{identifier}: publication availability is unknown")
        if source.published_at > snapshot.cutoff:
            raise ModelFactExtractionError(f"{identifier}: publication is after evidence cutoff")
        issuer_date = source.published_at.astimezone(ZoneInfo("America/New_York")).date()
        if issuer_date != expected_publication_dates[identifier]:
            raise ModelFactExtractionError(
                f"{identifier}: publication date does not match the issuer release"
            )
        if identifier == Q2_SOURCE_ID and source.published_at != Q2_PUBLISHED_AT:
            raise ModelFactExtractionError(
                f"{identifier}: publication timestamp does not match the exact issuer release time"
            )
        if source.availability != "full_text":
            raise ModelFactExtractionError(f"{identifier}: full text is required")
    return sources


def extract_model_facts(snapshot: EvidenceSnapshot) -> tuple[FinancialFact, ...]:
    """Extract exact reported and arithmetic TTM facts from a frozen snapshot."""

    sources = _validate_snapshot(snapshot)
    q2_pages = _pages(sources[Q2_SOURCE_ID], 13)
    fy_pages = _pages(sources[FY2025_SOURCE_ID], 15)
    _require_order(
        q2_pages[1],
        (
            "Robinhood Reports Second Quarter 2026 Results",
            "July 29, 2026",
            "(NASDAQ: HOOD)",
        ),
        source_id=Q2_SOURCE_ID,
        page=1,
        description="issuer identity",
    )
    _require_order(
        fy_pages[1],
        (
            "Robinhood Reports Fourth Quarter and Full Year 2025 Results",
            "February 10, 2026",
            "(NASDAQ: HOOD)",
        ),
        source_id=FY2025_SOURCE_ID,
        page=1,
        description="issuer identity",
    )

    annual = _extract_annual(fy_pages[7])
    q2, diluted_shares = _extract_q2(q2_pages[4], q2_pages[5])
    h1_prior, h1_current = _extract_h1(q2_pages[5], q2_pages[6])

    facts = [
        *_period_facts(
            annual,
            suffix="fy2025",
            source_id=FY2025_SOURCE_ID,
            period_start=FY2025_START,
            period_end=FY2025_END,
            revenue_page=7,
            income_page=7,
            column="FY2025 Year Ended December 31, 2025 column",
            include_nci=False,
        ),
        *_period_facts(
            h1_prior,
            suffix="h1-fy2025",
            source_id=Q2_SOURCE_ID,
            period_start=FY2025_START,
            period_end=H1_FY2025_END,
            revenue_page=5,
            income_page=6,
            column="H1 FY2025 Six Months Ended June 30, 2025 column",
            include_nci=False,
        ),
        *_period_facts(
            h1_current,
            suffix="h1-fy2026",
            source_id=Q2_SOURCE_ID,
            period_start=H1_FY2026_START,
            period_end=Q2_FY2026_END,
            revenue_page=5,
            income_page=6,
            column="H1 FY2026 Six Months Ended June 30, 2026 column",
            include_nci=True,
        ),
        *_period_facts(
            q2,
            suffix="q2-fy2026",
            source_id=Q2_SOURCE_ID,
            period_start=Q2_FY2026_START,
            period_end=Q2_FY2026_END,
            revenue_page=4,
            income_page=5,
            column="Q2 FY2026 Three Months Ended June 30, 2026 column",
            include_nci=True,
        ),
    ]
    by_id = {fact.id: fact for fact in facts}
    for metric in (
        "transaction_based_revenue",
        "net_interest_revenue",
        "other_revenue",
        "revenue",
    ):
        slug = metric.replace("_", "-")
        facts.append(
            _derived_fact(
                identifier=f"hood-{slug}-ttm-q2-fy2026",
                metric=metric,
                operands=(
                    by_id[f"hood-{slug}-fy2025"],
                    by_id[f"hood-{slug}-h1-fy2025"],
                    by_id[f"hood-{slug}-h1-fy2026"],
                ),
            )
        )
    facts.append(
        _derived_fact(
            identifier="hood-net-income-common-ttm-q2-fy2026",
            metric="net_income_common",
            operands=(
                by_id["hood-net-income-common-fy2025"],
                by_id["hood-net-income-common-h1-fy2025"],
                by_id["hood-net-income-common-h1-fy2026"],
            ),
        )
    )
    facts.append(
        _source_fact(
            identifier="hood-diluted-shares-q2-fy2026",
            source_id=Q2_SOURCE_ID,
            metric="weighted_average_diluted_shares",
            value=diluted_shares,
            period_start=Q2_FY2026_START,
            period_end=Q2_FY2026_END,
            page=5,
            row=(
                "Diluted - Weighted-average shares used to compute net income per share "
                "attributable to Robinhood common stockholders"
            ),
            column=(
                "Q2 FY2026 Three Months Ended June 30, 2026 column; millions of shares; "
                "91-day quarterly duration proxy, not H1 diluted shares or basic shares"
            ),
            unit="shares",
            currency=None,
            scale=SHARES_MILLIONS,
        )
    )
    identifiers = [fact.id for fact in facts]
    if len(identifiers) != len(set(identifiers)):
        raise ModelFactExtractionError("adapter generated duplicate fact identifiers")
    return tuple(facts)


def _same_fact(left: FinancialFact, right: FinancialFact) -> bool:
    return left == right


def _merge_facts(
    existing: Sequence[FinancialFact], extracted: Sequence[FinancialFact]
) -> tuple[tuple[FinancialFact, ...], tuple[str, ...]]:
    existing_by_id = {fact.id: fact for fact in existing}
    additions: list[FinancialFact] = []
    reused: list[str] = []
    for fact in extracted:
        prior = existing_by_id.get(fact.id)
        if prior is None:
            additions.append(fact)
        elif fact.inputs:
            raise ModelFactExtractionError(
                f"new derived model fact identifier collides with frozen snapshot: {fact.id}"
            )
        elif not _same_fact(prior, fact):
            raise ModelFactExtractionError(
                f"extracted source fact conflicts with frozen snapshot: {fact.id}"
            )
        else:
            reused.append(fact.id)
    return tuple(additions), tuple(reused)


def _write_new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ModelFactExtractionError(f"destination already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def enrich_snapshot(snapshot: EvidenceSnapshot, destination: Path) -> EnrichmentResult:
    """Write one fresh enriched snapshot while preserving all frozen source facts."""

    destination = Path(destination).resolve(strict=False)
    if destination.exists():
        raise ModelFactExtractionError(f"destination already exists: {destination}")
    extracted = extract_model_facts(snapshot)
    additions, reused = _merge_facts(snapshot.facts, extracted)
    data = snapshot.model_dump(mode="json")
    data["facts"] = [
        *(fact.model_dump(mode="json") for fact in snapshot.facts),
        *(fact.model_dump(mode="json") for fact in additions),
    ]
    data["gaps"] = list(dict.fromkeys((*snapshot.gaps, *MODEL_FACT_CAVEATS)))
    enriched = EvidenceSnapshot.model_validate(data)
    _write_new_file(destination, canonical_json(enriched))
    return EnrichmentResult(
        snapshot=enriched,
        destination=destination,
        added_fact_ids=tuple(fact.id for fact in additions),
        reused_fact_ids=reused,
    )


def _packet_records(packet: Path, snapshot: EvidenceSnapshot) -> None:
    acquisition_path = packet / "acquisition.json"
    try:
        acquisition = read_json(acquisition_path)
    except (OSError, ValueError) as exc:
        raise ModelFactExtractionError(
            f"frozen acquisition metadata is unavailable or invalid: {acquisition_path}"
        ) from exc
    if not isinstance(acquisition, dict) or acquisition.get("schema_version") != 1:
        raise ModelFactExtractionError("acquisition.json has an unsupported schema")
    if acquisition.get("scope") != "explicit_manifest_offline_public_documents_only":
        raise ModelFactExtractionError("acquisition.json does not describe an offline public packet")
    try:
        frozen_cutoff = datetime.fromisoformat(acquisition["frozen_cutoff"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelFactExtractionError("acquisition.json frozen_cutoff is invalid") from exc
    if frozen_cutoff.tzinfo is None or frozen_cutoff.utcoffset() is None:
        raise ModelFactExtractionError("acquisition.json frozen_cutoff must be timezone-aware")
    if frozen_cutoff != snapshot.cutoff:
        raise ModelFactExtractionError(
            "acquisition.json frozen_cutoff does not match evidence.json"
        )
    records_value = acquisition.get("sources")
    if not isinstance(records_value, list):
        raise ModelFactExtractionError("acquisition.json sources must be a list")
    records: dict[str, dict[str, object]] = {}
    for item in records_value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ModelFactExtractionError("acquisition.json contains an invalid source record")
        identifier = item["id"]
        if identifier in records:
            raise ModelFactExtractionError(f"acquisition.json duplicates source {identifier}")
        records[identifier] = item
    sources = {source.id: source for source in snapshot.sources}
    if records.keys() != sources.keys():
        raise ModelFactExtractionError(
            "acquisition.json source IDs do not exactly match evidence.json"
        )

    expected_page_counts = {Q2_SOURCE_ID: 13, FY2025_SOURCE_ID: 15}
    for identifier, source in sources.items():
        record = records[identifier]
        expected_text_relative = f"text/{identifier}.txt"
        if record.get("text_archive") != expected_text_relative:
            raise ModelFactExtractionError(
                f"{identifier}: text archive location does not match the frozen importer contract"
            )
        raw_relative = record.get("raw_archive")
        if not isinstance(raw_relative, str) or not raw_relative.startswith(f"raw/{identifier}."):
            raise ModelFactExtractionError(
                f"{identifier}: raw archive location does not match the frozen importer contract"
            )
        raw_path = packet / raw_relative
        text_path = packet / expected_text_relative
        if raw_path.is_symlink() or text_path.is_symlink():
            raise ModelFactExtractionError(f"{identifier}: frozen archive files cannot be symlinks")
        try:
            raw = raw_path.read_bytes()
            text_bytes = text_path.read_bytes()
        except OSError as exc:
            raise ModelFactExtractionError(
                f"{identifier}: frozen raw or text archive is unavailable"
            ) from exc
        if hashlib.sha256(raw).hexdigest() != record.get("raw_sha256"):
            raise ModelFactExtractionError(f"{identifier}: raw archive hash mismatch")
        if hashlib.sha256(text_bytes).hexdigest() != record.get("text_sha256"):
            raise ModelFactExtractionError(f"{identifier}: text archive hash mismatch")
        if record.get("raw_bytes") != len(raw) or record.get("text_bytes") != len(text_bytes):
            raise ModelFactExtractionError(f"{identifier}: archive byte counts do not match")
        if text_bytes != source.content.encode("utf-8"):
            raise ModelFactExtractionError(
                f"{identifier}: text archive does not match EvidenceSnapshot content"
            )
        if record.get("text_sha256") != source.content_sha256:
            raise ModelFactExtractionError(
                f"{identifier}: acquisition text hash does not match EvidenceSnapshot"
            )
        if record.get("original_url") != source.url:
            raise ModelFactExtractionError(
                f"{identifier}: acquisition origin does not match EvidenceSnapshot"
            )
        if record.get("published_at") != (
            source.published_at.isoformat() if source.published_at else None
        ) or record.get("retrieved_at") != source.retrieved_at.isoformat():
            raise ModelFactExtractionError(
                f"{identifier}: acquisition timestamps do not match EvidenceSnapshot"
            )
        if identifier in expected_page_counts:
            if record.get("media_type") != "application/pdf" or not raw.startswith(b"%PDF-"):
                raise ModelFactExtractionError(f"{identifier}: frozen raw source is not a PDF")
            extractor = record.get("extractor")
            if not isinstance(extractor, dict) or (
                extractor.get("name") != "pypdf.PdfReader.page.extract_text"
                or extractor.get("pages") != expected_page_counts[identifier]
                or extractor.get("page_markers") != "one-based [PDF page N]"
            ):
                raise ModelFactExtractionError(
                    f"{identifier}: PDF extractor provenance does not match the frozen text"
                )


def enrich_frozen_snapshot(frozen_input: Path, destination: Path) -> EnrichmentResult:
    """Validate a complete frozen packet and write a new snapshot outside it."""

    frozen_input = Path(frozen_input).resolve()
    destination = Path(destination).resolve(strict=False)
    if not frozen_input.is_dir():
        raise ModelFactExtractionError(
            "frozen_input must be a packet directory with evidence, acquisition, raw, and text archives"
        )
    frozen_path = frozen_input / "evidence.json"
    if destination == frozen_input or destination.is_relative_to(frozen_input):
        raise ModelFactExtractionError(
            "destination must be outside the frozen input directory"
        )
    try:
        snapshot = EvidenceSnapshot.model_validate(read_json(frozen_path))
    except (OSError, ValueError) as exc:
        raise ModelFactExtractionError(
            f"frozen evidence snapshot is unavailable or invalid: {frozen_path}"
        ) from exc
    _packet_records(frozen_input, snapshot)
    original_hash = hashlib.sha256(frozen_path.read_bytes()).hexdigest()
    result = enrich_snapshot(snapshot, destination)
    if hashlib.sha256(frozen_path.read_bytes()).hexdigest() != original_hash:
        result.destination.unlink(missing_ok=True)
        raise ModelFactExtractionError("frozen evidence input changed during enrichment")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frozen_input", type=Path, help="complete frozen packet directory")
    parser.add_argument("destination", type=Path, help="fresh output path outside frozen inputs")
    args = parser.parse_args()
    try:
        result = enrich_frozen_snapshot(args.frozen_input, args.destination)
    except (ModelFactExtractionError, OSError, ValueError) as exc:
        parser.error(str(exc))
    by_id = {fact.id: fact for fact in result.snapshot.facts}
    for identifier in (
        "hood-revenue-ttm-q2-fy2026",
        "hood-net-income-common-ttm-q2-fy2026",
        "hood-diluted-shares-q2-fy2026",
    ):
        fact = by_id[identifier]
        print(f"{identifier}={fact.value} scale={fact.scale} source={fact.source_id}")
    print(
        f"wrote {len(result.added_fact_ids)} facts to {result.destination}; "
        f"reused={len(result.reused_fact_ids)}"
    )


if __name__ == "__main__":
    main()
