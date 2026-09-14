import json
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.utils.evidence import HANDOFF_END, HANDOFF_START, build_packet
from tradingagents.dataflows import macro_preparation
from tradingagents.dataflows.interface import RoutedVendorResult


def _fred_report(
    series,
    *,
    title,
    units,
    frequency,
    analysis_date="2026-09-13",
    window_start="2026-06-15",
    first_date,
    first,
    latest_date,
    latest,
    delta,
    relative,
    exact_line=None,
):
    report = (
        f"## FRED: {title} ({series})\n"
        f"- Units: {units}\n"
        f"- Frequency: {frequency}\n"
        f"- Window: {window_start} to {analysis_date}\n\n"
        f"**Latest:** {latest} ({latest_date}) | **Change over window:** {delta} "
        f"({relative}%) from {first} ({first_date})\n"
    )
    if exact_line:
        report += exact_line + "\n"
    return report + f"\n| Date | Value |\n| --- | --- |\n| {latest_date} | {latest} |\n"


def _baseline_reports():
    return {
        "CPIAUCSL": _fred_report(
            "CPIAUCSL", title="Consumer Price Index", units="Index 1982-1984=100",
            frequency="Monthly (SA)", window_start="2025-03-12",
            first_date="2026-06-01", first="319", latest_date="2026-08-01",
            latest="321", delta="+2.00", relative="+0.63",
            exact_line=(
                "**Exact 12-month change:** +9.00 index points (+2.88%) from 312 "
                "(2025-08-01)."
            ),
        ),
        "PCEPILFE": _fred_report(
            "PCEPILFE", title="Core PCE Price Index", units="Index 2017=100",
            frequency="Monthly (SA)", window_start="2025-03-12",
            first_date="2026-06-01", first="124", latest_date="2026-07-01",
            latest="125", delta="+1.00", relative="+0.81",
            exact_line=(
                "**Exact 12-month change:** +3.00 index points (+2.46%) from 122 "
                "(2025-07-01)."
            ),
        ),
        "UNRATE": _fred_report(
            "UNRATE", title="Unemployment Rate", units="Percent", frequency="Monthly (SA)",
            first_date="2026-06-01", first="4.1", latest_date="2026-08-01",
            latest="4.3", delta="+0.20", relative="+4.88",
        ),
        "FEDFUNDS": _fred_report(
            "FEDFUNDS", title="Federal Funds Effective Rate", units="Percent",
            frequency="Monthly", first_date="2026-06-01", first="4.25",
            latest_date="2026-08-01", latest="4.50", delta="+0.25", relative="+5.88",
        ),
        "DGS10": _fred_report(
            "DGS10", title="10-Year Treasury Rate", units="Percent", frequency="Daily",
            window_start="2026-08-14", first_date="2026-08-14", first="4.00",
            latest_date="2026-09-11", latest="4.10", delta="+0.10", relative="+2.50",
        ),
        "T10Y2Y": _fred_report(
            "T10Y2Y", title="10Y-2Y Treasury Spread", units="Percent", frequency="Daily",
            window_start="2026-08-14", first_date="2026-08-14", first="0.25",
            latest_date="2026-09-11", latest="0.50", delta="+0.25", relative="+100.00",
        ),
    }


def test_macro_baseline_preserves_all_six_series_and_failure_caveat(monkeypatch):
    calls = []

    def route(method, **kwargs):
        calls.append((method, kwargs))
        if kwargs["indicator"] == "UNRATE":
            raise TimeoutError("private transport details")
        return RoutedVendorResult(f"FRED: {kwargs['indicator']} pinned to {kwargs['curr_date']}", "fred", method)

    monkeypatch.setattr(macro_preparation, "route_to_vendor_with_metadata", route)
    data = macro_preparation.prepare_macro("2026-09-13")
    assert [kwargs["indicator"] for _, kwargs in calls] == [series for series, _ in macro_preparation.MACRO_BASELINE]
    assert all(kwargs["curr_date"] == "2026-09-13" for _, kwargs in calls)
    assert len(data["sources"]) == 5
    assert any("UNRATE unavailable" in caveat for caveat in data["caveats"])
    assert "private transport details" not in str(data)
    assert all(source["id"].startswith("news-macro-") for source in data["sources"])
    assert all(source["published_at"] is None for source in data["sources"])


def test_macro_baseline_builds_typed_facts_with_one_routed_call_per_series(monkeypatch):
    reports = _baseline_reports()
    calls = []

    def route(method, **kwargs):
        calls.append((method, kwargs["indicator"]))
        return RoutedVendorResult(reports[kwargs["indicator"]], "fred", method)

    monkeypatch.setattr(macro_preparation, "route_to_vendor_with_metadata", route)
    data = macro_preparation.prepare_macro("2026-09-13")

    assert calls == [("get_macro_indicators", series) for series, _ in macro_preparation.MACRO_BASELINE]
    assert len(data["sources"]) == 6
    assert len(data["facts"]) == 18
    assert len(data["required_evidence_ids"]) == 6
    fact_ids = {fact["id"] for fact in data["facts"]}
    assert set(data["required_evidence_ids"]) <= fact_ids
    assert all(fact["id"].startswith("news-fact-macro-") for fact in data["facts"])
    for fact in data["facts"]:
        assert set(fact["inputs"]) <= fact_ids
        assert fact["period"] and fact["unit"] and fact["basis"]

    yoy = next(fact for fact in data["facts"] if fact["metric"] == "cpiaucsl_exact_yoy_change")
    assert yoy["value"] == 2.8846153846153846
    assert yoy["unit"] == "percent"
    assert yoy["period"] == "2025-08-01_to_2026-08-01"
    assert len(yoy["inputs"]) == 2

    rate_changes = [fact for fact in data["facts"] if fact["metric"].endswith("_window_change")]
    assert len(rate_changes) == 4
    assert all(fact["unit"] == "percentage_points" for fact in rate_changes)

    handoff = {
        "conclusions": ["The macro baseline is retained through deterministic fact IDs."],
        "caveats": [],
        "conflicts": [],
        "evidence_ids": [data["sources"][0]["id"]],
    }
    packet = build_packet(
        "news",
        f"{HANDOFF_START}\n{json.dumps(handoff)}\n{HANDOFF_END}",
        data,
    )
    assert packet.compacted is True
    assert {fact.id for fact in packet.facts} == fact_ids


def test_macro_exact_cpi_dates_and_lagged_pce_dates_are_preserved(monkeypatch):
    reports = _baseline_reports()
    monkeypatch.setattr(
        macro_preparation,
        "route_to_vendor_with_metadata",
        lambda method, **kwargs: RoutedVendorResult(reports[kwargs["indicator"]], "fred", method),
    )
    data = macro_preparation.prepare_macro("2026-09-13")
    by_metric = {fact["metric"]: fact for fact in data["facts"]}

    assert by_metric["cpiaucsl_exact_12m_base"]["period"] == "observation:2025-08-01"
    assert by_metric["cpiaucsl_latest_level"]["period"] == "observation:2026-08-01"
    assert by_metric["pcepilfe_exact_12m_base"]["period"] == "observation:2025-07-01"
    assert by_metric["pcepilfe_latest_level"]["period"] == "observation:2026-07-01"
    assert by_metric["pcepilfe_exact_yoy_change"]["period"] == "2025-07-01_to_2026-07-01"


def test_macro_missing_exact_base_keeps_latest_and_adds_caveat(monkeypatch):
    reports = _baseline_reports()
    reports["CPIAUCSL"] = _fred_report(
        "CPIAUCSL", title="Consumer Price Index", units="Index 1982-1984=100",
        frequency="Monthly (SA)", window_start="2025-03-12",
        first_date="2026-06-01", first="319", latest_date="2026-08-01",
        latest="321", delta="+2.00", relative="+0.63",
        exact_line=(
            "**Exact 12-month change:** unavailable; no observation dated 2025-08-01 "
            "is present at the 2026-09-13 vintage."
        ),
    )
    monkeypatch.setattr(
        macro_preparation,
        "route_to_vendor_with_metadata",
        lambda method, **kwargs: RoutedVendorResult(reports[kwargs["indicator"]], "fred", method),
    )

    data = macro_preparation.prepare_macro("2026-09-13")
    cpi_facts = [fact for fact in data["facts"] if fact["metric"].startswith("cpiaucsl_")]
    assert [fact["metric"] for fact in cpi_facts] == ["cpiaucsl_latest_level"]
    assert cpi_facts[0]["id"] in data["required_evidence_ids"]
    assert any("Exact 12-month CPIAUCSL comparison is unavailable" in item for item in data["caveats"])


def test_macro_zero_base_never_fabricates_yoy_percent(monkeypatch):
    reports = _baseline_reports()
    reports["CPIAUCSL"] = _fred_report(
        "CPIAUCSL", title="Consumer Price Index", units="Index 1982-1984=100",
        frequency="Monthly (SA)", window_start="2025-03-12",
        first_date="2026-06-01", first="90", latest_date="2026-08-01",
        latest="100", delta="+10.00", relative="+11.11",
        exact_line=(
            "**Exact 12-month change:** +100.00 index points from 0 (2025-08-01); "
            "percent change is undefined because the comparison value is zero."
        ),
    )
    monkeypatch.setattr(
        macro_preparation,
        "route_to_vendor_with_metadata",
        lambda method, **kwargs: RoutedVendorResult(reports[kwargs["indicator"]], "fred", method),
    )

    data = macro_preparation.prepare_macro("2026-09-13")
    cpi_facts = [fact for fact in data["facts"] if fact["metric"].startswith("cpiaucsl_")]
    assert not any(fact["metric"] == "cpiaucsl_exact_yoy_change" for fact in cpi_facts)
    point_change = next(
        fact for fact in cpi_facts if fact["metric"] == "cpiaucsl_exact_12m_index_point_change"
    )
    assert point_change["value"] == 100
    assert point_change["unit"] == "index_points"
    assert point_change["id"] in data["required_evidence_ids"]
    assert any("undefined" in caveat for caveat in point_change["caveats"])


def test_news_prepares_once_and_keeps_source_data_out_of_system_prompt(monkeypatch):
    source = {"id": "news-macro-CPI", "content": "CPI raw evidence", "label": "CPI",
              "vendor": "fred", "published_at": None, "retrieved_at": "2026-09-13"}
    prepared = {"analysis_date": "2026-09-13", "sources": [source], "facts": [], "caveats": []}
    prepare = MagicMock(return_value=prepared)
    monkeypatch.setattr("tradingagents.agents.analysts.news_analyst.prepare_macro", prepare)
    prompts = []

    def respond(prompt):
        prompts.append(prompt.to_messages())
        if len(prompts) == 1:
            return AIMessage(content="", tool_calls=[{"id": "news-1", "name": "get_news", "args": {}}])
        return AIMessage(content="Complete ordinary report.")

    llm = MagicMock()
    llm.bind_tools.return_value = RunnableLambda(respond)
    node = create_news_analyst(llm)
    state = {"trade_date": "2026-09-13", "company_of_interest": "TEST", "messages": []}
    state.update(node(state))
    tool_text = "Company news unique source response"
    state["messages"].append(ToolMessage(content=tool_text, tool_call_id="news-1", name="get_news"))
    update = node(state)
    assert prepare.call_count == 1
    assert "CPI raw evidence" not in prompts[-1][0].content
    assert sum(str(m.content).count("CPI raw evidence") for m in prompts[-1]) == 1
    assert sum(str(m.content).count(tool_text) for m in prompts[-1]) == 1
    assert len(update["prepared_data"]["news"]["sources"]) == 2
