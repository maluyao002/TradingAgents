"""Offline reader-delivery regressions for case-backed scenario presentation."""

from __future__ import annotations

from decimal import ROUND_DOWN, Context, Inexact, Rounded, localcontext
from pathlib import Path

import pytest

from tests.test_research_operating_scenarios import _draft, _reviewed
from tradingagents.research.calculated_values import render_calculations
from tradingagents.research.case_context import load_case_context
from tradingagents.research.case_report import case_reader_delivery
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.storage import canonical_json, read_json


def _request(tmp_path, *, timezone):
    return ResearchRequest(
        ticker="NVDA",
        cutoff="2025-09-18T12:00:00Z",
        timezone=timezone,
        backend="api",
        output_dir=tmp_path / "output",
        quality_revision="evidence-led-bounded",
        valuation_method="fcff",
        financial_case_path=tmp_path / "case.json",
    )


def _context(tmp_path, *, reviewed_operating: bool):
    package, case, snapshot = (_reviewed() if reviewed_operating else _draft())
    return load_case_context(
        canonical_json({"case": case, "operating_scenarios": package}),
        _request(tmp_path, timezone=case.timezone),
        snapshot,
    )


def _guidance_context(source_text, *, revenue="100000000000", label="Case"):
    def assumption(value, classification="analyst_assumption"):
        return {
            "value": value,
            "classification": classification,
            "evidence_ids": ["outlook"],
        }
    return {
        "review_status": "draft_unreviewed",
        "operating_scenarios": {
            "reviewed": True,
            "date_convention": "explicit_dates",
            "source_material": [{
                "id": "outlook", "source_id": "issuer-release", "text": source_text,
            }],
            "scenarios": [{
                "id": "base", "label": label,
                "periods": [
                    {
                        "id": "q3", "fiscal_label": "Q3", "period_start": "2026-07-01",
                        "period_end": "2026-09-30", "accounting_basis": "US GAAP",
                        "inputs": {
                            "revenue": assumption(revenue, "management_guidance_anchor"),
                            "gross_margin": assumption("0.7", "management_guidance_anchor"),
                            "opex": assumption("9000000000", "management_guidance_anchor"),
                        },
                    },
                    {
                        "id": "q4", "fiscal_label": "Q4", "period_start": "2026-10-01",
                        "period_end": "2026-12-31", "accounting_basis": "US GAAP",
                        "inputs": {
                            "revenue": assumption("110000000000"),
                            "gross_margin": assumption("0.69"),
                            "opex": assumption("9900000000"),
                        },
                    },
                ],
            }],
        },
    }


def test_delivery_separates_unreviewed_financial_draft_from_reviewed_operating_package(tmp_path):
    context = _context(tmp_path, reviewed_operating=True)

    delivery = case_reader_delivery(context)

    assert delivery == case_reader_delivery(context.model_context())
    assert delivery["financial_case_review"]["status"] == "draft_unreviewed"
    assert delivery["operating_package_review"]["status"] == "reviewed_conditional_operating_package"
    assert "does not review or clear the financial draft" in delivery["operating_package_review"][
        "reader_consequence"
    ]
    presentation = delivery["scenario_presentation"]
    assert presentation["classification"] == (
        "reviewed_conditional_inputs_and_analyst_calculations_not_reported_facts"
    )
    row = presentation["rows"][0]
    assert row["period_id"] == "q3"
    assert [item["name"] for item in row["inputs"]] == ["revenue", "gross_margin", "opex"]
    assert row["inputs"][0]["source_material_ids"] == ["q2-passage"]
    assert row["inputs"][0]["source_ids"] == ["ir-q2"]
    assert row["analyst_calculations"]["weeks"] == "13"
    assert row["analyst_calculations"]["revenue_per_week"].startswith("2.307692307692307692307692307")
    q4 = presentation["rows"][1]["analyst_calculations"]
    assert q4["revenue_change_vs_previous_period"] is not None
    assert q4["opex_change_vs_previous_period"] is not None
    assert q4["revenue_per_week_change_vs_previous_period"] is not None
    assert "input_ranges_across_scenarios" not in presentation


def test_unreviewed_or_missing_operating_inputs_are_not_invented(tmp_path):
    context = _context(tmp_path, reviewed_operating=False)

    delivery = case_reader_delivery(context)

    assert delivery["financial_case_review"]["status"] == "draft_unreviewed"
    assert delivery["operating_package_review"]["status"] == "absent_or_unreviewed"
    assert delivery["scenario_presentation"] is None
    try:
        render_calculations("{{scenario_assumptions_table}}", (), scenario_delivery=delivery)
    except ValueError as error:
        assert str(error) == "scenario assumptions table requires reviewed delivery presentation"
    else:
        raise AssertionError("unreviewed assumptions marker must not render")


def test_new_assumptions_marker_has_explicit_input_and_calculation_labels(tmp_path):
    delivery = case_reader_delivery(_context(tmp_path, reviewed_operating=True))

    rendered = render_calculations(
        "{{scenario_assumptions_table}}", (), scenario_delivery=delivery
    )

    assert rendered.startswith(
        "| Scenario / period | Inputs / provenance | "
        "Revenue / week (analyst calculation) |"
    )
    assert "issuer guidance" in rendered
    assert "[ir-q2]" in rendered
    assert "analyst calculation" in rendered
    assert "](http" not in rendered
    assert "{{scenario_assumptions_table}}" not in rendered


def test_frozen_nvda_delivery_uses_issuer_ranges_and_q4_rates_without_scenario_minmax():
    path = (
        Path(__file__).resolve().parents[1]
        / "reports/NVDA_VALIDATION_20260921/run_1/operating_scenario_context.json"
    )
    if not path.is_file():
        pytest.skip("optional frozen NVDA validation fixture is absent")
    delivery = case_reader_delivery({
        "review_status": "draft_unreviewed",
        "operating_scenarios": read_json(path),
    })

    presentation = delivery["scenario_presentation"]
    downside_q3, downside_q4 = presentation["rows"][:2]
    q3_inputs = {item["name"]: item for item in downside_q3["inputs"]}
    assert q3_inputs["revenue"]["issuer_guidance_range"]["range"] == "±2%"
    assert q3_inputs["gross_margin"]["issuer_guidance_range"]["range"] == "±50 basis points"
    assert q3_inputs["revenue"]["issuer_guidance_range"]["source_id"] == "nvda-q2-release"
    assert "Revenue is expected" in q3_inputs["revenue"]["issuer_guidance_range"]["exact_excerpt"]
    assert q3_inputs["opex"]["issuer_guidance_range"] is None
    assert downside_q4["analyst_calculations"]["revenue_change_vs_previous_period"] == "-0.05"
    assert downside_q4["analyst_calculations"]["opex_change_vs_previous_period"] == "0.15"
    assert "input_ranges_across_scenarios" not in presentation

    rendered = render_calculations("{{scenario_assumptions_table}}", (), scenario_delivery=delivery)
    assert "range ±2%" in rendered and "range ±50 basis points" in rendered
    assert "[nvda-q2-release]" in rendered
    assert "-5.00%" in rendered and "15.00%" in rendered
    assert "108000000000" not in rendered


def test_guidance_range_requires_one_matching_value_bound_clause_and_escapes_table_cells():
    mismatched = _guidance_context(
        "Revenue is expected to be $99 billion, plus or minus 2%."
    )
    ambiguous = _guidance_context(
        "Revenue is expected to be $100 billion, plus or minus 2%. "
        "Revenue is expected to be $100 billion, plus or minus 3%."
    )
    matched = _guidance_context(
        "Revenue is expected to be $100 billion, plus or minus 2%.",
        label="Case | label\nwith newline",
    )

    assert case_reader_delivery(mismatched)["scenario_presentation"]["rows"][0]["inputs"][0][
        "issuer_guidance_range"
    ] is None
    assert case_reader_delivery(ambiguous)["scenario_presentation"]["rows"][0]["inputs"][0][
        "issuer_guidance_range"
    ] is None
    delivery = case_reader_delivery(matched)
    witness = delivery["scenario_presentation"]["rows"][0]["inputs"][0]["issuer_guidance_range"]
    assert witness == {
        "range": "±2%",
        "source_id": "issuer-release",
        "exact_excerpt": "Revenue is expected to be $100 billion, plus or minus 2%",
    }
    rendered = render_calculations("{{scenario_assumptions_table}}", (), scenario_delivery=delivery)
    assert "Case \\| label with newline" in rendered


def test_unsafe_guidance_clause_is_retained_as_an_unclassified_exact_witness():
    context = _guidance_context(
        "GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively, "
        "plus or minus 50 basis points."
    )

    input_item = case_reader_delivery(context)["scenario_presentation"]["rows"][0]["inputs"][1]

    assert input_item["issuer_guidance_range"] is None
    assert input_item["unclassified_guidance_witnesses"] == [{
        "source_id": "issuer-release",
        "exact_excerpt": (
            "GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively, "
            "plus or minus 50 basis points."
        ),
    }]


def test_new_delivery_and_table_ignore_ambient_decimal_precision_rounding_and_traps():
    context = _guidance_context("Revenue is expected to be $100 billion, plus or minus 2%.")
    baseline_delivery = case_reader_delivery(context)
    baseline_rendered = render_calculations(
        "{{scenario_assumptions_table}}", (), scenario_delivery=baseline_delivery
    )
    constrained = Context(prec=2, rounding=ROUND_DOWN)
    constrained.traps[Inexact] = True
    constrained.traps[Rounded] = True
    with localcontext(constrained):
        delivery = case_reader_delivery(context)
        rendered = render_calculations("{{scenario_assumptions_table}}", (), scenario_delivery=delivery)

    assert delivery == baseline_delivery
    assert rendered == baseline_rendered
