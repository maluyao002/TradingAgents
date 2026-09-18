"""Deterministic gap routing and explicit investigation-closure ledger.

Collection preserves exact input text and every origin.  It never treats keyword
matches, missing matches, or repeated wording as evidence that a gap is resolved.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, EvidenceSnapshot
from .stages import AnalysisOutput, ValuationProposal

GapCategory: TypeAlias = Literal[
    "retrieval_needed",
    "normalization_needed",
    "analyst_assumption_needed",
    "scope_limitation",
    "unclassified",
]
DecisionStatus: TypeAlias = Literal["still_open", "resolved", "disposed"]
VerifierJudgment: TypeAlias = Literal["resolved", "not_resolved"]

_CATEGORIES = {
    "retrieval_needed",
    "normalization_needed",
    "analyst_assumption_needed",
    "scope_limitation",
    "unclassified",
}


class TaskOrigin(Contract):
    occurrence_id: str = Field(min_length=1)
    origin_path: str = Field(min_length=1)
    provenance_id: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    category: GapCategory = "unclassified"
    source_kind: Literal[
        "analysis_followup",
        "analysis_gap",
        "valuation_proposal",
        "valuation_result",
        "context_query",
    ]

    @field_validator("occurrence_id", "origin_path", "provenance_id", "original_text")
    @classmethod
    def nonblank_origin_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("investigation origin values cannot be blank")
        return value


class InvestigationTask(Contract):
    id: str = Field(pattern=r"^task-[a-f0-9]{64}$")
    text: str = Field(min_length=1)
    category: GapCategory
    origins: tuple[TaskOrigin, ...] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("investigation task text cannot be blank")
        return value

    @model_validator(mode="after")
    def exact_origins(self):
        if any(origin.original_text != self.text for origin in self.origins):
            raise ValueError("consolidated task origins must retain exact task text")
        if len({origin.occurrence_id for origin in self.origins}) != len(self.origins):
            raise ValueError("investigation occurrence identifiers must be unique")
        return self


class ClosureDecision(Contract):
    task_id: str = Field(pattern=r"^task-[a-f0-9]{64}$")
    status: DecisionStatus
    evidence_ids: tuple[str, ...] = ()
    verifier_judgment: VerifierJudgment | None = None
    verifier_provenance_id: str | None = None
    disposition: str = Field(min_length=1)

    @field_validator("disposition")
    @classmethod
    def nonblank_disposition(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("closure disposition cannot be blank")
        return value

    @field_validator("verifier_provenance_id")
    @classmethod
    def nonblank_verifier_provenance(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("verifier provenance cannot be blank")
        return value

    @model_validator(mode="after")
    def explicit_closure(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("closure evidence identifiers must be unique")
        if (self.verifier_judgment is None) != (self.verifier_provenance_id is None):
            raise ValueError("verifier judgment and provenance must be supplied together")
        if self.status == "resolved":
            if not self.evidence_ids:
                raise ValueError("resolved tasks require cited evidence")
            if self.verifier_judgment != "resolved":
                raise ValueError("resolved tasks require an explicit verifier resolution")
        elif self.verifier_judgment == "resolved":
            raise ValueError("a resolved verifier judgment requires resolved task status")
        return self


class LedgerEntry(Contract):
    task_id: str
    text: str
    category: GapCategory
    origins: tuple[TaskOrigin, ...]
    status: DecisionStatus
    evidence_ids: tuple[str, ...] = ()
    verifier_judgment: VerifierJudgment | None = None
    verifier_provenance_id: str | None = None
    disposition: str


class FollowupQuestion(Contract):
    task_id: str
    question: str
    category: GapCategory


class InvestigationLedger(Contract):
    entries: tuple[LedgerEntry, ...]
    routes: dict[GapCategory, tuple[str, ...]]
    followup_questions: tuple[FollowupQuestion, ...]
    followup_total: int = Field(ge=0)
    followup_truncated: bool


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()}"


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (AnalysisOutput, ValuationProposal)):
        return value.model_dump(mode="python")
    raise TypeError(f"{label} must be a mapping or supported typed stage output")


def _items(value: object, origin_path: str) -> Sequence[object]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{origin_path} must be a sequence of gap items")
    return value


def _parse_item(item: object, origin_path: str) -> tuple[str, GapCategory, str]:
    if isinstance(item, str):
        text = item
        category: GapCategory = "unclassified"
        provenance_id = origin_path
    elif isinstance(item, Mapping):
        unknown = set(item) - {"text", "category", "provenance_id"}
        if unknown:
            raise ValueError(f"unsupported tagged gap fields at {origin_path}: {sorted(unknown)}")
        text = item.get("text")
        category = item.get("category", "unclassified")
        provenance_id = item.get("provenance_id", origin_path)
        if not isinstance(text, str) or not isinstance(category, str) or not isinstance(
            provenance_id, str
        ):
            raise TypeError(f"invalid tagged gap at {origin_path}")
        if category not in _CATEGORIES:
            raise ValueError(f"unsupported investigation category at {origin_path}")
    else:
        raise TypeError(f"gap item at {origin_path} must be a string or tagged mapping")
    if not text.strip():
        raise ValueError(f"gap text at {origin_path} cannot be blank")
    if not provenance_id.strip():
        raise ValueError(f"gap provenance at {origin_path} cannot be blank")
    return text, category, provenance_id


def _origin(
    item: object,
    origin_path: str,
    source_kind: Literal[
        "analysis_followup",
        "analysis_gap",
        "valuation_proposal",
        "valuation_result",
        "context_query",
    ],
) -> TaskOrigin:
    text, category, provenance_id = _parse_item(item, origin_path)
    return TaskOrigin(
        occurrence_id=_stable_id("occurrence", origin_path, provenance_id, text),
        origin_path=origin_path,
        provenance_id=provenance_id,
        original_text=text,
        category=category,
        source_kind=source_kind,
    )


def _context_origins(context_metadata: Mapping[str, Any] | None) -> list[TaskOrigin]:
    if context_metadata is None:
        return []
    metadata = context_metadata.get("retrieval_metadata", context_metadata)
    if not isinstance(metadata, Mapping):
        raise TypeError("context retrieval metadata must be a mapping")
    queries = _items(metadata.get("queries", ()), "context.retrieval_metadata.queries")
    origins = []
    for position, query in enumerate(queries):
        path = f"context.retrieval_metadata.queries[{position}]"
        if not isinstance(query, Mapping):
            raise TypeError(f"{path} must be a mapping")
        unresolved = query.get("unresolved")
        match_label = query.get("match_label")
        if unresolved is not None and not isinstance(unresolved, bool):
            raise TypeError(f"{path}.unresolved must be a boolean")
        if (unresolved is False and match_label == "not_found") or (
            unresolved is True and match_label == "keyword_match"
        ):
            raise ValueError(f"inconsistent context query metadata at {path}")
        if unresolved is not True and match_label != "not_found":
            continue
        supplied_text = query.get("query")
        if supplied_text is not None and not isinstance(supplied_text, str):
            raise TypeError(f"{path}.query must be a string")
        terms = query.get("terms", ())
        if isinstance(terms, (str, bytes)) or not isinstance(terms, Sequence) or any(
            not isinstance(term, str) for term in terms
        ):
            raise TypeError(f"{path}.terms must be a sequence of strings")
        text = supplied_text or "Unmatched bounded context query: " + " ".join(terms)
        if not text.strip() or text.endswith(": "):
            raise ValueError(f"unmatched context query at {path} requires text or terms")
        origins.append(
            _origin(
                {
                    "text": text,
                    "category": "retrieval_needed",
                    "provenance_id": f"context-query:{query.get('query_index', position)}",
                },
                path,
                "context_query",
            )
        )
    return origins


def _task_category(origins: Sequence[TaskOrigin]) -> GapCategory:
    explicit = {origin.category for origin in origins if origin.category != "unclassified"}
    if len(explicit) == 1:
        return next(iter(explicit))
    return "unclassified"


def collect_tasks(
    analyses: Mapping[str, Mapping[str, Any] | AnalysisOutput],
    valuation_proposal: ValuationProposal | Mapping[str, Any] | None = None,
    valuation_result: Mapping[str, Any] | None = None,
    context_metadata: Mapping[str, Any] | None = None,
) -> tuple[InvestigationTask, ...]:
    """Collect and exact-deduplicate investigation tasks without resolving them.

    Plain strings are always ``unclassified``.  A producing stage may explicitly tag
    an item with ``{"text", "category", "provenance_id"}``.  Unmatched targeted-context
    metadata is an explicit retrieval task; keyword matches never close another task.
    """

    origins: list[TaskOrigin] = []
    if any(not isinstance(stage, str) or not stage for stage in analyses):
        raise ValueError("analysis stage names must be nonblank strings")
    for stage in sorted(analyses):
        output = _as_mapping(analyses[stage], f"analyses.{stage}")
        for field, source_kind in (
            ("followup_questions", "analysis_followup"),
            ("unresolved_gaps", "analysis_gap"),
        ):
            values = _items(output.get(field, ()), f"analyses.{stage}.{field}")
            for index, item in enumerate(values):
                path = f"analyses.{stage}.{field}[{index}]"
                origins.append(_origin(item, path, source_kind))

    if valuation_proposal is not None:
        proposal = _as_mapping(valuation_proposal, "valuation_proposal")
        values = _items(
            proposal.get("unsupported_inputs", ()), "valuation_proposal.unsupported_inputs"
        )
        for index, item in enumerate(values):
            path = f"valuation_proposal.unsupported_inputs[{index}]"
            origins.append(_origin(item, path, "valuation_proposal"))

    if valuation_result is not None:
        if not isinstance(valuation_result, Mapping):
            raise TypeError("valuation_result must be a mapping")
        values = _items(valuation_result.get("limitations", ()), "valuation_result.limitations")
        for index, item in enumerate(values):
            path = f"valuation_result.limitations[{index}]"
            origins.append(_origin(item, path, "valuation_result"))

    origins.extend(_context_origins(context_metadata))
    grouped: dict[str, list[TaskOrigin]] = {}
    for origin in origins:
        grouped.setdefault(origin.original_text, []).append(origin)
    return tuple(
        InvestigationTask(
            id=_stable_id("task", text),
            text=text,
            category=_task_category(task_origins),
            origins=tuple(task_origins),
        )
        for text, task_origins in grouped.items()
    )


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


def make_ledger(
    tasks: Iterable[InvestigationTask],
    snapshot: EvidenceSnapshot,
    decisions: Iterable[ClosureDecision] = (),
    *,
    max_followup_questions: int = 20,
) -> InvestigationLedger:
    """Bind explicit closure decisions and expose bounded still-open follow-ups.

    Evidence IDs are checked against the eligible snapshot graph.  The function does
    not inspect task wording or context-match metadata when deciding closure.
    """

    if isinstance(max_followup_questions, bool) or not isinstance(max_followup_questions, int):
        raise TypeError("max_followup_questions must be an integer")
    if max_followup_questions < 0:
        raise ValueError("max_followup_questions cannot be negative")
    task_items = tuple(tasks)
    task_by_id = {task.id: task for task in task_items}
    if len(task_by_id) != len(task_items):
        raise ValueError("investigation task identifiers must be unique")
    decision_items = tuple(decisions)
    decision_by_id = {decision.task_id: decision for decision in decision_items}
    if len(decision_by_id) != len(decision_items):
        raise ValueError("each investigation task may have at most one closure decision")
    unknown_tasks = set(decision_by_id) - set(task_by_id)
    if unknown_tasks:
        raise ValueError("closure decision references an unknown investigation task")
    eligible = _eligible_evidence_ids(snapshot)
    cited = {identifier for decision in decision_items for identifier in decision.evidence_ids}
    if cited - eligible:
        raise ValueError("closure decision references unknown or ineligible evidence identifiers")

    entries = []
    for task in task_items:
        decision = decision_by_id.get(task.id)
        if decision is None:
            decision = ClosureDecision(
                task_id=task.id,
                status="still_open",
                disposition="No closure decision supplied; investigation remains open.",
            )
        entries.append(
            LedgerEntry(
                task_id=task.id,
                text=task.text,
                category=task.category,
                origins=task.origins,
                status=decision.status,
                evidence_ids=decision.evidence_ids,
                verifier_judgment=decision.verifier_judgment,
                verifier_provenance_id=decision.verifier_provenance_id,
                disposition=decision.disposition,
            )
        )

    routes = {
        category: tuple(
            entry.task_id
            for entry in entries
            if entry.category == category and entry.status == "still_open"
        )
        for category in (
            "retrieval_needed",
            "normalization_needed",
            "analyst_assumption_needed",
            "scope_limitation",
            "unclassified",
        )
    }
    followup_candidates = [
        FollowupQuestion(task_id=entry.task_id, question=entry.text, category=entry.category)
        for entry in entries
        if entry.status == "still_open" and entry.category != "scope_limitation"
    ]
    return InvestigationLedger(
        entries=tuple(entries),
        routes=routes,
        followup_questions=tuple(followup_candidates[:max_followup_questions]),
        followup_total=len(followup_candidates),
        followup_truncated=len(followup_candidates) > max_followup_questions,
    )
