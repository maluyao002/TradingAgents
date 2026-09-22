"""Offline contract tests for the paired reader-coverage diagnostic."""

from collections import deque
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import research_coverage_diagnostic as diagnostic
from tradingagents.research.contracts import Assessment, ResearchRequest, ResearchResult, Usage
from tradingagents.research.services import ModelReply
from tradingagents.research.storage import canonical_json, digest, read_json


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FixtureService:
    def __init__(self, responses=(), *, clock=None, advances=()):
        self.responses = deque(responses)
        self.clock = clock
        self.advances = deque(advances)
        self.calls = []
        self.closed = 0

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload), request))
        if self.clock is not None and self.advances:
            self.clock.advance(self.advances.popleft())
        response = self.responses.popleft() if self.responses else None
        if isinstance(response, Exception):
            raise response
        if response is None:
            return valid_reply(payload)
        if callable(response):
            return response(payload)
        return response

    def close(self):
        self.closed += 1


def issues_and_reader():
    issues = [
        {"issue_id": f"issue-{index}", "text": f"Exact reader caveat {index}."}
        for index in range(18)
    ]
    return issues, "\n".join(issue["text"] for issue in issues)


def request_and_plan(tmp_path):
    request = ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="codex",
        output_dir=tmp_path / "run_1",
        quality_revision="evidence-led-bounded",
        report_language="English",
        budget=diagnostic.diagnostic_budget(),
    )
    issues, reader = issues_and_reader()
    return request, diagnostic.build_plan(request, issues, reader)


def valid_reply(payload, *, complete=True, tokens=24, excerpt="from-reader"):
    issues = payload["research"]["limitation_review"]
    return ModelReply(
        data={
            "reviewed_report": True,
            "limitation_dispositions": [
                {
                    "issue_id": issue["issue_id"],
                    "decision": "reader_covered",
                    "rationale": "Exact fixture coverage disposition.",
                    "reader_excerpt": issue["text"] if excerpt == "from-reader" else excerpt,
                }
                for issue in issues
            ],
        },
        usage=Usage(input_tokens=tokens, output_tokens=0, complete=complete),
    )


def trace(request):
    return read_json(request.output_dir / "diagnostic.json")


def cli_plan(tmp_path, *, output_dir=None, codex_home=None):
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    codex_home = (codex_home or tmp_path / "home").resolve()
    request = ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="codex",
        output_dir=output_dir or capsule / "run_1",
        quality_revision="evidence-led-bounded",
        report_language="English",
        budget=diagnostic.diagnostic_budget(),
    )
    issues, reader = issues_and_reader()
    plan = diagnostic.build_plan(request, issues, reader)
    plan.update(diagnostic.execution_binding(codex_home))
    plan["source_artifact_sha256"] = {}
    path = capsule / "plan.json"
    path.write_bytes(canonical_json(plan))
    return capsule, path, request, plan


def cli_result(request):
    return ResearchResult(
        ticker=request.ticker,
        cutoff=request.cutoff,
        artifacts={},
        artifact_hashes={},
        assessment=Assessment(status="needs_review"),
        usage=Usage(),
        stop_reason="coverage_diagnostic_completed",
    )


def test_build_plan_is_the_matched_two_legacy_one_packed_comparison(tmp_path):
    request, plan = request_and_plan(tmp_path)

    assert diagnostic.validate_plan(plan) == request
    assert [call["policy"] for call in plan["calls"]] == ["legacy-12", "legacy-12", "packed-24"]
    assert [len(call["issues"]) for call in plan["calls"]] == [12, 6, 18]
    assert [
        [issue["issue_id"] for issue in call["issues"]]
        for call in plan["calls"]
    ] == [
        [f"issue-{index}" for index in range(12)],
        [f"issue-{index}" for index in range(12, 18)],
        [f"issue-{index}" for index in range(18)],
    ]
    assert all(call["payload"]["research"]["limitation_review"] == call["issues"]
               for call in plan["calls"])


@pytest.mark.parametrize("mutation", ["payload", "envelope", "limit", "order", "reader"])
def test_validate_plan_rejects_tampering(tmp_path, mutation):
    _request, plan = request_and_plan(tmp_path)
    plan = deepcopy(plan)
    if mutation == "payload":
        plan["calls"][0]["payload"]["mandate"] = "tampered"
    elif mutation == "envelope":
        plan["calls"][0]["output_envelope"] += 1
    elif mutation == "limit":
        plan["worker_seconds"] -= 1
    elif mutation == "order":
        plan["calls"][0], plan["calls"][1] = plan["calls"][1], plan["calls"][0]
    else:
        plan["calls"][-1]["payload"]["research"]["rendered_reader"] += " tampered"

    with pytest.raises(ValueError):
        diagnostic.validate_plan(plan)


def test_execute_plan_collects_three_checked_calls_and_closes_service(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService()

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_completed"
    assert result.usage.complete
    assert [role for role, _payload, _request in service.calls] == ["verifier"] * 3
    assert service.closed == 1
    saved = trace(request)
    assert saved["status"] == "completed" and not saved["pending_dispatch"]
    assert [call["status"] for call in saved["calls"]] == ["reviewed"] * 3
    assert all(call["valid_disposition_ids"] for call in saved["calls"])


def test_second_provider_failure_blocks_the_third_call_and_marks_usage_incomplete(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService([None, RuntimeError("synthetic provider failure")])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed" and not result.usage.complete
    assert len(service.calls) == 2 and service.closed == 1
    saved = trace(request)
    assert saved["pending_dispatch"] and saved["calls"][-1]["status"] == "failed"


def test_cleanup_failure_cannot_overwrite_the_primary_failure(tmp_path):
    class BadCleanup(FixtureService):
        def close(self):
            raise RuntimeError("Synthetic cleanup failure, never echo raw text")

    request, plan = request_and_plan(tmp_path)
    service = BadCleanup([ValueError("Synthetic primary failure")])
    result = diagnostic.execute_plan(plan, request, service, clock=Clock())
    saved = trace(request)
    assert result.stop_reason == "coverage_diagnostic_failed" and not result.usage.complete
    assert saved["failure_type"] == "ValueError"
    assert saved["cleanup_failure_reason"] == "cleanup_failed"
    assert len(service.calls) == 1


def test_malformed_second_reply_preserves_known_usage_and_blocks_the_third_call(tmp_path):
    request, plan = request_and_plan(tmp_path)
    malformed = ModelReply(data={
        "reviewed_report": True,
        "limitation_dispositions": [{
            "issue_id": "issue-0", "decision": "not-a-decision", "rationale": "Malformed fixture.",
        }],
    }, usage=Usage(input_tokens=31, output_tokens=0))
    service = FixtureService([None, malformed])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed" and result.usage.complete
    assert result.usage.total_tokens == 55
    assert len(service.calls) == 2
    saved = trace(request)
    assert not saved["pending_dispatch"] and saved["calls"][-1]["status"] == "failed"
    assert saved["usage"]["input_tokens"] == 55


def test_incomplete_usage_stops_after_the_known_first_reply(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService([lambda payload: valid_reply(payload, complete=False)])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed" and not result.usage.complete
    assert len(service.calls) == 1
    assert trace(request)["calls"][-1]["status"] == "failed"


def test_known_budget_overshoot_prevents_a_followup_dispatch(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService([lambda payload: valid_reply(payload, tokens=diagnostic.TOKEN_CAP + 1)])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_failed" and result.usage.complete
    assert len(service.calls) == 1
    assert trace(request)["calls"][-1]["status"] == "failed"


def test_execute_plan_requires_a_fresh_output_directory_and_never_reruns(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService()

    diagnostic.execute_plan(plan, request, service, clock=Clock())
    with pytest.raises(FileExistsError):
        diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert len(service.calls) == 3


def test_remaining_wall_time_shrinks_subsequent_provider_timeout(tmp_path):
    request, plan = request_and_plan(tmp_path)
    clock = Clock()
    service = FixtureService(clock=clock, advances=[400, 1, 1])

    result = diagnostic.execute_plan(plan, request, service, clock=clock)

    assert result.stop_reason == "coverage_diagnostic_completed"
    assert [payload["timeout_seconds"] for _, payload, _ in service.calls] == [600, 480, 479]


def test_invalid_reader_excerpt_is_recorded_as_unaccepted_after_all_three_calls(tmp_path):
    request, plan = request_and_plan(tmp_path)
    service = FixtureService([None, None, lambda payload: valid_reply(payload, excerpt="not in reader")])

    result = diagnostic.execute_plan(plan, request, service, clock=Clock())

    assert result.stop_reason == "coverage_diagnostic_completed"
    assert len(service.calls) == 3
    checked = trace(request)["calls"][-1]["checked_review"]
    assert trace(request)["calls"][-1]["valid_disposition_ids"] == []
    assert any(finding["code"] == "limitation_disposition" for finding in checked["findings"])


def test_cli_run_is_capsule_bound_single_attempt_without_starting_models(tmp_path, monkeypatch):
    capsule, path, request, plan = cli_plan(tmp_path)
    calls = []

    def fake_supervisor(worker, called_request, *, timeout_seconds):
        calls.append((worker, called_request, timeout_seconds))
        assert not called_request.output_dir.exists()
        return SimpleNamespace(status="completed", code="completed", result=cli_result(called_request))

    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(diagnostic, "run_supervised", fake_supervisor)
    args = [
        "run", "--plan", str(path), "--approved-plan-sha256", digest(plan),
        "--codex-home", str(tmp_path / "home"), "--allow-live", "--allow-advisory-token-cap",
    ]

    assert diagnostic.main(args) == 0
    assert len(calls) == 1
    worker, called_request, timeout_seconds = calls[0]
    assert isinstance(worker, diagnostic.CoverageDiagnosticWorker)
    assert worker.home == Path(plan["codex_home"])
    assert plan["model_provider"] == "openai"
    assert called_request.output_dir == path.parent / "run_1"
    assert timeout_seconds == diagnostic.SUPERVISOR_SECONDS
    assert (capsule / "attempt_started").is_dir()
    assert not request.output_dir.exists()

    with pytest.raises(FileExistsError):
        diagnostic.main(args)
    assert len(calls) == 1


@pytest.mark.parametrize("arguments", [
    lambda path, plan, home: ["run", "--plan", str(path), "--approved-plan-sha256", digest(plan),
                              "--codex-home", str(home)],
    lambda path, _plan, home: ["run", "--plan", str(path), "--approved-plan-sha256", "not-the-plan",
                               "--codex-home", str(home), "--allow-live"],
])
def test_cli_rejects_missing_live_permission_or_hash_before_attempt(tmp_path, monkeypatch, arguments):
    capsule, path, _request, plan = cli_plan(tmp_path)
    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(diagnostic, "run_supervised", lambda *_args, **_kwargs:
                        pytest.fail("rejected plan reached supervision"))

    with pytest.raises(ValueError, match="exact plan approval"):
        diagnostic.main(arguments(path, plan, tmp_path / "home"))

    assert not (capsule / "attempt_started").exists()


def test_cli_rejects_manifest_with_output_outside_its_capsule_before_attempt(tmp_path, monkeypatch):
    capsule, path, _request, plan = cli_plan(tmp_path, output_dir=tmp_path / "outside" / "run_1")
    monkeypatch.setattr(diagnostic, "verify_runtime", lambda _plan: None)
    monkeypatch.setattr(diagnostic, "run_supervised", lambda *_args, **_kwargs:
                        pytest.fail("outside manifest reached supervision"))

    with pytest.raises(ValueError, match="capsule-bound"):
        diagnostic.main([
            "run", "--plan", str(path), "--approved-plan-sha256", digest(plan),
            "--codex-home", str(tmp_path / "home"), "--allow-live", "--allow-advisory-token-cap",
        ])

    assert not (capsule / "attempt_started").exists()


def test_cli_requires_advisory_token_acknowledgement_before_attempt(tmp_path, monkeypatch):
    capsule, path, _request, plan = cli_plan(tmp_path)
    monkeypatch.setattr(diagnostic, "run_supervised", lambda *_args, **_kwargs:
                        pytest.fail("unacknowledged token semantics reached supervision"))
    with pytest.raises(ValueError, match="best-effort"):
        diagnostic.main(["run", "--plan", str(path), "--approved-plan-sha256", digest(plan),
                         "--codex-home", str(tmp_path / "home"), "--allow-live"])
    assert not (capsule / "attempt_started").exists()
