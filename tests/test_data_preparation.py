from __future__ import annotations

import json
import re
from unittest import mock

import pytest

from tradingagents.dataflows import (
    alpha_vantage_fundamentals as alpha,
    fred,
    interface,
    preparation,
)
from tradingagents.dataflows.financial_calculations import parse_decimal
from tradingagents.dataflows.request_cache import data_request_scope


@pytest.fixture(autouse=True)
def _current_preparation_date(monkeypatch):
    """Most preparation fixtures represent a live/current-date request."""
    monkeypatch.setattr(preparation.date_window, "get_current_date", lambda: "2025-12-31")


@pytest.mark.unit
def test_market_failure_does_not_retain_sensitive_exception_details(monkeypatch):
    def fail(*args):
        raise RuntimeError("request failed api_key=SYNTHETIC_SECRET")

    monkeypatch.setattr(preparation, "build_verified_market_snapshot_data", fail)
    prepared = preparation.prepare_market("TEST", "2025-12-31")
    assert prepared["caveats"] == ["Verified market data unavailable (RuntimeError)."]
    assert "SYNTHETIC_SECRET" not in json.dumps(prepared)


def _alpha_statement(method: str, revenue: str = "100") -> str:
    common = {"fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD"}
    if method == "get_income_statement":
        report = {
            **common, "totalRevenue": revenue, "grossProfit": "40",
            "operatingIncome": "20", "netIncome": "10",
        }
    elif method == "get_balance_sheet":
        report = {**common, "totalCurrentAssets": "200", "totalCurrentLiabilities": "100"}
    else:
        report = {**common, "operatingCashflow": "100", "capitalExpenditures": "20"}
    quarterly = {**report, "fiscalDateEnding": "2025-09-30"}
    return json.dumps({"annualReports": [report], "quarterlyReports": [quarterly]})


def _alpha_router(revenue: str = "100"):
    def route(method, ticker, *args):
        if method == "get_fundamentals":
            value = json.dumps({
                "MarketCapitalization": "5000", "PERatio": "25",
                "ForwardPE": "20", "EPS": "4",
            })
        else:
            value = _alpha_statement(method, revenue)
        return interface.RoutedVendorResult(value, "alpha_vantage", method)
    return route


@pytest.mark.unit
def test_alpha_statement_frequencies_share_one_raw_request():
    calls = 0

    def request(function_name, params):
        nonlocal calls
        calls += 1
        return json.dumps({
            "annualReports": [{"fiscalDateEnding": "2025-12-31"}],
            "quarterlyReports": [{"fiscalDateEnding": "2025-09-30"}],
        })

    with mock.patch.object(interface, "get_vendor", return_value="alpha_vantage"), \
            mock.patch.object(alpha, "_make_api_request", side_effect=request), \
            data_request_scope():
        annual = interface.route_to_vendor("get_balance_sheet", "AAPL", "annual", "2025-12-31")
        quarterly = interface.route_to_vendor(
            "get_balance_sheet", ticker="aapl", freq="quarterly", curr_date="2025-12-31"
        )

    assert annual == quarterly
    assert "annualReports" in annual and "quarterlyReports" in annual
    assert calls == 1


@pytest.mark.unit
def test_prepare_fundamentals_preserves_sources_and_safe_calculations(monkeypatch):
    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", _alpha_router())
    prepared = preparation.prepare_fundamentals("AAPL", "2025-12-31")

    assert prepared["analysis_date"] == "2025-12-31"
    assert len(prepared["sources"]) == 4  # overview + three deduplicated Alpha payloads
    assert all(source["published_at"] is None for source in prepared["sources"])
    assert all(source["id"].startswith("fundamentals-") for source in prepared["sources"])
    assert all(re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", fact["id"])
               for fact in prepared["facts"])
    assert all(fact["basis"] == "not_disclosed" for fact in prepared["facts"])
    assert any("not publication dates" in caveat for caveat in prepared["caveats"])

    by_metric = {}
    for fact in prepared["facts"]:
        by_metric.setdefault(fact["metric"], []).append(fact)
    assert by_metric["market_cap"][0]["value"] == 5000
    assert by_metric["gross_margin"][0]["value"] == pytest.approx(0.4)
    assert by_metric["current_ratio"][0]["value"] == 2
    assert by_metric["free_cash_flow"][0]["value"] == 80
    assert "forecast methodology" in by_metric["forward_pe"][0]["caveats"][0]
    assert by_metric["gross_margin"][0]["period"].startswith("annual:")
    assert len(by_metric["gross_margin"][0]["inputs"]) == 2


@pytest.mark.unit
def test_zero_revenue_skips_undefined_margins_with_explicit_caveat(monkeypatch):
    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", _alpha_router("0"))
    prepared = preparation.prepare_fundamentals("AAPL", "2025-12-31")
    assert not any(fact["metric"].endswith("margin") for fact in prepared["facts"])
    assert any("Revenue is zero" in caveat for caveat in prepared["caveats"])


@pytest.mark.unit
def test_exact_comparable_growth_and_loss_to_profit_change(monkeypatch):
    def route(method, ticker, *args):
        if method == "get_fundamentals":
            value = "{}"
        elif method == "get_income_statement":
            value = json.dumps({"annualReports": [
                {"fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD",
                 "totalRevenue": "120", "operatingIncome": "10"},
                {"fiscalDateEnding": "2024-12-31", "reportedCurrency": "USD",
                 "totalRevenue": "100", "operatingIncome": "-5"},
            ], "quarterlyReports": []})
        elif method == "get_balance_sheet":
            value = json.dumps({"annualReports": [{
                "fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD",
                "cashAndShortTermInvestments": "50", "inventory": "7",
                "longTermDebtNoncurrent": "30", "intangibleAssets": "12",
                "commonStockSharesOutstanding": "1000",
            }], "quarterlyReports": []})
        else:
            value = json.dumps({"annualReports": [{
                "fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD",
                "paymentsForRepurchaseOfCommonStock": "8",
                "proceedsFromStockOptions": "3",
            }], "quarterlyReports": []})
        return interface.RoutedVendorResult(value, "alpha_vantage", method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AMD", "2025-12-31")
    facts = {fact["metric"]: fact for fact in prepared["facts"]}
    assert facts["revenue_yoy_growth"]["value"] == pytest.approx(0.2)
    assert facts["operating_income_yoy_change"]["value"] == 15
    assert "percent growth would be misleading" in facts[
        "operating_income_yoy_change"
    ]["caveats"][0]
    assert "operating_income_yoy_growth" not in facts
    assert facts["shares_outstanding"]["unit"] == "shares"
    for metric in (
        "cash_and_short_term_investments", "inventory", "long_term_debt",
        "intangible_assets", "common_stock_repurchases", "stock_option_proceeds",
    ):
        assert metric in facts


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "expected_count"),
    [("25", "25.0", 1), ("25", "30", 0)],
)
def test_alpha_aliases_consolidate_equal_values_and_withhold_conflicts(
    monkeypatch, first, second, expected_count
):
    def route(method, ticker, *args):
        if method == "get_fundamentals":
            value = "{}"
        elif method == "get_balance_sheet":
            value = json.dumps({"annualReports": [{
                "fiscalDateEnding": "2025-12-31", "reportedCurrency": "USD",
                "currentLongTermDebt": first, "longTermDebtCurrent": second,
            }], "quarterlyReports": []})
        else:
            value = json.dumps({"annualReports": [], "quarterlyReports": []})
        return interface.RoutedVendorResult(value, "alpha_vantage", method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AMD", "2025-12-31")
    matching = [
        fact for fact in prepared["facts"] if fact["metric"] == "current_long_term_debt"
    ]
    assert len(matching) == expected_count
    if expected_count:
        assert matching[0]["value"] == 25
    else:
        assert any(
            "currentLongTermDebt=25" in caveat
            and "longTermDebtCurrent=30" in caveat
            and "canonical fact was withheld" in caveat
            for caveat in prepared["caveats"]
        )


@pytest.mark.unit
def test_yfinance_conflicting_aliases_do_not_feed_current_ratio(monkeypatch):
    def route(method, ticker, *args):
        if method == "get_fundamentals":
            value = "# Company Fundamentals for AMD"
        elif method == "get_balance_sheet":
            value = (
                ",2025-12-31\nCurrent Assets,200\nTotal Current Assets,250\n"
                "Current Liabilities,100\n"
            )
        else:
            value = ",2025-12-31\n"
        return interface.RoutedVendorResult(value, "yfinance", method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AMD", "2025-12-31")
    assert not any(fact["metric"] == "current_assets" for fact in prepared["facts"])
    assert not any(fact["metric"] == "current_ratio" for fact in prepared["facts"])
    assert any(
        "Current Assets=200" in caveat and "Total Current Assets=250" in caveat
        for caveat in prepared["caveats"]
    )
    valid_dependencies = {
        source["id"] for source in prepared["sources"]
    } | {fact["id"] for fact in prepared["facts"]}
    assert all(
        dependency in valid_dependencies
        for fact in prepared["facts"] for dependency in fact["inputs"]
    )


def _summary_router(*, mixed_repurchase_sign: bool = False, omit_q1_capex: bool = False):
    quarterly_dates = "2025-06-30,2025-03-31,2024-12-31,2024-09-30,2024-06-30"

    def route(method, ticker, *args):
        frequency = args[0] if args else None
        if method == "get_fundamentals":
            value = "# Company Fundamentals for AMD"
        elif method == "get_balance_sheet":
            value = f",{quarterly_dates}\n" if frequency == "quarterly" else ",2024-12-31\n"
        elif method == "get_cashflow" and frequency == "quarterly":
            repurchases = "-200,50,-100,-400,-500" if mixed_repurchase_sign else "-200,-300,-100,-400,-500"
            q1_capex = "" if omit_q1_capex else "-400"
            value = (
                f",{quarterly_dates}\n"
                f"Repurchase Of Capital Stock,{repurchases}\n"
                "Stock Based Compensation,500,400,300,200,100\n"
                f"Capital Expenditure,-800,{q1_capex},-200,-250,-300\n"
            )
        elif method == "get_cashflow":
            value = ",2024-12-31\nCapital Expenditure,-900\n"
        elif method == "get_income_statement" and frequency == "quarterly":
            value = (
                f",{quarterly_dates}\n"
                "Basic Average Shares,110,108,106,104,100\n"
                "Diluted Average Shares,120,118,116,114,110\n"
            )
        else:
            value = (
                ",2024-12-31\nOperating Income,2086\n"
                "Total Operating Income As Reported,1900\n"
                "Restructuring And Mergern Acquisition,186\n"
            )
        return interface.RoutedVendorResult(value, "yfinance", method)

    return route


@pytest.mark.unit
def test_material_financial_summaries_and_required_dependencies(monkeypatch):
    monkeypatch.setattr(
        preparation, "route_to_vendor_with_metadata", _summary_router()
    )
    prepared = preparation.prepare_fundamentals("AMD", "2025-12-31")
    by_metric = {fact["metric"]: fact for fact in prepared["facts"]}

    assert by_metric["ttm_common_stock_repurchases_magnitude"]["value"] == 1000
    assert by_metric["ttm_stock_based_compensation"]["value"] == 1400
    assert by_metric["basic_average_shares_yoy_change"]["value"] == pytest.approx(0.10)
    assert by_metric["diluted_average_shares_yoy_change"]["value"] == pytest.approx(10 / 110)
    assert by_metric["ytd_capital_expenditures_magnitude"]["value"] == 1200
    prior_capex = by_metric["prior_full_year_capital_expenditures_magnitude"]
    assert prior_capex["value"] == 900
    assert "not like-for-like" in prior_capex["caveats"][1]
    bridge = by_metric["operating_income_bridge_adjustment"]
    assert bridge["value"] == 186
    assert len(bridge["inputs"]) == 3
    assert by_metric["operating_income"]["value"] == 2086
    assert by_metric["total_operating_income_as_reported"]["value"] == 1900

    required = prepared["required_evidence_ids"]
    assert len(required) <= 48
    assert len(required) == len(set(required))
    by_id = {fact["id"]: fact for fact in prepared["facts"]}
    assert all(item in by_id for item in required)
    for metric in (
        "ttm_common_stock_repurchases_magnitude", "ttm_stock_based_compensation",
        "basic_average_shares_yoy_change", "diluted_average_shares_yoy_change",
        "ytd_capital_expenditures_magnitude",
        "prior_full_year_capital_expenditures_magnitude",
        "operating_income_bridge_adjustment",
    ):
        assert by_metric[metric]["id"] in required
        assert all(dependency in required for dependency in by_metric[metric]["inputs"])


@pytest.mark.unit
def test_material_summaries_withhold_mixed_sign_and_incomplete_quarters(monkeypatch):
    monkeypatch.setattr(
        preparation,
        "route_to_vendor_with_metadata",
        _summary_router(mixed_repurchase_sign=True, omit_q1_capex=True),
    )
    prepared = preparation.prepare_fundamentals("AMD", "2025-12-31")
    metrics = {fact["metric"] for fact in prepared["facts"]}
    assert "ttm_common_stock_repurchases_magnitude" not in metrics
    assert "ytd_capital_expenditures_magnitude" not in metrics
    assert any("positive yfinance value" in caveat for caveat in prepared["caveats"])
    assert any("calendar-quarter sequence is incomplete" in caveat
               for caveat in prepared["caveats"])


def _cashflow_reconciliation_router():
    def route(method, ticker, *args):
        frequency = args[0] if args else None
        if method == "get_fundamentals":
            value = "\n".join([
                "# Company Fundamentals for AMD",
                "Free Cash Flow: 8841499648",
            ])
        elif method == "get_cashflow" and frequency == "annual":
            value = (
                ",2025-12-31\n"
                "Operating Cash Flow,7709000000\n"
                "Cash Flow From Continuing Operating Activities,6493000000\n"
                "Cash From Discontinued Operating Activities,1216000000\n"
                "Capital Expenditure,-974000000\n"
                "Free Cash Flow,6735000000\n"
            )
        elif method == "get_cashflow":
            value = (
                ",2026-06-30,2026-03-31,2025-12-31,2025-09-30\n"
                "Free Cash Flow,1558000000,2566000000,2378000000,1901000000\n"
                "Operating Cash Flow,2366000000,2955000000,2600000000,2159000000\n"
                "Cash Flow From Continuing Operating Activities,2366000000,2955000000,"
                "2304000000,1788000000\n"
                "Cash From Discontinued Operating Activities,,,296000000,371000000\n"
                "Capital Expenditure,-808000000,-389000000,-222000000,-258000000\n"
            )
        elif method == "get_income_statement" and frequency == "quarterly":
            value = (
                ",2026-06-30,2025-06-30\n"
                "Total Revenue,11536000000,7685000000\n"
                "Gross Profit,6203000000,3059000000\n"
                "Operating Income,1990000000,-134000000\n"
                "Net Income,2297000000,872000000\n"
            )
        else:
            value = f",no-data-{frequency or 'overview'}\n"
        return interface.RoutedVendorResult(value, "yfinance", method)

    return route


@pytest.mark.unit
def test_cashflow_reconciliations_and_profitability_pairs_are_required(monkeypatch):

    monkeypatch.setattr(
        preparation, "route_to_vendor_with_metadata", _cashflow_reconciliation_router()
    )
    prepared = preparation.prepare_fundamentals("AMD", "2026-09-13")
    by_metric = {}
    for fact in prepared["facts"]:
        by_metric.setdefault(fact["metric"], []).append(fact)

    assert by_metric["continuing_operations_operating_cash_flow"][0]["value"] == 6493000000
    assert by_metric["discontinued_operations_operating_cash_flow"][0]["value"] == 1216000000
    bridge = by_metric["operating_cash_flow_reconciliation"][0]
    assert bridge["value"] == 7709000000
    assert len(bridge["inputs"]) == 5
    assert "Same-period annual free cash flow is 6735000000" in bridge["caveats"][0]
    assert "no continuing-operations free cash flow is inferred" in bridge["caveats"][0]
    assert "provider-signed capital expenditures -974000000 equals 6735000000" \
        in bridge["caveats"][0]

    four_quarters = by_metric["latest_four_quarters_free_cash_flow"][0]
    assert four_quarters["value"] == 8403000000
    assert four_quarters["period"] == "four_quarters:2025-09-30..2026-06-30"
    assert len(four_quarters["inputs"]) == 12
    discontinued_caveat = four_quarters["caveats"][1]
    assert "2025-09-30=371000000" in discontinued_caveat
    assert "2025-12-31=296000000" in discontinued_caveat
    assert "disclosed total 667000000" in discontinued_caveat
    assert "unavailable for 2026-03-31, 2026-06-30" in discontinued_caveat
    assert "missing values were not treated as zero" in discontinued_caveat
    assert "no continuing-operations free cash flow is inferred" in discontinued_caveat
    assert "not subtracted from the four-quarter total" in discontinued_caveat
    comparison = by_metric["free_cash_flow_overview_comparison_difference"][0]
    assert comparison["value"] == -438499648
    assert comparison["inputs"] == [
        four_quarters["id"], by_metric["overview_free_cash_flow"][0]["id"]
    ]
    assert "observation horizon and endpoint are unknown" in comparison["caveats"][0]
    assert "does not establish a contradiction or provider error" in comparison["caveats"][0]
    assert any("total 7709000000 equals continuing operations 6493000000"
               in caveat for caveat in prepared["caveats"])
    assert any("Dated statement free cash flow totals 8403000000"
               in caveat for caveat in prepared["caveats"])

    required = set(prepared["required_evidence_ids"])
    by_id = {fact["id"]: fact for fact in prepared["facts"]}
    for summary in (bridge, four_quarters, comparison):
        assert summary["id"] in required
        assert all(dependency in required for dependency in summary["inputs"])
    required_discontinued = [
        fact for fact in by_metric["discontinued_operations_operating_cash_flow"]
        if fact["period"] in {"quarterly:2025-09-30", "quarterly:2025-12-31"}
    ]
    assert len(required_discontinued) == 2
    assert all(fact["id"] in required for fact in required_discontinued)
    annual_context = [
        fact for fact in prepared["facts"]
        if fact["period"] == "annual:2025-12-31"
        and fact["metric"] in {"free_cash_flow", "capital_expenditures"}
    ]
    assert {fact["metric"] for fact in annual_context} == {
        "free_cash_flow", "capital_expenditures"
    }
    assert all(fact["id"] in required for fact in annual_context)
    for metric in ("gross_margin", "operating_margin", "net_income"):
        comparison_periods = {
            fact["period"] for fact in by_metric[metric] if fact["id"] in required
        }
        assert {"quarterly:2026-06-30", "quarterly:2025-06-30"} <= comparison_periods
        for fact in by_metric[metric]:
            if fact["period"] in comparison_periods:
                assert all(dependency in required for dependency in fact["inputs"])
    assert all(dependency in by_id for fact in prepared["facts"]
               for dependency in fact["inputs"])


@pytest.mark.unit
def test_cashflow_reconciliation_withholds_missing_component_and_cross_vendor_comparison(
    monkeypatch,
):
    def route(method, ticker, *args):
        frequency = args[0] if args else None
        if method == "get_fundamentals":
            return interface.RoutedVendorResult(
                "# Company Fundamentals for AMD\nFree Cash Flow: 400",
                "yfinance", method,
            )
        if method == "get_cashflow" and frequency == "annual":
            return interface.RoutedVendorResult(
                ",2025-12-31\nOperating Cash Flow,300\n"
                "Cash Flow From Continuing Operating Activities,250\n",
                "yfinance", method,
            )
        if method == "get_cashflow":
            reports = [
                {"fiscalDateEnding": period, "reportedCurrency": "USD",
                 "operatingCashflow": str(operating), "capitalExpenditures": "20"}
                for period, operating in (
                    ("2026-06-30", 120), ("2026-03-31", 110),
                    ("2025-12-31", 100), ("2025-09-30", 90),
                )
            ]
            return interface.RoutedVendorResult(
                json.dumps({"annualReports": [], "quarterlyReports": reports}),
                "alpha_vantage", method,
            )
        vendor = "alpha_vantage" if frequency == "quarterly" else "yfinance"
        empty = json.dumps({"annualReports": [], "quarterlyReports": []}) \
            if vendor == "alpha_vantage" else ",2025-12-31\n"
        return interface.RoutedVendorResult(empty, vendor, method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AMD", "2026-09-13")
    metrics = {fact["metric"] for fact in prepared["facts"]}
    assert "operating_cash_flow_reconciliation" not in metrics
    assert "latest_four_quarters_free_cash_flow" in metrics
    assert "free_cash_flow_overview_comparison_difference" not in metrics
    assert any("discontinued_operations_operating_cash_flow is unavailable"
               in caveat and "not treated as zero" in caveat
               for caveat in prepared["caveats"])
    assert any("no unique same-vendor, same-unit pair" in caveat
               for caveat in prepared["caveats"])

    summary = next(fact for fact in prepared["facts"]
                   if fact["metric"] == "latest_four_quarters_free_cash_flow")
    assert "Discontinued-operations operating cash flow is unavailable for" \
        in summary["caveats"][1]
    assert "missing values were not treated as zero" in summary["caveats"][1]
    assert "no continuing-operations free cash flow is inferred" in summary["caveats"][1]
    required = set(prepared["required_evidence_ids"])
    by_id = {fact["id"]: fact for fact in prepared["facts"]}
    assert summary["id"] in required
    assert all(dependency in required for dependency in summary["inputs"])
    for quarterly_fcf_id in summary["inputs"]:
        assert all(dependency in required
                   for dependency in by_id[quarterly_fcf_id]["inputs"])


@pytest.mark.unit
def test_required_profitability_pairs_use_newest_complete_source_unit_series():
    facts = []
    for metric in ("gross_margin", "operating_margin", "net_income"):
        for source_id, unit, period, value in (
            ("fundamentals-income_statement:yfinance:new", "reported_currency",
             "2026-06-30", 30),
            # A nearby date must not stand in for the missing exact prior year.
            ("fundamentals-income_statement:yfinance:new", "reported_currency",
             "2025-05-31", 20),
            ("fundamentals-income_statement:alpha_vantage:complete", "USD",
             "2026-03-31", 25),
            ("fundamentals-income_statement:alpha_vantage:complete", "USD",
             "2025-03-31", 15),
        ):
            facts.append(preparation._fact(
                metric=metric, value=value,
                unit="ratio" if metric.endswith("margin") else unit,
                period=f"quarterly:{period}", basis="not_disclosed",
                source_id=source_id, kind="reported",
            ))

    required = set(preparation._required_financial_evidence_ids(facts))
    for metric in ("gross_margin", "operating_margin", "net_income"):
        selected_periods = {
            fact["period"] for fact in facts
            if fact["metric"] == metric and fact["id"] in required
        }
        assert "quarterly:2026-06-30" in selected_periods
        assert {"quarterly:2026-03-31", "quarterly:2025-03-31"} <= selected_periods
        assert "quarterly:2025-05-31" not in selected_periods


def test_required_financial_comparisons_survive_a_source_only_handoff(monkeypatch):
    from tradingagents.agents.utils.evidence import (
        HANDOFF_END,
        HANDOFF_START,
        build_packet,
        render_analyst_context,
    )

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", _summary_router())
    prepared = preparation.prepare_fundamentals("TEST", "2025-12-31")
    # Even a specialist that cites broad sources must not drop the material
    # comparisons selected by deterministic preparation.
    response = HANDOFF_START + json.dumps({
        "conclusions": ["Financial comparisons require attention."],
        "caveats": [], "conflicts": [],
        "evidence_ids": [source["id"] for source in prepared["sources"]],
    }) + HANDOFF_END
    packet = build_packet("fundamentals", response, prepared)
    assert packet.compacted, packet.validation_errors
    state = {"prepared_data": {"fundamentals": prepared},
             "evidence_packets": {"fundamentals": packet.to_dict()},
             "fundamentals_report": packet.report}
    context = render_analyst_context(state)
    for metric in ("ttm_common_stock_repurchases_magnitude", "ttm_stock_based_compensation",
                   "basic_average_shares_yoy_change", "diluted_average_shares_yoy_change",
                   "ytd_capital_expenditures_magnitude", "operating_income_bridge_adjustment"):
        assert metric in context
    assert "not like-for-like" in context


def test_cashflow_required_context_survives_source_only_handoff_and_restore(monkeypatch):
    from tradingagents.agents.utils.evidence import (
        HANDOFF_END,
        HANDOFF_START,
        build_packet,
        render_analyst_context,
    )

    monkeypatch.setattr(
        preparation, "route_to_vendor_with_metadata", _cashflow_reconciliation_router()
    )
    prepared = preparation.prepare_fundamentals("AMD", "2026-09-13")
    response = HANDOFF_START + json.dumps({
        "conclusions": ["Source-only cash-flow handoff."],
        "caveats": [], "conflicts": [],
        "evidence_ids": [source["id"] for source in prepared["sources"]],
    }) + HANDOFF_END

    packet = build_packet("fundamentals", response, prepared)
    assert packet.compacted, packet.validation_errors
    packet_by_id = {fact.id: fact for fact in packet.facts}
    critical_metrics = {
        "operating_cash_flow_reconciliation",
        "latest_four_quarters_free_cash_flow",
        "free_cash_flow_overview_comparison_difference",
    }
    critical = [fact for fact in packet.facts if fact.metric in critical_metrics]
    assert {fact.metric for fact in critical} == critical_metrics
    for fact in critical:
        assert all(dependency in packet_by_id for dependency in fact.inputs)
    assert any(fact.metric == "free_cash_flow"
               and fact.period == "annual:2025-12-31" for fact in packet.facts)
    assert any(fact.metric == "capital_expenditures"
               and fact.period == "annual:2025-12-31" for fact in packet.facts)

    # render_analyst_context reloads the serialized packet, exercising the
    # checkpoint/resume path after the initial build_packet selection.
    state = {
        "prepared_data": {"fundamentals": prepared},
        "evidence_packets": {"fundamentals": packet.to_dict()},
        "fundamentals_report": packet.report,
    }
    context = render_analyst_context(state)
    for metric in critical_metrics:
        assert metric in context
    for value in ("7709000000", "6493000000", "1216000000", "8403000000",
                  "8841499648", "-438499648", "6735000000", "-974000000"):
        assert value in context
    assert "no continuing-operations free cash flow is inferred" in context
    assert "disclosed total 667000000" in context
    assert "not subtracted from the four-quarter total" in context
    assert "does not establish a contradiction or provider error" in context


@pytest.mark.unit
def test_yfinance_csv_uses_negative_capex_sign_convention(monkeypatch):
    def route(method, ticker, *args):
        if method == "get_fundamentals":
            value = "\n".join([
                "# Company Fundamentals for AAPL", "Market Cap: 5000",
                "PE Ratio (TTM): 25", "Forward PE: 20", "EPS (TTM): 4",
                "Forward EPS: 5",
            ])
        elif method == "get_cashflow":
            value = ",2025-12-31\nOperating Cash Flow,100\nCapital Expenditure,-20\n"
        elif method == "get_income_statement":
            value = ",2025-12-31\nTotal Revenue,100\nGross Profit,40\n"
        else:
            value = ",2025-12-31\nCurrent Assets,200\nCurrent Liabilities,100\n"
        return interface.RoutedVendorResult(value, "yfinance", method)

    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AAPL", "2025-12-31")
    free_cash_flow = [
        fact for fact in prepared["facts"] if fact["metric"] == "free_cash_flow"
        and fact["kind"] == "calculated"
    ]
    assert free_cash_flow
    assert all(fact["value"] == 80 for fact in free_cash_flow)
    assert any(fact["metric"] == "forward_eps" for fact in prepared["facts"])


@pytest.mark.unit
def test_historical_statements_without_publication_vintage_are_withheld_before_fetch(monkeypatch):
    # Even a fiscal period ending before the analysis date might be published
    # afterward. Without a genuine publication/vintage field it cannot be used.
    route = mock.Mock(return_value=interface.RoutedVendorResult(
        json.dumps({"annualReports": [{
            "fiscalDateEnding": "2025-12-31", "totalRevenue": "100"
        }]}),
        "alpha_vantage",
        "get_income_statement",
    ))
    monkeypatch.setattr(preparation.date_window, "get_current_date", lambda: "2026-02-01")
    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", route)
    prepared = preparation.prepare_fundamentals("AAPL", "2025-12-31")
    route.assert_not_called()
    assert prepared["sources"] == []
    assert prepared["facts"] == []
    assert "does not prove the statement was published" in prepared["caveats"][0]


@pytest.mark.unit
def test_prepare_market_exports_traceable_numeric_facts(monkeypatch):
    snapshot = {
        "symbol": "AAPL", "requested_date": "2025-12-31", "latest_date": "2025-12-31",
        "latest_ohlcv": {"Open": 99.0, "High": 102.0, "Low": 98.0,
                         "Close": 101.0, "Volume": 1000},
        "indicators": {"rsi": {"value": 55.25, "error": None}},
        "rows": [{"date": "2025-12-31", "open": 99.0, "high": 102.0,
                  "low": 98.0, "close": 101.0, "volume": 1000}],
    }
    monkeypatch.setattr(preparation, "build_verified_market_snapshot_data", lambda *a: snapshot)
    monkeypatch.setattr(preparation, "render_verified_market_snapshot", lambda value: "RAW SNAPSHOT")
    prepared = preparation.prepare_market("AAPL", "2025-12-31")
    source_id = prepared["sources"][0]["id"]
    assert prepared["analysis_date"] == "2025-12-31"
    assert source_id.startswith("market-")
    assert prepared["sources"][0]["published_at"] is None
    indicator = next(fact for fact in prepared["facts"] if fact["metric"] == "rsi")
    assert indicator["value"] == 55.25
    assert indicator["inputs"] == [source_id]
    assert indicator["unit"] == "index_points"


@pytest.mark.unit
def test_source_ids_do_not_embed_punctuation_from_ticker(monkeypatch):
    snapshot = {
        "symbol": "^GSPC", "requested_date": "2025-12-31", "latest_date": "2025-12-31",
        "latest_ohlcv": {"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1},
        "indicators": {},
        "rows": [{"date": "2025-12-31", "open": 1, "high": 1,
                  "low": 1, "close": 1, "volume": 1}],
    }
    monkeypatch.setattr(preparation, "build_verified_market_snapshot_data", lambda *a: snapshot)
    monkeypatch.setattr(preparation, "render_verified_market_snapshot", lambda value: "RAW")
    prepared = preparation.prepare_market("^GSPC", "2025-12-31")
    ids = [prepared["sources"][0]["id"], *(fact["id"] for fact in prepared["facts"])]
    assert all(re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", item) for item in ids)
    assert all("^" not in item for item in ids)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
def test_non_finite_provider_numbers_are_rejected(value):
    assert parse_decimal(value) is None


@pytest.mark.unit
def test_fred_price_index_exact_twelve_month_change_and_extended_history():
    captured = {}
    meta = {"seriess": [{"title": "CPI", "units_short": "Index", "frequency": "Monthly"}]}
    observations = {"observations": [
        {"date": "2024-08-01", "value": "300"},
        {"date": "2025-08-01", "value": "309"},
    ]}

    def request(path, params):
        captured[path] = params
        return meta if path == "series" else observations

    with mock.patch.object(fred, "_request", side_effect=request), \
            mock.patch.object(fred, "_fred_today", return_value="2025-08-31"):
        out = fred.get_macro_data("cpi", "2025-08-31", 30)
    assert captured["series/observations"]["observation_start"] == "2024-02-28"
    assert "Exact 12-month change" in out
    assert "+3.00%" in out
    assert "2024-08-01" in out


@pytest.mark.unit
def test_fred_price_index_states_missing_exact_comparison():
    meta = {"seriess": [{"title": "PCE", "units_short": "Index", "frequency": "Monthly"}]}
    observations = {"observations": [
        {"date": "2024-07-01", "value": "100"},
        {"date": "2025-08-01", "value": "103"},
    ]}
    with mock.patch.object(
        fred, "_request", side_effect=lambda path, params: meta if path == "series" else observations
    ), mock.patch.object(fred, "_fred_today", return_value="2025-08-31"):
        out = fred.get_macro_data("pce", "2025-08-31", 30)
    assert "unavailable; no observation dated 2024-08-01" in out


@pytest.mark.unit
def test_fred_delayed_pce_release_still_reaches_exact_prior_year_only_for_yoy():
    meta = {"seriess": [{"title": "PCE", "units_short": "Index", "frequency": "Monthly"}]}
    observations = {"observations": [
        {"date": "2025-07-01", "value": "100"},
        {"date": "2026-07-01", "value": "104"},
    ]}
    captured = {}

    def request(path, params):
        captured[path] = params
        return meta if path == "series" else observations

    with mock.patch.object(fred, "_request", side_effect=request), \
            mock.patch.object(fred, "_fred_today", return_value="2026-09-13"):
        out = fred.get_macro_data("pce", "2026-09-13", 365)
    assert captured["series/observations"]["observation_start"] == "2025-03-12"
    assert "Exact 12-month change:** +4.00 index points (+4.00%)" in out
    # The extended 2025-07 base is used only for YoY, not window-change output/table.
    assert "Change over window:** +0.00" in out
    assert "| 2025-07-01 | 100 |" not in out
