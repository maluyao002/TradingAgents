from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.research_nvda_forecast_facts import (
    CURRENT_CAPEX_FACT_ID,
    CURRENT_TAX_FACT_ID,
    Q2_FILING_SOURCE_ID,
    Q2_RELEASE_SOURCE_ID,
    ForecastFactExtractionError,
    enrich_snapshot,
    extract_forecast_facts,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    SourceDocument,
)
from tradingagents.research.storage import canonical_json, read_json

FILING_TEXT = """NVIDIA Corporation and Subsidiaries
Condensed Consolidated Statements of Income
(In millions, except per share data)
(Unaudited)
  Three Months Ended Six Months Ended
  Jul 26, 2026 Jul 27, 2025 Jul 26, 2026 Jul 27, 2025
Revenue $ 96,221 $ 46,743 $ 177,837 $ 90,805␠
Operating income  63,734  28,440  117,270  50,078␠
Other income, net  7,773  2,766  24,140  3,039␠
Income before income tax  71,507  31,206  141,410  53,117␠
Income tax expense  11,819  4,784  23,400  7,920␠
Net income $ 59,688 $ 26,422 $ 118,010 $ 45,197␠
See accompanying Notes to Condensed Consolidated Financial Statements.

NVIDIA Corporation and Subsidiaries
Condensed Consolidated Statements of Cash Flows
(In millions)
(Unaudited)
  Six Months Ended
  Jul 26, 2026 Jul 27, 2025
Cash flows from operating activities:
Net income $ 118,010 $ 45,197␠
Adjustments to reconcile net income to net cash provided by operating␠
activities:
Stock-based compensation expense  3,954  3,099␠
Depreciation and amortization  2,124  1,280␠
Deferred income taxes  982  (2,160)␠
Net cash provided by operating activities  74,421  42,779␠
Cash flows from investing activities:
Purchases of debt securities  (21,777)  (14,108)␠
Purchases related to property and equipment and intangible assets  (4,434)  (3,122)␠
Acquisitions, net of cash acquired  (298)  (677)␠
Cash and cash equivalents at end of period $ 22,443 $ 11,639␠
See accompanying Notes to Condensed Consolidated Financial Statements.

Basis of Presentation
The accompanying unaudited condensed consolidated financial statements were prepared in accordance with accounting␠
principles generally accepted in the United States of America, or U.S. GAAP, for interim financial information and with the␠
instructions to Form 10-Q and Article 10 of Securities and Exchange Commission, or SEC, Regulation S-X.

Note 11 - Income Taxes
Income tax expense was $11.8 billion and $4.8 billion for the second quarter, and $23.4 billion and $7.9 billion for the first␠
half, of fiscal years 2027 and 2026, respectively. Income tax as a percentage of income before income tax was 16.5% and␠
15.3% for the second quarter, and 16.5% and 14.9% for the first half, of fiscal years 2027 and 2026, respectively.
""".replace("␠", " ")

RELEASE_TEXT = """NVIDIA CORPORATION
CONDENSED CONSOLIDATED STATEMENTS OF INCOME
(In millions, except per share data)
(Unaudited)
Three Months Ended
Six Months Ended
July 26,
July 27,
July 26,
July 27,
2026
2025
2026
2025
Revenue
$
96,221
$
46,743
$
177,837
$
90,805
Operating income
63,734
28,440
117,270
50,078
Other income, net
7,773
2,766
24,140
3,039
Income before income tax
71,507
31,206
141,410
53,117
Income tax expense
11,819
4,784
23,400
7,920
Net income
$
59,688
$
26,422
$
118,010
$
45,197
NVIDIA CORPORATION
CONDENSED CONSOLIDATED BALANCE SHEETS

NVIDIA CORPORATION
CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS
(In millions)
(Unaudited)
Three Months Ended
Six Months Ended
July 26,
July 27,
July 26,
July 27,
2026
2025
2026
2025
Cash flows from operating activities:
Stock-based compensation expense
2,027
1,624
3,954
3,099
Depreciation and amortization
1,127
668
2,124
1,280
Deferred income taxes
(602
)
18
982
(2,160
)
Cash flows from investing activities:
Purchases of debt securities
(21,777
)
(7,812
)
(21,777
)
(14,108
)
Purchases related to property and equipment and intangible assets
(2,677
)
(1,894
)
(4,434
)
(3,122
)
Acquisitions, net of cash acquired
(211
)
(294
)
(298
)
(677
)
NVIDIA CORPORATION
RECONCILIATION OF GAAP TO NON-GAAP FINANCIAL MEASURES
"""


def _source(identifier: str, content: str) -> SourceDocument:
    return SourceDocument(
        id=identifier,
        url=f"https://example.test/{identifier}",
        title=identifier,
        publisher="NVIDIA",
        retrieved_at=datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 27, 23, tzinfo=timezone.utc),
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        kind="filing" if identifier == Q2_FILING_SOURCE_ID else "ir",
    )


def _fact(
    identifier: str,
    *,
    metric: str = "fixture_metric",
    value: str = "1",
    period_start: str = "2026-01-26",
    period_end: str = "2026-07-26",
) -> FinancialFact:
    return FinancialFact(
        id=identifier,
        source_id=Q2_RELEASE_SOURCE_ID,
        metric=metric,
        value=value,
        scale="1000000",
        unit="USD",
        currency="USD",
        period_start=period_start,
        period_end=period_end,
        period_type="duration",
        basis="US GAAP",
        location="pre-existing frozen fixture fact",
    )


def _snapshot(
    *,
    filing_text: str = FILING_TEXT,
    release_text: str = RELEASE_TEXT,
    tax_value: str = "23400",
    capex_value: str = "-4434",
) -> EvidenceSnapshot:
    prior = [
        _fact(
            CURRENT_TAX_FACT_ID,
            metric="income_tax_expense",
            value=tax_value,
        ),
        _fact(
            CURRENT_CAPEX_FACT_ID,
            metric="capex_cashflow",
            value=capex_value,
        ),
    ]
    prior.extend(_fact(f"fixture-prior-{index}") for index in range(79))
    return EvidenceSnapshot(
        ticker="NVDA",
        cutoff="2026-09-17T15:21:06+00:00",
        sources=(
            _source(Q2_RELEASE_SOURCE_ID, release_text),
            _source(Q2_FILING_SOURCE_ID, filing_text),
        ),
        facts=tuple(prior),
        gaps=("existing gap",),
    )


def _facts_by_id(snapshot: EvidenceSnapshot | None = None) -> dict[str, FinancialFact]:
    return {fact.id: fact for fact in extract_forecast_facts(snapshot or _snapshot())}


def test_extracts_only_missing_h1_reported_facts_and_same_period_tax_rates() -> None:
    facts = _facts_by_id()

    assert len(facts) == 8
    assert facts["nvda-depreciation_amortization-h1-fy27"].value == 2124
    assert facts["nvda-depreciation_amortization-h1-fy26"].value == 1280
    assert facts["nvda-depreciation_amortization-h1-fy27"].metric == (
        "depreciation_amortization"
    )
    assert facts["nvda-income_before_income_tax-h1-fy27"].value == 141410
    assert facts["nvda-income_before_income_tax-h1-fy26"].value == 53117
    assert facts["nvda-income_tax_expense-h1-fy26"].value == 7920
    assert facts["nvda-capex_cashflow-h1-fy26"].value == -3122
    assert CURRENT_TAX_FACT_ID not in facts
    assert CURRENT_CAPEX_FACT_ID not in facts

    rate27 = facts["nvda-effective_tax_rate-h1-fy27"]
    rate26 = facts["nvda-effective_tax_rate-h1-fy26"]
    assert rate27.value == Decimal(23400) / Decimal(141410)
    assert rate26.value == Decimal(7920) / Decimal(53117)
    assert rate27.unit == rate26.unit == "ratio"
    assert rate27.scale == rate26.scale == 1
    assert rate27.inputs == (
        CURRENT_TAX_FACT_ID,
        "nvda-income_before_income_tax-h1-fy27",
    )
    assert rate26.inputs == (
        "nvda-income_tax_expense-h1-fy26",
        "nvda-income_before_income_tax-h1-fy26",
    )


def test_facts_retain_exact_source_location_and_never_create_quarter_values() -> None:
    facts = tuple(_facts_by_id().values())
    direct = [fact for fact in facts if not fact.inputs]

    assert all(fact.basis == "US GAAP" for fact in facts)
    assert all(fact.period_type == "duration" for fact in facts)
    assert {fact.period_start.isoformat() for fact in facts} == {
        "2025-01-27",
        "2026-01-26",
    }
    assert {fact.period_end.isoformat() for fact in facts} == {
        "2025-07-27",
        "2026-07-26",
    }
    assert all("source_sha256=" in fact.location for fact in direct)
    assert all("chars=[" in fact.location and "lines=" in fact.location for fact in direct)
    assert all("table_chars=[" in fact.location for fact in direct)
    assert all("exact_row=" in fact.location and "column=" in fact.location for fact in direct)
    assert all("Six Months Ended" in fact.location for fact in direct)
    assert not any("q1" in fact.id or "q2" in fact.id for fact in facts)


@pytest.mark.parametrize(
    ("target", "replacement", "message"),
    [
        ("Six Months Ended\n\u00a0 Jul 26", "Half Year Ended\n\u00a0 Jul 26", "exact header"),
        ("Depreciation and amortization  2,124", "D&A  2,124", "required row"),
        ("Income before income tax  71,507", "Pretax income  71,507", "required row"),
    ],
)
def test_changed_filing_header_or_row_fails_closed(
    target: str, replacement: str, message: str
) -> None:
    changed = FILING_TEXT.replace(target, replacement, 1)
    with pytest.raises(ForecastFactExtractionError, match=message):
        extract_forecast_facts(_snapshot(filing_text=changed))


def test_release_disagreement_and_existing_operand_conflicts_fail_closed() -> None:
    changed_release = RELEASE_TEXT.replace("2,124\n1,280", "2,125\n1,280", 1)
    with pytest.raises(ForecastFactExtractionError, match="filing and release.*disagree"):
        extract_forecast_facts(_snapshot(release_text=changed_release))

    with pytest.raises(ForecastFactExtractionError, match="conflicts with the exact frozen row"):
        extract_forecast_facts(_snapshot(tax_value="23399"))
    with pytest.raises(ForecastFactExtractionError, match="conflicts with the exact frozen row"):
        extract_forecast_facts(_snapshot(capex_value="4434"))


def test_enrichment_preserves_81_facts_and_source_bytes_and_writes_exclusively(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "frozen"
    source_dir.mkdir()
    base = source_dir / "evidence_with_model_facts.json"
    original = _snapshot()
    base_bytes = canonical_json(original)
    base.write_bytes(base_bytes)
    destination = tmp_path / "new" / "evidence_with_forecast_facts.json"

    result = enrich_snapshot(base, destination)

    assert result.base_snapshot == base.resolve()
    assert result.destination == destination.resolve()
    assert len(result.added_fact_ids) == 8
    assert result.reused_fact_ids == (CURRENT_TAX_FACT_ID, CURRENT_CAPEX_FACT_ID)
    assert len(result.snapshot.facts) == 89
    assert result.snapshot.facts[:81] == original.facts
    assert result.snapshot.sources == original.sources
    assert [source.content.encode() for source in result.snapshot.sources] == [
        source.content.encode() for source in original.sources
    ]
    assert base.read_bytes() == base_bytes
    assert EvidenceSnapshot.model_validate(read_json(destination)) == result.snapshot
    assert any("No quarterly depreciation" in gap for gap in result.snapshot.gaps)
    assert any("not statutory tax rates" in gap for gap in result.snapshot.gaps)
    assert extract_forecast_facts(result.snapshot) == ()
    assert len({fact.id for fact in result.snapshot.facts}) == 89

    with pytest.raises(ForecastFactExtractionError, match="already exists"):
        enrich_snapshot(base, destination)
    with pytest.raises(ForecastFactExtractionError, match="outside the frozen source"):
        enrich_snapshot(base, source_dir / "nested" / "out.json")


def test_real_frozen_snapshot_extracts_expected_narrow_additions() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "reports/RESEARCH_EN_20260917/nvda_inputs/evidence_with_model_facts.json"
    if not path.is_file():
        pytest.skip("optional local frozen NVDA pilot is not present in a clean checkout")
    snapshot = EvidenceSnapshot.model_validate(read_json(path))

    facts = _facts_by_id(snapshot)

    assert len(snapshot.facts) == 81
    assert len(facts) == 8
    assert facts["nvda-depreciation_amortization-h1-fy27"].value == 2124
    assert facts["nvda-depreciation_amortization-h1-fy26"].value == 1280
    assert facts["nvda-capex_cashflow-h1-fy26"].value == -3122
    assert all(fact.period_end <= snapshot.cutoff.date() for fact in facts.values())
