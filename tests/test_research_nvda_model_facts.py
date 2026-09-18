from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research_nvda_model_facts import (
    FY26_SOURCE_ID,
    Q2_SOURCE_ID,
    ModelFactExtractionError,
    enrich_snapshot,
    extract_model_facts,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument
from tradingagents.research.sources import FetchedSource, FileSourceCache, extract_text
from tradingagents.research.storage import canonical_json, read_json


def _table(rows: list[list[str]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table>{body}</table>"


def _html(tables: list[list[list[str]]]) -> bytes:
    return ("<html><body>" + "".join(_table(table) for table in tables) + "</body></html>").encode()


def _filler(label: str) -> list[list[str]]:
    return [[label]]


def _fy26_html() -> bytes:
    income = [
        ["NVIDIA CORPORATION"],
        ["CONDENSED CONSOLIDATED STATEMENTS OF INCOME"],
        ["(In millions, except per share data)"],
        ["(Unaudited)"],
        ["Three Months Ended", "Twelve Months Ended"],
        ["January 25,", "January 26,", "January 25,", "January 26,"],
        ["2026", "2025", "2026", "2025"],
        ["Revenue", "$", "68,127", "$", "39,331", "$", "215,938", "$", "130,497"],
    ]
    return _html(
        [
            [["Q4 FY26"]],
            _filler("summary"),
            income,
            _filler("balance"),
            _filler("cash flow"),
            _filler("reconciliation"),
            _filler("outlook"),
        ]
    )


def _q2_html() -> bytes:
    income = [
        ["NVIDIA CORPORATION"],
        ["CONDENSED CONSOLIDATED STATEMENTS OF INCOME"],
        ["(In millions, except per share data)"],
        ["(Unaudited)"],
        ["Three Months Ended", "Six Months Ended"],
        ["July 26,", "July 27,", "July 26,", "July 27,"],
        ["2026", "2025", "2026", "2025"],
        ["Revenue", "$", "96,221", "$", "46,743", "$", "177,837", "$", "90,805"],
        ["Weighted average shares used in per share computation:"],
        ["Basic", "24,190", "24,366", "24,238", "24,404"],
        ["Diluted", "24,285", "24,532", "24,338", "24,571"],
    ]
    balance = [
        ["NVIDIA CORPORATION"],
        ["CONDENSED CONSOLIDATED BALANCE SHEETS"],
        ["(In millions)"],
        ["(Unaudited)"],
        ["July 26,", "January 25,"],
        ["2026", "2026"],
        ["ASSETS"],
        ["Cash and cash equivalents", "$", "22,443", "$", "10,605"],
        ["Marketable debt securities", "34,143", "39,065"],
        ["Marketable equity securities", "42,783", "12,886"],
        ["Accounts receivable, net", "63,059", "38,466"],
        ["Inventories", "31,575", "21,403"],
        ["Prepaid expenses and other current assets", "3,409", "3,180"],
        ["Accounts payable", "$", "15,059", "$", "9,812"],
        ["Accrued and other current liabilities", "26,960", "21,352"],
        ["Short-term debt", "1,000", "999"],
        ["Long-term debt", "32,366", "7,469"],
    ]
    return _html(
        [
            [["Q2 FY27"]],
            _filler("summary"),
            income,
            balance,
            _filler("cash flow"),
            _filler("reconciliation"),
            _filler("outlook"),
        ]
    )


def _facts_by_id(fy26_raw: bytes | None = None, q2_raw: bytes | None = None):
    facts = extract_model_facts(fy26_raw or _fy26_html(), q2_raw or _q2_html())
    return {fact.id: fact for fact in facts}


def test_ttm_revenue_identity_retains_all_period_operands_and_sources() -> None:
    facts = _facts_by_id()
    ttm = facts["nvda-revenue-ttm-q2-fy27"]

    assert facts["nvda-revenue-fy26"].value == 215938
    assert facts["nvda-revenue-h1-fy26"].value == 90805
    assert facts["nvda-revenue-h1-fy27"].value == 177837
    assert ttm.value == 302970
    assert ttm.inputs == (
        "nvda-revenue-fy26",
        "nvda-revenue-h1-fy26",
        "nvda-revenue-h1-fy27",
    )
    assert ttm.formula == (
        "nvda-revenue-fy26 - nvda-revenue-h1-fy26 + nvda-revenue-h1-fy27"
    )
    assert ttm.period_start.isoformat() == "2025-07-28"
    assert ttm.period_end.isoformat() == "2026-07-26"
    assert ttm.source_id == Q2_SOURCE_ID
    assert ttm.basis == "US GAAP"


def test_working_capital_and_net_debt_use_explicit_conservative_scopes() -> None:
    facts = _facts_by_id()
    latest_wc = facts["nvda-operating-working-capital-q2-fy27-end"]
    fy26_wc = facts["nvda-operating-working-capital-fy26-end"]
    latest_net_debt = facts["nvda-net-debt-q2-fy27-end"]
    fy26_net_debt = facts["nvda-net-debt-fy26-end"]

    assert latest_wc.value == 63059 + 31575 + 3409 - 15059 - 26960 == 56024
    assert fy26_wc.value == 38466 + 21403 + 3180 - 9812 - 21352 == 31885
    assert latest_net_debt.value == 1000 + 32366 - 22443 == 10923
    assert fy26_net_debt.value == 999 + 7469 - 10605 == -2137
    assert latest_wc.metric == "working_capital"
    assert latest_wc.basis == latest_net_debt.basis == "US GAAP"
    assert "aggregate-row working-capital proxy" in latest_wc.location
    assert "may contain tax, lease" in latest_wc.location
    assert "marketable debt securities" in latest_net_debt.location
    assert "marketable equity securities" in latest_net_debt.location
    assert "restricted-cash" in latest_net_debt.location
    assert not {
        "nvda-model-marketable_debt_securities-q2-fy27-end",
        "nvda-model-marketable_equity_securities-q2-fy27-end",
    } & set(latest_net_debt.inputs)
    assert latest_wc.inputs == (
        "nvda-model-accounts_receivable-q2-fy27-end",
        "nvda-model-inventory-q2-fy27-end",
        "nvda-model-prepaid_and_other_current_assets-q2-fy27-end",
        "nvda-model-accounts_payable-q2-fy27-end",
        "nvda-model-accrued_and_other_current_liabilities-q2-fy27-end",
    )


def test_latest_diluted_shares_remain_a_duration_fact() -> None:
    shares = _facts_by_id()["nvda-diluted-shares-q2-fy27"]

    assert shares.value == 24285
    assert shares.scale == 1000000
    assert shares.metric == "weighted_average_diluted_shares"
    assert shares.unit == "shares"
    assert shares.currency is None
    assert shares.period_type == "duration"
    assert shares.period_start.isoformat() == "2026-04-27"
    assert shares.period_end.isoformat() == "2026-07-26"
    assert shares.basis == "US GAAP"
    assert "weighted-average" in shares.location
    assert "never a point-in-time share count" in shares.location


def test_every_extracted_fact_uses_exact_accounting_basis() -> None:
    assert {fact.basis for fact in extract_model_facts(_fy26_html(), _q2_html())} == {
        "US GAAP"
    }


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("Six Months Ended", "Half Year Ended", "headers changed"),
        ("Accounts receivable, net", "Trade receivables", "required row"),
        ("July 26,</td><td>July 27,", "July 27,</td><td>July 26,", "headers changed"),
    ],
)
def test_changed_header_row_or_column_fails_closed(old: str, new: str, message: str) -> None:
    q2 = _q2_html().replace(old.encode(), new.encode(), 1)
    with pytest.raises(ModelFactExtractionError, match=message):
        extract_model_facts(_fy26_html(), q2)


def test_missing_h1_period_is_not_coerced_to_zero() -> None:
    q2 = _q2_html().replace(b"90,805", b"-", 1)
    with pytest.raises(
        ModelFactExtractionError,
        match="Revenue.*column 9 is unavailable",
    ):
        extract_model_facts(_fy26_html(), q2)


def _source(identifier: str, url: str, raw: bytes) -> tuple[SourceDocument, FetchedSource]:
    text = extract_text(raw, media_type="text/html", charset="utf-8")
    assert text is not None
    raw_hash = hashlib.sha256(raw).hexdigest()
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    retrieved = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    source = SourceDocument(
        id=identifier,
        url=url,
        title=identifier,
        publisher="NVIDIA",
        retrieved_at=retrieved,
        published_at=datetime(2026, 8, 26, 23, 0, tzinfo=timezone.utc),
        content=text,
        content_sha256=text_hash,
        kind="ir",
    )
    fetched = FetchedSource(
        requested_url=url,
        final_url=url,
        retrieved_at=retrieved,
        status=200,
        media_type="text/html",
        charset="utf-8",
        raw=raw,
        raw_sha256=raw_hash,
        text=text,
        text_sha256=text_hash,
    )
    return source, fetched


def _packet(tmp_path: Path) -> Path:
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    source_pairs = (
        _source(FY26_SOURCE_ID, "https://example.test/fy26", _fy26_html()),
        _source(Q2_SOURCE_ID, "https://example.test/q2", _q2_html()),
    )
    snapshot = EvidenceSnapshot(
        ticker="NVDA",
        cutoff="2026-09-17T15:21:06+00:00",
        sources=tuple(source for source, _fetched in source_pairs),
    )
    (source_dir / "evidence.json").write_bytes(canonical_json(snapshot))
    cache = FileSourceCache(source_dir / "source-cache")
    acquisitions = []
    for source, fetched in source_pairs:
        cache.put(fetched)
        acquisitions.append(
            {
                "id": source.id,
                "requested_url": source.url,
                "raw_sha256": fetched.raw_sha256,
                "text_sha256": fetched.text_sha256,
            }
        )
    (source_dir / "acquisition.json").write_bytes(canonical_json(acquisitions))
    return source_dir


def _write_base_with_fact(source_dir: Path, path: Path, identifier: str) -> bytes:
    snapshot = EvidenceSnapshot.model_validate(read_json(source_dir / "evidence.json"))
    prior = FinancialFact(
        id=identifier,
        source_id=Q2_SOURCE_ID,
        metric="operating_income",
        value="1",
        scale="1000000",
        unit="USD",
        currency="USD",
        period_end="2026-07-26",
        period_type="instant",
        basis="US GAAP",
        location="pre-existing validated fact",
    )
    data = snapshot.model_dump(mode="json")
    data["facts"] = [prior.model_dump(mode="json")]
    content = canonical_json(EvidenceSnapshot.model_validate(data))
    path.write_bytes(content)
    return content


def test_enrichment_is_hash_bound_and_writes_only_a_fresh_external_destination(
    tmp_path: Path,
) -> None:
    source_dir = _packet(tmp_path)
    original_evidence = (source_dir / "evidence.json").read_bytes()
    destination = tmp_path / "fresh" / "enriched.json"

    result = enrich_snapshot(source_dir, destination)

    assert result.destination == destination.resolve()
    assert result.base_snapshot == (source_dir / "evidence.json").resolve()
    assert len(result.added_fact_ids) == 29
    assert len(result.snapshot.facts) == 29
    assert any("aggregate-row proxy" in gap for gap in result.snapshot.gaps)
    assert any("restricted-cash" in gap for gap in result.snapshot.gaps)
    assert EvidenceSnapshot.model_validate(read_json(destination)).facts == result.snapshot.facts
    assert (source_dir / "evidence.json").read_bytes() == original_evidence
    with pytest.raises(ModelFactExtractionError, match="already exists"):
        enrich_snapshot(source_dir, destination)
    with pytest.raises(ModelFactExtractionError, match="outside the frozen source"):
        enrich_snapshot(source_dir, source_dir / "enriched.json")


def test_enrichment_rejects_acquisition_hash_disagreement(tmp_path: Path) -> None:
    source_dir = _packet(tmp_path)
    acquisitions = read_json(source_dir / "acquisition.json")
    acquisitions[0]["raw_sha256"] = "0" * 64
    (source_dir / "acquisition.json").write_bytes(canonical_json(acquisitions))

    with pytest.raises(ModelFactExtractionError, match="raw_sha256 does not match"):
        enrich_snapshot(source_dir, tmp_path / "out.json")


def test_enrichment_prefers_existing_fact_snapshot_and_preserves_every_prior_fact(
    tmp_path: Path,
) -> None:
    source_dir = _packet(tmp_path)
    preferred = source_dir / "evidence_with_facts.json"
    original = _write_base_with_fact(source_dir, preferred, "prior-fact")

    result = enrich_snapshot(source_dir, tmp_path / "out.json")

    assert result.base_snapshot == preferred.resolve()
    assert len(result.snapshot.facts) == 30
    assert result.snapshot.facts[0].id == "prior-fact"
    assert preferred.read_bytes() == original


def test_explicit_clean_base_is_supported_and_id_collisions_fail_closed(
    tmp_path: Path,
) -> None:
    source_dir = _packet(tmp_path)
    explicit = tmp_path / "explicit-base.json"
    _write_base_with_fact(source_dir, explicit, "explicit-prior-fact")

    result = enrich_snapshot(
        source_dir,
        tmp_path / "explicit-out.json",
        base_snapshot=explicit,
    )
    assert result.base_snapshot == explicit.resolve()
    assert result.snapshot.facts[0].id == "explicit-prior-fact"

    collision = tmp_path / "collision-base.json"
    _write_base_with_fact(source_dir, collision, "nvda-revenue-ttm-q2-fy27")
    with pytest.raises(
        ModelFactExtractionError,
        match="derived model fact identifier collides.*nvda-revenue-ttm-q2-fy27",
    ):
        enrich_snapshot(
            source_dir,
            tmp_path / "collision-out.json",
            base_snapshot=collision,
        )


def test_matching_preexisting_source_operand_is_reused_without_duplication(
    tmp_path: Path,
) -> None:
    source_dir = _packet(tmp_path)
    base_path = tmp_path / "base-with-operand.json"
    snapshot = EvidenceSnapshot.model_validate(read_json(source_dir / "evidence.json"))
    operand = _facts_by_id()["nvda-model-accounts_receivable-fy26-end"].model_copy(
        update={"location": "equivalent earlier adapter location wording"}
    )
    data = snapshot.model_dump(mode="json")
    data["facts"] = [operand.model_dump(mode="json")]
    base_path.write_bytes(canonical_json(EvidenceSnapshot.model_validate(data)))

    result = enrich_snapshot(
        source_dir,
        tmp_path / "reused-out.json",
        base_snapshot=base_path,
    )

    assert result.reused_fact_ids == ("nvda-model-accounts_receivable-fy26-end",)
    assert len(result.added_fact_ids) == 28
    assert len(result.snapshot.facts) == 29
    assert sum(
        fact.id == "nvda-model-accounts_receivable-fy26-end"
        for fact in result.snapshot.facts
    ) == 1
