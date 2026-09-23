"""Offline regressions for the dated conditional cash-flow bridge."""

from __future__ import annotations

from decimal import ROUND_DOWN, Context, Decimal, localcontext
from pathlib import Path

import pytest

from scripts.research_nvda_cashflow_case import _summary, build_package
from tests.test_research_case_scenarios import operating_setup
from tradingagents.research.cashflow_bridge import (
    AnalystCashFlowAssumption,
    CashFlowBridgePackage,
    CashFlowBridgeReview,
    CashFlowScenarioAssumptions,
    CommitmentOverlapAssumption,
    PeriodCashFlowAssumptions,
    ReportedCashFlowAnchor,
    WorkingCapitalAnchorComponent,
    _eligible_fact_map,
    _historical_result,
    cashflow_bridge_package_sha256,
    evaluate_cashflow_bridge,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact
from tradingagents.research.financial_case import (
    CommitmentItem,
    CommitmentSchedule,
    FinancialCase,
    FinancialConvention,
    evidence_snapshot_sha256,
)
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import canonical_json, digest, parse_json, read_json

D = Decimal
SOURCE = (
    Path(__file__).resolve().parents[1]
    / "reports/NVDA_VALIDATION_20260921/run_1"
)


def bridge_setup(tmp_path, *, reviewed=False):
    """Return a complete synthetic case with reviewed operating inputs.

    ``reviewed`` controls only the cash-flow bridge review; the operating package
    is always independently reviewed so tests exercise the intended layering.
    """

    request, snapshot, operating_package = operating_setup(
        tmp_path / "operating", reviewed=False
    )
    case = FinancialCase.model_validate(read_json(request.financial_case_path)["case"])
    duration = {
        "source_id": "filing",
        "scale": "1",
        "unit": "USD",
        "currency": "USD",
        "basis": "US GAAP",
        "period_start": "2026-01-01",
        "period_end": "2026-06-30",
        "period_type": "duration",
        "location": "Synthetic reported cash-flow anchor",
    }
    cash_facts = (
        FinancialFact(id="cash-pretax", metric="income_before_income_tax", value="600", **duration),
        FinancialFact(id="cash-tax", metric="income_tax_expense", value="120", **duration),
        FinancialFact(id="cash-da", metric="depreciation_amortization", value="20", **duration),
        FinancialFact(id="cash-capex", metric="capex_cashflow", value="-30", **duration),
        FinancialFact(id="cash-cfo", metric="operating_cash_flow", value="430", **duration),
    )
    balances = tuple(
        FinancialFact(
            id=f"cash-{metric}-{period}",
            metric=metric,
            value=value,
            source_id="filing",
            unit="USD",
            currency="USD",
            basis="US GAAP",
            period_end=end,
            location="Synthetic reported balance",
        )
        for metric, period, value, end in (
            ("accounts_receivable", "current", "120", "2026-06-30"),
            ("accounts_receivable", "prior", "100", "2025-12-31"),
            ("inventory", "current", "70", "2026-06-30"),
            ("inventory", "prior", "60", "2025-12-31"),
            ("accounts_payable", "current", "55", "2026-06-30"),
            ("accounts_payable", "prior", "50", "2025-12-31"),
        )
    )
    commitment_specs = (
        ("supply", "90", "purchase"),
        ("cloud", "3", "cloud"),
        ("leases", "0", "lease"),
        ("investments", "18", "other"),
        ("capex", "7", "purchase"),
        ("ai-cloud", "0", "cloud"),
        ("third-party-leases", "0", "lease"),
    )
    commitment_facts = tuple(
        FinancialFact(
            id=f"cash-commitment-{identifier}",
            metric=f"commitment_{identifier}",
            value=value,
            source_id="filing",
            unit="USD",
            currency="USD",
            basis="Reported disclosure",
            period_end="2026-06-30",
            location="Synthetic disclosed H2 commitment",
        )
        for identifier, value, _ in commitment_specs
    )
    snapshot = snapshot.model_copy(
        update={"facts": (*snapshot.facts, *cash_facts, *balances, *commitment_facts)}
    )
    commitment_items = tuple(
        CommitmentItem(
            id=f"cash-commitment-{identifier}-h2",
            fact_id=f"cash-commitment-{identifier}",
            normalized_value=D(value),
            unit="USD",
            currency="USD",
            period_end="2026-06-30",
            kind=kind,
            timing="unknown",
            disclosed_timing="h2",
            overlap="unknown",
            treatment="not_assessed",
            convention_ids=(f"cash-commitment-{identifier}-policy",),
        )
        for identifier, value, kind in commitment_specs
    )
    commitment_conventions = tuple(
        FinancialConvention(
            id=f"cash-commitment-{identifier}-policy",
            kind="assumption",
            description="Synthetic commitment remains unassessed in the source case.",
            affected_component_ids=(f"cash-commitment-{identifier}-h2",),
        )
        for identifier, _, _ in commitment_specs
    )
    schedule_component_ids = {
        component.id for schedule in case.schedules for component in schedule.components
    }
    retained_conventions = tuple(
        convention
        for convention in case.conventions
        if set(convention.affected_component_ids) <= schedule_component_ids
    )
    case = case.model_copy(
        update={
            "snapshot_sha256": evidence_snapshot_sha256(snapshot),
            "commitments": CommitmentSchedule(
                id=case.commitments.id,
                status="partial",
                rationale="Synthetic H2 commitments for cash-flow bridge mechanics.",
                items=commitment_items,
            ),
            "conventions": (*retained_conventions, *commitment_conventions),
        }
    )
    operating_package = operating_package.model_copy(
        update={
            "case_sha256": digest(case.model_dump(mode="json")),
            "evidence_sha256": evidence_snapshot_sha256(snapshot),
            "review": None,
        }
    )
    operating_package = OperatingScenarioPackage.model_validate(
        {
            **operating_package.model_dump(mode="json"),
            "review": {
                "reviewer_id": "synthetic_operating_reviewer",
                "reviewed_at": "2026-09-19T12:00:00Z",
                "package_sha256": operating_scenario_package_sha256(operating_package),
                "case_sha256": operating_package.case_sha256,
                "evidence_sha256": operating_package.evidence_sha256,
                "decision": "conditional_operating_scenarios",
                "limitations": ("Synthetic mechanics review is not economic approval.",),
            },
        }
    )

    def assumption(value, unit, evidence_ids, rationale):
        return AnalystCashFlowAssumption(
            value=value,
            unit=unit,
            evidence_ids=evidence_ids,
            rationale=rationale,
        )

    period_assumptions = tuple(
        PeriodCashFlowAssumptions(
            period_id=period,
            period_start=start,
            period_end=end,
            tax_rate=assumption(".2", "fraction", ("cash-tax", "cash-pretax"), "Synthetic tax rate."),
            depreciation_amortization=assumption("10", "USD", ("cash-da",), "Synthetic D&A."),
            capex=assumption("3.5", "USD", ("cash-commitment-capex",), "Synthetic capex."),
            change_in_operating_working_capital=assumption(
                "5", "USD", ("cash-accounts_receivable-current",), "Synthetic working capital."
            ),
        )
        for period, start, end in (
            ("q3", "2026-07-01", "2026-09-30"),
            ("q4", "2026-10-01", "2026-12-31"),
        )
    )
    treatments = {
        "supply": ("assumed_already_reflected", ("operating_income", "working_capital")),
        "cloud": ("assumed_already_reflected", ("operating_income",)),
        "leases": ("zero_disclosed", ()),
        "investments": ("excluded_non_operating", ()),
        "capex": ("assumed_already_reflected", ("capex",)),
        "ai-cloud": ("zero_disclosed", ()),
        "third-party-leases": ("zero_disclosed", ()),
    }
    bridge = CashFlowBridgePackage(
        ticker="NVDA",
        cutoff=snapshot.cutoff,
        author_id="synthetic_cashflow_author",
        case_sha256=digest(case.model_dump(mode="json")),
        evidence_sha256=evidence_snapshot_sha256(snapshot),
        operating_package_sha256=operating_scenario_package_sha256(operating_package),
        historical_anchor=ReportedCashFlowAnchor(
            fiscal_label="Synthetic H1",
            period_start="2026-01-01",
            period_end="2026-06-30",
            operating_income_fact_id="anchor-operating_income",
            income_before_tax_fact_id="cash-pretax",
            income_tax_expense_fact_id="cash-tax",
            depreciation_amortization_fact_id="cash-da",
            capex_cashflow_fact_id="cash-capex",
            operating_cash_flow_fact_id="cash-cfo",
            working_capital_components=tuple(
                WorkingCapitalAnchorComponent(
                    id=metric,
                    metric=metric,
                    current_fact_id=f"cash-{metric}-current",
                    prior_fact_id=f"cash-{metric}-prior",
                    effect="liability" if metric == "accounts_payable" else "asset",
                    rationale="Synthetic reported working-capital pair.",
                )
                for metric in ("accounts_receivable", "inventory", "accounts_payable")
            ),
        ),
        scenarios=(CashFlowScenarioAssumptions(id="base", periods=period_assumptions),),
        commitment_horizon="h2",
        commitment_assumptions=tuple(
            CommitmentOverlapAssumption(
                commitment_id=f"cash-commitment-{identifier}-h2",
                treatment=treatments[identifier][0],
                overlaps=treatments[identifier][1],
                rationale="Synthetic overlap treatment for boundary tests.",
            )
            for identifier, _, _ in commitment_specs
        ),
        limitations=("Synthetic conditional bridge is not economic approval.",),
    )
    if reviewed:
        bridge = _reviewed(bridge)
    return snapshot, case, operating_package, bridge


# Compatibility for early integration branches that imported the preliminary name.
cashflow_setup = bridge_setup


@pytest.mark.parametrize("reviewed", [False, True])
def test_self_contained_bridge_setup_exercises_review_delivery_boundary(tmp_path, reviewed):
    snapshot, case, operating_package, package = bridge_setup(
        tmp_path, reviewed=reviewed
    )
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    result = evaluate_cashflow_bridge(package, case, snapshot, operating)

    assert operating.reviewed
    assert result.reviewed is reviewed
    assert bool(result.calculated_values) is reviewed
    assert result.model_context["context_kind"] == (
        "reviewed_conditional_cash_flow_bridge"
        if reviewed
        else "cash_flow_bridge_audit_only"
    )


def real_setup():
    if not SOURCE.is_dir():
        pytest.skip("frozen NVDA validation packet is unavailable")
    snapshot = EvidenceSnapshot.model_validate(read_json(SOURCE / "evidence.json"))
    case = FinancialCase.model_validate(read_json(SOURCE / "financial_case.json"))
    operating_package = OperatingScenarioPackage.model_validate(
        read_json(SOURCE / "operating_scenario_package.json")
    )
    operating_result = evaluate_operating_scenarios(operating_package, case, snapshot)
    package = build_package(
        case, snapshot, operating_package, operating_result.model_context
    )
    return snapshot, case, operating_result, package


def _reviewed(package: CashFlowBridgePackage) -> CashFlowBridgePackage:
    review = CashFlowBridgeReview(
        reviewer_id="independent_cashflow_test_reviewer",
        reviewed_at="2026-09-20T00:00:00Z",
        package_sha256=cashflow_bridge_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        operating_package_sha256=package.operating_package_sha256,
        decision="conditional_cash_flow_bridge",
        limitations=("Mechanics-only test review; no economic approval.",),
    )
    return package.model_copy(update={"review": review})


def test_unreviewed_bridge_computes_review_artifact_but_exposes_no_numbers():
    snapshot, case, operating, package = real_setup()
    result = evaluate_cashflow_bridge(package, case, snapshot, operating)

    assert not result.reviewed and result.calculated_values == ()
    assert result.model_context["context_kind"] == "cash_flow_bridge_audit_only"
    assert "historical_anchor" not in result.model_context
    assert "scenarios" not in result.model_context
    assert result.model_context["output_scope"]["conditional_cash_flow_bridge"]["status"] == "audit_only"
    payload = parse_json(result.artifacts["cashflow_bridge_result.json"])
    historical = payload["historical_anchor"]
    assert D(historical["change_in_known_row_working_capital"]) == D("29518000000")
    assert D(historical["reported_free_cash_flow"]) == D("69987000000")
    assert D(historical["bridge_cash_flow"]).quantize(D("1")) == D("66036597270")
    totals = {item["id"]: D(item["fiscal_total"]["conditional_cash_flow"])
              for item in payload["scenarios"]}
    assert totals["downside"] < totals["base"] < totals["upside"]
    overlap = payload["commitment_overlap"]["reported_amounts_by_treatment"]
    assert D(overlap["assumed_already_reflected"]) == D("102000000000")
    assert D(overlap["excluded_non_operating"]) == D("18000000000")
    assert all(payload["output_scope"][name]["status"] == "blocked" for name in (
        "operating_asset_value", "equity_per_share_value", "funding_assessment"))


def test_reviewed_bridge_exposes_controlled_calculation_catalog_only():
    snapshot, case, operating, package = real_setup()
    result = evaluate_cashflow_bridge(_reviewed(package), case, snapshot, operating)

    assert result.reviewed
    assert result.model_context["context_kind"] == "reviewed_conditional_cash_flow_bridge"
    assert result.model_context["output_scope"]["conditional_cash_flow_bridge"]["status"] == "conditional"
    assert result.calculated_values
    by_id = {item.id: item for item in result.calculated_values}
    assert "cashflow_bridge.historical.bridge_cash_flow" in by_id
    assert "cashflow_bridge.base.q3.conditional_cash_flow" in by_id
    assert "cashflow_bridge.base.fiscal_total.conditional_cash_flow" in by_id
    assert all(item.valuation_method == "cashflow_bridge" for item in by_id.values())
    assert all(item.share_count_basis == "not_applicable" for item in by_id.values())
    assert set(result.model_context["calculated_value_ids"]) == set(by_id)
    assert "cashflow_bridge_calculated_values.json" in result.artifacts


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit", "EUR"),
        ("currency", "EUR"),
        ("metric", "invented_metric"),
        ("basis", "Adjusted"),
        ("segment", "Data Center"),
        ("period_type", "duration"),
    ],
)
def test_working_capital_pairs_require_exact_us_gaap_whole_company_instant_facts(
    tmp_path, field, value
):
    snapshot, _, _, package = bridge_setup(tmp_path)
    facts = _eligible_fact_map(snapshot)
    identifier = package.historical_anchor.working_capital_components[0].current_fact_id
    facts[identifier] = facts[identifier].model_copy(update={field: value})
    with pytest.raises(ValueError, match="working-capital anchor mismatch"):
        _historical_result(package.historical_anchor, facts)


def test_working_capital_current_fact_cannot_be_swapped_across_metrics(tmp_path):
    snapshot, _, _, package = bridge_setup(tmp_path)
    components = list(package.historical_anchor.working_capital_components)
    receivables = next(item for item in components if item.metric == "accounts_receivable")
    payables = next(item for item in components if item.metric == "accounts_payable")
    components[components.index(receivables)] = receivables.model_copy(
        update={"current_fact_id": payables.current_fact_id}
    )
    anchor = package.historical_anchor.model_copy(
        update={"working_capital_components": tuple(components)}
    )

    with pytest.raises(ValueError, match="working-capital anchor mismatch"):
        _historical_result(anchor, _eligible_fact_map(snapshot))


def test_empty_horizon_cannot_inherit_a_review_for_different_commitments(tmp_path):
    snapshot, case, operating_package, package = bridge_setup(tmp_path, reviewed=True)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    changed = package.model_copy(update={
        "commitment_horizon": "unmatched-label",
        "commitment_assumptions": (),
    })
    result = evaluate_cashflow_bridge(changed, case, snapshot, operating)
    assert not result.reviewed
    assert not result.calculated_values
    assert any("review package hash" in item for item in result.limitations)


def test_decimal_context_cannot_change_tax_or_cash_flow_arithmetic(tmp_path):
    snapshot, case, operating_package, package = bridge_setup(tmp_path)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    normal = evaluate_cashflow_bridge(package, case, snapshot, operating)
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
        constrained = evaluate_cashflow_bridge(package, case, snapshot, operating)
    assert (
        normal.artifacts["cashflow_bridge_result.json"]
        == constrained.artifacts["cashflow_bridge_result.json"]
    )


def test_nvda_package_builder_and_summary_ignore_ambient_decimal_context(tmp_path):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    bridge_result = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    result_payload = parse_json(bridge_result.artifacts["cashflow_bridge_result.json"])
    normal_package = build_package(
        case, snapshot, operating_package, operating.model_context
    )
    normal_summary = _summary(result_payload)

    with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
        constrained_package = build_package(
            case, snapshot, operating_package, operating.model_context
        )
        constrained_summary = _summary(result_payload)

    assert canonical_json(normal_package) == canonical_json(constrained_package)
    assert normal_summary == constrained_summary


def test_unknown_timing_commitment_cannot_become_incremental_cash_deduction(tmp_path):
    snapshot, case, operating_package, package = bridge_setup(tmp_path)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    changed = []
    for item in package.commitment_assumptions:
        if item.commitment_id.endswith("supply-h2"):
            item = CommitmentOverlapAssumption(
                commitment_id=item.commitment_id,
                treatment="incremental_deduction",
                period_id="q3",
                incremental_cash_outflow="1000000000",
                rationale="Synthetic invalid deduction for regression coverage.",
            )
        changed.append(item)
    package = package.model_copy(update={"commitment_assumptions": tuple(changed)})
    with pytest.raises(ValueError, match="unknown-timing commitments"):
        evaluate_cashflow_bridge(package, case, snapshot, operating)


def test_h1_is_not_silently_annualized_and_fiscal_dates_remain_explicit():
    snapshot, case, operating, package = real_setup()
    payload = parse_json(
        evaluate_cashflow_bridge(package, case, snapshot, operating).artifacts[
            "cashflow_bridge_result.json"
        ]
    )
    historical = payload["historical_anchor"]
    assert historical["period_start"] == "2026-01-26"
    assert historical["period_end"] == "2026-07-26"
    assert D(historical["reported_depreciation_amortization"]) == D("2124000000")
    base = next(item for item in payload["scenarios"] if item["id"] == "base")
    assert [(item["period_start"], item["period_end"]) for item in base["periods"]] == [
        ("2026-07-27", "2026-10-25"),
        ("2026-10-26", "2027-01-31"),
    ]
    assert D(base["periods"][0]["depreciation_amortization"]["value"]) == D("1296000000")
