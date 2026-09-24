"""v5-only pinned coverage calls and full-boundary delta bounds."""

from copy import deepcopy

import pytest

from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.coverage_policy import coverage_batches_for_policy, packed_issue_context
from tradingagents.research.review_batches import FinalizationCallPlan, finalization_workload
from tradingagents.research.revision_correction_context import CORRECTION_CONTEXT_FIELD
from tradingagents.research.revision_coverage_schedule import (
    coverage_delta_admission,
    decorated_packet_bytes,
    pinned_coverage_slots,
    project_pinned_coverage,
    reserve_global_coverage_delta,
)


def _issue(identifier, text="material issue"):
    return {"issue_id": identifier, "text": text,
            "reader_coverage_required": True, "resolution_protected": True}


def _row(index, *, base=100, actual=100, base_prompt=100, actual_prompt=100):
    return {"source_slot_index": index, "batch_index": index,
            "base_input_bytes": base, "actual_input_bytes": actual,
            "base_prompt_bytes": base_prompt, "actual_prompt_bytes": actual_prompt}


def test_valid_correction_that_would_rebatch_stays_in_one_pinned_slot():
    base = [_issue("a", "S" * 5000), _issue("b", "S" * 5000)]
    slots = pinned_coverage_slots(coverage_batches_for_policy(base, "packed-24"))
    assert len(slots) == 1
    assert len(packed_issue_context(slots[0])["limitation_review"]) == 1
    decorated = deepcopy(base)
    decorated[0][CORRECTION_CONTEXT_FIELD] = [{"accepted": "X" * 6500}]
    assert len(coverage_batches_for_policy(decorated, "packed-24")) == 2
    projected = project_pinned_coverage(slots, decorated)
    assert len(projected) == 1 and projected[0][0] == 0
    assert [item["issue_id"] for item in projected[0][1]] == ["a", "b"]
    assert len(packed_issue_context(projected[0][1])["limitation_review"]) == 2
    assert decorated_packet_bytes(projected[0][1]) > 12_000


@pytest.mark.parametrize("rows,accepted", [
    ([_row(0, actual=40_100), _row(1, actual=40_100)], False),
    ([_row(0, actual=40_100), _row(1, actual=80, base=100)], True),
    ([_row(0, actual=64_100)], True),
    ([_row(0, actual=64_101)], False),
    ([_row(0, actual_prompt=64_101)], False),
])
def test_positive_complete_byte_growth_is_global_and_never_netted(rows, accepted):
    if accepted:
        result = coverage_delta_admission(rows)
        assert result["positive_input_delta_bytes"] <= 64_000
        assert result["positive_prompt_delta_bytes"] <= 64_000
    else:
        with pytest.raises(BudgetExhausted, match="v5_coverage_complete_wire_delta_limit"):
            coverage_delta_admission(rows)


def test_pruned_slot_keeps_original_identity_and_rejects_changed_atoms():
    first, second = _issue("first"), _issue("second")
    slots = pinned_coverage_slots(((first,), (second,)))
    projected = project_pinned_coverage(slots, (second,))
    assert projected == ((1, (second,)),)
    with pytest.raises(ValueError, match="changed a pinned"):
        project_pinned_coverage(slots, (_issue("second", "changed"),))
    with pytest.raises(ValueError, match="new coverage issue"):
        project_pinned_coverage(slots, (_issue("third"),))
    with pytest.raises(ValueError, match="repeats"):
        project_pinned_coverage(slots, (second, second))


def test_global_allowance_recomputes_hard_cap_without_claiming_measured_bytes():
    workload = finalization_workload((
        FinalizationCallPlan("coverage-0", "coverage", b"x", 1, 10),
    ), hard_provider_spend_cap_tokens=1_000)
    assert workload["hard_provider_cap_covers_reserve"] is True
    bounded = reserve_global_coverage_delta(workload, has_slots=True)
    assert bounded["base_conservative_reserve_tokens"] == 2
    assert bounded["conservative_reserve_tokens"] == 64_002
    assert bounded["hard_provider_cap_covers_reserve"] is False
    assert bounded["serialized_input_bytes"] == 1
