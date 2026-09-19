"""Financial-path and publication gates for the explicit development case."""
from datetime import datetime, timezone
from decimal import Decimal, localcontext

import pytest

from scripts import research_nvda_scenarios as scenarios
from tests.test_research_scenario_compiler import _compile, _request, _snapshot
from tradingagents.research.scenario_compiler import REVIEWED_PACKET_FILES
from tradingagents.research.storage import canonical_json, read_json
from tradingagents.research.valuation import FCFFModelInput, ValuationUnits, dcf_valuation

D = Decimal
MARKET = {"risk_free_rate": ".0501", "erp": ".0414", "unlevered_beta_cash_corrected": "1.50"}


def test_conditional_paths_reconcile_terminal_reinvestment_and_sbc():
    request = _request()
    cases, audit = scenarios.authored_cases(request, MARKET)
    assert len(cases) == 3
    values = []
    for case in cases:
        assert len(case.periods) == 10 and case.periods[-1].revenue_growth == case.terminal_growth
        model = FCFFModelInput(as_of_date=case.periods[0].period_start, current_revenue=D("302970"),
            current_working_capital=D("56024"), net_debt=D("10923"), current_diluted_shares=D("24285"),
            units=ValuationUnits("USD", D("1000000"), D("1000000")), periods=case.periods,
            discount_rate=case.discount_rate, terminal_growth=case.terminal_growth)
        result = dcf_valuation(model)
        assert all(row.fcff > 0 for row in result.forecasts)
        for period, row in zip(case.periods, result.forecasts, strict=True):
            assert row.operating_profit_before_tax == row.revenue * period.operating_margin
        last = result.forecasts[-1]
        with localcontext() as context:
            context.prec = 40
            actual = (last.capex - last.depreciation_amortization + last.change_in_working_capital) / last.nopat
            expected = case.terminal_growth / audit[case.id]["terminal_roic_assumption"]
            assert abs(actual - expected) < D("1e-24")
        assert audit[case.id]["probability"] is None
        values.append(result.value_per_current_diluted_share)
    assert values[0] < values[1] < values[2]
    assert cases[1].discount_rate == D(".11220")


def test_calendar_anniversaries_support_leap_day():
    request = _request(cutoff=datetime(2024, 2, 29, tzinfo=timezone.utc))
    cases, _ = scenarios.authored_cases(request, MARKET)
    assert cases[0].periods[0].period_end.isoformat() == "2025-02-28"
    assert cases[0].periods[3].period_end.isoformat() == "2028-02-29"


def test_publication_never_overwrites_existing_bundle(tmp_path):
    output = tmp_path / "bundle"
    scenarios._publish(output, {"example.json": b"{}"})
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        scenarios._publish(output, {"example.json": b"changed"})
    assert (output / "manifest.json").read_bytes() == before


def test_compile_rejects_changed_packet_before_review(tmp_path):
    packet = tmp_path / "packet"
    scenarios._publish(packet, {"example.json": b"{}"})
    (packet / "example.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="inventory incomplete"):
        scenarios.compile_reviewed(packet, tmp_path / "unread-review", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_compile_rejects_manifest_path_traversal(tmp_path):
    packet = tmp_path / "packet"
    scenarios._publish(packet, {"example.json": b"{}"})
    manifest = read_json(packet / "manifest.json")
    manifest["artifact_hashes"]["../outside"] = "a" * 64
    (packet / "manifest.json").write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError, match="inventory incomplete"):
        scenarios.compile_reviewed(packet, tmp_path / "unread-review", tmp_path / "output")


def test_complete_inventory_still_checks_each_artifact_hash(tmp_path):
    packet = tmp_path / "packet"
    scenarios._publish(packet, dict.fromkeys(REVIEWED_PACKET_FILES, b"{}"))
    (packet / "economic_audit.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        scenarios.compile_reviewed(packet, tmp_path / "unread-review", tmp_path / "output")


def test_reviewer_and_package_limitations_are_visible_in_memo():
    memo = scenarios.render_memo(_compile(), {"base": {"terminal_roic_assumption": ".20"}},
                                 _snapshot(), ("Mixed working capital remains unresolved.",
                                               "Automated reviewer restricts this to a diagnostic."))
    assert "Mixed working capital remains unresolved." in memo
    assert "Automated reviewer restricts this to a diagnostic." in memo


def test_scoped_memo_does_not_read_raw_blocked_numbers():
    compiled = _compile()
    compiled["cases"][0]["result"] = {"forecasts": [{"label": "RAW_BLOCKED_FORECAST"}]}
    scoped = {"base": {"result": {}, "model_result_scope": {
        "operating_asset_value": {"status": "blocked", "reasons": ["Material operating inputs missing."]},
    }}}
    memo = scenarios.render_memo(compiled, {}, _snapshot(), scoped_results=scoped)
    assert "withheld" in memo
    assert "Material operating inputs missing." in memo
    assert compiled["cases"][0]["thesis"] in memo
    assert "RAW_BLOCKED_FORECAST" not in memo
    assert "| Year | Revenue" not in memo


def test_scoped_memo_uses_only_scoped_forecasts():
    compiled = _compile()
    from copy import deepcopy

    scoped_result = deepcopy(compiled["cases"][0]["result"])
    compiled["cases"][0]["result"] = {"forecasts": [{"label": "RAW_UNVERIFIED"}]}
    scoped = {"base": {"result": scoped_result, "model_result_scope": {
        "operating_asset_value": {"status": "conditional", "reasons": ["Conditional operating case."]},
    }}}
    memo = scenarios.render_memo(compiled, {"base": {"terminal_roic_assumption": ".20"}},
                                 _snapshot(), scoped_results=scoped)
    assert "| Year | Revenue" in memo and "RAW_UNVERIFIED" not in memo
