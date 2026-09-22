"""Exact current-engine rehearsal without historical attestation reuse."""

from hashlib import sha256

import pytest

from scripts.research_writer_capture import SOURCE_NAMES, capture_payload
from tests.test_research_case_engine import case_setup
from tradingagents.research.engine import run_research
from tradingagents.research.storage import canonical_json, digest, read_json


def source_fixture(tmp_path):
    request, services = case_setup(tmp_path / "source")
    run_research(request, services)
    return request, services


def static_payload(payload):
    return {k: v for k, v in payload.items() if k not in {"timeout_seconds", "max_output_tokens"}}


def test_capture_uses_exact_engine_editor_then_new_reader_factual_payload(tmp_path):
    request, services = source_fixture(tmp_path)
    source = request.output_dir
    hashes = {name: sha256((source / name).read_bytes()).hexdigest() for name in SOURCE_NAMES}
    editor = capture_payload(source, request, tmp_path / "editor")
    original = next(payload for _, payload in services.models.calls if payload["stage"] == "editor")
    assert editor == static_payload(original)
    authored = read_json(source / "stages/editor.json")["output"]
    authored["sections"][0]["text"] += " Newly authored diagnostic prose."
    factual = capture_payload(source, request, tmp_path / "factual", authored)
    assert factual["stage"] == "verify_report"
    reader = factual["research"]["rendered_reader"]
    assert "Newly authored diagnostic prose." in reader
    assert "financial schedules remain an unreviewed draft" in reader
    assert factual["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
    assert "rendering_provenance" in factual["research"]
    record = read_json(tmp_path / "factual/offline_capture.json")
    assert not record["acceptance"] and record["live_calls"] == 0
    assert record["payload_sha256"] == digest(factual)
    assert record["observed_stages"][-1] == "verify_report"
    assert len(record["fixture_bindings"]) == 8
    assert not (tmp_path / "factual/rehearsal/stages/verify_report.json").exists()
    assert all(sha256((source / name).read_bytes()).hexdigest() == value for name, value in hashes.items())


def test_historical_unknown_usage_is_retained_as_unknown(tmp_path):
    request, _ = source_fixture(tmp_path)
    path = request.output_dir / "result.json"
    result = read_json(path)
    result["usage"]["complete"] = False
    path.write_bytes(canonical_json(result))
    capture_payload(request.output_dir, request, tmp_path / "capture")
    meta = read_json(tmp_path / "capture/offline_capture.json")
    assert meta["historical_usage"] == result["usage"]
    assert not meta["historical_usage"]["complete"]
    assert meta["live_calls"] == 0


@pytest.mark.parametrize("mutation", ["input", "checkpoint", "path"])
def test_capture_rejects_changed_input_invalid_checkpoint_or_reused_path(tmp_path, mutation):
    request, _ = source_fixture(tmp_path)
    destination = tmp_path / "capture"
    if mutation == "input":
        request.financial_case_path.write_bytes(b"{}")
    elif mutation == "checkpoint":
        path = request.output_dir / "stages/planner.json"
        record = read_json(path)
        record["output"]["summary"] = "changed fixture"
        path.write_bytes(canonical_json(record))
    else:
        destination.mkdir()
    with pytest.raises(ValueError):
        capture_payload(request.output_dir, request, destination)


def test_engine_reads_private_frozen_inputs_if_original_paths_change(tmp_path, monkeypatch):
    from scripts import research_writer_capture as capture

    request, _ = source_fixture(tmp_path)
    original = request.financial_case_path.read_bytes()
    real_run = capture.run_research

    def mutate_original_then_run(offline_request, services):
        request.financial_case_path.write_bytes(b"{}")
        assert offline_request.financial_case_path != request.financial_case_path
        assert offline_request.financial_case_path.read_bytes() == original
        return real_run(offline_request, services)

    monkeypatch.setattr(capture, "run_research", mutate_original_then_run)
    assert capture.capture_payload(request.output_dir, request, tmp_path / "capture")["stage"] == "editor"


def test_old_prompt_mismatch_is_visible_fixture_context_not_validated_recovery(tmp_path):
    request, _ = source_fixture(tmp_path)
    path = request.output_dir / "stages/planner.json"
    record = read_json(path)
    record["inputs_hash"] = "0" * 64
    path.write_bytes(canonical_json(record))
    capture_payload(request.output_dir, request, tmp_path / "capture")
    bindings = read_json(tmp_path / "capture/offline_capture.json")["fixture_bindings"]
    planner = next(item for item in bindings if item["stage"] == "planner")
    assert not planner["matches_current_payload"]
    assert planner["classification"] == "offline_fixture_not_current_validated_recovery"
