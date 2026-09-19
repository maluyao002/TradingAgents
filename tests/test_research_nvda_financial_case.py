"""Portable synthetic note layouts plus optional immutable NVDA case replay."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research_nvda_financial_case import (
    ACCRUALS,
    ADDITIONAL,
    COMMITMENTS,
    build_nvda_case,
    case_analysis,
    extract_case_evidence,
    inherited_proxy_audit,
    prepare_financial_case,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.storage import read_json


def synthetic_snapshot():
    accruals = "\n".join(f"{label} {8 if name == 'total_accruals' else 1} {8 if name == 'total_accruals' else 1}"
                         for name, label in ACCRUALS)
    header = "Future commitments by fiscal year as of July 26, 2026, were as follows:\nRemainder of 2027 2028 2029 2030 2031 2032 and thereafter Total (In billions)\n"
    commitments = "\n".join(f"{label} 1 1 1 1 1 1 6" if name != "commitments" else f"{label} 5 5 5 5 5 5 30"
                            for name, label in COMMITMENTS)
    additional = "\n".join(f"{label} 1 1 1 1 1 1 6" if name != "additional_commitments" else f"{label} 2 2 2 2 2 2 12"
                           for name, label in ADDITIONAL)
    text = ("Synthetic test, not issuer evidence.\n"
        "The number of shares of common stock, $0.001 par value, outstanding as of August 21, 2026, was 24.1 billion.\n\n[PDF page 2]\n"
        "Jul 26, 2026 Jan 25, 2026\nAccrued and Other Current Liabilities: (In millions)\n"
        + accruals + "\n(1) Included customer advances\n"
        "Note 10 - Commitments and Contingencies\n" + header + commitments + "\nSupply and capacity  – synthetic terms.\n"
        "Additional Commitments\n" + header + additional + "\nAI cloud agreements – synthetic terms.\n"
        "Accrual for Product Warranty Liabilities\n"
        "Note 14 - Leases\nFuture minimum lease obligations as of July 26, 2026\n(In millions)\n"
        "2027 (the second half of fiscal year 2027) 1\n2028 1\n2029 1\n2030 1\n2031 1\n2032 and thereafter 1\n"
        "Total 6\nLess imputed interest 1\nPresent value of net future minimum lease payments 5\n"
        "Less short-term operating lease liabilities 1\nLong-term operating lease liabilities 4\nAs of July 26, 2026.\n"
        "Other information related to leases\nLiquidity\nOur primary sources are synthetic.\nCapital Return to Shareholders\n"
        "(1) Included $36.9 billion synthetic restriction marker.\nPublicly-held equity securities are subject to test risks.")
    release = ("Outlook\nNVIDIA’s outlook for the third quarter of fiscal 2027 is as follows:\nRevenue is expected to be $108.0 billion, plus or minus 2%.\n"
        "NVIDIA is not assuming any Data Center compute revenue from China in its outlook.\n"
        "gross margins are expected to be 74.0%, plus or minus 50 basis points.\n"
        "operating expenses are expected to be approximately $9.2 billion and $9.0 billion\nHighlights\n")
    call = ("Let me turn to the outlook for the third quarter. Synthetic fiscal year 2028 revenue to grow approximately 70% year-over-year.\n"
        "With that, we will now transition")
    def source(identifier, content):
        return SourceDocument(id=identifier, url="https://example.test/synthetic", title="Synthetic",
            publisher="Test", retrieved_at="2026-09-18T01:00:00Z", published_at="2026-08-26T20:00:00Z",
            content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest())
    return EvidenceSnapshot(ticker="NVDA", cutoff="2026-09-18T02:00:00Z", facts=(),
        sources=(source("nvda-q2-filing", text), source("nvda-q2-release", release), source("nvda-q2-call", call)))


def replace_text(snapshot, old, new):
    source = snapshot.sources[0]
    text = source.content.replace(old, new)
    source = source.model_copy(update={"content": text, "content_sha256": hashlib.sha256(text.encode()).hexdigest()})
    return snapshot.model_copy(update={"sources": (source, *snapshot.sources[1:])})


def test_extracts_and_reconciles_complete_synthetic_rows_without_mutation():
    snapshot = synthetic_snapshot()
    enriched, materials = extract_case_evidence(snapshot)
    assert len(enriched.facts) == 93
    assert not snapshot.facts
    assert extract_case_evidence(enriched)[0] == enriched
    assert all(item.text == next(source.content for source in snapshot.sources if source.id == item.source_id)[item.start:item.end]
               for item in materials)
    assert "Jul 26, 2026 Jan 25, 2026" in materials[0].text


@pytest.mark.parametrize("old,new", [
    ("Other 1 1", "Other 2 1"),
    ("Other 1 1", "Other 1 1 99"),
    ("Total 5 5 5 5 5 5 30", "Total 5 5 5 5 5 5 30 99"),
    ("Total 5 5 5 5 5 5 30", "Total 5 5 5 5 5 5 31"),
    ("Jul 26, 2026 Jan 25, 2026", "Jul 26, 2026 Jan 26, 2025"),
    ("(In billions)", "(In millions)"),
    ("Long-term operating lease liabilities 4", "Long-term operating lease liabilities 5"),
    ("August 21, 2026", "August 22, 2026"),
])
def test_changed_rows_dates_units_and_extra_cells_fail(old, new):
    with pytest.raises(ValueError):
        extract_case_evidence(replace_text(synthetic_snapshot(), old, new))


def test_late_source_fails():
    snapshot = synthetic_snapshot()
    snapshot = snapshot.model_copy(update={"cutoff": datetime(2026, 8, 27, tzinfo=timezone.utc)})
    with pytest.raises(ValueError, match="eligible"):
        extract_case_evidence(snapshot)


def test_optional_frozen_filing_reconciles_and_keeps_history():
    path = Path(__file__).resolve().parents[1] / "reports/RESEARCH_MODEL_20260918/scenario_packet_1/evidence.json"
    if not path.is_file():
        pytest.skip("optional local packet absent")
    original = path.read_bytes()
    snapshot = EvidenceSnapshot.model_validate(read_json(path))
    enriched, _ = extract_case_evidence(snapshot)
    facts = {item.id: item for item in enriched.facts}
    assert facts["nvda-case-total_accruals-current"].value == 26960
    assert facts["nvda-case-commitments-total"].normalized_value == 366_000_000_000
    assert facts["nvda-case-lease-pv"].value == 5494
    assert facts["nvda-case-basic-shares-cover"].metric != "diluted_shares"
    assert path.read_bytes() == original


def synthetic_case_inputs():
    from scripts.research_nvda_scenarios import authored_cases
    from tests.test_research_scenario_compiler import _request
    snapshot, materials = extract_case_evidence(synthetic_snapshot())
    values = {
        "nvda-model-accounts_receivable-q2-fy27-end": 100,
        "nvda-model-inventory-q2-fy27-end": 20,
        "nvda-model-accounts_payable-q2-fy27-end": 10,
        "nvda-model-prepaid_and_other_current_assets-q2-fy27-end": 3,
        "nvda-model-cash_and_cash_equivalents-q2-fy27-end": 5,
        "nvda-model-marketable_debt_securities-q2-fy27-end": 6,
        "nvda-model-marketable_equity_securities-q2-fy27-end": 7,
        "nvda-nonmarketable_securities-q2-end": 8,
        "nvda-model-short_term_debt-q2-fy27-end": 10,
        "nvda-model-long_term_debt-q2-fy27-end": 15,
        "nvda-operating-working-capital-q2-fy27-end": 105,
        "nvda-net-debt-q2-fy27-end": 20,
        "nvda-diluted-shares-q2-fy27": 10,
        "nvda-revenue-ttm-q2-fy27": 1000,
    }
    facts = tuple(FinancialFact(id=key, source_id="nvda-q2-filing", metric=key, value=value,
        scale=1000000, unit="shares" if "shares" in key else "USD", currency=None if "shares" in key else "USD",
        period_start="2025-07-28" if "ttm" in key else None,
        period_type="duration" if "ttm" in key else "instant",
        period_end="2026-07-26", basis="Synthetic test", location="Synthetic value, not issuer data")
        for key, value in values.items())
    snapshot = snapshot.model_copy(update={"facts": (*snapshot.facts, *facts)})
    request = _request(ticker="NVDA", cutoff=snapshot.cutoff)
    cases, _ = authored_cases(request, {"risk_free_rate": ".05", "erp": ".04", "unlevered_beta_cash_corrected": "1.5"})
    return snapshot, materials, request, cases


def test_partial_case_recomputes_classifications_without_equity_or_funding_clearance():
    from tradingagents.research.financial_case import reconcile_financial_case
    snapshot, materials, request, cases = synthetic_case_inputs()
    case = build_nvda_case(snapshot, materials, request)
    result = reconcile_financial_case(case, snapshot)
    analysis = case_analysis(snapshot, materials, cases)
    assert analysis["working_capital"]["identified_operating_components"] == 105_000_000
    assert analysis["working_capital"]["classification_low"] == 104_000_000
    assert analysis["working_capital"]["classification_high"] == 108_000_000
    assert analysis["liquidity"]["excess_cash"] is None
    assert not analysis["expectations_comparison"]["comparable"]
    assert analysis["expectations_comparison"]["consensus"]["value"] is None
    assert result.output_eligibility.equity_per_share_value.status == "blocked"
    assert result.output_eligibility.funding_assessment.status != "conditional"
    assert result.commitments.incremental_subtotal is None
    assert len(result.commitments.unassessed_ids) == 42
    assert {item.disclosed_timing for item in case.commitments.items} == {
        "fy27-h2", "fy28", "fy29", "fy30", "fy31", "fy32-plus"}


def test_all_commitment_rows_use_explicit_key_group_kinds_and_remain_unassessed():
    snapshot, materials, request, _ = synthetic_case_inputs()
    case = build_nvda_case(snapshot, materials, request)
    expected_kinds = {
        "supply": "purchase",
        "capex": "purchase",
        "cloud": "cloud",
        "ai_cloud": "cloud",
        "uncommenced_leases": "lease",
        "third_party_leases": "lease",
        "investments": "other",
    }
    assert len(case.commitments.items) == 42
    for key, expected_kind in expected_kinds.items():
        items = [item for item in case.commitments.items if item.id.startswith(f"commitment-{key}-")]
        assert len(items) == 6
        assert {item.kind for item in items} == {expected_kind}
        assert {(item.timing, item.overlap, item.treatment) for item in items} == {
            ("unknown", "unknown", "not_assessed")}


@pytest.mark.parametrize("cutoff,zone,opening", [
    ("2026-09-18T23:30:00Z", "Asia/Tokyo", "2026-09-19"),
    ("2026-09-18T01:30:00Z", "America/Los_Angeles", "2026-09-17"),
])
def test_builder_preserves_timezone_at_local_day_boundaries(cutoff, zone, opening):
    from tradingagents.research.financial_case import reconcile_financial_case
    snapshot, materials, request, _ = synthetic_case_inputs()
    snapshot = EvidenceSnapshot.model_validate({**snapshot.model_dump(), "cutoff": cutoff})
    request = type(request).model_validate({**request.model_dump(), "cutoff": cutoff, "timezone": zone})
    case = build_nvda_case(snapshot, materials, request)
    assert case.opening_date.isoformat() == opening
    assert case.timezone == zone
    assert reconcile_financial_case(case, snapshot).timezone == zone


def test_mechanical_reference_has_three_cases_27_cells_and_no_equity_results():
    snapshot, _, _, cases = synthetic_case_inputs()
    audit = inherited_proxy_audit(snapshot, cases)
    assert len(audit["cases"]) == 3
    assert sum(len(case["sensitivities"]) for case in audit["cases"]) == 27
    assert "RAW MECHANICAL AUDIT" in audit["scope"]
    assert all("equity_value" not in case for case in audit["cases"])


@pytest.mark.parametrize("updates", [
    {"unit": "shares", "currency": None}, {"currency": "EUR"},
    {"period_type": "instant"}, {"period_end": "2026-07-25"},
    {"period_start": "2025-01-01"},
])
def test_ttm_denominator_requires_exact_unit_and_duration(updates):
    snapshot, materials, _, cases = synthetic_case_inputs()
    facts = tuple(FinancialFact.model_validate({**fact.model_dump(), **updates})
        if fact.id == "nvda-revenue-ttm-q2-fy27" else fact for fact in snapshot.facts)
    with pytest.raises(ValueError, match="TTM revenue"):
        case_analysis(snapshot.model_copy(update={"facts": facts}), materials, cases)


@pytest.mark.parametrize("old,new", [
    ("NVIDIA is not assuming any Data Center compute revenue from China in its outlook.", ""),
    ("third quarter of fiscal 2027", "fourth quarter of fiscal 2027"),
])
def test_guidance_fields_require_disclosed_period_and_china_exclusion(old, new):
    snapshot, materials, _, cases = synthetic_case_inputs()
    materials = tuple(item.model_copy(update={"text": item.text.replace(old, new)})
        if item.id == "issuer-guidance" else item for item in materials)
    with pytest.raises(ValueError, match="guidance selectors"):
        case_analysis(snapshot, materials, cases)


def test_optional_preparation_creates_new_draft_and_keeps_original_bytes(tmp_path):
    packet = Path(__file__).resolve().parents[1] / "reports/RESEARCH_MODEL_20260918/scenario_packet_1"
    if not packet.is_dir():
        pytest.skip("optional local packet absent")
    before = {path.name: path.read_bytes() for path in packet.iterdir() if path.is_file()}
    output = tmp_path / "new-parent" / "case"
    result = prepare_financial_case(packet, output)
    assert result["status"] == "draft_requires_fresh_independent_review"
    assert result["model_calls"] == 0
    assert not (output / "review.json").exists()
    assert not (output / "scenario_packet/review.json").exists()
    assert (output / "reconciliation.json").is_file()
    assert read_json(output / "scenario_packet_hashes.json") == read_json(output / "scenario_packet/manifest.json")["artifact_hashes"]
    with pytest.raises(ValueError, match="new directory"):
        prepare_financial_case(packet, output)
    assert before == {path.name: path.read_bytes() for path in packet.iterdir() if path.is_file()}
