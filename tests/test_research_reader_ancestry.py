from __future__ import annotations

from hashlib import sha256

import pytest

from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
)
from tradingagents.research.reader import render_reader
from tradingagents.research.stages import ReportDraft


def _source(identifier: str, *, published: bool = True) -> SourceDocument:
    content = f"Synthetic source {identifier}"
    return SourceDocument(
        id=identifier,
        url=f"https://example.test/{identifier}",
        title=identifier,
        publisher="Fixture publisher",
        retrieved_at="2026-09-16T01:00:00Z",
        published_at="2026-09-16T00:00:00Z" if published else None,
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
    )


def _fact(
    identifier: str,
    source_id: str,
    *,
    inputs: tuple[str, ...] = (),
) -> FinancialFact:
    return FinancialFact(
        id=identifier,
        source_id=source_id,
        metric=identifier,
        value=1,
        unit="USD",
        currency="USD",
        period_end="2026-06-30",
        basis="US GAAP",
        location=f"Synthetic location for {identifier}",
        inputs=inputs,
        formula=" + ".join(inputs) if inputs else None,
    )


def _request(tmp_path) -> ResearchRequest:
    return ResearchRequest(
        ticker="TEST",
        cutoff="2026-09-17T00:00:00Z",
        backend="api",
        output_dir=tmp_path / "output",
        report_language="English",
    )


def _draft(text: str, evidence_ids: tuple[str, ...]) -> ReportDraft:
    return ReportDraft(
        sections=(
            {
                "title": "Evidence section",
                "text": text,
                "evidence_ids": evidence_ids,
            },
        ),
        limitations=(),
        investment_view="unrated",
    )


def test_multihop_fact_ancestry_includes_carriers_in_snapshot_source_order(tmp_path):
    sources = (_source("middle-source"), _source("base-source"), _source("top-source"))
    facts = (
        _fact("base-fact", "base-source"),
        _fact("middle-fact", "middle-source", inputs=("base-fact",)),
        _fact("top-fact", "top-source", inputs=("middle-fact",)),
    )
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff="2026-09-17T00:00:00Z", sources=sources, facts=facts
    )

    rendered = render_reader(_request(tmp_path), _draft("Derived result.", ("top-fact",)), snapshot, ())

    assert rendered.limitations_audit["section_evidence"] == [
        {
            "section_index": 1,
            "section_title": "Evidence section",
            "evidence_id": "top-fact",
            "source_ids": ["middle-source", "base-source", "top-source"],
            "source_footnote_numbers": [1, 2, 3],
        }
    ]
    assert "Derived result. [^1][^2][^3]" in rendered.reader_text
    assert [
        item["referenced_by_section_indexes"]
        for item in rendered.limitations_audit["source_footnotes"]
    ] == [[1], [1], [1]]


def test_mixed_evidence_deduplicates_sources_and_appends_all_missing_footnotes(tmp_path):
    sources = (_source("annual"), _source("quarterly"), _source("industry"))
    facts = (
        _fact("annual-a", "annual"),
        _fact("annual-b", "annual"),
        _fact("quarter", "quarterly"),
        _fact("derived", "quarterly", inputs=("annual-a", "annual-b", "quarter")),
    )
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff="2026-09-17T00:00:00Z", sources=sources, facts=facts
    )
    draft = _draft(
        "Combined conclusion. [quarterly]",
        ("derived", "industry", "quarterly"),
    )

    rendered = render_reader(_request(tmp_path), draft, snapshot, ())

    assert "Combined conclusion. [^2] [^1][^3]" in rendered.reader_text
    mappings = rendered.limitations_audit["section_evidence"]
    assert mappings[0]["source_ids"] == ["annual", "quarterly"]
    assert mappings[0]["source_footnote_numbers"] == [1, 2]
    assert mappings[1]["source_ids"] == ["industry"]
    assert mappings[2]["source_ids"] == ["quarterly"]
    assert rendered.reader_text.count("Combined conclusion. [^2] [^1][^3]") == 1


@pytest.mark.parametrize("failure", ["unknown-input", "unknown-source", "cycle"])
def test_inconsistent_fact_ancestry_fails_closed(tmp_path, failure):
    source = _source("filing")
    if failure == "unknown-input":
        broken = _fact("broken", "filing").model_copy(
            update={"inputs": ("missing",), "formula": "missing"}
        )
        facts = (broken,)
        message = "unknown fact"
    elif failure == "unknown-source":
        facts = (_fact("broken", "filing").model_copy(update={"source_id": "missing"}),)
        message = "unknown source"
    else:
        first = _fact("first", "filing").model_copy(
            update={"inputs": ("second",), "formula": "second"}
        )
        second = _fact("second", "filing").model_copy(
            update={"inputs": ("first",), "formula": "first"}
        )
        facts = (first, second)
        message = "cycle"
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff="2026-09-17T00:00:00Z", sources=(source,)
    ).model_copy(update={"facts": facts})

    with pytest.raises(ValueError, match=message):
        render_reader(_request(tmp_path), _draft("Source claim. [filing]", ("filing",)), snapshot, ())


def test_render_is_pure_and_deterministic_with_ineligible_unused_source(tmp_path):
    sources = (_source("unused", published=False), _source("annual"), _source("quarterly"))
    facts = (
        _fact("annual-fact", "annual"),
        _fact("derived", "quarterly", inputs=("annual-fact",)),
    )
    snapshot = EvidenceSnapshot(
        ticker="TEST", cutoff="2026-09-17T00:00:00Z", sources=sources, facts=facts
    )
    draft = _draft("Derived result.", ("derived",))
    snapshot_before = snapshot.model_dump(mode="json")
    draft_before = draft.model_dump(mode="json")

    first = render_reader(_request(tmp_path), draft, snapshot, ())
    second = render_reader(_request(tmp_path), draft, snapshot, ())

    assert first == second
    assert snapshot.model_dump(mode="json") == snapshot_before
    assert draft.model_dump(mode="json") == draft_before
    assert "Derived result. [^1][^2]" in first.reader_text
    assert "unused" not in first.reader_text
