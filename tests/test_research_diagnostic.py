import json

import pytest

from scripts import research_diagnostic as diagnostic
from scripts.research_diagnostic import summarize_event
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest, Usage
from tradingagents.research.services import ModelReply
from tradingagents.research.storage import digest, request_identity
from tradingagents.research.wire import WIRE_SCHEMA_VERSION


def test_diagnostic_never_copies_upstream_prose():
    result = summarize_event({"method": "error", "params": {"error": {
        "codexErrorInfo": "badRequest", "message": "Invalid schema: required; token-SECRET"}}})
    assert result["error_code"] == "badRequest"
    assert result["error_signatures"] == ["invalid schema", "required"]
    assert "SECRET" not in str(result)


def test_diagnostic_drops_prompt_and_output_fields():
    assert summarize_event({"method": "item/agentMessage/delta", "params": {"delta": "SECRET"}}) == {
        "method": "item/agentMessage/delta"}


@pytest.mark.parametrize("invalid,cleanup_error", [(False, False), (True, False), (False, True),
                                                  ("evidence", False), ("questions", False),
                                                  ("question_link", False), ("duplicate_claim", False)])
def test_one_inference_and_known_usage_survive_validation_or_cleanup_failure(tmp_path, monkeypatch,
                                                                          invalid, cleanup_error):
    calls = []

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if cleanup_error:
                raise diagnostic.CodexAdapterError("Synthetic cleanup failure")

        def complete(self, role, payload, request):
            calls.append(role)
            data = {"summary": "Synthetic planner response", "questions": [
                {"id": f"q{i}", "question": "Question", "consequence": "Cash flow",
                 "resolvability": "high"} for i in range(3)]}
            if invalid is True:
                data = {}
            elif invalid == "evidence":
                data["claims"] = [{"id": "c1", "text": "Claim", "kind": "reported",
                                   "source_ids": ["INVENTED_SECRET_ID"]}]
            elif invalid == "questions":
                data["questions"] = []
            elif invalid == "question_link":
                data["findings"] = [{"id": "f1", "question_id": "unknown", "conclusion": "Finding",
                    "economic_consequence": "Cash flow", "uncertainty": "Missing evidence",
                    "invalidation": "New data"}]
            elif invalid == "duplicate_claim":
                data["claims"] = [{"id": "c1", "text": "Claim", "kind": "reported"}] * 2
            return ModelReply(data=data, usage=Usage(input_tokens=12, output_tokens=3))

    monkeypatch.setattr(diagnostic, "CodexModelService", FakeService)
    monkeypatch.setattr(diagnostic, "load_snapshot", lambda path, request:
                        EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff))
    monkeypatch.setattr(diagnostic, "_prompt_evidence", lambda *args: {})
    request = ResearchRequest(ticker="TEST", cutoff="2026-09-17T00:00:00Z", backend="codex",
                              output_dir=tmp_path / "diagnostic")
    result = diagnostic.DiagnosticWorker(tmp_path / "runtime")(request)
    trace = json.loads((request.output_dir / "diagnostic.json").read_text())
    assert calls == ["planner"]
    assert result.usage.total_tokens == 15
    assert trace["usage"]["input_tokens"] == 12
    assert trace["status"] == ("failed" if invalid or cleanup_error else "succeeded")
    assert "Synthetic planner response" not in json.dumps(trace)
    assert "INVENTED_SECRET_ID" not in json.dumps(trace)
    assert (request.output_dir / "planner_reply.json").exists() == (not invalid and not cleanup_error)


@pytest.mark.parametrize("complete", [True, False])
def test_valuation_diagnostic_is_one_call_and_binds_reply_to_payload(tmp_path, monkeypatch, complete):
    calls = []

    class Service:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def complete(self, role, payload, request):
            calls.append((role, payload))
            return ModelReply(data={"model": None, "unsupported_inputs": ["No calibrated model"]},
                              usage=Usage(input_tokens=20, output_tokens=4, complete=complete))

    request = ResearchRequest(ticker="TEST", cutoff="2026-09-17T00:00:00Z", backend="codex",
                              output_dir=tmp_path / "diagnostic")
    monkeypatch.setattr(diagnostic, "CodexModelService", Service)
    monkeypatch.setattr(diagnostic, "load_snapshot", lambda *a:
                        EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff))
    monkeypatch.setattr(diagnostic, "load_valuation_context", lambda *a: ({}, {"research.json": "hash"}))
    result = diagnostic.DiagnosticWorker(tmp_path / "home", "valuation", tmp_path / "source")(request)
    assert [role for role, _ in calls] == ["valuation"]
    assert result.usage.total_tokens == 24
    assert result.stop_reason == ("diagnostic_succeeded" if complete else "diagnostic_failed")
    if complete:
        provenance = json.loads((request.output_dir / "provenance.json").read_text())
        payload = {k: v for k, v in calls[0][1].items()
                   if k not in {"timeout_seconds", "max_output_tokens"}}
        assert provenance["payload_hash"] == digest(payload)
        assert provenance["request_identity"] == request_identity(request)
        assert provenance["wire_schema_version"] == WIRE_SCHEMA_VERSION
        assert "financial_model_schema" in payload
    else:
        assert not (request.output_dir / "valuation_reply.json").exists()


def test_valuation_source_failure_prevents_provider_start(tmp_path, monkeypatch):
    request = ResearchRequest(ticker="TEST", cutoff="2026-09-17T00:00:00Z", backend="codex",
                              output_dir=tmp_path / "diagnostic")
    monkeypatch.setattr(diagnostic, "load_snapshot", lambda *a:
                        EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff))
    monkeypatch.setattr(diagnostic, "CodexModelService", lambda *a, **k:
                        pytest.fail("provider must not start"))
    result = diagnostic.DiagnosticWorker(tmp_path / "home", "valuation", tmp_path / "missing")(request)
    assert result.stop_reason == "diagnostic_failed"


@pytest.mark.parametrize("mutation", [None, "artifact", "stage", "request", "symlink"])
def test_valuation_context_verifies_real_checkpoint_prefix(tmp_path, mutation):
    from hashlib import sha256

    from tests.test_research_engine import replies
    from tradingagents.codex.adapter import CodexStructuredOutputError
    from tradingagents.research.contracts import SourceDocument
    from tradingagents.research.engine import run_research
    from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
    from tradingagents.research.services import ResearchServices

    home = tmp_path / "runtime"
    request = ResearchRequest(ticker="TEST", cutoff="2026-09-17T00:00:00Z", backend="codex",
                              output_dir=tmp_path / "source")

    class FailedValuation(ReplayModelService):
        kind = "codex"

        def complete(self, role, payload, request):
            if role == "valuation":
                raise CodexStructuredOutputError("Synthetic schema rejection")
            return super().complete(role, payload, request)

    models = FailedValuation(replies())
    models.identity = digest({"service": "isolated-codex-v1", "wire": "research-wire-v1",
                              "home": str(home.resolve())})
    source = SourceDocument(id="filing", url="https://example.com/filing", title="Synthetic",
        publisher="Fixture", retrieved_at=request.cutoff, published_at=request.cutoff,
        content="Synthetic evidence", content_sha256=sha256(b"Synthetic evidence").hexdigest())
    snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff, sources=(source,))
    run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    original = {str(p.relative_to(request.output_dir)): p.read_bytes()
                for p in request.output_dir.rglob("*") if p.is_file()}
    if mutation == "artifact":
        (request.output_dir / "reader_report.md").write_text("tampered")
    elif mutation == "stage":
        path = request.output_dir / "stages/planner.json"
        data = json.loads(path.read_text())
        data["output"]["summary"] = "tampered"
        path.write_text(json.dumps(data))
    elif mutation == "request":
        request = request.model_copy(update={"mandate": "Changed scope"})
    elif mutation == "symlink":
        path = request.output_dir / "research.json"
        copy = tmp_path / "linked.json"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    if mutation:
        with pytest.raises(ValueError):
            diagnostic.load_valuation_context(request.output_dir, request, home)
    else:
        research, hashes = diagnostic.load_valuation_context(request.output_dir, request, home)
        assert len(research) == 6
        assert "stages/resources.json" in hashes
        assert original == {str(p.relative_to(request.output_dir)): p.read_bytes()
                            for p in request.output_dir.rglob("*") if p.is_file()}
