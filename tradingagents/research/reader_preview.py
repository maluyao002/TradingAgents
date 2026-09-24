"""Offline presentation comparison of saved text; never research acceptance."""

from hashlib import sha256
from pathlib import Path

from .calculated_values import CalculatedValue, render_calculations
from .case_context import load_case_context
from .case_report import CaseReportDraft, case_reader_delivery
from .contracts import EvidenceSnapshot
from .evidence import validate_snapshot
from .reader import ReaderIssue, _eligible_evidence_ids, render_reader
from .reader_provenance import case_model_appendix, reader_provenance
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
    contents["run_metadata.json"] = _read_source(source, "run_metadata.json")
    engine = parse_json(contents["run_metadata.json"]).get("engine")
    if engine not in {f"research-v2-preview-{n}" for n in range(1, 13)}:
        raise ValueError("unsupported preview engine revision")
    requires_provenance = engine in {f"research-v2-preview-{n}" for n in range(6, 13)}
    provenance_name = f"stages/{reader_stage}-rendering-provenance.json"
    cite_calculations = False
    provenance = None
    if (source / provenance_name).is_symlink():
        raise ValueError("preview inputs cannot be symlinks")
    if requires_provenance and not (source / provenance_name).exists():
        raise ValueError("current-format preview requires rendering provenance")
    if (source / provenance_name).exists():
        contents[provenance_name] = _read_source(source, provenance_name)
        provenance = _checkpoint_output(parse_json(contents[provenance_name]), "rendering provenance")
        cite_calculations = provenance.get("calculation_citations") is True
        if cite_calculations:
            contents["model_appendix.md"] = _read_source(source, "model_appendix.md")
            contents["case_context.json"] = _read_source(source, "case_context.json")
    if engine in {f"research-v2-preview-{n}" for n in range(7, 13)}:
        contents["case_input.json"] = _read_source(source, "case_input.json")
        contents["case_context.json"] = _read_source(source, "case_context.json")
    records = {
        name: parse_json(content)
        for name, content in contents.items()
        if name.endswith(".json")
    }
    draft_record = records[f"stages/{draft_stage}.json"]
    draft_output = _checkpoint_output(draft_record, f"{draft_stage} draft")
    snapshot = validate_snapshot(EvidenceSnapshot.model_validate(records["evidence.json"]), request)
    calculations = tuple(CalculatedValue.model_validate(item) for item in records["calculated_values.json"])
    case_context = None
    if engine in {f"research-v2-preview-{n}" for n in range(7, 13)}:
        case_context = load_case_context(contents["case_input.json"], request, snapshot)
        if digest(case_context.model_context()) != digest(records["case_context.json"]):
            raise ValueError("saved case context differs from validated frozen inputs")
        expected_calculations = (case_context.operating_scenarios.calculated_values
                                 if case_context.operating_scenarios is not None else ())
        if case_context.cashflow_bridge is not None:
            if engine not in {f"research-v2-preview-{n}" for n in range(9, 13)}:
                raise ValueError("cash-flow bridge requires preview-9 or later")
            expected_calculations = (*expected_calculations, *case_context.cashflow_bridge.calculated_values)
        if calculations != expected_calculations:
            raise ValueError("saved calculation catalog differs from validated frozen case")
    delivery = case_reader_delivery(case_context) if case_context is not None else None
    authored = CaseReportDraft.model_validate(draft_output)
    draft = authored
    eligible_facts = tuple(f for f in snapshot.facts if f.id in _eligible_evidence_ids(snapshot))
    draft = draft.model_copy(update={"sections": tuple(section.model_copy(update={
        "text": render_calculations(render_references(section.text, eligible_facts, request.report_language),
                                    calculations, request.report_language, cite=cite_calculations,
                                    scenario_delivery=delivery),
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
    candidate_record = records[f"stages/{reader_stage}-reader-candidate.json"]
    candidate = _checkpoint_output(candidate_record, "reader candidate")
    prior = candidate.get("reader_text") if isinstance(candidate, dict) else None
    candidate_sha256 = candidate.get("reader_sha256") if isinstance(candidate, dict) else None
    if not isinstance(prior, str) or not isinstance(candidate_sha256, str):
        raise ValueError("invalid saved reader candidate")
    if sha256(prior.encode()).hexdigest() != candidate_sha256:
        raise ValueError("saved reader candidate hash mismatch")
    if provenance is not None:
        if provenance.get("calculation_catalog_sha256") != digest(calculations):
            raise ValueError("saved calculation catalog hash mismatch")
        inputs = provenance.get("rendering_inputs", {})
        if provenance.get("rendering_inputs_sha256") != digest(inputs):
            raise ValueError("saved rendering inputs hash mismatch")
        issues = []
        for issue in inputs.get("issues", []):
            if issue.get("kind") == "text" and isinstance(issue.get("value"), str):
                issues.append(issue["value"])
            elif issue.get("kind") == "reader_issue":
                issues.append(ReaderIssue(**issue["value"]))
            else:
                raise ValueError("invalid saved rendering issue")
        expected = reader_provenance(authored, draft, eligible_facts, calculations,
                                     request.report_language, prior, cite=cite_calculations,
                                     request=request, snapshot=snapshot, issues=issues, case_context=case_context,
                                     bind_case_state=engine in {f"research-v2-preview-{n}" for n in range(8, 13)},
                                     bind_cashflow_inputs=engine == "research-v2-preview-12")
        if digest(expected) != digest(provenance):
            raise ValueError("saved rendering provenance binding mismatch")
        if cite_calculations:
            expected_appendix = case_model_appendix(calculations, request.report_language,
                has_operating_scenarios=records["case_context.json"].get("operating_scenarios") is not None,
                has_cashflow_bridge=case_context.cashflow_bridge is not None if case_context else False)
            actual_appendix = contents["model_appendix.md"]
            if actual_appendix != expected_appendix:
                raise ValueError("saved calculation appendix mismatch")
    preview = render_reader(request, draft, snapshot, issues, request.report_language, compact=True,
                            case_context=case_context,
                            bind_case_state=engine in {f"research-v2-preview-{n}" for n in range(8, 13)},
                            bind_cashflow_inputs=engine == "research-v2-preview-12")
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
    if cite_calculations:
        atomic_write(destination / "model_appendix.md", contents["model_appendix.md"])
    if any(read_bytes(source / name) != content for name, content in contents.items()):
        raise ValueError("historical source changed during preview")
    return metrics
