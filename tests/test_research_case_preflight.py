"""Portable offline Stage 2-to-3 preparation; no source/model calls."""

from hashlib import sha256

import pytest

from scripts.research_case_preflight import prepare_case_reader
from tests.test_research_case_engine import case_setup
from tradingagents.research.storage import canonical_json, read_json


def write_inventory(directory, blobs):
    directory.mkdir(parents=True)
    for name, data in blobs.items():
        (directory / name).write_bytes(data)
    hashes = {name: sha256(data).hexdigest() for name, data in blobs.items()}
    (directory / "manifest.json").write_bytes(canonical_json({"artifact_hashes": hashes}))
    return hashes


def source_case(tmp_path):
    request, services = case_setup(tmp_path / "fixture")
    snapshot = services.evidence.snapshot
    source = snapshot.sources[0]
    case_dir = tmp_path / "case"
    packet_hashes = write_inventory(case_dir / "scenario_packet", {
        "request.json": canonical_json(request), "evidence.json": canonical_json(snapshot),
    })
    material = {"source_id": source.id, "source_sha256": source.content_sha256,
                "start": 0, "end": len(source.content), "text": source.content,
                "context": "Synthetic source slice", "unit": "USD", "observation_date": "2026-09-17"}
    blobs = {
        "financial_case.json": canonical_json(read_json(request.financial_case_path)["case"]),
        "source_material.json": canonical_json([material]),
        "scenario_packet_hashes.json": canonical_json(packet_hashes),
    }
    for name, data in blobs.items():
        (case_dir / name).write_bytes(data)
    (case_dir / "manifest.json").write_bytes(canonical_json({
        "artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in blobs.items()}}))
    return case_dir


def test_preflight_preserves_source_and_disables_live(tmp_path):
    source = source_case(tmp_path)
    before = {str(path): path.read_bytes() for path in source.rglob("*.json")}
    output = tmp_path / "new" / "preflight"
    result = prepare_case_reader(source, output)
    assert result["model_calls"] == 0
    assert not result["case_reviewed"] and not result["report_generated"] and not result["live_authorized"]
    request = read_json(output / "request.json")
    assert request["backend"] == "replay" and request["report_language"] == "English"
    assert request["dossier_dir"] is None and request["prior_dossier_path"] is None
    context = read_json(output / "case_context.json")
    assert "Synthetic source slice" in canonical_json(context).decode()
    assert {str(path): path.read_bytes() for path in source.rglob("*.json")} == before
    assert not (output / "run").exists()
    hashes = read_json(output / "manifest.json")["artifact_hashes"]
    assert all(sha256((output / name).read_bytes()).hexdigest() == expected for name, expected in hashes.items())
    with pytest.raises(ValueError, match="new directory"):
        prepare_case_reader(source, output)


@pytest.mark.parametrize("path", ["financial_case.json", "scenario_packet/evidence.json"])
def test_preflight_tamper_rejected_before_output(tmp_path, path):
    source = source_case(tmp_path)
    (source / path).write_text("{}")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare_case_reader(source, output)
    assert not output.exists()


def test_preflight_rejects_mismatched_case_packet_binding(tmp_path):
    source = source_case(tmp_path)
    binding = source / "scenario_packet_hashes.json"
    binding.write_bytes(canonical_json({}))
    manifest = read_json(source / "manifest.json")
    manifest["artifact_hashes"][binding.name] = sha256(binding.read_bytes()).hexdigest()
    (source / "manifest.json").write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError, match="inventories differ"):
        prepare_case_reader(source, tmp_path / "output")
