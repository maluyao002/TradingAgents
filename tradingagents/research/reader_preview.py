"""Offline presentation comparison of saved text; never research acceptance."""

from hashlib import sha256
from pathlib import Path

from .calculated_values import CalculatedValue, render_calculations
from .case_report import CaseReportDraft
from .contracts import EvidenceSnapshot
from .evidence import validate_snapshot
from .reader import ReaderIssue, render_reader
from .rendering import render_references
from .storage import atomic_write, canonical_json, digest, parse_json, read_bytes

_EXPORTED_DRAFT_STAGES = {
    "verify_report": "editor",
    "verify_repaired_report": "repair_report",
}


def _read_source(source: Path, name: str) -> bytes:
    path = source / name
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("preview inputs cannot be symlinks")
    return read_bytes(path)


def _checkpoint_output(record, label: str):
    if not isinstance(record, dict) or "output" not in record:
        raise ValueError(f"invalid saved {label} checkpoint")
    if digest(record["output"]) != record.get("output_hash"):
        raise ValueError(f"saved {label} output hash mismatch")
    return record["output"]


def _reader_binding(source: Path, request, contents: dict[str, bytes]):
    """Select the exported candidate, or retain the initial failed-run candidate."""

    verification_path = source / "reader_verification.json"
    if verification_path.is_symlink():
        raise ValueError("preview inputs cannot be symlinks")
    if not verification_path.exists():
        return "editor", "verify_report", None
    contents["reader_verification.json"] = _read_source(source, "reader_verification.json")
    verification = parse_json(contents["reader_verification.json"])
    if not isinstance(verification, dict):
        raise ValueError("invalid reader verification record")
    attestation = verification.get(request.report_language)
    if attestation is None:
        return "editor", "verify_report", None
    if not isinstance(attestation, dict) or type(attestation.get("exported")) is not bool:
        raise ValueError("invalid reader verification attestation")
    if not attestation["exported"]:
        return "editor", "verify_report", None
    stage = attestation.get("stage")
    if stage not in _EXPORTED_DRAFT_STAGES:
        raise ValueError("unsupported exported reader verification stage")
    reader_sha256 = attestation.get("reader_sha256")
    if not isinstance(reader_sha256, str):
        raise ValueError("exported reader verification lacks a reader hash")
    return _EXPORTED_DRAFT_STAGES[stage], stage, reader_sha256


def preview_saved_reader(source: Path, request, destination: Path):
    """Render a new explicitly unverified preview while retaining source hashes."""
    source, destination = source.resolve(), destination.resolve()
    if (destination == source or destination.is_relative_to(source)
            or source.is_relative_to(destination) or destination.exists()):
        raise ValueError("preview requires a fresh destination outside the source run")
    contents: dict[str, bytes] = {}
    draft_stage, reader_stage, exported_reader_sha256 = _reader_binding(
        source, request, contents
    )
    names = (
        f"stages/{draft_stage}.json",
        "evidence.json",
        "calculated_values.json",
        "reader_limitations.json",
        f"stages/{reader_stage}-reader-candidate.json",
    )
    if exported_reader_sha256 is not None:
        names = (*names, "reader_report.md")
    for name in names:
        contents[name] = _read_source(source, name)
    records = {
        name: parse_json(content)
        for name, content in contents.items()
        if name.endswith(".json")
    }
    draft_record = records[f"stages/{draft_stage}.json"]
    draft_output = _checkpoint_output(draft_record, f"{draft_stage} draft")
    snapshot = validate_snapshot(EvidenceSnapshot.model_validate(records["evidence.json"]), request)
    calculations = tuple(CalculatedValue.model_validate(item) for item in records["calculated_values.json"])
    draft = CaseReportDraft.model_validate(draft_output)
    draft = draft.model_copy(update={"sections": tuple(section.model_copy(update={
        "text": render_calculations(render_references(section.text, snapshot.facts, request.report_language),
                                    calculations, request.report_language),
    }) for section in draft.sections)})
    issues = []
    for item in records["reader_limitations.json"]["unresolved_issues"]["occurrences"]:
        if item["source_kind"] == "raw_gap":
            issues.append(item["original_text"])
        else:
            issues.append(ReaderIssue(
                message=item["original_text"], provenance_id=item["provenance_id"],
                severity=item["severity"], category=item["category"], code=item.get("code"),
                affected_ids=tuple(item.get("affected_ids", [])),
            ))
    preview = render_reader(request, draft, snapshot, issues, request.report_language, compact=True)
    candidate_record = records[f"stages/{reader_stage}-reader-candidate.json"]
    candidate = _checkpoint_output(candidate_record, "reader candidate")
    prior = candidate.get("reader_text") if isinstance(candidate, dict) else None
    candidate_sha256 = candidate.get("reader_sha256") if isinstance(candidate, dict) else None
    if not isinstance(prior, str) or not isinstance(candidate_sha256, str):
        raise ValueError("invalid saved reader candidate")
    if sha256(prior.encode()).hexdigest() != candidate_sha256:
        raise ValueError("saved reader candidate hash mismatch")
    binding = "initial_candidate"
    if exported_reader_sha256 is not None:
        final_reader = contents["reader_report.md"]
        if sha256(final_reader).hexdigest() != exported_reader_sha256:
            raise ValueError("saved final reader hash mismatch")
        if candidate_sha256 != exported_reader_sha256 or final_reader != prior.encode():
            raise ValueError("saved final reader does not match its selected candidate")
        binding = "exported_final_reader"
    banner = ("> OFFLINE PRESENTATION PREVIEW — NOT VERIFIED OR ACCEPTED.\n"
              "> Saved model-authored prose is retained; this is not a new factual review, "
              "financial clearance, or an admitted report.\n\n")
    text = banner + preview.reader_text
    metrics = {
        "status": "unverified_presentation_preview", "live_calls": 0,
        "source_hashes": {name: sha256(content).hexdigest() for name, content in contents.items()},
        "source_reader_binding": binding,
        "selected_draft_stage": draft_stage,
        "selected_reader_stage": reader_stage,
        "original_reader_bytes": len(prior.encode()),
        "candidate_reader_bytes": len(preview.reader_text.encode()),
        "preview_sha256": sha256(text.encode()).hexdigest(),
        "candidate_sha256": sha256(preview.reader_text.encode()).hexdigest(),
        "paragraphs_with_explicit_citations": sum(bool(item["source_ids"])
                                                  for item in preview.limitations_audit["paragraph_citations"]),
        "acceptance": False,
    }
    destination.mkdir(parents=True)
    atomic_write(destination / "reader_preview.md", text.encode())
    atomic_write(destination / "reader_limitations.json", canonical_json(preview.limitations_audit))
    atomic_write(destination / "comparison.json", canonical_json(metrics))
    if any(read_bytes(source / name) != content for name, content in contents.items()):
        raise ValueError("historical source changed during preview")
    return metrics
