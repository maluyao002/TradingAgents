"""Offline historical SEC evidence preparation and acceptance contracts."""

from __future__ import annotations

import json

import pytest

from tradingagents.agents.utils.evidence import (
    HANDOFF_END,
    HANDOFF_START,
    build_packet,
)
from tradingagents.dataflows import interface, preparation, sec_edgar
from tradingagents.dataflows.config import run_config
from tradingagents.research_quality import assess_research_quality


def row(end, val, filed, *, start=None, form="10-K", accn="0001"):
    result = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        result["start"] = start
    return result


TICKERS = {"0": {"ticker": "EXM", "cik_str": 1234}}
FACTS = {"facts": {"us-gaap": {
    "AssetsCurrent": {"units": {"USD": [
        row("2024-12-31", 201, "2025-02-01"),
    ]}},
    "LiabilitiesCurrent": {"units": {"USD": [
        row("2024-12-31", 101, "2025-02-02"),
    ]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        row("2023-12-31", 80, "2024-02-01", start="2023-01-01"),
        row("2024-12-31", 100, "2025-02-01", start="2024-01-01"),
    ]}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        row("2024-12-31", 120, "2025-02-03", start="2024-01-01", accn="ocf-accn"),
    ]}},
    "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
        row("2024-12-31", 20, "2025-02-05", start="2024-01-01", accn="capex-accn"),
    ]}},
}}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Offline Research offline@example.com")
    monkeypatch.setattr(sec_edgar, "_cached_json", lambda url, name: (
        TICKERS if name == "company_tickers.json" else FACTS
    ))
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    monkeypatch.setattr(preparation.date_window, "get_current_date", lambda: "2026-03-01")


def _prepare(vendor_chain="yfinance, sec_edgar"):
    with run_config({"tool_vendors": {
        "get_balance_sheet": vendor_chain,
        "get_cashflow": vendor_chain,
        "get_income_statement": vendor_chain,
    }}):
        return preparation.prepare_fundamentals("EXM", "2025-03-01")


def test_historical_preparation_preserves_exact_sec_provenance_and_derived_date(monkeypatch):
    def unsafe(*args):
        raise AssertionError("live-only vendor fetched")

    for method in ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"):
        for vendor in ("yfinance", "alpha_vantage"):
            monkeypatch.setitem(interface.VENDOR_METHODS[method], vendor, unsafe)

    prepared = _prepare()
    assert prepared["sources"]
    assert {source["vendor"] for source in prepared["sources"]} == {"sec_edgar"}
    assert all(source["published_at"] is None for source in prepared["sources"])
    assert any("profile" in caveat.lower() and "withheld" in caveat.lower()
               for caveat in prepared["caveats"])
    assert any("current SEC ticker map" in caveat for caveat in prepared["caveats"])
    raw = [json.loads(source["content"]) for source in prepared["sources"]]
    selected = [fact for snapshot in raw for fact in snapshot["facts"]]
    assert any(fact["val"] == 120 and fact["filed"] == "2025-02-03"
               and fact["accn"] == "ocf-accn" and fact["tag"] == "NetCashProvidedByUsedInOperatingActivities"
               for fact in selected)
    by_metric = {}
    for fact in prepared["facts"]:
        by_metric.setdefault(fact["metric"], []).append(fact)
    ocf = by_metric["operating_cash_flow"][0]
    capex = by_metric["capital_expenditures"][0]
    fcf = by_metric["free_cash_flow"][0]
    assert (ocf["value"], capex["value"], fcf["value"]) == (120, 20, 100)
    assert ocf["published_at"] == "2025-02-03"
    assert capex["published_at"] == "2025-02-05"
    assert fcf["published_at"] == "2025-02-05"
    assert fcf["inputs"] == [ocf["id"], capex["id"]]
    assert "capex-accn" in " ".join(capex["caveats"])


def test_sec_prepared_facts_form_valid_compact_handoff_and_acceptance():
    prepared = _prepare("sec_edgar")
    cited = next(fact for fact in prepared["facts"] if fact["metric"] == "operating_cash_flow")
    report = HANDOFF_START + json.dumps({
        "conclusions": [f"Reported operating cash flow is 120 USD [{cited['id']}]."],
        "caveats": ["The ticker-to-CIK mapping is current, not historical."],
        "conflicts": [],
        "evidence_ids": [cited["id"]],
    }) + HANDOFF_END
    packet = build_packet("fundamentals", report, prepared)
    assert packet.compacted is True
    assert packet.validation_errors == ()
    assert any(fact.published_at == "2025-02-03" for fact in packet.facts)
    assert any("ocf-accn" in " ".join(fact.caveats) for fact in packet.facts)

    state = {
        "_selected_analysts": ["fundamentals"], "_research_backend": "api",
        "prepared_data": {"fundamentals": prepared},
        "evidence_packets": {"fundamentals": packet.to_dict()},
        "fundamentals_report": packet.report,
        "investment_plan": "Rating: Hold",
        "trader_investment_plan": "Hold pending verification.",
        "final_trade_decision": "Rating: Hold",
        "investment_debate_state": {
            "bull_history": "Bull evidence.", "bear_history": "Bear evidence.",
            "history": "Debate.", "judge_decision": "Rating: Hold",
        },
        "risk_debate_state": {
            "aggressive_history": "Aggressive view.",
            "conservative_history": "Conservative view.",
            "neutral_history": "Neutral view.",
            "history": "Risk debate.", "judge_decision": "Rating: Hold",
        },
    }
    quality = assess_research_quality(state)
    assert quality["accepted"] is True
    assert quality["signal"] == "Hold"


def test_non_sec_history_has_no_statement_or_profile_fetch(monkeypatch):
    monkeypatch.setitem(interface.VENDOR_METHODS["get_fundamentals"], "yfinance", lambda *a: (
        (_ for _ in ()).throw(AssertionError("live profile fetched"))
    ))
    prepared = _prepare("yfinance")
    assert prepared["sources"] == []
    assert prepared["facts"] == []
    assert "Point-in-time fundamentals are unavailable" in prepared["caveats"][0]


def test_default_chain_includes_sec_but_history_uses_only_sec(monkeypatch):
    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "yfinance", lambda *a: (
        (_ for _ in ()).throw(AssertionError("unsafe fallback"))
    ))
    prepared = _prepare("default")
    assert prepared["sources"]
    assert all(source["vendor"] == "sec_edgar" for source in prepared["sources"])


def test_derived_publication_dates_require_all_inputs_and_are_transitive():
    facts = [
        {"id": "fundamentals:a", "kind": "reported", "published_at": "2025-02-01"},
        {"id": "fundamentals:b", "kind": "reported", "published_at": "2025-02-05"},
        {"id": "fundamentals:unknown", "kind": "reported"},
        {"id": "fundamentals:sum", "kind": "calculated", "inputs": ["fundamentals:a", "fundamentals:b"]},
        {"id": "fundamentals:transitive", "kind": "calculated", "inputs": ["fundamentals:sum", "fundamentals:a"]},
        {"id": "fundamentals:withheld", "kind": "calculated", "inputs": ["fundamentals:sum", "fundamentals:unknown"]},
    ]
    preparation._propagate_filing_dates(facts)
    assert facts[3]["published_at"] == "2025-02-05"
    assert facts[4]["published_at"] == "2025-02-05"
    assert "published_at" not in facts[5]


@pytest.mark.parametrize("bad_date", ["2025-3-1", "2025-02-30", "invalid"])
def test_preparation_rejects_noncanonical_date_before_fetch(monkeypatch, bad_date):
    monkeypatch.setattr(preparation, "route_to_vendor_with_metadata", lambda *a: (
        (_ for _ in ()).throw(AssertionError("vendor called"))
    ))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        preparation.prepare_fundamentals("EXM", bad_date)
