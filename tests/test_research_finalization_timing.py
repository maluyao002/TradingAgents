from types import SimpleNamespace

import pytest

from tests.test_research_bounded_finalization import BoundedFixture
from tests.test_research_engine import setup
from tradingagents.research.budget import BudgetTracker
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_timing import finalization_time_plan
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import read_json

MODELS = {role: SimpleNamespace(model="test-model", effort="high")
          for role in ("editor", "verifier")}


def call(stage="verify_report-coverage-2", cached=False):
    return {"call_id": stage, "cache_hit": cached, "timeout_seconds": 600}


def sample(stage="verify_report-coverage-0", duration=100, **updates):
    return {"stage": stage, "role": "verifier", "model": "test-model", "effort": "high",
            "duration_seconds": duration, "completed": True, "stage_accepted": True,
            "usage_origin": "current_live", **updates}


def test_cold_start_is_unknown_not_timeout_as_prediction():
    plan = finalization_time_plan([call()], [], MODELS, remaining_seconds=300)
    assert plan["estimated_seconds"] is None
    assert plan["unknown_call_count"] == 1
    assert plan["fits_observed_estimate"] is None
    assert plan["worst_case_timeout_seconds"] == 600
    assert not plan["stop_before_dispatch"]


def test_max_recent_observed_headroom_and_cache_exclusion():
    samples = [sample(duration=d) for d in (999, 40, 50, 60, 70, 80)]
    plan = finalization_time_plan([call(), call("verify_report-coverage-3", True)],
                                  samples, MODELS, remaining_seconds=109)
    assert plan["estimated_seconds"] == 110  # max(last 5)*1.25 + 10 seconds overhead
    assert plan["stop_before_dispatch"]
    assert plan["calls"][1]["estimated_seconds"] == 0
    assert not plan["is_completion_guarantee"]


@pytest.mark.parametrize("updates", [
    {"completed": False}, {"usage_origin": "imported_historical"},
    {"stage_accepted": False},
    {"usage_origin": "validated_reuse"}, {"model": "different"}, {"effort": "low"},
    {"duration_seconds": float("nan")}, {"duration_seconds": -1},
    {"duration_seconds": True}, {"role": "editor"},
    {"service_kind": "replay"},
])
def test_failed_imported_or_incompatible_samples_do_not_estimate(updates):
    plan = finalization_time_plan([call()], [sample(), sample(**updates)], MODELS,
                                  remaining_seconds=300)
    assert plan["estimated_seconds"] is None


def test_unknown_future_calls_cannot_hide_unaffordable_known_work():
    plan = finalization_time_plan([call(), call("repair_report")],
                                  [sample(), sample(duration=110)], MODELS,
                                  remaining_seconds=140)
    assert plan["estimated_seconds"] is None
    assert plan["stop_before_dispatch"]


def test_cached_only_needs_no_provider_time():
    plan = finalization_time_plan([call(cached=True)], [], MODELS, remaining_seconds=0)
    assert plan["estimated_seconds"] == 0
    assert not plan["stop_before_dispatch"]


def timed_run(tmp_path, monkeypatch, *, count, wall_seconds, warning=False):
    from tradingagents.research import engine

    now = [0.0]
    monkeypatch.setattr(engine, "BudgetTracker", lambda *a, **kw:
                        BudgetTracker(*a, **kw, clock=lambda: now[0]))

    class SlowFixture(BoundedFixture):
        kind = "injected_timing_fixture"

        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            stage = payload["stage"]
            now[0] += (40 if "-coverage-" in stage else
                       60 if stage in {"editor", "verify_report"} and warning else 1)
            return reply

    request, services = setup(tmp_path)
    request = request.model_copy(update={"quality_revision": "evidence-led-bounded",
        "report_language": "English", "budget": request.budget.model_copy(update={
            "wall_seconds": wall_seconds, "reserve_seconds": 0, "reserve_tokens": 0,
            "total_tokens": 10_000_000, "call_timeout_seconds": 100,
        })})
    models = SlowFixture(count=count, warning_once=warning)
    result = run_research(request, ResearchServices(services.evidence, models))
    return request, models, result


def test_observed_coverage_stops_before_an_unfinishable_remaining_pass(tmp_path, monkeypatch):
    request, models, result = timed_run(tmp_path, monkeypatch, count=70, wall_seconds=220)
    assert result.stop_reason == "finalization_pass_time_insufficient"
    coverage = [p for _, p in models.calls if "-coverage-" in p["stage"]]
    assert len(coverage) == 2
    assert result.usage.complete
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert not resources["dispatched"]
    plans = read_json(request.output_dir / "finalization_plan.json")
    checks = plans["verify_report"]["coverage_time_checks"]
    assert checks[0]["estimated_seconds"] is None
    assert checks[-1]["stop_before_dispatch"]
    assert len(resources["call_timings"]) == len(models.calls)
    assert all(row["completed"] for row in resources["call_timings"])


def test_repair_not_bought_when_observed_full_repair_path_cannot_fit(tmp_path, monkeypatch):
    _, models, result = timed_run(tmp_path, monkeypatch, count=25, wall_seconds=400, warning=True)
    assert result.stop_reason == "repair_path_time_insufficient"
    assert result.usage.complete
    assert not any(p["stage"] == "repair_report" for _, p in models.calls)
