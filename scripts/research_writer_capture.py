"""Offline exact-payload rehearsal; saved responses are fixtures, not recovery.

No provider is imported or instantiated. The real engine constructs its current
writer/factual payload and stops with an intentionally invalid fixture reply.
The rehearsal can never export an accepted report or reuse a reader attestation.
"""

import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from tradingagents.research.contracts import Budget, EvidenceSnapshot, Usage
from tradingagents.research.engine import run_research
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    read_bytes,
    read_json,
)

PREFIX = ("independent_challenge", "planner", "business", "accounting", "expectations",
          "management", "reconcile_challenge", "verify_claims")
SOURCE_NAMES = ("evidence.json", "case_input.json", "result.json", "run_metadata.json",
                *(f"stages/{stage}.json" for stage in PREFIX))


def source_material(source):
    """Read only the explicit frozen fixture set; never historical reader reviews."""
    source = Path(source)
    if source.is_symlink():
        raise ValueError("invalid diagnostic source")
    contents = {}
    for name in SOURCE_NAMES:
        path = source / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("diagnostic fixtures cannot be symlinks")
        contents[name] = read_bytes(path)
    return contents


class CaptureService:
    kind = "offline_diagnostic_fixture"

    def __init__(self, responses, target, input_hashes, historical_identity):
        self.responses = responses
        self.target = target
        self.identity = digest({"fixture_only": responses, "capture_target": target})
        self.payload = None
        self.seen = []
        self.input_hashes = input_hashes
        self.fixture_bindings = []
        self.historical_identity = historical_identity

    def complete(self, role, payload, request):
        stage = payload["stage"]
        self.seen.append(stage)
        if stage == self.target:
            if self.payload is not None:
                raise ValueError("duplicate capture target")
            self.payload = deepcopy(payload)
            # Complete zero usage describes THIS offline fixture invocation only,
            # never the historical model call that produced an input response.
            return ModelReply(data={"intentional_offline_capture_stop": True}, usage=Usage())
        if stage not in self.responses or self.seen.count(stage) != 1:
            raise ValueError("unexpected diagnostic fixture stage")
        if stage in self.input_hashes:
            current = digest({k: v for k, v in payload.items()
                              if k not in {"timeout_seconds", "max_output_tokens"}})
            self.fixture_bindings.append({"stage": stage,
                "historical_checkpoint_identity": self.historical_identity,
                "historical_inputs_sha256": self.input_hashes[stage],
                "current_inputs_sha256": current,
                "matches_current_payload": current == self.input_hashes[stage],
                "classification": "offline_fixture_not_current_validated_recovery"})
        return ModelReply(data=deepcopy(self.responses[stage]), usage=Usage())


def capture_payload(source, request, destination, writer_reply=None):
    """Build the current engine payload in a fresh offline diagnostic directory.

    writer_reply is the new editor's decoded data (not a ModelReply envelope).
    The returned payload is the actual static engine boundary; the bounded live
    caller must add its timeout and output limit separately.
    """
    if Path(source).is_symlink() or Path(destination).is_symlink():
        raise ValueError("diagnostic paths cannot be symlinks")
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if (destination.exists() or destination.is_relative_to(source)
            or source.is_relative_to(destination)):
        raise ValueError("capture requires a fresh destination outside source")
    if (request.additional_report_languages or request.budget.followup_cycles
            or request.prior_dossier_path or request.report_language != "English"
            or request.quality_revision != "evidence-led-bounded"):
        raise ValueError("capture requires a frozen English case with no followups")
    contents = source_material(source)
    for field, name in (("evidence_path", "evidence.json"), ("financial_case_path", "case_input.json")):
        path = getattr(request, field)
        if path is None or read_bytes(path) != contents[name]:
            raise ValueError("request input differs from frozen diagnostic source")
    responses, input_hashes = {}, {}
    metadata = read_json(source / "run_metadata.json")
    if not re.fullmatch(r"[0-9a-f]{64}", metadata.get("identity", "")):
        raise ValueError("diagnostic source historical identity is invalid")
    for stage in PREFIX:
        record = read_json(source / f"stages/{stage}.json")
        if digest(record["output"]) != record["output_hash"]:
            raise ValueError("diagnostic fixture output hash mismatch")
        if (record.get("identity") != metadata.get("identity")
                or not re.fullmatch(r"[0-9a-f]{64}", record.get("inputs_hash", ""))):
            raise ValueError("diagnostic fixture historical identity is invalid")
        responses[stage] = record["output"]
        input_hashes[stage] = record["inputs_hash"]
    target = "editor"
    if writer_reply is not None:
        from tradingagents.research.case_report import CaseReportDraft
        responses["editor"] = CaseReportDraft.model_validate(writer_reply).model_dump(mode="json")
        target = "verify_report"
    service = CaptureService(responses, target, input_hashes, metadata["identity"])
    destination.mkdir(parents=True, exist_ok=False)
    frozen_paths = {}
    for field, name in (("evidence_path", "evidence.json"), ("financial_case_path", "case_input.json")):
        frozen_paths[field] = destination / "inputs" / name
        atomic_write(frozen_paths[field], contents[name])
    # This budget is an offline rehearsal bound, never authorization to dispatch.
    offline_request = request.model_copy(update={
        "output_dir": destination / "rehearsal", **frozen_paths,
        "budget": Budget(total_tokens=20_000_000, wall_seconds=600, call_timeout_seconds=600,
                         reserve_tokens=0, reserve_seconds=0, followup_cycles=0),
    })
    snapshot = EvidenceSnapshot.model_validate_json(contents["evidence.json"])
    result = run_research(offline_request, ResearchServices(SnapshotEvidenceService(snapshot), service))
    if service.payload is not None:
        service.payload.pop("timeout_seconds", None)
        service.payload.pop("max_output_tokens", None)
    metadata = {"kind": "offline_payload_fixture_not_research_recovery", "live_calls": 0,
        "acceptance": False, "target": target, "observed_stages": service.seen,
        "fixture_bindings": service.fixture_bindings,
        "source_run": str(source), "historical_usage": read_json(source / "result.json")["usage"],
        "source_artifacts": {name: sha256(content).hexdigest() for name, content in contents.items()},
        "stop_reason": result.stop_reason,
        "payload_sha256": digest(service.payload) if service.payload else None}
    atomic_write(destination / "offline_capture.json", canonical_json(metadata))
    if service.payload is None or result.stop_reason == "completed_needs_review":
        raise ValueError("offline engine did not stop at the requested diagnostic boundary")
    if source_material(source) != contents:
        raise ValueError("diagnostic source changed during capture")
    if (read_bytes(frozen_paths["evidence_path"]) != contents["evidence.json"]
            or read_bytes(frozen_paths["financial_case_path"]) != contents["case_input.json"]):
        raise ValueError("private frozen inputs changed during capture")
    atomic_write(destination / "captured_payload.json", canonical_json(service.payload))
    return service.payload
