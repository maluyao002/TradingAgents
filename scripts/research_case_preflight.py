"""Prepare an offline-only core-reader input from an immutable Stage 2 case.

This checks delivery/readiness, not financial closure. It never instantiates a
model provider, copies an old scenario clearance, or executes a report run.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from cli.research import _resolve_paths
from tradingagents.research.case_context import load_case_context
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.storage import atomic_write, canonical_json, parse_json, read_bytes


def _verified_blobs(directory: Path) -> dict[str, bytes]:
    manifest = parse_json(read_bytes(directory / "manifest.json"))
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("source artifact inventory is missing")
    blobs = {}
    for name, expected in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid source artifact path")
        raw = read_bytes(directory / name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("source artifact hash mismatch")
        blobs[name] = raw
    return blobs


def prepare_case_reader(case_dir: Path, output: Path) -> dict:
    """Validate first, then create a new English replay-only input directory."""
    case_dir, output = case_dir.resolve(), output.resolve()
    if output.exists() or output == case_dir or case_dir in output.parents:
        raise ValueError("output must be a new directory outside the source case")
    blobs = _verified_blobs(case_dir)
    packet = _verified_blobs(case_dir / "scenario_packet")
    recorded_packet = parse_json(blobs["scenario_packet_hashes.json"])
    if recorded_packet != {name: hashlib.sha256(raw).hexdigest() for name, raw in packet.items()}:
        raise ValueError("case and scenario packet inventories differ")
    if len(packet["request.json"]) > 1024 * 1024:
        raise ValueError("source request exceeds size allowance")
    source_request = ResearchRequest.model_validate(
        _resolve_paths(parse_json(packet["request.json"]), case_dir / "scenario_packet"))
    snapshot = EvidenceSnapshot.model_validate(parse_json(packet["evidence.json"]))
    if validate_snapshot(snapshot, source_request) != snapshot:
        raise ValueError("source evidence requires normalization")
    case = parse_json(blobs["financial_case.json"])
    selected_ids = {row["fact_id"] for schedule in case["schedules"] for row in schedule["components"]}
    selected_ids.update(row["fact_id"] for row in case["commitments"]["items"])
    by_source = {}
    for fact in snapshot.facts:
        if fact.id in selected_ids:
            by_source.setdefault(fact.source_id, []).append(fact.id)
    passages = []
    for item in parse_json(blobs["source_material.json"]):
        if item["source_id"] not in by_source:
            continue  # Other ecosystem/market material remains in frozen evidence.
        passages.append({
            "source_id": item["source_id"], "source_sha256": item["source_sha256"],
            "start": item["start"], "end": item["end"], "text": item["text"],
            "fact_ids": by_source[item["source_id"]],
            "location_hints": [item["context"], f"Units: {item['unit']}",
                               f"Observation date: {item['observation_date']}",
                               "Source-level association only; not proof each fact is entailed by each passage."],
            "selection_basis": "authored_exact",
        })
    envelope = canonical_json({"case": case, "source_passages": passages})
    request = ResearchRequest.model_validate({
        **source_request.model_dump(mode="json"), "backend": "replay",
        "quality_revision": "evidence-led-bounded", "report_language": "English",
        "additional_report_languages": (), "financial_case_path": output / "case_input.json",
        "evidence_path": output / "evidence.json", "output_dir": output / "run",
        "prior_dossier_path": None, "dossier_dir": None,
    })
    context = load_case_context(envelope, request, snapshot)
    readiness = {
        "status": "offline_input_preflight_passed_not_financial_or_report_acceptance",
        "model_calls": 0, "report_generated": False, "live_authorized": False,
        "case_reviewed": context.reviewed, "facts": len(snapshot.facts),
        "case_context_utf8_bytes": len(canonical_json(context.model_context())),
        "input_source_dir": str(case_dir), "source_hashes": {
            name: hashlib.sha256(raw).hexdigest() for name, raw in blobs.items()},
        "output_scope": context.scope.model_dump(mode="json"),
        "limitations": context.limitations,
        "next": "Offline replay tests precede any explicitly authorized bounded live report pilot. "
                "Reviewed forecasts and financial closure remain engineering work, not user sign-off.",
    }
    outputs = {
        "request.json": canonical_json(request), "evidence.json": packet["evidence.json"],
        "case_input.json": envelope, **context.artifacts, "readiness.json": canonical_json(readiness),
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "artifact_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()},
        "scope": "Offline Stage 3 reader input; draft case, no live authorization or research report.",
    }))
    return readiness


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = prepare_case_reader(args.case_dir, args.output)
    print(canonical_json({key: result[key] for key in (
        "status", "model_calls", "report_generated", "live_authorized", "case_reviewed",
        "facts", "case_context_utf8_bytes")}).decode())


if __name__ == "__main__":
    main()
