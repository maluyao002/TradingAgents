"""Synthetic, offline controls for optional historical CFO source-row attribution."""

from __future__ import annotations

from decimal import ROUND_DOWN, Context, Decimal, localcontext

import pytest
from pydantic import ValidationError

from tests.test_research_cashflow_bridge import bridge_setup
from tradingagents.research.cashflow_bridge import (
    CashFlowBridgePackage,
    CashFlowBridgeReview,
    HistoricalCashFlowReconciliationSelector,
    HistoricalCashFlowRowSelector,
    _eligible_fact_map,
    _historical_reconciliation,
    _historical_result,
    cashflow_bridge_package_sha256,
    evaluate_cashflow_bridge,
)
from tradingagents.research.contracts import FinancialFact
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    evaluate_operating_scenarios,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import digest, parse_json

D = Decimal
ROWS = (
    ("net_income", "net_income", "410", "positive"),
    ("stock_compensation", "stock_based_compensation", "10", "positive"),
    ("deferred_tax", "deferred_tax", "5", "positive"),
    ("equity_gains", "equity_gains", "-20", "negative"),
    ("other_adjustment", "other_adjustment", "2", "positive"),
    ("receivables_movement", "receivables_movement", "-20", "negative"),
    ("inventory_movement", "inventory_movement", "-10", "negative"),
    ("prepaid_other_assets_movement", "prepaid_other_assets_movement", "-5", "negative"),
    ("payables_movement", "payables_movement", "15", "positive"),
    ("accrued_current_liabilities_movement", "accrued_current_liabilities_movement", "20", "positive"),
    ("other_long_term_liabilities_movement", "other_long_term_liabilities_movement", "3", "positive"),
)


def _setup(tmp_path, *, reviewed=False):
    snapshot, case, operating_package, package = bridge_setup(tmp_path)
    base = {
        "source_id": "filing", "unit": "USD", "currency": "USD", "scale": "1000000",
        "basis": "US GAAP", "period_start": "2026-01-01",
        "period_end": "2026-06-30", "period_type": "duration",
        "location": "Synthetic reported H1 source row",
    }
    facts = tuple(
        FinancialFact(id=f"recon-{role}", metric=metric, value=value, **base)
        for role, metric, value, _ in ROWS
    ) + (
        FinancialFact(id="recon-asset-principal", metric="asset_principal_cashflow",
                      value="-2", **base),
        FinancialFact(id="recon-issuer-fcf", metric="issuer_free_cash_flow", value="398",
                      **{**base, "basis": "issuer non-GAAP FCF"}),
    )
    historical_facts = tuple(
        fact.model_copy(update={"scale": D("1000000")})
        if fact.id.startswith("cash-") and not fact.id.startswith("cash-commitment-")
        else fact
        for fact in snapshot.facts
    )
    snapshot = snapshot.model_copy(update={"facts": (*historical_facts, *facts)})
    evidence_hash = evidence_snapshot_sha256(snapshot)
    case = case.model_copy(update={"snapshot_sha256": evidence_hash})
    case_hash = digest(case.model_dump(mode="json"))
    operating_package = operating_package.model_copy(update={
        "case_sha256": case_hash, "evidence_sha256": evidence_hash, "review": None,
    })
    operating_package = OperatingScenarioPackage.model_validate({
        **operating_package.model_dump(mode="json"),
        "review": {
            "reviewer_id": "independent_operating_reviewer",
            "reviewed_at": "2026-09-20T00:00:00Z",
            "package_sha256": operating_scenario_package_sha256(operating_package),
            "case_sha256": case_hash, "evidence_sha256": evidence_hash,
            "decision": "conditional_operating_scenarios",
            "limitations": ["Synthetic arithmetic review is not economic approval."],
        },
    })
    selector = HistoricalCashFlowReconciliationSelector(
        anchor_source_id="filing", cashflow_statement_source_id="filing",
        rows=tuple(
            HistoricalCashFlowRowSelector(role=role, fact_id=f"recon-{role}", sign=sign)
            for role, _, _, sign in ROWS
        ),
        asset_principal_cashflow_fact_id="recon-asset-principal",
        issuer_free_cash_flow_fact_id="recon-issuer-fcf",
    )
    package = CashFlowBridgePackage.model_validate({
        **package.model_dump(mode="json"),
        "case_sha256": case_hash, "evidence_sha256": evidence_hash,
        "operating_package_sha256": operating_scenario_package_sha256(operating_package),
        "historical_reconciliation": selector.model_dump(mode="json"),
        "review": None,
    })
    if reviewed:
        package = package.model_copy(update={
            "review": CashFlowBridgeReview(
                reviewer_id="independent_cashflow_reviewer",
                reviewed_at="2026-09-20T00:00:00Z",
                package_sha256=cashflow_bridge_package_sha256(package),
                case_sha256=case_hash, evidence_sha256=evidence_hash,
                operating_package_sha256=package.operating_package_sha256,
                decision="conditional_cash_flow_bridge",
                limitations=("Synthetic source-row mechanics only, not economic approval.",),
            )
        })
    return snapshot, case, operating_package, package


def _reconcile(snapshot, package):
    facts = _eligible_fact_map(snapshot)
    historical = _historical_result(package.historical_anchor, facts)
    return _historical_reconciliation(
        package.historical_reconciliation, package.historical_anchor, historical, facts
    )


def test_exact_source_rows_preserve_nonzero_proxy_residual_and_separate_issuer_fcf(tmp_path):
    snapshot, case, operating_package, package = _setup(tmp_path)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    result = evaluate_cashflow_bridge(package, case, snapshot, operating)
    assert not result.reviewed and not result.calculated_values
    historical = parse_json(result.artifacts["cashflow_bridge_result.json"])["historical_anchor"]
    reconciliation = historical["reconciliation"]
    assert reconciliation["status"] == "mechanically_attributed_not_economic_approval"
    assert reconciliation["source_rows"]["equity_gains"]["source_location"] == (
        "Synthetic reported H1 source row"
    )
    assert D(reconciliation["reconstructed_operating_cash_flow"]) == D("430000000")
    assert D(reconciliation["ocf_minus_capex"]) == D("400000000")
    assert D(reconciliation["issuer_free_cash_flow"]) == D("398000000")
    assert D(reconciliation["issuer_fcf_less_ocf_minus_capex"]) == D("-2000000")
    assert {
        name: D(component["value"])
        for name, component in reconciliation["residual_components"].items()
    } == {
        "tax_proxy_less_net_income": D("-10000000"),
        "minus_omitted_noncash_adjustments": D("3000000"),
        "balance_sheet_proxy_less_reported_movements": D("-28000000"),
    }
    assert D(reconciliation["bridge_minus_ocf_minus_capex"]) == D("-35000000")
    assert D(historical["bridge_minus_reported_free_cash_flow"]) == D("-35000000")
    assert reconciliation["residual_components"]["balance_sheet_proxy_less_reported_movements"]["fact_ids"]
    assert all(result.model_context["output_scope"][scope]["status"] == "blocked"
               for scope in ("operating_asset_value", "equity_per_share_value", "funding_assessment"))


def test_review_gates_new_values_and_changed_selection_invalidates_review(tmp_path):
    snapshot, case, operating_package, package = _setup(tmp_path, reviewed=True)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    result = evaluate_cashflow_bridge(package, case, snapshot, operating)
    assert result.reviewed
    values = {item.id: item for item in result.calculated_values}
    prefix = "cashflow_bridge.historical.reconciliation."
    assert D(values[prefix + "issuer_fcf_less_ocf_minus_capex"].value) == D("-2000000")
    assert set(values[prefix + "tax_proxy_less_net_income"].evidence_ids) == {
        "anchor-operating_income", "cash-pretax", "cash-tax", "recon-net_income",
    }
    rows = list(package.historical_reconciliation.rows)
    reordered = package.model_copy(update={"historical_reconciliation":
        package.historical_reconciliation.model_copy(update={
            "rows": (rows[1], rows[0], *rows[2:]),
        })})
    assert cashflow_bridge_package_sha256(reordered) != cashflow_bridge_package_sha256(package)
    stale = evaluate_cashflow_bridge(reordered, case, snapshot, operating)
    assert not stale.reviewed and not stale.calculated_values
    assert any("review package hash" in item for item in stale.limitations)
    rows[0] = rows[0].model_copy(update={"sign": "negative"})
    changed = package.model_copy(update={"historical_reconciliation":
        package.historical_reconciliation.model_copy(update={"rows": tuple(rows)})})
    assert cashflow_bridge_package_sha256(changed) != cashflow_bridge_package_sha256(package)
    with pytest.raises(ValueError, match="sign mismatch"):
        evaluate_cashflow_bridge(changed, case, snapshot, operating)


def test_old_package_hash_and_review_remain_valid_without_optional_field(tmp_path):
    snapshot, case, operating_package, package = bridge_setup(tmp_path, reviewed=True)
    original = package.model_dump(mode="json", exclude={"review", "historical_reconciliation"})
    assert cashflow_bridge_package_sha256(package) == digest(original)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    result = evaluate_cashflow_bridge(package, case, snapshot, operating)
    assert result.reviewed
    assert "reconciliation" not in parse_json(result.artifacts["cashflow_bridge_result.json"])["historical_anchor"]


@pytest.mark.parametrize("change, message", [
    ({"metric": "inventory_movement"}, "selector mismatch"),
    ({"unit": "EUR"}, "selector mismatch"),
    ({"currency": "EUR"}, "selector mismatch"),
    ({"basis": "Adjusted"}, "selector mismatch"),
    ({"period_start": "2026-01-02"}, "selector mismatch"),
    ({"period_end": "2026-06-29"}, "selector mismatch"),
    ({"source_id": "changed-source"}, "source mismatch"),
    ({"value": D("20")}, "sign mismatch"),
    ({"value": D("-21")}, "CFO subtotal mismatch"),
])
def test_source_row_rejects_wrong_metric_period_unit_source_sign_or_subtotal(
    tmp_path, change, message
):
    snapshot, _, _, package = _setup(tmp_path)
    facts = _eligible_fact_map(snapshot)
    identifier = "recon-receivables_movement"
    facts[identifier] = facts[identifier].model_copy(update=change)
    historical = _historical_result(package.historical_anchor, facts)
    with pytest.raises(ValueError, match=message):
        _historical_reconciliation(
            package.historical_reconciliation, package.historical_anchor, historical, facts
        )


def test_complete_roles_duplicate_rows_and_swapped_anchors_fail(tmp_path):
    snapshot, _, _, package = _setup(tmp_path)
    selector = package.historical_reconciliation
    rows = list(selector.rows)
    with pytest.raises(ValidationError, match="each source row role exactly once"):
        HistoricalCashFlowReconciliationSelector.model_validate({
            **selector.model_dump(mode="json"),
            "rows": [*rows[:-1], rows[0]],
        })
    with pytest.raises(ValidationError, match="cannot be reused across roles"):
        HistoricalCashFlowReconciliationSelector.model_validate({
            **selector.model_dump(mode="json"),
            "rows": [*rows[:-1], rows[-1].model_copy(update={"fact_id": rows[0].fact_id})],
        })
    rows[0] = rows[0].model_copy(update={"fact_id": "cash-cfo"})
    swapped = selector.model_copy(update={"rows": tuple(rows)})
    facts = _eligible_fact_map(snapshot)
    historical = _historical_result(package.historical_anchor, facts)
    with pytest.raises(ValueError, match="reuses a different anchor role"):
        _historical_reconciliation(swapped, package.historical_anchor, historical, facts)


@pytest.mark.parametrize("identifier,change,message", [
    ("recon-issuer-fcf", {"value": D("399")}, "issuer FCF differs"),
    ("recon-asset-principal", {"value": D("2")}, "negative-outflow"),
    ("recon-issuer-fcf", {"basis": "US GAAP"}, "issuer FCF selector mismatch"),
])
def test_issuer_fcf_identity_and_basis_are_checked(tmp_path, identifier, change, message):
    snapshot, _, _, package = _setup(tmp_path)
    facts = _eligible_fact_map(snapshot)
    facts[identifier] = facts[identifier].model_copy(update=change)
    historical = _historical_result(package.historical_anchor, facts)
    with pytest.raises(ValueError, match=message):
        _historical_reconciliation(
            package.historical_reconciliation, package.historical_anchor, historical, facts
        )


def test_reconciliation_decimal_arithmetic_ignores_ambient_context(tmp_path):
    snapshot, _, _, package = _setup(tmp_path)
    normal = _reconcile(snapshot, package)
    with localcontext(Context(prec=5, rounding=ROUND_DOWN)):
        constrained = _reconcile(snapshot, package)
    assert normal == constrained


def test_fixed_precision_source_statement_decomposition_without_report_fixture(tmp_path):
    snapshot, _, _, package = _setup(tmp_path)
    facts = _eligible_fact_map(snapshot)
    source_millions = {
        "anchor-operating_income": "117270",
        "cash-pretax": "141410", "cash-tax": "23400", "cash-da": "2124",
        "cash-capex": "-4434", "cash-cfo": "74421",
        "cash-accounts_receivable-current": "24593",
        "cash-accounts_receivable-prior": "0",
        "cash-inventory-current": "10172", "cash-inventory-prior": "0",
        "cash-accounts_payable-current": "5247",
        "cash-accounts_payable-prior": "0",
        "recon-net_income": "118010", "recon-stock_compensation": "3954",
        "recon-deferred_tax": "982", "recon-equity_gains": "-23707",
        "recon-other_adjustment": "222",
        "recon-receivables_movement": "-24590",
        "recon-inventory_movement": "-10204",
        "recon-prepaid_other_assets_movement": "-6480",
        "recon-payables_movement": "4125",
        "recon-accrued_current_liabilities_movement": "8015",
        "recon-other_long_term_liabilities_movement": "1970",
        "recon-asset-principal": "-92", "recon-issuer-fcf": "69895",
    }
    for identifier, value in source_millions.items():
        facts[identifier] = facts[identifier].model_copy(update={
            "value": D(value), "scale": D("1000000"),
        })

    def calculate():
        historical = _historical_result(package.historical_anchor, facts)
        return historical, _historical_reconciliation(
            package.historical_reconciliation, package.historical_anchor, historical, facts
        )

    historical, reconciliation = calculate()
    expected = {
        "tax_proxy_less_net_income": D(
            "-20145402729.65136836150201541616575914008910260943356198288664168022063503288310529880000000"
        ),
        "minus_omitted_noncash_adjustments": D("18549000000"),
        "balance_sheet_proxy_less_reported_movements": D("-2354000000"),
    }
    assert {name: item["value"] for name, item in reconciliation["residual_components"].items()} == expected
    assert reconciliation["bridge_minus_ocf_minus_capex"] == D(
        "-3950402729.65136836150201541616575914008910260943356198288664168022063503288310529880000000"
    )
    assert reconciliation["bridge_minus_ocf_minus_capex"] == historical[
        "bridge_minus_reported_free_cash_flow"
    ]
    assert reconciliation["reconstructed_operating_cash_flow"] == D("74421000000")
    assert reconciliation["issuer_fcf_less_ocf_minus_capex"] == D("-92000000")
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
        assert calculate() == (historical, reconciliation)
