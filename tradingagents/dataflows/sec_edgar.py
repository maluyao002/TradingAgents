"""Point-in-time US-GAAP statement facts from SEC EDGAR companyfacts.

The public tool response contains selected, original SEC values and per-fact
filing provenance. The cached raw documents are never used as present-day
company profiles or treated as evidence of a ticker's historical identity.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from .config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from .utils import get_current_date

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")
_REPORT_FORMS = (*_ANNUAL_FORMS, "10-Q")
_SPANS = {"annual": (300, 400), "quarterly": (60, 115)}

# Canonical metric -> preferred tags. One tag supplies a metric in any one
# period; aliases are never summed. SEC units are retained without scaling.
_STATEMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "get_balance_sheet": {
        "total_assets": ("Assets",),
        "current_assets": ("AssetsCurrent",),
        "cash_and_cash_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
        "inventory": ("InventoryNet",),
        "total_liabilities": ("Liabilities",),
        "current_liabilities": ("LiabilitiesCurrent",),
        "shareholders_equity": (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        "shares_outstanding": ("CommonStockSharesOutstanding",),
    },
    "get_income_statement": {
        "revenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues", "SalesRevenueNet",
        ),
        "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
        "gross_profit": ("GrossProfit",),
        "operating_income": ("OperatingIncomeLoss",),
        "net_income": ("NetIncomeLoss",),
        "basic_average_shares": ("WeightedAverageNumberOfSharesOutstandingBasic",),
        "diluted_average_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "diluted_eps": ("EarningsPerShareDiluted",),
    },
    "get_cashflow": {
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
        "continuing_operations_operating_cash_flow": (
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities",),
        "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities",),
        "capital_expenditures": (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        "dividends_paid": ("PaymentsOfDividends",),
        "common_stock_repurchases": ("PaymentsForRepurchaseOfCommonStock",),
        "stock_based_compensation": ("ShareBasedCompensation",),
        "change_in_cash_and_cash_equivalents": (
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        ),
    },
}

_SHARE_METRICS = frozenset({
    "shares_outstanding", "basic_average_shares", "diluted_average_shares",
})
_PER_SHARE_METRICS = frozenset({"diluted_eps"})


def _user_agent() -> str:
    """Require a real contact; no project placeholder is sent to SEC."""
    value = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not value or "@" not in value:
        raise VendorNotConfiguredError(
            "SEC_EDGAR_USER_AGENT must identify the caller with a contact address"
        )
    return value


def _fetch_json(url: str) -> dict[str, Any]:
    try:
        response = requests.get(url, headers={"User-Agent": _user_agent()}, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise VendorRateLimitError(
            f"SEC EDGAR request unavailable ({status or type(exc).__name__})"
        ) from None
    except ValueError:
        raise VendorRateLimitError("SEC EDGAR returned invalid JSON") from None
    if not isinstance(data, dict):
        raise VendorRateLimitError("SEC EDGAR returned an invalid document")
    return data


def _cached_json(url: str, name: str) -> dict[str, Any]:
    path = Path(get_config()["data_cache_dir"]) / "sec_edgar" / name
    try:
        if path.exists() and time.time() - path.stat().st_mtime < _CACHE_TTL_SECONDS:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return cached
    except (OSError, ValueError):
        pass
    data = _fetch_json(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(data, handle, ensure_ascii=False)
            temporary = handle.name
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return data


def cik_for(ticker: str) -> str | None:
    """Resolve from today's SEC ticker map; this is not historical identity proof."""
    table = _cached_json(_TICKERS_URL, "company_tickers.json")
    if not all(isinstance(item, dict) for item in table.values()):
        raise VendorRateLimitError("SEC EDGAR returned an invalid ticker map")
    wanted = ticker.strip().upper()
    for item in table.values():
        if str(item.get("ticker", "")).upper() == wanted:
            try:
                return f"{int(item['cik_str']):010d}"
            except (KeyError, TypeError, ValueError):
                raise VendorRateLimitError("SEC EDGAR returned an invalid CIK") from None
    return None


def _iso_day(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _valid_value(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _report_form(value: Any, frequency: str) -> bool:
    """Accept primary annual/quarterly forms and their exact amendments."""
    if not isinstance(value, str):
        return False
    forms = _ANNUAL_FORMS if frequency == "annual" else _REPORT_FORMS
    return any(value == form or value == f"{form}/A" for form in forms)


def _eligible(raw: dict[str, Any], frequency: str, cutoff: str, *, instant: bool) -> bool:
    end = _iso_day(raw.get("end"))
    filed = _iso_day(raw.get("filed"))
    if (
        not end or not filed or end > cutoff or filed > cutoff
        or not _valid_value(raw.get("val"))
        or not _report_form(raw.get("form"), frequency)
        or not isinstance(raw.get("accn"), str) or not raw["accn"].strip()
    ):
        return False
    start = raw.get("start")
    if instant:
        if start is not None:
            return False
    else:
        start = _iso_day(start)
        if not start:
            return False
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        low, high = _SPANS[frequency]
        if not low <= days <= high:
            return False
    return True


def _select_tag(
    gaap: dict[str, Any], tag: str, metric: str, unit: str,
    frequency: str, cutoff: str, *, instant: bool,
) -> dict[str, dict[str, Any]]:
    tagged = gaap.get(tag)
    if tagged is None:
        return {}
    if not isinstance(tagged, dict) or not isinstance(tagged.get("units"), dict):
        raise VendorRateLimitError("SEC EDGAR returned invalid tag units")
    rows = tagged["units"].get(unit, [])
    if not isinstance(rows, list):
        raise VendorRateLimitError("SEC EDGAR returned invalid fact rows")
    eligible: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise VendorRateLimitError("SEC EDGAR returned invalid fact rows")
        if _eligible(raw, frequency, cutoff, instant=instant):
            eligible.append(raw)
    selected: dict[str, dict[str, Any]] = {}
    for row in eligible:
        end = row["end"]
        previous = selected.get(end)
        if previous is None or (row["filed"], str(row.get("accn", ""))) > (
            previous["filed"], str(previous.get("accn", ""))
        ):
            selected[end] = row
    return {
        end: {**row, "metric": metric, "taxonomy": "us-gaap", "tag": tag, "unit": unit}
        for end, row in selected.items()
    }


def _statement(method: str, ticker: str, freq: str, curr_date: str | None) -> dict[str, Any]:
    # Validate configuration before consulting even a warm disk cache.
    _user_agent()
    frequency = freq.lower()
    if frequency not in _SPANS:
        raise ValueError("SEC statement frequency must be annual or quarterly")
    cutoff = curr_date or get_current_date()
    if _iso_day(cutoff) is None:
        raise ValueError("SEC statement date must use YYYY-MM-DD")
    cik = cik_for(ticker)
    if cik is None:
        raise NoMarketDataError(
            ticker, ticker, "ticker absent from the current SEC filer map; historical identity unverified"
        )
    document = _cached_json(_FACTS_URL.format(cik=cik), f"CIK{cik}.json")
    outer = document.get("facts")
    if not isinstance(outer, dict):
        raise VendorRateLimitError("SEC EDGAR returned invalid company facts")
    gaap = outer.get("us-gaap")
    if gaap is None or gaap == {}:
        raise NoMarketDataError(ticker, ticker, "SEC filer has no US-GAAP facts")
    if not isinstance(gaap, dict):
        raise VendorRateLimitError("SEC EDGAR returned invalid US-GAAP facts")

    selected: list[dict[str, Any]] = []
    for metric, tags in _STATEMENTS[method].items():
        unit = (
            "shares" if metric in _SHARE_METRICS
            else "USD/shares" if metric in _PER_SHARE_METRICS
            else "USD"
        )
        by_period: dict[str, dict[str, Any]] = {}
        for tag in tags:
            for end, row in _select_tag(
                gaap, tag, metric, unit, frequency, cutoff,
                instant=method == "get_balance_sheet",
            ).items():
                by_period.setdefault(end, row)
        selected.extend(by_period.values())
    if not selected:
        raise NoMarketDataError(ticker, ticker, f"no {frequency} SEC facts filed by {cutoff}")
    selected.sort(key=lambda row: (row["metric"], row["end"]))
    return {
        "schema_version": 1,
        "vendor": "sec_edgar",
        "ticker": ticker.strip().upper(),
        "cik": cik,
        "method": method,
        "frequency": frequency,
        "as_of": cutoff,
        "facts": selected,
        "caveats": [
            "Ticker-to-CIK association comes from the current SEC ticker map and is not "
            "verified for the historical analysis date."
        ],
    }


def get_balance_sheet(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None,
) -> dict[str, Any]:
    return _statement("get_balance_sheet", ticker, freq, curr_date)


def get_cashflow(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None,
) -> dict[str, Any]:
    return _statement("get_cashflow", ticker, freq, curr_date)


def get_income_statement(
    ticker: str, freq: str = "quarterly", curr_date: str | None = None,
) -> dict[str, Any]:
    return _statement("get_income_statement", ticker, freq, curr_date)
