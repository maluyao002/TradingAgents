"""Deterministic FRED evidence preparation for the news specialist."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from .financial_calculations import parse_decimal, plain_number
from .interface import route_to_vendor_with_metadata

# Price-index comparisons are extended to exact prior-year months by FRED's
# existing vintage-pinned implementation, independently of this display window.
MACRO_BASELINE = (
    ("CPIAUCSL", 90),
    ("PCEPILFE", 90),
    ("UNRATE", 90),
    ("FEDFUNDS", 90),
    ("DGS10", 30),
    ("T10Y2Y", 30),
)

_PRICE_INDEX_SERIES = frozenset({"CPIAUCSL", "PCEPILFE"})
_RATE_SERIES = frozenset({"UNRATE", "FEDFUNDS", "DGS10", "T10Y2Y"})
_NUMBER = r"[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_HEADER = re.compile(
    r"^## FRED: (?P<title>.+) \((?P<series>[A-Z0-9]+)\)\r?\n"
    r"- Units: (?P<units>[^\r\n]+)\r?\n"
    r"- Frequency: (?P<frequency>[^\r\n]+)\r?\n"
    r"- Window: (?P<window_start>\d{4}-\d{2}-\d{2}) to "
    r"(?P<window_end>\d{4}-\d{2}-\d{2})$",
    re.MULTILINE,
)
_LATEST = re.compile(
    rf"^\*\*Latest:\*\* (?P<latest>{_NUMBER}) "
    rf"\((?P<latest_date>\d{{4}}-\d{{2}}-\d{{2}})\)"
    rf"(?: \| \*\*Change over window:\*\* (?P<delta>{_NUMBER})"
    rf"(?: \((?P<relative>{_NUMBER})%\))? from (?P<first>{_NUMBER}) "
    rf"\((?P<first_date>\d{{4}}-\d{{2}}-\d{{2}})\))?$",
    re.MULTILINE,
)
_EXACT_CHANGE = re.compile(
    rf"^\*\*Exact 12-month change:\*\* (?P<delta>{_NUMBER}) index points"
    rf"(?: \((?P<percent>{_NUMBER})%\))? from (?P<base>{_NUMBER}) "
    rf"\((?P<base_date>\d{{4}}-\d{{2}}-\d{{2}})\)"
    r"(?P<zero_note>; percent change is undefined because the comparison value is zero)?\.$",
    re.MULTILINE,
)
_EXACT_UNAVAILABLE = re.compile(
    r"^\*\*Exact 12-month change:\*\* unavailable; no observation dated "
    r"(?P<base_date>\d{4}-\d{2}-\d{2}) is present at the "
    r"(?P<vintage>\d{4}-\d{2}-\d{2}) vintage\.$",
    re.MULTILINE,
)


def prepare_macro(date: str) -> dict[str, Any]:
    """Fetch each baseline series once and derive typed facts from known FRED output."""

    prepared: dict[str, Any] = {
        "role": "news",
        "analysis_date": date,
        "sources": [],
        "facts": [],
        "caveats": [],
        "required_evidence_ids": [],
    }
    for series, days in MACRO_BASELINE:
        try:
            result = route_to_vendor_with_metadata(
                "get_macro_indicators",
                indicator=series,
                curr_date=date,
                look_back_days=days,
            )
        except Exception as exc:
            prepared["caveats"].append(
                f"Macro series {series} unavailable ({type(exc).__name__})."
            )
            continue

        content = str(result.value)
        if not content.strip():
            prepared["caveats"].append(f"Macro series {series} unavailable: empty response.")
            continue

        retrieved_at = datetime.now(timezone.utc).isoformat()
        digest = sha256(content.encode()).hexdigest()[:12]
        source = {
            "id": f"news-macro-{series}-{digest}",
            "label": f"Macro baseline: {series}",
            "content": content,
            "vendor": result.vendor,
            "retrieved_at": retrieved_at,
            "published_at": None,
            "period": f"as_of:{date}",
            "basis": (
                "Observation dates, units, and vintage restrictions are recorded in the "
                "provider response."
            ),
        }
        prepared["sources"].append(source)

        if result.vendor == "unknown" or _is_unavailable_response(content):
            prepared["caveats"].append(
                f"Macro series {series} unavailable; see provider response {source['id']}."
            )
            continue

        facts, required_ids, caveats = _parse_fred_report(series, date, source)
        prepared["facts"].extend(facts)
        prepared["required_evidence_ids"].extend(required_ids)
        prepared["caveats"].extend(caveats)

    if prepared["sources"]:
        prepared["caveats"].append(
            "FRED formatter output does not expose release publication timestamps; "
            "published_at remains unknown."
        )
    return prepared


def _parse_fred_report(
    expected_series: str,
    analysis_date: str,
    source: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    content = source["content"]
    header = _HEADER.search(content)
    if header is None:
        return [], [], [_parse_caveat(expected_series, source["id"], "header")]
    if header.group("series") != expected_series or header.group("window_end") != analysis_date:
        return [], [], [_parse_caveat(expected_series, source["id"], "series/window metadata")]
    if not all(_valid_date(header.group(name)) for name in ("window_start", "window_end")):
        return [], [], [_parse_caveat(expected_series, source["id"], "window dates")]

    units = header.group("units").strip()
    frequency = header.group("frequency").strip()
    if not units or not frequency:
        return [], [], [_parse_caveat(expected_series, source["id"], "units/frequency")]

    latest_match = _LATEST.search(content)
    if latest_match is None:
        return [], [], [
            f"Macro series {expected_series} has no strictly parseable numeric observation in "
            f"source {source['id']}; raw source content is preserved."
        ]
    latest = parse_decimal(latest_match.group("latest"))
    latest_date = latest_match.group("latest_date")
    if latest is None or not _valid_observation_date(latest_date, analysis_date):
        return [], [], [_parse_caveat(expected_series, source["id"], "latest observation")]

    basis = f"FRED {frequency}; formatter vintage bounded by analysis date {analysis_date}"
    latest_fact = _fact(
        series=expected_series,
        metric="latest_level",
        value=latest,
        unit=units,
        period=f"observation:{latest_date}",
        basis=basis,
        source=source,
        kind="reported",
    )

    if expected_series in _PRICE_INDEX_SERIES:
        return _price_index_facts(
            expected_series,
            analysis_date,
            source,
            basis,
            units,
            latest,
            latest_date,
            latest_fact,
        )
    if expected_series in _RATE_SERIES:
        return _rate_facts(
            expected_series,
            analysis_date,
            source,
            basis,
            units,
            latest,
            latest_date,
            latest_fact,
            latest_match,
        )
    return [latest_fact], [latest_fact["id"]], []


def _price_index_facts(
    series: str,
    analysis_date: str,
    source: dict[str, Any],
    basis: str,
    units: str,
    latest: Decimal,
    latest_date: str,
    latest_fact: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    exact = _EXACT_CHANGE.search(source["content"])
    if exact is None:
        unavailable = _EXACT_UNAVAILABLE.search(source["content"])
        if unavailable is not None and unavailable.group("base_date") == _previous_year(latest_date):
            return [latest_fact], [latest_fact["id"]], [
                f"Exact 12-month {series} comparison is unavailable; source {source['id']} has "
                f"no observation dated {unavailable.group('base_date')}."
            ]
        return [latest_fact], [latest_fact["id"]], [
            f"Exact 12-month {series} comparison could not be parsed from source {source['id']}; "
            "no YoY fact was created."
        ]

    base = parse_decimal(exact.group("base"))
    displayed_delta = parse_decimal(exact.group("delta"))
    displayed_percent = parse_decimal(exact.group("percent"))
    base_date = exact.group("base_date")
    if (
        base is None
        or displayed_delta is None
        or base_date != _previous_year(latest_date)
        or not _valid_observation_date(base_date, analysis_date)
        or not _display_matches(latest - base, displayed_delta)
    ):
        return [], [], [_parse_caveat(series, source["id"], "exact 12-month comparison")]

    base_fact = _fact(
        series=series,
        metric="exact_12m_base",
        value=base,
        unit=units,
        period=f"observation:{base_date}",
        basis=basis,
        source=source,
        kind="reported",
    )
    inputs = [base_fact["id"], latest_fact["id"]]
    facts = [base_fact, latest_fact]
    period = f"{base_date}_to_{latest_date}"

    if base == 0:
        if displayed_percent is not None or exact.group("zero_note") is None:
            return [], [], [_parse_caveat(series, source["id"], "zero-base comparison")]
        change_fact = _fact(
            series=series,
            metric="exact_12m_index_point_change",
            value=latest - base,
            unit="index_points",
            period=period,
            basis=(
                "latest index level minus exact prior-year index level; percent undefined at "
                "zero base"
            ),
            source=source,
            kind="calculated",
            inputs=inputs,
            caveats=["YoY percent change is undefined because the exact prior-year base is zero."],
        )
        facts.append(change_fact)
        return facts, [change_fact["id"]], list(change_fact["caveats"])

    yoy = (latest - base) / base * Decimal(100)
    if displayed_percent is None or not _display_matches(yoy, displayed_percent):
        return [], [], [_parse_caveat(series, source["id"], "YoY percentage")]
    yoy_fact = _fact(
        series=series,
        metric="exact_yoy_change",
        value=yoy,
        unit="percent",
        period=period,
        basis="(latest index level / exact prior-year index level - 1) * 100",
        source=source,
        kind="calculated",
        inputs=inputs,
    )
    facts.append(yoy_fact)
    return facts, [yoy_fact["id"]], []


def _rate_facts(
    series: str,
    analysis_date: str,
    source: dict[str, Any],
    basis: str,
    units: str,
    latest: Decimal,
    latest_date: str,
    latest_fact: dict[str, Any],
    latest_match: re.Match[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    first = parse_decimal(latest_match.group("first"))
    displayed_delta = parse_decimal(latest_match.group("delta"))
    first_date = latest_match.group("first_date")
    if (
        first is None
        or displayed_delta is None
        or first_date is None
        or not _valid_observation_date(first_date, analysis_date)
        or first_date > latest_date
        or not _display_matches(latest - first, displayed_delta)
    ):
        return [latest_fact], [latest_fact["id"]], [
            f"Window change for macro series {series} could not be validated from source "
            f"{source['id']}; only the latest level was retained."
        ]
    if not _percent_units(units):
        return [latest_fact], [latest_fact["id"]], [
            f"Window change for rate series {series} has unsupported units {units!r}; "
            "no percentage-point change fact was created."
        ]

    first_fact = _fact(
        series=series,
        metric="window_start_level",
        value=first,
        unit=units,
        period=f"observation:{first_date}",
        basis=basis,
        source=source,
        kind="reported",
    )
    change_fact = _fact(
        series=series,
        metric="window_change",
        value=latest - first,
        unit="percentage_points",
        period=f"{first_date}_to_{latest_date}",
        basis="latest rate minus first rate in the requested window",
        source=source,
        kind="calculated",
        inputs=[first_fact["id"], latest_fact["id"]],
    )
    return [first_fact, latest_fact, change_fact], [change_fact["id"]], []


def _fact(
    *,
    series: str,
    metric: str,
    value: Decimal,
    unit: str,
    period: str,
    basis: str,
    source: dict[str, Any],
    kind: str,
    inputs: list[str] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    digest_input = "|".join((source["id"], series, metric, period, basis))
    digest = sha256(digest_input.encode()).hexdigest()[:16]
    return {
        "id": f"news-fact-macro-{series.lower()}-{metric.replace('_', '-')}-{digest}",
        "kind": kind,
        "metric": f"{series.lower()}_{metric}",
        "value": plain_number(value),
        "unit": unit,
        "period": period,
        "basis": basis,
        "source_id": source["id"],
        "published_at": None,
        "retrieved_at": source["retrieved_at"],
        "inputs": list(inputs or []),
        "caveats": list(caveats or []),
    }


def _display_matches(calculated: Decimal, displayed: Decimal) -> bool:
    return abs(calculated - displayed) <= Decimal("0.005")


def _is_unavailable_response(content: str) -> bool:
    normalized = content.lstrip().upper()
    return normalized.startswith(
        ("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:", "ERROR:", "ERROR ")
    )


def _percent_units(units: str) -> bool:
    normalized = units.strip().lower()
    return normalized in {"%", "percent", "percentage points", "percentage point"}


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_observation_date(value: str, analysis_date: str) -> bool:
    return _valid_date(value) and value <= analysis_date


def _previous_year(value: str) -> str:
    current = datetime.strptime(value, "%Y-%m-%d")
    try:
        return current.replace(year=current.year - 1).strftime("%Y-%m-%d")
    except ValueError:
        return f"{current.year - 1}-02-28"


def _parse_caveat(series: str, source_id: str, component: str) -> str:
    return (
        f"Macro series {series} has invalid or unrecognized {component} metadata in source "
        f"{source_id}; raw source content is preserved and no numeric facts were fabricated."
    )
