"""Strict offline NVDA model-fact enrichment from a frozen evidence packet.

The adapter reads only the FY26 and Q2 FY27 issuer-release HTML already present
in ``FileSourceCache``.  It performs no HTTP requests or model calls.  Source
tables, headers, row labels, and selected column positions are checked exactly;
missing or changed cells fail closed rather than becoming zero.

The destination is caller-selected, must be outside the frozen source directory,
and must not already exist.  The source snapshot and cache are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from pathlib import Path

from bs4 import BeautifulSoup

from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.sources import FetchedSource, FileSourceCache
from tradingagents.research.storage import canonical_json, read_json

FY26_SOURCE_ID = "nvda-fy26-release"
Q2_SOURCE_ID = "nvda-q2-release"
REQUIRED_SOURCE_IDS = (FY26_SOURCE_ID, Q2_SOURCE_ID)
USD_MILLIONS = Decimal("1000000")
ACCOUNTING_BASIS = "US GAAP"

FY26_START = date(2025, 1, 27)
FY26_END = date(2026, 1, 25)
H1_FY26_END = date(2025, 7, 27)
H1_FY27_START = date(2026, 1, 26)
Q1_FY27_END = date(2026, 4, 26)
Q2_FY27_START = date(2026, 4, 27)
Q2_FY27_END = date(2026, 7, 26)
TTM_START = date(2025, 7, 28)

WORKING_CAPITAL_DEFINITION = (
    "aggregate-row working-capital proxy equal to accounts receivable plus inventories "
    "plus prepaid expenses and other current assets, less accounts payable and accrued "
    "and other current liabilities; the two mixed 'other' rows may contain tax, lease, "
    "or other non-operating components that the release does not disaggregate"
)
NET_DEBT_DEFINITION = (
    "short-term debt plus long-term debt less cash and cash equivalents reported "
    "in current assets; excludes marketable debt securities, marketable equity "
    "securities, non-marketable securities, leases, and other assets or liabilities"
)
MODEL_FACT_CAVEATS = (
    "Valuation input caveat: working_capital is an aggregate-row proxy, not pure operating "
    "working capital. Prepaid expenses and other current assets and accrued and other "
    "current liabilities may include tax, lease, or other non-operating components that "
    "the frozen issuer release does not disaggregate; use is illustrative only.",
    "Valuation input caveat: net_debt excludes all marketable and non-marketable securities "
    "and subtracts reported cash and cash equivalents. The frozen issuer release has no "
    "separate restricted-cash row, so legal availability requires independent note review.",
)

_NUMBER = re.compile(r"(?:0|[1-9]\d{0,2}(?:,\d{3})*)(?:\.\d+)?")


class ModelFactExtractionError(ValueError):
    """Raised when frozen evidence cannot support a required model fact exactly."""


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    snapshot: EvidenceSnapshot
    destination: Path
    base_snapshot: Path
    added_fact_ids: tuple[str, ...]
    reused_fact_ids: tuple[str, ...]


def _cells(row) -> list[str]:
    values = [
        cell.get_text(" ", strip=True)
        for cell in row.find_all(["th", "td"], recursive=False)
    ]
    return [value for value in values if value]


def _table_rows(raw: bytes, source_id: str, marker: str) -> tuple[list[list[str]], ...]:
    try:
        tables = BeautifulSoup(raw, "html.parser").find_all("table")
    except Exception as exc:
        raise ModelFactExtractionError(f"{source_id}: HTML parsing failed") from exc
    if len(tables) != 7:
        raise ModelFactExtractionError(
            f"{source_id}: expected 7 issuer tables, found {len(tables)}"
        )
    if marker not in tables[0].get_text(" ", strip=True):
        raise ModelFactExtractionError(
            f"{source_id}: table 1 marker {marker!r} is unavailable"
        )
    return tuple(
        [cells for row in table.find_all("tr") if (cells := _cells(row))]
        for table in tables
    )


def _require_headers(
    rows: Sequence[Sequence[str]],
    expected: Sequence[Sequence[str]],
    *,
    source_id: str,
    table_number: int,
) -> None:
    actual = list(rows[: len(expected)])
    normalized_expected = [list(row) for row in expected]
    if actual != normalized_expected:
        raise ModelFactExtractionError(
            f"{source_id}: table {table_number} headers changed; "
            f"expected {normalized_expected!r}, found {actual!r}"
        )


def _unique_row(
    rows: Sequence[Sequence[str]],
    label: str,
    *,
    source_id: str,
    table_number: int,
) -> list[str]:
    matches = [list(row) for row in rows if row and row[0] == label]
    if not matches:
        raise ModelFactExtractionError(
            f"{source_id}: table {table_number} required row {label!r} is unavailable"
        )
    if len(matches) != 1:
        raise ModelFactExtractionError(
            f"{source_id}: table {table_number} row {label!r} is ambiguous"
        )
    return matches[0]


def _number(cell: str, *, source_id: str, field: str, column: int) -> Decimal:
    if cell in {"-", "--", "—", ""}:
        raise ModelFactExtractionError(
            f"{source_id}: required field {field!r} column {column} is unavailable"
        )
    if _NUMBER.fullmatch(cell) is None:
        raise ModelFactExtractionError(
            f"{source_id}: required field {field!r} column {column} "
            f"contains unexpected value {cell!r}"
        )
    value = Decimal(cell.replace(",", ""))
    if not value.is_finite():
        raise ModelFactExtractionError(
            f"{source_id}: required field {field!r} column {column} is non-finite"
        )
    return value


def _money_values(
    row: Sequence[str],
    *,
    source_id: str,
    table_number: int,
    label: str,
    dollar_cells: bool,
) -> tuple[Decimal, Decimal]:
    expected_length = 5 if dollar_cells else 3
    if len(row) != expected_length:
        raise ModelFactExtractionError(
            f"{source_id}: table {table_number} row {label!r} expected "
            f"{expected_length} cells, found {len(row)}"
        )
    if dollar_cells:
        if row[1] != "$" or row[3] != "$":
            raise ModelFactExtractionError(
                f"{source_id}: table {table_number} row {label!r} currency columns changed"
            )
        indexes = (2, 4)
    else:
        indexes = (1, 2)
    return tuple(
        _number(row[index], source_id=source_id, field=label, column=index + 1)
        for index in indexes
    )  # type: ignore[return-value]


def _four_period_values(
    row: Sequence[str],
    *,
    source_id: str,
    table_number: int,
    label: str,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if len(row) != 9 or tuple(row[index] for index in (1, 3, 5, 7)) != (
        "$",
        "$",
        "$",
        "$",
    ):
        raise ModelFactExtractionError(
            f"{source_id}: table {table_number} row {label!r} column layout changed"
        )
    return tuple(
        _number(row[index], source_id=source_id, field=label, column=index + 1)
        for index in (2, 4, 6, 8)
    )  # type: ignore[return-value]


def _source_fact(
    *,
    identifier: str,
    source_id: str,
    metric: str,
    value: Decimal,
    period_start: date | None,
    period_end: date,
    period_type: str,
    basis: str,
    location: str,
    unit: str = "USD",
    currency: str | None = "USD",
) -> FinancialFact:
    return FinancialFact(
        id=identifier,
        source_id=source_id,
        metric=metric,
        value=value,
        scale=USD_MILLIONS,
        unit=unit,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,  # type: ignore[arg-type]
        basis=basis,
        location=location,
    )


def _derived_fact(
    *,
    identifier: str,
    source_id: str,
    metric: str,
    value: Decimal,
    period_start: date | None,
    period_end: date,
    period_type: str,
    basis: str,
    operands: Sequence[FinancialFact],
    operators: Sequence[str],
    location: str,
) -> FinancialFact:
    if len(operands) < 2 or len(operators) != len(operands) - 1:
        raise ModelFactExtractionError(f"{identifier}: invalid derived-fact expression")
    if any(operator not in {"+", "-"} for operator in operators):
        raise ModelFactExtractionError(f"{identifier}: unsupported derived-fact operator")
    first = operands[0]
    if any(
        (operand.unit, operand.currency, operand.scale)
        != (first.unit, first.currency, first.scale)
        for operand in operands[1:]
    ):
        raise ModelFactExtractionError(f"{identifier}: derived operands have incompatible units")
    with localcontext(Context(prec=50)):
        calculated = first.value
        for operator, operand in zip(operators, operands[1:], strict=True):
            calculated = calculated + operand.value if operator == "+" else calculated - operand.value
    if calculated != value:
        raise ModelFactExtractionError(f"{identifier}: derived value does not match operands")
    formula_parts = [first.id]
    for operator, operand in zip(operators, operands[1:], strict=True):
        formula_parts.extend((operator, operand.id))
    formula = " ".join(formula_parts)
    return FinancialFact(
        id=identifier,
        source_id=source_id,
        metric=metric,
        value=value,
        scale=first.scale,
        unit=first.unit,
        currency=first.currency,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,  # type: ignore[arg-type]
        basis=basis,
        location=location,
        inputs=tuple(operand.id for operand in operands),
        formula=formula,
    )


def _extract_revenue_and_shares(
    fy26_tables: Sequence[Sequence[Sequence[str]]],
    q2_tables: Sequence[Sequence[Sequence[str]]],
) -> list[FinancialFact]:
    fy_rows = fy26_tables[2]
    _require_headers(
        fy_rows,
        (
            ("NVIDIA CORPORATION",),
            ("CONDENSED CONSOLIDATED STATEMENTS OF INCOME",),
            ("(In millions, except per share data)",),
            ("(Unaudited)",),
            ("Three Months Ended", "Twelve Months Ended"),
            ("January 25,", "January 26,", "January 25,", "January 26,"),
            ("2026", "2025", "2026", "2025"),
        ),
        source_id=FY26_SOURCE_ID,
        table_number=3,
    )
    fy_revenue_row = _unique_row(
        fy_rows, "Revenue", source_id=FY26_SOURCE_ID, table_number=3
    )
    _q4_fy26, _q4_fy25, fy26_revenue, _fy25_revenue = _four_period_values(
        fy_revenue_row,
        source_id=FY26_SOURCE_ID,
        table_number=3,
        label="Revenue",
    )
    annual = _source_fact(
        identifier="nvda-revenue-fy26",
        source_id=FY26_SOURCE_ID,
        metric="revenue",
        value=fy26_revenue,
        period_start=FY26_START,
        period_end=FY26_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            "HTML table 3, CONDENSED CONSOLIDATED STATEMENTS OF INCOME, "
            "row 'Revenue', cell 7 (FY26 Twelve Months Ended); USD millions"
        ),
    )

    q2_rows = q2_tables[2]
    _require_headers(
        q2_rows,
        (
            ("NVIDIA CORPORATION",),
            ("CONDENSED CONSOLIDATED STATEMENTS OF INCOME",),
            ("(In millions, except per share data)",),
            ("(Unaudited)",),
            ("Three Months Ended", "Six Months Ended"),
            ("July 26,", "July 27,", "July 26,", "July 27,"),
            ("2026", "2025", "2026", "2025"),
        ),
        source_id=Q2_SOURCE_ID,
        table_number=3,
    )
    q2_revenue_row = _unique_row(
        q2_rows, "Revenue", source_id=Q2_SOURCE_ID, table_number=3
    )
    _q2_fy27, _q2_fy26, h1_fy27_revenue, h1_fy26_revenue = _four_period_values(
        q2_revenue_row,
        source_id=Q2_SOURCE_ID,
        table_number=3,
        label="Revenue",
    )
    h1_fy26 = _source_fact(
        identifier="nvda-revenue-h1-fy26",
        source_id=Q2_SOURCE_ID,
        metric="revenue",
        value=h1_fy26_revenue,
        period_start=FY26_START,
        period_end=H1_FY26_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            "HTML table 3, CONDENSED CONSOLIDATED STATEMENTS OF INCOME, "
            "row 'Revenue', cell 9 (H1 FY26 Six Months Ended); USD millions"
        ),
    )
    h1_fy27 = _source_fact(
        identifier="nvda-revenue-h1-fy27",
        source_id=Q2_SOURCE_ID,
        metric="revenue",
        value=h1_fy27_revenue,
        period_start=H1_FY27_START,
        period_end=Q2_FY27_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            "HTML table 3, CONDENSED CONSOLIDATED STATEMENTS OF INCOME, "
            "row 'Revenue', cell 7 (H1 FY27 Six Months Ended); USD millions"
        ),
    )
    ttm_value = annual.value - h1_fy26.value + h1_fy27.value
    ttm = _derived_fact(
        identifier="nvda-revenue-ttm-q2-fy27",
        source_id=Q2_SOURCE_ID,
        metric="revenue",
        value=ttm_value,
        period_start=TTM_START,
        period_end=Q2_FY27_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        operands=(annual, h1_fy26, h1_fy27),
        operators=("-", "+"),
        location=(
            "derived TTM revenue from FY26 annual revenue less H1 FY26 revenue "
            "plus H1 FY27 revenue; US GAAP operands retain annual/H1 period lineage"
        ),
    )

    context_label = "Weighted average shares used in per share computation:"
    context_positions = [index for index, row in enumerate(q2_rows) if row == [context_label]]
    if len(context_positions) != 1:
        raise ModelFactExtractionError(
            f"{Q2_SOURCE_ID}: table 3 shares section {context_label!r} is unavailable or ambiguous"
        )
    shares_index = context_positions[0]
    if shares_index + 2 >= len(q2_rows) or q2_rows[shares_index + 2][0] != "Diluted":
        raise ModelFactExtractionError(
            f"{Q2_SOURCE_ID}: table 3 diluted weighted-average shares row position changed"
        )
    diluted_row = list(q2_rows[shares_index + 2])
    if len(diluted_row) != 5:
        raise ModelFactExtractionError(
            f"{Q2_SOURCE_ID}: table 3 diluted shares column layout changed"
        )
    latest_diluted_shares = _number(
        diluted_row[1],
        source_id=Q2_SOURCE_ID,
        field="Weighted average diluted shares",
        column=2,
    )
    shares = _source_fact(
        identifier="nvda-diluted-shares-q2-fy27",
        source_id=Q2_SOURCE_ID,
        metric="weighted_average_diluted_shares",
        value=latest_diluted_shares,
        period_start=Q2_FY27_START,
        period_end=Q2_FY27_END,
        period_type="duration",
        basis=ACCOUNTING_BASIS,
        location=(
            "HTML table 3, CONDENSED CONSOLIDATED STATEMENTS OF INCOME, "
            "row 'Diluted' under weighted-average shares, cell 2 (Q2 FY27); millions of "
            "common shares; quarterly duration proxy, never a point-in-time share count"
        ),
        unit="shares",
        currency=None,
    )
    return [annual, h1_fy26, h1_fy27, ttm, shares]


def _extract_balance_facts(
    q2_tables: Sequence[Sequence[Sequence[str]]],
) -> list[FinancialFact]:
    rows = q2_tables[3]
    _require_headers(
        rows,
        (
            ("NVIDIA CORPORATION",),
            ("CONDENSED CONSOLIDATED BALANCE SHEETS",),
            ("(In millions)",),
            ("(Unaudited)",),
            ("July 26,", "January 25,"),
            ("2026", "2026"),
            ("ASSETS",),
        ),
        source_id=Q2_SOURCE_ID,
        table_number=4,
    )
    specifications = (
        ("Cash and cash equivalents", "cash_and_cash_equivalents", True),
        ("Marketable debt securities", "marketable_debt_securities", False),
        ("Marketable equity securities", "marketable_equity_securities", False),
        ("Accounts receivable, net", "accounts_receivable", False),
        ("Inventories", "inventory", False),
        ("Prepaid expenses and other current assets", "prepaid_and_other_current_assets", False),
        ("Accounts payable", "accounts_payable", True),
        ("Accrued and other current liabilities", "accrued_and_other_current_liabilities", False),
        ("Short-term debt", "short_term_debt", False),
        ("Long-term debt", "long_term_debt", False),
    )
    by_metric: dict[str, tuple[FinancialFact, FinancialFact]] = {}
    result: list[FinancialFact] = []
    for label, metric, dollar_cells in specifications:
        row = _unique_row(rows, label, source_id=Q2_SOURCE_ID, table_number=4)
        latest_value, fy26_value = _money_values(
            row,
            source_id=Q2_SOURCE_ID,
            table_number=4,
            label=label,
            dollar_cells=dollar_cells,
        )
        latest = _source_fact(
            identifier=f"nvda-model-{metric}-q2-fy27-end",
            source_id=Q2_SOURCE_ID,
            metric=metric,
            value=latest_value,
            period_start=None,
            period_end=Q2_FY27_END,
            period_type="instant",
            basis=ACCOUNTING_BASIS,
            location=(
                "HTML table 4, CONDENSED CONSOLIDATED BALANCE SHEETS, "
                f"row {label!r}, first numeric column (July 26, 2026); USD millions"
            ),
        )
        fy26 = _source_fact(
            identifier=f"nvda-model-{metric}-fy26-end",
            source_id=Q2_SOURCE_ID,
            metric=metric,
            value=fy26_value,
            period_start=None,
            period_end=FY26_END,
            period_type="instant",
            basis=ACCOUNTING_BASIS,
            location=(
                "HTML table 4, CONDENSED CONSOLIDATED BALANCE SHEETS, "
                f"row {label!r}, second numeric column (January 25, 2026); USD millions"
            ),
        )
        by_metric[metric] = (latest, fy26)
        result.extend((latest, fy26))

    wc_metrics = (
        "accounts_receivable",
        "inventory",
        "prepaid_and_other_current_assets",
        "accounts_payable",
        "accrued_and_other_current_liabilities",
    )
    debt_metrics = ("short_term_debt", "long_term_debt", "cash_and_cash_equivalents")
    for index, suffix, period_end in (
        (0, "q2-fy27-end", Q2_FY27_END),
        (1, "fy26-end", FY26_END),
    ):
        wc_operands = tuple(by_metric[metric][index] for metric in wc_metrics)
        wc_value = (
            wc_operands[0].value
            + wc_operands[1].value
            + wc_operands[2].value
            - wc_operands[3].value
            - wc_operands[4].value
        )
        result.append(
            _derived_fact(
                identifier=f"nvda-operating-working-capital-{suffix}",
                source_id=Q2_SOURCE_ID,
                metric="working_capital",
                value=wc_value,
                period_start=None,
                period_end=period_end,
                period_type="instant",
                basis=ACCOUNTING_BASIS,
                operands=wc_operands,
                operators=("+", "+", "-", "-"),
                location=f"derived operating working capital; definition: {WORKING_CAPITAL_DEFINITION}",
            )
        )

        debt_operands = tuple(by_metric[metric][index] for metric in debt_metrics)
        net_debt_value = debt_operands[0].value + debt_operands[1].value - debt_operands[2].value
        result.append(
            _derived_fact(
                identifier=f"nvda-net-debt-{suffix}",
                source_id=Q2_SOURCE_ID,
                metric="net_debt",
                value=net_debt_value,
                period_start=None,
                period_end=period_end,
                period_type="instant",
                basis=ACCOUNTING_BASIS,
                operands=debt_operands,
                operators=("+", "-"),
                location=(
                    f"derived strict cash-only net debt; definition: {NET_DEBT_DEFINITION}. "
                    "The checked release table has no separate restricted-cash row; legal "
                    "availability of reported cash requires independent note review."
                ),
            )
        )
    return result


def extract_model_facts(fy26_raw: bytes, q2_raw: bytes) -> tuple[FinancialFact, ...]:
    """Extract source facts and validated derived model anchors from release HTML."""

    fy26_tables = _table_rows(fy26_raw, FY26_SOURCE_ID, "Q4 FY26")
    q2_tables = _table_rows(q2_raw, Q2_SOURCE_ID, "Q2 FY27")
    facts = [
        *_extract_revenue_and_shares(fy26_tables, q2_tables),
        *_extract_balance_facts(q2_tables),
    ]
    ids = [fact.id for fact in facts]
    if len(ids) != len(set(ids)):
        raise ModelFactExtractionError("adapter generated duplicate fact identifiers")
    return tuple(facts)


def _acquisition_records(source_dir: Path) -> dict[str, dict[str, object]]:
    payload = read_json(source_dir / "acquisition.json")
    if not isinstance(payload, list):
        raise ModelFactExtractionError("acquisition.json must contain a list")
    records: dict[str, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ModelFactExtractionError("acquisition.json contains an invalid record")
        identifier = item["id"]
        if identifier in records:
            raise ModelFactExtractionError(f"acquisition.json duplicates source {identifier}")
        records[identifier] = item
    return records


def _frozen_html(
    *,
    source: SourceDocument,
    acquisition: dict[str, object] | None,
    cache: FileSourceCache,
) -> FetchedSource:
    if acquisition is None:
        raise ModelFactExtractionError(f"{source.id}: acquisition metadata is unavailable")
    fetched = cache.get(source.url)
    if fetched is None:
        raise ModelFactExtractionError(f"{source.id}: frozen FileSourceCache entry is unavailable")
    expected = {
        "requested_url": source.url,
        "raw_sha256": fetched.raw_sha256,
        "text_sha256": source.content_sha256,
    }
    for field, expected_value in expected.items():
        if acquisition.get(field) != expected_value:
            raise ModelFactExtractionError(
                f"{source.id}: acquisition {field} does not match frozen source/cache"
            )
    if fetched.text_sha256 != source.content_sha256 or fetched.text != source.content:
        raise ModelFactExtractionError(
            f"{source.id}: cached extracted text does not match evidence content hash"
        )
    if fetched.media_type != "text/html":
        raise ModelFactExtractionError(
            f"{source.id}: cached media type {fetched.media_type!r} is not HTML"
        )
    if fetched.status != 200:
        raise ModelFactExtractionError(
            f"{source.id}: cached HTTP status {fetched.status} is not successful"
        )
    if hashlib.sha256(fetched.raw).hexdigest() != fetched.raw_sha256:
        raise ModelFactExtractionError(f"{source.id}: cached raw HTML hash mismatch")
    return fetched


def _write_new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
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
        "segment",
        "inputs",
        "formula",
        "period_type",
        "supersedes_id",
    )
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _merge_model_facts(
    existing: Sequence[FinancialFact], extracted: Sequence[FinancialFact]
) -> tuple[tuple[FinancialFact, ...], tuple[str, ...]]:
    existing_by_id = {fact.id: fact for fact in existing}
    additions: list[FinancialFact] = []
    reused: list[str] = []
    for fact in extracted:
        prior = existing_by_id.get(fact.id)
        if prior is None:
            additions.append(fact)
            continue
        if fact.inputs:
            raise ModelFactExtractionError(
                f"new derived model fact identifier collides with base snapshot: {fact.id}"
            )
        if not _same_fact_semantics(prior, fact):
            raise ModelFactExtractionError(
                f"extracted source fact conflicts with base snapshot: {fact.id}"
            )
        reused.append(fact.id)
    return tuple(additions), tuple(reused)


def _load_base_snapshot(
    source_dir: Path, supplied_base: Path | None
) -> tuple[EvidenceSnapshot, Path]:
    frozen_path = source_dir / "evidence.json"
    try:
        frozen = EvidenceSnapshot.model_validate(read_json(frozen_path))
    except (OSError, ValueError) as exc:
        raise ModelFactExtractionError(
            f"frozen evidence snapshot is unavailable or invalid: {frozen_path}"
        ) from exc
    if supplied_base is not None:
        base_path = Path(supplied_base).resolve()
    else:
        enriched_candidate = source_dir / "evidence_with_facts.json"
        base_path = enriched_candidate if enriched_candidate.is_file() else frozen_path
    try:
        base = EvidenceSnapshot.model_validate(read_json(base_path))
    except (OSError, ValueError) as exc:
        raise ModelFactExtractionError(
            f"base evidence snapshot is unavailable or invalid: {base_path}"
        ) from exc
    if (
        base.ticker != frozen.ticker
        or base.cutoff != frozen.cutoff
        or base.instrument != frozen.instrument
    ):
        raise ModelFactExtractionError(
            "base snapshot identity, cutoff, or instrument differs from frozen evidence.json"
        )
    if base.sources != frozen.sources:
        raise ModelFactExtractionError(
            "base snapshot sources differ from frozen evidence.json"
        )
    return base, base_path


def enrich_snapshot(
    source_dir: Path,
    destination: Path,
    *,
    base_snapshot: Path | None = None,
) -> EnrichmentResult:
    """Create one new enriched snapshot without mutating the frozen packet."""

    source_dir = Path(source_dir).resolve()
    destination = Path(destination).resolve(strict=False)
    if not source_dir.is_dir():
        raise ModelFactExtractionError(f"source directory is unavailable: {source_dir}")
    if destination == source_dir or destination.is_relative_to(source_dir):
        raise ModelFactExtractionError("destination must be outside the frozen source directory")
    if destination.exists():
        raise ModelFactExtractionError(f"destination already exists: {destination}")

    snapshot, base_path = _load_base_snapshot(source_dir, base_snapshot)
    sources = {source.id: source for source in snapshot.sources}
    missing_sources = [identifier for identifier in REQUIRED_SOURCE_IDS if identifier not in sources]
    if missing_sources:
        raise ModelFactExtractionError(
            "required evidence sources are unavailable: " + ", ".join(missing_sources)
        )
    acquisitions = _acquisition_records(source_dir)
    cache = FileSourceCache(source_dir / "source-cache")
    fy26 = _frozen_html(
        source=sources[FY26_SOURCE_ID],
        acquisition=acquisitions.get(FY26_SOURCE_ID),
        cache=cache,
    )
    q2 = _frozen_html(
        source=sources[Q2_SOURCE_ID],
        acquisition=acquisitions.get(Q2_SOURCE_ID),
        cache=cache,
    )
    extracted_facts = extract_model_facts(fy26.raw, q2.raw)
    facts, reused_fact_ids = _merge_model_facts(snapshot.facts, extracted_facts)
    data = snapshot.model_dump(mode="json")
    data["facts"] = [
        *(fact.model_dump(mode="json") for fact in snapshot.facts),
        *(fact.model_dump(mode="json") for fact in facts),
    ]
    data["gaps"] = list(dict.fromkeys((*snapshot.gaps, *MODEL_FACT_CAVEATS)))
    enriched = EvidenceSnapshot.model_validate(data)
    _write_new_file(destination, canonical_json(enriched))
    return EnrichmentResult(
        snapshot=enriched,
        destination=destination,
        base_snapshot=base_path,
        added_fact_ids=tuple(fact.id for fact in facts),
        reused_fact_ids=reused_fact_ids,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="frozen packet inputs directory")
    parser.add_argument("destination", type=Path, help="new snapshot path outside source_dir")
    parser.add_argument(
        "--base-snapshot",
        type=Path,
        help=(
            "validated snapshot whose existing facts are preserved; defaults to "
            "source_dir/evidence_with_facts.json when present, otherwise evidence.json"
        ),
    )
    args = parser.parse_args()
    try:
        result = enrich_snapshot(
            args.source_dir,
            args.destination,
            base_snapshot=args.base_snapshot,
        )
    except (ModelFactExtractionError, OSError, ValueError) as exc:
        parser.error(str(exc))
    by_id = {fact.id: fact for fact in result.snapshot.facts}
    key_ids = (
        "nvda-revenue-fy26",
        "nvda-revenue-h1-fy26",
        "nvda-revenue-h1-fy27",
        "nvda-revenue-ttm-q2-fy27",
        "nvda-operating-working-capital-fy26-end",
        "nvda-operating-working-capital-q2-fy27-end",
        "nvda-net-debt-fy26-end",
        "nvda-net-debt-q2-fy27-end",
        "nvda-diluted-shares-q2-fy27",
    )
    print(
        f"wrote {len(result.added_fact_ids)} source-bound facts to {result.destination}; "
        f"reused={len(result.reused_fact_ids)}; base={result.base_snapshot}"
    )
    for identifier in key_ids:
        fact = by_id[identifier]
        print(f"{identifier}={fact.value} scale={fact.scale} source={fact.source_id}")


if __name__ == "__main__":
    main()
