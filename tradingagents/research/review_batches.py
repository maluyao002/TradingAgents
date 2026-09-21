"""Lossless, bounded reader-coverage work; never infer materiality from size."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

from .budget import BudgetExhausted
from .contracts import ReviewFinding
from .prompt_context import model_input_bytes
from .report_review import ReaderVerification, check_dispositions
from .storage import canonical_json

MAX_COVERAGE_ITEMS = 12
MAX_COVERAGE_BYTES = 12_000
SHARED_CONTEXT_FIELDS = ("claims", "related_records", "prior_findings", "origins")
_SHARED_CONTEXT_REF = "shared_issue_context_ref"


@dataclass(frozen=True)
class EquivalentIssueGroup:
    """Exact-context issue aliases and the untouched issues they represent."""

    group_id: str
    issue_ids: tuple[str, ...]
    context_sha256: str
    original_issues: tuple[dict, ...]


def _issue_context(issue):
    return {key: deepcopy(value) for key, value in issue.items() if key != "issue_id"}


def _validate_equivalent_group(group):
    if not group.original_issues or not group.issue_ids:
        raise ValueError("equivalent issue groups cannot be empty")
    ids = tuple(item.get("issue_id") for item in group.original_issues)
    if ids != group.issue_ids or len(ids) != len(set(ids)):
        raise ValueError("equivalent issue group lost or duplicated original identifiers")
    contexts = tuple(canonical_json(_issue_context(item)) for item in group.original_issues)
    if any(context != contexts[0] for context in contexts[1:]):
        raise ValueError("equivalent issue group contains different contexts")
    context_sha256 = sha256(contexts[0]).hexdigest()
    if group.context_sha256 != context_sha256:
        raise ValueError("equivalent issue context hash does not match")
    expected_group_id = (ids[0] if len(ids) == 1 else
                         "equivalent-" + context_sha256)
    if group.group_id != expected_group_id:
        raise ValueError("equivalent issue group identifier does not match")


def group_equivalent_issues(issues):
    """Group only byte-equivalent obligations and context, excluding ``issue_id``.

    This is deliberately not semantic deduplication. Different text, origins,
    protection flags, claims, findings, or any other context field form separate
    groups. Stable first-seen ordering is retained, as is every untouched issue.
    """
    originals = tuple(deepcopy(item) for item in issues)
    ids = tuple(item.get("issue_id") for item in originals)
    if any(type(identifier) is not str or not identifier for identifier in ids):
        raise ValueError("limitation identifiers must be nonempty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate limitation identifiers")

    grouped: dict[bytes, list[dict]] = {}
    for issue in originals:
        grouped.setdefault(canonical_json(_issue_context(issue)), []).append(issue)

    result = []
    for context, members in grouped.items():
        context_sha256 = sha256(context).hexdigest()
        result.append(EquivalentIssueGroup(
            group_id=(members[0]["issue_id"] if len(members) == 1 else
                      "equivalent-" + context_sha256),
            issue_ids=tuple(item["issue_id"] for item in members),
            context_sha256=context_sha256,
            original_issues=tuple(members),
        ))
    return tuple(result)


def compact_issue_groups(groups):
    """Build review issues with one exact context and all represented raw IDs."""
    compact = []
    seen_ids = set()
    seen_groups = set()
    for group in groups:
        _validate_equivalent_group(group)
        if group.group_id in seen_groups or seen_ids.intersection(group.issue_ids):
            raise ValueError("equivalent issue groups overlap or repeat")
        seen_groups.add(group.group_id)
        seen_ids.update(group.issue_ids)
        if len(group.issue_ids) == 1:
            compact.append(deepcopy(group.original_issues[0]))
        else:
            compact.append({
                **_issue_context(group.original_issues[0]),
                "issue_id": group.group_id,
                "equivalent_issue_ids": list(group.issue_ids),
                "equivalent_context_sha256": group.context_sha256,
            })
    return tuple(compact)


def _shared_context_id(field, value):
    fingerprint = canonical_json({"field": field, "value": value})
    return "context-" + sha256(fingerprint).hexdigest()


def compact_coverage_context(issues):
    """Reference exact repeated nested context only when the full packet shrinks.

    This does not group obligations or change IDs. It only replaces byte-identical
    values in the allowlisted context fields with explicit, field-bound references.
    The original issues remain the authority for validation and disposition checks.
    """
    original = tuple(deepcopy(item) for item in issues)
    ids = tuple(item.get("issue_id") for item in original)
    if any(type(identifier) is not str or not identifier for identifier in ids):
        raise ValueError("limitation identifiers must be nonempty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate limitation identifiers")

    compact = [deepcopy(item) for item in original]
    shared = {}
    occurrences = {}
    order = []
    for index, issue in enumerate(original):
        for field in SHARED_CONTEXT_FIELDS:
            if field not in issue:
                continue
            key = (field, canonical_json(issue[field]))
            if key not in occurrences:
                occurrences[key] = []
                order.append(key)
            occurrences[key].append(index)

    def packet():
        return {"limitation_review": deepcopy(compact),
                "shared_issue_context": deepcopy(shared)}

    current_size = len(canonical_json(packet()))
    for field, encoded in order:
        indexes = occurrences[(field, encoded)]
        if len(indexes) < 2:
            continue
        value = original[indexes[0]][field]
        reference = _shared_context_id(field, value)
        if reference in shared:
            raise ValueError("shared issue context hash collision")
        previous = [deepcopy(compact[index][field]) for index in indexes]
        shared[reference] = {"field": field, "value": deepcopy(value)}
        for index in indexes:
            compact[index][field] = {_SHARED_CONTEXT_REF: reference}
        candidate_size = len(canonical_json(packet()))
        if candidate_size < current_size:
            current_size = candidate_size
        else:
            for index, prior in zip(indexes, previous, strict=True):
                compact[index][field] = prior
            del shared[reference]
    return packet()


def expand_coverage_context(packet):
    """Losslessly decode the explicit shared-context wire representation."""
    if not isinstance(packet, dict) or set(packet) != {
            "limitation_review", "shared_issue_context"}:
        raise ValueError("invalid compact coverage context packet")
    issues = packet["limitation_review"]
    shared = packet["shared_issue_context"]
    if not isinstance(issues, (list, tuple)) or not isinstance(shared, dict):
        raise ValueError("invalid compact coverage context containers")

    validated = {}
    for reference, entry in shared.items():
        if (type(reference) is not str or not isinstance(entry, dict)
                or set(entry) != {"field", "value"}
                or entry["field"] not in SHARED_CONTEXT_FIELDS
                or reference != _shared_context_id(entry["field"], entry["value"])):
            raise ValueError("invalid shared issue context entry")
        validated[reference] = entry

    expanded = []
    used = set()
    for source in issues:
        if not isinstance(source, dict):
            raise ValueError("compact coverage issues must be objects")
        issue = deepcopy(source)
        for field in SHARED_CONTEXT_FIELDS:
            value = issue.get(field)
            if not isinstance(value, dict) or _SHARED_CONTEXT_REF not in value:
                continue
            if set(value) != {_SHARED_CONTEXT_REF}:
                raise ValueError("invalid shared issue context reference")
            reference = value[_SHARED_CONTEXT_REF]
            entry = validated.get(reference)
            if entry is None or entry["field"] != field:
                raise ValueError("unknown or cross-field shared issue context reference")
            issue[field] = deepcopy(entry["value"])
            used.add(reference)
        expanded.append(issue)
    if used != set(validated):
        raise ValueError("unused shared issue context entry")
    ids = tuple(item.get("issue_id") for item in expanded)
    if (any(type(identifier) is not str or not identifier for identifier in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("expanded limitation identifiers are invalid")
    return tuple(expanded)


def fanout_group_dispositions(review, groups):
    """Expand one exact-group disposition and finding back to every raw ID."""
    if isinstance(review, ReaderVerification):
        review = ReaderVerification.model_validate(review.model_dump(mode="json"))
    else:
        review = ReaderVerification.model_validate(review)
    groups = tuple(groups)
    compact = compact_issue_groups(groups)  # Revalidate equality and disjointness.
    expected = tuple(item["issue_id"] for item in compact)
    counts = Counter(item.issue_id for item in review.limitation_dispositions)
    if set(counts) != set(expected) or any(counts[identifier] != 1 for identifier in expected):
        raise ValueError("group review dispositions are incomplete, duplicated, or foreign")
    by_id = {item.issue_id: item for item in review.limitation_dispositions}
    dispositions = tuple(
        by_id[group.group_id].model_copy(update={"issue_id": issue_id})
        for group in groups
        for issue_id in group.issue_ids
    )
    expanded = {group.group_id: group.issue_ids for group in groups}
    findings = tuple(finding.model_copy(update={
        "affected_ids": tuple(dict.fromkeys(
            original_id
            for identifier in finding.affected_ids
            for original_id in expanded.get(identifier, (identifier,))
        )),
    }) for finding in review.findings)
    return review.model_copy(update={
        "findings": findings,
        "limitation_dispositions": dispositions,
    })


@dataclass(frozen=True)
class FinalizationCallPlan:
    """One exact provider payload in a worst-case finalization path."""

    call_id: str
    phase: str
    payload: object
    output_token_envelope: int
    timeout_seconds: float
    cache_hit: bool = False
    reader_bytes: int = 0
    role: str | None = None
    valuation_method: str | None = None


def finalization_workload(calls, *, hard_provider_spend_cap_tokens=None):
    """Measure an explicit initial/repair call path without claiming actual spend.

    ``payload`` must be the complete payload that would be serialized for the
    provider. Real model payloads require ``role`` and ``valuation_method`` so the
    exact trusted instructions and strict wire schema can also be counted. Generic
    byte payloads and non-model dictionaries retain direct serialized-byte
    accounting. Consequently factual and coverage reader bytes are counted exactly.
    ``reader_bytes`` is reporting metadata and is not added a second time. Calls
    known to be cache hits remain visible but consume no dispatch reserve or time.

    The planning estimate uses a named four-UTF-8-bytes-per-token heuristic. The
    conservative reserve instead treats every serialized input byte as one token
    and adds the full output envelope. Neither is a provider-enforced spend cap.
    """
    calls = tuple(calls)
    if len({call.call_id for call in calls}) != len(calls):
        raise ValueError("finalization call identifiers must be unique")
    if hard_provider_spend_cap_tokens is not None and (
            type(hard_provider_spend_cap_tokens) is not int
            or hard_provider_spend_cap_tokens <= 0):
        raise ValueError("hard provider spend cap must be a positive integer")

    details = []
    for call in calls:
        if type(call.call_id) is not str or not call.call_id:
            raise ValueError("finalization call identifiers must be nonempty strings")
        if type(call.phase) is not str or not call.phase:
            raise ValueError("finalization call phases must be nonempty strings")
        if type(call.output_token_envelope) is not int or call.output_token_envelope <= 0:
            raise ValueError("output token envelopes must be positive integers")
        if (isinstance(call.timeout_seconds, bool)
                or not isinstance(call.timeout_seconds, (int, float))
                or not isfinite(call.timeout_seconds) or call.timeout_seconds <= 0):
            raise ValueError("model timeouts must be positive finite numbers")
        if type(call.cache_hit) is not bool:
            raise ValueError("cache-hit flags must be booleans")
        if type(call.reader_bytes) is not int or call.reader_bytes < 0:
            raise ValueError("reader byte counts must be nonnegative integers")
        if (call.role is None) != (call.valuation_method is None):
            raise ValueError("model call plans require role and valuation method together")
        serialized = call.payload if isinstance(call.payload, bytes) else canonical_json(call.payload)
        if isinstance(call.payload, bytes):
            input_bytes = len(serialized)
        elif call.role is not None:
            input_bytes = model_input_bytes(
                call.payload,
                role=call.role,
                output_token_envelope=call.output_token_envelope,
                valuation_method=call.valuation_method,
            )
        elif isinstance(call.payload, dict) and (
                "system" in call.payload or "response_schema" in call.payload):
            raise ValueError("model payload plans require role and valuation method")
        else:
            input_bytes = len(serialized)
        if call.reader_bytes > input_bytes:
            raise ValueError("reader bytes cannot exceed the complete serialized payload")
        estimated_input_tokens = (input_bytes + 3) // 4
        details.append({
            "call_id": call.call_id,
            "phase": call.phase,
            "cache_hit": call.cache_hit,
            "serialized_input_bytes": input_bytes,
            "reader_bytes": call.reader_bytes,
            "planning_estimated_input_tokens": estimated_input_tokens,
            "output_token_envelope": call.output_token_envelope,
            "timeout_seconds": call.timeout_seconds,
            "planning_estimated_tokens": estimated_input_tokens + call.output_token_envelope,
            "conservative_reserve_tokens": input_bytes + call.output_token_envelope,
        })

    dispatched = tuple(item for item in details if not item["cache_hit"])
    phases = {}
    for item in details:
        phase = phases.setdefault(item["phase"], {
            "call_count": 0,
            "dispatch_call_count": 0,
            "cached_call_count": 0,
            "serialized_input_bytes": 0,
            "reader_bytes": 0,
            "output_token_envelope": 0,
            "planning_estimated_tokens": 0,
            "conservative_reserve_tokens": 0,
            "worst_case_timeout_seconds": 0,
        })
        phase["call_count"] += 1
        if item["cache_hit"]:
            phase["cached_call_count"] += 1
            continue
        phase["dispatch_call_count"] += 1
        for key in (
                "serialized_input_bytes", "reader_bytes", "output_token_envelope",
                "planning_estimated_tokens", "conservative_reserve_tokens"):
            phase[key] += item[key]
        phase["worst_case_timeout_seconds"] += item["timeout_seconds"]
    planning_tokens = sum(item["planning_estimated_tokens"] for item in dispatched)
    reserve_tokens = sum(item["conservative_reserve_tokens"] for item in dispatched)
    planned_seconds = sum(item["timeout_seconds"] for item in dispatched)
    cap_covers_reserve = (None if hard_provider_spend_cap_tokens is None else
                          reserve_tokens <= hard_provider_spend_cap_tokens)
    return {
        "call_count": len(details),
        "dispatch_call_count": len(dispatched),
        "cached_call_count": len(details) - len(dispatched),
        "serialized_input_bytes": sum(item["serialized_input_bytes"] for item in dispatched),
        "reader_bytes": sum(item["reader_bytes"] for item in dispatched),
        "output_token_envelope": sum(item["output_token_envelope"] for item in dispatched),
        "planning_estimated_tokens": planning_tokens,
        "conservative_reserve_tokens": reserve_tokens,
        "worst_case_timeout_seconds": planned_seconds,
        "wall_time_is_advisory": True,
        "hard_provider_spend_cap_tokens": hard_provider_spend_cap_tokens,
        "hard_provider_cap_covers_reserve": cap_covers_reserve,
        "is_hard_spend_guarantee": False,
        "phases": phases,
        "calls": tuple(details),
    }


def coverage_batches(issues, *, max_items=MAX_COVERAGE_ITEMS, max_bytes=MAX_COVERAGE_BYTES):
    """Keep each original issue intact; an oversized item fails before dispatch."""
    if type(max_items) is not int or type(max_bytes) is not int or min(max_items, max_bytes) <= 0:
        raise ValueError("review bounds must be positive integers")
    ids = [item["issue_id"] for item in issues]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate limitation identifiers")
    batches, current = [], []
    for item in issues:
        if len(canonical_json([item])) > max_bytes:
            raise BudgetExhausted("limitation_item_exceeds_review_bound")
        if current and (len(current) == max_items or len(canonical_json([*current, item])) > max_bytes):
            batches.append(tuple(current))
            current = []
        current.append(item)
    if current:
        batches.append(tuple(current))
    return tuple(batches)


@dataclass(frozen=True)
class CoverageBatchResult:
    reader_sha256: str
    issues: tuple[dict, ...]
    review: ReaderVerification


def combine_coverage(main_review, results, issues, reader, reader_sha256):
    """Every raw ID gets its own judgment against identical immutable reader bytes."""
    if sha256(reader.encode("utf-8")).hexdigest() != reader_sha256:
        raise ValueError("reader bytes do not match their declared hash")
    expected = [item["issue_id"] for item in issues]
    if len(expected) != len(set(expected)):
        raise ValueError("duplicate required limitation identifiers")
    assigned = [item["issue_id"] for batch in results for item in batch.issues]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(expected):
        raise ValueError("coverage batch assignment is incomplete or duplicated")
    if any(batch.reader_sha256 != reader_sha256 for batch in results):
        raise ValueError("coverage batch belongs to a different reader")
    reviews = [check_dispositions(batch.review, batch.issues, reader) for batch in results]
    main_review = block_reader_contradictions(main_review)
    combined = ReaderVerification(
        reviewed_report=main_review.reviewed_report and all(item.reviewed_report for item in reviews),
        supported_claim_ids=main_review.supported_claim_ids,
        contradicted_claim_ids=main_review.contradicted_claim_ids,
        findings=(*main_review.findings, *(finding for review in reviews for finding in review.findings)),
        limitation_dispositions=tuple(item for review in reviews for item in review.limitation_dispositions),
    )
    # A batch cannot silently adjudicate claims without source context.
    if any(review.supported_claim_ids or review.contradicted_claim_ids for review in reviews):
        raise ValueError("coverage-only batch supplied factual claim decisions")
    return check_dispositions(combined, issues, reader)


def block_reader_contradictions(review):
    """An explicit contradictory claim decision is never neutralized by empty findings."""
    if not review.contradicted_claim_ids:
        return review
    if any(item.code == "reader_contradicted_claims"
           and item.severity == "critical"
           and item.affected_ids == review.contradicted_claim_ids for item in review.findings):
        return review
    finding = ReviewFinding(
        code="reader_contradicted_claims", severity="critical", category="research",
        message="Final reader verification contradicts claims: " + ", ".join(review.contradicted_claim_ids),
        affected_ids=review.contradicted_claim_ids,
    )
    return review.model_copy(update={"findings": (*review.findings, finding)})


def finalization_allowance(issues, *, call_timeout_seconds, reader_bytes=24_000,
                           context_bytes=48_000, language_count=1):
    """Conservative planning signal for optional work, not a spend guarantee.

    Include two complete passes (initial plus one repair), with an editor and a
    global factual review in each. Actual prompts still require normal admission;
    a future reader can be larger than this declared planning assumption.
    """
    batches = coverage_batches(issues)
    if min(context_bytes, reader_bytes, language_count, call_timeout_seconds) <= 0:
        raise ValueError("finalization planning inputs must be positive")
    coverage = sum(reader_bytes + len(canonical_json(items)) + 10_096 for items in batches)
    # Still outstanding before editing: challenge reconciliation and claim review.
    pre_editor = 2 * (context_bytes + 16_000)
    editor = context_bytes + len(canonical_json(issues)) + 16_000
    factual_review = context_bytes + reader_bytes + 16_000
    return {
        "coverage_batches_per_pass": len(batches),
        "reader_bytes_planning_assumption": reader_bytes,
        "language_count": language_count,
        "estimated_tokens": pre_editor + 2 * language_count * (coverage + editor + factual_review),
        "planned_call_seconds": (2 + 2 * language_count * (len(batches) + 2)) * call_timeout_seconds,
        "is_hard_spend_guarantee": False,
    }
