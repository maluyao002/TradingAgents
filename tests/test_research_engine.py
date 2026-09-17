import json
from copy import deepcopy
from dataclasses import asdict, fields
from hashlib import sha256

import pytest

from tests.test_research_valuation import _model
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest, SourceDocument
from tradingagents.research.dossiers import DossierStore
from tradingagents.research.engine import _calculate, run_research
from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import read_json


def replies():
    analysis = {"summary": "Synthetic test analysis, not an investment conclusion."}
    planner = {**analysis, "questions": [
        {"id": f"q{i}", "question": f"Synthetic question {i}", "consequence": "Cash flow",
         "resolvability": "high"} for i in range(3)]}

    def wrap(data):
        return {"data": data, "usage": {"input_tokens": 100, "output_tokens": 30}}

    return {"planner": [wrap(planner)], "business": [wrap(analysis)],
            "accounting": [wrap(analysis)], "management": [wrap(analysis)],
            "expectations": [wrap(analysis)], "challenger": [wrap(analysis), wrap(analysis)],
            "valuation": [wrap({"unsupported_inputs": ["Synthetic fixture has no financials"]})],
            "verifier": [wrap({}), wrap({"reviewed_report": True})],
            "editor": [wrap({"sections": [{"title": "研究结论", "text": "测试研究，证据不足。"}],
                             "limitations": ["No financial data"], "investment_view": "unrated"})]}


def setup(tmp_path, response_data=None):
    request = ResearchRequest(ticker="NVDA", cutoff="2026-09-17T00:00:00Z", backend="api",
                              output_dir=tmp_path / "out")
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff=request.cutoff)
    model = ReplayModelService(response_data or replies())
    return request, ResearchServices(SnapshotEvidenceService(snapshot), model)


def test_standalone_orchestration_blinds_challenger_and_exports_review_required(tmp_path):
    request, services = setup(tmp_path)
    result = run_research(request, services)
    assert result.assessment.status == "needs_review"
    assert result.assessment.investment_view == "unrated"
    assert result.stop_reason == "completed_needs_review"
    assert result.usage.total_tokens == 11 * 130
    assert len(result.artifacts) == 8
    challenge = [payload for role, payload in services.models.calls if role == "challenger"]
    assert set(challenge[0]["research"]) == {"questions"}
    assert "valuation" in challenge[1]["research"]
    assert "未评级" in (request.output_dir / "reader_report.md").read_text()
    assert not read_json(request.output_dir / "run_metadata.json")["production_accepted"]
    stages = read_json(request.output_dir / "run_metadata.json")["usage_by_stage"]
    assert len(stages) == 11
    assert stages["business"][0]["input_tokens"] == 100


def test_completed_replay_resumes_without_new_model_calls(tmp_path):
    request, services = setup(tmp_path)
    first = run_research(request, services)
    service2 = ReplayModelService(replies())
    second = run_research(request, ResearchServices(services.evidence, service2))
    assert not service2.calls
    assert second.usage == first.usage
    assert second.artifact_hashes["reader_report.md"] == first.artifact_hashes["reader_report.md"]


def test_invalid_model_claim_fails_closed_and_preserves_valid_stages(tmp_path):
    data = replies()
    data["business"][0]["data"]["claims"] = [{"id": "c", "text": "Unfounded", "kind": "reported",
                                               "source_ids": ["invented"]}]
    request, services = setup(tmp_path, data)
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert result.assessment.investment_view == "unrated"
    assert (request.output_dir / "stages/planner.json").exists()
    assert not (request.output_dir / "stages/business.json").exists()


def test_changed_responses_cannot_reuse_checkpoint(tmp_path):
    request, services = setup(tmp_path)
    run_research(request, services)
    changed = deepcopy(replies())
    changed["business"][0]["data"]["summary"] = "Changed research"
    with pytest.raises(ValueError, match="incompatible"):
        run_research(request, ResearchServices(services.evidence, ReplayModelService(changed)))


def test_critical_final_review_withholds_reader_draft(tmp_path):
    data = replies()
    data["verifier"][1]["data"].update(findings=[{
        "code": "unsupported", "severity": "critical", "message": "Unsupported claim"}])
    request, services = setup(tmp_path, data)
    result = run_research(request, services)
    assert "研究结论" not in (request.output_dir / "reader_report.md").read_text()
    assert any("withheld" in gap for gap in result.unresolved_gaps)


def test_editor_invented_reference_preserves_diagnostics(tmp_path):
    data = replies()
    data["editor"][0]["data"]["sections"][0]["evidence_ids"] = ["invented"]
    request, services = setup(tmp_path, data)
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert (request.output_dir / "result.json").exists()


def test_provider_failure_exports_incomplete_usage_and_blocks_retry(tmp_path):
    request, services = setup(tmp_path)

    class Broken:
        def complete(self, *args):
            raise RuntimeError("secret provider exception")

    services = ResearchServices(services.evidence, Broken())
    result = run_research(request, services)
    assert not result.usage.complete
    assert result.stop_reason == "stage_failed"
    assert "secret provider" not in (request.output_dir / "reader_report.md").read_text()
    assert run_research(request, services).stop_reason == "usage_incomplete"


def test_completed_artifacts_are_immutable_and_tampering_rejected(tmp_path):
    request, services = setup(tmp_path)
    first = run_research(request, services)
    second = run_research(request, services)
    assert second == first
    (request.output_dir / "reader_report.md").write_text("tampered")
    with pytest.raises(ValueError, match="artifact mismatch"):
        run_research(request, services)


def test_preview_dossier_is_persisted_without_promotion(tmp_path):
    request, services = setup(tmp_path)
    request = request.model_copy(update={"dossier_dir": tmp_path / "dossiers"})
    result = run_research(request, services)
    assert "dossier.json" in result.artifacts
    assert DossierStore(request.dossier_dir).load_current_accepted("NVDA") is None
    dossier = read_json(request.output_dir / "dossier.json")
    assert dossier["coverage_watermark"] is None
    assert run_research(request, services) == result


def test_interrupted_provider_call_never_becomes_a_free_retry(tmp_path):
    request, services = setup(tmp_path)

    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt

    services = ResearchServices(services.evidence, Interrupted())
    with pytest.raises(KeyboardInterrupt):
        run_research(request, services)
    result = run_research(request, services)
    assert result.stop_reason == "usage_incomplete"
    assert not result.usage.complete


@pytest.mark.parametrize("change", [
    {"ticker": "OTHER"}, {"artifacts": {}, "artifact_hashes": {}},
    {"unresolved_gaps": []}])
def test_result_manifest_cannot_be_weakened_on_resume(tmp_path, change):
    request, services = setup(tmp_path)
    run_research(request, services)
    path = request.output_dir / "result.json"
    path.write_text(json.dumps({**read_json(path), **change}))
    with pytest.raises(ValueError, match="manifest mismatch"):
        run_research(request, services)


@pytest.mark.parametrize("sections", [[], [{"title": "Summary", "text": "   "}]])
def test_empty_reader_output_is_a_stage_failure(tmp_path, sections):
    data = replies()
    data["editor"][0]["data"]["sections"] = sections
    request, services = setup(tmp_path, data)
    assert run_research(request, services).stop_reason == "stage_failed"


def followup_setup(tmp_path):
    data = replies()
    data["business"][0]["data"]["followup_questions"] = ["Seek contradictory customer evidence"]
    revised = deepcopy(data["planner"][0])
    for question in revised["data"]["questions"]:
        question["id"] += "-revised"
    data["planner"].append(revised)
    for role in ("business", "accounting", "expectations", "management"):
        data[role].append({"data": {"summary": "Thesis revised after contrary evidence"},
                           "usage": {"input_tokens": 100, "output_tokens": 30}})
    data["valuation"].append(deepcopy(data["valuation"][0]))
    request, services = setup(tmp_path, data)
    source = SourceDocument(id="original", url="https://example.com/original", title="Original",
                            publisher="Synthetic", published_at="2026-09-15T00:00:00Z",
                            retrieved_at="2026-09-15T01:00:00Z", content="Initial evidence",
                            content_sha256=sha256(b"Initial evidence").hexdigest())
    snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff, sources=(source,))
    addition = source.model_copy(update={"id": "contrary", "content": "Contrary customer evidence",
        "content_sha256": sha256(b"Contrary customer evidence").hexdigest()})

    class Followup(SnapshotEvidenceService):
        count = 0
        updated = snapshot.model_copy(update={"sources": (source, addition)})

        def followup(self, *args):
            self.count += 1
            return self.updated

    evidence = Followup(snapshot)
    return request, ResearchServices(evidence, services.models)


def test_followup_can_change_thesis_and_questions_then_stop(tmp_path):
    request, services = followup_setup(tmp_path)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    assert services.evidence.count == 1
    calls = [payload for role, payload in services.models.calls if role == "business"]
    assert calls[1]["research"]["questions"][0]["id"] == "q0-revised"
    assert read_json(request.output_dir / "research.json")["business"]["summary"].startswith("Thesis revised")
    assert run_research(request, services) == result
    assert services.evidence.count == 1


@pytest.mark.parametrize("violation", ["remove", "future"])
def test_invalid_followup_is_never_checkpointed(tmp_path, violation):
    request, services = followup_setup(tmp_path)
    snapshot = services.evidence.updated
    if violation == "remove":
        updated = snapshot.model_copy(update={"sources": snapshot.sources[1:]})
    else:
        future = snapshot.sources[1].model_copy(update={"published_at": request.cutoff.replace(year=2027)})
        updated = snapshot.model_copy(update={"sources": (snapshot.sources[0], future)})
    services.evidence.updated = updated
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert not (request.output_dir / "stages/followup-0.json").exists()


def test_any_nonempty_rationale_is_not_sufficient_for_valuation(tmp_path):
    request = ResearchRequest(ticker="TEST", cutoff="2024-12-31T12:00:00Z", backend="api",
                              output_dir=tmp_path)
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff)
    proposal = ValuationProposal(model=asdict(_model()), assumption_rationale={"anything": "Trust me"})
    result = _calculate(proposal, request, snapshot)
    assert result["status"] == "unavailable"


def test_supported_base_inputs_and_all_assumptions_are_required(tmp_path):
    request = ResearchRequest(ticker="TEST", cutoff="2024-12-31T12:00:00Z", backend="api",
                              output_dir=tmp_path)
    source = SourceDocument(id="filing", url="https://example.com/filing", title="Synthetic",
                            publisher="Synthetic", content="Financial evidence",
                            content_sha256=sha256(b"Financial evidence").hexdigest(),
                            published_at=request.cutoff, retrieved_at=request.cutoff)
    opening = {"current_revenue": ("revenue", 100), "current_working_capital": ("working_capital", 10),
               "net_debt": ("net_debt", 20), "current_diluted_shares": ("diluted_shares", 10)}
    facts = [{"id": name, "source_id": "filing", "metric": metric, "value": value,
              "unit": "shares" if "shares" in name else "USD",
              "currency": None if "shares" in name else "USD", "period_end": "2024-12-31",
              "basis": "US GAAP", "location": "fixture"} for name, (metric, value) in opening.items()]
    facts[0].update(period_start="2024-01-01", period_type="duration")
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(source,), facts=facts)
    model = _model()
    required = {*opening, "discount_rate", "terminal_growth", "units.currency",
                "units.amount_scale", "units.share_scale"}
    for i, period in enumerate(model.periods):
        required.update(f"periods.{i}.{field.name}" for field in fields(period)
                        if field.name not in {"label", "discount_years"})
    assumptions = {name: {"kind": "reported" if name in opening else "assumption",
                           "rationale": "Synthetic scenario for tests only",
                           "evidence_ids": [name if name in opening else "filing"]} for name in required}
    proposal = ValuationProposal(model=asdict(model), evidence_ids=("filing",), assumptions=assumptions,
                                 accounting_basis="US GAAP")
    assert _calculate(proposal, request, snapshot)["status"] == "illustrative"
    changed = proposal.model_copy(update={"model": {**proposal.model, "net_debt": "200"}})
    assert _calculate(changed, request, snapshot)["status"] == "unavailable"
    stale = snapshot.model_copy(update={"facts": tuple(
        fact.model_copy(update={"period_end": fact.period_end.replace(year=2010)}) for fact in snapshot.facts)})
    assert _calculate(proposal, request, stale)["status"] == "unavailable"


def test_interruption_after_provider_reply_keeps_unsettled_dispatch(tmp_path, monkeypatch):
    from tradingagents.research.budget import BudgetTracker

    request, services = setup(tmp_path)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(BudgetTracker, "complete", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_research(request, services)
    monkeypatch.undo()
    replay = ReplayModelService(replies())
    result = run_research(request, ResearchServices(services.evidence, replay))
    assert result.stop_reason == "usage_incomplete"
    assert not replay.calls


def test_valuation_caveats_cannot_be_omitted_by_editor(tmp_path):
    request, services = setup(tmp_path)
    run_research(request, services)
    assert "Synthetic fixture has no financials" in (request.output_dir / "reader_report.md").read_text()


def test_live_calls_are_withheld_when_no_eligible_source_text(tmp_path):
    request, services = setup(tmp_path)
    services.models.kind = "codex"
    result = run_research(request, services)
    assert result.stop_reason == "stage_failed"
    assert not services.models.calls
    assert any("live model calls withheld" in gap for gap in result.unresolved_gaps)
