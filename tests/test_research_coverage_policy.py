from copy import deepcopy

import pytest
from pydantic import ValidationError

from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.coverage_policy import (
    LEGACY_COVERAGE_POLICY,
    PACKED_COVERAGE_POLICY,
    coverage_batches_for_policy,
    coverage_output_envelope,
    packed_issue_context,
)
from tradingagents.research.report_review import ReaderVerification
from tradingagents.research.review_batches import (
    MAX_COVERAGE_BYTES,
    compact_issue_groups,
    coverage_batches,
    expand_coverage_context,
    fanout_group_dispositions,
    finalization_allowance,
    group_equivalent_issues,
)
from tradingagents.research.storage import canonical_json


def _issue(identifier, *, text="Exact obligation", claims=None, origins=None, protected=False):
    return {
        "issue_id": identifier,
        "text": text,
        "claims": deepcopy(claims if claims is not None else [{"id": "claim-1", "value": "same"}]),
        "origins": deepcopy(origins if origins is not None else [{"origin_id": "source", "retirable": False}]),
        "resolution_protected": protected,
    }


def _request_data(**overrides):
    data = {
        "ticker": "AMD",
        "cutoff": "2026-09-17T12:00:00+00:00",
        "backend": "api",
        "output_dir": "research-output",
    }
    data.update(overrides)
    return data


def test_contract_defaults_to_legacy_and_packed_is_explicit_bounded_opt_in():
    assert ResearchRequest.model_validate(_request_data()).coverage_batch_policy == LEGACY_COVERAGE_POLICY
    request = ResearchRequest.model_validate(_request_data(
        quality_revision="evidence-led-bounded", coverage_batch_policy=PACKED_COVERAGE_POLICY,
    ))
    assert request.coverage_batch_policy == PACKED_COVERAGE_POLICY
    with pytest.raises(ValidationError, match="packed coverage requires"):
        ResearchRequest.model_validate(_request_data(coverage_batch_policy=PACKED_COVERAGE_POLICY))


def test_legacy_policy_is_exactly_the_existing_batcher_including_oversize_failure():
    issues = tuple(_issue(f"issue-{index}", text=f"Obligation {index}") for index in range(13))
    assert coverage_batches_for_policy(issues, LEGACY_COVERAGE_POLICY) == coverage_batches(issues)

    oversize = (_issue("oversize", text="é" * 8_000),)
    with pytest.raises(BudgetExhausted, match="review_bound"):
        coverage_batches(oversize)
    with pytest.raises(BudgetExhausted, match="review_bound"):
        coverage_batches_for_policy(oversize, LEGACY_COVERAGE_POLICY)


def test_packed_batches_cap_raw_fanout_without_mutating():
    issues = [_issue(f"issue-{index}", text=f"Obligation {index}") for index in range(25)]
    original = deepcopy(issues)

    batches = coverage_batches_for_policy(issues, PACKED_COVERAGE_POLICY)

    assert [item for batch in batches for item in batch] == original
    assert [item["issue_id"] for batch in batches for item in batch] == [item["issue_id"] for item in issues]
    assert all(returned is supplied for returned, supplied in zip(
        (item for batch in batches for item in batch), issues, strict=True
    ))
    assert issues == original
    assert max(map(len, batches)) == 24
    assert len(batches) == 2


def test_packed_batches_cap_the_actual_packed_issue_packet_at_12_kb():
    issues = [_issue(f"issue-{index}", text=("x" * 3_100) + str(index)) for index in range(8)]

    batches = coverage_batches_for_policy(issues, PACKED_COVERAGE_POLICY)

    assert len(batches) > 1
    assert all(len(canonical_json(packed_issue_context(batch))) <= MAX_COVERAGE_BYTES for batch in batches)


def test_packed_context_round_trips_shared_context_and_fans_exact_groups_out():
    shared_claims = [{"id": "claim-1", "value": "x" * 600}]
    shared_origins = [{"origin_id": "filing", "detail": "y" * 600, "retirable": False}]
    issues = [
        _issue("raw-b", claims=shared_claims, origins=shared_origins),
        _issue("raw-a", claims=shared_claims, origins=shared_origins),
        _issue("raw-c", text="Different obligation", claims=shared_claims, origins=shared_origins),
    ]
    groups = group_equivalent_issues(issues)
    packet = packed_issue_context(issues)

    assert expand_coverage_context(packet) == compact_issue_groups(groups)
    assert packet["shared_issue_context"]
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": group.group_id,
        "decision": "audit_only_operational",
        "rationale": "Synthetic exact-group decision.",
    } for group in groups])
    expanded = fanout_group_dispositions(review, groups)
    assert tuple(item.issue_id for item in expanded.limitation_dispositions) == ("raw-b", "raw-a", "raw-c")


@pytest.mark.parametrize(
    "issues, message",
    [
        ([_issue("same"), _issue("same")], "duplicate limitation identifiers"),
        ([_issue("")], "nonempty strings"),
    ],
)
def test_packed_policy_rejects_duplicate_or_empty_raw_identifiers(issues, message):
    with pytest.raises(ValueError, match=message):
        coverage_batches_for_policy(issues, PACKED_COVERAGE_POLICY)


@pytest.mark.parametrize("policy", ["", "unknown", None])
def test_unknown_policy_including_empty_is_rejected_even_without_issues(policy):
    with pytest.raises(ValueError, match="unknown coverage batching policy"):
        coverage_batches_for_policy((), policy)
    with pytest.raises(ValueError, match="unknown coverage batching policy"):
        coverage_output_envelope(policy)


def test_packed_policy_rejects_an_overlarge_single_raw_issue():
    with pytest.raises(BudgetExhausted, match="review_bound"):
        coverage_batches_for_policy((_issue("oversize", text="é" * 8_000),), PACKED_COVERAGE_POLICY)


def test_exact_context_differences_in_claims_origins_or_protection_never_merge():
    issues = [
        _issue("exact-1", protected=True),
        _issue("exact-2", protected=True),
        _issue("different-claims", claims=[{"id": "claim-2", "value": "different"}], protected=True),
        _issue("different-origins", origins=[{"origin_id": "other", "retirable": False}], protected=True),
        _issue("different-protection", protected=False),
    ]

    groups = group_equivalent_issues(issues)

    assert [group.issue_ids for group in groups] == [
        ("exact-1", "exact-2"),
        ("different-claims",),
        ("different-origins",),
        ("different-protection",),
    ]


def test_coverage_output_envelopes_are_policy_specific():
    assert coverage_output_envelope(LEGACY_COVERAGE_POLICY) == 6_000
    assert coverage_output_envelope(PACKED_COVERAGE_POLICY) == 12_000


def test_optional_cycle_allowance_uses_packed_batches_and_scaled_output():
    issues = [_issue(f"issue-{i}", text=f"Obligation {i}") for i in range(30)]
    batches = coverage_batches_for_policy(issues, PACKED_COVERAGE_POLICY)
    plan = finalization_allowance(issues, call_timeout_seconds=600,
                                  coverage_batch_policy=PACKED_COVERAGE_POLICY)
    coverage = sum(24_000 + len(canonical_json(packed_issue_context(b))) + 4_096 + 12_000
                   for b in batches)
    pre_editor = 2 * (48_000 + 16_000)
    editor = 48_000 + len(canonical_json(issues)) + 16_000
    factual = 48_000 + 24_000 + 16_000
    assert plan["estimated_tokens"] == pre_editor + 2 * (coverage + editor + factual)
    assert plan["coverage_batches_per_pass"] == len(batches)
    assert plan["planned_call_seconds"] == (2 + 2 * (len(batches) + 2)) * 600
    assert plan["is_hard_spend_guarantee"] is False
