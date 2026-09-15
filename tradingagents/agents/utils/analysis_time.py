"""Local-calendar context matching the CLI date selector, without changing provider windows."""

from datetime import date, datetime, time, timedelta
from typing import Any


def analysis_calendar(value: Any) -> dict[str, str | None]:
    """Describe date-only selection; localize each boundary separately for DST.

    These are calendar boundaries, not an assertion of a market-close cutoff or
    evidence availability. Provider publication filters keep their own rules.
    """
    result = {
        "timezone_basis": "system local (same as CLI analysis date)",
        "day_start": None,
        "day_end_exclusive": None,
        "decision_cutoff": "not specified; date-only selection",
    }
    try:
        selected = date.fromisoformat(value) if isinstance(value, str) else None
        if selected is not None:
            result["day_start"] = datetime.combine(selected, time.min).astimezone().isoformat()
            result["day_end_exclusive"] = datetime.combine(
                selected + timedelta(days=1), time.min,
            ).astimezone().isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return result


def local_retrieval_time(value: Any) -> str:
    """Convert only offset-aware timestamps; never guess an absent timezone."""
    if not isinstance(value, str):
        return "unknown"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return "unknown"
        return stamp.astimezone().isoformat()
    except (ValueError, OverflowError, OSError):
        return "unknown"


def render_analysis_calendar(value: Any) -> str:
    context = analysis_calendar(value)
    return (
        "Analysis calendar: system local, matching the CLI date selector; "
        f"day starts {context['day_start'] or 'unknown'}, "
        f"ends before {context['day_end_exclusive'] or 'unknown'}. "
        "No intraday decision cutoff is specified. Provider publication windows "
        "retain their own boundaries. Retrieval time is not publication time or bar finality."
    )
