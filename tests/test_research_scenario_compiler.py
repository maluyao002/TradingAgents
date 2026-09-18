"""Deterministic assumption-package to FCFF scenario compilation tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradingagents.research.assumptions import (
    REQUIRED_FCFF_PATHS,
    AssumptionEntry,
    AssumptionPackage,
    AssumptionRange,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
    default_roles,
)
from tradingagents.research.scenario_compiler import ConditionalScenario, compile_scenarios
from tradingagents.research.storage import digest
from tradingagents.research.valuation import ForecastPeriod

D = Decimal
CUTOFF = "2025-01-15T12:00:00+00:00"


def _source() -> SourceDocument:
    content = "Synthetic historical statements for compiler tests; no recommendation."
    return SourceDocument(
        id="filing",
        url="https://example.test/filing",
        title="Synthetic filing",
        publisher="Synthetic issuer",
        retrieved_at="2025-01-14T12:00:00+00:00",
        published_at="2025-01-10T12:00:00+00:00",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        kind="filing",
    )


def _fact(identifier: str, metric: str, value: str, **updates) -> FinancialFact:
    values = {
        "id": identifier,
        "source_id": "filing",
        "metric": metric,
        "value": value,
        "scale": "1000000",
        "unit": "USD",
        "currency": "USD",
        "period_end": "2024-12-31",
        "basis": "US GAAP",
        "location": "synthetic fixture",
    }
    values.update(updates)
    return FinancialFact(**values)


def _snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        ticker="TEST",
        cutoff=CUTOFF,
        sources=(_source(),),
        facts=(
            _fact(
                "revenue",
                "revenue",
                "100",
                period_start="2024-01-01",
                period_type="duration",
            ),
            _fact("working-capital", "working_capital", "10"),
            _fact("net-debt", "net_debt", "20"),
            _fact(
                "shares",
                "diluted_shares",
                "10",
                unit="shares",
                currency=None,
            ),
        ),
        gaps=("Synthetic fixtures do not validate a fundamental forecast.",),
    )


def _request(**updates) -> ResearchRequest:
    values = {
        "ticker": "TEST",
        "cutoff": CUTOFF,
        "timezone": "UTC",
        "backend": "api",
        "output_dir": "synthetic-output",
        "quality_revision": "evidence-led-bounded",
        "valuation_method": "fcff",
        "models": {role: setting.model_dump() for role, setting in default_roles().items()},
    }
    values.update(updates)
    return ResearchRequest(**values)


def _numeric(
    identifier: str,
    path: str,
    low: str,
    base: str,
    high: str,
    *,
    category: str = "analyst_assumption",
    evidence_ids: tuple[str, ...] = ("filing",),
    fact_id: str | None = None,
    unit: str = "fraction",
) -> AssumptionEntry:
    return AssumptionEntry(
        id=identifier,
        model_path=path,
        category=category,
        status="reviewed",
        unit=unit,
        rationale="Synthetic bounded support for deterministic compiler testing only.",
        evidence_ids=evidence_ids,
        range=AssumptionRange(low=low, base=base, high=high),
        fact_id=fact_id,
    )


def _entries() -> tuple[AssumptionEntry, ...]:
    historical = (
        _numeric(
            "revenue-anchor", "current_revenue", "100000000", "100000000", "100000000",
            category="historical_anchor", evidence_ids=("revenue",), fact_id="revenue", unit="USD",
        ),
        _numeric(
            "wc-anchor", "current_working_capital", "10000000", "10000000", "10000000",
            category="historical_anchor", evidence_ids=("working-capital",),
            fact_id="working-capital", unit="USD",
        ),
        _numeric(
            "debt-anchor", "net_debt", "20000000", "20000000", "20000000",
            category="historical_anchor", evidence_ids=("net-debt",), fact_id="net-debt", unit="USD",
        ),
        _numeric(
            "share-anchor", "current_diluted_shares", "10000000", "10000000", "10000000",
            category="historical_anchor", evidence_ids=("shares",), fact_id="shares", unit="shares",
        ),
    )
    ranges = {
        "discount_rate": ("0.09", "0.10", "0.11"),
        "terminal_growth": ("0.02", "0.03", "0.04"),
        "periods.*.revenue_growth": ("0.05", "0.10", "0.15"),
        "periods.*.operating_margin": ("0.15", "0.20", "0.25"),
        "periods.*.tax_rate": ("0.20", "0.25", "0.30"),
        "periods.*.depreciation_amortization_pct_revenue": ("0.04", "0.05", "0.06"),
        "periods.*.capex_pct_revenue": ("0.03", "0.04", "0.05"),
        "periods.*.working_capital_pct_revenue": ("0.08", "0.10", "0.12"),
        "periods.*.sbc_pct_revenue": ("0.02", "0.03", "0.04"),
    }
    numeric = tuple(
        _numeric(f"numeric-{index}", path, *bounds)
        for index, (path, bounds) in enumerate(ranges.items())
    )
    convention_values = {
        "as_of_date": "2025-01-15",
        "units.currency": "USD",
        "units.amount_scale": "1000000",
        "units.share_scale": "1000000",
        "periods.*.operating_margin_basis": "after_sbc",
        "periods.*.external_funding_required": "false",
        "forecast_schedule": "annual periods from the local cutoff date",
    }
    conventions = tuple(
        AssumptionEntry(
            id=f"convention-{index}",
            model_path=path,
            category="model_convention",
            status="reviewed",
            unit="convention",
            rationale="Explicit modeling convention, not a reported future fact.",
            value=value,
        )
        for index, (path, value) in enumerate(convention_values.items())
    )
    entries = historical + numeric + conventions
    assert {entry.model_path for entry in entries} == set(REQUIRED_FCFF_PATHS)
    return entries


def _package(snapshot: EvidenceSnapshot) -> tuple[AssumptionPackage, bytes]:
    evidence_bytes = snapshot.model_dump_json().encode()
    return AssumptionPackage(
        ticker="TEST",
        cutoff=CUTOFF,
        created_at="2025-01-16T12:00:00+00:00",
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
        valuation_method="fcff",
        entries=_entries(),
        limitations=(
            "Conditional source-linked modeling only.",
            "Synthetic fixtures are not financial recommendations.",
        ),
        reviewer="Casey Reviewer",
        reviewed_at="2025-01-17T12:00:00+00:00",
    ), evidence_bytes


def _period(label: str, start: date, end: date, years: str, **updates) -> ForecastPeriod:
    values = {
        "label": label,
        "period_start": start,
        "period_end": end,
        "discount_years": D(years),
        "revenue_growth": D("0.10"),
        "operating_margin": D("0.20"),
        "operating_margin_basis": "after_sbc",
        "tax_rate": D("0.25"),
        "depreciation_amortization_pct_revenue": D("0.05"),
        "capex_pct_revenue": D("0.04"),
        "working_capital_pct_revenue": D("0.10"),
        "sbc_pct_revenue": D("0.03"),
        "external_funding_required": False,
    }
    values.update(updates)
    return ForecastPeriod(**values)


def _scenario(identifier: str = "base", **updates) -> ConditionalScenario:
    values = {
        "id": identifier,
        "thesis": "Synthetic conditional operating case; not a forecast endorsement.",
        "periods": (
            _period("FY2026", date(2025, 1, 15), date(2026, 1, 15), "1"),
            _period("FY2027", date(2026, 1, 15), date(2027, 1, 15), "2"),
        ),
        "discount_rate": D("0.10"),
        "terminal_growth": D("0.03"),
        "limitations": ("Future operating assumptions remain conditional.",),
    }
    values.update(updates)
    return ConditionalScenario(**values)


def _compile(scenarios: tuple[ConditionalScenario, ...] | None = None):
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    return compile_scenarios(
        package, snapshot, _request(), evidence_bytes, scenarios or (_scenario(),)
    )


def _replace_entry(package: AssumptionPackage, path: str, **updates) -> AssumptionPackage:
    return package.model_copy(update={
        "entries": tuple(
            entry.model_copy(update=updates) if entry.model_path == path else entry
            for entry in package.entries
        )
    })


def test_compiles_million_scaled_source_bound_fcff_case() -> None:
    compiled = _compile()
    json.dumps(compiled)
    assert compiled["status"] == "conditional_illustrative"
    assert compiled["review"]["reviewer"] == "Casey Reviewer"
    case = compiled["cases"][0]
    assert case["typed_input"]["current_revenue"] == "100"
    assert case["typed_input"]["current_diluted_shares"] == "10"
    assert case["typed_input"]["units"] == {
        "amount_scale": "1000000", "currency": "USD", "share_scale": "1000000"
    }
    assert set(case["opening_input_bindings"]) == {
        "current_revenue", "current_working_capital", "net_debt", "current_diluted_shares"
    }
    assert case["decomposition"]["enterprise_value"] == case["result"]["enterprise_value"]
    assert case["decomposition"]["equity_value"] == case["result"]["equity_value"]
    assert case["decomposition"]["value_per_current_diluted_share"] == (
        case["result"]["value_per_current_diluted_share"]
    )
    assert case["hashes"]["typed_input_sha256"] == digest(case["typed_input"])
    assert case["hashes"]["proposal_sha256"] == digest(case["proposal"])
    assert case["hashes"]["result_sha256"] == digest(case["result"])
    date_support = case["proposal"]["assumptions"]["periods.0.period_end"]
    assert date_support["kind"] == "assumption"
    assert "Modeling convention" in date_support["rationale"]
    assert date_support["evidence_ids"] == ["revenue", "working-capital", "net-debt", "shares"]


def test_wrong_evidence_hash_and_unfinished_package_fail_closed() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    wrong_hash = package.model_copy(update={"evidence_sha256": "0" * 64})
    with pytest.raises(ValueError, match="evidence_sha256"):
        compile_scenarios(wrong_hash, snapshot, _request(), evidence_bytes, (_scenario(),))

    unfinished = _replace_entry(package, "discount_rate", status="draft")
    with pytest.raises(ValueError, match="draft required assumption: discount_rate"):
        compile_scenarios(unfinished, snapshot, _request(), evidence_bytes, (_scenario(),))


def test_out_of_range_inputs_and_invalid_model_dates_are_rejected() -> None:
    periods = _scenario().periods
    out_of_range = _scenario(periods=(replace(periods[0], revenue_growth=D("0.16")), periods[1]))
    with pytest.raises(ValueError, match="outside the reviewed range.*revenue_growth"):
        _compile((out_of_range,))

    with pytest.raises(ValueError, match="outside the reviewed range: discount_rate"):
        _compile((_scenario(discount_rate=D("0.12")),))

    gap = _scenario(periods=(
        periods[0], replace(periods[1], period_start=date(2026, 1, 16))
    ))
    with pytest.raises(ValueError, match="start at as_of_date and remain contiguous"):
        _compile((gap,))


@pytest.mark.parametrize(
    "period_update, message",
    [
        ({"operating_margin_basis": "before_sbc"}, "operating_margin_basis"),
        ({"external_funding_required": True}, "external_funding_required"),
    ],
)
def test_package_margin_and_funding_conventions_are_exact(period_update, message) -> None:
    periods = _scenario().periods
    changed = _scenario(periods=(replace(periods[0], **period_update), periods[1]))
    with pytest.raises(ValueError, match=message):
        _compile((changed,))


def test_unknown_package_evidence_and_duplicate_cases_are_rejected() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    unknown = _replace_entry(
        package, "periods.*.sbc_pct_revenue", evidence_ids=("invented",)
    )
    with pytest.raises(ValueError, match="ineligible evidence IDs"):
        compile_scenarios(unknown, snapshot, _request(), evidence_bytes, (_scenario(),))

    with pytest.raises(ValueError, match="scenario IDs must be unique"):
        _compile((_scenario(), _scenario()))


def test_contract_has_no_probability_or_investment_target_and_output_is_not_accepted() -> None:
    payload = _scenario().model_dump()
    with pytest.raises(ValidationError):
        ConditionalScenario(**{**payload, "probability": D("0.5")})
    with pytest.raises(ValidationError):
        ConditionalScenario(**{**payload, "investment_target": D("200")})

    compiled = _compile()
    assert compiled["status"] != "accepted"
    assert "not human review" in compiled["scope"]

    def keys(value):
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in keys(item)}
        return set()

    assert "probability" not in keys(compiled)
    assert "investment_target" not in keys(compiled)
