from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research_hood_model_facts import (
    FY2025_SOURCE_ID,
    Q2_SOURCE_ID,
    ModelFactExtractionError,
    enrich_frozen_snapshot,
    enrich_snapshot,
    extract_model_facts,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.engine import _calculate
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import canonical_json, read_json


def _page_marked_content(page_count: int, pages: dict[int, str]) -> str:
    return "\n\n".join(
        f"[PDF page {number}]\n{pages.get(number, f'Synthetic archive page {number}.')}"
        for number in range(1, page_count + 1)
    ) + "\n"


def _q2_content() -> str:
    return _page_marked_content(
        13,
        {
            1: """
Robinhood Reports Second Quarter 2026 Results
July 29, 2026
Robinhood Markets, Inc. (Robinhood) (NASDAQ: HOOD) announced results.
""",
            4: """
ROBINHOOD MARKETS, INC.
CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
(Unaudited)
Three Months Ended June 30, YOY% Change Three Months Ended March 31, QOQ% Change
(in millions, except per share and percentage data) 2025 2026 2026
Revenues:
Transaction-based revenues $ 539 $ 776 44% $ 623 25%
Net interest revenues 357 389 9% 359 8%
Other revenues 93 143 54% 85 68%
Total net revenues 989 1,308 32% 1,067 23%
""",
            5: """
Net income $ 386 $ 573 48% $ 346 66%
Less: Net income (loss) attributable to non-controlling interests — 12 NM (4) NM
Net income attributable to Robinhood $ 386 $ 561 45% $ 350 60%
Net income attributable to Robinhood common stockholders:
Basic $ 386 $ 561 $ 350
Diluted $ 386 $ 561 $ 350
Net income per share attributable to Robinhood common stockholders:
Basic $ 0 $ 0 $ 0
Diluted $ 0 $ 0 $ 0
Weighted-average shares used to compute net income per share attributable to Robinhood common stockholders:
Basic 882 899 899
Diluted 909 912 915
ROBINHOOD MARKETS, INC.
CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
(Unaudited)
Six Months Ended June 30, YOY% Change
(in millions, except per share and percentage data) 2025 2026
Revenues:
Transaction-based revenues $ 1,122 $ 1,399 25%
Net interest revenues 647 748 16%
Other revenues 147 228 55%
Total net revenues 1,916 2,375 24%
""",
            6: """
Net income $ 722 $ 919 27%
Less: Net income (loss) attributable to non-controlling interests — 8 NM
Net income attributable to Robinhood $ 722 $ 911 26%
Net income attributable to Robinhood common stockholders:
Basic $ 722 $ 911
Diluted $ 722 $ 911
Net income per share attributable to Robinhood common stockholders:
Basic $ 0 $ 0
Diluted $ 0 $ 0
Weighted-average shares used to compute net income per share attributable to Robinhood common stockholders:
Basic 883 899
Diluted 911 913
ROBINHOOD MARKETS, INC.
CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
(Unaudited)
""",
        },
    )


def _fy2025_content() -> str:
    return _page_marked_content(
        15,
        {
            1: """
Robinhood Reports Fourth Quarter and Full Year 2025 Results
February 10, 2026
Robinhood Markets, Inc. (Robinhood) (NASDAQ: HOOD) announced results.
""",
            7: """
ROBINHOOD MARKETS, INC.
CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
(Unaudited)
Year Ended December 31, YOY% Change
(in millions, except share, per share, and percentage data) 2024 2025
Revenues:
Transaction-based revenues $ 1,647 $ 2,628 60%
Net interest revenues 1,109 1,514 37%
Other revenues 195 331 70%
Total net revenues 2,951 4,473 52%
Net income $ 1,411 $ 1,883 33%
Net income (loss) attributable to non-controlling interest — — NM
Net income attributable to Robinhood $ 1,411 $ 1,883 33%
Net income attributable to Robinhood common stockholders:
Basic $ 1,411 $ 1,883
Diluted $ 1,411 $ 1,883
Net income per share attributable to Robinhood common stockholders:
Basic $ 0 $ 0
Diluted $ 0 $ 0
Weighted-average shares used to compute net income per share attributable to Robinhood common stockholders:
Basic 881,113,156 888,504,958
Diluted 906,171,504 918,781,846
""",
        },
    )


def _source(identifier: str, content: str) -> SourceDocument:
    published_at = {
        Q2_SOURCE_ID: "2026-07-29T20:05:00+00:00",
        FY2025_SOURCE_ID: "2026-02-11T04:59:59+00:00",
    }[identifier]
    return SourceDocument(
        id=identifier,
        url=f"file:///frozen/{identifier}.pdf",
        title=identifier,
        publisher="Robinhood Markets, Inc.",
        retrieved_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        published_at=published_at,
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        kind="ir",
    )


def _snapshot(
    *, q2_content: str | None = None, fy2025_content: str | None = None
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        ticker="HOOD",
        cutoff="2026-09-17T12:00:00+00:00",
        sources=(
            _source(Q2_SOURCE_ID, q2_content or _q2_content()),
            _source(FY2025_SOURCE_ID, fy2025_content or _fy2025_content()),
        ),
        gaps=("Synthetic frozen fixture; not investment research.",),
    )


def _facts(snapshot: EvidenceSnapshot | None = None) -> dict[str, FinancialFact]:
    return {fact.id: fact for fact in extract_model_facts(snapshot or _snapshot())}


def _write_packet(packet: Path, snapshot: EvidenceSnapshot) -> bytes:
    (packet / "raw").mkdir(parents=True)
    (packet / "text").mkdir()
    records = []
    page_counts = {Q2_SOURCE_ID: 13, FY2025_SOURCE_ID: 15}
    for source in snapshot.sources:
        raw = f"%PDF-1.7\nsynthetic frozen bytes for {source.id}\n".encode()
        text = source.content.encode()
        raw_relative = f"raw/{source.id}.pdf"
        text_relative = f"text/{source.id}.txt"
        (packet / raw_relative).write_bytes(raw)
        (packet / text_relative).write_bytes(text)
        records.append(
            {
                "id": source.id,
                "acquisition": "local",
                "http_fetched_by_importer": False,
                "local_file": f"/synthetic/{source.id}.pdf",
                "original_url": source.url,
                "media_type": "application/pdf",
                "raw_archive": raw_relative,
                "raw_bytes": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "text_archive": text_relative,
                "text_bytes": len(text),
                "text_sha256": hashlib.sha256(text).hexdigest(),
                "extractor": {
                    "name": "pypdf.PdfReader.page.extract_text",
                    "version": "synthetic",
                    "pages": page_counts[source.id],
                    "page_markers": "one-based [PDF page N]",
                    "empty_text_page_count": 0,
                    "empty_text_page_ids": [],
                },
                "published_at": source.published_at.isoformat(),
                "retrieved_at": source.retrieved_at.isoformat(),
            }
        )
    original = canonical_json(snapshot)
    (packet / "evidence.json").write_bytes(original)
    (packet / "acquisition.json").write_bytes(
        canonical_json(
            {
                "schema_version": 1,
                "scope": "explicit_manifest_offline_public_documents_only",
                "coverage": "No HTTP fetch or full-web coverage is claimed by this importer.",
                "frozen_cutoff": snapshot.cutoff.isoformat(),
                "sources": records,
            }
        )
    )
    return original


def test_exact_ttm_revenue_and_common_income_ancestry() -> None:
    facts = _facts()
    revenue = facts["hood-revenue-ttm-q2-fy2026"]
    common_income = facts["hood-net-income-common-ttm-q2-fy2026"]

    assert facts["hood-revenue-fy2025"].value == 4473
    assert facts["hood-revenue-h1-fy2025"].value == 1916
    assert facts["hood-revenue-h1-fy2026"].value == 2375
    assert revenue.value == 4932
    assert revenue.inputs == (
        "hood-revenue-fy2025",
        "hood-revenue-h1-fy2025",
        "hood-revenue-h1-fy2026",
    )
    assert revenue.formula == (
        "hood-revenue-fy2025 - hood-revenue-h1-fy2025 + hood-revenue-h1-fy2026"
    )

    assert facts["hood-net-income-common-fy2025"].value == 1883
    assert facts["hood-net-income-common-h1-fy2025"].value == 722
    assert facts["hood-net-income-common-h1-fy2026"].value == 911
    assert common_income.value == 2072
    assert common_income.inputs == (
        "hood-net-income-common-fy2025",
        "hood-net-income-common-h1-fy2025",
        "hood-net-income-common-h1-fy2026",
    )
    assert common_income.formula == (
        "hood-net-income-common-fy2025 - hood-net-income-common-h1-fy2025 "
        "+ hood-net-income-common-h1-fy2026"
    )
    assert common_income.period_start.isoformat() == "2025-07-01"
    assert common_income.period_end.isoformat() == "2026-06-30"


def test_common_income_is_not_consolidated_income_and_q2_shares_are_exact_proxy() -> None:
    facts = _facts()
    shares = facts["hood-diluted-shares-q2-fy2026"]

    assert facts["hood-net-income-consolidated-q2-fy2026"].value == 573
    assert facts["hood-net-income-nci-q2-fy2026"].value == 12
    assert facts["hood-net-income-common-q2-fy2026"].value == 561
    assert facts["hood-net-income-consolidated-h1-fy2026"].value == 919
    assert facts["hood-net-income-nci-h1-fy2026"].value == 8
    assert facts["hood-net-income-common-h1-fy2026"].value == 911

    assert shares.value == 912
    assert shares.metric == "weighted_average_diluted_shares"
    assert shares.scale == 1_000_000
    assert shares.unit == "shares"
    assert shares.currency is None
    assert shares.period_type == "duration"
    assert shares.period_start.isoformat() == "2026-04-01"
    assert shares.period_end.isoformat() == "2026-06-30"
    assert (shares.period_end - shares.period_start).days + 1 == 91
    assert "not H1 diluted shares or basic shares" in shares.location
    assert [fact.metric for fact in facts.values()].count("weighted_average_diluted_shares") == 1


def test_revenue_components_reconcile_and_no_disallowed_model_facts_are_added() -> None:
    facts = _facts()
    expected_ttm = {
        "transaction-based-revenue": 2905,
        "net-interest-revenue": 1615,
        "other-revenue": 412,
        "revenue": 4932,
    }
    for slug, expected in expected_ttm.items():
        fact = facts[f"hood-{slug}-ttm-q2-fy2026"]
        assert fact.value == expected
        assert fact.inputs
        assert fact.basis == "US GAAP"
        assert fact.scale == 1_000_000

    assert sum(expected_ttm[slug] for slug in expected_ttm if slug != "revenue") == 4932
    disallowed = {
        "net_debt",
        "working_capital",
        "cash_and_cash_equivalents",
        "customer_cash",
        "required_capital_retention",
        "forecast_revenue",
        "normalized_net_income",
    }
    assert not ({fact.metric for fact in facts.values()} & disallowed)
    assert len(facts) == 32


def test_every_source_fact_has_exact_page_location_units_and_gaap_basis() -> None:
    facts = tuple(_facts().values())
    assert {fact.basis for fact in facts} == {"US GAAP"}
    for fact in facts:
        if fact.inputs:
            assert "derived TTM reported value" in fact.location
        else:
            assert fact.location.startswith("PDF page ")
            assert "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS" in fact.location
    assert "PDF page 7" in _facts()["hood-revenue-fy2025"].location
    assert "PDF page 5" in _facts()["hood-net-income-common-q2-fy2026"].location
    assert "PDF page 6" in _facts()["hood-net-income-common-h1-fy2026"].location


def test_engine_binds_ttm_common_income_and_latest_quarter_share_proxy(tmp_path: Path) -> None:
    snapshot = _snapshot()
    facts = extract_model_facts(snapshot)
    snapshot = snapshot.model_copy(update={"facts": facts})
    request = ResearchRequest(
        ticker="HOOD",
        cutoff=snapshot.cutoff,
        backend="api",
        output_dir=tmp_path,
        quality_revision="evidence-led",
        valuation_method="equity_fcfe",
        share_count_basis="latest_quarter_diluted_proxy",
    )
    model = {
        "as_of_date": "2026-09-17",
        "current_net_income": "2072",
        "periods": [
                {
                    "label": "FY2027 assumption",
                    "period_start": "2026-09-17",
                    "period_end": "2027-09-17",
                "discount_years": "1",
                "net_income_common": "2200",
                "required_capital_retention": "200",
            }
        ],
        "cost_of_equity": "0.10",
        "terminal_growth": "0.03",
        "current_diluted_shares": "912",
        "units": {
            "currency": "USD",
            "amount_scale": "1000000",
            "share_scale": "1000000",
        },
    }
    opening = {
        "current_net_income": "hood-net-income-common-ttm-q2-fy2026",
        "current_diluted_shares": "hood-diluted-shares-q2-fy2026",
    }
    required_assumptions = {
        "cost_of_equity",
        "terminal_growth",
        "units.currency",
        "units.amount_scale",
        "units.share_scale",
        "periods.0.period_start",
        "periods.0.period_end",
        "periods.0.net_income_common",
        "periods.0.required_capital_retention",
    }
    assumptions = {
        name: {
            "kind": "reported" if name in opening else "assumption",
            "rationale": (
                "Exact historical opening anchor"
                if name in opening
                else "Synthetic scenario assumption; not emitted by the HOOD fact adapter"
            ),
            "evidence_ids": [opening.get(name, Q2_SOURCE_ID)],
        }
        for name in {*opening, *required_assumptions}
    }
    proposal = ValuationProposal(
        model=model,
        evidence_ids=(Q2_SOURCE_ID, FY2025_SOURCE_ID),
        assumptions=assumptions,
        accounting_basis="US GAAP",
        scope_limitations=("Synthetic forecast assumptions for binding test only.",),
    )

    valuation = _calculate(proposal, request, snapshot)

    assert valuation["status"] == "illustrative"
    assert valuation["share_count_basis"] == "latest_quarter_diluted_proxy"
    income_binding = valuation["opening_input_bindings"]["current_net_income"]
    share_binding = valuation["opening_input_bindings"]["current_diluted_shares"]
    assert income_binding["classification"] == "derived"
    assert income_binding["inputs"] == (
        "hood-net-income-common-fy2025",
        "hood-net-income-common-h1-fy2025",
        "hood-net-income-common-h1-fy2026",
    )
    assert share_binding["classification"] == "reported"
    assert share_binding["period_type"] == "duration"
    assert all(
        assumptions[name]["kind"] == "assumption" for name in required_assumptions
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("Six Months Ended June 30,", "Half Year Ended June 30,", "marker|headers"),
        ("2025 2026 2026", "2026 2025 2026", "period columns"),
        ("Total net revenues 1,916", "Net revenues 1,916", "row 'Total net revenues'"),
        ("Diluted 909 912 915", "Diluted shares 909 912 915", "row 'Diluted'"),
    ],
)
def test_changed_table_headers_period_columns_or_row_labels_fail_closed(
    old: str, new: str, message: str
) -> None:
    mutated = _q2_content().replace(old, new, 1)
    with pytest.raises(ModelFactExtractionError, match=message):
        extract_model_facts(_snapshot(q2_content=mutated))


def test_duplicate_or_missing_required_cells_fail_closed() -> None:
    duplicated = _q2_content().replace(
        "Total net revenues 989 1,308 32% 1,067 23%",
        "Total net revenues 989 1,308 32% 1,067 23%\n"
        "Total net revenues 989 1,308 32% 1,067 23%",
        1,
    )
    with pytest.raises(ModelFactExtractionError, match="row 'Total net revenues' is ambiguous"):
        extract_model_facts(_snapshot(q2_content=duplicated))

    unavailable = _q2_content().replace("Diluted $ 722 $ 911", "Diluted $ 722 $ —", 1)
    with pytest.raises(ModelFactExtractionError, match="required field.*is unavailable"):
        extract_model_facts(_snapshot(q2_content=unavailable))


def test_common_income_reconciliation_prevents_consolidated_substitution() -> None:
    mutated = _q2_content().replace(
        "Basic $ 386 $ 561 $ 350\nDiluted $ 386 $ 561 $ 350",
        "Basic $ 386 $ 573 $ 350\nDiluted $ 386 $ 573 $ 350",
        1,
    )
    with pytest.raises(ModelFactExtractionError, match="common-stockholder.*do not reconcile"):
        extract_model_facts(_snapshot(q2_content=mutated))


def test_snapshot_identity_hash_cutoff_and_required_sources_are_rechecked() -> None:
    snapshot = _snapshot()
    with pytest.raises(ModelFactExtractionError, match="ticker must be 'HOOD'"):
        extract_model_facts(snapshot.model_copy(update={"ticker": "NVDA"}))
    with pytest.raises(ModelFactExtractionError, match="cutoff predates"):
        extract_model_facts(
            snapshot.model_copy(update={"cutoff": datetime(2026, 7, 28, tzinfo=timezone.utc)})
        )

    q2, fy2025 = snapshot.sources
    wrong_hash = q2.model_copy(update={"content_sha256": "0" * 64})
    with pytest.raises(ModelFactExtractionError, match="content hash mismatch"):
        extract_model_facts(snapshot.model_copy(update={"sources": (wrong_hash, fy2025)}))

    wrong_id = q2.model_copy(update={"id": "hood-unreviewed-release"})
    with pytest.raises(ModelFactExtractionError, match=Q2_SOURCE_ID):
        extract_model_facts(snapshot.model_copy(update={"sources": (wrong_id, fy2025)}))


def test_enrichment_preserves_frozen_records_and_refuses_overwrite(tmp_path: Path) -> None:
    snapshot = _snapshot()
    prior = FinancialFact(
        id="preexisting-source-fact",
        source_id=Q2_SOURCE_ID,
        metric="reported_context_only",
        value=1,
        unit="USD",
        currency="USD",
        scale=1_000_000,
        period_start="2026-04-01",
        period_end="2026-06-30",
        period_type="duration",
        basis="US GAAP",
        location="existing exact source location",
    )
    data = snapshot.model_dump(mode="json")
    data["facts"] = [prior.model_dump(mode="json")]
    snapshot = EvidenceSnapshot.model_validate(data)
    destination = tmp_path / "fresh" / "hood-enriched.json"

    result = enrich_snapshot(snapshot, destination)

    assert result.snapshot.sources == snapshot.sources
    assert result.snapshot.facts[0] == prior
    assert result.snapshot.gaps[0] == snapshot.gaps[0]
    assert len(result.added_fact_ids) == 32
    assert EvidenceSnapshot.model_validate(read_json(destination)) == result.snapshot
    assert any("not a forecast" in gap for gap in result.snapshot.gaps)
    assert any("regulatory-capital-retention" in gap for gap in result.snapshot.gaps)
    assert any("RVI-related gain" in gap for gap in result.snapshot.gaps)
    with pytest.raises(ModelFactExtractionError, match="already exists"):
        enrich_snapshot(snapshot, destination)


def test_matching_source_fact_is_reused_but_derived_id_collision_fails(tmp_path: Path) -> None:
    snapshot = _snapshot()
    extracted = _facts(snapshot)
    reused = extracted["hood-revenue-fy2025"]
    with_reused = snapshot.model_copy(update={"facts": (reused,)})
    result = enrich_snapshot(with_reused, tmp_path / "reused.json")
    assert result.reused_fact_ids == ("hood-revenue-fy2025",)
    assert len(result.added_fact_ids) == 31
    assert sum(fact.id == reused.id for fact in result.snapshot.facts) == 1

    collision = FinancialFact(
        id="hood-revenue-ttm-q2-fy2026",
        source_id=Q2_SOURCE_ID,
        metric="revenue",
        value=4932,
        unit="USD",
        currency="USD",
        scale=1_000_000,
        period_start="2025-07-01",
        period_end="2026-06-30",
        period_type="duration",
        basis="US GAAP",
        location="preexisting untrusted calculation",
    )
    with_collision = snapshot.model_copy(update={"facts": (collision,)})
    with pytest.raises(ModelFactExtractionError, match="derived model fact identifier collides"):
        enrich_snapshot(with_collision, tmp_path / "collision.json")


def test_file_wrapper_keeps_frozen_packet_unchanged_and_requires_external_output(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "frozen"
    snapshot = _snapshot()
    original = _write_packet(packet, snapshot)
    frozen_path = packet / "evidence.json"

    with pytest.raises(ModelFactExtractionError, match="outside the frozen input"):
        enrich_frozen_snapshot(packet, packet / "enriched.json")

    destination = tmp_path / "output" / "hood-enriched.json"
    result = enrich_frozen_snapshot(packet, destination)
    assert result.destination == destination.resolve()
    assert frozen_path.read_bytes() == original
    assert EvidenceSnapshot.model_validate(read_json(destination)) == result.snapshot


def test_file_wrapper_rejects_raw_archive_hash_disagreement(tmp_path: Path) -> None:
    packet = tmp_path / "frozen"
    _write_packet(packet, _snapshot())
    raw_path = packet / "raw" / f"{Q2_SOURCE_ID}.pdf"
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")

    with pytest.raises(ModelFactExtractionError, match="raw archive hash mismatch"):
        enrich_frozen_snapshot(packet, tmp_path / "output.json")
    assert not (tmp_path / "output.json").exists()
