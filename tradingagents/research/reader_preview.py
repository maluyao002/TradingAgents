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


def preview_saved_reader(source: Path, request, destination: Path):
    """Render a new explicitly unverified preview while retaining source hashes."""
    source, destination = source.resolve(), destination.resolve()
    if (destination == source or destination.is_relative_to(source)
            or source.is_relative_to(destination) or destination.exists()):
        raise ValueError("preview requires a fresh destination outside the source run")
    names = ("stages/editor.json", "evidence.json", "calculated_values.json",
             "reader_limitations.json", "stages/verify_report-reader-candidate.json")
    contents = {}
    for name in names:
        path = source / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("preview inputs cannot be symlinks")
        contents[name] = read_bytes(path)
    records = {name: parse_json(content) for name, content in contents.items()}
    editor = records["stages/editor.json"]
    if digest(editor["output"]) != editor["output_hash"]:
        raise ValueError("saved editor output hash mismatch")
    snapshot = validate_snapshot(EvidenceSnapshot.model_validate(records["evidence.json"]), request)
    calculations = tuple(CalculatedValue.model_validate(item) for item in records["calculated_values.json"])
    draft = CaseReportDraft.model_validate(editor["output"])
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
    prior_record = records["stages/verify_report-reader-candidate.json"]
    if digest(prior_record["output"]) != prior_record["output_hash"]:
        raise ValueError("saved reader candidate output hash mismatch")
    prior = prior_record["output"]["reader_text"]
    if sha256(prior.encode()).hexdigest() != prior_record["output"]["reader_sha256"]:
        raise ValueError("saved reader candidate hash mismatch")
    banner = ("> OFFLINE PRESENTATION PREVIEW — NOT VERIFIED OR ACCEPTED.\n"
              "> Saved model-authored prose is retained; this is not a new factual review, "
              "financial clearance, or an admitted report.\n\n")
    text = banner + preview.reader_text
    metrics = {
        "status": "unverified_presentation_preview", "live_calls": 0,
        "source_hashes": {name: sha256(content).hexdigest() for name, content in contents.items()},
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
