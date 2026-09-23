"""Self-contained cash-flow delivery tests; no live calls or research acceptance."""

from dataclasses import replace
from hashlib import sha256

import pytest

from tests.test_research_case_engine import CaseFixture, case_setup
from tests.test_research_cashflow_bridge import bridge_setup
from tradingagents.research.case_context import load_case_context
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.review_disclosures import review_disclosure
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


def setup(tmp_path, *, reviewed=True, stale=False):
    snapshot, case, operating, bridge = bridge_setup(tmp_path / "fixtures", reviewed=reviewed)
    if stale:
        bridge = bridge.model_copy(update={"limitations": (*bridge.limitations, "New unreviewed limitation.")})
    request, _ = case_setup(tmp_path / "run")
    envelope = {"case": case, "operating_scenarios": operating, "cashflow_bridge": bridge}
    request.evidence_path.write_bytes(canonical_json(snapshot))
    request.financial_case_path.write_bytes(canonical_json(envelope))
    return request, snapshot


class CashFixture(CaseFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if role == "editor":
            section = next(item for item in reply.data["sections"] if item["purpose"] == "scenarios")
            section["text"] = (
                "Synthetic conditional cash flow, not economic approval: "
                "{{calc:cashflow_bridge.base.q3.conditional_cash_flow}}."
            )
        return reply


def test_reviewed_cashflow_flows_through_engine_exact_reader_and_preview(tmp_path):
    request, snapshot = setup(tmp_path)
    models = CashFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "{{calc:" not in reader
    assert "cashflow_bridge.base.q3.conditional_cash_flow" in reader
    assert "The conditional cash-flow bridge was separately reviewed" in reader
    assert "financial schedules remain an unreviewed draft" in reader
    factual = next(payload for _, payload in models.calls if payload["stage"] == "verify_report")
    assert factual["research"]["rendered_reader"] == reader
    assert factual["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
    for _, payload in models.calls:
        if "financial_case" in payload:
            assert payload["financial_case"]["cashflow_bridge"]["reviewed"]
            assert payload["case_reader_delivery"]["cashflow_bridge_delivery"]
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["cashflow_bridge"]["status"] == "conditional"
    assert admission["model_conclusions"]["operating_asset_value"]["status"] == "blocked"
    assert admission["model_conclusions"]["equity_per_share_value"]["status"] == "blocked"
    assert admission["model_conclusions"]["funding_assessment"]["status"] == "blocked"
    assert not admission["production_activation"]
    preview_saved_reader(request.output_dir, request, tmp_path / "preview")


@pytest.mark.parametrize("stale", [False, True])
def test_absent_or_stale_cash_review_never_delivers_numeric_outputs(tmp_path, stale):
    request, snapshot = setup(tmp_path, reviewed=stale, stale=stale)
    models = CashFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    assert result.stop_reason == "stage_failed"
    assert not any(item["valuation_method"] == "cashflow_bridge"
                   for item in read_json(request.output_dir / "calculated_values.json"))
    for _, payload in models.calls:
        if "financial_case" in payload:
            context = payload["financial_case"]["cashflow_bridge"]
            assert not context["reviewed"]
            assert "scenarios" not in context
    assert read_json(request.output_dir / "report_admission.json")["cashflow_bridge"]["status"] == "blocked"


@pytest.mark.parametrize("reviewed", [False, True])
def test_disclosure_revalidates_bridge_separately_and_supports_chinese(tmp_path, reviewed):
    request, snapshot = setup(tmp_path, reviewed=reviewed)
    context = load_case_context(request.financial_case_path.read_bytes(), request, snapshot)
    result = review_disclosure(request, snapshot, context, "English")
    assert result["financial_status"] == "draft_unreviewed"
    assert result["cashflow_bridge_status"] == ("reviewed_conditional_only" if reviewed else "draft_unreviewed")
    assert "Fiscal cash flows use their stated dates" in result["text"]
    assert "cashflow_bridge_package_sha256" in result
    chinese = review_disclosure(request, snapshot, context, "Chinese")
    assert "现金流" in chinese["text"] and "Fiscal cash flows" not in chinese["text"]
    forged_bridge = replace(context.cashflow_bridge, model_context={"reviewed": not reviewed})
    with pytest.raises(ValueError, match="differs from frozen"):
        review_disclosure(request, snapshot, replace(context, cashflow_bridge=forged_bridge), "English")
