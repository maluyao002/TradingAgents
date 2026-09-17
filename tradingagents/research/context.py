"""Deterministic source excerpts; limits cover source characters, not payload/tokens."""

from __future__ import annotations

import re
from itertools import chain

from .contracts import EvidenceSnapshot

_KEYWORDS = re.compile(
    r"\b(?:revenue|sales|margin|cash|debt|capital|inventory|customer|supplier|"
    r"capacity|competition|cost|risk|shares|guidance|earnings)\b|"
    r"收入|营收|利润|现金|债务|资本|库存|客户|供应商|产能|竞争|成本|风险|股份|指引",
    re.IGNORECASE,
)


def _allocate(lengths: list[int], budget: int) -> list[int]:
    """Max-min allocation; ties receive a single extra character in source order."""
    allocations = [0] * len(lengths)
    active = [i for i, length in enumerate(lengths) if length]
    while active and budget:
        share, remainder = divmod(budget, len(active))
        for position, i in enumerate(active):
            grant = min(lengths[i] - allocations[i], share + (position < remainder))
            allocations[i] += grant
            budget -= grant
        active = [i for i in active if allocations[i] < lengths[i]]
    return allocations


def _excerpts(content: str, allowance: int) -> list[dict]:
    """Offsets are Python Unicode character indices, with an exclusive end."""
    if not allowance:
        return []
    spans: list[tuple[int, int]] = []
    candidates = chain(
        [(0, min(len(content), max(1, min(512, allowance // 2))))],
        (
            (max(0, m.start() - 80), min(len(content), m.end() + 160))
            for m in _KEYWORDS.finditer(content)
        ),
        [(0, len(content))],
    )
    for start, end in candidates:
        # Subtract existing spans before spending the remaining character allowance.
        cursor = start
        additions = []
        for left, right in [*spans, (end, end)]:
            stop = min(left, end, cursor + allowance)
            if stop > cursor:
                additions.append((cursor, stop))
                allowance -= stop - cursor
            cursor = max(cursor, right)
            if cursor >= end or not allowance:
                break
        merged: list[tuple[int, int]] = []
        for left, right in sorted([*spans, *additions]):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
            else:
                merged.append((left, right))
        spans = merged
        if not allowance:
            break
    return [{"start": start, "end": end, "text": content[start:end]} for start, end in spans]


def pack_evidence(
    snapshot: EvidenceSnapshot, *, max_source_chars: int = 60000, max_chars_per_source: int = 6000
) -> dict:
    """Pack eligible evidence with immutable provenance and explicit context omissions.

    All eligible structured records are retained regardless of excerpt allocation.
    ``source_char_count`` measures original text, while ``complete`` requires all
    that text and full_text availability. Offsets count Unicode code points, not
    UTF-8 bytes. No claim is made about total serialized size or token allowance.
    """
    for name, value in (
        ("max_source_chars", max_source_chars),
        ("max_chars_per_source", max_chars_per_source),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    gaps: list[str] = []
    sources = []
    for source in snapshot.sources:
        if source.published_at is None or source.published_at > snapshot.cutoff:
            gaps.append(f"Source {source.id} omitted: publication unavailable or after cutoff.")
        else:
            sources.append(source)
    source_ids = {source.id for source in sources}
    # Admit only facts whose full operand ancestry is retained; cycles fail closed.
    fact_ids: set[str] = set()
    pending = [
        f
        for f in snapshot.facts
        if f.source_id in source_ids and f.period_end <= snapshot.cutoff.date()
    ]
    while pending:
        ready = [f for f in pending if set(f.inputs) <= fact_ids]
        if not ready:
            break
        fact_ids.update(f.id for f in ready)
        pending = [f for f in pending if f.id not in fact_ids]
    events = [
        e
        for e in snapshot.events
        if e.source_id in source_ids
        and e.published_at is not None
        and e.published_at <= snapshot.cutoff
    ]
    expectations = [
        e
        for e in snapshot.expectations
        if set(e.source_ids) <= source_ids and e.as_of <= snapshot.cutoff
    ]
    facts = [f for f in snapshot.facts if f.id in fact_ids]
    for name, original, kept in (
        ("facts", snapshot.facts, facts),
        ("events", snapshot.events, events),
        ("expectations", snapshot.expectations, expectations),
    ):
        if len(kept) != len(original):
            gaps.append(
                f"{len(original) - len(kept)} {name} omitted: ineligible or missing dependencies."
            )
    allowances = _allocate(
        [min(len(s.content), max_chars_per_source) for s in sources], max_source_chars
    )
    packed_sources = []
    for source, allowance in zip(sources, allowances, strict=True):
        entry = source.model_dump(mode="json", exclude={"content"})
        entry["excerpts"] = _excerpts(source.content, allowance)
        entry["source_char_count"] = len(source.content)
        entry["complete"] = allowance == len(source.content) and source.availability == "full_text"
        if allowance < len(source.content):
            gaps.append(
                f"Source {source.id} text omitted/truncated: {allowance}/{len(source.content)} characters retained."
            )
        if source.availability != "full_text":
            gaps.append(f"Source {source.id} full text unavailable: {source.availability}.")
        packed_sources.append(entry)
    if not sources or not any(s.content for s in sources):
        gaps.append("No eligible source text available; empty context does not establish absence.")
    result = snapshot.model_dump(
        mode="json", exclude={"sources", "facts", "events", "expectations"}
    )
    result.update(
        sources=packed_sources,
        facts=[f.model_dump(mode="json") for f in facts],
        events=[e.model_dump(mode="json") for e in events],
        expectations=[e.model_dump(mode="json") for e in expectations],
        context_gaps=gaps,
        context_budget_scope="Source excerpt Unicode characters only; excludes metadata, structured records, and serialization.",
    )
    return result
