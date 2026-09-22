"""Artifact-level checks for the offline NVDA cash-flow builder."""

from hashlib import sha256
from pathlib import Path

import pytest

from scripts.research_nvda_cashflow_case import _require_unchanged_source, prepare
from tradingagents.research.storage import read_bytes, read_json

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "reports/NVDA_VALIDATION_20260921/run_1"
)


def test_source_recheck_detects_post_capture_change_before_publication(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    names = ("evidence.json", "financial_case.json", "operating_scenario_context.json")
    for name in names:
        (source / name).write_bytes((name + "-captured").encode())
    captured = {name: read_bytes(source / name) for name in names}

    _require_unchanged_source(source, captured)
    (source / "operating_scenario_context.json").write_bytes(b"changed-after-capture")
    with pytest.raises(ValueError, match="source artifact changed during preparation"):
        _require_unchanged_source(source, captured)


def test_prepare_creates_fresh_hash_bound_unreviewed_artifact_without_source_mutation(tmp_path):
    if not SOURCE.is_dir():
        pytest.skip("frozen NVDA validation packet is unavailable")
    before = {path.name: path.read_bytes() for path in SOURCE.iterdir() if path.is_file()}
    output = tmp_path / "nvda_cashflow_case"
    status = prepare(SOURCE, output)

    assert status["status"] == "conditional_cash_flow_bridge_created"
    assert not status["reviewed"]
    assert status["model_calls"] == status["network_calls"] == 0
    assert before == {path.name: path.read_bytes() for path in SOURCE.iterdir() if path.is_file()}
    manifest = read_json(output / "manifest.json")
    assert all(
        sha256((output / name).read_bytes()).hexdigest() == expected
        for name, expected in manifest["artifact_hashes"].items()
    )
    context = read_json(output / "cashflow_bridge_context.json")
    assert context["context_kind"] == "cash_flow_bridge_audit_only"
    assert "scenarios" not in context
    assert not (output / "cashflow_bridge_calculated_values.json").exists()
    summary = (output / "summary.md").read_text()
    assert "FY2027 bridge cash flow" in summary
    assert "not independently reviewed" in summary
    assert "160.407" in summary
    assert read_json(output / "provenance.json")["independent_review"] is None
    with pytest.raises(ValueError, match="new directory"):
        prepare(SOURCE, output)
