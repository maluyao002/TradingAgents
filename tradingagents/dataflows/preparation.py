"""Deterministic preparation of market and fundamental evidence for analysts."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from . import date_window
from .financial_calculations import free_cash_flow, parse_decimal, plain_number, ratio
from .interface import route_to_vendor_with_metadata
from .market_data_validator import (
    build_verified_market_snapshot_data,
    market_finality_caveat,
    render_verified_market_snapshot,
)

_STATEMENT_METHODS = {
    "get_balance_sheet": "Balance sheet",
    "get_cashflow": "Cash flow statement",
    "get_income_statement": "Income statement",
}

_ALPHA_FIELDS = {
    "get_income_statement": {
        "totalRevenue": "revenue",
        "grossProfit": "gross_profit",
        "operatingIncome": "operating_income",
        "netIncome": "net_income",
    },
    "get_balance_sheet": {
        "totalCurrentAssets": "current_assets",
        "totalCurrentLiabilities": "current_liabilities",
        "cashAndCashEquivalentsAtCarryingValue": "cash_and_cash_equivalents",
        "cashAndShortTermInvestments": "cash_and_short_term_investments",
        "inventory": "inventory",
        "currentNetReceivables": "current_net_receivables",
        "totalAssets": "total_assets",
        "goodwill": "goodwill",
        "intangibleAssets": "intangible_assets",
        "intangibleAssetsExcludingGoodwill": "intangible_assets_excluding_goodwill",
        "shortTermDebt": "short_term_debt",
        "currentLongTermDebt": "current_long_term_debt",
        "longTermDebtCurrent": "current_long_term_debt",
        "longTermDebtNoncurrent": "long_term_debt",
        "totalLiabilities": "total_liabilities",
        "totalShareholderEquity": "shareholders_equity",
        "commonStockSharesOutstanding": "shares_outstanding",
    },
    "get_cashflow": {
        # Alpha Vantage documents only the total operatingCashflow row here;
        # continuing/discontinued components must remain absent rather than zero.
        "operatingCashflow": "operating_cash_flow",
        "capitalExpenditures": "capital_expenditures",
        "cashflowFromInvestment": "investing_cash_flow",
        "cashflowFromFinancing": "financing_cash_flow",
        "dividendPayout": "dividends_paid",
        "paymentsForRepurchaseOfCommonStock": "common_stock_repurchases",
        "proceedsFromIssuanceOfCommonStock": "common_stock_issuance",
        "proceedsFromStockOptions": "stock_option_proceeds",
        "changeInCashAndCashEquivalents": "change_in_cash_and_cash_equivalents",
    },
}

_YFINANCE_FIELDS = {
    "get_income_statement": {
        "Total Revenue": "revenue",
        "Gross Profit": "gross_profit",
        "Operating Income": "operating_income",
        "Total Operating Income As Reported": "total_operating_income_as_reported",
        "Restructuring And Mergern Acquisition": "restructuring_and_merger_acquisition",
        "Basic Average Shares": "basic_average_shares",
        "Diluted Average Shares": "diluted_average_shares",
        "Net Income": "net_income",
    },
    "get_balance_sheet": {
        "Current Assets": "current_assets",
        "Total Current Assets": "current_assets",
        "Current Liabilities": "current_liabilities",
        "Total Current Liabilities": "current_liabilities",
        "Cash And Cash Equivalents": "cash_and_cash_equivalents",
        "Cash Cash Equivalents And Short Term Investments": "cash_and_short_term_investments",
        "Inventory": "inventory",
        "Receivables": "receivables",
        "Accounts Receivable": "accounts_receivable",
        "Total Assets": "total_assets",
        "Goodwill": "goodwill",
        "Other Intangible Assets": "intangible_assets_excluding_goodwill",
        "Goodwill And Other Intangible Assets": "intangible_assets",
        "Current Debt": "short_term_debt",
        "Long Term Debt": "long_term_debt",
        "Total Debt": "total_debt",
        "Net Debt": "net_debt",
        "Total Liabilities Net Minority Interest": "total_liabilities",
        "Stockholders Equity": "shareholders_equity",
        "Ordinary Shares Number": "shares_outstanding",
        "Share Issued": "shares_issued",
    },
    "get_cashflow": {
        "Operating Cash Flow": "operating_cash_flow",
        "Total Cash From Operating Activities": "operating_cash_flow",
        "Cash Flow From Continuing Operating Activities": (
            "continuing_operations_operating_cash_flow"
        ),
        "Cash From Discontinued Operating Activities": (
            "discontinued_operations_operating_cash_flow"
        ),
        "Capital Expenditure": "capital_expenditures",
        "Capital Expenditures": "capital_expenditures",
        "Free Cash Flow": "free_cash_flow",
        "Investing Cash Flow": "investing_cash_flow",
        "Financing Cash Flow": "financing_cash_flow",
        "Common Stock Dividend Paid": "dividends_paid",
        "Repurchase Of Capital Stock": "common_stock_repurchases",
        "Issuance Of Capital Stock": "common_stock_issuance",
        "Stock Based Compensation": "stock_based_compensation",
        "Changes In Cash": "change_in_cash_and_cash_equivalents",
    },
}

_SHARE_METRICS = frozenset({
    "shares_outstanding", "shares_issued",
    "basic_average_shares", "diluted_average_shares",
})
_PRICE_INDICATORS = frozenset({
    "close_10_ema", "close_50_sma", "close_200_sma",
    "boll", "boll_ub", "boll_lb", "macd", "macds", "macdh", "atr",
})


def _retrieved_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_id(kind: str, vendor: str, ticker: str, content: str) -> str:
    identity = f"{ticker.upper()}|{content}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{vendor}:{digest}"


def _fact(
    *,
    metric: str,
    value: int | float,
    unit: str | None,
    period: str,
    basis: str,
    source_id: str,
    kind: str,
    inputs: list[str] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    role_prefix = "market" if source_id.startswith("market-") else "fundamentals"
    digest_input = "|".join((source_id, basis, period, metric, kind))
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    fact_id = f"{role_prefix}-fact-{metric.replace('_', '-')}-{digest}"
    return {
        "id": fact_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": period,
        "basis": basis,
        "source_id": source_id,
        "kind": kind,
        "inputs": list(inputs or []),
        "caveats": list(caveats or []),
    }


def prepare_market(ticker: str, date: str) -> dict[str, Any]:
    """Return raw verified market evidence and its exact latest numeric facts."""
    caveats: list[str] = []
    try:
        snapshot = build_verified_market_snapshot_data(ticker, date)
    except Exception as exc:  # keep the preparation contract useful on no-data paths
        return {
            "analysis_date": date,
            "sources": [],
            "facts": [],
            "caveats": [
                f"Verified market data unavailable ({type(exc).__name__})."
            ],
        }

    finality_caveat = market_finality_caveat(snapshot)
    caveats.append(finality_caveat)
    raw = render_verified_market_snapshot(snapshot)
    # Observation time is provenance, not evidence identity. Identical prices
    # and finality status should retain the same citation IDs across fetches.
    identity = json.dumps(
        {key: value for key, value in snapshot.items() if key != "observed_at"},
        sort_keys=True, ensure_ascii=False,
    )
    source_id = _source_id("market-snapshot", "yfinance", ticker, identity)
    source = {
        "id": source_id,
        "label": f"Verified market snapshot for {ticker.upper()}",
        "content": raw,
        "vendor": "yfinance",
        "retrieved_at": snapshot.get("observed_at") or _retrieved_at(),
        "published_at": None,
        "period": snapshot["latest_date"],
        "basis": "not_disclosed",
        "caveats": [finality_caveat],
    }

    facts = []
    for field, value in snapshot["latest_ohlcv"].items():
        if value is None:
            caveats.append(f"Latest {field} is unavailable.")
            continue
        facts.append(_fact(
            metric=field.lower(), value=value,
            unit="units_not_disclosed" if field == "Volume" else "quote_currency",
            period=f"trading_day:{snapshot['latest_date']}", basis="not_disclosed",
            source_id=source_id, kind="reported",
        ))

    for name, item in snapshot["indicators"].items():
        value = item.get("value")
        if value is None:
            caveats.append(
                f"Indicator {name} is unavailable"
                + (f" ({item['error']})." if item.get("error") else ".")
            )
            continue
        facts.append(_fact(
            metric=name, value=value,
            unit=(
                "index_points" if name == "rsi"
                else "quote_currency" if name in _PRICE_INDICATORS
                else "unit_not_disclosed"
            ),
            period=f"trading_day:{snapshot['latest_date']}", basis=item.get("method", "not_disclosed"),
            source_id=source_id, kind="calculated", inputs=[source_id],
        ))

    for comparison in snapshot.get("indicator_comparisons", []):
        facts.append(_fact(
            metric=comparison["indicator"] + "_change_5_trading_rows",
            value=comparison["change"], unit="quote_currency",
            period=f"trading_rows:{comparison['start_date']}..{comparison['end_date']}",
            basis=comparison["method"] + "; end value minus start value, five trading-row intervals",
            source_id=source_id, kind="calculated", inputs=[source_id],
        ))

    by_metric = {fact["metric"]: fact for fact in facts}
    fast, slow = by_metric.get("close_10_ema"), by_metric.get("close_50_sma")
    if fast and slow:
        facts.append(_fact(
            metric="ema10_minus_sma50", value=fast["value"] - slow["value"],
            unit="quote_currency", period=f"trading_day:{snapshot['latest_date']}",
            basis="calculated: 10-row EMA minus 50-row SMA; not a crossover date",
            source_id=source_id, kind="calculated", inputs=[fast["id"], slow["id"]],
        ))

    for fact in facts:
        fact["caveats"].append(finality_caveat)

    return {
        "analysis_date": date,
        "sources": [source],
        "facts": facts,
        "required_evidence_ids": [fact["id"] for fact in facts],
        "caveats": caveats,
    }


def _content_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _append_source(
    sources: list[dict[str, Any]],
    internal: list[dict[str, Any]],
    seen: dict[tuple[str, str, str, str | None], int],
    *,
    method: str,
    basis: str | None,
    ticker: str,
    vendor: str,
    value: Any,
) -> None:
    content = _content_string(value)
    # Alpha statement endpoints return both annual and quarterly arrays in the
    # same payload. Other vendors may legitimately return identical-looking CSV
    # for two frequencies, but their frequency remains part of the provenance.
    dedup_key = (method, vendor, content, None if vendor == "alpha_vantage" else basis)
    if dedup_key in seen:
        index = seen[dedup_key]
        if sources[index].get("basis") != basis:
            sources[index]["period"] = "annual_and_quarterly"
            sources[index]["basis"] = "not_disclosed"
            sources[index]["label"] = sources[index]["label"].replace(
                " (annual)", " (annual and quarterly)"
            )
            internal[index]["basis"] = "annual_and_quarterly"
        return

    kind = method.removeprefix("get_")
    source = {
        "id": _source_id(f"fundamentals-{kind}", vendor, ticker, content),
        "label": (
            f"{_STATEMENT_METHODS.get(method, 'Company overview')} for {ticker.upper()}"
            + (f" ({basis})" if basis else "")
        ),
        "content": content,
        "vendor": vendor or "unknown",
        "retrieved_at": _retrieved_at(),
        "published_at": None,
    }
    if basis:
        source["period"] = basis
        source["basis"] = "not_disclosed"
    seen[dedup_key] = len(sources)
    sources.append(source)
    internal.append({"method": method, "basis": basis, "source": source})


def _reported_fact(
    facts: list[dict[str, Any]],
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
    *,
    metric: str,
    raw_value: Any,
    unit: str | None,
    period: str,
    frequency: str,
    vendor: str,
    source_id: str,
) -> None:
    decimal = parse_decimal(raw_value)
    if decimal is None:
        return
    record = _fact(
        metric=metric, value=plain_number(decimal), unit=unit,
        period=f"{frequency}:{period}", basis="not_disclosed",
        source_id=source_id, kind="reported",
    )
    facts.append(record)
    values.setdefault((vendor, unit, frequency, period), {})[metric] = {
        "decimal": decimal,
        "fact": record,
    }


def _unit_for_metric(metric: str, default: str | None) -> str | None:
    return "shares" if metric in _SHARE_METRICS else default


def _emit_alias_candidates(
    facts: list[dict[str, Any]],
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
    caveats: list[str],
    *,
    candidates: list[tuple[str, Any]],
    metric: str,
    unit: str | None,
    period: str,
    frequency: str,
    vendor: str,
    source_id: str,
    source_label: str,
) -> bool:
    """Emit one canonical fact for equal aliases; withhold conflicting aliases."""
    parsed = [
        (label, raw_value, parse_decimal(raw_value))
        for label, raw_value in candidates
    ]
    parsed = [item for item in parsed if item[2] is not None]
    if not parsed:
        return False
    distinct = {item[2] for item in parsed}
    if len(distinct) > 1:
        evidence = ", ".join(f"{label}={raw_value}" for label, raw_value, _ in parsed)
        caveats.append(
            f"{source_label} {frequency}:{period} has conflicting provider aliases "
            f"for {metric} ({evidence}); the canonical fact was withheld."
        )
        return False
    _reported_fact(
        facts, values, metric=metric, raw_value=parsed[0][1], unit=unit,
        period=period, frequency=frequency, vendor=vendor, source_id=source_id,
    )
    return True


def _parse_alpha_source(
    item: dict[str, Any],
    facts: list[dict[str, Any]],
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
    caveats: list[str],
    cutoff: str,
) -> bool:
    method = item["method"]
    fields = _ALPHA_FIELDS.get(method)
    if not fields:
        return False
    try:
        payload = json.loads(item["source"]["content"])
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False

    parsed = False
    for report_key, basis in (("annualReports", "annual"), ("quarterlyReports", "quarterly")):
        reports = payload.get(report_key)
        if not isinstance(reports, list):
            continue
        for report in reports:
            if not isinstance(report, dict):
                continue
            period = report.get("fiscalDateEnding")
            if not isinstance(period, str) or not period or period > cutoff:
                continue
            unit = report.get("reportedCurrency") or None
            candidates_by_metric: dict[str, list[tuple[str, Any]]] = {}
            for provider_field, metric in fields.items():
                if provider_field in report:
                    candidates_by_metric.setdefault(metric, []).append(
                        (provider_field, report.get(provider_field))
                    )
            for metric, candidates in candidates_by_metric.items():
                emitted = _emit_alias_candidates(
                    facts, values, caveats, candidates=candidates, metric=metric,
                    unit=_unit_for_metric(metric, unit), period=period,
                    frequency=basis, vendor="alpha_vantage",
                    source_id=item["source"]["id"],
                    source_label=item["source"]["label"],
                )
                parsed = parsed or emitted
    return parsed


def _normalize_period(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


def _parse_yfinance_source(
    item: dict[str, Any],
    facts: list[dict[str, Any]],
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
    caveats: list[str],
    cutoff: str,
) -> bool:
    method = item["method"]
    fields = _YFINANCE_FIELDS.get(method)
    basis = item.get("basis")
    if not fields or basis not in {"annual", "quarterly"}:
        return False
    csv_text = "\n".join(
        line for line in item["source"]["content"].splitlines()
        if line and not line.startswith("#")
    )
    try:
        rows = list(csv.reader(StringIO(csv_text)))
    except csv.Error:
        return False
    if len(rows) < 2 or len(rows[0]) < 2:
        return False
    periods = [_normalize_period(value) for value in rows[0][1:]]
    parsed = False
    candidates_by_period_metric: dict[tuple[str, str], list[tuple[str, Any]]] = {}
    for row in rows[1:]:
        if not row or row[0] not in fields:
            continue
        metric = fields[row[0]]
        for period, raw_value in zip(periods, row[1:], strict=False):
            if not period or period > cutoff:
                continue
            candidates_by_period_metric.setdefault((period, metric), []).append(
                (row[0], raw_value)
            )
    for (period, metric), candidates in candidates_by_period_metric.items():
        emitted = _emit_alias_candidates(
            facts, values, caveats, candidates=candidates, metric=metric,
            unit=_unit_for_metric(metric, "reported_currency"), period=period,
            frequency=basis, vendor="yfinance", source_id=item["source"]["id"],
            source_label=item["source"]["label"],
        )
        parsed = parsed or emitted
    return parsed


def _parse_overview_source(
    item: dict[str, Any],
    facts: list[dict[str, Any]],
    analysis_date: str,
) -> bool:
    """Parse a few explicitly labeled valuation/profile fields without inference."""
    if item["method"] != "get_fundamentals":
        return False
    vendor = item["source"]["vendor"]
    content = item["source"]["content"]
    fields: list[tuple[str, Any, str | None, bool]] = []

    if vendor == "alpha_vantage":
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        fields = [
            ("market_cap", payload.get("MarketCapitalization"), "reported_currency", False),
            ("trailing_pe", payload.get("PERatio"), "ratio", False),
            ("forward_pe", payload.get("ForwardPE"), "ratio", True),
            ("trailing_eps", payload.get("EPS"), "reported_currency_per_share", False),
        ]
    elif vendor == "yfinance":
        labeled = {}
        for line in content.splitlines():
            if line.startswith("#") or ": " not in line:
                continue
            label, value = line.split(": ", 1)
            labeled[label] = value
        fields = [
            ("market_cap", labeled.get("Market Cap"), "reported_currency", False),
            ("trailing_pe", labeled.get("PE Ratio (TTM)"), "ratio", False),
            ("forward_pe", labeled.get("Forward PE"), "ratio", True),
            ("trailing_eps", labeled.get("EPS (TTM)"), "reported_currency_per_share", False),
            ("forward_eps", labeled.get("Forward EPS"), "reported_currency_per_share", True),
            ("overview_free_cash_flow", labeled.get("Free Cash Flow"), "reported_currency", False),
        ]
    else:
        return False

    parsed = False
    for metric, raw_value, unit, is_forecast in fields:
        decimal = parse_decimal(raw_value)
        if decimal is None:
            continue
        caveats = []
        if is_forecast:
            caveats.append(
                "Forward-looking provider estimate; forecast methodology and "
                "accounting basis are not disclosed."
            )
        if metric == "overview_free_cash_flow":
            caveats.append(
                "Provider overview free cash flow; its observation horizon, endpoint, "
                "and accounting basis are not disclosed."
            )
        facts.append(_fact(
            metric=metric, value=plain_number(decimal), unit=unit,
            period=f"as_of:{analysis_date}", basis="not_disclosed",
            source_id=item["source"]["id"], kind="reported",
            caveats=caveats,
        ))
        parsed = True
    return parsed


def _calculated_facts(
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    calculated = []
    calculation_caveats = []
    grouped: dict[tuple[str, str | None, str], dict[str, dict[str, dict[str, Any]]]] = {}
    for (vendor, unit, frequency, period), period_values in values.items():
        grouped.setdefault((vendor, unit, frequency), {})[period] = period_values
    for (_vendor, _unit, frequency, period), period_values in sorted(
        values.items(), key=lambda item: repr(item[0])
    ):
        reported_period = f"{frequency}:{period}"
        for metric, numerator_name in (
            ("gross_margin", "gross_profit"),
            ("operating_margin", "operating_income"),
            ("net_margin", "net_income"),
        ):
            numerator = period_values.get(numerator_name)
            denominator = period_values.get("revenue")
            if not numerator or not denominator:
                continue
            value, caveats = ratio(
                numerator["decimal"], denominator["decimal"],
                denominator_label="Revenue",
            )
            if value is None:
                calculation_caveats.extend(
                    f"{reported_period} {metric}: {caveat}" for caveat in caveats
                )
                continue
            calculated.append(_fact(
                metric=metric, value=plain_number(value), unit="ratio",
                period=reported_period, basis="not_disclosed",
                source_id=numerator["fact"]["source_id"], kind="calculated",
                inputs=[numerator["fact"]["id"], denominator["fact"]["id"]],
                caveats=caveats,
            ))

        assets = period_values.get("current_assets")
        liabilities = period_values.get("current_liabilities")
        if assets and liabilities:
            value, caveats = ratio(
                assets["decimal"], liabilities["decimal"],
                denominator_label="Current liabilities",
            )
            if value is not None:
                calculated.append(_fact(
                    metric="current_ratio", value=plain_number(value), unit="ratio",
                    period=reported_period, basis="not_disclosed",
                    source_id=assets["fact"]["source_id"], kind="calculated",
                    inputs=[assets["fact"]["id"], liabilities["fact"]["id"]],
                    caveats=caveats,
                ))
            else:
                calculation_caveats.extend(
                    f"{reported_period} current_ratio: {caveat}" for caveat in caveats
                )

        operating = period_values.get("operating_cash_flow")
        capex = period_values.get("capital_expenditures")
        direct_fcf = period_values.get("free_cash_flow")
        if operating and capex and not direct_fcf:
            is_alpha = operating["fact"]["source_id"].split(":", 2)[1] == "alpha_vantage"
            value, caveats = free_cash_flow(
                operating["decimal"], capex["decimal"],
                capital_expenditure_is_outflow_magnitude=is_alpha,
            )
            calculated.append(_fact(
                metric="free_cash_flow", value=plain_number(value),
                unit=operating["fact"]["unit"], period=reported_period,
                basis="not_disclosed",
                source_id=operating["fact"]["source_id"], kind="calculated",
                inputs=[operating["fact"]["id"], capex["fact"]["id"]],
                caveats=caveats,
            ))

    # Straightforward comparable-period changes. Exact month/day matching keeps
    # quarterly and annual periods separate and avoids choosing a nearby period.
    for (_vendor, unit, frequency), periods in sorted(
        grouped.items(), key=lambda item: repr(item[0])
    ):
        for period, current_values in sorted(periods.items()):
            try:
                period_date = datetime.strptime(period, "%Y-%m-%d")
                prior_period = period_date.replace(year=period_date.year - 1).strftime("%Y-%m-%d")
            except ValueError:
                prior_period = ""
            prior_values = periods.get(prior_period)
            current_revenue = current_values.get("revenue")
            if current_revenue:
                prior_revenue = prior_values.get("revenue") if prior_values else None
                if not prior_revenue:
                    calculation_caveats.append(
                        f"{frequency}:{period} revenue_yoy_growth: no exact "
                        "same-frequency prior-year comparison is available."
                    )
                elif prior_revenue["decimal"] <= 0:
                    calculation_caveats.append(
                        f"{frequency}:{period} revenue_yoy_growth: the prior-year "
                        "revenue denominator is non-positive, so percent growth is omitted."
                    )
                else:
                    growth = (
                        current_revenue["decimal"] - prior_revenue["decimal"]
                    ) / prior_revenue["decimal"]
                    calculated.append(_fact(
                        metric="revenue_yoy_growth", value=plain_number(growth), unit="ratio",
                        period=f"{frequency}:{period}", basis="not_disclosed",
                        source_id=current_revenue["fact"]["source_id"], kind="calculated",
                        inputs=[prior_revenue["fact"]["id"], current_revenue["fact"]["id"]],
                    ))

            current_operating = current_values.get("operating_income")
            prior_operating = prior_values.get("operating_income") if prior_values else None
            if not current_operating or not prior_operating:
                continue
            change = current_operating["decimal"] - prior_operating["decimal"]
            if prior_operating["decimal"] <= 0 < current_operating["decimal"]:
                calculated.append(_fact(
                    metric="operating_income_yoy_change", value=plain_number(change), unit=unit,
                    period=f"{frequency}:{period}", basis="not_disclosed",
                    source_id=current_operating["fact"]["source_id"], kind="calculated",
                    inputs=[prior_operating["fact"]["id"], current_operating["fact"]["id"]],
                    caveats=[
                        "Operating income changed from a loss or break-even to profit; "
                        "absolute change is shown because percent growth would be misleading."
                    ],
                ))
            elif prior_operating["decimal"] > 0:
                calculated.append(_fact(
                    metric="operating_income_yoy_growth",
                    value=plain_number(change / prior_operating["decimal"]), unit="ratio",
                    period=f"{frequency}:{period}", basis="not_disclosed",
                    source_id=current_operating["fact"]["source_id"], kind="calculated",
                    inputs=[prior_operating["fact"]["id"], current_operating["fact"]["id"]],
                ))
    return calculated, calculation_caveats


def _previous_quarter_end(period: str) -> str | None:
    """Return the prior calendar-quarter end for a verified quarter-end date."""
    try:
        value = datetime.strptime(period, "%Y-%m-%d")
    except ValueError:
        return None
    if (value.month, value.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        return None
    previous = {
        3: (value.year - 1, 12, 31),
        6: (value.year, 3, 31),
        9: (value.year, 6, 30),
        12: (value.year, 9, 30),
    }[value.month]
    return f"{previous[0]:04d}-{previous[1]:02d}-{previous[2]:02d}"


def _quarter_span(
    periods: dict[str, dict[str, dict[str, Any]]],
    metric: str,
    count: int,
) -> list[dict[str, Any]] | None:
    available = sorted(period for period, values in periods.items() if metric in values)
    if not available:
        return None
    cursor = available[-1]
    span = []
    for _ in range(count):
        record = periods.get(cursor, {}).get(metric)
        if record is None:
            return None
        span.append(record)
        cursor = _previous_quarter_end(cursor)
        if cursor is None and len(span) < count:
            return None
    return span


def _cash_outflow_magnitude(
    records: list[dict[str, Any]],
    vendor: str,
    label: str,
) -> tuple[Any | None, list[str]]:
    values = [record["decimal"] for record in records]
    if vendor == "yfinance":
        if any(value > 0 for value in values):
            return None, [
                f"{label} includes a positive yfinance value although this provider "
                "normally reports cash outflows as negative; magnitude was withheld."
            ]
        return -sum(values), [
            "yfinance reports these cash outflows as negative; this calculated fact "
            "shows their positive magnitude."
        ]
    if vendor == "alpha_vantage":
        if any(value < 0 for value in values):
            return None, [
                f"{label} includes a negative Alpha Vantage value although this field "
                "normally reports an outflow magnitude; magnitude was withheld."
            ]
        return sum(values), [
            "Alpha Vantage reports this field as an outflow magnitude; the sign was retained."
        ]
    return None, [f"{label} sign convention is unknown for vendor {vendor}; magnitude withheld."]


def _material_summary_facts(
    values: dict[tuple[str, str | None, str, str], dict[str, dict[str, Any]]],
    facts: list[dict[str, Any]],
    source_vendors: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build a small set of deterministic, decision-relevant financial summaries."""
    calculated: list[dict[str, Any]] = []
    caveats: list[str] = []
    grouped: dict[tuple[str, str | None, str], dict[str, dict[str, dict[str, Any]]]] = {}
    for (vendor, unit, frequency, period), period_values in values.items():
        grouped.setdefault((vendor, unit, frequency), {})[period] = period_values

    for (vendor, unit, frequency), periods in sorted(
        grouped.items(), key=lambda item: repr(item[0])
    ):
        if frequency == "quarterly":
            for source_metric, summary_metric, is_outflow in (
                ("common_stock_repurchases", "ttm_common_stock_repurchases_magnitude", True),
                ("stock_based_compensation", "ttm_stock_based_compensation", False),
            ):
                span = _quarter_span(periods, source_metric, 4)
                if span is None:
                    if any(source_metric in period_values for period_values in periods.values()):
                        caveats.append(
                            f"{summary_metric}: four contiguous calendar-quarter values "
                            "from one vendor/unit series are unavailable; TTM was withheld."
                        )
                    continue
                if is_outflow:
                    total, fact_caveats = _cash_outflow_magnitude(span, vendor, summary_metric)
                else:
                    if any(record["decimal"] < 0 for record in span):
                        total = None
                        fact_caveats = [
                            "Stock-based compensation includes a negative quarterly value; "
                            "TTM was withheld because the provider convention is ambiguous."
                        ]
                    else:
                        total = sum(record["decimal"] for record in span)
                        fact_caveats = [
                            "Sum of four contiguous quarterly values from the same provider row."
                        ]
                if total is None:
                    caveats.extend(fact_caveats)
                    continue
                latest_period = span[0]["fact"]["period"].split(":", 1)[1]
                calculated.append(_fact(
                    metric=summary_metric, value=plain_number(total), unit=unit,
                    period=f"ttm_through:{latest_period}", basis="not_disclosed",
                    source_id=span[0]["fact"]["source_id"], kind="calculated",
                    inputs=[record["fact"]["id"] for record in reversed(span)],
                    caveats=fact_caveats,
                ))

            capex_periods = sorted(
                period for period, period_values in periods.items()
                if "capital_expenditures" in period_values
            )
            if capex_periods:
                latest_capex_period = capex_periods[-1]
                latest_month = int(latest_capex_period[5:7])
                if latest_month in {3, 6, 9}:
                    capex_span = _quarter_span(
                        periods, "capital_expenditures", latest_month // 3
                    )
                    if capex_span is None:
                        caveats.append(
                            f"YTD capital expenditures through {latest_capex_period}: "
                            "the calendar-quarter sequence is incomplete; YTD was withheld."
                        )
                    else:
                        latest_period = capex_span[0]["fact"]["period"].split(":", 1)[1]
                        total, fact_caveats = _cash_outflow_magnitude(
                            capex_span, vendor, "YTD capital expenditures"
                        )
                        if total is None:
                            caveats.extend(fact_caveats)
                        else:
                            duration = {3: "Q1", 6: "H1", 9: "nine-month YTD"}[latest_month]
                            fact_caveats.append(
                                f"{duration} total from contiguous calendar-quarter values; "
                                "it is a shorter duration than a full fiscal year."
                            )
                            calculated.append(_fact(
                                metric="ytd_capital_expenditures_magnitude",
                                value=plain_number(total), unit=unit,
                                period=f"ytd_through:{latest_period}", basis="not_disclosed",
                                source_id=capex_span[0]["fact"]["source_id"], kind="calculated",
                                inputs=[record["fact"]["id"] for record in reversed(capex_span)],
                                caveats=fact_caveats,
                            ))
                            prior_annual = grouped.get((vendor, unit, "annual"), {}).get(
                                f"{int(latest_period[:4]) - 1}-12-31", {}
                            ).get("capital_expenditures")
                            if prior_annual:
                                prior_total, prior_caveats = _cash_outflow_magnitude(
                                    [prior_annual], vendor, "Prior full-year capital expenditures"
                                )
                                if prior_total is None:
                                    caveats.extend(prior_caveats)
                                else:
                                    prior_caveats.append(
                                        f"Full-year {int(latest_period[:4]) - 1} is shown only as "
                                        f"context for {duration} through {latest_period}; durations "
                                        "are not like-for-like, so no growth rate is calculated."
                                    )
                                    calculated.append(_fact(
                                        metric="prior_full_year_capital_expenditures_magnitude",
                                        value=plain_number(prior_total), unit=unit,
                                        period=f"annual:{int(latest_period[:4]) - 1}-12-31",
                                        basis="not_disclosed",
                                        source_id=prior_annual["fact"]["source_id"],
                                        kind="calculated", inputs=[prior_annual["fact"]["id"]],
                                        caveats=prior_caveats,
                                    ))

        if frequency == "annual":
            bridge_metrics = (
                "operating_cash_flow",
                "continuing_operations_operating_cash_flow",
                "discontinued_operations_operating_cash_flow",
            )
            bridge_periods = sorted(
                period for period, period_values in periods.items()
                if any(metric in period_values for metric in bridge_metrics)
            )
            if bridge_periods:
                latest_period = bridge_periods[-1]
                latest_values = periods[latest_period]
                missing = [
                    metric for metric in bridge_metrics if metric not in latest_values
                ]
                if missing:
                    caveats.append(
                        f"annual:{latest_period} operating cash flow cannot be reconciled "
                        "between total, continuing operations, and discontinued operations "
                        f"because {', '.join(missing)} is unavailable; missing values were "
                        "not treated as zero."
                    )
                else:
                    total, continuing, discontinued = (
                        latest_values[metric] for metric in bridge_metrics
                    )
                    component_sum = continuing["decimal"] + discontinued["decimal"]
                    if total["decimal"] != component_sum:
                        caveats.append(
                            f"annual:{latest_period} operating cash flow does not reconcile "
                            f"exactly: total {plain_number(total['decimal'])} versus continuing "
                            f"{plain_number(continuing['decimal'])} plus discontinued "
                            f"{plain_number(discontinued['decimal'])}; no reconciliation fact "
                            "was emitted."
                        )
                    else:
                        annual_fcf = next((
                            fact for fact in facts
                            if fact["metric"] == "free_cash_flow"
                            and fact["period"] == f"annual:{latest_period}"
                            and fact.get("unit") == unit
                            and source_vendors.get(fact["source_id"]) == vendor
                        ), None)
                        annual_capex = latest_values.get("capital_expenditures")
                        bridge_caveat = (
                            f"annual:{latest_period} operating cash flow reconciles exactly: "
                            f"total {plain_number(total['decimal'])} equals continuing operations "
                            f"{plain_number(continuing['decimal'])} plus discontinued operations "
                            f"{plain_number(discontinued['decimal'])}. The total therefore "
                            "includes "
                            "the disclosed discontinued-operations component."
                        )
                        if annual_fcf:
                            bridge_caveat += (
                                f" Same-period annual free cash flow is "
                                f"{annual_fcf['value']}; its provider/accounting presentation "
                                "does not disclose whether it is continuing-only or includes "
                                "discontinued operations, so no continuing-operations free cash "
                                "flow is inferred."
                            )
                        if annual_capex:
                            capex_value = annual_capex["decimal"]
                            expected_fcf = None
                            operator = ""
                            if vendor == "yfinance" and capex_value <= 0:
                                expected_fcf = total["decimal"] + capex_value
                                operator = "plus provider-signed capital expenditures"
                            elif vendor == "alpha_vantage" and capex_value >= 0:
                                expected_fcf = total["decimal"] - capex_value
                                operator = "minus capital-expenditure outflow magnitude"
                            if annual_fcf and expected_fcf == parse_decimal(annual_fcf["value"]):
                                bridge_caveat += (
                                    f" The annual free-cash-flow arithmetic also reconciles: "
                                    f"total operating cash flow {plain_number(total['decimal'])} "
                                    f"{operator} {plain_number(capex_value)} equals "
                                    f"{annual_fcf['value']}. This uses total operating cash flow, "
                                    "including the discontinued-operations component; it does not "
                                    "establish clean continuing-operations free cash flow or a "
                                    "capital-expenditure allocation between continuing and "
                                    "discontinued operations."
                                )
                            else:
                                bridge_caveat += (
                                    f" Same-period capital expenditures are "
                                    f"{plain_number(capex_value)} with the provider sign, but a "
                                    "complete sign-consistent annual free-cash-flow bridge is "
                                    "unavailable or does not reconcile exactly. No clean "
                                    "continuing-operations free cash flow or capital-expenditure "
                                    "allocation is inferred."
                                )
                        calculated.append(_fact(
                            metric="operating_cash_flow_reconciliation",
                            value=plain_number(total["decimal"]), unit=unit,
                            period=f"annual:{latest_period}", basis="not_disclosed",
                            source_id=total["fact"]["source_id"], kind="calculated",
                            inputs=[
                                total["fact"]["id"], continuing["fact"]["id"],
                                discontinued["fact"]["id"],
                                *([annual_fcf["id"]] if annual_fcf else []),
                                *([annual_capex["fact"]["id"]] if annual_capex else []),
                            ],
                            caveats=[bridge_caveat],
                        ))
                        caveats.append(bridge_caveat)

        for share_metric in ("basic_average_shares", "diluted_average_shares"):
            matching_periods = sorted(
                period for period, period_values in periods.items()
                if share_metric in period_values
            )
            if not matching_periods:
                continue
            current_period = matching_periods[-1]
            try:
                current_date = datetime.strptime(current_period, "%Y-%m-%d")
                prior_period = current_date.replace(
                    year=current_date.year - 1
                ).strftime("%Y-%m-%d")
            except ValueError:
                prior_period = ""
            current = periods[current_period][share_metric]
            prior = periods.get(prior_period, {}).get(share_metric)
            if not prior:
                caveats.append(
                    f"{share_metric} {frequency}:{current_period}: exact prior-year "
                    "same-frequency comparison is unavailable."
                )
                continue
            if prior["decimal"] <= 0:
                caveats.append(
                    f"{share_metric} {frequency}:{current_period}: prior share count is "
                    "non-positive, so change was withheld."
                )
                continue
            change = (current["decimal"] - prior["decimal"]) / prior["decimal"]
            calculated.append(_fact(
                metric=f"{share_metric}_yoy_change", value=plain_number(change), unit="ratio",
                period=f"{frequency}:{current_period}", basis="not_disclosed",
                source_id=current["fact"]["source_id"], kind="calculated",
                inputs=[prior["fact"]["id"], current["fact"]["id"]],
                caveats=[
                    "Exact prior-year comparison of the same provider row, unit, and "
                    "reporting frequency; accounting basis remains not disclosed."
                ],
            ))

        for period, period_values in sorted(periods.items()):
            operating = period_values.get("operating_income")
            reported = period_values.get("total_operating_income_as_reported")
            restructuring = period_values.get("restructuring_and_merger_acquisition")
            if not operating or not reported or not restructuring:
                continue
            bridge = operating["decimal"] - reported["decimal"]
            if bridge != restructuring["decimal"]:
                caveats.append(
                    f"{frequency}:{period} provider operating-income bridge does not "
                    "reconcile exactly; no bridge fact was emitted."
                )
                continue
            calculated.append(_fact(
                metric="operating_income_bridge_adjustment", value=plain_number(bridge), unit=unit,
                period=f"{frequency}:{period}", basis="not_disclosed",
                source_id=operating["fact"]["source_id"], kind="calculated",
                inputs=[
                    operating["fact"]["id"], restructuring["fact"]["id"],
                    reported["fact"]["id"],
                ],
                caveats=[
                    "Provider-row bridge: Operating Income minus Restructuring And Mergern "
                    "Acquisition equals Total Operating Income As Reported. This is an exact "
                    "provider reconciliation; filing classification and accounting basis "
                    "remain not disclosed."
                ],
            ))

    # Build a dated FCF total from exactly four contiguous statement quarters.
    # This includes calculated quarterly FCF records (for example Alpha Vantage
    # operating cash flow less its positive capex magnitude) as well as direct
    # provider FCF rows. Comparisons remain within one vendor and unit.
    quarterly_fcf: dict[tuple[str, str | None], dict[str, dict[str, Any]]] = {}
    quarterly_cashflow_context: dict[
        tuple[str, str | None], dict[str, dict[str, dict[str, Any]]]
    ] = {}
    overview_fcf: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for fact in [*facts, *calculated]:
        vendor = source_vendors.get(fact["source_id"])
        if vendor is None:
            continue
        if fact["metric"] == "free_cash_flow" and fact["period"].startswith("quarterly:"):
            period = fact["period"].split(":", 1)[1]
            quarterly_fcf.setdefault((vendor, fact.get("unit")), {})[period] = fact
        elif fact["metric"] == "overview_free_cash_flow":
            overview_fcf.setdefault((vendor, fact.get("unit")), []).append(fact)
        if fact["period"].startswith("quarterly:") and fact["metric"] in {
            "operating_cash_flow",
            "continuing_operations_operating_cash_flow",
            "discontinued_operations_operating_cash_flow",
            "capital_expenditures",
        }:
            period = fact["period"].split(":", 1)[1]
            quarterly_cashflow_context.setdefault(
                (vendor, fact.get("unit")), {}
            ).setdefault(period, {})[fact["metric"]] = fact

    compared_overviews: set[str] = set()
    for (vendor, unit), periods in sorted(
        quarterly_fcf.items(), key=lambda item: repr(item[0])
    ):
        available = sorted(periods)
        if not available:
            continue
        cursor = available[-1]
        span: list[dict[str, Any]] = []
        for _ in range(4):
            record = periods.get(cursor)
            if record is None:
                break
            span.append(record)
            previous = _previous_quarter_end(cursor)
            if previous is None and len(span) < 4:
                break
            cursor = previous or ""
        if len(span) != 4:
            caveats.append(
                f"latest_four_quarters_free_cash_flow for {vendor}: four contiguous "
                "calendar-quarter values in one unit series are unavailable; the sum "
                "was withheld."
            )
            continue

        chronological = list(reversed(span))
        total = sum(parse_decimal(fact["value"]) for fact in chronological)
        start = chronological[0]["period"].split(":", 1)[1]
        end = chronological[-1]["period"].split(":", 1)[1]
        components = ", ".join(
            f"{fact['period'].split(':', 1)[1]}={fact['value']}"
            for fact in chronological
        )
        sum_caveat = (
            f"Sum of four contiguous quarterly free-cash-flow values from {start} "
            f"through {end}: {components}; total {plain_number(total)}."
        )
        summary_inputs = [fact["id"] for fact in chronological]
        summary_caveats = [sum_caveat]

        cashflow_periods = quarterly_cashflow_context.get((vendor, unit), {})
        disclosed_discontinued: list[tuple[str, dict[str, Any]]] = []
        undisclosed_periods: list[str] = []
        reconciled_periods: list[str] = []
        discontinued_inputs: list[str] = []
        for fcf_fact in chronological:
            period = fcf_fact["period"].split(":", 1)[1]
            period_values = cashflow_periods.get(period, {})
            discontinued = period_values.get(
                "discontinued_operations_operating_cash_flow"
            )
            if discontinued is None:
                undisclosed_periods.append(period)
                continue

            disclosed_discontinued.append((period, discontinued))
            supporting = [discontinued]
            operating = period_values.get("operating_cash_flow")
            continuing = period_values.get("continuing_operations_operating_cash_flow")
            capex = period_values.get("capital_expenditures")
            if operating and continuing and (
                parse_decimal(operating["value"])
                == parse_decimal(continuing["value"])
                + parse_decimal(discontinued["value"])
            ):
                supporting.extend((operating, continuing))
                capex_value = parse_decimal(capex["value"]) if capex else None
                expected_fcf = None
                if capex_value is not None:
                    if vendor == "yfinance" and capex_value <= 0:
                        expected_fcf = parse_decimal(operating["value"]) + capex_value
                    elif vendor == "alpha_vantage" and capex_value >= 0:
                        expected_fcf = parse_decimal(operating["value"]) - capex_value
                if capex and expected_fcf == parse_decimal(fcf_fact["value"]):
                    supporting.append(capex)
                    reconciled_periods.append(period)
            for support in supporting:
                if support["id"] not in discontinued_inputs:
                    discontinued_inputs.append(support["id"])

        nonzero_discontinued = [
            (period, fact) for period, fact in disclosed_discontinued
            if parse_decimal(fact["value"]) != 0
        ]
        if nonzero_discontinued or undisclosed_periods:
            limitation_parts: list[str] = []
            if nonzero_discontinued:
                disclosed_components = ", ".join(
                    f"{period}={fact['value']}"
                    for period, fact in nonzero_discontinued
                )
                disclosed_total = sum(
                    parse_decimal(fact["value"])
                    for _, fact in nonzero_discontinued
                )
                limitation_parts.append(
                    "Within this span, the source reports discontinued-operations "
                    f"operating cash flow of {disclosed_components}; disclosed total "
                    f"{plain_number(disclosed_total)}."
                )
            if reconciled_periods:
                limitation_parts.append(
                    "For " + ", ".join(reconciled_periods)
                    + ", total operating cash flow reconciles to continuing plus "
                    "discontinued operating cash flow, and reported free cash flow "
                    "reconciles arithmetically to total operating cash flow and signed "
                    "capital expenditures."
                )
            if undisclosed_periods:
                limitation_parts.append(
                    "Discontinued-operations operating cash flow is unavailable for "
                    + ", ".join(undisclosed_periods)
                    + "; missing values were not treated as zero."
                )
            limitation_parts.append(
                "Capital expenditures are not allocated between continuing and "
                "discontinued operations, so no continuing-operations free cash flow "
                "is inferred"
                + (
                    " and the disclosed discontinued-operations amounts are not "
                    "subtracted from the four-quarter total."
                    if nonzero_discontinued
                    else " and no discontinued-operations adjustment is made to the "
                    "four-quarter total."
                )
            )
            discontinued_caveat = " ".join(limitation_parts)
            summary_inputs.extend(discontinued_inputs)
            summary_caveats.append(discontinued_caveat)
            caveats.append(discontinued_caveat)

        summary = _fact(
            metric="latest_four_quarters_free_cash_flow",
            value=plain_number(total), unit=unit,
            period=f"four_quarters:{start}..{end}", basis="not_disclosed",
            source_id=span[0]["source_id"], kind="calculated",
            inputs=summary_inputs, caveats=summary_caveats,
        )
        calculated.append(summary)

        candidates = overview_fcf.get((vendor, unit), [])
        if len(candidates) != 1:
            if len(candidates) > 1:
                caveats.append(
                    f"The dated four-quarter free cash flow through {end} was not "
                    f"compared with the {vendor} overview because multiple overview "
                    "free-cash-flow facts are present."
                )
            continue
        overview = candidates[0]
        compared_overviews.add(overview["id"])
        overview_value = parse_decimal(overview["value"])
        if overview_value is None:
            continue
        difference = total - overview_value
        comparison_caveat = (
            f"Dated statement free cash flow totals {plain_number(total)} for the four "
            f"contiguous quarters {start} through {end}, versus {overview['value']} in "
            f"the {vendor} overview as of {overview['period'].split(':', 1)[1]}, a "
            f"difference of {plain_number(difference)}. The overview observation horizon "
            "and endpoint are unknown, so the figures are not established as comparable; "
            "this difference does not establish a contradiction or provider error."
        )
        calculated.append(_fact(
            metric="free_cash_flow_overview_comparison_difference",
            value=plain_number(difference), unit=unit,
            period=f"four_quarters:{start}..{end}", basis="not_disclosed",
            source_id=summary["source_id"], kind="calculated",
            inputs=[summary["id"], overview["id"]], caveats=[comparison_caveat],
        ))
        caveats.append(comparison_caveat)

    unmatched_overviews = [
        fact for candidates in overview_fcf.values() for fact in candidates
        if fact["id"] not in compared_overviews
    ]
    if unmatched_overviews and quarterly_fcf:
        caveats.append(
            "An overview free-cash-flow value was not compared with the dated "
            "four-quarter statement series because no unique same-vendor, same-unit "
            "pair was available."
        )
    return calculated, caveats


def _required_financial_evidence_ids(facts: list[dict[str, Any]]) -> list[str]:
    """Select bounded material summaries and their fact dependencies for handoff."""
    priorities = (
        "operating_cash_flow_reconciliation",
        "latest_four_quarters_free_cash_flow",
        "free_cash_flow_overview_comparison_difference",
        "ttm_common_stock_repurchases_magnitude",
        "ttm_stock_based_compensation",
        "basic_average_shares_yoy_change",
        "diluted_average_shares_yoy_change",
        "ytd_capital_expenditures_magnitude",
        "prior_full_year_capital_expenditures_magnitude",
        "operating_income_bridge_adjustment",
        "revenue_yoy_growth",
        "operating_income_yoy_change",
        "operating_income_yoy_growth",
        "gross_margin",
        "operating_margin",
        "free_cash_flow",
        "current_ratio",
    )
    by_id = {fact["id"]: fact for fact in facts}
    selected: list[str] = []

    def period_key(fact: dict[str, Any]) -> tuple[str, int]:
        period = fact.get("period", "")
        date = period.rsplit(":", 1)[-1]
        nonzero = int(fact.get("value") != 0)
        return date, nonzero

    for metric in priorities:
        candidates = [fact for fact in facts if fact["metric"] == metric]
        if not candidates:
            continue
        if metric == "operating_income_bridge_adjustment":
            nonzero = [fact for fact in candidates if fact["value"] != 0]
            chosen = max(nonzero or candidates, key=period_key)
        else:
            chosen = max(candidates, key=period_key)
        if chosen["id"] not in selected:
            selected.append(chosen["id"])

    # Preserve the globally latest observation and the newest complete exact
    # prior-year pair behind material profitability comparisons. The complete
    # pair may be an older series when the latest source/unit series is incomplete.
    # A nearby period or a second provider never substitutes for the exact prior.
    for metric in ("gross_margin", "operating_margin", "net_income"):
        quarterly = [
            fact for fact in facts
            if fact["metric"] == metric and fact["period"].startswith("quarterly:")
        ]
        if not quarterly:
            continue
        latest = max(quarterly, key=period_key)
        if latest["id"] not in selected:
            selected.append(latest["id"])

        complete_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for current in quarterly:
            current_period = current["period"].split(":", 1)[1]
            try:
                current_date = datetime.strptime(current_period, "%Y-%m-%d")
                prior_period = current_date.replace(
                    year=current_date.year - 1
                ).strftime("%Y-%m-%d")
            except ValueError:
                continue
            prior = next((
                fact for fact in quarterly
                if fact["period"] == f"quarterly:{prior_period}"
                and fact["source_id"] == current["source_id"]
                and fact.get("unit") == current.get("unit")
            ), None)
            if prior:
                complete_pairs.append((current, prior))
        if complete_pairs:
            current, prior = max(complete_pairs, key=lambda pair: period_key(pair[0]))
            for fact in (current, prior):
                if fact["id"] not in selected:
                    selected.append(fact["id"])

    # Retain the annual FCF context attached to the OCF bridge. These are
    # reported/calculated rows only; no continuing-operations FCF is inferred.
    for metric in ("free_cash_flow", "capital_expenditures"):
        annual = [
            fact for fact in facts
            if fact["metric"] == metric and fact["period"].startswith("annual:")
        ]
        if annual:
            chosen = max(annual, key=period_key)
            if chosen["id"] not in selected:
                selected.append(chosen["id"])

    cursor = 0
    while cursor < len(selected):
        fact = by_id.get(selected[cursor])
        if fact:
            for dependency in fact.get("inputs", []):
                if dependency in by_id and dependency not in selected:
                    selected.append(dependency)
        cursor += 1
    return selected


def prepare_fundamentals(ticker: str, date: str) -> dict[str, Any]:
    """Prefetch routed fundamentals, preserve raw payloads, and derive safe facts."""
    today = date_window.get_current_date()
    if date < today:
        return {
            "analysis_date": date,
            "sources": [],
            "facts": [],
            "required_evidence_ids": [],
            "caveats": [
                f"Point-in-time fundamentals are unavailable for {date}. The configured "
                "company-profile and statement sources do not provide genuine publication "
                "or vintage timestamps. A fiscal period end on or before the analysis date "
                "does not prove the statement was published by that date, so no raw or "
                "derived fundamental values were fetched or exposed."
            ],
        }

    sources: list[dict[str, Any]] = []
    internal: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str, str | None], int] = {}
    caveats: list[str] = []

    overview = route_to_vendor_with_metadata("get_fundamentals", ticker, date)
    _append_source(
        sources, internal, seen, method="get_fundamentals", basis=None,
        ticker=ticker, vendor=overview.vendor, value=overview.value,
    )

    for method in _STATEMENT_METHODS:
        for basis in ("annual", "quarterly"):
            routed = route_to_vendor_with_metadata(method, ticker, basis, date)
            _append_source(
                sources, internal, seen, method=method, basis=basis,
                ticker=ticker, vendor=routed.vendor, value=routed.value,
            )

    if any("withheld" in source["content"].lower() for source in sources):
        caveats.append(
            "Historical company profile values are withheld because the provider "
            "does not supply a point-in-time vintage for the requested date."
        )
    if any(item["method"] in _STATEMENT_METHODS for item in internal):
        caveats.append(
            "Fiscal period end dates are accounting period boundaries, not "
            "publication dates. The provider did not supply publication timestamps, "
            "so published_at remains unknown."
        )

    facts: list[dict[str, Any]] = []
    values: dict[
        tuple[str, str | None, str, str], dict[str, dict[str, Any]]
    ] = {}
    for item in internal:
        vendor = item["source"]["vendor"]
        if item["method"] == "get_fundamentals":
            parsed = _parse_overview_source(item, facts, date)
        elif vendor == "alpha_vantage":
            parsed = _parse_alpha_source(item, facts, values, caveats, date)
        elif vendor == "yfinance":
            parsed = _parse_yfinance_source(item, facts, values, caveats, date)
        else:
            parsed = False
        if item["method"] in _STATEMENT_METHODS and not parsed:
            caveats.append(
                f"{item['source']['label']} was preserved as raw source content "
                "because no documented statement fields could be parsed reliably."
            )

    calculated, calculation_caveats = _calculated_facts(values)
    facts.extend(calculated)
    caveats.extend(calculation_caveats)
    material, material_caveats = _material_summary_facts(
        values, facts, {source["id"]: source["vendor"] for source in sources}
    )
    facts.extend(material)
    caveats.extend(material_caveats)
    return {
        "analysis_date": date,
        "sources": sources,
        "facts": facts,
        "required_evidence_ids": _required_financial_evidence_ids(facts),
        "caveats": caveats,
    }
