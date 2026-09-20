from copy import deepcopy

import pytest

from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.report_review import ReaderVerification
from tradingagents.research.review_batches import (
    FinalizationCallPlan,
    compact_coverage_context,
    compact_issue_groups,
    coverage_batches,
    expand_coverage_context,
    fanout_group_dispositions,
    finalization_workload,
    group_equivalent_issues,
)
from tradingagents.research.storage import canonical_json


def _issue(identifier, *, text="Exact obligation", protected=False, origins=None):
    return {
        "issue_id": identifier,
        "text": text,
        "claims": [{"id": "claim-1", "value": "same exact context"}],
        "missing_claim_ids": [],
        "prior_findings": [],
        "origins": deepcopy(origins or []),
        "resolution_protected": protected,
    }


def _group_review(groups):
    return ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": group.group_id,
        "decision": "audit_only_operational",
        "rationale": "The exact shared obligation is operational for this fixture.",
    } for group in groups])


def test_exact_grouping_and_fanout_preserve_every_original_id_and_context():
    issues = [_issue("raw-b"), _issue("raw-a"), _issue("raw-c", text="Other obligation")]
    expected = deepcopy(issues)

    groups = group_equivalent_issues(issues)
    issues[0]["claims"][0]["value"] = "caller mutated after grouping"

    assert [group.issue_ids for group in groups] == [("raw-b", "raw-a"), ("raw-c",)]
    assert [item for group in groups for item in group.original_issues] == expected
    compact = compact_issue_groups(groups)
    assert compact[0]["equivalent_issue_ids"] == ["raw-b", "raw-a"]
    assert compact[0]["claims"] == expected[0]["claims"]
    assert compact[1] == expected[2]  # Singleton groups add no wire overhead.

    review_payload = _group_review(groups).model_dump(mode="json")
    review_payload["findings"] = [{
        "code": "shared_warning",
        "severity": "warning",
        "message": "The group remains visible for review.",
        "affected_ids": (groups[0].group_id,),
    }]
    review = ReaderVerification.model_validate(review_payload)
    expanded = fanout_group_dispositions(review, groups)
    assert tuple(item.issue_id for item in expanded.limitation_dispositions) == (
        "raw-b", "raw-a", "raw-c",
    )
    assert expanded.findings[0].affected_ids == ("raw-b", "raw-a")


def test_grouping_never_infers_equivalence_or_merges_protected_context_differences():
    protected_origin = [{"origin_id": "authored", "retirable": False}]
    retirable_origin = [{"origin_id": "review-status", "retirable": True}]
    issues = [
        _issue("exact-1", origins=protected_origin, protected=True),
        _issue("exact-2", origins=protected_origin, protected=True),
        _issue("different-protection", origins=protected_origin, protected=False),
        _issue("different-origin", origins=retirable_origin, protected=True),
        _issue("similar-prose", text="Exact obligation.", origins=protected_origin,
               protected=True),
    ]

    groups = group_equivalent_issues(issues)

    assert [group.issue_ids for group in groups] == [
        ("exact-1", "exact-2"),
        ("different-protection",),
        ("different-origin",),
        ("similar-prose",),
    ]
    assert all(group.original_issues[0]["resolution_protected"]
               for group in (groups[0], groups[2], groups[3]))


def test_compact_groups_retain_ids_and_obey_the_existing_serialized_payload_bound():
    issues = [_issue(f"raw-{index}") for index in range(40)]
    groups = group_equivalent_issues(issues)
    compact = compact_issue_groups(groups)
    exact_bytes = len(canonical_json([compact[0]]))

    assert len(groups) == len(compact) == 1
    assert compact[0]["equivalent_issue_ids"] == [item["issue_id"] for item in issues]
    assert coverage_batches(compact, max_items=1, max_bytes=exact_bytes) == (compact,)
    with pytest.raises(BudgetExhausted, match="review_bound"):
        coverage_batches(compact, max_items=1, max_bytes=exact_bytes - 1)


def test_repeated_nested_context_is_referenced_only_exactly_and_decodes_losslessly():
    repeated = {
        "claims": [{"id": "claim-1", "text": "x" * 400}],
        "related_records": [{"id": "record-1", "text": "y" * 400}],
        "prior_findings": [{"code": "finding", "message": "z" * 400}],
        "origins": [{"origin_id": "origin", "detail": "o" * 400,
                     "retirable": False}],
    }
    issues = [{"issue_id": f"raw-{index}", "text": f"Obligation {index}",
               **deepcopy(repeated)} for index in range(4)]
    baseline = {"limitation_review": issues, "shared_issue_context": {}}

    compact = compact_coverage_context(issues)

    assert expand_coverage_context(compact) == tuple(issues)
    assert len(compact["shared_issue_context"]) == 4
    assert len(canonical_json(compact)) < len(canonical_json(baseline))
    assert [item["issue_id"] for item in compact["limitation_review"]] == [
        item["issue_id"] for item in issues]
    assert all(set(item["claims"]) == {"shared_issue_context_ref"}
               for item in compact["limitation_review"])


def test_context_compaction_keeps_small_or_different_values_inline():
    issues = [
        _issue("raw-1"),
        _issue("raw-2"),
        _issue("raw-3", origins=[{"origin_id": "protected", "retirable": False}]),
    ]
    issues[1]["claims"] = [{"id": "claim-1", "value": "different exact context"}]
    baseline = {"limitation_review": issues, "shared_issue_context": {}}

    compact = compact_coverage_context(issues)

    assert compact == baseline
    assert expand_coverage_context(compact) == tuple(issues)


@pytest.mark.parametrize("mutation", ["unknown", "cross_field", "hash", "unused"])
def test_context_decoder_rejects_unbound_or_tampered_references(mutation):
    issues = [{"issue_id": f"raw-{index}", "text": str(index),
               "claims": [{"id": "claim", "text": "x" * 500}]}
              for index in range(3)]
    compact = compact_coverage_context(issues)
    reference = next(iter(compact["shared_issue_context"]))
    if mutation == "unknown":
        compact["limitation_review"][0]["claims"]["shared_issue_context_ref"] = "unknown"
    elif mutation == "cross_field":
        compact["limitation_review"][0]["origins"] = \
            compact["limitation_review"][0].pop("claims")
    elif mutation == "hash":
        compact["shared_issue_context"][reference]["value"].append({"tampered": True})
    else:
        compact["limitation_review"] = []

    with pytest.raises(ValueError, match="shared issue context"):
        expand_coverage_context(compact)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign"])
def test_fanout_rejects_incomplete_duplicate_or_foreign_group_decisions(mutation):
    groups = group_equivalent_issues([_issue("raw-1"), _issue("raw-2")])
    payload = _group_review(groups).model_dump(mode="json")
    if mutation == "missing":
        payload["limitation_dispositions"] = []
    elif mutation == "duplicate":
        payload["limitation_dispositions"].append(
            deepcopy(payload["limitation_dispositions"][0]))
    else:
        payload["limitation_dispositions"][0]["issue_id"] = "foreign"

    with pytest.raises(ValueError, match="incomplete, duplicated, or foreign"):
        fanout_group_dispositions(ReaderVerification.model_validate(payload), groups)


def test_workload_counts_full_repair_reverification_and_exact_payload_bytes():
    calls = (
        FinalizationCallPlan("editor", "first_pass", b"e" * 40, 16, 10),
        FinalizationCallPlan("factual", "first_pass", b"f" * 80, 16, 20,
                             reader_bytes=30),
        FinalizationCallPlan("coverage-0", "first_pass", b"c" * 60, 6, 30,
                             reader_bytes=30),
        FinalizationCallPlan("repair", "repair", b"r" * 100, 16, 40,
                             reader_bytes=45),
        FinalizationCallPlan("refactual", "reverification", b"v" * 120, 16, 50,
                             reader_bytes=45),
        FinalizationCallPlan("recoverage-0", "reverification", b"w" * 90, 6, 60,
                             reader_bytes=45),
    )

    plan = finalization_workload(calls)

    assert plan["serialized_input_bytes"] == 490
    assert plan["reader_bytes"] == 195
    assert plan["output_token_envelope"] == 76
    assert plan["planning_estimated_tokens"] == sum(
        (len(call.payload) + 3) // 4 + call.output_token_envelope for call in calls)
    assert plan["conservative_reserve_tokens"] == 490 + 76
    assert plan["phases"]["first_pass"]["conservative_reserve_tokens"] == 218
    assert plan["phases"]["repair"]["conservative_reserve_tokens"] == 116
    assert plan["phases"]["reverification"]["conservative_reserve_tokens"] == 232
    assert plan["worst_case_timeout_seconds"] == 210
    assert plan["wall_time_is_advisory"] is True
    assert plan["is_hard_spend_guarantee"] is False


def test_cached_payload_cost_is_reported_but_skipped_and_provider_cap_stays_distinct():
    cached = FinalizationCallPlan(
        "coverage-cache", "first_pass", {"rendered_reader": "é" * 10}, 6, 300,
        cache_hit=True, reader_bytes=20,
    )
    live = FinalizationCallPlan(
        "coverage-live", "first_pass", {"rendered_reader": "reader"}, 6, 120,
        reader_bytes=6,
    )
    live_bytes = len(canonical_json(live.payload))

    plan = finalization_workload((cached, live), hard_provider_spend_cap_tokens=10_000)

    assert plan["call_count"] == 2
    assert plan["dispatch_call_count"] == plan["cached_call_count"] == 1
    assert plan["serialized_input_bytes"] == live_bytes
    assert plan["reader_bytes"] == 6
    assert plan["conservative_reserve_tokens"] == live_bytes + 6
    assert plan["worst_case_timeout_seconds"] == 120
    assert plan["hard_provider_spend_cap_tokens"] == 10_000
    assert plan["hard_provider_cap_covers_reserve"] is True
    assert plan["is_hard_spend_guarantee"] is False
    cached_detail = next(item for item in plan["calls"] if item["cache_hit"])
    assert cached_detail["serialized_input_bytes"] == len(canonical_json(cached.payload))
