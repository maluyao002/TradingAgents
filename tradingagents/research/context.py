"""Deterministic source excerpts; limits cover source characters, not payload/tokens."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from itertools import chain

from .contracts import EvidenceSnapshot

_KEYWORDS = re.compile(
    r"\b(?:revenue|sales|margin|cash|debt|capital|inventory|customer|supplier|"
    r"capacity|competition|cost|risk|shares|guidance|earnings)\b|"
    r"收入|营收|利润|现金|债务|资本|库存|客户|供应商|产能|竞争|成本|风险|股份|指引",
    re.IGNORECASE,
)

_TOKEN = re.compile(r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE)
_TARGETED_CONTEXT_VERSION = 2
_MAX_QUERIES = 16
_MAX_QUERY_CHARS = 512
_MAX_TOTAL_QUERY_CHARS = 4096
_MAX_TERMS_PER_QUERY = 32
_MAX_SCANNED_SOURCES = 128
_MAX_RETRIEVED_SOURCES = 32
_MAX_SCAN_CHARS_PER_SOURCE = 2_000_000
_MAX_TOTAL_SCAN_CHARS = 16_000_000
_MAX_RETAINED_HITS_PER_TERM = 256
_MAX_CANDIDATES_PER_QUERY_SOURCE = 64
_PASSAGE_CHARS = 900
_TABLE_PASSAGE_CHARS = 1400

# Deliberately broad question/prompt words are poor lexical retrieval keys.  Two-character
# terms are excluded except for a small domain term allowlist; e.g. ticker symbols remain
# eligible without teaching this module any issuer names.
_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "analysis",
    "analyze",
    "are",
    "as",
    "at",
    "be",
    "business",
    "by",
    "company",
    "could",
    "describe",
    "discuss",
    "do",
    "does",
    "evidence",
    "explain",
    "financial",
    "for",
    "from",
    "future",
    "has",
    "have",
    "how",
    "impact",
    "in",
    "is",
    "issuer",
    "key",
    "material",
    "of",
    "on",
    "or",
    "outlook",
    "performance",
    "primary",
    "question",
    "quarter",
    "report",
    "result",
    "results",
    "should",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "with",
    "would",
    "year",
}
_SHORT_TERM_ALLOWLIST = {"ai"}


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    focus_start: int
    focus_end: int
    matched_terms: tuple[str, ...]
    occurrence_count: int
    table: bool


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


def _pack_evidence_legacy(
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


def _stem(token: str) -> str:
    token = token.casefold().strip("'\N{RIGHT SINGLE QUOTATION MARK}-")
    if token.endswith(("'s", "\N{RIGHT SINGLE QUOTATION MARK}s")):
        token = token[:-2]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def _query_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN.finditer(query):
        raw = match.group().casefold()
        term = _stem(raw)
        if (
            raw in _STOPWORDS
            or term in _STOPWORDS
            or (len(term) < 3 and term not in _SHORT_TERM_ALLOWLIST)
            or term in seen
        ):
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) == _MAX_TERMS_PER_QUERY:
            break
    return tuple(terms)


def _validate_queries(queries: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if type(queries) is not tuple:
        raise ValueError("queries must be a tuple of strings")
    if len(queries) > _MAX_QUERIES:
        raise ValueError(f"queries must contain at most {_MAX_QUERIES} items")
    if sum(len(query) for query in queries if isinstance(query, str)) > _MAX_TOTAL_QUERY_CHARS:
        raise ValueError(f"queries must contain at most {_MAX_TOTAL_QUERY_CHARS} characters total")
    for query in queries:
        if type(query) is not str:
            raise ValueError("queries must be a tuple of strings")
        if len(query) > _MAX_QUERY_CHARS:
            raise ValueError(f"each query must contain at most {_MAX_QUERY_CHARS} characters")
    return tuple(_query_terms(query) for query in queries)


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped
        and (
            stripped.count("|") >= 2
            or "\t" in stripped
            or re.search(r"\S\s{2,}\S", stripped)
        )
    )


def _line_bounds(content: str, position: int) -> tuple[int, int]:
    start = content.rfind("\n", 0, position) + 1
    newline = content.find("\n", position)
    return start, len(content) if newline < 0 else newline + 1


def _passage_bounds(content: str, focus_start: int, focus_end: int) -> tuple[int, int, bool]:
    line_start, line_end = _line_bounds(content, focus_start)
    table = _looks_like_table_row(content[line_start:line_end])
    if table:
        start, end = line_start, line_end
        while start and end - start < _TABLE_PASSAGE_CHARS:
            previous_start, previous_end = _line_bounds(content, max(0, start - 1))
            if previous_start == start or not _looks_like_table_row(
                content[previous_start:previous_end]
            ):
                break
            if end - previous_start > _TABLE_PASSAGE_CHARS:
                break
            start = previous_start
        while end < len(content) and end - start < _TABLE_PASSAGE_CHARS:
            next_start, next_end = _line_bounds(content, end)
            if next_end == end or not _looks_like_table_row(content[next_start:next_end]):
                break
            if next_end - start > _TABLE_PASSAGE_CHARS:
                break
            end = next_end
        return start, end, True

    paragraph_start = content.rfind("\n\n", 0, focus_start) + 2
    paragraph_end_marker = content.find("\n\n", focus_end)
    paragraph_end = len(content) if paragraph_end_marker < 0 else paragraph_end_marker
    if paragraph_end - paragraph_start <= _PASSAGE_CHARS:
        return paragraph_start, paragraph_end, False
    start = max(paragraph_start, focus_start - _PASSAGE_CHARS // 2)
    end = min(paragraph_end, start + _PASSAGE_CHARS)
    start = max(paragraph_start, end - _PASSAGE_CHARS)
    return start, end, False


def _retained_positions(stat: dict) -> list[tuple[int, int]]:
    positions = [*stat["first"], *stat["last"]]
    return sorted(set(positions))


def _scan_positions(
    content: str, terms: set[str], scan_chars: int
) -> dict[str, dict[str, object]]:
    stats = {
        term: {"count": 0, "first": [], "last": deque(maxlen=_MAX_RETAINED_HITS_PER_TERM // 2)}
        for term in terms
    }
    for match in _TOKEN.finditer(content, 0, scan_chars):
        term = _stem(match.group())
        if term not in stats:
            continue
        stat = stats[term]
        stat["count"] += 1
        position = (match.start(), match.end())
        if len(stat["first"]) < _MAX_RETAINED_HITS_PER_TERM // 2:
            stat["first"].append(position)
        stat["last"].append(position)
    return stats


def _focus_positions(
    query_terms: tuple[str, ...], stats: dict[str, dict[str, object]]
) -> list[tuple[str, int, int]]:
    retained = {
        term: _retained_positions(stats[term])
        for term in query_terms
        if stats[term]["count"]
    }
    chosen: list[tuple[str, int, int]] = []
    # First and last occurrences guarantee geographic coverage for every matched term.
    for term in sorted(retained, key=lambda item: (stats[item]["count"], item)):
        positions = retained[term]
        chosen.append((term, *positions[0]))
        if positions[-1] != positions[0]:
            chosen.append((term, *positions[-1]))
    all_positions = sorted(
        (term, start, end)
        for term, positions in retained.items()
        for start, end in positions
    )
    if len(chosen) < _MAX_CANDIDATES_PER_QUERY_SOURCE:
        remaining = [position for position in all_positions if position not in chosen]
        slots = _MAX_CANDIDATES_PER_QUERY_SOURCE - len(chosen)
        if len(remaining) > slots:
            remaining = [remaining[(i * len(remaining)) // slots] for i in range(slots)]
        chosen.extend(remaining[:slots])
    return sorted(set(chosen), key=lambda item: (item[1], item[2], item[0]))[
        :_MAX_CANDIDATES_PER_QUERY_SOURCE
    ]


def _candidates_for_query(
    content: str, query_terms: tuple[str, ...], stats: dict[str, dict[str, object]]
) -> list[_Candidate]:
    candidates: dict[tuple[int, int], _Candidate] = {}
    query_set = set(query_terms)
    for _, focus_start, focus_end in _focus_positions(query_terms, stats):
        start, end, table = _passage_bounds(content, focus_start, focus_end)
        passage_terms = [_stem(match.group()) for match in _TOKEN.finditer(content, start, end)]
        matched = tuple(sorted(query_set.intersection(passage_terms)))
        candidate = _Candidate(
            start=start,
            end=end,
            focus_start=focus_start,
            focus_end=focus_end,
            matched_terms=matched,
            occurrence_count=sum(term in query_set for term in passage_terms),
            table=table,
        )
        current = candidates.get((start, end))
        if current is None or (
            len(candidate.matched_terms), candidate.occurrence_count, -candidate.focus_start
        ) > (len(current.matched_terms), current.occurrence_count, -current.focus_start):
            candidates[(start, end)] = candidate
    return sorted(
        candidates.values(),
        key=lambda candidate: (
            -len(candidate.matched_terms),
            -candidate.occurrence_count,
            candidate.start,
            candidate.end,
            candidate.focus_start,
        ),
    )


def _clip_candidate(content: str, candidate: _Candidate, limit: int) -> tuple[int, int]:
    if candidate.end - candidate.start <= limit:
        return candidate.start, candidate.end
    if candidate.table:
        row_start, row_end = _line_bounds(content, candidate.focus_start)
        if row_end - row_start <= limit:
            # Keep the entire matching row, then use spare room for adjacent table context.
            start = max(candidate.start, row_end - limit)
            end = min(candidate.end, start + limit)
            start = max(candidate.start, end - limit)
            if not (start <= row_start and row_end <= end):
                start, end = row_start, row_end
            return start, end
    start = max(candidate.start, candidate.focus_start - limit // 2)
    end = min(candidate.end, start + limit)
    start = max(candidate.start, end - limit)
    return start, end


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _targeted_excerpts(
    content: str,
    allowance: int,
    candidates_by_query: list[list[_Candidate]],
    include_fallback: bool,
) -> list[dict]:
    if not allowance:
        return []
    active = [i for i, candidates in enumerate(candidates_by_query) if candidates]
    slots: list[int | None] = [*active]
    if include_fallback or not slots:
        slots.append(None)
    quotas = _allocate([allowance] * len(slots), allowance)
    spans: list[tuple[int, int]] = []
    for slot, quota in zip(slots, quotas, strict=True):
        if not quota:
            continue
        if slot is None:
            spans.append((0, min(len(content), quota)))
            continue
        remaining = quota
        for candidate in candidates_by_query[slot]:
            if not remaining:
                break
            start, end = _clip_candidate(content, candidate, remaining)
            spans.append((start, end))
            remaining -= end - start
    return [
        {"start": start, "end": end, "text": content[start:end]}
        for start, end in _merge_spans(spans)
    ]


def _select_sources(
    candidates: list[list[list[_Candidate]]], max_sources: int
) -> list[int]:
    ranked_by_query: list[list[int]] = []
    query_count = len(candidates[0]) if candidates else 0
    for query_index in range(query_count):
        ranked = [i for i, per_source in enumerate(candidates) if per_source[query_index]]
        ranked.sort(
            key=lambda i: (
                -len(candidates[i][query_index][0].matched_terms),
                -candidates[i][query_index][0].occurrence_count,
                i,
            )
        )
        ranked_by_query.append(ranked)
    selected: list[int] = []
    positions = [0] * query_count
    while len(selected) < max_sources:
        changed = False
        for query_index, ranked in enumerate(ranked_by_query):
            while positions[query_index] < len(ranked):
                source_index = ranked[positions[query_index]]
                positions[query_index] += 1
                if source_index in selected:
                    continue
                selected.append(source_index)
                changed = True
                break
            if len(selected) == max_sources:
                break
        if not changed:
            break
    return selected


def _pack_evidence_targeted(
    snapshot: EvidenceSnapshot,
    *,
    queries: tuple[str, ...],
    max_source_chars: int,
    max_chars_per_source: int,
) -> dict:
    for name, value in (
        ("max_source_chars", max_source_chars),
        ("max_chars_per_source", max_chars_per_source),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    query_terms = _validate_queries(queries)
    # Reuse the legacy eligibility and structured-record path with a zero text budget.  This
    # keeps cutoff and operand-ancestry behavior identical without scanning ineligible text.
    result = _pack_evidence_legacy(
        snapshot, max_source_chars=0, max_chars_per_source=max_chars_per_source
    )
    source_by_id = {source.id: source for source in snapshot.sources}
    sources = [source_by_id[entry["id"]] for entry in result["sources"]]
    all_terms = set(chain.from_iterable(query_terms))
    stats_by_source: list[dict[str, dict[str, object]]] = []
    scanned_chars: list[int] = []
    remaining_scan = _MAX_TOTAL_SCAN_CHARS
    for source_index, source in enumerate(sources):
        if not all_terms or source_index >= _MAX_SCANNED_SOURCES or not remaining_scan:
            scan_chars = 0
        else:
            scan_chars = min(len(source.content), _MAX_SCAN_CHARS_PER_SOURCE, remaining_scan)
        stats_by_source.append(_scan_positions(source.content, all_terms, scan_chars))
        scanned_chars.append(scan_chars)
        remaining_scan -= scan_chars

    candidates: list[list[list[_Candidate]]] = []
    for source, stats in zip(sources, stats_by_source, strict=True):
        candidates.append(
            [_candidates_for_query(source.content, terms, stats) for terms in query_terms]
        )
    selected = _select_sources(candidates, _MAX_RETRIEVED_SOURCES)
    any_matches = bool(selected)
    if not any_matches:
        selected = [
            i
            for i, source in enumerate(sources[:_MAX_RETRIEVED_SOURCES])
            if source.content
        ]
    selected_set = set(selected)
    lengths = [
        min(len(source.content), max_chars_per_source) if i in selected_set else 0
        for i, source in enumerate(sources)
    ]
    allowances = _allocate(lengths, max_source_chars)
    unresolved_queries = [
        query_index
        for query_index in range(len(queries))
        if not any(candidates[source_index][query_index] for source_index in range(len(sources)))
    ]
    for source_index, (entry, source, allowance) in enumerate(
        zip(result["sources"], sources, allowances, strict=True)
    ):
        entry["excerpts"] = _targeted_excerpts(
            source.content,
            allowance,
            candidates[source_index],
            include_fallback=bool(unresolved_queries),
        )
        retained = sum(len(excerpt["text"]) for excerpt in entry["excerpts"])
        entry["complete"] = retained == len(source.content) and source.availability == "full_text"

    # Replace zero-budget legacy truncation messages with actual targeted retention counts.
    result["context_gaps"] = [
        gap
        for gap in result["context_gaps"]
        if not (gap.startswith("Source ") and " text omitted/truncated:" in gap)
    ]
    for entry, source in zip(result["sources"], sources, strict=True):
        retained = sum(len(excerpt["text"]) for excerpt in entry["excerpts"])
        if retained < len(source.content):
            result["context_gaps"].append(
                f"Source {source.id} text omitted/truncated: {retained}/{len(source.content)} characters retained."
            )
    for query_index in unresolved_queries:
        result["context_gaps"].append(
            f"Targeted query {query_index} not found by bounded keyword matching; fallback context does not establish absence."
        )
    scan_was_bounded = bool(all_terms) and (
        len(sources) > _MAX_SCANNED_SOURCES
        or any(
            scanned < len(source.content)
            for source, scanned in zip(sources, scanned_chars, strict=True)
        )
    )
    if scan_was_bounded:
        result["context_gaps"].append(
            "Targeted keyword scan reached a deterministic source/character bound; unscanned text may contain matches."
        )

    query_metadata = []
    for query_index, terms in enumerate(query_terms):
        hits = []
        for source_index, (source, stats) in enumerate(zip(sources, stats_by_source, strict=True)):
            matched_terms = [term for term in terms if stats[term]["count"]]
            if not matched_terms:
                continue
            hits.append(
                {
                    "source_id": source.id,
                    "match_count": sum(int(stats[term]["count"]) for term in matched_terms),
                    "matched_terms": matched_terms,
                    "selected": source_index in selected_set,
                }
            )
        query_metadata.append(
            {
                "query_index": query_index,
                "terms": list(terms),
                "match_label": "keyword_match" if hits else "not_found",
                "unresolved": not bool(hits),
                "hit_count": sum(hit["match_count"] for hit in hits),
                "hits": hits,
            }
        )
    result["context_version"] = _TARGETED_CONTEXT_VERSION
    result["retrieval_metadata"] = {
        "version": _TARGETED_CONTEXT_VERSION,
        "mode": "bounded_keyword_passages",
        "queries": query_metadata,
        "scan": {
            "eligible_sources": len(sources),
            "scanned_sources": sum(bool(char_count) for char_count in scanned_chars),
            "scanned_characters": sum(scanned_chars),
            "bounded": scan_was_bounded,
        },
        "limits": {
            "max_queries": _MAX_QUERIES,
            "max_query_characters": _MAX_QUERY_CHARS,
            "max_total_query_characters": _MAX_TOTAL_QUERY_CHARS,
            "max_terms_per_query": _MAX_TERMS_PER_QUERY,
            "max_scanned_sources": _MAX_SCANNED_SOURCES,
            "max_retrieved_sources": _MAX_RETRIEVED_SOURCES,
            "max_scan_characters_per_source": _MAX_SCAN_CHARS_PER_SOURCE,
            "max_total_scan_characters": _MAX_TOTAL_SCAN_CHARS,
        },
    }
    return result


def pack_evidence(
    snapshot: EvidenceSnapshot,
    *,
    max_source_chars: int = 60000,
    max_chars_per_source: int = 6000,
    queries: tuple[str, ...] = (),
) -> dict:
    """Pack evidence, optionally selecting bounded exact passages for lexical queries.

    Empty/default ``queries`` takes the byte-for-byte legacy payload path. Query mode is
    local and deterministic: metadata labels lexical matches only and makes no claim that a
    research question or fact has been semantically resolved.
    """
    if not queries:
        return _pack_evidence_legacy(
            snapshot,
            max_source_chars=max_source_chars,
            max_chars_per_source=max_chars_per_source,
        )
    return _pack_evidence_targeted(
        snapshot,
        queries=queries,
        max_source_chars=max_source_chars,
        max_chars_per_source=max_chars_per_source,
    )
