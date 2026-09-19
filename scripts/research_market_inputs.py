"""Strict offline parsing of frozen public market-reference pages.

This module never fetches.  It consumes only ``FileSourceCache`` entries and
fails closed when the small, intentionally explicit table shapes used below no
longer identify the requested observations unambiguously.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.sources import FetchedSource, FileSourceCache, SourceAccessError
from tradingagents.research.storage import canonical_json, read_json

H15_URL = "https://www.federalreserve.gov/releases/h15/"
ERP_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/home.htm"
BETA_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/Betas.html"
SEP_URL = "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm"

SOURCES = (
    ("market-fed-h15", H15_URL, "Federal Reserve H.15 selected interest rates", "Federal Reserve Board"),
    ("market-damodaran-erp", ERP_URL, "Damodaran implied equity risk premium", "Aswath Damodaran, NYU Stern"),
    ("market-damodaran-beta", BETA_URL, "Damodaran industry betas", "Aswath Damodaran, NYU Stern"),
    ("market-fed-sep", SEP_URL, "Federal Reserve Summary of Economic Projections", "Federal Reserve Board"),
)
SOURCE_IDS = tuple(item[0] for item in SOURCES)


class MarketInputParseError(ValueError):
    """Frozen market input cannot be located with the required exact structure."""


def _text(cell) -> str:
    return " ".join(" ".join(cell.stripped_strings).split())


def _row_cells(row) -> list[str]:
    return [_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]


def _one(items, description: str):
    if len(items) != 1:
        raise MarketInputParseError(f"expected exactly one {description}, found {len(items)}")
    return items[0]


def _percent(value: str, context: str) -> Decimal:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s*", value)
    if match is None:
        raise MarketInputParseError(f"{context}: expected a percent, found {value!r}")
    return Decimal(match.group(1)) / Decimal("100")


def _date(value: str, context: str) -> str:
    try:
        return datetime.strptime(value, "%Y %b %d").date().isoformat()
    except ValueError as exc:
        raise MarketInputParseError(f"{context}: invalid H.15 column date {value!r}") from exc


def _parse_h15(raw: bytes) -> tuple[Decimal, str]:
    soup = BeautifulSoup(raw, "html.parser")
    title = _text(soup.title) if soup.title else ""
    if "H.15 - Selected Interest Rates (Daily) - September 17, 2026" not in title:
        raise MarketInputParseError("H.15 release date/title changed")
    table = _one(soup.select("table#h15table"), "H.15 selected-rates table")
    rows = [values for row in table.find_all("tr") if (values := _row_cells(row))]
    header = rows[0] if rows else []
    if header[:1] != ["Instruments"] or header[1:] != ["2026 Sep 10", "2026 Sep 11", "2026 Sep 14", "2026 Sep 15", "2026 Sep 16"]:
        raise MarketInputParseError("H.15 date headers changed")
    nominal = _one([i for i, row in enumerate(rows) if row == ["Nominal 9", "", "", "", "", ""]], "H.15 nominal section")
    indexed = _one([i for i, row in enumerate(rows) if row == ["Inflation indexed 10", "", "", "", "", ""]], "H.15 inflation-indexed section")
    if nominal >= indexed:
        raise MarketInputParseError("H.15 nominal/inflation-indexed section order changed")
    candidates = [row for row in rows[nominal + 1:indexed] if row and row[0] == "10-year"]
    row = _one(candidates, "nominal H.15 10-year row")
    if len(row) != len(header):
        raise MarketInputParseError("H.15 nominal 10-year row width changed")
    return _percent(row[-1] + "%", "H.15 nominal 10-year"), _date(header[-1], "H.15")


def _parse_erp(raw: bytes) -> tuple[Decimal, str, Decimal]:
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    # A missing current-month rate must not borrow the previous month's pairing.
    text = text.split("Implied ERP in previous month", 1)[0]
    expression = re.compile(
        r"Implied ERP on September 1, 2026\s*=\s*(\d+\s*\.\s*\d+)%\s*"
        r"\(Trailing 12 month, with adjusted payout\).*?"
        r"US treasury rate of (\d+\.\d+)% used as the riskfree rate.*?"
        r"default spread \((\d+\.\d+)%\)",
        re.IGNORECASE,
    )
    match = _one(expression.findall(text), "September 1, 2026 adjusted-payout ERP statement")
    erp, risk_free, default_spread = match
    if default_spread != "0.22":
        raise MarketInputParseError("ERP default-spread disclosure changed")
    return Decimal(re.sub(r"\s+", "", erp)) / 100, "2026-09-01", Decimal(risk_free) / 100


def _parse_beta(raw: bytes) -> tuple[str, int, str]:
    soup = BeautifulSoup(raw, "html.parser")
    if re.search(r"Data used is as of January 2026\b", _text(soup)) is None:
        raise MarketInputParseError("Damodaran beta analysis date changed")
    tables = soup.find_all("table")
    table = _one(
        [table for table in tables if "Unlevered beta corrected for cash" in _text(table)],
        "Damodaran beta table",
    )
    rows = [_row_cells(row) for row in table.find_all("tr")]
    header = _one([row for row in rows if row and row[0] == "Industry Name"], "Damodaran beta header")
    expected = [
        "Industry Name", "Number of firms", "Beta", "D/E Ratio", "Effective Tax rate",
        "Unlevered beta", "Cash/Firm value", "Unlevered beta corrected for cash", "HiLo Risk",
        "Standard deviation of equity", "Standard deviation in operating income (last 10 years)",
    ]
    if header != expected:
        raise MarketInputParseError("Damodaran beta headers changed")
    semiconductor = _one([row for row in rows if row and row[0] == "Semiconductor"], "Semiconductor sector row")
    if len(semiconductor) != len(expected) or semiconductor[1] != "66" or semiconductor[2] != "1.52":
        raise MarketInputParseError("Damodaran Semiconductor row changed")
    if not re.fullmatch(r"\d+\.\d+", semiconductor[7]):
        raise MarketInputParseError("Damodaran cash-corrected beta is invalid")
    return semiconductor[7], 66, "2026-01"


def _parse_sep(raw: bytes) -> tuple[Decimal, Decimal]:
    soup = BeautifulSoup(raw, "html.parser")
    _one(
        [tag for tag in soup.find_all(["h4", "h5"]) if _text(tag) == "Summary of Economic Projections"],
        "SEP heading",
    )
    if re.search(r"September 15(?:–|-)16, 2026", soup.get_text(" ", strip=True)) is None:
        raise MarketInputParseError("SEP meeting date changed")
    expected_headers = ["2026", "2027", "2028", "2029", "Longer run"] * 3
    tables = []
    for table in soup.find_all("table"):
        rows = [_row_cells(row) for row in table.find_all("tr") if _row_cells(row)]
        if len(rows) >= 3 and rows[0] == ["Variable", "Median 1", "Central Tendency 2", "Range 3"] and rows[1] == expected_headers:
            tables.append(rows)
    rows = _one(tables, "SEP median projection table with exact headers")
    real = _one([row for row in rows if row and row[0] == "Change in real GDP"], "SEP real GDP row")
    inflation = _one([row for row in rows if row and row[0] == "PCE inflation"], "SEP PCE row")
    if len(real) != 16 or len(inflation) != 16:
        raise MarketInputParseError("SEP median row width changed")
    return _percent(real[5] + "%", "SEP longer-run real GDP"), _percent(inflation[5] + "%", "SEP longer-run PCE")


def _document(identifier: str, url: str, title: str, publisher: str, source: FetchedSource) -> SourceDocument:
    if source.status != 200 or source.media_type != "text/html" or source.text is None or source.text_sha256 is None:
        raise MarketInputParseError(f"{identifier}: required full-text HTML cache entry is unavailable")
    # ``retrieved_at`` is deliberately the first observed public availability in this frozen cache;
    # it is not represented as a claim about first publication by the publisher.
    return SourceDocument(
        id=identifier, url=url, title=title, publisher=publisher, retrieved_at=source.retrieved_at,
        published_at=source.retrieved_at, content=source.text, content_sha256=source.text_sha256,
        kind="market", availability="full_text",
    )


def parse_market_inputs(cache: FileSourceCache) -> tuple[tuple[SourceDocument, ...], dict]:
    """Read the four known cached URLs and return documents plus model-input metadata."""
    fetched: dict[str, FetchedSource] = {}
    documents: list[SourceDocument] = []
    for identifier, url, title, publisher in SOURCES:
        try:
            source = cache.get(url)
        except SourceAccessError as exc:
            raise MarketInputParseError(f"{identifier}: cache validation failed") from exc
        if source is None:
            raise MarketInputParseError(f"{identifier}: cached source is missing")
        fetched[identifier] = source
        documents.append(_document(identifier, url, title, publisher, source))
    risk_free, risk_free_as_of = _parse_h15(fetched["market-fed-h15"].raw)
    erp, erp_as_of, erp_risk_free = _parse_erp(fetched["market-damodaran-erp"].raw)
    beta, firms, beta_as_of = _parse_beta(fetched["market-damodaran-beta"].raw)
    real_growth, inflation = _parse_sep(fetched["market-fed-sep"].raw)
    return tuple(documents), {
        "risk_free_rate": str(risk_free), "risk_free_as_of": risk_free_as_of,
        "erp": str(erp), "erp_as_of": erp_as_of, "erp_risk_free_rate": str(erp_risk_free),
        "unlevered_beta_cash_corrected": beta, "beta_as_of": beta_as_of,
        "real_growth_long_run": str(real_growth), "inflation_long_run": str(inflation),
        "source_ids": list(SOURCE_IDS),
        "provenance": {
            "risk_free_rate": "H.15 nominal Treasury constant-maturity 10-year; not the TIPS rate.",
            "erp": "September 1 adjusted-payout trailing-12-month ERP paired with its stated 4.75% risk-free rate.",
            "beta": f"Damodaran January 2026 Semiconductor sector, {firms} firms; not Semiconductor Equip.",
            "macro": "SEP September 16 longer-run median real GDP and PCE inflation; nominal 4.04% compound context is not an NVDA forecast.",
        },
        "limitations": [
            "Source published_at records first observed availability in this cache, not the publisher's actual first-publication time.",
            "Do not recompute the September ERP using the later H.15 risk-free rate without disclosing the changed pairing.",
            "SEP longer-run medians are participant macro projections under appropriate policy, not a company forecast.",
        ],
    }


def _publish_new_files(blobs: dict[Path, bytes]) -> None:
    """Publish synced contents without replacement; preserve partial outputs.

    File contents are synced, but directory entries are not crash-atomic or ordered
    durably. Either file may survive a failure; no single path is a completion
    marker. Consumers must validate both files and their hash binding. Never unlink
    published paths on failure: an ownership check then unlink would race a writer
    replacing that path. Only private temporary staging names are cleaned up.
    """
    for destination in blobs:
        if os.path.lexists(destination):
            raise MarketInputParseError(f"destination already exists: {destination}")
    staged: dict[Path, Path] = {}
    try:
        for destination, payload in blobs.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
            temporary = staged[destination] = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for destination, temporary in staged.items():
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError as exc:
                raise MarketInputParseError(f"destination already exists: {destination}") from exc
        for destination, temporary in staged.items():
            current, owned = destination.lstat(), temporary.stat()
            if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                raise MarketInputParseError("published artifact replaced before completion")
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _write_new_file(destination: Path, payload: bytes) -> None:
    _publish_new_files({destination: payload})


def merge_evidence(base_snapshot: Path, cache_dir: Path, destination: Path) -> EvidenceSnapshot:
    """Write a new snapshot with market documents, leaving inputs and cache untouched."""
    base_path, cache_path = Path(base_snapshot).resolve(), Path(cache_dir).resolve()
    supplied = Path(destination).expanduser().absolute()
    if os.path.lexists(supplied):
        raise MarketInputParseError(f"destination already exists: {supplied}")
    destination = supplied.parent.resolve() / supplied.name
    if destination == base_path or destination.is_relative_to(base_path.parent):
        raise MarketInputParseError("destination must be a new path outside the base snapshot directory")
    try:
        base = EvidenceSnapshot.model_validate(read_json(base_path))
    except (OSError, ValueError) as exc:
        raise MarketInputParseError("base evidence snapshot is unavailable or invalid") from exc
    documents, inputs = parse_market_inputs(FileSourceCache(cache_path))
    duplicate = set(SOURCE_IDS) & {source.id for source in base.sources}
    if duplicate:
        raise MarketInputParseError("base snapshot already has market source ids: " + ", ".join(sorted(duplicate)))
    cutoff = max([base.cutoff, *(document.retrieved_at for document in documents)])
    data = base.model_dump(mode="json")
    data["cutoff"] = cutoff.isoformat()
    data["sources"] = [*(source.model_dump(mode="json") for source in base.sources), *(source.model_dump(mode="json") for source in documents)]
    merged = EvidenceSnapshot.model_validate(data)
    metadata = destination.with_suffix(destination.suffix + ".market-inputs.json")
    raw = canonical_json(merged)
    _publish_new_files({
        metadata: canonical_json({"market_inputs": inputs, "snapshot_sha256": hashlib.sha256(raw).hexdigest()}),
        destination: raw,
    })
    return merged
