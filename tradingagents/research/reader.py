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
    lines = value.splitlines() or [""]
    return "- " + "\n  ".join(lines)


def _source_ids_by_evidence(snapshot: EvidenceSnapshot) -> dict[str, tuple[str, ...]]:
    result = {source.id: (source.id,) for source in snapshot.sources}
    result.update({fact.id: (fact.source_id,) for fact in snapshot.facts})
    result.update({event.id: (event.source_id,) for event in snapshot.events})
    result.update({item.id: item.source_ids for item in snapshot.expectations})
    return result


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


def _replace_source_references(
    body: str, source_numbers: dict[str, int]
) -> tuple[str, set[str]]:
    if not source_numbers:
        return body, set()
    pattern = re.compile(
        r"\[(" + "|".join(re.escape(identifier) for identifier in sorted(
            source_numbers, key=len, reverse=True
        )) + r")\]"
    )
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        used.add(identifier)
        return f"[^{source_numbers[identifier]}]"

    return pattern.sub(replace, body), used


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
) -> ReaderRender:
    """Render a reader report and a complete limitations/source audit.

    This function performs no I/O and invokes no provider.  Exact duplicate strings
    are consolidated for display, but every original occurrence and provenance ID is
    retained in ``limitations_audit``.  No semantic deduplication is attempted.
    """

    language = language or request.report_language
    chinese = language == "Chinese"
    eligible = _eligible_evidence_ids(snapshot)
    eligible_sources = [source for source in snapshot.sources if source.id in eligible]
    source_numbers = {source.id: index for index, source in enumerate(eligible_sources, start=1)}
    source_ids_by_evidence = _source_ids_by_evidence(snapshot)

    issue_occurrences = _coerce_issues(gaps)
    consolidated_issues = _consolidate_issues(issue_occurrences)
    draft_audit = _draft_limitation_audit(draft)

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
            body, cited_source_ids = _replace_source_references(section.text, source_numbers)
            for source_id in cited_source_ids:
                body_citations.setdefault(source_id, set()).add(section_index)
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

    limitations_heading = "重要限制 / Material limitations" if chinese else "Material limitations"
    text.extend(["", f"## {limitations_heading}", ""])
    authored_limitations = draft_audit["consolidated_exact_text"]
    if authored_limitations:
        text.extend(_bullet(item["original_text"]) for item in authored_limitations)
    else:
        text.append(
            "- 未提供作者撰写的重要限制；请查阅完整审计记录。"
            if chinese
            else "- No authored material limitations were supplied; consult the complete audit."
        )

    critical_issues = [
        item for item in consolidated_issues if item["reader_display"] != "audit_only"
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
            text.append(
                _bullet(f"{summary} (audit issue `{item['issue_id']}`)")
            )

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
    if eligible_sources:
        for source in eligible_sources:
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
    audit = {
        "schema_version": 1,
        "artifact": LIMITATIONS_AUDIT_FILENAME,
        "ticker": request.ticker,
        "cutoff": request.cutoff.isoformat(),
        "language": language,
        "research_status": "needs_review",
        "investment_view": "unrated",
        "deduplication": "exact_original_text_only",
        "reader_policy": {
            "raw_gaps": "audit_only",
            "draft_limitations": "reader_and_audit",
            "structured_critical_nonoperational": "reader_and_audit",
            "structured_critical_operational": "generic_reader_notice_and_full_audit",
        },
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
        "source_footnotes": source_footnotes,
    }
    return ReaderRender(reader_text="\n".join(text) + "\n", limitations_audit=audit)
