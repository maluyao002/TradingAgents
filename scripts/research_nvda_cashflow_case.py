"""Build an offline NVDA FY27 cash-flow bridge from frozen reviewed artifacts.

No network or model call is made.  The emitted figures are conditional analyst
calculations and the package intentionally carries no fabricated review record.
"""

from __future__ import annotations

import argparse
import hashlib
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

from tradingagents.research.cashflow_bridge import (
    AnalystCashFlowAssumption,
    CashFlowBridgePackage,
    CashFlowScenarioAssumptions,
    CommitmentOverlapAssumption,
    PeriodCashFlowAssumptions,
    ReportedCashFlowAnchor,
    WorkingCapitalAnchorComponent,
    evaluate_cashflow_bridge,
)
from tradingagents.research.contracts import EvidenceSnapshot
from tradingagents.research.financial_case import FinancialCase
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)

D = Decimal
_ARITHMETIC_CONTEXT = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
)
_SOURCE_FILES = (
    "evidence.json",
    "financial_case.json",
    "operating_scenario_package.json",
    "operating_scenario_context.json",
)
_TAX_RATES = {"downside": D(".19"), "base": D(".18"), "upside": D(".17")}
_H2_WORKING_CAPITAL = {
    "downside": D("20000000000"),
    "base": D("17000000000"),
    "upside": D("14000000000"),
}


def _fixed_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_ARITHMETIC_CONTEXT):
        return left * right


def _fixed_divide(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_ARITHMETIC_CONTEXT):
        return left / right


def _require_unchanged_source(source: Path, captured: dict[str, bytes]) -> None:
    """Fail before publication if any captured source artifact changed on disk."""

    for name, expected in captured.items():
        if read_bytes(source / name) != expected:
            raise ValueError(f"source artifact changed during preparation: {name}")


def _assumption(value, unit, evidence_ids, rationale):
    return AnalystCashFlowAssumption(
        value=value,
        unit=unit,
        evidence_ids=evidence_ids,
        rationale=rationale,
    )


def _historical_anchor() -> ReportedCashFlowAnchor:
    rows = (
        (
            "receivables",
            "accounts_receivable",
            "nvda-model-accounts_receivable-q2-fy27-end",
            "nvda-model-accounts_receivable-fy26-end",
            "asset",
        ),
        (
            "inventory",
            "inventory",
            "nvda-model-inventory-q2-fy27-end",
            "nvda-model-inventory-fy26-end",
            "asset",
        ),
        (
            "payables",
            "accounts_payable",
            "nvda-model-accounts_payable-q2-fy27-end",
            "nvda-model-accounts_payable-fy26-end",
            "liability",
        ),
    )
    return ReportedCashFlowAnchor(
        fiscal_label="FY2027 H1 reported",
        period_start="2026-01-26",
        period_end="2026-07-26",
        operating_income_fact_id="nvda-operating_income-h1",
        income_before_tax_fact_id="nvda-income_before_income_tax-h1-fy27",
        income_tax_expense_fact_id="nvda-income_tax_expense-h1",
        depreciation_amortization_fact_id="nvda-depreciation_amortization-h1-fy27",
        capex_cashflow_fact_id="nvda-capex_cashflow-h1",
        operating_cash_flow_fact_id="nvda-operating_cash_flow-h1",
        working_capital_components=tuple(
            WorkingCapitalAnchorComponent(
                id=identifier,
                metric=metric,
                current_fact_id=current,
                prior_fact_id=prior,
                effect=effect,
                rationale=(
                    "Reported balance included in a bounded known-row operating-working-capital "
                    "proxy; mixed and supplemental accrued-liability rows remain excluded and disclosed."
                ),
            )
            for identifier, metric, current, prior, effect in rows
        ),
    )


def _scenarios(operating_context: dict) -> tuple[CashFlowScenarioAssumptions, ...]:
    tax_evidence = (
        "nvda-income_before_income_tax-h1-fy27",
        "nvda-income_tax_expense-h1",
    )
    wc_evidence = (
        "nvda-model-accounts_receivable-q2-fy27-end",
        "nvda-model-accounts_receivable-fy26-end",
        "nvda-model-inventory-q2-fy27-end",
        "nvda-model-inventory-fy26-end",
        "nvda-model-accounts_payable-q2-fy27-end",
        "nvda-model-accounts_payable-fy26-end",
    )
    scenarios = []
    for scenario in operating_context["scenarios"]:
        identifier = scenario["id"]
        working_capital_each = _fixed_divide(_H2_WORKING_CAPITAL[identifier], D(2))
        periods = []
        for period in scenario["periods"]:
            revenue = D(period["inputs"]["revenue"]["value"])
            periods.append(
                PeriodCashFlowAssumptions(
                    period_id=period["id"],
                    period_start=period["period_start"],
                    period_end=period["period_end"],
                    tax_rate=_assumption(
                        _TAX_RATES[identifier],
                        "fraction",
                        tax_evidence,
                        (
                            "Explicit normalized operating-tax assumption. The reported H1 "
                            "consolidated effective rate is an anchor only; it is not silently "
                            "treated as a quarterly or annual operating-tax forecast."
                        ),
                    ),
                    depreciation_amortization=_assumption(
                        _fixed_product(revenue, D(".012")),
                        "USD",
                        ("nvda-depreciation_amortization-h1-fy27",),
                        (
                            "Explicit 1.2% of period revenue assumption, separately adopted near "
                            "the reported H1 ratio; the six-month amount is not annualized."
                        ),
                    ),
                    capex=_assumption(
                        D("3500000000"),
                        "USD",
                        ("nvda-case-capex-fy27-h2",),
                        (
                            "Explicit equal-quarter allocation of the disclosed $7bn remainder-of-"
                            "FY27 capital-expenditure commitment. A commitment is not reported cash "
                            "spend, so this remains an analyst cash-timing assumption."
                        ),
                    ),
                    change_in_operating_working_capital=_assumption(
                        working_capital_each,
                        "USD",
                        wc_evidence,
                        (
                            f"Explicit equal-quarter allocation of the {identifier} H2 working-"
                            "capital investment judgment. It is not extrapolated from H1 and does "
                            "not resolve the excluded mixed balance-sheet rows."
                        ),
                    ),
                )
            )
        scenarios.append(CashFlowScenarioAssumptions(id=identifier, periods=tuple(periods)))
    return tuple(scenarios)


def _commitments() -> tuple[CommitmentOverlapAssumption, ...]:
    return (
        CommitmentOverlapAssumption(
            commitment_id="commitment-supply-fy27-h2",
            treatment="assumed_already_reflected",
            overlaps=("operating_income", "working_capital"),
            rationale=(
                "The $92bn supply/capacity bucket is conditionally treated as product-cost and "
                "inventory/payable activity already represented by operating income and the "
                "working-capital assumption; it is not deducted again."
            ),
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-cloud-fy27-h2",
            treatment="assumed_already_reflected",
            overlaps=("operating_income",),
            rationale=(
                "The $3bn cloud-service bucket is conditionally treated as an operating cost "
                "already represented by the after-SBC operating-income scenario."
            ),
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-uncommenced_leases-fy27-h2",
            treatment="zero_disclosed",
            rationale="The frozen commitment table reports no remainder-of-FY27 amount.",
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-investments-fy27-h2",
            treatment="excluded_non_operating",
            rationale=(
                "The $18bn contingent equity-investment bucket is excluded from operating FCFF, "
                "not ignored for liquidity; it remains a funding and equity-bridge consideration."
            ),
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-capex-fy27-h2",
            treatment="assumed_already_reflected",
            overlaps=("capex",),
            rationale=(
                "The $7bn capital-expenditure bucket is exactly represented by the two explicit "
                "$3.5bn period assumptions and therefore is not deducted twice."
            ),
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-ai_cloud-fy27-h2",
            treatment="zero_disclosed",
            rationale="The frozen additional-commitments table reports no remainder-of-FY27 amount.",
        ),
        CommitmentOverlapAssumption(
            commitment_id="commitment-third_party_leases-fy27-h2",
            treatment="zero_disclosed",
            rationale="The frozen additional-commitments table reports no remainder-of-FY27 amount.",
        ),
    )


def build_package(
    case: FinancialCase,
    snapshot: EvidenceSnapshot,
    operating_package: OperatingScenarioPackage,
    operating_context: dict,
) -> CashFlowBridgePackage:
    return CashFlowBridgePackage(
        ticker="NVDA",
        cutoff=snapshot.cutoff,
        author_id="offline_nvda_cashflow_bridge_preparer",
        case_sha256=digest(case.model_dump(mode="json")),
        evidence_sha256=case.snapshot_sha256,
        operating_package_sha256=operating_scenario_package_sha256(operating_package),
        historical_anchor=_historical_anchor(),
        scenarios=_scenarios(operating_context),
        commitment_horizon="fy27-h2",
        commitment_assumptions=_commitments(),
        limitations=(
            "H1 is reported through July 26, 2026; Q3 and Q4 use their actual fiscal dates through January 31, 2027. No calendar-year translation is performed.",
            "The H1 operating-tax proxy applies the reported consolidated effective rate to operating income; investment gains and discrete tax items prevent treating that rate as pure operating tax.",
            "The H1 strict US-GAAP-labeled working-capital proxy includes accounts receivable, inventory, and accounts payable only; mixed and supplemental accrued-liability rows remain excluded rather than silently classified.",
            "Q3/Q4 tax, D&A, capex cash timing, and working-capital changes are explicit conditional analyst assumptions, not issuer guidance, consensus, probabilities, or annualized H1 facts.",
            "Commitment overlap treatments prevent mechanical double counting but do not establish cancellation rights, exact payment dates, liquidity availability, or funding adequacy.",
            "The $18bn remainder-of-FY27 equity-investment commitment is excluded from operating FCFF but remains a material potential cash use outside this operating bridge.",
            "Gross guarantee exposure is not an expected cash outflow and is not inserted into FCFF; guarantee probability, timing, and funding consequences remain unresolved.",
            "The July 26 reported anchor is not rolled forward to the September 18 local evidence-cutoff date; Q3 is a full conditional fiscal period, not a cutoff-date balance-sheet update.",
            "The frozen operating package has an independent arithmetic/source review, but these cash-flow assumptions do not; no independent external evidence authenticates their economic likelihood.",
        ),
        review=None,
    )


def _billions(value) -> str:
    with localcontext(_ARITHMETIC_CONTEXT):
        scaled = D(value) / D("1000000000")
        return f"{scaled:.3f}"


def _summary(context: dict) -> bytes:
    historical = context["historical_anchor"]
    lines = [
        "# NVDA FY2027 conditional operating cash-flow bridge",
        "",
        "**Status:** conditional analyst calculation; not independently reviewed, not economic approval, and not valuation, equity, or funding clearance.",
        "",
        "## Reported H1 anchor and bridge check",
        "",
        "| Item | USD bn | Basis |",
        "|---|---:|---|",
        f"| Operating income | {_billions(historical['operating_income'])} | Reported US GAAP |",
        f"| Operating tax proxy | ({_billions(historical['operating_tax_proxy'])}) | Reported consolidated ETR applied explicitly to operating income |",
        f"| D&A | {_billions(historical['reported_depreciation_amortization'])} | Reported |",
        f"| Capex | ({_billions(historical['reported_capex_magnitude'])}) | Reported cash outflow magnitude |",
        f"| Change in known-row operating working capital | ({_billions(historical['change_in_known_row_working_capital'])}) | Reported balances; mixed rows excluded |",
        f"| Bridge cash flow | **{_billions(historical['bridge_cash_flow'])}** | Reported anchors plus explicit tax/OWC proxy |",
        f"| Reported CFO less capex | **{_billions(historical['reported_free_cash_flow'])}** | Independent arithmetic check |",
        f"| Bridge variance | {_billions(historical['bridge_minus_reported_free_cash_flow'])} | Unreconciled residual; sources not fully attributed |",
        "",
        "The six-month tax, D&A, capex, and working-capital observations are not annualized. Q3/Q4 amounts below are separately stated assumptions. Known proxy differences do not establish a full reconciliation or attribute the residual.",
        "",
        "## Conditional FY2027 outcomes",
        "",
        "| Case | H2 cash flow (USD bn) | FY2027 bridge cash flow (USD bn) |",
        "|---|---:|---:|",
    ]
    for scenario in context["scenarios"]:
        lines.append(
            f"| {scenario['label']} | {_billions(scenario['future_period_cash_flow'])} | "
            f"**{_billions(scenario['fiscal_total']['conditional_cash_flow'])}** |"
        )
    commitments = context["commitment_overlap"]["reported_amounts_by_treatment"]
    lines.extend(
        [
            "",
            "## Commitment overlap judgment",
            "",
            f"The bridge covers all seven disclosed FY27-H2 commitment rows: USD {_billions(commitments['assumed_already_reflected'])}bn is assumed already represented in operating income, working capital, or capex; USD {_billions(commitments['excluded_non_operating'])}bn of contingent equity investments is outside operating FCFF; and no gross commitment is automatically deducted. This avoids double counting while leaving funding assessment blocked.",
            "",
            "## What still blocks valuation",
            "",
            "- No reviewed discount rate, terminal cash flow, or fiscal-to-long-range forecast connection is supplied by this package.",
            "- Cash/securities availability, debt and lease treatment, point-in-time diluted shares, and the opening-date roll-forward remain incomplete.",
            "- Commitment payment timing, cancellation/adjustment rights, contingent equity-investment funding, and guarantee loss probability remain unresolved.",
            "- The cash-flow assumptions need a fresh independent hash-bound review before they can be described as reviewed; review would still not constitute economic approval.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def prepare(source: Path, output: Path) -> dict:
    """Authenticate the frozen local inputs and publish one new immutable bridge bundle."""

    source, output = source.resolve(), output.resolve()
    if output.exists() or source in output.parents or output == source:
        raise ValueError("output must be a new directory outside the frozen source directory")
    raw = {name: read_bytes(source / name) for name in _SOURCE_FILES}
    snapshot = EvidenceSnapshot.model_validate_json(raw["evidence.json"])
    case = FinancialCase.model_validate_json(raw["financial_case.json"])
    operating_package = OperatingScenarioPackage.model_validate_json(
        raw["operating_scenario_package.json"]
    )
    operating_result = evaluate_operating_scenarios(operating_package, case, snapshot)
    stored_context = parse_json(raw["operating_scenario_context.json"])
    if canonical_json(operating_result.model_context) != canonical_json(stored_context):
        raise ValueError("stored operating-scenario context differs from fresh offline evaluation")
    if not operating_result.reviewed:
        raise ValueError("source operating scenarios are not independently reviewed")

    package = build_package(case, snapshot, operating_package, stored_context)
    result = evaluate_cashflow_bridge(package, case, snapshot, operating_result)
    gaps = {
        "schema_version": 1,
        "classification": "cash_flow_bridge_open_questions_not_blanket_output_block",
        "conditional_cash_flow_available": True,
        "valuation_blockers": [
            "reviewed long-range cash-flow and terminal assumptions",
            "discount-rate and terminal-value connection",
            "complete equity bridge and point-in-time diluted capitalization",
        ],
        "funding_blockers": [
            "commitment cash timing and adjustment/cancellation rights",
            "liquidity availability and investment realizability",
            "contingent equity-investment and guarantee outcomes",
        ],
        "review_gap": (
            "No independent review is represented for the authored cash-flow assumptions or "
            "commitment-overlap judgments."
        ),
    }
    provenance = {
        "schema_version": 1,
        "method": "offline_deterministic_replay_no_network_no_model_calls",
        "source_artifact_sha256": {
            name: hashlib.sha256(content).hexdigest() for name, content in raw.items()
        },
        "case_sha256": package.case_sha256,
        "evidence_sha256": package.evidence_sha256,
        "operating_package_sha256": package.operating_package_sha256,
        "cashflow_package_sha256": result.model_context["package_sha256"],
        "independent_review": None,
        "independent_review_claimed": False,
        "model_calls": 0,
        "network_calls": 0,
    }
    review_payload = parse_json(result.artifacts["cashflow_bridge_result.json"])
    blobs = {
        **result.artifacts,
        "summary.md": _summary(review_payload),
        "gaps.json": canonical_json(gaps),
        "provenance.json": canonical_json(provenance),
    }
    _require_unchanged_source(source, raw)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in blobs.items():
        atomic_write(output / name, content)
    manifest = {
        "schema_version": 1,
        "scope": "conditional_cash_flow_bridge_not_valuation_equity_funding_or_approval",
        "artifact_hashes": {
            name: hashlib.sha256(content).hexdigest() for name, content in blobs.items()
        },
    }
    atomic_write(output / "manifest.json", canonical_json(manifest))
    return {
        "status": "conditional_cash_flow_bridge_created",
        "reviewed": result.reviewed,
        "model_calls": 0,
        "network_calls": 0,
        "output": str(output),
        "package_sha256": result.model_context["package_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(prepare(args.source, args.output))


if __name__ == "__main__":
    main()
