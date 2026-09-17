import pytest

from tradingagents.research.contracts import EvidenceSnapshot
from tradingagents.research.evaluation import BenchmarkCase, score_case
from tradingagents.research.storage import digest


def test_scoring_does_not_call_empty_or_synthetic_gold_validated():
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff="2026-09-17T00:00:00Z")
    case = BenchmarkCase(id="empty", ticker="NVDA", window="quiet", evidence_hash=digest(snapshot))
    score = score_case(case, snapshot, ())
    assert score.precision is None and score.recall is None
    assert not score.eligible_for_release
    synthetic = case.model_copy(update={"window": "synthetic", "human_reviewed": True,
                                        "material_event_ids": ("event",)})
    assert not score_case(synthetic, snapshot, ("event",)).eligible_for_release


def test_precision_recall_and_must_capture():
    snapshot = EvidenceSnapshot(ticker="AMD", cutoff="2026-09-17T00:00:00Z")
    case = BenchmarkCase(id="events", ticker="AMD", window="earnings",
                         evidence_hash=digest(snapshot), material_event_ids=("a", "b"),
                         must_capture_event_ids=("a",), human_reviewed=True)
    score = score_case(case, snapshot, ("b", "noise"))
    assert score.precision == score.recall == .5
    assert score.missing_must_capture == ("a",)
    assert not score.eligible_for_release
    assert score_case(case, snapshot, ("a", "b")).eligible_for_release
    with pytest.raises(ValueError, match="differs"):
        score_case(case, snapshot.model_copy(update={"ticker": "INTC"}), ())
