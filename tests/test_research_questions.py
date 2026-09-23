from copy import deepcopy

import pytest

from tradingagents.research.research_questions import question_register, render_question_register
from tradingagents.research.storage import digest


def question(**kwargs):
    return dict(id="cash", priority=1, question="Does profit convert to cash?",
                kinds=["unfinished_analysis"], owner="financial analyst",
                affected_conclusions=["conditional cash generation"], required_work=["Reconcile WC"],
                completion_criteria=["Source-linked reconciliation independently checked"],
                bounded_search="Latest two filings", fallback="Conditional range, not funding clearance",
                issue_ids=["a"], **kwargs)


def test_lossless_many_to_many_register_is_not_resolution():
    issues = [{"issue_id": "a", "text": "Mixed WC", "claims": [{"text": "context"}],
               "resolution_protected": True}, {"issue_id": "b", "text": "Audit question"}]
    before = deepcopy(issues)
    second = {**question(), "id": "funding"}
    result = question_register(issues, [question(), second], analyst="analyst", source_identity="frozen")
    assert issues == before
    assert result["inventory"] == before
    assert result["inventory_sha256"] == digest(before)
    assert result["resolved_by_this_plan"] == 0
    assert result["assigned_issue_count"] == 1
    assert result["unassigned_issue_ids"] == ["b"]
    issues[0]["claims"][0]["text"] = "mutation"
    assert result["inventory"] == before
    assert "Unassigned" in render_question_register(result)


@pytest.mark.parametrize("mutation", ["foreign", "duplicate_question", "duplicate_issue", "blank"])
def test_register_rejects_invalid_bindings(mutation):
    issues, questions = [{"issue_id": "a", "text": "x"}], [question()]
    if mutation == "foreign":
        questions[0]["issue_ids"] = ["absent"]
    elif mutation == "duplicate_question":
        questions.append(question())
    elif mutation == "duplicate_issue":
        issues.append(deepcopy(issues[0]))
    else:
        questions[0]["completion_criteria"] = [" "]
    with pytest.raises(ValueError):
        question_register(issues, questions, analyst="analyst", source_identity="frozen")
