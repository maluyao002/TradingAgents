"""Deterministic acceptance gate for research outputs, not trading approval."""

import re
import unicodedata
from collections.abc import Mapping, Sequence

from tradingagents.agents.utils.evidence import _packet_from_value
from tradingagents.agents.utils.rating import RATING_REVIEW, RATINGS_5_TIER

_REPORTS = {
    "market": "market_report",
    "sentiment": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}
_UNAVAILABLE = re.compile(
    r"^\s*(?:<(?:\w+\s+)?unavailable\b|(?:error|unavailable)\s*:|"
    r"<no (?:StockTwits messages|Reddit posts)\b|"
    r"error fetching (?:global )?news\b|"
    r"(?:company news|global news|news|data) unavailable\b|"
    r"no (?:(?:global )?news|articles|posts|messages|data) (?:found|available|returned)\b)",
    re.IGNORECASE,
)
_EXPLICIT_RATING = re.compile(
    r"^\s*(?:#{1,6}\s*)?\**Rating\**\s*[:：-][\s*]*(" + "|".join(RATINGS_5_TIER) + r")\b",
    re.IGNORECASE | re.MULTILINE,
)


def _normalized_text(value) -> str | None:
    """Canonicalize persisted copies without weakening exact-content checks."""
    if not isinstance(value, str):
        return None
    return unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _role_scope(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if not all(isinstance(role, str) and role in {*_REPORTS, "social"} for role in value):
        return None
    return {"sentiment" if role == "social" else role for role in value}


def assess_research_quality(state: Mapping, selected_analysts=None, backend=None) -> dict:
    """Revalidate source-backed packets; never trust a stored accepted flag.

    Accepted means selected evidence contracts and final workflow outputs are
    present. It does not certify factual truth, data freshness or profitability.
    Missing selection defaults conservatively to the full four-analyst workflow.
    """
    reasons = []
    stored_selection = state.get("_selected_analysts")
    if stored_selection is not None and selected_analysts is not None and (
        _role_scope(stored_selection) != _role_scope(selected_analysts)
    ):
        reasons.append({"code": "analyst_scope_mismatch"})
    selected = stored_selection if stored_selection is not None else selected_analysts
    if selected is None:
        selected = tuple(_REPORTS)
    stored_backend = state.get("_research_backend")
    if stored_backend is not None and backend is not None and stored_backend != backend:
        reasons.append({"code": "backend_scope_mismatch"})
    effective_backend = stored_backend if stored_backend is not None else backend
    if effective_backend not in (None, "api", "codex"):
        reasons.append({"code": "invalid_backend"})
    roles = []
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        selected = []
    for role in selected:
        role = "sentiment" if role == "social" else role
        if isinstance(role, str) and role in _REPORTS:
            if role not in roles:
                roles.append(role)
        else:
            reasons.append({"code": "invalid_analyst_selection"})
    if not roles:
        reasons.append({"code": "no_selected_analysts"})
    packets = state.get("evidence_packets")
    prepared = state.get("prepared_data")
    packets = packets if isinstance(packets, Mapping) else {}
    prepared = prepared if isinstance(prepared, Mapping) else {}
    for role in roles:
        report = state.get(_REPORTS[role])
        if not isinstance(report, str) or not report.strip():
            reasons.append({"role": role, "code": "missing_analyst_report"})
        raw = packets.get(role)
        data = prepared.get(role)
        if not isinstance(raw, Mapping) or not isinstance(data, Mapping):
            reasons.append({"role": role, "code": "missing_evidence"})
            continue
        try:
            packet = _packet_from_value(role, raw, prepared=data)
        except (TypeError, ValueError, AttributeError):
            packet = None
        if packet is None or not packet.compacted or packet.validation_errors:
            reasons.append({"role": role, "code": "invalid_evidence"})
            continue
        if _normalized_text(report) != _normalized_text(packet.report):
            reasons.append({"role": role, "code": "analyst_report_mismatch"})
        if not packet.sources or (role in {"market", "fundamentals"} and not packet.facts):
            reasons.append({"role": role, "code": "no_usable_evidence"})
        # Required baselines must be present even if a well-formed answer says
        # they were never supplied. Unselected analysts are not required.
        required = {
            "news": {"news-company-baseline"},
            "sentiment": {
                "sentiment-news",
                "sentiment-stocktwits",
                "sentiment-reddit",
            },
        }.get(role, set())
        if role == "news" and effective_backend == "codex":
            required.add("news-global-baseline")
        source_ids = {source.id for source in packet.sources}
        if not required <= source_ids:
            reasons.append({"role": role, "code": "missing_required_source"})
        if any(
            not source.content.strip() or _UNAVAILABLE.search(source.content)
            for source in packet.sources
        ):
            reasons.append({"role": role, "code": "source_unavailable"})
    for key in ("investment_plan", "trader_investment_plan", "final_trade_decision"):
        value = state.get(key)
        if not isinstance(value, str) or not value.strip():
            reasons.append({"code": "missing_" + key})
    decision = state.get("final_trade_decision")
    complete_debates = {}
    for key, fields in {
        "investment_debate_state": ("bull_history", "bear_history", "history", "judge_decision"),
        "risk_debate_state": (
            "aggressive_history",
            "conservative_history",
            "neutral_history",
            "history",
            "judge_decision",
        ),
    }.items():
        debate = state.get(key)
        complete = isinstance(debate, Mapping) and not any(
            not isinstance(debate.get(field), str) or not debate[field].strip() for field in fields
        )
        if not complete:
            reasons.append({"code": "incomplete_" + key})
        else:
            complete_debates[key] = debate
    investment_debate = complete_debates.get("investment_debate_state")
    if investment_debate is not None and _normalized_text(
        state.get("investment_plan")
    ) != _normalized_text(investment_debate["judge_decision"]):
        reasons.append({"code": "investment_plan_mismatch"})
    risk_debate = complete_debates.get("risk_debate_state")
    if risk_debate is not None and _normalized_text(decision) != _normalized_text(
        risk_debate["judge_decision"]
    ):
        reasons.append({"code": "final_decision_mismatch"})
    # A passing gate requires an explicit, unambiguous decision label. Mentioning
    # "buy" in a rejected alternative must not produce an accepted Buy signal.
    ratings = (
        set(_EXPLICIT_RATING.findall(unicodedata.normalize("NFKC", decision)))
        if isinstance(decision, str)
        else set()
    )
    ratings = {value.capitalize() for value in ratings}
    rating = next(iter(ratings)) if len(ratings) == 1 else None
    if rating is None:
        reasons.append({"code": "unparseable_final_rating"})
    accepted = not reasons
    return {
        "schema_version": 1,
        "status": "accepted" if accepted else "degraded",
        "accepted": accepted,
        "selected_analysts": roles,
        "reasons": reasons,
        "signal": rating if accepted else RATING_REVIEW,
        "scope": "Structural research acceptance only; not factual verification or authorization to trade.",
    }


def finalize_research_quality(state: dict, selected_analysts, backend="api") -> dict:
    """Attach scope and freshly computed acceptance to a completed state."""
    state["_selected_analysts"] = list(selected_analysts)
    state["_research_backend"] = backend
    state["research_quality"] = assess_research_quality(state)
    return state["research_quality"]
