"""Deterministic market-data verification snapshot.

The market analyst is an LLM that can confabulate exact numbers — citing a
Bollinger band or a "historically validated bounce" that the underlying data
doesn't support (#830). This module computes a ground-truth snapshot (latest
OHLCV row on or before the analysis date, common indicators, recent closes)
the analyst is told to treat as the source of truth for any exact numeric
claim. Deterministic, no LLM involved.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from typing import Any

import pandas as pd
from stockstats import StockDataFrame, dft_windows, wrap

from tradingagents.dataflows.stockstats_utils import load_ohlcv

# A fixed, common indicator set so the snapshot is the same shape every run.
DEFAULT_SNAPSHOT_INDICATORS: tuple[str, ...] = (
    "close_10_ema", "close_50_sma", "close_200_sma",
    "rsi", "boll", "boll_ub", "boll_lb",
    "macd", "macds", "macdh", "atr",
)

_TREND_INDICATORS = {"close_10_ema", "close_50_sma", "close_200_sma", "macd"}


def _observed_at() -> datetime:
    return datetime.now(timezone.utc)


def market_finality_caveat(snapshot: dict[str, Any]) -> str:
    if snapshot.get("bar_status") == "historical":
        return "Historical provider daily bar; exchange-final status is not independently verified."
    return (
        "PROVISIONAL: daily-bar finality is unverified. The Close field is the latest "
        "provider price observation, not a confirmed session close; do not say 'closed at'. "
        "OHLCV, indicators and changes using this row may change before the session ends."
    )


def _indicator_method(name: str) -> str:
    family = {"boll_ub": "boll", "boll_lb": "boll", "macds": "macd", "macdh": "macd"}.get(name, name)
    windows = dft_windows(family)
    parameters = f"; configured windows={windows}" if windows else ""
    if family == "boll":
        parameters += f"; standard-deviation multiplier={StockDataFrame.BOLL_STD_TIMES}"
    return f"stockstats {version('stockstats')}; indicator={name}{parameters}; computed on supplied daily OHLCV"


def _verified_rows(symbol: str, curr_date: str) -> pd.DataFrame:
    """OHLCV on or before curr_date, date-sorted. Raises if nothing usable.

    ``load_ohlcv`` already normalizes the Date column and filters out
    look-ahead rows, but we re-apply the cutoff defensively — this is a
    verification path, so it must not trust its input to be pre-filtered.
    """
    data = load_ohlcv(symbol, curr_date)
    if data is None or data.empty:
        raise ValueError(f"No OHLCV data available for {symbol}.")

    df = data.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df = df[df["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")
    if df.empty:
        raise ValueError(f"No OHLCV rows on or before {curr_date} for {symbol}.")
    return df


def _fmt(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _plain_number(value: Any) -> int | float | None:
    """Return JSON-friendly exact numeric data, or ``None`` for an absent value."""
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_verified_market_snapshot_data(
    symbol: str,
    curr_date: str,
    indicators: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compute the verified OHLCV and indicator snapshot once as numeric data.

    The complete in-range OHLCV frame is retained in ``rows`` so callers that
    need exact arithmetic or dates are not limited by the compact Markdown
    renderer's 30-row display cap.
    """
    df = _verified_rows(symbol, curr_date)
    stock_df = wrap(df.copy())
    selected = tuple(indicators or DEFAULT_SNAPSHOT_INDICATORS)

    indicator_values: dict[str, dict[str, Any]] = {}
    comparisons = []
    for name in selected:
        try:
            stock_df[name]  # triggers stockstats calculation
            indicator_values[name] = {
                "value": _plain_number(stock_df.iloc[-1][name]),
                "error": None,
                "method": _indicator_method(name),
            }
            if name in _TREND_INDICATORS and len(df) >= 6:
                before = _plain_number(stock_df.iloc[-6][name])
                latest = indicator_values[name]["value"]
                if before is not None and latest is not None:
                    comparisons.append({
                        "indicator": name, "start_date": _fmt(df.iloc[-6]["Date"]),
                        "end_date": _fmt(df.iloc[-1]["Date"]), "start_value": before,
                        "end_value": latest, "change": latest - before,
                        "method": indicator_values[name]["method"],
                    })
        except Exception as exc:  # noqa: BLE001 — preserve partial verified output
            indicator_values[name] = {
                "value": None,
                "error": type(exc).__name__,
            }

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "date": _fmt(row["Date"]),
            "open": _plain_number(row.get("Open")),
            "high": _plain_number(row.get("High")),
            "low": _plain_number(row.get("Low")),
            "close": _plain_number(row.get("Close")),
            "volume": _plain_number(row.get("Volume")),
        })

    observed = _observed_at()
    # Without exchange/session metadata, retain a conservative worldwide date
    # boundary (UTC-12), including sessions still active after midnight UTC.
    recent = rows[-1]["date"] >= (observed - timedelta(hours=12)).date().isoformat()
    return {
        "symbol": symbol.upper(),
        "observed_at": observed.isoformat(),
        "bar_status": "provisional" if recent else "historical",
        "requested_date": curr_date,
        "latest_date": rows[-1]["date"],
        "latest_ohlcv": {
            field: rows[-1][field.lower()]
            for field in ("Open", "High", "Low", "Close", "Volume")
        },
        "indicators": indicator_values,
        "indicator_comparisons": comparisons,
        "rows": rows,
    }


def render_verified_market_snapshot(
    snapshot: dict[str, Any],
    look_back_days: int = 30,
) -> str:
    """Render numeric snapshot data in the long-standing Markdown format."""
    symbol = snapshot["symbol"]
    curr_date = snapshot["requested_date"]
    latest_date = snapshot["latest_date"]
    latest = snapshot["latest_ohlcv"]
    indicator_values = snapshot["indicators"]
    window = max(1, min(int(look_back_days), 30))
    recent = snapshot["rows"][-window:]

    lines = [
        f"## Verified market data snapshot for {symbol}",
        "",
        f"- Requested analysis date: {curr_date}",
        f"- Latest trading row used: {latest_date}",
        f"- Snapshot observed at (not necessarily provider refresh time): {snapshot.get('observed_at', 'unknown')}",
        f"- {market_finality_caveat(snapshot)}",
        "- Rows after the requested analysis date are excluded before verification.",
        "",
        "### Latest verified OHLCV row",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for field in ("Open", "High", "Low", "Close", "Volume"):
        label = "Close (provisional price)" if field == "Close" and snapshot.get("bar_status") != "historical" else field
        lines.append(f"| {label} | {_fmt(latest.get(field))} |")

    lines += ["", "### Verified technical indicators (latest row)", "",
              "| Indicator | Value |", "|---|---:|"]
    for name, item in indicator_values.items():
        value = item.get("value")
        rendered = _fmt(value) if value is not None else (
            f"N/A ({item['error']})" if item.get("error") else "N/A"
        )
        lines.append(f"| {name} | {rendered} |")

    methods = [item["method"] for item in indicator_values.values() if item.get("method")]
    if methods:
        lines += ["", "### Calculation provenance", *[f"- {method}" for method in methods]]
        lines.append("- Quote currency and price-adjustment policy are not supplied by this snapshot; do not invent them.")
    if snapshot.get("indicator_comparisons"):
        lines += ["", "### Change across five trading-row intervals", "",
                  "| Indicator | Start date | Start | End date | End | Absolute change |",
                  "|---|---|---:|---|---:|---:|"]
        for item in snapshot["indicator_comparisons"]:
            lines.append(
                f"| {item['indicator']} | {item['start_date']} | {_fmt(item['start_value'])} "
                f"| {item['end_date']} | {_fmt(item['end_value'])} | {_fmt(item['change'])} |"
            )
        lines.append("These endpoint changes do not establish a monotonic trend or an exact crossover date.")

    lines += ["", f"### Recent verified closes (last {len(recent)} rows)", "",
              "| Date | Close |", "|---|---:|"]
    for row in recent:
        lines.append(f"| {row['date']} | {_fmt(row.get('close'))} |")

    lines += [
        "",
        "Use this snapshot as the source of truth for exact OHLCV, price-level, "
        "and indicator-value claims. If another tool output conflicts with it, "
        "flag the discrepancy rather than inventing a reconciled number. Do not "
        "claim historical validation, support/resistance bounces, or exact "
        "percentage moves unless directly supported by tool output with concrete "
        "dates and prices.",
    ]
    return "\n".join(lines)


def build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Iterable[str] | None = None,
) -> str:
    """Render a ground-truth snapshot: latest OHLCV row, indicators, recent closes."""
    snapshot = build_verified_market_snapshot_data(symbol, curr_date, indicators)
    return render_verified_market_snapshot(snapshot, look_back_days)
