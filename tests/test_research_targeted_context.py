import hashlib
import json
from datetime import datetime

import pytest

from tradingagents.research.context import pack_evidence
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, SourceDocument

CUTOFF = datetime.fromisoformat("2026-09-17T12:00:00+00:00")


def source(identifier: str, content: str, **changes) -> SourceDocument:
    data = {
        "id": identifier,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "url": f"https://example.test/{identifier}",
        "title": f"Document {identifier}",
        "publisher": "Publisher",
        "published_at": CUTOFF,
        "retrieved_at": CUTOFF,
    }
    data.update(changes)
    return SourceDocument(**data)


def retained_text(packed: dict) -> str:
    return "\n".join(
        excerpt["text"]
        for packed_source in packed["sources"]
        for excerpt in packed_source["excerpts"]
    )


def test_late_material_commitment_after_100k_noise_is_selected():
    important = (
        "Purchase commitments for advanced packaging are $4.8 billion through 2028, "
        "including non-cancellable supplier capacity reservations."
    )
    content = "routine disclosure " * 7500 + "\n\n" + important + "\n\nordinary ending"
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff=CUTOFF, sources=[source("filing", content)])

    packed = pack_evidence(
        snapshot,
        queries=("non-cancellable advanced packaging purchase commitments",),
        max_source_chars=700,
        max_chars_per_source=700,
    )

    assert important in retained_text(packed)
    assert packed["retrieval_metadata"]["queries"][0]["match_label"] == "keyword_match"
    assert packed["retrieval_metadata"]["queries"][0]["unresolved"] is False


def test_targeted_offsets_are_exact_hash_preserved_and_output_deterministic():
    content = "开头😀\n\n" + "甲" * 1000 + " supplier concentration covenant " + "乙" * 1000
    original = source("risk", content)
    snapshot = EvidenceSnapshot(ticker="HOOD", cutoff=CUTOFF, sources=[original])
    kwargs = {
        "queries": ("supplier concentration covenant",),
        "max_source_chars": 420,
        "max_chars_per_source": 420,
    }

    first = pack_evidence(snapshot, **kwargs)
    second = pack_evidence(snapshot, **kwargs)
    entry = first["sources"][0]

    assert entry["content_sha256"] == original.content_sha256
    assert first == second
    assert all(
        excerpt["text"] == content[excerpt["start"] : excerpt["end"]]
        for excerpt in entry["excerpts"]
    )
    assert all(
        left["end"] < right["start"]
        for left, right in zip(entry["excerpts"], entry["excerpts"][1:], strict=False)
    )


def test_empty_queries_preserve_the_legacy_payload_snapshot_byte_for_byte():
    snapshot = EvidenceSnapshot(
        ticker="NVDA", cutoff=CUTOFF, sources=[source("filing", "Revenue was 10. Cash was 4.")]
    )
    default = pack_evidence(snapshot, max_source_chars=19, max_chars_per_source=19)
    explicit_empty = pack_evidence(
        snapshot, queries=(), max_source_chars=19, max_chars_per_source=19
    )
    encoded = json.dumps(default, separators=(",", ":")).encode()

    assert explicit_empty == default
    assert "context_version" not in default and "retrieval_metadata" not in default
    assert hashlib.sha256(encoded).hexdigest() == (
        "9628c8a30cd6ee9c3d10418947a11f7f07e0800ec14213bcffe0bffbfaf1d8fd"
    )


def test_generic_fake_query_is_unresolved_and_uses_explicit_fallback():
    content = "Opening source context.\n\nA later paragraph has no relevant language."
    packed = pack_evidence(
        EvidenceSnapshot(ticker="HOOD", cutoff=CUTOFF, sources=[source("filing", content)]),
        queries=("What is the company outlook and future financial performance?",),
        max_source_chars=25,
        max_chars_per_source=25,
    )
    query = packed["retrieval_metadata"]["queries"][0]

    assert query == {
        "query_index": 0,
        "terms": [],
        "match_label": "not_found",
        "unresolved": True,
        "hit_count": 0,
        "hits": [],
    }
    assert packed["sources"][0]["excerpts"] == [
        {"start": 0, "end": 25, "text": content[:25]}
    ]
    assert any("not found" in gap and "does not establish absence" in gap for gap in packed["context_gaps"])


def test_budget_cutoff_and_structured_facts_are_preserved():
    eligible = source("eligible", "A disclosed collateral covenant and customer concentration.")
    future = source("future", "future-only collateral covenant").model_copy(
        update={"published_at": datetime.fromisoformat("2026-09-18T00:00:00+00:00")}
    )
    good_fact = FinancialFact(
        id="good-fact",
        source_id="eligible",
        metric="cash",
        value="10",
        unit="USD",
        basis="GAAP",
        period_end="2026-06-30",
        location="p1",
    )
    future_fact = good_fact.model_copy(update={"id": "future-fact", "source_id": "future"})
    valid = EvidenceSnapshot(
        ticker="HOOD", cutoff=CUTOFF, sources=[eligible], facts=[good_fact]
    )
    defensive = valid.model_copy(
        update={"sources": (eligible, future), "facts": (good_fact, future_fact)}
    )

    packed = pack_evidence(
        defensive,
        queries=("collateral covenant",),
        max_source_chars=31,
        max_chars_per_source=17,
    )

    assert [item["id"] for item in packed["sources"]] == ["eligible"]
    assert [item["id"] for item in packed["facts"]] == ["good-fact"]
    sizes = [sum(len(excerpt["text"]) for excerpt in item["excerpts"]) for item in packed["sources"]]
    assert sum(sizes) <= 31 and all(size <= 17 for size in sizes)
    assert all(hit["source_id"] != "future" for hit in packed["retrieval_metadata"]["queries"][0]["hits"])


def test_table_match_keeps_header_row_and_associated_numbers_contiguous():
    table = (
        "| Obligation | 2027 | Thereafter |\n"
        "|---|---:|---:|\n"
        "| Advanced packaging commitments | $4.8 billion | $7.6 billion |\n"
        "| Operating leases | $0.3 billion | $1.1 billion |\n"
    )
    content = "Liquidity note\n\n" + table + "\nNarrative after table."
    packed = pack_evidence(
        EvidenceSnapshot(ticker="NVDA", cutoff=CUTOFF, sources=[source("note", content)]),
        queries=("advanced packaging commitments thereafter",),
        max_source_chars=500,
        max_chars_per_source=500,
    )
    text = retained_text(packed)

    assert table in text
    assert "Advanced packaging commitments | $4.8 billion | $7.6 billion" in text


def test_multiple_queries_share_budget_without_first_query_monopoly():
    first = "Supplier concentration is 61 percent for outsourced fabrication."
    second = "Regulatory collateral requirements can constrain customer withdrawals."
    content = "preface\n\n" + first + "\n\n" + "x" * 3000 + "\n\n" + second
    packed = pack_evidence(
        EvidenceSnapshot(ticker="HOOD", cutoff=CUTOFF, sources=[source("risk", content)]),
        queries=("supplier concentration fabrication", "collateral customer withdrawals"),
        max_source_chars=260,
        max_chars_per_source=260,
    )
    text = retained_text(packed)

    assert first in text and second in text
    assert [item["match_label"] for item in packed["retrieval_metadata"]["queries"]] == [
        "keyword_match",
        "keyword_match",
    ]


def test_retrieved_source_cap_is_fair_across_queries_and_keeps_source_provenance():
    sources = [source(f"alpha-{i}", f"Alpha exposure disclosure number {i}.") for i in range(20)]
    sources += [source(f"beta-{i}", f"Beta dependency disclosure number {i}.") for i in range(20)]
    packed = pack_evidence(
        EvidenceSnapshot(ticker="NVDA", cutoff=CUTOFF, sources=sources),
        queries=("alpha exposure", "beta dependency"),
        max_source_chars=4000,
        max_chars_per_source=200,
    )

    assert len(packed["sources"]) == 40
    assert sum(bool(item["excerpts"]) for item in packed["sources"]) == 32
    selected_counts = [
        sum(hit["selected"] for hit in query["hits"])
        for query in packed["retrieval_metadata"]["queries"]
    ]
    assert selected_counts == [16, 16]


@pytest.mark.parametrize(
    "queries, message",
    [
        (("x",) * 17, "at most 16"),
        (("x" * 513,), "at most 512"),
        (("x" * 500,) * 9, "at most 4096"),
        (["not", "a", "tuple"], "tuple of strings"),
    ],
)
def test_query_bounds_reject_unbounded_or_wrongly_typed_inputs(queries, message):
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff=CUTOFF)
    with pytest.raises(ValueError, match=message):
        pack_evidence(snapshot, queries=queries)


@pytest.mark.parametrize("field", ["max_source_chars", "max_chars_per_source"])
def test_query_mode_keeps_character_budget_validation(field):
    snapshot = EvidenceSnapshot(ticker="NVDA", cutoff=CUTOFF)
    with pytest.raises(ValueError, match="nonnegative integer"):
        pack_evidence(snapshot, queries=("supplier dependency",), **{field: -1})
