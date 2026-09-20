"""Provenance regressions for retirable reviewed operating limitations."""

from tests.test_research_operating_scenarios import _draft, _reviewed
from tradingagents.research.case_context import load_case_context
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.operating_scenarios import (
    _BOUNDARY_LIMITATION,
    OperatingScenarioReview,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import canonical_json


def test_prompt_origin_metadata_does_not_alias_machine_protection(tmp_path):
    from copy import deepcopy

    from tradingagents.research.report_review import limitation_packet
    from tradingagents.research.review_lifecycle import enrich_issues

    package, case, snapshot = _reviewed()
    request = ResearchRequest(
        ticker=case.ticker, cutoff=case.cutoff, timezone=case.timezone, backend="api",
        output_dir=tmp_path / "out", quality_revision="evidence-led-bounded",
        financial_case_path=tmp_path / "case.json",
    )
    context = load_case_context(canonical_json({
        "case": case, "operating_scenarios": package,
    }), request, snapshot)
    before = deepcopy(context.limitation_origins)
    operating_before = deepcopy(context.operating_scenarios.model_context)
    packet = context.model_context()
    packet["limitation_origins"][_BOUNDARY_LIMITATION][0]["retirable"] = True
    packet["operating_scenarios"]["package_sha256"] = "f" * 64
    enriched = enrich_issues(limitation_packet([_BOUNDARY_LIMITATION]), {}, [],
                             limitation_origins=context.limitation_origins)
    enriched[0]["origins"][0]["retirable"] = True
    assert context.limitation_origins == before
    assert context.operating_scenarios.model_context == operating_before


def test_reviewed_operating_origins_require_the_package_metadata_witness():
    package, case, snapshot = _reviewed()
    result = evaluate_operating_scenarios(package, case, snapshot)

    package_origin = result.limitation_origins[package.limitations[0]][0]
    review_origin = result.limitation_origins[package.review.limitations[0]][0]
    boundary_origin = result.limitation_origins[_BOUNDARY_LIMITATION][0]
    assert package_origin["retirable"] and review_origin["retirable"]
    assert package_origin["required_witness_reference"] == "review:operating_scenarios"
    assert package_origin["package_sha256"] == operating_scenario_package_sha256(package)
    assert not boundary_origin["retirable"]
    assert boundary_origin["required_witness_reference"] is None


def test_duplicate_text_preserves_origins_and_a_protected_origin_wins():
    package, case, snapshot = _reviewed()
    package = package.model_copy(update={"limitations": (*package.limitations, _BOUNDARY_LIMITATION)})
    review = OperatingScenarioReview(
        reviewer_id=package.review.reviewer_id,
        reviewed_at=package.review.reviewed_at,
        package_sha256=operating_scenario_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        decision=package.review.decision,
        limitations=package.review.limitations,
    )
    package = package.model_copy(update={"review": review})

    result = evaluate_operating_scenarios(package, case, snapshot)

    origins = result.limitation_origins[_BOUNDARY_LIMITATION]
    assert {origin["origin_id"] for origin in origins} == {
        "operating.package.limitation.1", "operating.boundary",
    }
    assert any(origin["retirable"] for origin in origins)
    assert any(not origin["retirable"] for origin in origins)


def test_unreviewed_authored_limitations_are_not_exposed_through_case_context(tmp_path):
    package, case, snapshot = _draft()
    request = ResearchRequest(
        ticker=case.ticker,
        cutoff=case.cutoff,
        timezone=case.timezone,
        backend="api",
        output_dir=tmp_path / "output",
        quality_revision="evidence-led-bounded",
        valuation_method="fcff",
        financial_case_path=tmp_path / "case.json",
    )
    context = load_case_context(canonical_json({
        "case": case.model_dump(mode="json"),
        "operating_scenarios": package.model_dump(mode="json"),
    }), request, snapshot)

    assert package.limitations[0] not in context.limitation_origins
    assert package.limitations[0] not in context.model_context()["limitation_origins"]
