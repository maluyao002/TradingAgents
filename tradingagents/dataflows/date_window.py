"""Shared look-ahead-safe date-window filtering for dated content.

News, StockTwits, and Reddit all pull recent items that must be trimmed to the
analysis window so a historical/backtest run never sees content published after
its as-of date. Centralizing the rule keeps every source consistent (#1126,
#1220): every timestamp is normalized to UTC, the upper bound is exclusive at
midnight after ``end`` (so an item stamped exactly then can't leak), and an
undated item is kept only when the window reaches the present (a live run), since
in a backtest we can't prove it isn't future.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .utils import get_current_date


def require_iso_date(value: str) -> str:
    """Reject ambiguous run dates before any point-in-time comparison or fetch."""
    try:
        canonical = date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("Analysis date must use canonical YYYY-MM-DD") from None
    if canonical != value:
        raise ValueError("Analysis date must use canonical YYYY-MM-DD")
    return value


def to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC-aware; a naive value is assumed to be UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def in_window(pub_dt: datetime | None, start_dt: datetime, end_dt: datetime) -> bool:
    """Whether an item belongs in the half-open window ``[start, end + 1 day)``.

    ``pub_dt`` None means undated: kept only when the window reaches the present.
    """
    end = to_utc(end_dt)
    if pub_dt is not None:
        return to_utc(start_dt) <= to_utc(pub_dt) < end + timedelta(days=1)
    return end >= datetime.now(timezone.utc) - timedelta(days=1)


def coverage_gap(
    dates, start_date: str, end_date: str, source: str, subject: str
) -> str | None:
    """Explain when a recent-items feed never observed the requested window.

    Timestamps only prove continuous coverage for a newest-first feed. Callers
    merging relevance-ranked searches should pass no dates.
    """
    now = datetime.now(timezone.utc)
    oldest = min((to_utc(date) for date in dates if date is not None), default=now)
    if datetime.strptime(end_date, "%Y-%m-%d").date() > now.date():
        reason = "the window extends past today"
    elif oldest.date() > datetime.strptime(start_date, "%Y-%m-%d").date():
        reason = "it only serves recent items"
    else:
        return None
    return (
        f"<unavailable: {source} for {start_date}..{end_date}: {reason}, "
        f"so this is not an absence of {subject}>"
    )


def _parse(date: str | None) -> datetime | None:
    try:
        return datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def as_of(requested: str | None, trade_date: str) -> str | None:
    """Bound a tool's requested date by the date injected from graph state.

    Direct calls outside a graph retain their existing date behavior.
    """
    if not trade_date:
        return requested
    parsed = _parse(requested)
    run_date = _parse(trade_date)
    return requested if parsed is not None and run_date is not None and parsed <= run_date else trade_date


def as_of_window(start_date: str, end_date: str, trade_date: str) -> tuple[str, str]:
    """Clamp a requested window to the run date, retaining its length if wholly later."""
    start, requested_end = _parse(start_date), _parse(end_date)
    if trade_date and start is not None and requested_end is not None and start > requested_end:
        raise ValueError(f"requested date window starts after it ends: {start_date} > {end_date}")
    end = as_of(end_date, trade_date)
    bounded_end = _parse(end)
    if end == end_date or start is None or bounded_end is None or start <= bounded_end:
        return start_date, end
    span = requested_end - start if requested_end is not None and requested_end >= start else timedelta(0)
    return f"{bounded_end - span:%Y-%m-%d}", end


def withhold_live_profile(curr_date: str | None, label: str) -> str | None:
    """Notice to serve instead of a live-only company profile, or None to serve it.

    Vendor "company overview" endpoints (yfinance ``Ticker.info``, Alpha Vantage
    ``OVERVIEW``) carry no historical vintage — not even name, sector and
    industry, which move when a company renames or is reclassified — so serving
    one into a run dated in the past leaks post-decision information (#1300).
    Every fundamentals vendor withholds on this rule, so switching between them
    cannot reintroduce the leak.
    """
    if not curr_date:
        return None
    today = get_current_date()
    if curr_date >= today:
        return None
    return (
        f"# Company Fundamentals for {label}\n"
        f"# Point-in-time as of: {curr_date}\n\n"
        f"Profile fundamentals are withheld for this date. This vendor serves "
        "only present-day values with no historical vintage: market "
        f"cap, valuation multiples, the 52-week range and TTM income move with "
        f"today's quote, and even the name, sector and industry reflect today "
        f"rather than {curr_date} (companies rename and get reclassified). "
        f"Serving them would put post-decision information into a {curr_date} "
        f"analysis. Point-in-time fundamentals for {curr_date} are available "
        f"from the balance sheet, income statement, and cash flow tools."
    )
