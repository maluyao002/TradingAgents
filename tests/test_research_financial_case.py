"""Focused offline regressions for Stage 2 financial reconciliation."""

from decimal import Decimal, Inexact, Overflow, localcontext
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.financial_case import (
    CommitmentItem,
    CommitmentSchedule,
    ConclusionAssessment,
    EvidenceGap,
    FinancialCase,
    FinancialConvention,
    ReconciliationSchedule,
    ScheduleLine,
    evidence_snapshot_sha256,
    reconcile_financial_case,
)

CUTOFF = "2026-09-18T12:00:00Z"
OPENING_DATE = "2026-09-17"


def _source(source_id="filing", *, availability="full_text"):
    content = f"frozen evidence for {source_id}"
    return SourceDocument(
        id=source_id,
        url=f"https://example.test/{source_id}",
        title="Filing",
        publisher="Issuer",
        retrieved_at="2026-09-17T10:00:00Z",
        published_at="2026-09-16T10:00:00Z",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        availability=availability,
    )


def _fact(fact_id, metric, value, *, scale="1000000", unit="USD", currency="USD",
          period_end=OPENING_DATE, period_start=None, period_type="instant", inputs=(),
          formula=None, source_id="filing"):
    return FinancialFact(
        id=fact_id,
        source_id=source_id,
        metric=metric,
        value=value,
        scale=scale,
        unit=unit,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,
        basis="US GAAP",
        location=f"row {fact_id}",
        inputs=inputs,
        formula=formula,
    )


def _snapshot(*, facts=None, sources=None):
    return EvidenceSnapshot(
        ticker="NVDA",
        cutoff=CUTOFF,
        sources=sources or (_source(),),
        facts=facts or (
            _fact("receivables", "receivables", "12"),
            _fact("payables", "payables", "5"),
            _fact("cash", "cash", "10"),
            _fact("securities", "marketable_securities", "4"),
            _fact("debt", "debt", "3"),
            _fact("lease", "operating_lease_liability", "2"),
            _fact("shares", "diluted_shares", "24", unit="shares", currency=None),
            _fact("commitment", "purchase_commitment", "6"),
            _fact("cloud", "cloud_commitment", "7"),
        ),
    )


def _component(component_id, fact, classification, effect="add", *, convention_id=None):
    return ScheduleLine(
        id=component_id,
        fact_id=fact.id,
        classification=classification,
        effect=effect,
        normalized_value=fact.normalized_value,
        unit=fact.unit,
        currency=fact.currency,
        period_start=fact.period_start,
        period_end=fact.period_end,
        convention_ids=(convention_id or f"policy-{component_id}",),
    )


def _case(snapshot, *, share_basis="point_in_time_diluted", schedule_status="complete",
          commitment_status="complete", assessments="not_assessed", gaps=(),
          maximum_age_days=0, unknown_commitment=False):
    facts = {fact.id: fact for fact in snapshot.facts}
    components = (
        _component("wc-asset", facts["receivables"], "operating_current_asset"),
        _component("wc-liability", facts["payables"], "operating_current_liability", "subtract"),
        _component("cash-row", facts["cash"], "available_cash"),
        _component("securities-row", facts["securities"], "available_security"),
        _component("debt-row", facts["debt"], "borrowed_debt"),
        _component("lease-row", facts["lease"], "operating_lease", "exclude"),
        _component("share-row", facts["shares"], "diluted_shares"),
    )
    schedules = (
        ReconciliationSchedule(
            id="wc-schedule", kind="operating_working_capital", status=schedule_status,
            rationale="Operating current rows are classified explicitly.", components=components[:2],
        ),
        ReconciliationSchedule(
            id="cash-schedule", kind="cash_and_securities", status=schedule_status,
            rationale="Only amounts assessed as available are included.", components=components[2:4],
        ),
        ReconciliationSchedule(
            id="debt-schedule", kind="debt_and_leases", status=schedule_status,
            rationale="The operating lease is excluded under the stated profit convention.",
            components=components[4:6],
        ),
        ReconciliationSchedule(
            id="share-schedule", kind="shares", status=schedule_status,
            rationale="The denominator basis is explicit.", components=components[6:],
            share_basis=share_basis,
        ),
    )
    commitment_fact = facts["cloud"] if unknown_commitment else facts["commitment"]
    commitment = CommitmentItem(
        id="commitment-row",
        fact_id=commitment_fact.id,
        normalized_value=commitment_fact.normalized_value,
        unit=commitment_fact.unit,
        currency=commitment_fact.currency,
        period_start=commitment_fact.period_start,
        period_end=commitment_fact.period_end,
        timing="unknown" if unknown_commitment else "known",
        due_start=None if unknown_commitment else "2026-10-01",
        due_end=None if unknown_commitment else "2027-09-30",
        overlap="unknown" if unknown_commitment else "none",
        treatment="not_assessed" if unknown_commitment else "deduct_incrementally",
        convention_ids=("policy-commitment-row",),
    )
    all_items = (*components, commitment)
    conventions = tuple(
        FinancialConvention(
            id=item.convention_ids[0],
            kind="convention",
            description=f"Explicit treatment for {item.id}; not an issuer-reported fact.",
            affected_component_ids=(item.id,),
        )
        for item in all_items
    )
    evidence_ids = ("cash", "debt", "shares", "commitment")
    return FinancialCase(
        ticker="NVDA",
        cutoff=CUTOFF,
        snapshot_sha256=evidence_snapshot_sha256(snapshot),
        opening_date=OPENING_DATE,
        maximum_age_days=maximum_age_days,
        schedules=schedules,
        commitments=CommitmentSchedule(
            id="commitment-schedule",
            status=commitment_status,
            rationale="Timing and overlap are separately classified.",
            items=(commitment,),
        ),
        conventions=conventions,
        gaps=gaps,
        assessments=(
            ConclusionAssessment(
                id="equity-assessment",
                output="equity_bridge",
                status=assessments,
                rationale="Reviewed only when explicitly marked assessed.",
                evidence_ids=evidence_ids if assessments == "assessed" else (),
            ),
            ConclusionAssessment(
                id="funding-assessment",
                output="funding",
                status=assessments,
                rationale="Funding adequacy is a separate reviewed conclusion.",
                evidence_ids=evidence_ids if assessments == "assessed" else (),
            ),
        ),
    )


def _schedule(result, kind):
    return next(schedule for schedule in result.schedules if schedule.kind == kind)


def test_recomputes_normalized_schedule_subtotals_and_keeps_assessments_blocked():
    snapshot = _snapshot()
    result = reconcile_financial_case(_case(snapshot), snapshot)

    assert _schedule(result, "operating_working_capital").included_subtotal == 7_000_000
    assert _schedule(result, "cash_and_securities").included_subtotal == 14_000_000
    assert _schedule(result, "debt_and_leases").included_subtotal == 3_000_000
    assert _schedule(result, "debt_and_leases").excluded_component_ids == ("lease-row",)
    assert _schedule(result, "shares").included_subtotal == 24_000_000
    assert result.commitments.incremental_subtotal == 6_000_000
    assert result.output_eligibility.operating_asset_value.status == "conditional"
    assert result.output_eligibility.equity_per_share_value.status == "blocked"
    assert result.output_eligibility.funding_assessment.status == "blocked"
    assert result.snapshot_sha256 == evidence_snapshot_sha256(snapshot)
    assert len(result.case_sha256) == 64


def test_explicit_complete_assessments_cannot_clear_equity_or_funding():
    snapshot = _snapshot()
    result = reconcile_financial_case(_case(snapshot, assessments="assessed"), snapshot)

    assert result.output_eligibility.equity_per_share_value.status == "blocked"
    assert result.output_eligibility.funding_assessment.status == "blocked"
    assert "does not by itself authorize" in result.output_eligibility.equity_per_share_value.reasons[-1]
    assert "does not by itself authorize" in result.output_eligibility.funding_assessment.reasons[-1]


def test_unknown_commitment_stays_unknown_and_blocks_funding_without_double_deduction():
    snapshot = _snapshot()
    case = _case(
        snapshot,
        assessments="assessed",
        commitment_status="partial",
        unknown_commitment=True,
    )
    result = reconcile_financial_case(case, snapshot)

    assert result.commitments.incremental_subtotal is None
    assert result.commitments.already_reflected_subtotal is None
    assert result.commitments.unassessed_ids == ("commitment-row",)
    assert result.output_eligibility.funding_assessment.status == "blocked"

    with pytest.raises(ValidationError, match="cannot be deducted"):
        CommitmentItem.model_validate({
            **case.commitments.items[0].model_dump(),
            "treatment": "deduct_incrementally",
        })


@pytest.mark.parametrize("overlap", ["opex", "capex", "working_capital", "multiple"])
def test_reflected_commitments_cannot_be_deducted_twice(overlap):
    snapshot = _snapshot()
    item = _case(snapshot).commitments.items[0]
    with pytest.raises(ValidationError, match="cannot be deducted twice"):
        CommitmentItem.model_validate({
            **item.model_dump(),
            "overlap": overlap,
            "treatment": "deduct_incrementally",
        })


def test_gap_stale_date_and_share_proxy_each_block_dependent_outputs():
    stale_facts = tuple(
        FinancialFact.model_validate({**fact.model_dump(), "period_end": "2026-07-26"})
        for fact in _snapshot().facts
    )
    snapshot = _snapshot(facts=stale_facts)
    gap = EvidenceGap(
        id="lease-gap",
        area="debt_and_leases",
        description="Lease classification remains unresolved.",
        blocks=("equity_per_share_value", "funding_assessment"),
        evidence_ids=("lease",),
    )
    case = _case(
        snapshot,
        share_basis="latest_quarter_diluted_proxy",
        assessments="assessed",
        gaps=(gap,),
    )
    result = reconcile_financial_case(case, snapshot)

    assert result.output_eligibility.operating_asset_value.status == "blocked"
    assert result.output_eligibility.equity_per_share_value.status == "blocked"
    assert result.output_eligibility.funding_assessment.status == "blocked"
    assert result.output_eligibility.opening_date_alignment.status == "blocked"
    assert result.evidence_gaps == (gap,)


def test_partial_subtotal_is_labeled_partial_and_never_unlocks_equity_bridge():
    snapshot = _snapshot()
    case = _case(snapshot, schedule_status="partial", assessments="assessed")
    result = reconcile_financial_case(case, snapshot)

    cash = _schedule(result, "cash_and_securities")
    assert cash.status == "partial"
    assert cash.included_subtotal == 14_000_000
    assert result.output_eligibility.equity_per_share_value.status == "blocked"
    assert result.output_eligibility.funding_assessment.status == "blocked"


@pytest.mark.parametrize(
    ("kind", "classification", "unit", "currency", "message"),
    [
        (
            "operating_working_capital",
            "operating_current_asset",
            "shares",
            None,
            "monetary schedules",
        ),
        ("debt_and_leases", "borrowed_debt", "ratio", None, "monetary schedules"),
        ("shares", "diluted_shares", "USD", "USD", "share schedules"),
    ],
)
def test_schedule_unit_semantics_apply_to_unresolved_rows(
    kind, classification, unit, currency, message
):
    line = ScheduleLine(
        id="wrong-unit-row",
        fact_id="selected-fact",
        classification=classification,
        effect="unresolved",
        normalized_value="1",
        unit=unit,
        currency=currency,
        period_end=OPENING_DATE,
        convention_ids=("classification-policy",),
    )
    with pytest.raises(ValidationError, match=message):
        ReconciliationSchedule(
            id="wrong-unit-schedule",
            kind=kind,
            status="partial",
            rationale="Unit mismatch must fail even while the row is unresolved.",
            components=(line,),
            share_basis="not_assessed" if kind == "shares" else None,
        )


def test_boundary_revalidates_copies_and_verifies_snapshot_hash_and_normalized_fields():
    snapshot = _snapshot()
    case = _case(snapshot)
    forged = case.model_copy(update={
        "schedules": (
            case.schedules[0].model_copy(update={"status": "complete", "components": ()}),
            *case.schedules[1:],
        ),
    })
    with pytest.raises(ValidationError, match="included component"):
        reconcile_financial_case(forged, snapshot)

    wrong_hash = case.model_copy(update={"snapshot_sha256": "0" * 64})
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        reconcile_financial_case(wrong_hash, snapshot)

    cash_schedule = case.schedules[1]
    bad_cash = cash_schedule.components[0].model_copy(update={"normalized_value": Decimal(1)})
    wrong_amount = case.model_copy(update={
        "schedules": (
            case.schedules[0],
            cash_schedule.model_copy(update={
                "components": (bad_cash, *cash_schedule.components[1:]),
            }),
            *case.schedules[2:],
        ),
    })
    with pytest.raises(ValueError, match="normalized fact fields differ"):
        reconcile_financial_case(wrong_amount, snapshot)


def test_ineligible_fact_ancestry_and_duplicate_fact_use_fail_closed():
    source = _source()
    unavailable = _source("missing", availability="unavailable")
    parent = _fact("parent", "cash", "1", source_id="missing")
    derived = _fact(
        "cash", "cash", "1", inputs=("parent",), formula="parent", source_id="filing"
    )
    ordinary = _snapshot().facts
    facts = tuple(derived if fact.id == "cash" else fact for fact in ordinary) + (parent,)
    snapshot = _snapshot(facts=facts, sources=(source, unavailable))
    with pytest.raises(ValueError, match="ineligible fact"):
        reconcile_financial_case(_case(snapshot), snapshot)

    clean = _snapshot()
    case = _case(clean)
    duplicate = case.schedules[1].components[0].model_copy(update={"fact_id": "receivables"})
    with pytest.raises(ValidationError, match="selected more than once"):
        FinancialCase.model_validate({
            **case.model_dump(),
            "schedules": (
                case.schedules[0],
                case.schedules[1].model_copy(update={
                    "components": (duplicate, *case.schedules[1].components[1:]),
                }),
                *case.schedules[2:],
            ),
        })


def test_duplicate_commitment_and_cross_schedule_fact_references_are_rejected():
    snapshot = _snapshot()
    case = _case(snapshot)
    item = case.commitments.items[0]
    duplicate = CommitmentItem.model_validate({
        **item.model_dump(),
        "id": "duplicate-commitment-row",
        "convention_ids": ("policy-duplicate-commitment-row",),
    })
    duplicate_convention = FinancialConvention(
        id="policy-duplicate-commitment-row",
        kind="convention",
        description="Duplicate fact selection must fail before reconciliation.",
        affected_component_ids=(duplicate.id,),
    )
    with pytest.raises(ValidationError, match="selected more than once"):
        FinancialCase.model_validate({
            **case.model_dump(),
            "commitments": case.commitments.model_copy(
                update={"items": (*case.commitments.items, duplicate)}
            ),
            "conventions": (*case.conventions, duplicate_convention),
        })

    cash = next(fact for fact in snapshot.facts if fact.id == "cash")
    cross_schedule = item.model_copy(update={
        "fact_id": cash.id,
        "normalized_value": cash.normalized_value,
        "unit": cash.unit,
        "currency": cash.currency,
        "period_start": cash.period_start,
        "period_end": cash.period_end,
    })
    with pytest.raises(ValidationError, match="selected more than once"):
        FinancialCase.model_validate({
            **case.model_dump(),
            "commitments": case.commitments.model_copy(update={"items": (cross_schedule,)}),
        })


def test_commitment_conventions_can_target_commitment_items():
    snapshot = _snapshot()
    case = _case(snapshot)

    assert case.commitments.items[0].id in {
        target
        for convention in case.conventions
        for target in convention.affected_component_ids
    }
    assert reconcile_financial_case(case, snapshot).commitments.incremental_ids == (
        "commitment-row",
    )


def test_maximum_guarantee_exposure_is_not_treated_as_expected_cash():
    item = _case(_snapshot()).commitments.items[0]

    with pytest.raises(ValidationError, match="not an expected cash commitment"):
        CommitmentItem.model_validate({
            **item.model_dump(),
            "kind": "guarantee",
            "amount_basis": "maximum_exposure",
        })
    exposure = CommitmentItem.model_validate({
        **item.model_dump(),
        "kind": "guarantee",
        "amount_basis": "maximum_exposure",
        "treatment": "not_assessed",
    })
    assert exposure.treatment == "not_assessed"


def test_duplicate_selected_fact_ids_for_one_source_row_are_rejected():
    snapshot = _snapshot()
    facts = tuple(
        FinancialFact.model_validate({
            **fact.model_dump(),
            "metric": "cash",
            "value": "10",
            "location": "row cash",
        })
        if fact.id == "securities"
        else fact
        for fact in snapshot.facts
    )
    duplicate_snapshot = _snapshot(facts=facts)

    with pytest.raises(ValueError, match="duplicate the same source row"):
        reconcile_financial_case(_case(duplicate_snapshot), duplicate_snapshot)


def test_decimal_reconciliation_is_isolated_from_callers_context():
    facts = list(_snapshot().facts)
    facts[0] = _fact("receivables", "receivables", "12345678901234567890.12", scale="100")
    facts[1] = _fact("payables", "payables", "0.12", scale="100")
    snapshot = _snapshot(facts=tuple(facts))
    case = _case(snapshot)

    with localcontext() as context:
        context.prec = 4
        context.Emax = 9
        context.Emin = -9
        context.traps[Inexact] = True
        context.traps[Overflow] = True
        result = reconcile_financial_case(case, snapshot)

    assert _schedule(result, "operating_working_capital").included_subtotal == Decimal(
        "1234567890123456789000"
    )
