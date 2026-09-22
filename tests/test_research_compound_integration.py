import pytest

from tests.test_research_case_scenarios import ScenarioFixture, operating_setup
from tradingagents.research.engine import run_research
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.review_lifecycle import _SCENARIO_AND_VALUATION_GAP
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import digest, read_json


class CompoundFixture(ScenarioFixture):
    def __init__(self, *, omit_component=False):
        super().__init__()
        self.omit_component = omit_component

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if role == "business":
            reply.data["unresolved_gaps"] = [_SCENARIO_AND_VALUATION_GAP]
        if role == "editor":
            for issue in payload["compound_obligation_guidance"]:
                active = [component["text"] for component in issue["compound_obligation"]["components"]
                          if component["reader_treatment"] == "reader_required"]
                for section in reply.data["sections"]:
                    section["text"] = section["text"].replace(issue["text"], "\n\n".join(active))
        if role == "verifier" and self.omit_component and "-coverage-" in payload["stage"]:
            reply.data["limitation_dispositions"] = [
                item for item in reply.data["limitation_dispositions"]
                if not item["issue_id"].endswith("#component:market_and_discount_rate_inputs")
            ]
        return reply


@pytest.mark.parametrize("omit", [False, True])
def test_atomic_coverage_fans_in_only_after_each_child_passes(tmp_path, omit):
    request, snapshot, _ = operating_setup(tmp_path)
    models = CompoundFixture(omit_component=omit)
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    parent_id = "limitation-" + digest(_SCENARIO_AND_VALUATION_GAP)
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    children = [issue for _, payload in models.calls
                for issue in payload["research"].get("limitation_review", ())
                if issue.get("compound_parent", {}).get("issue_id") == parent_id]
    assert children
    assert all(child["text"] != _SCENARIO_AND_VALUATION_GAP for child in children)
    assert not any(child["coverage_component"]["status"] == "satisfied_current_evidence" for child in children)
    lifecycle = next(item for item in verification["issue_lifecycle"]["issues"]
                     if item["issue_id"] == parent_id)
    assert lifecycle["status"] == "open"
    assert lifecycle["text"] == _SCENARIO_AND_VALUATION_GAP
    assert lifecycle["resolution_protected"]
    if omit:
        assert result.stop_reason == "verification_failed"
        assert not verification["exported"]
        assert parent_id not in verification["validated_limitation_ids"]
    else:
        assert result.stop_reason == "completed_needs_review"
        assert verification["exported"]
        assert parent_id in verification["required_limitation_ids"]
        assert parent_id in verification["validated_limitation_ids"]
        assert not any("#component:" in item for item in verification["required_limitation_ids"])
        assert _SCENARIO_AND_VALUATION_GAP not in (request.output_dir / "reader_report.md").read_text()
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["model_conclusions"]["equity_per_share_value"]["status"] == "blocked"
    assert not admission["production_activation"]
