"""Explicit, content-bound recovery of a failed research run.

Recovery is deliberately separate from normal resume behavior.  It validates a
fixed historical prefix before a model service is allowed to dispatch anything,
then exposes those replies as imported historical calls.  Their known counters
remain charged to the original budget while the source run's unknown usage is
never converted to zero or described as complete.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .contracts import EvidenceSnapshot, ResearchRequest, Usage
from .engine import _calculate, _prompt_evidence, _validate_analysis
from .evidence import validate_snapshot
from .resource_diagnostics import valid_source_resource_shape
from .services import ModelReply
from .stages import AnalysisOutput, ValuationProposal, instruction
from .storage import canonical_json, digest, load_request_inputs, parse_json, request_identity
from .updates import describe_update, eligible_prior
from .valuation import FCFFModelInput
from .wire import WIRE_SCHEMA_VERSION

RECOVERY_SCHEMA_VERSION = 1
SOURCE_WIRE_SCHEMA_VERSION = "research-wire-v1"
IMPORTED_STAGES = (
    "planner",
    "independent_challenge",
    "business",
    "accounting",
    "expectations",
    "management",
)
_STAGE_ROLES = {
    "planner": "planner",
    "independent_challenge": "challenger",
    "business": "business",
    "accounting": "accounting",
    "expectations": "expectations",
    "management": "management",
}
_MAX_FILE_BYTES = 32 * 1024 * 1024
_HASH = re.compile(r"[a-f0-9]{64}")


def _read_regular(path: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
    """Read one bounded regular file without following its final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("recovery inputs must be regular files")
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("recovery input exceeds size allowance")
    return content


def _read_object(path: Path) -> dict[str, Any]:
    value = parse_json(_read_regular(path))
    if not isinstance(value, dict):
        raise ValueError("recovery JSON input must be an object")
    return value


def _artifact_names(source_run: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    result = _read_object(source_run / "result.json")
    artifacts = result.get("artifact_hashes")
    targets = result.get("artifacts")
    if (
        not isinstance(artifacts, dict)
        or not isinstance(targets, dict)
        or artifacts.keys() != targets.keys()
    ):
        raise ValueError("source result artifact manifest is invalid")
    names = []
    for name, expected_hash in artifacts.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not _HASH.fullmatch(str(expected_hash))
        ):
            raise ValueError("source result contains an invalid artifact entry")
        expected_path = source_run / name
        if Path(targets[name]) != expected_path:
            raise ValueError("source result artifact target does not match source directory")
        names.append(name)
    return result, tuple(sorted(names))


def source_artifact_hashes(source_run: Path) -> dict[str, str]:
    """Hash the exact immutable source set shared with valuation diagnostics."""
    source_run = Path(source_run)
    if source_run.is_symlink() or not source_run.is_dir():
        raise ValueError("source run must be a real directory")
    source_run = source_run.resolve()
    result, artifact_names = _artifact_names(source_run)
    relative_names = {
        "result.json",
        "research_checkpoint.json",
        "stages/resources.json",
        *(f"stages/{stage}.json" for stage in IMPORTED_STAGES),
        *artifact_names,
    }
    hashes: dict[str, str] = {}
    for name in sorted(relative_names):
        path = source_run / name
        if path.parent != source_run and path.parent.is_symlink():
            raise ValueError("source artifact directory cannot be a symlink")
        content = _read_regular(path)
        hashes[name] = hashlib.sha256(content).hexdigest()
        if (
            name in result.get("artifact_hashes", {})
            and hashes[name] != result["artifact_hashes"][name]
        ):
            raise ValueError("source artifact hash does not match result manifest")
    return hashes


def _source_identity(
    request: ResearchRequest, home: Path, inputs: dict[str, bytes]
) -> tuple[str, str]:
    request_id = request_identity(request, inputs)
    model_id = digest(
        {
            "service": "isolated-codex-v1",
            "wire": SOURCE_WIRE_SCHEMA_VERSION,
            "home": str(Path(home).resolve()),
        }
    )
    return request_id, digest({"request": request_id, "model_service": model_id})


def _payload(
    request: ResearchRequest,
    snapshot: EvidenceSnapshot,
    stage: str,
    role: str,
    data: dict[str, Any],
    schema: type,
    *,
    role_call_index: int = 0,
) -> dict[str, Any]:
    payload = {
        **instruction(role, schema),
        "evidence": _prompt_evidence(snapshot),
        "research": data,
        "cutoff": request.cutoff.isoformat(),
        "mandate": request.mandate,
        "stage": stage,
        "role_call_index": role_call_index,
        "valuation_months": request.valuation_months,
        "return_months": request.return_months,
        "language": request.internal_language,
    }
    if role == "valuation":
        payload["financial_model_schema"] = TypeAdapter(FCFFModelInput).json_schema()
    return payload


def _checkpoint_output(source_run: Path, stage: str, identity: str, inputs: Any) -> Any:
    record = _read_object(source_run / "stages" / f"{stage}.json")
    if set(record) != {"schema_version", "identity", "inputs_hash", "output", "output_hash"}:
        raise ValueError("source stage checkpoint shape is invalid")
    if (
        record["schema_version"] != 1
        or record["identity"] != identity
        or record["inputs_hash"] != digest(inputs)
        or record["output_hash"] != digest(record["output"])
    ):
        raise ValueError(f"source stage checkpoint mismatch: {stage}")
    return record["output"]


def _sum_usage(entries: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(item.input_tokens for item in entries),
        output_tokens=sum(item.output_tokens for item in entries),
        cached_input_tokens=sum(item.cached_input_tokens for item in entries),
        reasoning_output_tokens=sum(item.reasoning_output_tokens for item in entries),
    )


def _add_known_usage(left: Usage, right: Usage | None) -> Usage:
    right = right or Usage()
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        reasoning_output_tokens=(left.reasoning_output_tokens + right.reasoning_output_tokens),
    )


@dataclass(frozen=True)
class ImportedStage:
    stage: str
    role: str
    payload: dict[str, Any]
    output: dict[str, Any]
    usage: Usage

    @property
    def payload_hash(self) -> str:
        return digest(self.payload)


@dataclass(frozen=True)
class RecoveryPlan:
    source_run: Path
    source_identity: str
    source_request_identity: str
    source_artifact_hashes: dict[str, str]
    source_usage: Usage
    source_elapsed_seconds: float
    source_dispatched: bool
    snapshot: EvidenceSnapshot
    imported_stages: tuple[ImportedStage, ...]
    valuation_payload: dict[str, Any]
    valuation_reply: ModelReply | None = None
    valuation_reply_sha256: str | None = None
    valuation_diagnostic_elapsed_seconds: float = 0.0

    @property
    def imported_usage_by_stage(self) -> dict[str, list[dict[str, Any]]]:
        return {
            item.stage: [
                {
                    "role": item.role,
                    **item.usage.model_dump(mode="json"),
                    "usage_origin": "imported_historical",
                }
            ]
            for item in self.imported_stages
        }


def _validate_diagnostic(
    directory: Path,
    *,
    request_id: str,
    source_hashes: dict[str, str],
    valuation_payload: dict[str, Any],
) -> tuple[ModelReply, str, float]:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("valuation diagnostic must be a real directory")
    directory = directory.resolve()
    provenance = _read_object(directory / "provenance.json")
    trace = _read_object(directory / "diagnostic.json")
    reply_bytes = _read_regular(directory / "valuation_reply.json")
    reply_sha = hashlib.sha256(reply_bytes).hexdigest()
    required = {
        "schema_version": 1,
        "role": "valuation",
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "request_identity": request_id,
        "source_artifact_hashes": source_hashes,
        "payload_hash": digest(valuation_payload),
        "reply_sha256": reply_sha,
    }
    if any(provenance.get(key) != value for key, value in required.items()):
        raise ValueError("valuation diagnostic provenance mismatch")
    reply = ModelReply.model_validate(parse_json(reply_bytes))
    elapsed = trace.get("elapsed_seconds")
    if (
        trace.get("mode") != "one_valuation_call"
        or trace.get("status") != "succeeded"
        or trace.get("usage") != reply.usage.model_dump(mode="json")
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("valuation diagnostic trace mismatch")
    if not reply.usage.complete:
        raise ValueError("valuation diagnostic usage must be complete")
    return reply, reply_sha, float(elapsed)


def validate_recovery_source(
    request: ResearchRequest,
    source_run: Path,
    source_codex_home: Path,
    *,
    valuation_diagnostic: Path | None = None,
) -> RecoveryPlan:
    """Validate all recovery inputs without constructing or calling a live service."""
    source_run = Path(source_run)
    if source_run.is_symlink() or not source_run.is_dir():
        raise ValueError("source run must be a real directory")
    source_run = source_run.resolve()
    if request.backend != "codex" or request.output_dir.resolve() != source_run:
        raise ValueError("source request must be the Codex request for the source run")
    source_hashes = source_artifact_hashes(source_run)
    inputs = load_request_inputs(request)
    request_id, expected_identity = _source_identity(request, source_codex_home, inputs)
    manifest = _read_object(source_run / "research_checkpoint.json")
    if manifest != {"schema_version": 1, "identity": expected_identity}:
        raise ValueError("source run identity does not match request, wire v1, and Codex home")

    evidence_output = _checkpoint_output(source_run, "evidence", expected_identity, {})
    snapshot = validate_snapshot(EvidenceSnapshot.model_validate(evidence_output), request)
    if canonical_json(snapshot) != _read_regular(source_run / "evidence.json"):
        raise ValueError("source evidence artifact differs from its checkpoint")
    if "evidence_path" in inputs:
        supplied = validate_snapshot(
            EvidenceSnapshot.model_validate(parse_json(inputs["evidence_path"])), request
        )
        if supplied != snapshot:
            raise ValueError("source request evidence differs from source run evidence")

    prior = (
        eligible_prior(inputs["prior_dossier_path"], request)
        if "prior_dossier_path" in inputs
        else None
    )
    prior_data = (
        {
            "prior_hypotheses": prior.model_dump(mode="json"),
            "update": describe_update(prior, snapshot),
        }
        if prior
        else {}
    )
    stage_payloads: dict[str, dict[str, Any]] = {}
    stage_outputs: dict[str, AnalysisOutput] = {}
    stage_payloads["planner"] = _payload(
        request, snapshot, "planner", "planner", prior_data, AnalysisOutput
    )
    stage_outputs["planner"] = AnalysisOutput.model_validate(
        _checkpoint_output(source_run, "planner", expected_identity, stage_payloads["planner"])
    )
    if not 3 <= len(stage_outputs["planner"].questions) <= 5:
        raise ValueError("source planner must contain 3-5 decisive questions")
    question_data = {
        "questions": [item.model_dump(mode="json") for item in stage_outputs["planner"].questions]
    }
    for stage in IMPORTED_STAGES[1:]:
        role = _STAGE_ROLES[stage]
        stage_payloads[stage] = _payload(
            request, snapshot, stage, role, question_data, AnalysisOutput
        )
        stage_outputs[stage] = AnalysisOutput.model_validate(
            _checkpoint_output(source_run, stage, expected_identity, stage_payloads[stage])
        )

    known_questions: set[str] = set()
    known_claims: dict[str, dict[str, Any]] = {}
    for stage in IMPORTED_STAGES:
        output = _validate_analysis(stage_outputs[stage], snapshot)
        if len({claim.id for claim in output.claims}) != len(output.claims):
            raise ValueError("duplicate claim identifiers within an imported stage")
        local_questions = {question.id for question in output.questions}
        if any(
            finding.question_id not in known_questions | local_questions
            for finding in output.findings
        ):
            raise ValueError("imported finding references an unknown research question")
        known_questions.update(local_questions)
        for claim in output.claims:
            serialized = claim.model_dump(mode="json")
            if claim.id in known_claims and known_claims[claim.id] != serialized:
                raise ValueError("ambiguous claim identifier across imported stages")
            known_claims[claim.id] = serialized

    resources = _checkpoint_output(source_run, "resources", expected_identity, {})
    if not valid_source_resource_shape(resources):
        raise ValueError("source resources checkpoint shape is invalid")
    source_usage = Usage.model_validate(resources["usage"])
    elapsed = resources["elapsed_seconds"]
    if (
        source_usage.complete
        or resources["dispatched"] is not True
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("source run does not contain the acknowledged unknown dispatch")
    by_stage = resources["by_stage"]
    if not isinstance(by_stage, dict) or set(by_stage) != set(IMPORTED_STAGES):
        raise ValueError("source usage stages do not match the recovery prefix")
    imported: list[ImportedStage] = []
    known_usage: list[Usage] = []
    for stage in IMPORTED_STAGES:
        entries = by_stage.get(stage)
        role = _STAGE_ROLES[stage]
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise ValueError("source stage usage entry is invalid")
        entry = entries[0]
        setting = request.models[role]
        if (
            entry.get("role") != role
            or entry.get("model") != setting.model
            or entry.get("effort") != setting.effort
        ):
            raise ValueError("source model settings do not match the request")
        usage = Usage.model_validate(
            {key: entry[key] for key in Usage.model_fields if key in entry}
        )
        if not usage.complete:
            raise ValueError("imported stage usage must be known")
        known_usage.append(usage)
        imported.append(
            ImportedStage(
                stage=stage,
                role=role,
                payload=deepcopy(stage_payloads[stage]),
                output=stage_outputs[stage].model_dump(mode="json"),
                usage=usage,
            )
        )
    if _sum_usage(known_usage).model_copy(update={"complete": False}) != source_usage:
        raise ValueError("source aggregate usage differs from imported stage counters")

    expected_research = {
        "planner": stage_outputs["planner"].model_dump(mode="json"),
        "challenger": stage_outputs["independent_challenge"].model_dump(mode="json"),
        **{
            stage: stage_outputs[stage].model_dump(mode="json")
            for stage in ("business", "accounting", "expectations", "management")
        },
    }
    if parse_json(_read_regular(source_run / "research.json")) != expected_research:
        raise ValueError("source research artifact differs from imported stages")
    result = _read_object(source_run / "result.json")
    metadata = _read_object(source_run / "run_metadata.json")
    if (
        Usage.model_validate(result.get("usage")) != source_usage
        or Usage.model_validate(metadata.get("usage")) != source_usage
        or metadata.get("usage_by_stage") != by_stage
        or metadata.get("total_tokens") != source_usage.total_tokens
        or metadata.get("identity") != expected_identity
        or result.get("stop_reason") != "stage_failed"
        or metadata.get("failure_type") != "CodexStructuredOutputError"
    ):
        raise ValueError("source result, metadata, and resource ledger disagree")

    outputs = deepcopy(expected_research)
    valuation_payload = _payload(
        request, snapshot, "valuation", "valuation", outputs, ValuationProposal
    )
    valuation_reply = None
    valuation_reply_sha = None
    valuation_diagnostic_elapsed = 0.0
    if valuation_diagnostic is not None:
        valuation_reply, valuation_reply_sha, valuation_diagnostic_elapsed = _validate_diagnostic(
            valuation_diagnostic,
            request_id=request_id,
            source_hashes=source_hashes,
            valuation_payload=valuation_payload,
        )
        _calculate(ValuationProposal.model_validate(valuation_reply.data), request, snapshot)
    return RecoveryPlan(
        source_run=source_run,
        source_identity=expected_identity,
        source_request_identity=request_id,
        source_artifact_hashes=source_hashes,
        source_usage=source_usage,
        source_elapsed_seconds=float(elapsed),
        source_dispatched=True,
        snapshot=snapshot,
        imported_stages=tuple(imported),
        valuation_payload=valuation_payload,
        valuation_reply=valuation_reply,
        valuation_reply_sha256=valuation_reply_sha,
        valuation_diagnostic_elapsed_seconds=valuation_diagnostic_elapsed,
    )


class RecoveryModelService:
    """Serve a validated historical prefix, then delegate explicitly authorized calls."""

    kind = "recovery"

    def __init__(
        self,
        plan: RecoveryPlan,
        live_service,
        *,
        allow_live: bool = False,
        acknowledge_unknown_usage: bool = False,
    ):
        if allow_live is not True or acknowledge_unknown_usage is not True:
            raise ValueError("recovery requires explicit live and unknown-usage authorization")
        self.plan = plan
        self.live_service = live_service
        self._allow_live = allow_live
        self._acknowledge_unknown_usage = acknowledge_unknown_usage
        live_identity = getattr(live_service, "identity", None)
        if not isinstance(live_identity, str) or not _HASH.fullmatch(live_identity):
            raise ValueError("recovery requires an identity-bound live model service")
        self.supports_hard_output_cap = getattr(live_service, "supports_hard_output_cap", False)
        self.identity = digest(
            {
                "service": "explicit-research-recovery-v1",
                "wire": WIRE_SCHEMA_VERSION,
                "live_service_identity": live_identity,
                "source_identity": plan.source_identity,
                "source_artifact_hashes": plan.source_artifact_hashes,
                "valuation_reply_sha256": plan.valuation_reply_sha256,
            }
        )
        self._imported = {item.stage: item for item in plan.imported_stages}
        self._current_calls: list[dict[str, Any]] = []

    @property
    def recovery_context(self) -> dict[str, Any]:
        initial_budget_usage = _add_known_usage(
            self.plan.source_usage,
            self.plan.valuation_reply.usage if self.plan.valuation_reply is not None else None,
        )
        return {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "source_identity": self.plan.source_identity,
            "source_request_identity": self.plan.source_request_identity,
            "source_artifact_hashes": self.plan.source_artifact_hashes,
            "previous_usage": self.plan.source_usage.model_dump(mode="json"),
            "initial_budget_usage": initial_budget_usage.model_dump(mode="json"),
            "previous_elapsed_seconds": (
                self.plan.source_elapsed_seconds + self.plan.valuation_diagnostic_elapsed_seconds
            ),
            "source_elapsed_seconds": self.plan.source_elapsed_seconds,
            "source_dispatched": self.plan.source_dispatched,
            "authorization": {
                "allow_live": self._allow_live,
                "acknowledge_unknown_usage": self._acknowledge_unknown_usage,
                "automatic_recovery": False,
            },
            "scope": {
                "imported_stages": list(IMPORTED_STAGES),
                "first_current_stage": (
                    "reconcile_challenge" if self.plan.valuation_reply is not None else "valuation"
                ),
            },
            "imported_stages": [
                {
                    "stage": item.stage,
                    "role": item.role,
                    "payload_hash": item.payload_hash,
                    "output_hash": digest(item.output),
                    "usage": item.usage.model_dump(mode="json"),
                    "usage_origin": "imported_historical",
                }
                for item in self.plan.imported_stages
            ],
            "valuation_diagnostic": (
                {
                    "reply_sha256": self.plan.valuation_reply_sha256,
                    "payload_hash": digest(self.plan.valuation_payload),
                    "elapsed_seconds": self.plan.valuation_diagnostic_elapsed_seconds,
                    "usage": self.plan.valuation_reply.usage.model_dump(mode="json"),
                }
                if self.plan.valuation_reply is not None
                else None
            ),
            "current_calls": deepcopy(self._current_calls),
        }

    def restore_recovery_context(self, saved: dict[str, Any]) -> None:
        expected = self.recovery_context
        static_keys = set(expected) - {"current_calls"}
        if not isinstance(saved, dict) or any(
            saved.get(key) != expected[key] for key in static_keys
        ):
            raise ValueError("recovery checkpoint provenance mismatch")
        calls = saved.get("current_calls")
        if not isinstance(calls, list) or not all(isinstance(item, dict) for item in calls):
            raise ValueError("recovery checkpoint current-call provenance is invalid")
        self._current_calls = deepcopy(calls)

    @staticmethod
    def _engine_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"timeout_seconds", "max_output_tokens"}
        }

    def call_origin(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> str:
        engine_payload = self._engine_payload(payload)
        stage = engine_payload.get("stage")
        imported = self._imported.get(stage)
        if imported is not None:
            if role != imported.role or digest(engine_payload) != imported.payload_hash:
                raise ValueError("imported stage payload mismatch")
            return "imported_historical"
        if stage == "valuation":
            if role != "valuation" or digest(engine_payload) != digest(self.plan.valuation_payload):
                raise ValueError("valuation recovery payload mismatch")
            if self.plan.valuation_reply is not None:
                if any(
                    item.get("stage") == "valuation"
                    and item.get("origin") == "validated_diagnostic"
                    for item in self._current_calls
                ):
                    raise ValueError("validated valuation diagnostic was already consumed")
                return "validated_diagnostic"
        return "current_live"

    def complete(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> ModelReply:
        origin = self.call_origin(role, payload, request)
        engine_payload = self._engine_payload(payload)
        stage = engine_payload["stage"]
        if origin == "imported_historical":
            item = self._imported[stage]
            return ModelReply(data=deepcopy(item.output), usage=item.usage)
        if origin == "validated_diagnostic":
            reply = self.plan.valuation_reply
            assert reply is not None
        else:
            try:
                reply = ModelReply.model_validate(
                    self.live_service.complete(role, payload, request)
                )
            except BaseException:
                self._current_calls.append(
                    {
                        "stage": stage,
                        "role": role,
                        "payload_hash": digest(engine_payload),
                        "origin": origin,
                        "status": "failed_usage_unknown",
                    }
                )
                raise
        self._current_calls.append(
            {
                "stage": stage,
                "role": role,
                "payload_hash": digest(engine_payload),
                "origin": origin,
                "status": "completed" if reply.usage.complete else "completed_usage_unknown",
                "usage": reply.usage.model_dump(mode="json"),
            }
        )
        return reply


def assert_source_unchanged(plan: RecoveryPlan) -> None:
    if source_artifact_hashes(plan.source_run) != plan.source_artifact_hashes:
        raise ValueError("source run changed during recovery")
