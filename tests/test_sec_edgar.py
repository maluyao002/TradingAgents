"""Offline SEC companyfacts selection, routing, and cache tests."""

from __future__ import annotations

import json

import pytest

from tradingagents.dataflows import interface, sec_edgar
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

TICKERS = {"0": {"ticker": "EXM", "cik_str": 1234}}
_REAL_CACHED_JSON = sec_edgar._cached_json


def row(end, val, filed, *, start=None, form="10-K", accn="0001"):
    result = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        result["start"] = start
    return result


FACTS = {"facts": {"us-gaap": {
    "Assets": {"units": {"USD": [
        row("2024-12-31", 10_000_000_001, "2025-02-01", accn="original"),
        row("2024-12-31", 20_000_000_002, "2026-02-01", form="10-K/A", accn="recast"),
        row("2024-12-31", 99_000_000_009, "2026-02-02", form="8-K", accn="nonannual"),
        row("2025-03-31", 30_000_000_003, "2025-05-01", form="10-Q"),
    ]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        row("2024-12-31", 1_234_567_890_123, "2025-02-01", start="2024-01-01"),
        row("2025-12-31", 100_000_001, "2026-02-01", start="2025-10-01", form="10-Q"),
        row("2025-12-31", 300_000_003, "2026-02-01", start="2025-07-01", form="10-Q"),
    ]}},
    "Revenues": {"units": {"USD": [
        row("2023-12-31", 900_000_009, "2024-02-01", start="2023-01-01"),
        row("2024-12-31", 1, "2025-02-01", start="2024-01-01"),
    ]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [
        row("2024-12-31", 1.23456789, "2025-02-01", start="2024-01-01"),
    ]}},
    "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
        row("2024-12-31", 5_000_000_001, "2025-02-01", start="2024-01-01"),
    ]}},
}}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Offline Research offline@example.com")
    monkeypatch.setattr(
        sec_edgar, "_cached_json",
        lambda url, name: TICKERS if name == "company_tickers.json" else FACTS,
    )


def test_original_exact_value_and_filing_provenance_before_recast():
    original = sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")
    assert original["cik"] == "0000001234"
    asset = next(fact for fact in original["facts"] if fact["metric"] == "total_assets")
    assert asset["val"] == 10_000_000_001
    assert asset["filed"] == "2025-02-01"
    assert asset["accn"] == "original"
    assert asset["tag"] == "Assets"
    assert asset["unit"] == "USD"
    assert asset["taxonomy"] == "us-gaap"
    assert asset["end"] == "2024-12-31"
    assert "entityName" not in original

    recast = sec_edgar.get_balance_sheet("EXM", "annual", "2026-03-01")
    recast_asset = next(fact for fact in recast["facts"] if fact["metric"] == "total_assets")
    assert recast_asset["val"] == 20_000_000_002
    assert recast_asset["accn"] == "recast"
    assert recast_asset["filed"] == "2026-02-01"


def test_unfiled_period_excluded_and_annual_balance_requires_annual_form():
    with pytest.raises(NoMarketDataError, match="no annual SEC facts filed"):
        sec_edgar.get_balance_sheet("EXM", "annual", "2025-01-15")
    annual = sec_edgar.get_balance_sheet("EXM", "annual", "2025-06-01")
    quarterly = sec_edgar.get_balance_sheet("EXM", "quarterly", "2025-06-01")
    assert all(fact["end"] != "2025-03-31" for fact in annual["facts"])
    assert any(fact["end"] == "2025-03-31" for fact in quarterly["facts"])


def test_quarter_duration_never_uses_ytd_and_tags_never_sum():
    quarterly = sec_edgar.get_income_statement("EXM", "quarterly", "2026-03-01")
    revenue = next(fact for fact in quarterly["facts"] if fact["metric"] == "revenue")
    assert revenue["val"] == 100_000_001
    assert revenue["start"] == "2025-10-01"
    annual = sec_edgar.get_income_statement("EXM", "annual", "2025-03-01")
    revenues = {fact["end"]: fact for fact in annual["facts"] if fact["metric"] == "revenue"}
    assert revenues["2024-12-31"]["val"] == 1_234_567_890_123
    assert revenues["2023-12-31"]["tag"] == "Revenues"
    eps = next(fact for fact in annual["facts"] if fact["metric"] == "diluted_eps")
    assert eps["val"] == 1.23456789
    assert eps["unit"] == "USD/shares"


def test_capex_productive_assets_alias_is_exact_outflow_magnitude():
    statement = sec_edgar.get_cashflow("EXM", "annual", "2025-03-01")
    capex = next(fact for fact in statement["facts"] if fact["metric"] == "capital_expenditures")
    assert capex["tag"] == "PaymentsToAcquireProductiveAssets"
    assert capex["val"] == 5_000_000_001


def test_annual_foreign_filer_forms_and_only_exact_amendments(monkeypatch):
    documents = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        row("2023-12-31", 10, "2024-02-01", form="20-F"),
        row("2024-12-31", 20, "2025-02-01", form="40-F"),
        row("2024-12-31", 30, "2025-02-02", form="40-F/A"),
        row("2024-12-31", 999, "2025-02-03", form="40-F-OTHER"),
    ]}}}}}
    monkeypatch.setattr(sec_edgar, "_cached_json", lambda url, name: (
        TICKERS if name == "company_tickers.json" else documents
    ))
    actual = sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")
    assert {fact["end"]: fact["val"] for fact in actual["facts"]} == {
        "2023-12-31": 10, "2024-12-31": 30,
    }


def test_missing_accession_is_not_accepted_as_as_filed_provenance(monkeypatch):
    documents = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        row("2024-12-31", 100, "2025-02-01", accn=""),
    ]}}}}}
    monkeypatch.setattr(sec_edgar, "_cached_json", lambda url, name: (
        TICKERS if name == "company_tickers.json" else documents
    ))
    with pytest.raises(NoMarketDataError, match="no annual SEC facts"):
        sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")


def test_missing_contact_is_typed_and_checked_before_warm_cache(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT")
    with pytest.raises(VendorNotConfiguredError, match="SEC_EDGAR_USER_AGENT"):
        sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")


def test_absent_ticker_is_distinct_from_malformed_provider_data(monkeypatch):
    with pytest.raises(NoMarketDataError, match="current SEC filer map"):
        sec_edgar.get_balance_sheet("MISSING", "annual", "2025-03-01")
    monkeypatch.setattr(sec_edgar, "_cached_json", lambda url, name: TICKERS if name == "company_tickers.json" else {"facts": "bad"})
    with pytest.raises(VendorRateLimitError, match="invalid company facts"):
        sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")


def test_raw_cache_is_reused_and_contains_unrounded_provider_document(tmp_path, monkeypatch):
    documents = []

    def fetch(url):
        documents.append(url)
        return TICKERS if "company_tickers" in url else FACTS

    monkeypatch.setattr(sec_edgar, "_cached_json", _REAL_CACHED_JSON)
    monkeypatch.setattr(sec_edgar, "_fetch_json", fetch)
    with run_config({"data_cache_dir": str(tmp_path)}):
        sec_edgar.get_balance_sheet("EXM", "annual", "2025-03-01")
        sec_edgar.get_balance_sheet("EXM", "annual", "2026-03-01")
    assert len(documents) == 2  # ticker map and companyfacts once each
    cached = json.loads((tmp_path / "sec_edgar" / "CIK0000001234.json").read_text())
    assert cached == FACTS
    assert not list((tmp_path / "sec_edgar").glob("*.tmp"))


def test_historical_router_never_calls_unsafe_fallback(monkeypatch):
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    calls = []

    def unsafe(*args):
        calls.append("unsafe")
        return "current vendor values"

    def sec(*args):
        calls.append("sec")
        raise VendorRateLimitError("offline SEC unavailable")

    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "yfinance", unsafe)
    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "sec_edgar", sec)
    with run_config({"tool_vendors": {"get_balance_sheet": "yfinance,sec_edgar"}}), \
            pytest.raises(VendorRateLimitError):
        interface.route_to_vendor_with_metadata("get_balance_sheet", "EXM", "annual", "2025-03-01")
    assert calls == ["sec"]


def test_historical_unconfigured_sec_returns_notice_without_fetch(monkeypatch):
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_balance_sheet"], "yfinance",
        lambda *args: (_ for _ in ()).throw(AssertionError("unsafe fetch")),
    )
    with run_config({"tool_vendors": {"get_balance_sheet": "yfinance"}}):
        routed = interface.route_to_vendor_with_metadata(
            "get_balance_sheet", "EXM", "annual", "2025-03-01",
        )
    assert routed.vendor == "unknown"
    assert "Historical statements require" in routed.value


def test_current_day_route_preserves_configured_vendor(monkeypatch):
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "yfinance", lambda *a: "CURRENT")
    with run_config({"tool_vendors": {"get_balance_sheet": "yfinance"}}):
        routed = interface.route_to_vendor_with_metadata(
            "get_balance_sheet", "EXM", "annual", "2026-03-01",
        )
    assert routed.vendor == "yfinance"
    assert routed.value == "CURRENT"


@pytest.mark.parametrize("bad_date", ["2025-3-1", "2025-02-30", "nonsense"])
def test_historical_router_rejects_bad_date_before_any_vendor(monkeypatch, bad_date):
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    calls = []
    monkeypatch.setitem(interface.VENDOR_METHODS["get_balance_sheet"], "yfinance", lambda *a: (
        calls.append("unsafe") or "CURRENT"
    ))
    with run_config({"tool_vendors": {"get_balance_sheet": "yfinance"}}), \
            pytest.raises(ValueError, match="YYYY-MM-DD"):
        interface.route_to_vendor_with_metadata(
            "get_balance_sheet", "EXM", "annual", bad_date,
        )
    assert calls == []


def test_sec_tool_text_is_json_while_metadata_retains_structured_snapshot(monkeypatch):
    monkeypatch.setattr(interface, "get_current_date", lambda: "2026-03-01")
    with run_config({"tool_vendors": {"get_balance_sheet": "sec_edgar"}}):
        metadata = interface.route_to_vendor_with_metadata(
            "get_balance_sheet", "EXM", "annual", "2025-03-01",
        )
        text = interface.route_to_vendor("get_balance_sheet", "EXM", "annual", "2025-03-01")
    assert metadata.vendor == "sec_edgar"
    assert isinstance(metadata.value, dict)
    assert json.loads(text) == metadata.value
