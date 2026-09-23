import pytest

from scripts import research_nvda_questions as script
from tradingagents.research.research_questions import question_register
from tradingagents.research.storage import canonical_json, digest


def test_all_authored_assignments_are_retained_without_closure(monkeypatch):
    # The synthetic replacement checks index completeness, not real-source meaning.
    issues = [{"issue_id": f"issue-{n}", "text": f"synthetic {n}"} for n in range(185)]
    monkeypatch.setattr(script, "INVENTORY_SHA256", digest(issues))
    questions = script.nvda_questions(issues)
    register = question_register(issues, questions, analyst="test", source_identity="synthetic")
    assert register["question_count"] == 9
    assert register["assigned_issue_count"] == 185
    assert register["unassigned_issue_ids"] == []
    assert register["resolved_by_this_plan"] == 0
    issues[0]["text"] = "changed input"
    with pytest.raises(ValueError, match="exact reviewed inventory"):
        script.nvda_questions(issues)


def test_historical_output_is_never_overwritten(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="fresh output"):
        script.build(source, source)
    with pytest.raises(ValueError, match="fresh output"):
        script.build(source, source / "new")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="fresh output"):
        script.build(source, existing)


@pytest.mark.parametrize("changed", ["payload", "reply"])
def test_lifecycle_inputs_are_pinned_before_output_creation(tmp_path, monkeypatch, changed):
    source = tmp_path / "source"
    source.mkdir()
    payload, reply = {"research": "fixture"}, {"data": "fixture"}
    monkeypatch.setattr(script, "PAYLOAD_SHA256", digest(payload))
    monkeypatch.setattr(script, "REPLY_SHA256", digest(reply))
    (payload if changed == "payload" else reply)["changed"] = True
    (source / "factual-payload.json").write_bytes(canonical_json(payload))
    (source / "factual-reply.json").write_bytes(canonical_json(reply))
    destination = tmp_path / "new"
    with pytest.raises(ValueError, match="exact factual payload and reply"):
        script.build(source, destination)
    assert not destination.exists()
