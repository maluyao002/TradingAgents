"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.

``extract_rating`` returns ``None`` when no rating can be found, so the graph can
surface an explicit ``REVIEW`` signal instead of a fabricated ``Hold`` (#1170).
``parse_rating`` uses ``REVIEW`` for callers that need a string, including the
decision log. Conflicting labels remain ambiguous, as required by the fork's
research acceptance gate.
"""

from __future__ import annotations

import re
import unicodedata

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

# Signal emitted when the model's decision has no recognizable rating. It is not
# a tradeable position: it flags output that needs a human/re-run rather than
# silently degrading to Hold. Callers that map the signal onto the 5-tier enum
# (e.g. ``PortfolioRating(signal)``) should guard with ``is_review`` first.
RATING_REVIEW = "REVIEW"

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}
_RATING_LABEL_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|[-*]\s+|\d+[.)]\s+)?\**Rating\**\s*"
    r"[:\-\u2010-\u2015][\s*]*(\w+)(.*)$", re.IGNORECASE | re.MULTILINE,
)

# Standalone 5-tier word anywhere (word boundaries so "Buyer"/"Holding" don't match).
_RATING_WORD_RE = re.compile(
    r"\b(" + "|".join(RATINGS_5_TIER) + r")\b", re.IGNORECASE
)


def extract_explicit_rating(text: str) -> str | None:
    """Read an unambiguous standalone label shared by the gate and log."""
    if not isinstance(text, str):
        return None
    labels = _RATING_LABEL_RE.findall(unicodedata.normalize("NFKC", text))
    ratings = set()
    for value, suffix in labels:
        if value.lower() not in _RATING_SET:
            return None
        rating = value.capitalize()
        # Decision renderers put explanations on subsequent lines. Requiring a
        # standalone label also rejects alternatives such as "Buy / REVIEW" or
        # "Buy or TBD", which contain no second member of the rating scale.
        if suffix.strip().strip("*").strip() not in {"", "."}:
            return None
        ratings.add(rating)
    return next(iter(ratings)) if len(ratings) == 1 else None


def extract_rating(text: str) -> str | None:
    """Read a labelled decision, or a single unambiguous rating in prose."""
    if not isinstance(text, str) or not text:
        return None
    norm = unicodedata.normalize("NFKC", text)
    if _RATING_LABEL_RE.search(norm):
        return extract_explicit_rating(norm)
    named = {m.group(1).capitalize() for m in _RATING_WORD_RE.finditer(norm)}
    return named.pop() if len(named) == 1 else None


def parse_rating(text: str, default: str = RATING_REVIEW) -> str:
    """Extract a rating, defaulting unreadable decisions to ``REVIEW``."""
    rating = extract_rating(text)
    return rating if rating is not None else default


def is_review(signal: str) -> bool:
    """Whether a signal is the non-tradeable REVIEW sentinel (#1170)."""
    return signal == RATING_REVIEW
