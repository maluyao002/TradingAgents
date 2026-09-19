"""Focused offline regressions for reviewed operating-scenario delivery."""

from __future__ import annotations

import json
from datetime import date
from decimal import Context, Decimal, Inexact, Overflow, Rounded, localcontext
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ReviewFinding,
    SourceDocument,
)
from tradingagents.research.financial_case import (
    CommitmentSchedule,
    ConclusionAssessment,
    FinancialCase,
    ReconciliationSchedule,
    evidence_snapshot_sha256,
)
from tradingagents.research.operating_scenarios import (
    ForecastSourceMaterial,
    HistoricalOperatingAnchor,
    OperatingScenario,
    OperatingScenarioAssumption,
    OperatingScenarioPackage,
    OperatingScenarioPeriod,
    OperatingScenarioReview,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import digest

D = Decimal
CUTOFF = "2025-09-18T12:00:00Z"
H1_START = date(2025, 1, 27)
H1_END = date(2025, 7, 27)
FY_END = date(2026, 1, 25)
SOURCE_TEXT = (
    "Management supplied an exact Q3 revenue and gross-margin guide. "
    "The analyst uses that guide and states a separate Q4 operating assumption."
)


def _source(content: str = SOURCE_TEXT) -> SourceDocument:
    return SourceDocument(
        id="ir-q2",
        url="https://example.test/q2-release",
        title="Q2 earnings release",
        publisher="Issuer",
        retrieved_at="2025-08-28T10:00:00Z",
        published_at="2025-08-27T20:00:00Z",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        kind="ir",
        availability="full_text",
    )


def _fact(fact_id: str, metric: str, value: str, *, segment=None, basis="US GAAP"):
    return FinancialFact(
        id=fact_id,
        source_id="ir-q2",
        metric=metric,
        value=value,
        unit="USD",
        currency="USD",
        scale="1",
        period_start=H1_START,
        period_end=H1_END,
        period_type="duration",
        basis=basis,
        location=f"reported {metric}",
        segment=segment,
    )


def _snapshot(source: SourceDocument | None = None) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        ticker="NVDA",
        cutoff=CUTOFF,
        sources=(source or _source(),),
        facts=(
            _fact("h1-revenue", "revenue", "50"),
            _fact("h1-operating-income", "operating_income", "20"),
        ),
    )


def _case(snapshot: EvidenceSnapshot, *, opening_date=H1_END) -> FinancialCase:
    schedules = tuple(
        ReconciliationSchedule(
            id=f"{kind}-schedule",
            kind=kind,
            status="not_assessed",
            rationale="This schedule remains outside the operating-scenario boundary.",
            share_basis="not_assessed" if kind == "shares" else None,
        )
        for kind in (
            "operating_working_capital",
            "cash_and_securities",
            "debt_and_leases",
            "shares",
        )
    )
    return FinancialCase(
        ticker="NVDA",
        cutoff=CUTOFF,
        snapshot_sha256=evidence_snapshot_sha256(snapshot),
        opening_date=opening_date,
        schedules=schedules,
        commitments=CommitmentSchedule(
            id="commitment-schedule",
            status="not_assessed",
            rationale="Commitments are not assessed by an operating scenario.",
        ),
        conventions=(),
        assessments=(
            ConclusionAssessment(
                id="equity-assessment",
                output="equity_bridge",
                status="not_assessed",
                rationale="No equity conclusion is made.",
            ),
            ConclusionAssessment(
                id="funding-assessment",
                output="funding",
                status="not_assessed",
                rationale="No funding conclusion is made.",
            ),
        ),
    )


def _assumption(value: str, *, guidance=False) -> OperatingScenarioAssumption:
    return OperatingScenarioAssumption(
        value=D(value),
        classification=("management_guidance_anchor" if guidance else "analyst_assumption"),
        evidence_ids=("q2-passage",),
        rationale="The exact passage anchors this explicit conditional input.",
    )


def _periods() -> tuple[OperatingScenarioPeriod, OperatingScenarioPeriod]:
    return (
        OperatingScenarioPeriod(
            id="q3",
            fiscal_label="FY2026 Q3",
            period_start=date(2025, 7, 28),
            period_end=date(2025, 10, 26),
            accounting_basis="US GAAP",
            revenue=_assumption("30", guidance=True),
            gross_margin=_assumption("0.5", guidance=True),
            opex=_assumption("5"),
        ),
        OperatingScenarioPeriod(
            id="q4",
            fiscal_label="FY2026 Q4",
            period_start=date(2025, 10, 27),
            period_end=FY_END,
            accounting_basis="US GAAP",
            revenue=_assumption("40"),
            gross_margin=_assumption("0.6"),
            opex=_assumption("6"),
            tax_rate=_assumption("0.15"),
        ),
    )


def _draft(
    snapshot: EvidenceSnapshot | None = None,
    case: FinancialCase | None = None,
    *,
    periods=None,
    anchor: HistoricalOperatingAnchor | None = None,
) -> tuple[OperatingScenarioPackage, FinancialCase, EvidenceSnapshot]:
    snapshot = snapshot or _snapshot()
    case = case or _case(snapshot)
    facts = {fact.id: fact for fact in snapshot.facts}
    anchor = anchor or HistoricalOperatingAnchor(
        fiscal_label="FY2026 H1 reported",
        case_opening_date=H1_END,
        revenue_fact=facts["h1-revenue"],
        operating_income_fact=facts["h1-operating-income"],
    )
    material = ForecastSourceMaterial(
        id="q2-passage",
        source_id="ir-q2",
        source_sha256=snapshot.sources[0].content_sha256,
        start=0,
        end=len(snapshot.sources[0].content),
        text=snapshot.sources[0].content,
        context="Management guidance followed by the analyst's explicit Q4 convention.",
    )
    scenario = OperatingScenario(
        id="base",
        label="Conditional base",
        rationale="Q3 follows guidance; Q4 is an explicit analyst assumption.",
        rationale_evidence_ids=(material.id,),
        falsifier="A material miss against the stated guide would falsify this case.",
        falsifier_evidence_ids=(material.id,),
        fiscal_year_end=FY_END,
        periods=periods or _periods(),
    )
    package = OperatingScenarioPackage(
        ticker="NVDA",
        cutoff=CUTOFF,
        author_id="scenario-author",
        case_sha256=digest(case.model_dump(mode="json")),
        evidence_sha256=evidence_snapshot_sha256(snapshot),
        historical_anchor=anchor,
        source_material=(material,),
        scenarios=(scenario,),
        limitations=("Q4 is a conditional analyst case, not issuer guidance.",),
    )
    return package, case, snapshot


def _reviewed(**kwargs):
    package, case, snapshot = _draft(**kwargs)
    review = OperatingScenarioReview(
        reviewer_id="independent-reviewer",
        reviewed_at="2025-09-19T12:00:00Z",
        package_sha256=operating_scenario_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        decision="conditional_operating_scenarios",
        limitations=("Review is conditional and does not validate valuation conclusions.",),
    )
    return package.model_copy(update={"review": review}), case, snapshot


def _calculated(result, suffix: str):
    return next(value for value in result.calculated_values if value.id.endswith(suffix))


def test_reviewed_package_delivers_exact_material_review_and_operating_arithmetic():
    package, case, snapshot = _reviewed()
    result = evaluate_operating_scenarios(package, case, snapshot)

    assert result.reviewed is True
    assert result.model_context["source_material"] == [
        package.source_material[0].model_dump(mode="json")
    ]
    assert result.model_context["review"] == package.review.model_dump(mode="json")
    assert result.model_context["scenarios"][0]["rationale"] == package.scenarios[0].rationale
    assert result.model_context["scenarios"][0]["falsifier"] == package.scenarios[0].falsifier
    assert _calculated(result, "q3.gross_profit").value == D("15")
    assert _calculated(result, "q3.operating_income").value == D("10")
    assert _calculated(result, "q4.operating_income").value == D("18")
    assert _calculated(result, "fiscal_total.revenue").value == D("120")
    assert _calculated(result, "fiscal_total.operating_income").value == D("48")
    assert len(result.calculated_values) == 12
    assert all(value.valuation_method == "operating_scenario" for value in result.calculated_values)
    assert all(value.share_count_basis == "not_applicable" for value in result.calculated_values)
    assert {item for value in result.calculated_values for item in value.evidence_ids} <= {
        "ir-q2",
        "h1-revenue",
        "h1-operating-income",
    }
    artifact = json.loads(result.artifacts["operating_scenario_context.json"])
    assert artifact["source_material"][0]["text"] == SOURCE_TEXT
    assert artifact["review"]["reviewer_id"] == "independent-reviewer"


def test_absent_or_hash_stale_review_is_audit_only_without_numeric_leak():
    draft, case, snapshot = _draft()
    draft_result = evaluate_operating_scenarios(draft, case, snapshot)

    assert draft_result.reviewed is False
    assert draft_result.calculated_values == ()
    assert set(draft_result.model_context) == {
        "schema_version",
        "context_kind",
        "reviewed",
        "audit",
        "limitations",
    }
    assert "scenarios" not in draft_result.model_context
    assert "operating_scenario_calculated_values.json" not in draft_result.artifacts

    reviewed, case, snapshot = _reviewed()
    q3, q4 = reviewed.scenarios[0].periods
    changed_q4 = q4.model_copy(update={"revenue": q4.revenue.model_copy(update={"value": D("41")})})
    changed_scenario = reviewed.scenarios[0].model_copy(update={"periods": (q3, changed_q4)})
    changed = reviewed.model_copy(update={"scenarios": (changed_scenario,)})
    stale_result = evaluate_operating_scenarios(changed, case, snapshot)

    assert stale_result.reviewed is False
    assert stale_result.calculated_values == ()
    assert "scenarios" not in stale_result.model_context
    assert "stale or mismatched" in " ".join(stale_result.limitations)


def test_review_must_be_independent_post_cutoff_and_free_of_unresolved_findings():
    package, case, snapshot = _draft()
    warning = ReviewFinding(code="numbers", severity="warning", message="Needs resolution")
    review = OperatingScenarioReview(
        reviewer_id=package.author_id,
        reviewed_at="2025-09-17T12:00:00Z",
        package_sha256=operating_scenario_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        decision="conditional_operating_scenarios",
        findings=(warning,),
        limitations=("The warning is unresolved.",),
    )
    result = evaluate_operating_scenarios(
        package.model_copy(update={"review": review}), case, snapshot
    )

    assert result.reviewed is False
    assert result.calculated_values == ()
    joined = " ".join(result.limitations)
    assert "author and independent reviewer" in joined
    assert "cannot predate" in joined
    assert "warning or critical" in joined


def test_periods_must_be_unique_contiguous_and_cover_a_real_fiscal_year():
    package, case, snapshot = _draft()
    q3, q4 = package.scenarios[0].periods

    with pytest.raises(ValidationError, match="period IDs and fiscal labels must be unique"):
        OperatingScenario.model_validate(
            {
                **package.scenarios[0].model_dump(),
                "periods": (q3, q3),
            }
        )

    gap_q4 = q4.model_copy(update={"period_start": date(2025, 10, 28)})
    gap_scenario = package.scenarios[0].model_copy(update={"periods": (q3, gap_q4)})
    with pytest.raises(ValueError, match="contiguous after the anchor"):
        evaluate_operating_scenarios(
            package.model_copy(update={"scenarios": (gap_scenario,)}), case, snapshot
        )

    short_scenario = package.scenarios[0].model_copy(
        update={
            "fiscal_year_end": date(2025, 12, 31),
            "periods": (q3, q4.model_copy(update={"period_end": date(2025, 12, 31)})),
        }
    )
    with pytest.raises(ValueError, match="360-to-371-day fiscal year"):
        evaluate_operating_scenarios(
            package.model_copy(update={"scenarios": (short_scenario,)}), case, snapshot
        )


def test_units_basis_segments_and_case_opening_date_fail_closed():
    package, case, snapshot = _draft()
    period = package.scenarios[0].periods[0]
    with pytest.raises(ValidationError):
        OperatingScenarioPeriod.model_validate({**period.model_dump(), "currency": "EUR"})

    q3, q4 = package.scenarios[0].periods
    wrong_basis = q3.model_copy(update={"accounting_basis": "IFRS"})
    scenario = package.scenarios[0].model_copy(update={"periods": (wrong_basis, q4)})
    with pytest.raises(ValueError, match="historical GAAP basis"):
        evaluate_operating_scenarios(
            package.model_copy(update={"scenarios": (scenario,)}), case, snapshot
        )

    with pytest.raises(ValidationError, match="whole-entity US GAAP"):
        HistoricalOperatingAnchor(
            fiscal_label="FY2026 H1",
            case_opening_date=H1_END,
            revenue_fact=_fact("segment-revenue", "revenue", "50", segment="Compute"),
            operating_income_fact=_fact("segment-op", "operating_income", "20", segment="Compute"),
        )

    changed_case = _case(snapshot, opening_date=date(2025, 7, 26))
    changed_package = package.model_copy(
        update={"case_sha256": digest(changed_case.model_dump(mode="json"))}
    )
    with pytest.raises(ValueError, match="observed case opening date"):
        evaluate_operating_scenarios(changed_package, changed_case, snapshot)


def test_changed_frozen_source_or_fact_copy_is_rejected():
    package, case, snapshot = _draft()
    changed_source = _source(SOURCE_TEXT + " changed")
    changed_snapshot = _snapshot(changed_source)
    with pytest.raises(ValueError, match="evidence hash differs"):
        evaluate_operating_scenarios(package, case, changed_snapshot)

    copied_anchor = package.historical_anchor.model_copy(
        update={
            "revenue_fact": package.historical_anchor.revenue_fact.model_copy(
                update={"value": D("51")}
            )
        }
    )
    copied_package = package.model_copy(update={"historical_anchor": copied_anchor})
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        evaluate_operating_scenarios(copied_package, case, snapshot)


def test_decimal_results_ignore_hostile_caller_context_and_extremes_are_rejected():
    package, case, snapshot = _reviewed()
    baseline = evaluate_operating_scenarios(package, case, snapshot)
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        context.traps[Overflow] = True
        hostile = evaluate_operating_scenarios(package, case, snapshot)

    assert hostile.calculated_values == baseline.calculated_values
    q3, q4 = package.scenarios[0].periods
    tiny = q3.model_copy(
        update={"gross_margin": q3.gross_margin.model_copy(update={"value": D("1e-31")})}
    )
    scenario = package.scenarios[0].model_copy(update={"periods": (tiny, q4)})
    with pytest.raises(ValueError, match="bounded decimal domain"):
        evaluate_operating_scenarios(
            package.model_copy(update={"scenarios": (scenario,)}), case, snapshot
        )


def test_permitted_long_decimal_coefficients_multiply_and_sum_exactly():
    revenue = D("12345678901234567890123456789012345678901234567890.123456789012345678901234567890")
    gross_margin = D("0.123456789012345678901234567890")
    q3, q4 = _periods()
    long_q3 = q3.model_copy(
        update={
            "revenue": q3.revenue.model_copy(update={"value": revenue}),
            "gross_margin": q3.gross_margin.model_copy(update={"value": gross_margin}),
        }
    )
    package, case, snapshot = _reviewed(periods=(long_q3, q4))

    with localcontext(Context(prec=200)):
        expected_product = revenue * gross_margin
        expected_fiscal_operating_income = expected_product - D("5") + D("18") + D("20")
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.traps[Overflow] = True
        result = evaluate_operating_scenarios(package, case, snapshot)

    assert _calculated(result, "q3.gross_profit").value == expected_product
    assert (
        _calculated(result, "fiscal_total.operating_income").value
        == expected_fiscal_operating_income
    )


def test_raw_numeric_outputs_are_operating_only():
    package, case, snapshot = _reviewed()
    result = evaluate_operating_scenarios(package, case, snapshot)

    period_output_keys = set(result.model_context["scenarios"][0]["periods"][0]["calculated"])
    fiscal_output_keys = set(result.model_context["scenarios"][0]["fiscal_total"])
    assert period_output_keys == {"gross_profit", "operating_income"}
    assert fiscal_output_keys == {
        "fiscal_label",
        "period_start",
        "period_end",
        "revenue",
        "operating_income",
    }
    forbidden = {"fcff", "dcf", "equity", "per_share", "net_income", "terminal", "funding"}
    assert all(
        not any(token in value.id for token in forbidden) for value in result.calculated_values
    )
