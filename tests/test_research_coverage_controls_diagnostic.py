"""Offline dispatch/accounting checks; mocked labels are not semantic evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import research_coverage_diagnostic as diagnostic
from tests.test_research_coverage_diagnostic import (
    Clock,
    FixtureService,
    cli_result,
    issues_and_reader,
    request_and_plan,
    trace,
    valid_reply,
)
from tradingagents.research.storage import canonical_json, digest


def control_fixture(tmp_path):
    request, _ = request_and_plan(tmp_path)
    request = request.model_copy(update={"budget": diagnostic.control_budget()})
    issues, reader = issues_and_reader()
    issues[2].update(reader_coverage_required=True,
                     origins=[{"origin_id": "case.review.draft"}],
                     text="The financial schedules are an unreviewed draft; ingestion is not approval.")
    return request, diagnostic.build_control_plan(request, issues, reader)


def control_reply(payload):
    reply = valid_reply(payload)
    # Supply mechanically exact witnesses for mock non-target dispositions.
    for item in reply.data["limitation_dispositions"]:
        item["reader_excerpt"] = "Exact reader caveat 0."
    return reply


def test_control_plan_pairs_exact_inventories_and_reverses_policy_order(tmp_path):
    request, plan = control_fixture(tmp_path)
    assert diagnostic.validate_plan(plan) == request
    assert [call["policy"] for call in plan["calls"]] == [
        "packed-24", "legacy-12", "legacy-12", "legacy-12", "legacy-12", "packed-24"]
    for group in (plan["calls"][:3], plan["calls"][3:]):
        packed = next(call for call in group if call["policy"] == "packed-24")
        legacy = [call for call in group if call["policy"] == "legacy-12"]
        assert packed["issues"] == legacy[0]["issues"] + legacy[1]["issues"]
        assert len({call["payload"]["research"]["rendered_reader"] for call in group}) == 1
        for call in group:
            assert "expected_decisions" not in repr(call["payload"])
            assert "control_id" not in call["payload"]
    assert plan["aggregate_reserve_tokens"] < request.budget.total_tokens == 350_000


def test_control_plan_selects_the_global_protected_target_batch_without_reordering(tmp_path):
    request, _ = request_and_plan(tmp_path)
    request = request.model_copy(update={"budget": diagnostic.control_budget()})
    issues = [{"issue_id": f"issue-{index}", "text": f"Exact reader caveat {index}.",
               "origins": [], "reader_coverage_required": False} for index in range(42)]
    issues[30].update(reader_coverage_required=True, origins=[{"origin_id": "case.review.draft"}])

    plan = diagnostic.build_control_plan(request, issues, "\n".join(item["text"] for item in issues))

    assert plan["kind"] == "disclosure-control-diagnostic-v2"
    assert plan["selected_packed_batch_index"] == 1
    assert [item["issue_id"] for item in plan["calls"][0]["issues"]] == [
        f"issue-{index}" for index in range(24, 42)
    ]


def test_control_plan_fails_when_protected_batch_is_not_a_matched_pair(tmp_path):
    request, _ = request_and_plan(tmp_path)
    request = request.model_copy(update={"budget": diagnostic.control_budget()})
    issues = [{"issue_id": f"issue-{index}", "text": f"Exact reader caveat {index}.",
               "origins": [], "reader_coverage_required": False} for index in range(30)]
    issues[24].update(reader_coverage_required=True, origins=[{"origin_id": "case.review.draft"}])

    with pytest.raises(ValueError, match="not a matched one-versus-two"):
        diagnostic.build_control_plan(request, issues, "\n".join(item["text"] for item in issues))


def test_historical_v1_control_plan_is_validatable_but_not_executable(tmp_path):
    request, _plan = control_fixture(tmp_path)
    issues, reader = issues_and_reader()
    issues[2].update(reader_coverage_required=True,
                     origins=[{"origin_id": "case.review.draft"}],
                     text="The financial schedules are an unreviewed draft; ingestion is not approval.")
    plan = diagnostic._build_control_plan_v1(request, issues, reader)
    service = FixtureService()

    assert diagnostic.validate_plan(plan) == request
    with pytest.raises(ValueError, match="read-only"):
        diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert service.calls == [] and service.closed == 0
    assert not request.output_dir.exists()


@pytest.mark.parametrize("field", ["order", "expected", "reader", "budget", "payload", "reserve"])
def test_control_plan_rejects_manifest_drift(tmp_path, field):
    _request, plan = control_fixture(tmp_path)
    plan = deepcopy(plan)
    if field == "order":
        plan["calls"][0], plan["calls"][1] = plan["calls"][1], plan["calls"][0]
    elif field == "expected":
        plan["controls"][1]["expected_decisions"]["issue-2"] = "reader_covered"
    elif field == "reader":
        plan["baseline_reader"] += " changed"
    elif field == "budget":
        plan["request"]["budget"]["total_tokens"] += 1
    elif field == "payload":
        plan["calls"][0]["payload"]["system"] += " hidden answer"
    else:
        plan["aggregate_reserve_tokens"] -= 1
    with pytest.raises(ValueError):
        diagnostic.validate_plan(plan)


def test_all_six_calls_share_one_budget_and_four_target_scores(tmp_path):
    request, plan = control_fixture(tmp_path)
    service = FixtureService([control_reply] * 6)
    result = diagnostic.execute_plan(plan, request, service, clock=Clock())
    assert result.stop_reason == "coverage_diagnostic_completed"
    assert result.usage.total_tokens == 144
    assert len(service.calls) == 6 and service.closed == 1
    rows = trace(request)["calls"]
    assert ["control_score" in row for row in rows] == [True, True, False, True, False, True]
    # A semantically wrong negative answer is retained, not retried or accepted.
    assert not rows[0]["control_score"]["passed"]


def test_second_control_failure_preserves_spend_and_prevents_remaining_calls(tmp_path):
    request, plan = control_fixture(tmp_path)
    service = FixtureService([control_reply] * 3 + [RuntimeError("synthetic")])
    result = diagnostic.execute_plan(plan, request, service, clock=Clock())
    assert not result.usage.complete and result.usage.total_tokens == 72
    assert len(service.calls) == 4 and service.closed == 1
    assert trace(request)["pending_dispatch"]


def test_control_budget_overshoot_stops_next_call(tmp_path):
    request, plan = control_fixture(tmp_path)
    service = FixtureService([lambda payload: valid_reply(payload, tokens=350_001)])
    result = diagnostic.execute_plan(plan, request, service, clock=Clock())
    assert result.stop_reason == "coverage_diagnostic_failed"
    assert result.usage.complete and len(service.calls) == 1


def test_control_deadline_is_not_renewed_between_conditions(tmp_path):
    request, plan = control_fixture(tmp_path)
    clock = Clock()
    service = FixtureService([control_reply] * 6, clock=clock, advances=[200] * 6)
    result = diagnostic.execute_plan(plan, request, service, clock=clock)
    assert result.stop_reason == "coverage_diagnostic_failed"
    assert len(service.calls) == 5
    assert [payload["timeout_seconds"] for _, payload, _ in service.calls] == [600, 600, 480, 280, 80]


def test_control_cli_shares_supervisor_and_single_use_approval_guards(tmp_path, monkeypatch):
    request, plan = control_fixture(tmp_path)
    plan.update(diagnostic.execution_binding(tmp_path / "home"))
    path = tmp_path / "plan.json"
    plan["source_artifact_sha256"] = {}
    path.write_bytes(canonical_json(plan))
    calls = []

    def supervise(worker, called_request, *, timeout_seconds):
        calls.append(worker)
        assert called_request == request
        assert timeout_seconds == 890
        return SimpleNamespace(status="completed", code="completed", result=cli_result(request))

    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(diagnostic, "run_supervised", supervise)
    args = ["run", "--plan", str(path), "--approved-plan-sha256", digest(plan),
            "--codex-home", plan["codex_home"], "--allow-live", "--allow-advisory-token-cap"]
    assert diagnostic.main(args) == 0
    with pytest.raises(FileExistsError):
        diagnostic.main(args)
    assert len(calls) == 1
