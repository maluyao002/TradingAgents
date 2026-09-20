"""Case-to-reader integration of independently reviewed operating scenarios."""

from datetime import date
from hashlib import sha256

import pytest

from tests.test_research_case_engine import CaseFixture, case_setup
from tests.test_research_financial_case import _case, _snapshot
from tradingagents.research.contracts import FinancialFact
from tradingagents.research.engine import run_research
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    operating_scenario_package_sha256,
)
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, digest, read_json


def operating_setup(tmp_path, *, reviewed=True):
    request, _ = case_setup(tmp_path)
    snapshot = _snapshot()
    anchor_facts = tuple(FinancialFact(
        id=f"anchor-{metric}", metric=metric, value=value, scale=1, source_id="filing",
        unit="USD", currency="USD", basis="US GAAP", period_start="2026-01-01",
        period_end="2026-06-30", period_type="duration", location="Synthetic H1 anchor",
    ) for metric, value in (("revenue", "1000000000"), ("operating_income", "500000000")))
    historical = tuple(f.model_copy(update={"period_end": date(2026, 6, 30)}) for f in snapshot.facts)
    snapshot = snapshot.model_copy(update={"facts": (*historical, *anchor_facts)})
    case = _case(snapshot).model_copy(update={"opening_date": date(2026, 6, 30)})
    source = snapshot.sources[0]

    def assumption(value):
        return {"value": value, "classification": "analyst_assumption",
                "evidence_ids": ["source-slice"], "rationale": "Synthetic conditional assumption."}

    package = OperatingScenarioPackage.model_validate({
        "ticker": "NVDA", "cutoff": snapshot.cutoff, "author_id": "fixture-author",
        "case_sha256": digest(case), "evidence_sha256": evidence_snapshot_sha256(snapshot),
        "historical_anchor": {"fiscal_label": "Synthetic YTD", "case_opening_date": case.opening_date,
                              "revenue_fact": anchor_facts[0], "operating_income_fact": anchor_facts[1]},
        "source_material": [{"id": "source-slice", "source_id": source.id,
                             "source_sha256": source.content_sha256, "start": 0,
                             "end": len(source.content), "text": source.content,
                             "context": "Synthetic mechanism test, not economic evidence."}],
        "scenarios": [{"id": "base", "label": "Synthetic base", "rationale": "Conditional test",
                       "rationale_evidence_ids": ["source-slice"], "falsifier": "Evidence contradicts test",
                       "falsifier_evidence_ids": ["source-slice"], "fiscal_year_end": "2026-12-31",
                       "periods": [{"id": period, "fiscal_label": period,
                                    "period_start": start, "period_end": end,
                                    "accounting_basis": "US GAAP", "revenue": assumption("500000000"),
                                    "gross_margin": assumption(".7"), "opex": assumption("50000000")}
                                   for period, start, end in (("q3", "2026-07-01", "2026-09-30"),
                                                              ("q4", "2026-10-01", "2026-12-31"))]}],
        "limitations": ["Synthetic operating calculation is not cash flow or valuation.",
                        "Unreviewed numerical payload secret sentinel 987654321."],
    })
    if reviewed:
        package = OperatingScenarioPackage.model_validate({**package.model_dump(), "review": {
            "reviewer_id": "fixture-independent-reviewer", "reviewed_at": "2026-09-19T12:00:00Z",
            "package_sha256": operating_scenario_package_sha256(package),
            "case_sha256": package.case_sha256, "evidence_sha256": package.evidence_sha256,
            "decision": "conditional_operating_scenarios",
            "limitations": ["Synthetic review validates mechanics only, not real economics."],
        }})
    request.evidence_path.write_bytes(canonical_json(snapshot))
    request.financial_case_path.write_bytes(canonical_json({"case": case, "operating_scenarios": package}))
    return request, snapshot, package


class ScenarioFixture(CaseFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if role == "editor":
            data = reply.data
            section = next(s for s in data["sections"] if s["purpose"] == "scenarios")
            section["text"] = (
                "Synthetic conditional fiscal-year operating income: "
                "{{calc:operating_scenario.base.fiscal_total.operating_income}}. "
                "This is not a target, cash flow or funding assessment. [filing]"
            )
            section["evidence_ids"] = ["filing"]
        return reply


def test_reviewed_operating_numbers_flow_through_challenge_reader_and_exact_review(tmp_path):
    request, snapshot, package = operating_setup(tmp_path)
    model = ScenarioFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), model))
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "1.10 billion USD" in reader and "{{calc:" not in reader
    assert "operating_scenario_review.json" in result.artifacts
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["operating_scenarios"]["status"] == "conditional"
    assert admission["model_conclusions"]["equity_per_share_value"]["status"] == "blocked"
    assert admission["acceptance_eligibility"]["status"] == "blocked"
    assert not admission["production_activation"]
    for _, payload in model.calls:
        if payload["stage"] in {"planner", "independent_challenge"} or "-coverage-" in payload["stage"]:
            assert "financial_case" not in payload
        else:
            delivered = payload["financial_case"]["operating_scenarios"]
            assert delivered["reviewed"] and delivered["package_sha256"] == operating_scenario_package_sha256(package)
            assert "Synthetic mechanism test" in canonical_json(delivered).decode()
    factual = next(p for _, p in model.calls if p["stage"] == "verify_report")
    assert factual["research"]["rendered_reader"] == reader
    assert factual["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
    catalog = read_json(request.output_dir / "calculated_values.json")
    assert all(item["valuation_method"] == "operating_scenario" for item in catalog)
    assert all(set(item["evidence_ids"]) <= {"filing", "anchor-revenue", "anchor-operating_income"} for item in catalog)
    fresh = ScenarioFixture()
    assert run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), fresh)) == result
    assert not fresh.calls


@pytest.mark.parametrize("mode", ["absent", "stale"])
def test_unreviewed_or_changed_forecast_cannot_render_calculated_values(tmp_path, mode):
    request, snapshot, _ = operating_setup(tmp_path, reviewed=mode == "stale")
    if mode == "stale":
        envelope = read_json(request.financial_case_path)
        envelope["operating_scenarios"]["scenarios"][0]["periods"][0]["revenue"]["value"] = "2000000000"
        request.financial_case_path.write_bytes(canonical_json(envelope))
    model = ScenarioFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), model))
    assert result.stop_reason == "stage_failed"
    assert result.assessment.status == "incomplete"
    assert read_json(request.output_dir / "calculated_values.json") == []
    assert read_json(request.output_dir / "report_admission.json")["operating_scenarios"]["status"] == "blocked"
    for _, payload in model.calls:
        assert "secret sentinel" not in canonical_json(payload).decode()


def test_scenario_review_and_assumptions_are_part_of_checkpoint_identity(tmp_path):
    request, snapshot, _ = operating_setup(tmp_path)
    run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), ScenarioFixture()))
    envelope = read_json(request.financial_case_path)
    envelope["operating_scenarios"]["review"]["limitations"].append("Changed review condition")
    request.financial_case_path.write_bytes(canonical_json(envelope))
    with pytest.raises(ValueError, match="incompatible"):
        run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), ScenarioFixture()))
