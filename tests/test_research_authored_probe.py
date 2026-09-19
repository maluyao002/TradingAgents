"""Bounded authored-context diagnostics retain existing no-retry/usage rules."""
import hashlib
import os
from datetime import datetime, timezone

import pytest

from scripts import research_model_probe as probe
from tests.test_research_model_probe import _home, _install_service
from tests.test_research_scenario_compiler import _package, _request, _scenario, _snapshot
from tradingagents.research.scenario_compiler import (
    REVIEWED_PACKET_FILES,
    apply_conditional_review,
    compile_scenarios,
)
from tradingagents.research.storage import canonical_json, digest, read_json

pytestmark = pytest.mark.skipif(os.name != "posix", reason="authored probe requires POSIX file guards")


def fixture(tmp_path, relative_request=False):
    snapshot = _snapshot()
    source = tmp_path / "inputs"
    source.mkdir()
    evidence = source / "evidence.json"
    evidence.write_bytes(canonical_json(snapshot))
    request = _request(backend="codex", evidence_path=evidence, output_dir=tmp_path / "out")
    package, raw = _package(snapshot)
    evidence.write_bytes(raw)
    cases = (_scenario(),)
    artifacts = dict.fromkeys(REVIEWED_PACKET_FILES, {})
    artifacts.update({"evidence.json": snapshot, "request.json": request,
                      "assumptions.json": package, "scenarios.json": cases})
    if relative_request:
        artifacts["request.json"] = {**request.model_dump(mode="json"), "evidence_path": "evidence.json"}
    for name, value in artifacts.items():
        (source / name).write_bytes(raw if name == "evidence.json" else canonical_json(value))
    manifest = {"artifact_hashes": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                     for name in artifacts}}
    (source / "manifest.json").write_bytes(canonical_json(manifest))
    review = {"decision": "conditional_modeling_cleared", "reviewer_kind": "automated_agent",
              "reviewer": "Synthetic reviewer", "reviewed_at": datetime.now(timezone.utc).isoformat(),
              "prerequisites": [], "limitations": ["Synthetic review; no real financial acceptance."],
              "manifest_sha256": digest(manifest), "assumptions_sha256": digest(package),
              "scenarios_sha256": digest(cases)}
    reviewed = apply_conditional_review(package, cases, review, manifest)
    proposal = compile_scenarios(reviewed, snapshot, request, raw, cases)["cases"][0]["proposal"]
    context = {"ticker": request.ticker, "cutoff": request.cutoff.isoformat(),
               "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
               "proposal": proposal, "review_sha256": digest(review), "review": review,
               "packet_manifest": manifest, "source_assumptions": package.model_dump(mode="json"),
               "source_scenarios": [case.model_dump(mode="json") for case in cases],
               "scope": "Synthetic conditional model; not a human-approved valuation."}
    path = tmp_path / "context.json"
    path.write_bytes(canonical_json(context))
    (tmp_path / "approved-review-digest").write_text(digest(review))
    return request, context, path


def worker(tmp_path, path):
    return probe.ModelProbeWorker(_home(tmp_path), path,
                                  (tmp_path / "approved-review-digest").read_text(), tmp_path / "inputs")


def test_valid_context_is_bound_in_prompt_preflight_and_provenance(tmp_path, monkeypatch):
    request, context, path = fixture(tmp_path)
    calls = _install_service(monkeypatch, data=context["proposal"])
    result = worker(tmp_path, path)(request)
    assert result.stop_reason == "model_probe_completed" and len(calls) == 1
    assert calls[0][1]["authored_conditional_model"] == context
    assert "not human approval" in calls[0][1]["authored_model_review_scope"]
    provenance = read_json(request.output_dir / "provenance.json")
    assert provenance["authored_context_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert provenance["authored_context_sha256_after"] == provenance["authored_context_sha256"]
    assert read_json(request.output_dir / "calculation_result.json")["status"] == "illustrative"


@pytest.mark.parametrize("field,value", [("ticker", "OTHER"), ("cutoff", "2020-01-01"),
                                        ("evidence_sha256", "f"*64), ("review_sha256", "invalid")])
def test_invalid_context_never_dispatches(tmp_path, monkeypatch, field, value):
    request, context, path = fixture(tmp_path)
    context[field] = value
    path.write_bytes(canonical_json(context))
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.usage.total_tokens == 0 and result.usage.complete
    assert result.stop_reason == "model_probe_failed"


def test_changed_context_retains_usage_and_blocks_success(tmp_path, monkeypatch):
    request, context, path = fixture(tmp_path)
    calls = _install_service(monkeypatch, data=context["proposal"],
                             on_complete=lambda: path.write_bytes(b"changed"))
    result = worker(tmp_path, path)(request)
    assert len(calls) == 1 and result.usage.complete and result.usage.total_tokens == 150
    assert result.stop_reason == "model_probe_context_changed"


def test_context_without_calculable_proposal_is_not_admitted(tmp_path, monkeypatch):
    request, context, path = fixture(tmp_path)
    context["proposal"]["model"] = None
    path.write_bytes(canonical_json(context))
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.stop_reason == "model_probe_failed"


def test_context_symlink_substitution_is_rejected_at_open(tmp_path, monkeypatch):
    request, _, path = fixture(tmp_path)
    alternate = tmp_path / "alternate"
    alternate.write_bytes(path.read_bytes())
    original = os.open
    swapped = False

    def swap_then_open(target, flags, *args, **kwargs):
        nonlocal swapped
        if target == path and not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(alternate)
        return original(target, flags, *args, **kwargs)

    monkeypatch.setattr(probe.os, "open", swap_then_open)
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert swapped and not calls and result.stop_reason == "model_probe_failed"


def test_nonregular_context_is_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(probe.ModelProbeError, match="regular"):
        probe._read_authored_context(fifo)


@pytest.mark.parametrize("mutation", ["empty_inventory", "open_prerequisite", "changed_proposal",
                                     "lost_limitations", "changed_draft", "fake_review_hash"])
def test_review_chain_tampering_never_dispatches(tmp_path, monkeypatch, mutation):
    request, context, path = fixture(tmp_path)
    if mutation == "empty_inventory":
        context["packet_manifest"]["artifact_hashes"] = {}
        context["review"]["manifest_sha256"] = digest(context["packet_manifest"])
    elif mutation == "open_prerequisite":
        context["review"]["prerequisites"] = ["unresolved calibration"]
    elif mutation == "changed_proposal":
        context["proposal"]["model"]["discount_rate"] = "0.115"
    elif mutation == "lost_limitations":
        context["proposal"]["scope_limitations"] = []
    elif mutation == "changed_draft":
        context["source_assumptions"]["entries"][0]["rationale"] = "Changed after review"
    context["review_sha256"] = digest(context["review"])
    if mutation == "fake_review_hash":
        context["review_sha256"] = "a" * 64
    path.write_bytes(canonical_json(context))
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.stop_reason == "model_probe_failed"
    assert result.usage.complete and result.usage.total_tokens == 0


def test_coherently_replaced_review_chain_fails_external_anchor(tmp_path, monkeypatch):
    from pydantic import TypeAdapter

    from tradingagents.research.assumptions import AssumptionPackage
    from tradingagents.research.scenario_compiler import ConditionalScenario

    request, context, path = fixture(tmp_path)
    context["review"].update(reviewer="Replacement", limitations=["Replacement restrictions"])
    context["review_sha256"] = digest(context["review"])
    original = AssumptionPackage.model_validate(context["source_assumptions"])
    cases = TypeAdapter(tuple[ConditionalScenario, ...]).validate_python(context["source_scenarios"])
    reviewed = apply_conditional_review(original, cases, context["review"], context["packet_manifest"])
    context["proposal"] = compile_scenarios(reviewed, _snapshot(), request,
        request.evidence_path.read_bytes(), cases)["cases"][0]["proposal"]
    path.write_bytes(canonical_json(context))
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.stop_reason == "model_probe_failed"


@pytest.mark.parametrize("name", ["economic_audit.json", "market_inputs.json", "calibration.json", "request.json"])
def test_actual_packet_tampering_never_dispatches(tmp_path, monkeypatch, name):
    request, _, path = fixture(tmp_path)
    (tmp_path / "inputs" / name).write_text("{}")
    # Synthetic non-request auxiliaries start empty; ensure different actual bytes.
    if name != "request.json":
        (tmp_path / "inputs" / name).write_text('{"changed":true}')
    calls = _install_service(monkeypatch)
    result = worker(tmp_path, path)(request)
    assert not calls and result.stop_reason == "model_probe_failed"


def test_packet_request_uses_verified_bytes_not_a_second_path_read(tmp_path, monkeypatch):
    request, context, path = fixture(tmp_path)
    actual = probe._read_authored_context

    def replace_after_read(target):
        content = actual(target)
        if target.name == "request.json":
            target.write_bytes(b"unreviewed replacement")
        return content

    monkeypatch.setattr(probe, "_read_authored_context", replace_after_read)
    calls = _install_service(monkeypatch, data=context["proposal"])
    result = worker(tmp_path, path)(request)
    assert len(calls) == 1 and result.stop_reason == "model_probe_completed"


def test_verified_request_resolves_packet_relative_paths(tmp_path, monkeypatch):
    request, context, path = fixture(tmp_path, relative_request=True)
    calls = _install_service(monkeypatch, data=context["proposal"])
    result = worker(tmp_path, path)(request)
    assert len(calls) == 1 and result.stop_reason == "model_probe_completed"
