"""Pure, bounded rendering for research reader and limitations-audit artifacts.

The reader intentionally contains authored report prose, authored material
limitations, and structured critical blockers.  Raw workflow gaps remain available
losslessly in the companion audit mapping instead of being copied into the reader.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias
from urllib.parse import quote, urlsplit

from .case_report import CaseReportDraft
from .contracts import EvidenceSnapshot, ReportLanguage, ResearchRequest, ReviewFinding
from .stages import ReportDraft

LIMITATIONS_AUDIT_FILENAME = "reader_limitations.json"

IssueSeverity: TypeAlias = Literal["unknown", "info", "warning", "critical"]
IssueCategory: TypeAlias = Literal[
    "unclassified",
    "financial",
    "data",
    "numerical",
    "research",
    "editorial",
    "security",
    "operational",
]

_SEVERITIES = {"unknown", "info", "warning", "critical"}
_CATEGORIES = {
    "unclassified",
    "financial",
    "data",
    "numerical",
    "research",
    "editorial",
    "security",
    "operational",
}
_SEVERITY_RANK = {"unknown": 0, "info": 1, "warning": 2, "critical": 3}


@dataclass(frozen=True)
class ReaderIssue:
    """A gap with enough structure to make safe reader-display decisions.

    ``provenance_id`` is supplied by the producing stage and is retained verbatim in
    the audit.  Use ``category="operational"`` for usage, recovery, provider, and
    workflow diagnostics; their raw messages are never copied into the reader.
    """

    message: str
    provenance_id: str
    severity: IssueSeverity = "unknown"
    category: IssueCategory = "unclassified"
    code: str | None = None
    affected_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("structured reader issues require a nonblank message")
        if not self.provenance_id.strip():
            raise ValueError("structured reader issues require a provenance identifier")
        if self.severity not in _SEVERITIES:
            raise ValueError("unsupported reader issue severity")
        if self.category not in _CATEGORIES:
            raise ValueError("unsupported reader issue category")


GapInput: TypeAlias = str | ReaderIssue | ReviewFinding


@dataclass(frozen=True)
class ReaderRender:
    """The two artifacts produced by :func:`render_reader`.

    ``limitations_audit`` contains only JSON-compatible values and can be passed
    directly to the repository's canonical JSON serializer.
    """

    reader_text: str
    limitations_audit: dict[str, Any]


def _eligible_evidence_ids(snapshot: EvidenceSnapshot) -> set[str]:
    eligible_sources = {
        source.id
        for source in snapshot.sources
        if source.published_at is not None and source.availability == "full_text"
    }
    fact_ids: set[str] = set()
    pending = [fact for fact in snapshot.facts if fact.source_id in eligible_sources]
    while pending:
        ready = {fact.id for fact in pending if set(fact.inputs) <= fact_ids}
        if not ready:
            break
        fact_ids.update(ready)
        pending = [fact for fact in pending if fact.id not in fact_ids]
    return eligible_sources | fact_ids | {
        event.id for event in snapshot.events if event.source_id in eligible_sources
    } | {
        expectation.id
        for expectation in snapshot.expectations
        if not set(expectation.source_ids) - eligible_sources
    }


def _content_id(prefix: str, text: str) -> str:
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _single_line(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines()).strip()


def _markdown_label(value: str) -> str:
    return _single_line(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _safe_http_url(value: str) -> str | None:
    clean = "".join(character for character in value.strip() if ord(character) >= 32)
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return quote(clean, safe="/:?#[]@!$&'()*+,;=%")


def _bullet(value: str) -> str:
    # Limitation text is retained verbatim in audit, but cannot author renderer
    # source ordinals through a displayed bullet either.
    value = re.sub(r"\[\^\d+\]", lambda match: "\\[" + match[0][1:-1] + "\\]", value)
    lines = value.splitlines() or [""]
    return "- " + "\n  ".join(lines)


def _source_ids_by_evidence(snapshot: EvidenceSnapshot) -> dict[str, tuple[str, ...]]:
    source_order = tuple(source.id for source in snapshot.sources)
    source_ids = set(source_order)
    facts = {fact.id: fact for fact in snapshot.facts}
    if len(facts) != len(snapshot.facts):
        raise ValueError("evidence snapshot contains duplicate fact identifiers")
    memo: dict[str, frozenset[str]] = {}
    visiting: set[str] = set()

    def fact_sources(identifier: str) -> frozenset[str]:
        if identifier in memo:
            return memo[identifier]
        if identifier in visiting:
            raise ValueError("fact source ancestry contains a cycle")
        fact = facts.get(identifier)
        if fact is None:
            raise ValueError(f"fact source ancestry references unknown fact {identifier!r}")
        if fact.source_id not in source_ids:
            raise ValueError(
                f"fact {identifier!r} references unknown source {fact.source_id!r}"
            )
        visiting.add(identifier)
        resolved = {fact.source_id}
        for input_id in fact.inputs:
            resolved.update(fact_sources(input_id))
        visiting.remove(identifier)
        memo[identifier] = frozenset(resolved)
        return memo[identifier]

    result = {source_id: (source_id,) for source_id in source_order}
    for fact in snapshot.facts:
        resolved = fact_sources(fact.id)
        result[fact.id] = tuple(source_id for source_id in source_order if source_id in resolved)
    for event in snapshot.events:
        if event.source_id not in source_ids:
            raise ValueError(f"event {event.id!r} references unknown source {event.source_id!r}")
        result[event.id] = (event.source_id,)
    for item in snapshot.expectations:
        unknown = set(item.source_ids) - source_ids
        if unknown:
            raise ValueError(f"expectation {item.id!r} references unknown sources")
        selected = set(item.source_ids)
        result[item.id] = tuple(
            source_id for source_id in source_order if source_id in selected
        )
    return result


def _append_missing_footnotes(
    body: str,
    required_source_ids: tuple[str, ...],
    cited_source_ids: set[str],
    source_numbers: dict[str, int],
) -> tuple[str, set[str]]:
    missing = [
        source_id for source_id in required_source_ids if source_id not in cited_source_ids
    ]
    if not missing:
        return body, cited_source_ids
    footnotes = "".join(f"[^{source_numbers[source_id]}]" for source_id in missing)
    separator = "" if not body or body[-1].isspace() else " "
    return body + separator + footnotes, cited_source_ids | set(missing)


def _coerce_issues(gaps: Iterable[GapInput]) -> list[dict[str, Any]]:
    occurrences = []
    for index, gap in enumerate(gaps, start=1):
        occurrence_id = f"gap-occurrence-{index:04d}"
        if isinstance(gap, str):
            message = gap
            provenance_id = f"gap:{index:04d}"
            severity = "unknown"
            category = "unclassified"
            code = None
            affected_ids: tuple[str, ...] = ()
            source_kind = "raw_gap"
        elif isinstance(gap, ReaderIssue):
            message = gap.message
            provenance_id = gap.provenance_id
            severity = gap.severity
            category = gap.category
            code = gap.code
            affected_ids = gap.affected_ids
            source_kind = "structured_issue"
        elif isinstance(gap, ReviewFinding):
            message = gap.message
            provenance_id = f"review:{index:04d}:{gap.code}"
            severity = gap.severity
            category = gap.category
            code = gap.code
            affected_ids = gap.affected_ids
            source_kind = "review_finding"
        else:
            raise TypeError("gaps must contain strings, ReaderIssue, or ReviewFinding values")
        occurrences.append(
            {
                "occurrence_id": occurrence_id,
                "issue_id": _content_id("issue", message),
                "original_text": message,
                "provenance_id": provenance_id,
                "severity": severity,
                "category": category,
                "code": code,
                "affected_ids": list(affected_ids),
                "source_kind": source_kind,
            }
        )
    return occurrences


def _consolidate_issues(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence["original_text"], []).append(occurrence)

    consolidated = []
    for original_text, items in grouped.items():
        structured_critical = [
            item
            for item in items
            if item["source_kind"] != "raw_gap" and item["severity"] == "critical"
        ]
        if any(item["category"] != "operational" for item in structured_critical):
            display_mode = "full_text"
        elif structured_critical:
            display_mode = "generic_operational"
        else:
            display_mode = "audit_only"
        severity = max(items, key=lambda item: _SEVERITY_RANK[item["severity"]])["severity"]
        consolidated.append(
            {
                "issue_id": items[0]["issue_id"],
                "original_text": original_text,
                "occurrence_ids": [item["occurrence_id"] for item in items],
                "provenance_ids": [item["provenance_id"] for item in items],
                "severities": list(dict.fromkeys(item["severity"] for item in items)),
                "categories": list(dict.fromkeys(item["category"] for item in items)),
                "highest_severity": severity,
                "reader_display": display_mode,
                "displayed_in_reader": display_mode != "audit_only",
            }
        )
    return consolidated


def _draft_limitation_audit(draft: ReportDraft | None) -> dict[str, Any]:
    limitations = draft.limitations if draft else ()
    occurrences = [
        {
            "occurrence_id": f"draft-limitation-occurrence-{index:04d}",
            "limitation_id": _content_id("draft-limitation", text),
            "original_text": text,
            "provenance_id": f"draft.limitations[{index - 1}]",
        }
        for index, text in enumerate(limitations, start=1)
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence["original_text"], []).append(occurrence)
    consolidated = [
        {
            "limitation_id": items[0]["limitation_id"],
            "original_text": text,
            "occurrence_ids": [item["occurrence_id"] for item in items],
            "provenance_ids": [item["provenance_id"] for item in items],
            "displayed_in_reader": True,
        }
        for text, items in grouped.items()
    ]
    return {
        "draft_available": draft is not None,
        "occurrences": occurrences,
        "consolidated_exact_text": consolidated,
    }


def _explicit_evidence_ids(body: str, known_evidence_ids: set[str]) -> tuple[str, ...]:
    """Return only authored bracket references to known evidence.

    Reader prose can contain ordinary square brackets.  A reference is evidence
    only when its exact identifier is present in the frozen snapshot; the
    renderer never infers a source from nearby prose or declared section scope.
    """

    if not known_evidence_ids:
        return ()
    pattern = re.compile(
        r"\[(" + "|".join(re.escape(identifier) for identifier in sorted(
            known_evidence_ids, key=len, reverse=True
        )) + r")\]"
    )
    return tuple(dict.fromkeys(match.group(1) for match in pattern.finditer(body)))


def _replace_source_references(
    body: str,
    source_ids_by_evidence: dict[str, tuple[str, ...]],
    source_numbers: dict[str, int],
) -> tuple[str, set[str]]:
    # Numeric footnotes belong exclusively to this renderer. Accepting authored
    # ordinals would invent a source relationship (or index a nonexistent one).
    if re.search(r"\[\^\d+\]", body):
        raise ValueError("draft must cite evidence IDs, not authored numeric footnotes")
    known_evidence_ids = set(source_ids_by_evidence)
    explicit_ids = _explicit_evidence_ids(body, known_evidence_ids)
    for identifier in explicit_ids:
        if set(source_ids_by_evidence[identifier]) - set(source_numbers):
            raise ValueError("draft explicitly cited ineligible evidence")
    if not explicit_ids:
        return body, set()
    pattern = re.compile(
        r"\[(" + "|".join(re.escape(identifier) for identifier in sorted(
            explicit_ids, key=len, reverse=True
        )) + r")\]"
    )
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        source_ids = source_ids_by_evidence[identifier]
        used.update(source_ids)
        return "".join(f"[^{source_numbers[source_id]}]" for source_id in source_ids)

    return pattern.sub(replace, body), used


def _paragraph_citations(
    body: str,
    source_numbers: dict[str, int],
    section_index: int,
    *,
    include_missing_explicit: bool = False,
) -> list[dict[str, Any]]:
    """Record explicit footnotes, and flag uncited evidence-bearing paragraphs."""

    source_by_number = {number: source_id for source_id, number in source_numbers.items()}
    citations = []
    for paragraph_index, block in enumerate(body.split("\n\n"), start=1):
        numbers = tuple(dict.fromkeys(
            int(match.group(1)) for match in re.finditer(r"\[\^(\d+)\]", block)
        ))
        if not numbers:
            if include_missing_explicit and block.strip():
                citations.append(
                    {
                        "section_index": section_index,
                        "paragraph_index": paragraph_index,
                        "source_ids": [],
                        "source_footnote_numbers": [],
                        "citation_scope": "missing_explicit",
                    }
                )
            continue
        citations.append(
            {
                "section_index": section_index,
                "paragraph_index": paragraph_index,
                "source_ids": [source_by_number[number] for number in numbers],
                "source_footnote_numbers": list(numbers),
                "citation_scope": "paragraph",
            }
        )
    return citations


def _compact_state(
    *,
    compact: bool,
    draft: ReportDraft | None,
) -> dict[str, Any]:
    """Select a candidate presentation; this never accepts or verifies it."""

    has_authored_material_gaps = isinstance(draft, CaseReportDraft) and any(
        section.purpose == "material_gaps" and section.text.strip()
        for section in draft.sections
    )
    active = bool(compact and has_authored_material_gaps)
    if not compact:
        reason = "not_requested"
    elif not has_authored_material_gaps:
        reason = "authored_material_gaps_section_required"
    else:
        reason = "candidate_requires_exact_reader_verification"
    return {
        "requested": compact,
        "active": active,
        "reason": reason,
        "presentation_only_requires_exact_reader_verification": compact,
        "accepted": False,
        "mandatory_coverage_sources": ["draft_limitations", "unresolved_issues"],
    }


def _compact_display_issues(
    issues: list[dict[str, Any]], compact_active: bool
) -> list[dict[str, Any]]:
    """Keep security/numerical blockers literal; defer other coverage to review."""

    if not compact_active:
        return issues
    displayed = []
    for item in issues:
        copied = dict(item)
        if copied["reader_display"] != "audit_only" and not {
            "security", "numerical"
        }.intersection(copied["categories"]):
            copied["reader_display"] = "authored_material_gaps_pending_verification"
            copied["displayed_in_reader"] = False
        displayed.append(copied)
    return displayed


def _source_audit(
    snapshot: EvidenceSnapshot,
    eligible: set[str],
    source_numbers: dict[str, int],
    section_mappings: list[dict[str, Any]],
    body_citations: dict[str, set[int]],
) -> list[dict[str, Any]]:
    sections_by_source: dict[str, set[int]] = {}
    for mapping in section_mappings:
        for source_id in mapping["source_ids"]:
            sections_by_source.setdefault(source_id, set()).add(mapping["section_index"])
    for source_id, section_indexes in body_citations.items():
        sections_by_source.setdefault(source_id, set()).update(section_indexes)
    return [
        {
            "source_id": source.id,
            "eligible": source.id in eligible,
            "footnote_number": source_numbers.get(source.id),
            "title": source.title,
            "url": source.url,
            "publisher": source.publisher,
            "published_at": source.published_at.isoformat() if source.published_at else None,
            "availability": source.availability,
            "referenced_by_section_indexes": sorted(sections_by_source.get(source.id, set())),
        }
        for source in snapshot.sources
    ]


def render_reader(
    request: ResearchRequest,
    draft: ReportDraft | None,
    snapshot: EvidenceSnapshot,
    gaps: Iterable[GapInput],
    language: ReportLanguage | None = None,
    *,
    compact: bool = False,
) -> ReaderRender:
    """Render a reader report and a complete limitations/source audit.

    This function performs no I/O and invokes no provider.  Exact duplicate strings
    are consolidated for display, but every original occurrence and provenance ID is
    retained in ``limitations_audit``.  No semantic deduplication is attempted.

    ``compact=True`` selects an unverified candidate format only when a
    ``CaseReportDraft`` has an authored ``material_gaps`` section.  The caller
    must perform factual review and mandatory issue coverage against these exact
    bytes before export; this renderer never turns a later review into a rewrite.
    """

    language = language or request.report_language
    chinese = language == "Chinese"
    eligible = _eligible_evidence_ids(snapshot)
    eligible_sources = [source for source in snapshot.sources if source.id in eligible]
    source_ids_by_evidence = _source_ids_by_evidence(snapshot)

    issue_occurrences = _coerce_issues(gaps)
    consolidated_issues = _consolidate_issues(issue_occurrences)
    draft_audit = _draft_limitation_audit(draft)
    compact_state = _compact_state(
        compact=compact,
        draft=draft,
    )
    if compact_state["active"]:
        for item in draft_audit["consolidated_exact_text"]:
            item["displayed_in_reader"] = False
            item["reader_display"] = "authored_material_gaps_pending_verification"
    consolidated_issues = _compact_display_issues(consolidated_issues, compact_state["active"])

    explicit_source_ids: set[str] = set()
    section_evidence_source_ids: set[str] = set()
    if compact_state["active"] and draft is not None:
        eligible_source_ids = {source.id for source in eligible_sources}
        for section in draft.sections:
            for identifier in _explicit_evidence_ids(section.text, set(source_ids_by_evidence)):
                source_ids = source_ids_by_evidence[identifier]
                if set(source_ids) - eligible_source_ids:
                    raise ValueError("draft explicitly cited ineligible evidence")
                explicit_source_ids.update(source_ids)
            for evidence_id in section.evidence_ids:
                if evidence_id not in eligible:
                    raise ValueError("draft invented or used ineligible evidence identifiers")
                source_ids = source_ids_by_evidence[evidence_id]
                if set(source_ids) - eligible_source_ids:
                    raise ValueError("draft used ineligible evidence identifiers")
                section_evidence_source_ids.update(source_ids)
    if compact_state["active"]:
        cited_sources = [
            source
            for source in eligible_sources
            if source.id in explicit_source_ids | section_evidence_source_ids
        ]
        source_numbers = {source.id: index for index, source in enumerate(cited_sources, start=1)}
    else:
        # Keep the historical broad footnote behavior unless the compact gate is
        # explicitly and safely active.  Existing saved previews therefore retain
        # byte-compatible citation semantics.
        source_numbers = {source.id: index for index, source in enumerate(eligible_sources, start=1)}

    title = "深度研究报告" if chinese else "Deep research report"
    status = "需要复核 / 未评级" if chinese else "Needs review / Unrated"
    as_of = "截止时间" if chinese else "As of"
    preview = (
        "研究基础预览；并非已接受的投资评估。"
        if chinese
        else "Research foundation preview; not an accepted investment assessment."
    )
    text = [
        f"# {request.ticker} — {title}",
        "",
        f"**{status}**",
        "",
        f"{as_of}: {request.cutoff.isoformat()}",
        "",
        preview,
    ]

    section_mappings: list[dict[str, Any]] = []
    paragraph_citations: list[dict[str, Any]] = []
    section_citations: list[dict[str, Any]] = []
    body_citations: dict[str, set[int]] = {}
    if draft is None:
        heading = "仅限诊断 — 无读者报告草稿" if chinese else "Diagnostic only — no reader draft"
        message = (
            "没有可用的已验证读者草稿。本文件不是最终研究报告，不应视为投资评估。"
            if chinese
            else "No verified reader draft was available. This file is not a final research "
            "report and must not be treated as an investment assessment."
        )
        text.extend(["", f"## {heading}", "", message])
    else:
        for section_index, section in enumerate(draft.sections, start=1):
            unknown = set(section.evidence_ids) - eligible
            if unknown:
                raise ValueError("draft invented or used ineligible evidence identifiers")
            body, cited_source_ids = _replace_source_references(
                section.text, source_ids_by_evidence, source_numbers
            )
            required_set = {
                source_id
                for evidence_id in section.evidence_ids
                for source_id in source_ids_by_evidence[evidence_id]
            }
            required_source_ids = tuple(
                source.id for source in eligible_sources if source.id in required_set
            )
            if required_set != set(required_source_ids):
                raise ValueError("eligible evidence has ineligible source ancestry")
            if not compact_state["active"]:
                body, cited_source_ids = _append_missing_footnotes(
                    body, required_source_ids, cited_source_ids, source_numbers
                )
            for source_id in cited_source_ids:
                body_citations.setdefault(source_id, set()).add(section_index)
            paragraph_citations.extend(
                _paragraph_citations(
                    body,
                    source_numbers,
                    section_index,
                    include_missing_explicit=compact_state["active"] and bool(required_source_ids),
                )
            )
            fallback_source_ids = tuple(
                source_id for source_id in required_source_ids if source_id not in cited_source_ids
            )
            fallback_line = None
            if compact_state["active"] and fallback_source_ids:
                fallback_footnotes = "".join(
                    f"[^{source_numbers[source_id]}]" for source_id in fallback_source_ids
                )
                fallback_label = (
                    "本节来源（非段落级支持）："
                    if chinese
                    else "Section sources (not paragraph-level support): "
                )
                fallback_line = fallback_label + fallback_footnotes
                for source_id in fallback_source_ids:
                    body_citations.setdefault(source_id, set()).add(section_index)
                section_citations.append(
                    {
                        "section_index": section_index,
                        "citation_scope": "section_only",
                        "source_ids": list(fallback_source_ids),
                        "source_footnote_numbers": [
                            source_numbers[source_id] for source_id in fallback_source_ids
                        ],
                    }
                )
            elif compact_state["active"] and required_source_ids:
                section_citations.append(
                    {
                        "section_index": section_index,
                        "citation_scope": "paragraph_only",
                        "source_ids": [],
                        "source_footnote_numbers": [],
                    }
                )
            for evidence_id in section.evidence_ids:
                source_ids = source_ids_by_evidence[evidence_id]
                section_mappings.append(
                    {
                        "section_index": section_index,
                        "section_title": section.title,
                        "evidence_id": evidence_id,
                        "source_ids": list(source_ids),
                        "source_footnote_numbers": [
                            source_numbers[source_id]
                            for source_id in source_ids
                            if source_id in source_numbers
                        ],
                    }
                )
            text.extend(["", f"## {_markdown_label(section.title)}", "", body])
            if fallback_line is not None:
                text.extend(["", fallback_line])

    authored_limitations = draft_audit["consolidated_exact_text"]
    if not compact_state["active"]:
        limitations_heading = "重要限制 / Material limitations" if chinese else "Material limitations"
        text.extend(["", f"## {limitations_heading}", ""])
        if authored_limitations:
            text.extend(_bullet(item["original_text"]) for item in authored_limitations)
        else:
            text.append(
                "- 未提供作者撰写的重要限制；请查阅完整审计记录。"
                if chinese
                else "- No authored material limitations were supplied; consult the complete audit."
            )

    critical_issues = [
        item
        for item in consolidated_issues
        if item["reader_display"]
        not in {"audit_only", "authored_material_gaps_pending_verification"}
    ]
    if critical_issues:
        critical_heading = "关键阻碍" if chinese else "Critical blockers"
        text.extend(["", f"## {critical_heading}", ""])
        for item in critical_issues:
            if item["reader_display"] == "generic_operational":
                summary = (
                    "存在影响研究完整性的关键运行问题；原始详情仅保留在审计记录中。"
                    if chinese
                    else "A critical operational issue affected research completeness; raw "
                    "details are retained only in the audit."
                )
            else:
                summary = item["original_text"]
            text.append(_bullet(summary))

    audit_notice = (
        f"完整的未解决问题记录见 [{LIMITATIONS_AUDIT_FILENAME}]"
        f"({LIMITATIONS_AUDIT_FILENAME})。研究状态仍为需要复核 / 未评级。"
        if chinese
        else f"The complete unresolved-issue record is available in "
        f"[{LIMITATIONS_AUDIT_FILENAME}]({LIMITATIONS_AUDIT_FILENAME}). "
        "Research status remains Needs review / Unrated."
    )
    text.extend(["", audit_notice])

    sources_heading = "来源 / Sources" if chinese else "Sources"
    text.extend(["", f"## {sources_heading}", ""])
    rendered_sources = [source for source in eligible_sources if source.id in source_numbers]
    if rendered_sources:
        for source in rendered_sources:
            number = source_numbers[source.id]
            label = _markdown_label(source.title) or source.id
            publisher = _markdown_label(source.publisher)
            url = _safe_http_url(source.url)
            source_text = f"[{label}](<{url}>)" if url else label
            suffix = f" — {publisher}" if publisher else ""
            text.append(f"[^{number}]: {source_text}{suffix}")
    else:
        text.append(
            "没有可用的来源脚注。" if chinese else "No eligible source footnotes were available."
        )

    source_footnotes = _source_audit(
        snapshot, eligible, source_numbers, section_mappings, body_citations
    )
    if not compact_state["active"]:
        citation_scope = "legacy_section_evidence_footnotes"
    elif any(item["citation_scope"] == "section_only" for item in section_citations):
        citation_scope = (
            "mixed"
            if any(item["citation_scope"] == "paragraph_only" for item in section_citations)
            or any(item["citation_scope"] == "paragraph" for item in paragraph_citations)
            else "section_only"
        )
    elif section_citations:
        citation_scope = "paragraph_only"
    else:
        citation_scope = "none"
    audit = {
        "schema_version": 2,
        "artifact": LIMITATIONS_AUDIT_FILENAME,
        "ticker": request.ticker,
        "cutoff": request.cutoff.isoformat(),
        "language": language,
        "research_status": "needs_review",
        "investment_view": "unrated",
        "deduplication": "exact_original_text_only",
        "reader_policy": {
            "raw_gaps": "audit_only",
            "draft_limitations": (
                "authored_material_gaps_and_full_audit"
                if compact_state["active"] else "reader_and_audit"
            ),
            "structured_critical_nonoperational": (
                "security_and_numerical_reader_and_audit; other_categories_pending_exact_coverage"
                if compact_state["active"] else "reader_and_audit"
            ),
            "structured_critical_operational": (
                "authored_material_gaps_pending_exact_coverage_and_full_audit"
                if compact_state["active"] else "generic_reader_notice_and_full_audit"
            ),
            "citations": (
                "explicit_evidence_links_only; unused_sources_audit_only"
                if compact_state["active"] else "legacy_section_evidence_footnotes"
            ),
        },
        "citation_scope": citation_scope,
        "reader_compaction": compact_state,
        "draft_limitations": draft_audit,
        "unresolved_issues": {
            "occurrences": issue_occurrences,
            "consolidated_exact_text": consolidated_issues,
            "unrepresented_issue_ids": [
                item["issue_id"]
                for item in consolidated_issues
                if not item["displayed_in_reader"]
            ],
        },
        "section_evidence": section_mappings,
        "paragraph_citations": paragraph_citations,
        "section_citations": section_citations,
        "source_footnotes": source_footnotes,
    }
    return ReaderRender(reader_text="\n".join(text) + "\n", limitations_audit=audit)
