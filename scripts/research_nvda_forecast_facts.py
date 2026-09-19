"""Add narrow NVDA forecast-calibration facts from a frozen evidence snapshot.

This adapter is deliberately offline and source-bound.  It reads the embedded
text of the frozen Q2 FY27 filing and earnings release, checks exact table
headers and row layouts, and emits only reported H1 facts plus tax-rate
calculations whose numerator and denominator are normalized to the same H1
period.  It does not infer quarterly values from six-month rows and does not
turn management guidance into reported financial facts.

The input snapshot is immutable.  ``enrich_snapshot`` writes a new file with
exclusive-create semantics, and the destination must be outside the input
snapshot's directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from pathlib import Path

from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    SourceDocument,
)
from tradingagents.research.storage import canonical_json, read_json

Q2_RELEASE_SOURCE_ID = "nvda-q2-release"
Q2_FILING_SOURCE_ID = "nvda-q2-filing"
USD_MILLIONS = Decimal("1000000")
ACCOUNTING_BASIS = "US GAAP"

H1_FY27_START = date(2026, 1, 26)
H1_FY27_END = date(2026, 7, 26)
H1_FY26_START = date(2025, 1, 27)
H1_FY26_END = date(2025, 7, 27)

CURRENT_TAX_FACT_ID = "nvda-income_tax_expense-h1"
CURRENT_CAPEX_FACT_ID = "nvda-capex_cashflow-h1"

FORECAST_FACT_CAVEATS = (
    "Forecast calibration enrichment is limited to frozen reported H1 US GAAP rows. "
    "No quarterly depreciation, amortization, tax, or cash-capex value was inferred "
    "from a six-month value, and no management guidance was normalized as a reported fact.",
    "Effective tax rates are calculations from rounded USD-million income-tax expense "
    "and pretax-income rows, cross-checked only to the filing's rounded H1 percentages; "
    "they are not statutory tax rates.",
)

_FILING_INCOME_HEADER = (
    "NVIDIA Corporation and Subsidiaries\n"
    "Condensed Consolidated Statements of Income\n"
    "(In millions, except per share data)\n"
    "(Unaudited)\n"
    "\N{NO-BREAK SPACE} Three Months Ended Six Months Ended\n"
    "\N{NO-BREAK SPACE} Jul 26, 2026 Jul 27, 2025 Jul 26, 2026 Jul 27, 2025\n"
)
_FILING_CASHFLOW_HEADER = (
    "NVIDIA Corporation and Subsidiaries\n"
    "Condensed Consolidated Statements of Cash Flows\n"
    "(In millions)\n"
    "(Unaudited)\n"
    "\N{NO-BREAK SPACE} Six Months Ended\n"
    "\N{NO-BREAK SPACE} Jul 26, 2026 Jul 27, 2025\n"
)
_FILING_TABLE_END = "\nSee accompanying Notes to Condensed Consolidated Financial Statements."
_FILING_BASIS_CONTEXT = (
    "Basis of Presentation\n"
    "The accompanying unaudited condensed consolidated financial statements were "
    "prepared in accordance with accounting \n"
    "principles generally accepted in the United States of America, or U.S. GAAP, "
    "for interim financial information and with the \n"
    "instructions to Form 10-Q and Article 10 of Securities and Exchange Commission, "
    "or SEC, Regulation S-X."
)
_FILING_TAX_NOTE = (
    "Note 11 - Income Taxes\n"
    "Income tax expense was $11.8 billion and $4.8 billion for the second quarter, "
    "and $23.4 billion and $7.9 billion for the first \n"
    "half, of fiscal years 2027 and 2026, respectively. Income tax as a percentage "
    "of income before income tax was 16.5% and \n"
    "15.3% for the second quarter, and 16.5% and 14.9% for the first half, of fiscal "
    "years 2027 and 2026, respectively."
)

_RELEASE_INCOME_HEADER = (
    "NVIDIA CORPORATION\n"
    "CONDENSED CONSOLIDATED STATEMENTS OF INCOME\n"
    "(In millions, except per share data)\n"
    "(Unaudited)\n"
    "Three Months Ended\n"
    "Six Months Ended\n"
    "July 26,\n"
    "July 27,\n"
    "July 26,\n"
    "July 27,\n"
    "2026\n"
    "2025\n"
    "2026\n"
    "2025\n"
)
_RELEASE_CASHFLOW_HEADER = (
    "NVIDIA CORPORATION\n"
    "CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS\n"
    "(In millions)\n"
    "(Unaudited)\n"
    "Three Months Ended\n"
    "Six Months Ended\n"
    "July 26,\n"
    "July 27,\n"
    "July 26,\n"
    "July 27,\n"
    "2026\n"
    "2025\n"
    "2026\n"
    "2025\n"
)
_RELEASE_INCOME_END = "\nNVIDIA CORPORATION\nCONDENSED CONSOLIDATED BALANCE SHEETS"
_RELEASE_CASHFLOW_END = (
    "\nNVIDIA CORPORATION\nRECONCILIATION OF GAAP TO NON-GAAP FINANCIAL MEASURES"
)

_PLAIN_NUMBER = r"(?:0|[1-9]\d{0,2}(?:,\d{3})*)"


class ForecastFactExtractionError(ValueError):
    """Raised when the frozen text cannot support an exact required fact."""


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    snapshot: EvidenceSnapshot
    base_snapshot: Path
    destination: Path
    added_fact_ids: tuple[str, ...]
    reused_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Table:
    source: SourceDocument
    name: str
    header_description: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class _Row:
    table: _Table
    label: str
    text: str
    start: int
    end: int
    values: tuple[Decimal, ...]


def _source(snapshot: EvidenceSnapshot, identifier: str) -> SourceDocument:
    matches = [source for source in snapshot.sources if source.id == identifier]
    if len(matches) != 1:
        raise ForecastFactExtractionError(
            f"required source {identifier!r} is unavailable or ambiguous"
        )
    source = matches[0]
    actual_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
    if actual_hash != source.content_sha256:
        raise ForecastFactExtractionError(
            f"{identifier}: embedded text does not match content_sha256"
        )
    return source


def _unique_span(text: str, needle: str, *, description: str) -> tuple[int, int]:
    starts = [match.start() for match in re.finditer(re.escape(needle), text)]
    if len(starts) != 1:
        raise ForecastFactExtractionError(
            f"{description} is unavailable or ambiguous; expected exactly one exact match"
        )
    return starts[0], starts[0] + len(needle)


def _table(
    source: SourceDocument,
    *,
    name: str,
    header: str,
    end_marker: str,
    header_description: str,
) -> _Table:
    start, header_end = _unique_span(
        source.content,
        header,
        description=f"{source.id}: {name} exact header",
    )
    end = source.content.find(end_marker, header_end)
    if end < 0:
        raise ForecastFactExtractionError(
            f"{source.id}: {name} exact table boundary changed or is unavailable"
        )
    return _Table(
        source=source,
        name=name,
        header_description=header_description,
        start=start,
        end=end,
        text=source.content[start:end],
    )


def _decimal(token: str, *, source_id: str, label: str) -> Decimal:
    cleaned = token.replace(",", "")
    try:
        value = Decimal(cleaned)
    except ArithmeticError as exc:
        raise ForecastFactExtractionError(
            f"{source_id}: row {label!r} contains invalid number {token!r}"
        ) from exc
    if not value.is_finite():
        raise ForecastFactExtractionError(
            f"{source_id}: row {label!r} contains a non-finite number"
        )
    return value


def _row(table: _Table, label: str, pattern: str, *, negative: bool = False) -> _Row:
    matches = list(re.finditer(pattern, table.text, flags=re.MULTILINE))
    if len(matches) != 1:
        raise ForecastFactExtractionError(
            f"{table.source.id}: {table.name} required row {label!r} changed, "
            "is unavailable, or is ambiguous"
        )
    match = matches[0]
    values = tuple(
        _decimal(match.group(f"v{index}"), source_id=table.source.id, label=label)
        for index in range(1, len(match.groups()) + 1)
    )
    if negative:
        values = tuple(-value for value in values)
    return _Row(
        table=table,
        label=label,
        text=match.group(0),
        start=table.start + match.start(),
        end=table.start + match.end(),
        values=values,
    )


def _filing_four_column_row(table: _Table, label: str) -> _Row:
    pattern = (
        rf"^{re.escape(label)}  (?P<v1>{_PLAIN_NUMBER})  (?P<v2>{_PLAIN_NUMBER})  "
        rf"(?P<v3>{_PLAIN_NUMBER})  (?P<v4>{_PLAIN_NUMBER}) \n"
    )
    return _row(table, label, pattern)


def _filing_two_column_row(table: _Table, label: str, *, negative: bool = False) -> _Row:
    if negative:
        pattern = (
            rf"^{re.escape(label)}  \((?P<v1>{_PLAIN_NUMBER})\)  "
            rf"\((?P<v2>{_PLAIN_NUMBER})\) \n"
        )
    else:
        pattern = (
            rf"^{re.escape(label)}  (?P<v1>{_PLAIN_NUMBER})  "
            rf"(?P<v2>{_PLAIN_NUMBER}) \n"
        )
    return _row(table, label, pattern, negative=negative)


def _release_four_column_row(
    table: _Table, label: str, *, next_label: str, negative: bool = False
) -> _Row:
    if negative:
        cells = "\n".join(
            rf"\((?P<v{index}>{_PLAIN_NUMBER})\n\)" for index in range(1, 5)
        )
    else:
        cells = "\n".join(
            rf"(?P<v{index}>{_PLAIN_NUMBER})" for index in range(1, 5)
        )
    return _row(
        table,
        label,
        rf"^{re.escape(label)}\n{cells}$(?=\n{re.escape(next_label)}$)",
        negative=negative,
    )


def _line_range(content: str, start: int, end: int) -> tuple[int, int]:
    line_start = content.count("\n", 0, start) + 1
    line_end = content.count("\n", 0, max(start, end - 1)) + 1
    return line_start, line_end


def _row_location(row: _Row, *, column: str) -> str:
    source = row.table.source
    row_lines = _line_range(source.content, row.start, row.end)
    table_lines = _line_range(source.content, row.table.start, row.table.end)
    escaped_row = row.text.rstrip("\n").replace("\n", " | ")
    return (
        f"source_sha256={source.content_sha256}; chars=[{row.start},{row.end}); "
        f"lines={row_lines[0]}-{row_lines[1]}; "
        f"table_chars=[{row.table.start},{row.table.end}); "
        f"table_lines={table_lines[0]}-{table_lines[1]}; "
        f"table={row.table.name!r}; header={row.table.header_description!r}; "
        f"exact_row={escaped_row!r}; column={column!r}"
    )


def _source_fact(
    *,
    identifier: str,
    row: _Row,
    value: Decimal,
    metric: str,
    period_start: date,
    period_end: date,
    column: str,
) -> FinancialFact:
    return FinancialFact(
        id=identifier,
        source_id=row.table.source.id,
        metric=metric,
        value=value,
        scale=USD_MILLIONS,
        unit="USD",
        currency="USD",
        period_start=period_start,
        period_end=period_end,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=_row_location(row, column=column),
    )


def _check_equal(
    label: str,
    filing_values: tuple[Decimal, ...],
    release_values: tuple[Decimal, ...],
) -> None:
    if filing_values != release_values:
        raise ForecastFactExtractionError(
            f"{label}: filing and release consolidated table values disagree; "
            f"filing={filing_values!r}, release={release_values!r}"
        )


def _require_existing_operand(
    snapshot: EvidenceSnapshot,
    *,
    identifier: str,
    metric: str,
    value: Decimal,
    period_start: date,
    period_end: date,
) -> FinancialFact:
    matches = [fact for fact in snapshot.facts if fact.id == identifier]
    if len(matches) != 1:
        raise ForecastFactExtractionError(
            f"required existing fact {identifier!r} is unavailable or ambiguous"
        )
    fact = matches[0]
    expected = {
        "metric": metric,
        "value": value,
        "scale": USD_MILLIONS,
        "unit": "USD",
        "currency": "USD",
        "period_start": period_start,
        "period_end": period_end,
        "period_type": "duration",
        "basis": ACCOUNTING_BASIS,
    }
    disagreements = [
        field for field, expected_value in expected.items()
        if getattr(fact, field) != expected_value
    ]
    if disagreements:
        raise ForecastFactExtractionError(
            f"existing fact {identifier!r} conflicts with the exact frozen row: "
            + ", ".join(disagreements)
        )
    if fact.inputs or fact.formula:
        raise ForecastFactExtractionError(
            f"existing source operand {identifier!r} must not be a derived fact"
        )
    return fact


def _tax_rate_fact(
    *,
    identifier: str,
    tax: FinancialFact,
    pretax: FinancialFact,
    stated_percent: Decimal,
    note_source: SourceDocument,
    note_start: int,
    note_end: int,
) -> FinancialFact:
    if (
        tax.period_start != pretax.period_start
        or tax.period_end != pretax.period_end
        or tax.period_type != pretax.period_type
        or tax.scale != pretax.scale
        or tax.unit != pretax.unit
        or tax.currency != pretax.currency
        or tax.basis != pretax.basis
    ):
        raise ForecastFactExtractionError(
            f"{identifier}: tax-rate numerator and denominator are not normalized alike"
        )
    if pretax.value <= 0:
        raise ForecastFactExtractionError(f"{identifier}: pretax denominator is not positive")
    with localcontext(Context(prec=28)):
        value = tax.value / pretax.value
        rounded_percent = (value * Decimal(100)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    if rounded_percent != stated_percent:
        raise ForecastFactExtractionError(
            f"{identifier}: calculated {rounded_percent}% does not match filing note "
            f"{stated_percent}%"
        )
    note_lines = _line_range(note_source.content, note_start, note_end)
    return FinancialFact(
        id=identifier,
        source_id=note_source.id,
        metric="effective_tax_rate",
        value=value,
        scale=Decimal(1),
        unit="ratio",
        currency=None,
        period_start=tax.period_start,
        period_end=tax.period_end,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            f"calculated from same-period US GAAP rows; source_sha256="
            f"{note_source.content_sha256}; tax_note_chars=[{note_start},{note_end}); "
            f"tax_note_lines={note_lines[0]}-{note_lines[1]}; numerator={tax.id} "
            f"({tax.value} USD millions); denominator={pretax.id} "
            f"({pretax.value} USD millions); filing_note_rounded_percent="
            f"{stated_percent}%"
        ),
        inputs=(tax.id, pretax.id),
        formula=f"{tax.id} / {pretax.id}",
    )


def _candidate_facts(
    snapshot: EvidenceSnapshot,
) -> tuple[tuple[FinancialFact, ...], tuple[str, ...]]:
    filing = _source(snapshot, Q2_FILING_SOURCE_ID)
    release = _source(snapshot, Q2_RELEASE_SOURCE_ID)

    basis_start, basis_end = _unique_span(
        filing.content,
        _FILING_BASIS_CONTEXT,
        description=f"{filing.id}: exact US GAAP basis context",
    )
    filing_income = _table(
        filing,
        name="Condensed Consolidated Statements of Income",
        header=_FILING_INCOME_HEADER,
        end_marker=_FILING_TABLE_END,
        header_description=(
            "In millions, unaudited; Three Months Ended and Six Months Ended; "
            "Jul 26 2026, Jul 27 2025, Jul 26 2026, Jul 27 2025"
        ),
    )
    filing_cashflow = _table(
        filing,
        name="Condensed Consolidated Statements of Cash Flows",
        header=_FILING_CASHFLOW_HEADER,
        end_marker=_FILING_TABLE_END,
        header_description=(
            "In millions, unaudited; Six Months Ended; Jul 26 2026 and Jul 27 2025"
        ),
    )
    release_income = _table(
        release,
        name="CONDENSED CONSOLIDATED STATEMENTS OF INCOME",
        header=_RELEASE_INCOME_HEADER,
        end_marker=_RELEASE_INCOME_END,
        header_description=(
            "In millions, unaudited; Three Months Ended and Six Months Ended; "
            "July 26 2026, July 27 2025, July 26 2026, July 27 2025"
        ),
    )
    release_cashflow = _table(
        release,
        name="CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS",
        header=_RELEASE_CASHFLOW_HEADER,
        end_marker=_RELEASE_CASHFLOW_END,
        header_description=(
            "In millions, unaudited; Three Months Ended and Six Months Ended; "
            "July 26 2026, July 27 2025, July 26 2026, July 27 2025"
        ),
    )

    filing_pretax = _filing_four_column_row(
        filing_income, "Income before income tax"
    )
    filing_tax = _filing_four_column_row(filing_income, "Income tax expense")
    filing_da = _filing_two_column_row(
        filing_cashflow, "Depreciation and amortization"
    )
    filing_capex = _filing_two_column_row(
        filing_cashflow,
        "Purchases related to property and equipment and intangible assets",
        negative=True,
    )

    release_pretax = _release_four_column_row(
        release_income, "Income before income tax", next_label="Income tax expense"
    )
    release_tax = _release_four_column_row(
        release_income, "Income tax expense", next_label="Net income"
    )
    release_da = _release_four_column_row(
        release_cashflow, "Depreciation and amortization", next_label="Deferred income taxes"
    )
    release_capex = _release_four_column_row(
        release_cashflow,
        "Purchases related to property and equipment and intangible assets",
        next_label="Acquisitions, net of cash acquired",
        negative=True,
    )

    _check_equal("Income before income tax", filing_pretax.values, release_pretax.values)
    _check_equal("Income tax expense", filing_tax.values, release_tax.values)
    _check_equal("Depreciation and amortization", filing_da.values, release_da.values[2:])
    _check_equal("Cash capex", filing_capex.values, release_capex.values[2:])

    note_start, note_end = _unique_span(
        filing.content,
        _FILING_TAX_NOTE,
        description=f"{filing.id}: exact H1 income-tax note",
    )

    current_tax = _require_existing_operand(
        snapshot,
        identifier=CURRENT_TAX_FACT_ID,
        metric="income_tax_expense",
        value=filing_tax.values[2],
        period_start=H1_FY27_START,
        period_end=H1_FY27_END,
    )
    _require_existing_operand(
        snapshot,
        identifier=CURRENT_CAPEX_FACT_ID,
        metric="capex_cashflow",
        value=filing_capex.values[0],
        period_start=H1_FY27_START,
        period_end=H1_FY27_END,
    )

    basis_suffix = (
        f"; basis_context_chars=[{basis_start},{basis_end}); "
        f"basis_context_sha256={filing.content_sha256}"
    )
    da_fy27 = _source_fact(
        identifier="nvda-depreciation_amortization-h1-fy27",
        row=filing_da,
        value=filing_da.values[0],
        metric="depreciation_amortization",
        period_start=H1_FY27_START,
        period_end=H1_FY27_END,
        column="Jul 26, 2026 (H1 FY27 Six Months Ended; not a quarter)",
    ).model_copy(update={"location": _row_location(filing_da, column="Jul 26, 2026 (H1 FY27 Six Months Ended; not a quarter)") + basis_suffix})
    da_fy26 = _source_fact(
        identifier="nvda-depreciation_amortization-h1-fy26",
        row=filing_da,
        value=filing_da.values[1],
        metric="depreciation_amortization",
        period_start=H1_FY26_START,
        period_end=H1_FY26_END,
        column="Jul 27, 2025 (H1 FY26 Six Months Ended; not a quarter)",
    ).model_copy(update={"location": _row_location(filing_da, column="Jul 27, 2025 (H1 FY26 Six Months Ended; not a quarter)") + basis_suffix})
    pretax_fy27 = _source_fact(
        identifier="nvda-income_before_income_tax-h1-fy27",
        row=filing_pretax,
        value=filing_pretax.values[2],
        metric="income_before_income_tax",
        period_start=H1_FY27_START,
        period_end=H1_FY27_END,
        column="Jul 26, 2026 (H1 FY27 Six Months Ended; third numeric column)",
    ).model_copy(update={"location": _row_location(filing_pretax, column="Jul 26, 2026 (H1 FY27 Six Months Ended; third numeric column)") + basis_suffix})
    pretax_fy26 = _source_fact(
        identifier="nvda-income_before_income_tax-h1-fy26",
        row=filing_pretax,
        value=filing_pretax.values[3],
        metric="income_before_income_tax",
        period_start=H1_FY26_START,
        period_end=H1_FY26_END,
        column="Jul 27, 2025 (H1 FY26 Six Months Ended; fourth numeric column)",
    ).model_copy(update={"location": _row_location(filing_pretax, column="Jul 27, 2025 (H1 FY26 Six Months Ended; fourth numeric column)") + basis_suffix})
    tax_fy26 = _source_fact(
        identifier="nvda-income_tax_expense-h1-fy26",
        row=filing_tax,
        value=filing_tax.values[3],
        metric="income_tax_expense",
        period_start=H1_FY26_START,
        period_end=H1_FY26_END,
        column="Jul 27, 2025 (H1 FY26 Six Months Ended; fourth numeric column)",
    ).model_copy(update={"location": _row_location(filing_tax, column="Jul 27, 2025 (H1 FY26 Six Months Ended; fourth numeric column)") + basis_suffix})
    capex_fy26 = _source_fact(
        identifier="nvda-capex_cashflow-h1-fy26",
        row=filing_capex,
        value=filing_capex.values[1],
        metric="capex_cashflow",
        period_start=H1_FY26_START,
        period_end=H1_FY26_END,
        column="Jul 27, 2025 (H1 FY26 Six Months Ended; cash outflow sign retained)",
    ).model_copy(update={"location": _row_location(filing_capex, column="Jul 27, 2025 (H1 FY26 Six Months Ended; cash outflow sign retained)") + basis_suffix})

    rate_fy27 = _tax_rate_fact(
        identifier="nvda-effective_tax_rate-h1-fy27",
        tax=current_tax,
        pretax=pretax_fy27,
        stated_percent=Decimal("16.5"),
        note_source=filing,
        note_start=note_start,
        note_end=note_end,
    )
    rate_fy26 = _tax_rate_fact(
        identifier="nvda-effective_tax_rate-h1-fy26",
        tax=tax_fy26,
        pretax=pretax_fy26,
        stated_percent=Decimal("14.9"),
        note_source=filing,
        note_start=note_start,
        note_end=note_end,
    )
    candidates = (
        da_fy27,
        da_fy26,
        pretax_fy27,
        pretax_fy26,
        tax_fy26,
        capex_fy26,
        rate_fy27,
        rate_fy26,
    )
    return candidates, (CURRENT_TAX_FACT_ID, CURRENT_CAPEX_FACT_ID)


def _same_fact_semantics(left: FinancialFact, right: FinancialFact) -> bool:
    fields = (
        "id",
        "source_id",
        "metric",
        "value",
        "unit",
        "currency",
        "scale",
        "period_start",
        "period_end",
        "basis",
        "location",
        "segment",
        "inputs",
        "formula",
        "period_type",
        "supersedes_id",
    )
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _new_facts(
    snapshot: EvidenceSnapshot,
) -> tuple[tuple[FinancialFact, ...], tuple[str, ...]]:
    candidates, required_reused = _candidate_facts(snapshot)
    existing = {fact.id: fact for fact in snapshot.facts}
    additions: list[FinancialFact] = []
    reused = list(required_reused)
    for candidate in candidates:
        prior = existing.get(candidate.id)
        if prior is None:
            additions.append(candidate)
        elif _same_fact_semantics(prior, candidate):
            reused.append(candidate.id)
        else:
            raise ForecastFactExtractionError(
                f"forecast fact identifier conflicts with base snapshot: {candidate.id}"
            )
    return tuple(additions), tuple(dict.fromkeys(reused))


def extract_forecast_facts(snapshot: EvidenceSnapshot) -> tuple[FinancialFact, ...]:
    """Return only missing, strictly extracted forecast-calibration facts.

    Existing current-period H1 tax expense and cash capex are validated as exact
    operands but are intentionally not duplicated in the returned tuple.
    """

    if snapshot.ticker != "NVDA":
        raise ForecastFactExtractionError(
            f"forecast adapter requires ticker NVDA, found {snapshot.ticker!r}"
        )
    additions, _reused = _new_facts(snapshot)
    ids = [fact.id for fact in additions]
    if len(ids) != len(set(ids)):
        raise ForecastFactExtractionError("adapter generated duplicate fact identifiers")
    return additions


def _write_new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ForecastFactExtractionError(f"destination already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def enrich_snapshot(base_snapshot: Path, destination: Path) -> EnrichmentResult:
    """Write a new enriched snapshot while preserving every source and prior fact."""

    base_path = Path(base_snapshot).resolve()
    destination_path = Path(destination).resolve(strict=False)
    if not base_path.is_file():
        raise ForecastFactExtractionError(f"base snapshot is unavailable: {base_path}")
    source_directory = base_path.parent
    if destination_path == source_directory or destination_path.is_relative_to(
        source_directory
    ):
        raise ForecastFactExtractionError(
            "destination must be outside the frozen source snapshot directory"
        )
    if destination_path.exists():
        raise ForecastFactExtractionError(
            f"destination already exists: {destination_path}"
        )
    try:
        snapshot = EvidenceSnapshot.model_validate(read_json(base_path))
    except (OSError, ValueError) as exc:
        raise ForecastFactExtractionError(
            f"base evidence snapshot is unavailable or invalid: {base_path}"
        ) from exc

    additions, reused = _new_facts(snapshot)
    data = snapshot.model_dump(mode="json")
    data["sources"] = [source.model_dump(mode="json") for source in snapshot.sources]
    data["facts"] = [
        *(fact.model_dump(mode="json") for fact in snapshot.facts),
        *(fact.model_dump(mode="json") for fact in additions),
    ]
    data["gaps"] = list(dict.fromkeys((*snapshot.gaps, *FORECAST_FACT_CAVEATS)))
    enriched = EvidenceSnapshot.model_validate(data)

    if enriched.sources != snapshot.sources:
        raise ForecastFactExtractionError("source records changed during enrichment")
    if enriched.facts[: len(snapshot.facts)] != snapshot.facts:
        raise ForecastFactExtractionError("prior facts changed during enrichment")
    for before, after in zip(snapshot.sources, enriched.sources, strict=True):
        if before.content.encode("utf-8") != after.content.encode("utf-8"):
            raise ForecastFactExtractionError(
                f"source bytes changed during enrichment: {before.id}"
            )

    _write_new_file(destination_path, canonical_json(enriched))
    return EnrichmentResult(
        snapshot=enriched,
        base_snapshot=base_path,
        destination=destination_path,
        added_fact_ids=tuple(fact.id for fact in additions),
        reused_fact_ids=reused,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_snapshot", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        result = enrich_snapshot(args.base_snapshot, args.destination)
    except (ForecastFactExtractionError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"wrote {len(result.added_fact_ids)} NVDA forecast-calibration facts to "
        f"{result.destination}; reused={len(result.reused_fact_ids)}; "
        f"base={result.base_snapshot}"
    )


if __name__ == "__main__":
    main()
