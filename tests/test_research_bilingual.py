from collections import Counter
from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.engine import run_research
from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
from tradingagents.research.services import ResearchServices


def _wrap(data):
    return {"data": data, "usage": {"input_tokens": 100, "output_tokens": 30}}


def _responses():
    analysis = {"summary": "Synthetic test analysis, not an investment conclusion."}
    planner = {**analysis, "questions": [
        {"id": f"q{i}", "question": f"Synthetic question {i}", "consequence": "Cash flow",
         "resolvability": "high"} for i in range(3)]}
    section_ids = ["filing", "revenue"]
    return {
        "planner": [_wrap(planner)],
        "business": [_wrap(analysis)],
        "accounting": [_wrap(analysis)],
        "management": [_wrap(analysis)],
        "expectations": [_wrap(analysis)],
        "challenger": [_wrap(analysis), _wrap(analysis)],
        "valuation": [_wrap({"unsupported_inputs": ["Synthetic fixture has no valuation"]})],
        "verifier": [
            _wrap({}),
            _wrap({"reviewed_report": True}),
            _wrap({"reviewed_report": True}),
        ],
        "editor": [
            _wrap({"sections": [{"title": "Research conclusion",
                                   "text": "Revenue {{fact:revenue}}. [filing]",
                                   "evidence_ids": section_ids}],
                   "limitations": ["No company-specific valuation"],
                   "investment_view": "unrated"}),
            _wrap({"sections": [{"title": "研究结论",
                                   "text": "收入 {{fact:revenue}}。[filing]",
                                   "evidence_ids": section_ids}],
                   "limitations": ["无公司特定估值"], "investment_view": "unrated"}),
        ],
    }


def _setup(tmp_path, responses=None):
    request = ResearchRequest(
        ticker="NVDA",
        cutoff="2026-09-17T00:00:00Z",
        backend="api",
        output_dir=tmp_path / "out",
        report_language="English",
        additional_report_languages=("Chinese",),
    )
    source = SourceDocument(
        id="filing",
        url="https://example.test/filing",
        title="Synthetic filing",
        publisher="Synthetic",
        retrieved_at="2026-09-16T01:00:00Z",
        published_at="2026-09-16T00:00:00Z",
        content="Synthetic financial evidence",
        content_sha256=sha256(b"Synthetic financial evidence").hexdigest(),
    )
    fact = FinancialFact(
        id="revenue",
        source_id="filing",
        metric="revenue",
        value="1000000000",
        unit="USD",
        currency="USD",
        period_start="2025-01-01",
        period_end="2025-12-31",
        period_type="duration",
        basis="US GAAP",
        location="fixture",
    )
    snapshot = EvidenceSnapshot(
        ticker=request.ticker, cutoff=request.cutoff, sources=(source,), facts=(fact,))
    model = ReplayModelService(responses or _responses())
    return request, ResearchServices(SnapshotEvidenceService(snapshot), model)


def test_additional_report_languages_are_optional_unique_and_bounded(tmp_path):
    base = {"ticker": "NVDA", "cutoff": "2026-09-17T00:00:00Z", "backend": "api",
            "output_dir": tmp_path}
    assert ResearchRequest(**base).additional_report_languages == ()
    assert ResearchRequest(**base, additional_report_languages=("English",))
    with pytest.raises(ValidationError, match="primary report language cannot be repeated"):
        ResearchRequest(**base, additional_report_languages=("Chinese",))
    with pytest.raises(ValidationError, match="additional report languages must be unique"):
        ResearchRequest(**base, additional_report_languages=("English", "English"))


def test_bilingual_reports_share_research_render_facts_and_complete_budget(tmp_path):
    request, services = _setup(tmp_path)
    result = run_research(request, services)

    assert result.stop_reason == "completed_needs_review"
    assert result.usage.complete
    assert result.usage.total_tokens == 13 * 130
    assert Counter(role for role, _ in services.models.calls) == {
        "planner": 1, "challenger": 2, "business": 1, "accounting": 1,
        "expectations": 1, "management": 1, "valuation": 1, "verifier": 3,
        "editor": 2,
    }
    editors = [payload for role, payload in services.models.calls if role == "editor"]
    assert [payload["language"] for payload in editors] == ["English", "Chinese"]
    assert editors[1]["research"]["analyses"] == editors[0]["research"]["analyses"]
    assert editors[1]["research"]["valuation"] == editors[0]["research"]["valuation"]
    assert editors[1]["evidence"] == editors[0]["evidence"]
    assert "source_verified_draft" in editors[1]["research"]
    assert "{{fact:revenue}}" in editors[1]["research"][
        "source_draft_with_placeholders"]["sections"][0]["text"]

    primary = (request.output_dir / "reader_report.md").read_text()
    chinese = (request.output_dir / "reader_report_zh.md").read_text()
    english = (request.output_dir / "reader_report_en.md").read_text()
    assert primary == english
    assert "10亿 USD" in chinese
    assert "1 billion USD" in english
    assert "{{fact:" not in chinese + english
    assert "No company-specific valuation" in english
    assert "无公司特定估值" not in english
    assert "无公司特定估值" in chinese
    assert "No company-specific valuation" not in chinese
    assert {"reader_report.md", "reader_report_en.md", "reader_report_zh.md"} <= (
        result.artifacts.keys())


@pytest.mark.parametrize("artifact", ["reader_report_en.md", "reader_report_zh.md"])
def test_bilingual_completed_replay_validates_each_report_hash(tmp_path, artifact):
    request, services = _setup(tmp_path)
    first = run_research(request, services)
    replay = ReplayModelService(_responses())
    second = run_research(request, ResearchServices(services.evidence, replay))
    assert not replay.calls
    assert second == first
    assert second.artifact_hashes[artifact] == first.artifact_hashes[artifact]

    (request.output_dir / artifact).write_text("tampered")
    with pytest.raises(ValueError, match="artifact mismatch"):
        run_research(request, ResearchServices(services.evidence, ReplayModelService(_responses())))


def test_failed_additional_verifier_withholds_only_that_language(tmp_path):
    responses = deepcopy(_responses())
    responses["verifier"][2]["data"] = {
        "reviewed_report": True,
        "findings": [{"code": "translation_drift", "severity": "critical",
                      "message": "Translated number changed", "category": "editorial"}],
    }
    request, services = _setup(tmp_path, responses)
    result = run_research(request, services)

    assert result.stop_reason == "verification_failed"
    assert result.usage.complete
    assert (request.output_dir / "reader_report.md").exists()
    assert (request.output_dir / "reader_report_en.md").exists()
    assert not (request.output_dir / "reader_report_zh.md").exists()
    assert "Research conclusion" in (request.output_dir / "reader_report.md").read_text()
    assert any("Chinese reader draft withheld" in gap for gap in result.unresolved_gaps)
