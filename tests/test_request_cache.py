from __future__ import annotations

import json
from unittest import mock

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError
from tradingagents.dataflows.request_cache import data_request_scope, run_data_scope
from tradingagents.reporting import write_report_tree


@pytest.mark.unit
def test_same_scope_canonicalizes_positional_keyword_and_defaults():
    calls = 0

    def news(ticker, start_date, end_date, limit=20):
        nonlocal calls
        calls += 1
        return "NEWS"

    with mock.patch.object(interface, "get_vendor", return_value="yfinance"), \
            mock.patch.dict(interface.VENDOR_METHODS, {"get_news": {"yfinance": news}}), \
            data_request_scope():
        first = interface.route_to_vendor("get_news", "aapl", "2026-01-01", "2026-01-02")
        second = interface.route_to_vendor(
            "get_news", ticker="AAPL", start_date="2026-01-01",
            end_date="2026-01-02", limit=20,
        )

    assert first == second == "NEWS"
    assert calls == 1


@pytest.mark.unit
def test_scopes_are_isolated():
    calls = 0

    def stock(symbol, start_date, end_date):
        nonlocal calls
        calls += 1
        return "DATA"

    with mock.patch.object(interface, "get_vendor", return_value="yfinance"), \
            mock.patch.dict(interface.VENDOR_METHODS, {"get_stock_data": {"yfinance": stock}}):
        with data_request_scope():
            interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-02")
        with data_request_scope():
            interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-02")

    assert calls == 2


@pytest.mark.unit
def test_failures_are_not_cached():
    calls = 0

    def stock(symbol, start_date, end_date):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AlphaVantageRateLimitError("limited")
        return "RECOVERED"

    with mock.patch.object(interface, "get_vendor", return_value="alpha_vantage"), \
            mock.patch.dict(
                interface.VENDOR_METHODS,
                {"get_stock_data": {"alpha_vantage": stock}},
            ), data_request_scope():
        with pytest.raises(RuntimeError):
            interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-02")
        assert interface.route_to_vendor(
            "get_stock_data", "AAPL", "2026-01-01", "2026-01-02"
        ) == "RECOVERED"

    assert calls == 2


@pytest.mark.unit
def test_metadata_reports_actual_fallback_vendor():
    def broken(symbol, start_date, end_date):
        raise ValueError("broken")

    def healthy(symbol, start_date, end_date):
        return "YF"

    with mock.patch.object(
        interface, "get_vendor", return_value="alpha_vantage,yfinance"
    ), mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"alpha_vantage": broken, "yfinance": healthy}},
    ):
        routed = interface.route_to_vendor_with_metadata(
            "get_stock_data", "AAPL", "2026-01-01", "2026-01-02"
        )

    assert routed.value == "YF"
    assert routed.vendor == "yfinance"


@pytest.mark.unit
def test_error_string_value_falls_back_to_healthy_vendor():
    primary = mock.Mock(return_value="Error retrieving fundamentals for AAPL: timeout")
    secondary = mock.Mock(return_value="HEALTHY")
    with mock.patch.object(
        interface, "get_vendor", return_value="yfinance,alpha_vantage"
    ), mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_fundamentals": {"yfinance": primary, "alpha_vantage": secondary}},
    ):
        routed = interface.route_to_vendor_with_metadata(
            "get_fundamentals", "AAPL", "2026-01-02"
        )
    assert routed.value == "HEALTHY"
    assert routed.vendor == "alpha_vantage"
    primary.assert_called_once()
    secondary.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["{}", '{"Error Message":"invalid symbol"}'])
def test_alpha_failure_payload_is_not_cached_and_fallback_success_is_cached(failure):
    calls = {"alpha": 0, "yahoo": 0}

    def alpha(ticker, curr_date=None):
        calls["alpha"] += 1
        return failure

    def yahoo(ticker, curr_date=None):
        calls["yahoo"] += 1
        return "HEALTHY"
    with mock.patch.object(
        interface, "get_vendor", return_value="alpha_vantage,yfinance"
    ), mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_fundamentals": {"alpha_vantage": alpha, "yfinance": yahoo}},
    ), data_request_scope():
        assert interface.route_to_vendor(
            "get_fundamentals", "AAPL", "2026-01-02"
        ) == "HEALTHY"
        assert interface.route_to_vendor(
            "get_fundamentals", "aapl", "2026-01-02"
        ) == "HEALTHY"

    # Failed Alpha values are retried; the successful fallback is served from cache.
    assert calls == {"alpha": 2, "yahoo": 1}


@pytest.mark.unit
def test_run_data_scope_decorator_caches_one_complete_call():
    calls = 0

    def stock(symbol, start_date, end_date):
        nonlocal calls
        calls += 1
        return "DATA"

    @run_data_scope
    def run():
        interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-02")
        return interface.route_to_vendor(
            "get_stock_data", symbol="aapl", start_date="2026-01-01",
            end_date="2026-01-02",
        )

    with mock.patch.object(interface, "get_vendor", return_value="yfinance"), \
            mock.patch.dict(interface.VENDOR_METHODS, {"get_stock_data": {"yfinance": stock}}):
        assert run() == "DATA"
    assert calls == 1


@pytest.mark.unit
@pytest.mark.parametrize("payload", [
    "Error: request failed api_key=SYNTHETIC_SECRET",
    '{"Error Message": "api_key=SYNTHETIC_SECRET"}',
    '{"Information": "api_key=SYNTHETIC_SECRET"}',
    '{"Note": "api_key=SYNTHETIC_SECRET"}',
])
def test_provider_failure_classification_omits_raw_details(payload):
    failure = interface._provider_failure(payload, "alpha_vantage")
    assert failure is not None
    assert "SYNTHETIC_SECRET" not in failure


@pytest.mark.unit
def test_optional_provider_exception_is_not_persisted_or_logged(tmp_path, caplog):
    secret = "SYNTHETIC_SECRET"

    def macro(indicator, curr_date, look_back_days=None):
        raise RuntimeError(
            "500 for url: https://api.example.test/data?api_key=" + secret
        )

    with mock.patch.object(interface, "get_vendor", return_value="fred"), \
            mock.patch.dict(
                interface.VENDOR_METHODS,
                {"get_macro_indicators": {"fred": macro}},
            ):
        routed = interface.route_to_vendor_with_metadata(
            "get_macro_indicators", "CPIAUCSL", "2026-01-02"
        )

    state = {
        "prepared_data": {
            "news": {"sources": [{"content": routed.value}]},
        },
        "evidence_packets": {},
    }
    write_report_tree(state, "TEST", tmp_path)
    saved = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))

    assert routed.value == (
        "DATA_UNAVAILABLE: optional macro_data could not be retrieved "
        "(RuntimeError). Proceed without it; do not fabricate values."
    )
    assert secret not in caplog.text
    assert secret not in json.dumps(saved)
