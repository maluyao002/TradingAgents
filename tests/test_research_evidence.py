"""Offline evidence normalization tests; no provider or network calls are used."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

import pytest

from tests.test_research_contracts import fact_data, request_data, source_data
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.evidence import (
    derive_quarter,
    load_snapshot,
    normalize_company_facts,
    normalize_news,
)

CUTOFF = "2026-09-17T12:00:00+00:00"


def request() -> ResearchRequest:
    return ResearchRequest.model_validate(request_data(cutoff=CUTOFF, timezone="UTC"))


def source(**overrides) -> SourceDocument:
    content = overrides.pop("content", "reported revenue")
    return SourceDocument.model_validate(
        source_data(
            content=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            **overrides,
        )
    )


def fact(**overrides) -> FinancialFact:
    if "period_start" in overrides and "period_type" not in overrides:
        overrides["period_type"] = "duration"
    return FinancialFact.model_validate(fact_data(**overrides))


def write_snapshot(path, snapshot: EvidenceSnapshot) -> None:
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")


def test_load_snapshot_validates_hash_and_exact_request_identity(tmp_path):
    valid_source = source(published_at="2026-09-16T12:00:00+00:00")
    snapshot = EvidenceSnapshot(ticker="AMD", cutoff=CUTOFF, sources=[valid_source], facts=[fact()])
    path = tmp_path / "snapshot.json"
    write_snapshot(path, snapshot)
    assert load_snapshot(path, request()).facts == snapshot.facts

    tampered = snapshot.model_dump(mode="json")
    tampered["sources"][0]["content"] = "altered"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_snapshot(path, request())

    path.write_text('{"ticker":"AMD","ticker":"NVDA"}', encoding="utf-8")
    with pytest.raises(ValueError, match="readable bounded JSON"):
        load_snapshot(path, request())

    write_snapshot(path, snapshot)
    with pytest.raises(ValueError, match="ticker"):
        load_snapshot(path, request_data_as_request(ticker="NVDA"))
    with pytest.raises(ValueError, match="cutoff"):
        load_snapshot(path, request_data_as_request(cutoff="2026-09-17T13:00:00+00:00"))


def request_data_as_request(**overrides) -> ResearchRequest:
    data = request_data(cutoff=CUTOFF, timezone="UTC")
    data.update(overrides)
    return ResearchRequest.model_validate(data)


def test_load_snapshot_blocks_cutoff_leakage_and_marks_unknown_publication_sources(tmp_path):
    known = source(id="known", published_at="2026-09-16T12:00:00+00:00")
    unknown = source(id="unknown", published_at=None)
    snapshot = EvidenceSnapshot(
        ticker="AMD",
        cutoff=CUTOFF,
        sources=[known, unknown],
        facts=[fact(id="known-fact", source_id="known")],
    )
    path = tmp_path / "snapshot.json"
    write_snapshot(path, snapshot)

    loaded = load_snapshot(path, request())
    assert [item.id for item in loaded.sources] == ["known", "unknown"]
    assert [item.id for item in loaded.facts] == ["known-fact"]
    assert not loaded.events and not loaded.expectations
    assert any("unknown publication" in gap for gap in loaded.gaps)

    leaking = snapshot.model_dump(mode="json")
    leaking["facts"][0]["period_end"] = "2026-09-18"
    path.write_text(json.dumps(leaking), encoding="utf-8")
    with pytest.raises(ValueError, match="reported financial period"):
        load_snapshot(path, request())

    future = snapshot.model_dump(mode="json")
    future["events"] = [
        {
            "id": "event-future",
            "title": "Future event",
            "source_id": "known",
            "entities": ["AMD"],
            "published_at": "2026-09-18T12:00:00+00:00",
            "origin_id": "wire-1",
            "classification": "reporting",
        }
    ]
    path.write_text(json.dumps(future), encoding="utf-8")
    with pytest.raises(ValueError, match="event publication"):
        load_snapshot(path, request())

    future_expectation = snapshot.model_dump(mode="json")
    future_expectation["expectations"] = [
        {
            "id": "expectation-future",
            "metric": "revenue",
            "kind": "guidance",
            "period_end": "2026-12-31",
            "basis": "GAAP",
            "unit": "USD",
            "value": "100",
            "source_ids": ["known"],
            "as_of": "2026-09-18T12:00:00+00:00",
        }
    ]
    path.write_text(json.dumps(future_expectation), encoding="utf-8")
    with pytest.raises(ValueError, match="expectation is after"):
        load_snapshot(path, request())


def test_load_snapshot_rejects_adr_ratio_not_effective_at_cutoff(tmp_path):
    instrument = {
        "issuer": "AMD",
        "cik": "0000002488",
        "exchange": "NASDAQ",
        "share_class": "ordinary",
        "quote_currency": "USD",
        "reporting_currency": "USD",
        "ordinary_shares_per_adr": "1",
        "adr_ratio_effective_at": "2026-09-18",
        "identity_source": "issuer filing",
    }
    snapshot = EvidenceSnapshot(ticker="AMD", cutoff=CUTOFF, instrument=instrument)
    path = tmp_path / "snapshot.json"
    write_snapshot(path, snapshot)
    with pytest.raises(ValueError, match="not effective"):
        load_snapshot(path, request_data_as_request(instrument=instrument))


def test_normalize_companyfacts_keeps_units_accessions_and_restated_periods_without_q4_inference():
    sec_source = source(id="filing-1", accession="0001", published_at="2026-09-17T10:00:00+00:00")
    old_source = source(id="filing-2", published_at="2026-09-16T10:00:00+00:00")
    payload = {
        "ticker": "AMD",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001",
                                "filed": "2026-09-17",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 200,
                                "scale": 1000000,
                            },
                            {
                                "accn": "0002",
                                "filed": "2026-09-16",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 190,
                            },
                        ],
                        "EUR": [
                            {
                                "accn": "0001",
                                "filed": "2026-09-17",
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 180,
                            }
                        ],
                    }
                }
            }
        },
    }
    facts = normalize_company_facts(
        payload,
        "AMD",
        datetime.fromisoformat(CUTOFF),
        sources={"filing-1": sec_source, "filing-2": old_source},
        accession_source_ids={"0001": "filing-1", "0002": "filing-2"},
    )
    assert {(item.value, item.unit, item.source_id, item.period_start) for item in facts} == {
        (Decimal("200"), "USD", "filing-1", datetime(2026, 1, 1).date()),
        (Decimal("190"), "USD", "filing-2", datetime(2026, 1, 1).date()),
        (Decimal("180"), "EUR", "filing-1", datetime(2026, 1, 1).date()),
    }
    assert all(item.basis == "US GAAP" and item.period_type == "duration" for item in facts)
    assert next(item for item in facts if item.value == 200).normalized_value == Decimal(
        "200000000"
    )
    assert all("us-gaap:Revenues" in item.location for item in facts)
    with pytest.raises(ValueError, match="explicit sources"):
        normalize_company_facts(
            payload, "AMD", datetime.fromisoformat(CUTOFF), sources={}, accession_source_ids={}
        )

    conflicting = json.loads(json.dumps(payload))
    conflicting["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"accn": "0001", "start": "2026-01-01", "end": "2026-06-30", "val": 201, "scale": 1000000}
    )
    with pytest.raises(ValueError, match="conflicting companyfacts"):
        normalize_company_facts(
            conflicting,
            "AMD",
            datetime.fromisoformat(CUTOFF),
            sources={"filing-1": sec_source, "filing-2": old_source},
            accession_source_ids={"0001": "filing-1", "0002": "filing-2"},
        )

    ifrs = {
        "ticker": "AMD",
        "facts": {
            "ifrs-full": {
                "ProfitLoss": {"units": {"USD": [{"accn": "0001", "end": "2026-06-30", "val": 5}]}}
            }
        },
    }
    assert (
        normalize_company_facts(
            ifrs,
            "AMD",
            datetime.fromisoformat(CUTOFF),
            sources={"filing-1": sec_source},
            accession_source_ids={"0001": "filing-1"},
        )[0].metric
        == "net_income"
    )


def test_derive_quarter_requires_comparable_contiguous_ytd_operands():
    current = fact(id="ytd-2", value="150", period_start="2026-01-01", period_end="2026-06-30")
    previous = fact(id="ytd-1", value="60", period_start="2026-01-01", period_end="2026-03-31")
    quarter = derive_quarter(current, previous)
    assert quarter.value == Decimal("90")
    assert quarter.period_start.isoformat() == "2026-04-01"
    assert quarter.inputs == ("ytd-2", "ytd-1")
    assert quarter.formula == "ytd-2 - ytd-1"
    assert len(quarter.id) < 128
    with pytest.raises(ValueError, match="share metric"):
        derive_quarter(
            current,
            fact(id="bad", metric="cash", period_start="2026-01-01", period_end="2026-03-31"),
        )
    with pytest.raises(ValueError, match="share metric"):
        derive_quarter(
            current,
            fact(
                id="segment-swap",
                segment="datacenter",
                period_start="2026-01-01",
                period_end="2026-03-31",
            ),
        )
    with pytest.raises(ValueError, match="70-105"):
        derive_quarter(
            fact(id="fy", value="200", period_start="2026-01-01", period_end="2026-12-31"),
            previous,
        )
    with pytest.raises(ValueError, match="share metric"):
        derive_quarter(
            current,
            fact(
                id="currency-swap",
                currency="EUR",
                period_start="2026-01-01",
                period_end="2026-03-31",
            ),
        )


def test_normalize_news_filters_before_deduplication_limit_and_marks_empty_incomplete():
    records = [
        {
            "url": "https://news.test/shared",
            "origin_id": "shared",
            "title": "Future",
            "publisher": "Wire",
            "entities": ["AMD"],
            "published_at": "2026-09-18T00:00:00+00:00",
        },
        {
            "url": "https://news.test/shared",
            "origin_id": "shared",
            "title": "Eligible",
            "publisher": "Wire",
            "entities": ["AMD"],
            "published_at": "2026-09-16T00:00:00+00:00",
            "content": "full",
        },
        {
            "url": "https://news.test/other",
            "origin_id": "other",
            "title": "Other",
            "publisher": "Wire",
            "entities": ["AMD"],
            "published_at": "2026-09-15T00:00:00+00:00",
        },
        {
            "url": "https://news.test/irrelevant",
            "origin_id": "irrelevant",
            "title": "AMD mention",
            "publisher": "Wire",
            "entities": ["NVDA"],
            "published_at": "2026-09-15T00:00:00+00:00",
        },
    ]
    normalized = normalize_news(records, "AMD", datetime.fromisoformat(CUTOFF), limit=1)
    assert len(normalized.events) == len(normalized.sources) == 1
    assert normalized.events[0].title == "Eligible"
    assert normalized.sources[0].availability == "full_text"

    empty = normalize_news([], "AMD", datetime.fromisoformat(CUTOFF))
    assert not empty.events
    assert any("not complete" in gap for gap in empty.gaps)
