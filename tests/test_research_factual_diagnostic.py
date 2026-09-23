"""Offline checks for the one-call exact-reader factual diagnostic."""

from copy import deepcopy
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pytest

from scripts import research_factual_diagnostic as diagnostic
from scripts.research_writer_capture import capture_payload
from tests.test_research_case_engine import case_setup
from tradingagents.codex.adapter import CodexInferenceError
from tradingagents.research.engine import run_research
from tradingagents.research.storage import atomic_write, canonical_json, digest, read_json


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("factual-diagnostic").resolve()
    original, services = case_setup(directory / "synthetic-source")
    run_research(original, services)
    source = original.output_dir
    request = original.model_copy(update={
        "backend": "codex", "budget": diagnostic.diagnostic_budget(),
        "models": {**original.models, "verifier": original.models["verifier"].model_copy(
            update={"model": "gpt-6-sol", "effort": "xhigh"})},
    })
    editor = read_json(source / "stages/editor.json")["output"]
    capture_payload(source, request, directory / "calibration", editor)
    calibration = read_json(directory / "calibration/offline_capture.json")
    for row in calibration["fixture_bindings"]:
        path = source / f"stages/{row['stage']}.json"
        record = read_json(path)
        record["inputs_hash"] = row["current_inputs_sha256"]
        atomic_write(path, canonical_json(record))
    home = directory / "isolated-home"
    home.mkdir()
    revision = diagnostic.subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip()
    plan = diagnostic.prepare_capsule(source, request, directory / "capsule",
                                      revision, home)
    return directory / "capsule", plan, home


def clone(prepared, tmp_path):
    source_capsule, original, home = prepared
    capsule = tmp_path.resolve() / "capsule"
    copytree(source_capsule, capsule)
    plan = deepcopy(original)
    plan["request"]["output_dir"] = str(capsule / "run_1")
    plan["request_sha256"] = digest(plan["request"])
    return capsule, plan, home


class FakeService:
    def __init__(self, answer=None):
        self.answer = answer or {"data": {"reviewed_report": True},
                                 "usage": {"input_tokens": 10, "output_tokens": 5}}
        self.calls = []
        self.closed = False

    def complete(self, role, payload, request):
        self.calls.append((role, payload, request))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def close(self):
        self.closed = True


def test_preparation_reconstructs_exact_reader_and_all_prefixes(prepared):
    capsule, plan, home = prepared
    request, payload = diagnostic.validate_plan(plan, capsule, home=home)
    capture = read_json(capsule / "capture/offline_capture.json")
    assert payload["stage"] == "verify_report"
    assert payload["research"]["rendered_reader_sha256"] == plan["reader_sha256"]
    assert capture["live_calls"] == 0
    assert len(plan["fixture_bindings"]) == 8
    assert all(row["matches_current_payload"] for row in plan["fixture_bindings"])
    assert plan["call"]["input_bytes"] > 0
    assert plan["call"]["reserve_tokens"] < diagnostic.TOKEN_CAP
    assert (request.models["verifier"].model, request.models["verifier"].effort) == (
        "gpt-6-sol", "xhigh")
    assert not (capsule / "run_1").exists()


def test_plan_drift_prefix_reuse_and_changed_reader_stop_before_dispatch(prepared, tmp_path, monkeypatch):
    capsule, plan, home = clone(prepared, tmp_path)
    altered = deepcopy(plan)
    altered["fixture_bindings"][0]["matches_current_payload"] = False
    altered["fixture_bindings_sha256"] = digest(altered["fixture_bindings"])
    with pytest.raises(ValueError, match="prefix inputs"):
        diagnostic.validate_plan(altered, capsule, home=home)

    altered = deepcopy(plan)
    altered["source_artifact_sha256"]["stages/editor.json"] = "0" * 64
    with pytest.raises(ValueError, match="source changed"):
        diagnostic.validate_plan(altered, capsule, home=home)

    altered = deepcopy(plan)
    altered["reader_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="reader binding"):
        diagnostic.validate_plan(altered, capsule, home=home)

    altered = deepcopy(plan)
    altered["call"]["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="captured payload"):
        diagnostic.validate_plan(altered, capsule, home=home)

    monkeypatch.setattr(diagnostic, "runtime_binding", lambda: {})
    with pytest.raises(ValueError, match="implementation changed"):
        diagnostic.verify_runtime(plan)


def test_success_saves_one_fresh_structured_review_without_acceptance(prepared, tmp_path):
    capsule, plan, home = clone(prepared, tmp_path)
    request, payload = diagnostic.validate_plan(plan, capsule, home=home)
    service = FakeService()
    result = diagnostic.execute_plan(plan, capsule, request, payload, service)
    trace = read_json(request.output_dir / "diagnostic.json")
    assert result.stop_reason == "factual_diagnostic_completed"
    assert len(service.calls) == 1 and service.closed
    assert service.calls[0][0] == "verifier"
    assert service.calls[0][1]["max_output_tokens"] == diagnostic.OUTPUT_ENVELOPE
    assert trace["fresh_live_calls"] == 1
    assert trace["structured_review"]["reviewed_report"] is True
    assert trace["request_sha256"] == plan["request_sha256"]
    assert trace["payload_sha256"] == plan["call"]["payload_sha256"]
    assert trace["usage_complete"] is True
    assert trace["report_exported"] is False and trace["acceptance"] is False
    assert not (request.output_dir / "reader_report.md").exists()
    assert not (request.output_dir / "reader_verification.json").exists()


@pytest.mark.parametrize("answer,expected_complete", [
    ({"data": {"reviewed_report": True}, "usage": {"complete": False}}, False),
    ({"data": {"reviewed_report": True}, "usage": {"input_tokens": 1_500_001}}, True),
])
def test_incomplete_usage_or_token_overshoot_fails_after_one_call(
    prepared, tmp_path, answer, expected_complete
):
    capsule, plan, home = clone(prepared, tmp_path)
    request, payload = diagnostic.validate_plan(plan, capsule, home=home)
    service = FakeService(answer)
    result = diagnostic.execute_plan(plan, capsule, request, payload, service)
    trace = read_json(request.output_dir / "diagnostic.json")
    assert result.stop_reason == "factual_diagnostic_failed"
    assert len(service.calls) == 1
    assert trace["usage_complete"] is expected_complete
    assert trace["fresh_live_calls"] == 1
    assert "structured_review" not in trace


def test_failure_diagnostic_is_allowlisted_and_private_error_is_not_written(prepared, tmp_path):
    capsule, plan, home = clone(prepared, tmp_path)
    request, payload = diagnostic.validate_plan(plan, capsule, home=home)
    service = FakeService(CodexInferenceError(
        "private prompt and provider response SECRET", reason="unknown",
        diagnostic={"kind": "request_size_limit", "phase": "turn_start",
                    "request_bytes": 1173651, "limit_bytes": 1000000,
                    "private": "SECRET"}))
    result = diagnostic.execute_plan(plan, capsule, request, payload, service)
    trace = read_json(request.output_dir / "diagnostic.json")
    assert result.stop_reason == "factual_diagnostic_failed"
    assert len(service.calls) == 1
    assert trace["usage_complete"] is False
    assert trace["failure_diagnostic"] == {
        "kind": "request_size_limit", "phase": "turn_start",
        "request_bytes": 1173651, "limit_bytes": 1000000}
    assert b"SECRET" not in (request.output_dir / "diagnostic.json").read_bytes()
    assert not (request.output_dir / "factual_reply.json").exists()


def test_run_requires_approval_and_cannot_reuse_capsule(prepared, tmp_path, monkeypatch):
    capsule, plan, home = clone(prepared, tmp_path)
    atomic_write(capsule / "plan.json", canonical_json(plan))
    called = []
    def supervise(*args, **kwargs):
        called.append(args)
        return SimpleNamespace(status="failed", code="fixture", result=None)
    monkeypatch.setattr(diagnostic, "run_supervised", supervise)
    base = ["run", "--plan", str(capsule / "plan.json"), "--codex-home", str(home),
            "--approved-plan-sha256", digest(plan)]
    with pytest.raises(ValueError, match="acknowledgements"):
        diagnostic.main(base)
    with pytest.raises(ValueError, match="approval"):
        diagnostic.main(base[:-1] + ["0" * 64, "--allow-live",
                              "--allow-advisory-token-cap",
                              "--acknowledge-unknown-historical-usage"])
    assert called == []
    approved = base + ["--allow-live", "--allow-advisory-token-cap",
                       "--acknowledge-unknown-historical-usage"]
    assert diagnostic.main(approved) == 1
    assert len(called) == 1
    assert (capsule / "attempt_started").is_dir()
    with pytest.raises(FileExistsError):
        diagnostic.main(approved)
    assert len(called) == 1
