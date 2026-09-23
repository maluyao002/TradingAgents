"""Synthetic replay regressions for the cash-flow review attachment adapter."""

from __future__ import annotations

from hashlib import sha256

import pytest

from scripts import research_cashflow_review as adapter
from scripts.research_cashflow_review import attach_cashflow_review
from tests.test_research_case_engine import case_setup
from tests.test_research_cashflow_bridge import bridge_setup
from tradingagents.research.case_context import load_case_context
from tradingagents.research.cashflow_bridge import (
    CashFlowBridgeReview,
    cashflow_bridge_package_sha256,
    evaluate_cashflow_bridge,
)
from tradingagents.research.storage import canonical_json, read_json


def _write_bundle(directory, blobs):
    directory.mkdir()
    for name, content in blobs.items():
        (directory / name).write_bytes(content)
    (directory / "manifest.json").write_bytes(canonical_json({
        "artifact_hashes": {name: sha256(content).hexdigest() for name, content in blobs.items()},
    }))


def _refresh_manifest(directory, name):
    manifest = read_json(directory / "manifest.json")
    manifest["artifact_hashes"][name] = sha256((directory / name).read_bytes()).hexdigest()
    (directory / "manifest.json").write_bytes(canonical_json(manifest))


def _setup(tmp_path):
    snapshot, case, operating, bridge = bridge_setup(tmp_path / "bridge", reviewed=False)
    request, _ = case_setup(tmp_path / "request")
    envelope = {"case": case, "operating_scenarios": operating}
    request.evidence_path.write_bytes(canonical_json(snapshot))
    request.financial_case_path.write_bytes(canonical_json(envelope))
    context = load_case_context(canonical_json(envelope), request, snapshot)
    operating_blobs = {
        "request.json": canonical_json(request),
        "evidence.json": canonical_json(snapshot),
        "case_input.json": canonical_json(envelope),
        "financial_case.json": canonical_json(case),
        "operating_scenario_package.json": canonical_json(operating),
        "operating_scenario_context.json": canonical_json(context.operating_scenarios.model_context),
        **context.artifacts,
    }
    # Context artifacts repeat the standalone files above with the same bytes.
    _write_bundle(tmp_path / "operating_source", operating_blobs)
    result = evaluate_cashflow_bridge(bridge, case, snapshot, context.operating_scenarios)
    source_names = (
        "evidence.json", "financial_case.json", "operating_scenario_package.json",
        "operating_scenario_context.json",
    )
    draft_blobs = {
        **result.artifacts,
        "provenance.json": canonical_json({
            "source_artifact_sha256": {
                name: sha256(operating_blobs[name]).hexdigest() for name in source_names
            },
        }),
    }
    _write_bundle(tmp_path / "cashflow_draft", draft_blobs)
    review = CashFlowBridgeReview(
        reviewer_id="independent_cashflow_reviewer",
        reviewed_at="2026-09-20T00:00:00Z",
        package_sha256=cashflow_bridge_package_sha256(bridge),
        case_sha256=bridge.case_sha256,
        evidence_sha256=bridge.evidence_sha256,
        operating_package_sha256=bridge.operating_package_sha256,
        decision="conditional_cash_flow_bridge",
        limitations=("Synthetic independent review is not economic approval.",),
    )
    review_path = tmp_path / "review.json"
    review_path.write_bytes(canonical_json(review))
    return tmp_path / "operating_source", tmp_path / "cashflow_draft", review_path


def test_attach_cashflow_review_replays_only_and_regenerates_context(tmp_path):
    operating_source, cashflow_draft, review_path = _setup(tmp_path)
    output = tmp_path / "output"
    original = {path: path.read_bytes() for directory in (operating_source, cashflow_draft)
                for path in directory.iterdir()}

    readiness = attach_cashflow_review(operating_source, cashflow_draft, review_path, output)

    assert readiness["cashflow_reviewed"]
    assert readiness["financial_case_still_draft"]
    assert readiness["output_scope_blocked"]
    assert readiness["model_calls"] == 0 and not readiness["live_authorized"]
    context = read_json(output / "case_context.json")
    assert context["cashflow_bridge"]["reviewed"]
    assert context["reviewed"] is False
    assert read_json(output / "readiness.json")["status"] == (
        "cashflow_reviewed_conditional_financial_case_still_draft"
    )
    assert (operating_source / "manifest.json").is_file()
    assert (cashflow_draft / "cashflow_bridge_package.json").is_file()
    assert all(path.read_bytes() == raw for path, raw in original.items())
    for name, expected in read_json(output / "manifest.json")["artifact_hashes"].items():
        assert sha256((output / name).read_bytes()).hexdigest() == expected
    request = read_json(output / "request.json")
    assert request["prior_dossier_path"] is None and request["dossier_dir"] is None


@pytest.mark.parametrize(
    "mutation",
    ["review_hash", "draft_review", "nested_source", "existing_bridge", "draft_provenance", "output_symlink"],
)
def test_attach_cashflow_review_rejects_unbound_or_nonimmutable_inputs(tmp_path, mutation):
    operating_source, cashflow_draft, review_path = _setup(tmp_path)
    if mutation == "review_hash":
        payload = read_json(review_path)
        payload["package_sha256"] = "0" * 64
        review_path.write_bytes(canonical_json(payload))
    elif mutation == "draft_review":
        payload = read_json(cashflow_draft / "cashflow_bridge_package.json")
        payload["review"] = read_json(review_path)
        (cashflow_draft / "cashflow_bridge_package.json").write_bytes(canonical_json(payload))
        _refresh_manifest(cashflow_draft, "cashflow_bridge_package.json")
    elif mutation == "existing_bridge":
        payload = read_json(operating_source / "case_input.json")
        payload["cashflow_bridge"] = read_json(cashflow_draft / "cashflow_bridge_package.json")
        (operating_source / "case_input.json").write_bytes(canonical_json(payload))
        _refresh_manifest(operating_source, "case_input.json")
    elif mutation == "draft_provenance":
        payload = read_json(cashflow_draft / "provenance.json")
        payload["source_artifact_sha256"].pop("operating_scenario_context.json")
        (cashflow_draft / "provenance.json").write_bytes(canonical_json(payload))
        _refresh_manifest(cashflow_draft, "provenance.json")
    elif mutation == "output_symlink":
        (tmp_path / "output").symlink_to(tmp_path / "missing-output-target")
    else:
        (operating_source / "nested").mkdir()

    with pytest.raises(ValueError):
        attach_cashflow_review(operating_source, cashflow_draft, review_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("target", ["source_manifest", "draft_manifest", "review", "source_blob"])
def test_mid_assembly_mutation_is_rejected_before_publication(tmp_path, monkeypatch, target):
    operating_source, cashflow_draft, review_path = _setup(tmp_path)
    original = adapter.load_case_context
    calls = 0

    def mutate(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            path = {"source_manifest": operating_source / "manifest.json",
                    "draft_manifest": cashflow_draft / "manifest.json",
                    "review": review_path, "source_blob": operating_source / "evidence.json"}[target]
            path.write_bytes(b"{}")
        return result

    monkeypatch.setattr(adapter, "load_case_context", mutate)
    with pytest.raises(ValueError, match="changed"):
        attach_cashflow_review(operating_source, cashflow_draft, review_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()
