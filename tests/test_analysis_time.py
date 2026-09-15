"""Calendar rollover regressions; no model or provider requests."""

import copy
import json
import time

import pytest

from tradingagents.agents.utils.analysis_time import analysis_calendar, local_retrieval_time
from tradingagents.agents.utils.evidence import (
    HANDOFF_END,
    HANDOFF_START,
    build_packet,
    render_analyst_context,
    render_prepared_evidence,
)
from tradingagents.agents.utils.prompt_policy import decision_context, evidence_policy
from tradingagents.reporting import build_run_metadata


@pytest.fixture
def pacific(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("System-local timezone override requires tzset")
    with monkeypatch.context() as patch:
        patch.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        try:
            yield
        finally:
            patch.undo()
            time.tzset()


def test_saved_run_utc_rollover_is_same_local_date_in_both_prompt_views(pacific):
    prepared = {
        "analysis_date": "2026-09-14",
        "sources": [{"id": "sentiment-news", "label": "News", "vendor": "example",
                     "content": "Supplied news", "retrieved_at": "2026-09-15T03:25:17Z",
                     "published_at": None}],
        "facts": [],
    }
    original = copy.deepcopy(prepared)
    report = HANDOFF_START + json.dumps({
        "conclusions": ["Limited news evidence [sentiment-news]."],
        "caveats": ["Publication is unknown."], "conflicts": [],
        "evidence_ids": ["sentiment-news"],
    }) + HANDOFF_END
    packet = build_packet("sentiment", report, prepared)
    assert packet.compacted
    state = {"trade_date": "2026-09-14", "prepared_data": {"sentiment": prepared},
             "evidence_packets": {"sentiment": packet.to_dict()}}
    for rendered in (render_prepared_evidence(prepared), render_analyst_context(state)):
        assert "2026-09-15T03:25:17Z" in rendered
        assert "2026-09-14T20:25:17-07:00" in rendered
        assert "2026-09-14T00:00:00-07:00" in rendered
        assert "2026-09-15T00:00:00-07:00" in rendered
        assert "No intraday decision cutoff is specified" in rendered
    assert prepared == original
    assert packet.sources[0].published_at == "unknown"
    assert "not their UTC date labels alone" in evidence_policy()
    assert "preserve unknown publication/vintage caveats separately" in evidence_policy()
    assert "2026-09-14T00:00:00-07:00" in decision_context(state)
    assert build_run_metadata(state, "AMD")["analysis_calendar"] == analysis_calendar("2026-09-14")


def test_actual_next_local_day_is_not_shifted_back_to_analysis_day(pacific):
    assert local_retrieval_time("2026-09-15T08:25:00Z") == "2026-09-15T01:25:00-07:00"


@pytest.mark.parametrize("value", [None, "unknown", "bad", "2026-09-15", "2026-09-15T03:25:17"])
def test_missing_timestamp_offset_is_not_inferred(value):
    assert local_retrieval_time(value) == "unknown"


def test_day_boundaries_honor_daylight_saving_changes(pacific):
    spring = analysis_calendar("2026-03-08")
    assert spring["day_start"] == "2026-03-08T00:00:00-08:00"
    assert spring["day_end_exclusive"] == "2026-03-09T00:00:00-07:00"
    fall = analysis_calendar("2026-11-01")
    assert fall["day_start"] == "2026-11-01T00:00:00-07:00"
    assert fall["day_end_exclusive"] == "2026-11-02T00:00:00-08:00"


def test_unknown_analysis_date_does_not_invent_cutoff():
    context = analysis_calendar("unknown")
    assert context["day_start"] is None and context["day_end_exclusive"] is None
    assert "not specified" in context["decision_cutoff"]


def test_future_publication_still_rejected_independently_of_retrieval_display(pacific):
    prepared = {"analysis_date": "2026-09-14", "sources": [{
        "id": "sentiment-news", "content": "Future publication",
        "retrieved_at": "2026-09-15T03:25:17Z", "published_at": "2026-09-16T00:00:00Z",
    }], "facts": []}
    rendered = render_prepared_evidence(prepared)
    assert "publication is after analysis date" in rendered
    assert "Future publication" not in rendered


@pytest.mark.parametrize("published,accepted", [
    ("2026-09-15T03:25:17Z", True),  # Still September 14 locally.
    ("2026-09-15T07:00:00Z", False),  # Exact start of the next local day.
    ("2026-09-14T23:00:00-10:00", False),  # September 15 in Pacific time.
    ("2026-09-15", False),  # Date-only sources retain their stated date.
    ("2026-09-14T20:25:17", True),  # Naive sources retain their stated date.
])
def test_source_and_fact_publication_use_analysis_calendar(pacific, published, accepted):
    prepared = {"analysis_date": "2026-09-14", "sources": [{
        "id": "market-source", "content": "Source data",
        "retrieved_at": "2026-09-15T03:25:17Z", "published_at": published,
    }], "facts": [{
        "id": "market-fact", "metric": "price", "value": 10, "unit": "USD",
        "period": "2026-09-14", "basis": "reported", "kind": "reported",
        "source_id": "market-source", "published_at": published,
    }]}
    report = HANDOFF_START + json.dumps({
        "conclusions": ["Price is 10 [market-fact]."], "caveats": [], "conflicts": [],
        "evidence_ids": ["market-fact"],
    }) + HANDOFF_END
    packet = build_packet("market", report, prepared)
    assert packet.compacted is accepted
    # Also exercise the independently dated fact path with a known-old source.
    prepared["sources"][0]["published_at"] = "2026-09-13"
    packet = build_packet("market", report, prepared)
    assert packet.compacted is accepted


@pytest.mark.parametrize("published", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"])
def test_publication_timezone_overflow_is_invalid_metadata_not_a_crash(pacific, published):
    rendered = render_prepared_evidence({"analysis_date": "2026-09-14", "sources": [{
        "id": "market-source", "content": "Untrusted extreme timestamp",
        "published_at": published,
    }], "facts": []})
    assert "invalid published_at" in rendered
