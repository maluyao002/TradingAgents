import ast
import json
from pathlib import Path

import pytest

from tradingagents.research.budget import BudgetExhausted, BudgetTracker
from tradingagents.research.contracts import Budget, Usage
from tradingagents.research.storage import CheckpointStore, atomic_write, canonical_json, read_json


def test_core_imports_have_no_application_or_agent_dependency():
    for path in Path("tradingagents/research").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("cli", "tradingagents.agents", "tradingagents.graph",
                                            "tradingagents.weekly", "tradingagents.reporting")), path


def test_checkpoint_resume_identity_and_input_binding(tmp_path):
    store = CheckpointStore(tmp_path, "first")
    with store.lock():
        store.save_stage("plan", {"evidence": 1}, {"questions": []})
    with store.lock():
        assert store.load_stage("plan", {"evidence": 1}) == {"questions": []}
        assert store.load_stage("plan", {"evidence": 2}) is None
    with pytest.raises(ValueError, match="incompatible"), CheckpointStore(tmp_path, "different").lock():
        pass


def test_checkpoint_rejects_tampering_and_path_escape(tmp_path):
    store = CheckpointStore(tmp_path, "same")
    with store.lock():
        store.save_stage("plan", {}, {"answer": 1})
        with pytest.raises(ValueError, match="invalid checkpoint stage"):
            store.save_stage("../../escape", {}, {})
    path = tmp_path / "stages/plan.json"
    record = json.loads(path.read_text())
    record["output"] = {"answer": 2}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid research stage"):
        store.load_stage("plan", {})


def test_lock_and_no_overwrite(tmp_path):
    with (CheckpointStore(tmp_path, "one").lock(),
          pytest.raises(ValueError, match="in use"),
          CheckpointStore(tmp_path, "one").lock()):
        pass
    other = tmp_path / "other"
    other.mkdir()
    (other / "user.md").write_text("keep")
    with pytest.raises(ValueError, match="not empty"), CheckpointStore(other, "one").lock():
        pass
    assert (other / "user.md").read_text() == "keep"


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}'])
def test_strict_json(text, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_json(path)


def test_atomic_canonical_json(tmp_path):
    path = tmp_path / "value.json"
    atomic_write(path, canonical_json({"zh": "中文", "a": 2}))
    assert read_json(path) == {"zh": "中文", "a": 2}
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_budget_reserves_and_no_double_count():
    now = [0.0]
    tracker = BudgetTracker(Budget(), clock=lambda: now[0])
    tracker.record(Usage(input_tokens=1_199_000, output_tokens=1000,
                         cached_input_tokens=100, reasoning_output_tokens=50))
    assert tracker.usage.total_tokens == 1_200_000
    with pytest.raises(BudgetExhausted):
        tracker.admit()
    assert tracker.admit(finalization=True) == 300
    now[0] = 5400
    with pytest.raises(BudgetExhausted):
        tracker.admit(finalization=True)


def test_missing_telemetry_blocks_further_calls():
    tracker = BudgetTracker(Budget())
    tracker.record(Usage(complete=False))
    with pytest.raises(BudgetExhausted, match="usage_incomplete"):
        tracker.admit(finalization=True)
