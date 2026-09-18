from dataclasses import asdict, fields
from hashlib import sha256

from tests.test_research_engine import replies
from tests.test_research_equity_valuation import _model
from tests.test_research_evidence_led import ExplicitFixtureReviews
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest, SourceDocument
from tradingagents.research.engine import _calculate, run_research
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import read_json


def fixture(tmp_path):
    request = ResearchRequest(ticker="HOOD", cutoff="2024-12-31T12:00:00Z", backend="api",
                              output_dir=tmp_path, quality_revision="evidence-led", valuation_method="equity_fcfe")
    source = SourceDocument(id="filing", url="https://example.com/filing", title="Synthetic",
                            publisher="Synthetic", content="Financial evidence",
                            content_sha256=sha256(b"Financial evidence").hexdigest(),
                            published_at=request.cutoff, retrieved_at=request.cutoff)
    opening = {"current_net_income": ("net_income_common", 80), "current_diluted_shares": ("diluted_shares", 10)}
    facts = [{"id": name, "source_id": "filing", "metric": metric, "value": value,
              "unit": "shares" if "shares" in name else "USD",
              "currency": None if "shares" in name else "USD", "period_end": "2024-12-31",
              "basis": "US GAAP", "location": "synthetic fixture"} for name, (metric, value) in opening.items()]
    facts[0].update(period_start="2024-01-01", period_type="duration")
    snapshot = EvidenceSnapshot(ticker="HOOD", cutoff=request.cutoff, sources=(source,), facts=facts)
    model = _model()
    required = {*opening, "cost_of_equity", "terminal_growth", "units.currency", "units.amount_scale", "units.share_scale"}
    for i, period in enumerate(model.periods):
        required.update(f"periods.{i}.{field.name}" for field in fields(period) if field.name not in {"label", "discount_years"})
    assumptions = {name: {"kind": "reported" if name in opening else "assumption",
                           "rationale": "Synthetic scenario; not real Robinhood financials",
                           "evidence_ids": [name if name in opening else "filing"]} for name in required}
    proposal = ValuationProposal(model=asdict(model), evidence_ids=("filing",), assumptions=assumptions, accounting_basis="US GAAP")
    return request, snapshot, proposal


def test_equity_method_binds_common_income_and_share_facts(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    valuation = _calculate(proposal, request, snapshot)
    assert valuation["status"] == "illustrative"
    assert "enterprise_value" not in valuation["result"]
    assert "net_debt" not in valuation["result"]
    assert valuation["result"]["forecasts"][0]["equity_cash_flow"] == 80


def test_consolidated_net_income_is_not_common_net_income(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    wrong = snapshot.facts[0].model_copy(update={"metric": "net_income"})
    snapshot = snapshot.model_copy(update={"facts": (wrong, snapshot.facts[1])})
    assert _calculate(proposal, request, snapshot)["status"] == "unavailable"


def test_retained_regulatory_capital_requires_explicit_assumption_support(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    assumptions = dict(proposal.assumptions)
    del assumptions["periods.0.required_capital_retention"]
    proposal = proposal.model_copy(update={"assumptions": assumptions})
    assert _calculate(proposal, request, snapshot)["status"] == "unavailable"


def test_weighted_average_share_proxy_is_explicit_and_never_an_instant_fact(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    shares = snapshot.facts[1].model_copy(update={
        "metric": "weighted_average_diluted_shares", "period_type": "duration",
        "period_start": snapshot.facts[1].period_end.replace(month=10, day=1),
    })
    snapshot = snapshot.model_copy(update={"facts": (snapshot.facts[0], shares)})
    assert _calculate(proposal, request, snapshot)["status"] == "unavailable"
    request = request.model_copy(update={"share_count_basis": "latest_quarter_diluted_proxy"})
    result = _calculate(proposal, request, snapshot)
    assert result["status"] == "illustrative"
    assert result["share_count_basis"] == "latest_quarter_diluted_proxy"
    assert any("not point-in-time" in limitation for limitation in result["limitations"])
    assert snapshot.facts[1].period_type == "duration"
    annual_shares = shares.model_copy(update={"period_start": shares.period_end.replace(month=1, day=1)})
    snapshot = snapshot.model_copy(update={"facts": (snapshot.facts[0], annual_shares)})
    assert _calculate(proposal, request, snapshot)["status"] == "unavailable"


def test_equity_model_runs_through_exact_verified_numerical_reader(tmp_path):
    request, snapshot, proposal = fixture(tmp_path)
    request = request.model_copy(update={
        "report_language": "English", "budget": request.budget.model_copy(update={"followup_cycles": 0}),
    })
    data = replies()
    data["valuation"][0]["data"] = proposal.model_dump(mode="json")
    data["editor"][0]["data"]["sections"] = [{
        "title": "Illustrative equity cash-flow bridge",
        "text": "First-period equity cash flow: {{calc:valuation.forecasts.0.equity_cash_flow}}. "
                "Illustrative value: {{calc:valuation.value_per_current_diluted_share}}. "
                "Rounded to two decimals; assumptions-based calculations, not reported facts.",
        "evidence_ids": ["filing"],
    }]
    models = ExplicitFixtureReviews(data)
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "80.00 USD" in reader
    assert "USD/share" in reader
    assert "{{calc:" not in reader
    assert models.calls[-1][1]["research"]["rendered_reader"] == reader
    assert read_json(request.output_dir / "valuation_results.json")["status"] == "illustrative"
    assert read_json(request.output_dir / "calculated_values.json")
