"""Offline preparation and explicit same-case recovery for one fixed case prefix.

This module does not create a live model client.  It validates the exact six-stage
case prefix with the current engine, preserves the source's unsettled-dispatch
state, and requires a separately content-bound incremental authorization before a
``CaseRecoveryModelService`` can delegate any continuation call.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import stat
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator

from .contracts import Budget, Contract, EvidenceSnapshot, ResearchRequest, Usage
from .engine import run_research
from .models import CodexModelService
from .replay import SnapshotEvidenceService
from .resource_diagnostics import valid_source_resource_shape
from .services import ModelReply, ResearchServices
from .storage import atomic_write, canonical_json, digest, parse_json, request_identity
from .wire import WIRE_SCHEMA_VERSION

CASE_RECOVERY_SCHEMA_VERSION = 1
CASE_RECOVERY_IMPORTED_STAGES = (
    "independent_challenge",
    "planner",
    "business",
    "accounting",
    "expectations",
    "management",
)
_STAGE_ROLES = {
    "independent_challenge": "challenger",
    "planner": "planner",
    "business": "business",
    "accounting": "accounting",
    "expectations": "expectations",
    "management": "management",
}
_FIRST_CURRENT_STAGE = "reconcile_challenge"
_FIRST_CURRENT_ROLE = "challenger"
_MAX_FILE_BYTES = 32 * 1024 * 1024
_HASH_PATTERN = r"^[a-f0-9]{64}$"


class CaseRecoveryAuthorization(Contract):
    """A new authorization for incremental continuation spend only."""

    authorization_id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:-]+$")
    authorized_at: AwareDatetime
    plan_sha256: str = Field(pattern=_HASH_PATTERN)
    continuation_request_identity: str = Field(pattern=_HASH_PATTERN)
    incremental_budget: Budget
    acknowledge_source_usage_incomplete: Literal[True]
    authorize_live_continuation: Literal[True]

    @field_validator(
        "acknowledge_source_usage_incomplete",
        "authorize_live_continuation",
        mode="before",
    )
    @classmethod
    def literal_true_boolean(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("case-recovery acknowledgements must be boolean true")
        return value

    @property
    def authorization_sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class ImportedCaseStage:
    stage: str
    role: str
    payload_hash: str
    output_hash: str
    output: dict[str, Any]
    usage: Usage


@dataclass(frozen=True)
class CaseRecoveryPlan:
    source_request: ResearchRequest
    source_run: Path
    source_codex_home: Path
    source_request_identity: str
    source_model_identity: str
    source_identity: str
    source_artifact_hashes: dict[str, str]
    frozen_input_hashes: dict[str, str]
    frozen_inputs: dict[str, bytes]
    source_checkpoint_bytes: dict[str, bytes]
    source_known_usage: Usage
    source_elapsed_seconds: float
    imported_stages: tuple[ImportedCaseStage, ...]

    @property
    def source_usage(self) -> Usage:
        return self.source_known_usage.model_copy(update={"complete": False})

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": CASE_RECOVERY_SCHEMA_VERSION,
            "mode": "same_case_fixed_six_stage_prefix",
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "source_request_identity": self.source_request_identity,
            "source_model_identity": self.source_model_identity,
            "source_identity": self.source_identity,
            "source_artifact_hashes": self.source_artifact_hashes,
            "frozen_input_hashes": self.frozen_input_hashes,
            "source_known_usage": self.source_known_usage.model_dump(mode="json"),
            "source_usage": self.source_usage.model_dump(mode="json"),
            "source_elapsed_seconds": self.source_elapsed_seconds,
            "source_dispatched": True,
            "whole_run_usage_complete": False,
            "unknown_dispatched_call_usage": "unknown_not_zero",
            "imported_stages": [
                {
                    "stage": item.stage,
                    "role": item.role,
                    "payload_hash": item.payload_hash,
                    "output_hash": item.output_hash,
                    "usage": item.usage.model_dump(mode="json"),
                }
                for item in self.imported_stages
            ],
        }


@dataclass(frozen=True)
class VerifiedCaseRecoveryPlan:
    prepared: CaseRecoveryPlan
    next_stage: str
    next_role: str
    next_payload_hash: str

    @property
    def plan_sha256(self) -> str:
        return digest(self.manifest())

    def manifest(self) -> dict[str, Any]:
        return {
            **self.prepared.manifest(),
            "verification": {
                "engine": "current_run_research_offline",
                "served_stage_count": len(CASE_RECOVERY_IMPORTED_STAGES),
                "next_stage": self.next_stage,
                "next_role": self.next_role,
                "next_payload_hash": self.next_payload_hash,
                "next_reply_dispatched": False,
            },
        }


@dataclass(frozen=True)
class AuthorizedCaseRecoveryPlan:
    verified: VerifiedCaseRecoveryPlan
    authorization: CaseRecoveryAuthorization
    continuation_request: ResearchRequest


@dataclass(frozen=True)
class CaseRecoveryCapsule:
    directory: Path
    plan_sha256: str
    manifest_sha256: str


class _PrefixVerificationBoundary(Exception):
    """Expected offline stop before the first non-imported model reply."""


def _read_regular(path: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("case-recovery inputs must be regular files")
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("case-recovery input exceeds size allowance")
    return content


def _read_object(path: Path) -> dict[str, Any]:
    value = parse_json(_read_regular(path))
    if not isinstance(value, dict):
        raise ValueError("case-recovery JSON input must be an object")
    return value


@contextmanager
def _source_lock(source_run: Path):
    lock_path = source_run / ".research.lock"
    descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("source research lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("source research run is in use") from exc
        yield
    finally:
        os.close(descriptor)


def _checkpoint_record(
    content: bytes,
    *,
    identity: str,
    expected_inputs_hash: str | None = None,
) -> dict[str, Any]:
    record = parse_json(content)
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "identity",
        "inputs_hash",
        "output",
        "output_hash",
    }:
        raise ValueError("source stage checkpoint shape is invalid")
    if (
        record["schema_version"] != 1
        or record["identity"] != identity
        or record["output_hash"] != digest(record["output"])
        or (expected_inputs_hash is not None and record["inputs_hash"] != expected_inputs_hash)
    ):
        raise ValueError("source stage checkpoint identity or hash is invalid")
    return record


def _sum_usage(entries: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(item.input_tokens for item in entries),
        output_tokens=sum(item.output_tokens for item in entries),
        cached_input_tokens=sum(item.cached_input_tokens for item in entries),
        reasoning_output_tokens=sum(item.reasoning_output_tokens for item in entries),
    )


def _research_settings(request: ResearchRequest) -> dict[str, Any]:
    return request.model_dump(
        mode="json",
        exclude={
            "output_dir",
            "dossier_dir",
            "budget",
            "evidence_path",
            "financial_case_path",
            "prior_dossier_path",
        },
    )


def _bound_continuation_identity(request: ResearchRequest, frozen: dict[str, bytes]) -> str:
    return digest(
        {
            "request_identity": request_identity(request, frozen),
            "output_dir": str(request.output_dir.resolve()),
        }
    )


def _current_source_hashes(plan: CaseRecoveryPlan) -> tuple[dict[str, str], dict[str, str]]:
    checkpoint_hashes = {
        name: hashlib.sha256(_read_regular(plan.source_run / name)).hexdigest()
        for name in plan.source_artifact_hashes
    }
    input_hashes = {
        name: hashlib.sha256(_read_regular(Path(getattr(plan.source_request, name)))).hexdigest()
        for name in plan.frozen_input_hashes
    }
    return checkpoint_hashes, input_hashes


def _validate_prepared_plan_content(plan: CaseRecoveryPlan) -> None:
    if {
        name: hashlib.sha256(content).hexdigest()
        for name, content in plan.source_checkpoint_bytes.items()
    } != plan.source_artifact_hashes:
        raise ValueError("prepared case-recovery checkpoint bytes changed")
    if {
        name: hashlib.sha256(content).hexdigest() for name, content in plan.frozen_inputs.items()
    } != plan.frozen_input_hashes:
        raise ValueError("prepared case-recovery frozen input bytes changed")
    if tuple(item.stage for item in plan.imported_stages) != CASE_RECOVERY_IMPORTED_STAGES:
        raise ValueError("prepared case-recovery stage sequence changed")
    for item in plan.imported_stages:
        record = _checkpoint_record(
            plan.source_checkpoint_bytes[f"stages/{item.stage}.json"],
            identity=plan.source_identity,
        )
        if (
            item.role != _STAGE_ROLES[item.stage]
            or item.payload_hash != record["inputs_hash"]
            or item.output_hash != record["output_hash"]
            or item.output != record["output"]
            or digest(item.output) != item.output_hash
        ):
            raise ValueError("verified case-recovery plan content changed")


def assert_case_recovery_source_unchanged(
    plan: CaseRecoveryPlan | VerifiedCaseRecoveryPlan | AuthorizedCaseRecoveryPlan,
) -> None:
    if isinstance(plan, AuthorizedCaseRecoveryPlan):
        prepared = plan.verified.prepared
    elif isinstance(plan, VerifiedCaseRecoveryPlan):
        prepared = plan.prepared
    else:
        prepared = plan
    _validate_prepared_plan_content(prepared)
    with _source_lock(prepared.source_run):
        checkpoint_hashes, input_hashes = _current_source_hashes(prepared)
    if (
        checkpoint_hashes != prepared.source_artifact_hashes
        or input_hashes != prepared.frozen_input_hashes
    ):
        raise ValueError("case-recovery source or frozen inputs changed")


def prepare_case_recovery(
    source_request: ResearchRequest,
    source_run: Path,
    source_codex_home: Path,
) -> CaseRecoveryPlan:
    """Validate the immutable shape and accounting of a case-backed failed run."""
    source_request = ResearchRequest.model_validate_json(
        source_request.model_dump_json(warnings="error")
    )
    source_run = Path(source_run)
    source_codex_home = Path(source_codex_home)
    if source_run.is_symlink() or not source_run.is_dir():
        raise ValueError("source run must be a real directory")
    if source_codex_home.is_symlink() or not source_codex_home.is_dir():
        raise ValueError("source Codex home must be a real directory")
    source_run = source_run.resolve()
    source_codex_home = source_codex_home.resolve()
    if (
        source_request.backend != "codex"
        or source_request.output_dir.resolve() != source_run
        or source_request.financial_case_path is None
        or source_request.quality_revision != "evidence-led-bounded"
        or source_request.valuation_method != "fcff"
    ):
        raise ValueError("source request is not the bounded Codex financial-case run")
    if (source_run / "result.json").exists():
        raise ValueError("case recovery requires the preserved parent-failure checkpoint set")
    stages_dir = source_run / "stages"
    if stages_dir.is_symlink() or not stages_dir.is_dir():
        raise ValueError("source stages must be a real directory")

    input_names = tuple(
        name
        for name in ("evidence_path", "prior_dossier_path", "financial_case_path")
        if getattr(source_request, name) is not None
    )
    if set(input_names) != {"evidence_path", "financial_case_path"}:
        raise ValueError("fixed case recovery requires only frozen evidence and case inputs")

    with _source_lock(source_run):
        frozen_inputs = {
            name: _read_regular(Path(getattr(source_request, name))) for name in input_names
        }
        source_request_id = request_identity(source_request, frozen_inputs)
        source_model_id = CodexModelService(source_codex_home).identity
        source_identity = digest({"request": source_request_id, "model_service": source_model_id})
        manifest = _read_object(source_run / "research_checkpoint.json")
        if manifest != {"schema_version": 1, "identity": source_identity}:
            raise ValueError("source identity does not match request, model, and current wire")

        relative_names = (
            "research_checkpoint.json",
            "stages/evidence.json",
            "stages/resources.json",
            *(f"stages/{stage}.json" for stage in CASE_RECOVERY_IMPORTED_STAGES),
        )
        checkpoint_bytes = {name: _read_regular(source_run / name) for name in relative_names}
        artifact_hashes = {
            name: hashlib.sha256(content).hexdigest() for name, content in checkpoint_bytes.items()
        }

        evidence_record = _checkpoint_record(
            checkpoint_bytes["stages/evidence.json"],
            identity=source_identity,
            expected_inputs_hash=digest({}),
        )
        EvidenceSnapshot.model_validate(evidence_record["output"])
        if canonical_json(evidence_record["output"]) != frozen_inputs["evidence_path"]:
            raise ValueError("source evidence checkpoint differs from frozen evidence")

        resource_record = _checkpoint_record(
            checkpoint_bytes["stages/resources.json"],
            identity=source_identity,
            expected_inputs_hash=digest({}),
        )
        resources = resource_record["output"]
        if not valid_source_resource_shape(resources):
            raise ValueError("source resource checkpoint shape is invalid")
        known_usage = Usage.model_validate(resources["usage"])
        elapsed = resources["elapsed_seconds"]
        if (
            not known_usage.complete
            or resources["dispatched"] is not True
            or isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ValueError("source must preserve known counters and an unsettled dispatch")
        by_stage = resources["by_stage"]
        if not isinstance(by_stage, dict) or set(by_stage) != set(CASE_RECOVERY_IMPORTED_STAGES):
            raise ValueError("source usage must contain exactly the fixed six-stage prefix")

        imported: list[ImportedCaseStage] = []
        stage_usages: list[Usage] = []
        for stage in CASE_RECOVERY_IMPORTED_STAGES:
            record = _checkpoint_record(
                checkpoint_bytes[f"stages/{stage}.json"], identity=source_identity
            )
            entries = by_stage[stage]
            role = _STAGE_ROLES[stage]
            if (
                not isinstance(entries, list)
                or len(entries) != 1
                or not isinstance(entries[0], dict)
            ):
                raise ValueError("source stage usage entry is invalid")
            entry = entries[0]
            setting = source_request.models[role]
            if (
                entry.get("role") != role
                or entry.get("model") != setting.model
                or entry.get("effort") != setting.effort
                or entry.get("usage_origin") != "current_live"
            ):
                raise ValueError("source stage model or usage origin is inconsistent")
            usage = Usage.model_validate(
                {key: entry[key] for key in Usage.model_fields if key in entry}
            )
            if not usage.complete:
                raise ValueError("imported stage usage must be settled")
            if not isinstance(record["output"], dict):
                raise ValueError("source stage output must be an object")
            stage_usages.append(usage)
            imported.append(
                ImportedCaseStage(
                    stage=stage,
                    role=role,
                    payload_hash=record["inputs_hash"],
                    output_hash=record["output_hash"],
                    output=deepcopy(record["output"]),
                    usage=usage,
                )
            )
        if _sum_usage(stage_usages) != known_usage:
            raise ValueError("source aggregate counters differ from the six settled calls")

        plan = CaseRecoveryPlan(
            source_request=source_request,
            source_run=source_run,
            source_codex_home=source_codex_home,
            source_request_identity=source_request_id,
            source_model_identity=source_model_id,
            source_identity=source_identity,
            source_artifact_hashes=artifact_hashes,
            frozen_input_hashes={
                name: hashlib.sha256(content).hexdigest() for name, content in frozen_inputs.items()
            },
            frozen_inputs=frozen_inputs,
            source_checkpoint_bytes=checkpoint_bytes,
            source_known_usage=known_usage,
            source_elapsed_seconds=float(elapsed),
            imported_stages=tuple(imported),
        )
        current_hashes = _current_source_hashes(plan)
        if current_hashes != (artifact_hashes, plan.frozen_input_hashes):
            raise ValueError("case-recovery source changed while it was inspected")
    return plan


class _PrefixVerifierService:
    kind = "case_recovery_prefix_verifier"
    supports_hard_output_cap = False

    def __init__(self, plan: CaseRecoveryPlan):
        self.identity = plan.source_model_identity
        self._stages = plan.imported_stages
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _engine_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"timeout_seconds", "max_output_tokens"}
        }

    def complete(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> ModelReply:
        engine_payload = self._engine_payload(payload)
        call = {
            "stage": engine_payload.get("stage"),
            "role": role,
            "payload_hash": digest(engine_payload),
        }
        self.calls.append(call)
        index = len(self.calls) - 1
        if index == len(self._stages):
            raise _PrefixVerificationBoundary("offline prefix boundary reached")
        if index > len(self._stages):
            raise ValueError("offline verifier crossed the fixed recovery boundary")
        expected = self._stages[index]
        if (
            call["stage"] != expected.stage
            or role != expected.role
            or call["payload_hash"] != expected.payload_hash
        ):
            raise ValueError("current engine payload differs from the source checkpoint")
        return ModelReply(data=deepcopy(expected.output), usage=expected.usage)


def verify_case_recovery_prefix(
    plan: CaseRecoveryPlan,
) -> VerifiedCaseRecoveryPlan:
    """Replay current orchestration offline and stop before the seventh reply."""
    assert_case_recovery_source_unchanged(plan)
    with tempfile.TemporaryDirectory(prefix="case-recovery-verify-") as temporary:
        root = Path(temporary)
        input_paths: dict[str, Path] = {}
        for name, content in plan.frozen_inputs.items():
            path = root / "inputs" / f"{name}.json"
            atomic_write(path, content)
            input_paths[name] = path
        request = plan.source_request.model_copy(
            update={
                "output_dir": root / "run",
                "dossier_dir": None,
                **input_paths,
            }
        )
        snapshot = EvidenceSnapshot.model_validate(parse_json(plan.frozen_inputs["evidence_path"]))
        verifier = _PrefixVerifierService(plan)
        result = run_research(
            request,
            ResearchServices(SnapshotEvidenceService(snapshot), verifier),
        )
        if (
            result.stop_reason != "stage_failed"
            or len(verifier.calls) != len(CASE_RECOVERY_IMPORTED_STAGES) + 1
        ):
            raise ValueError("offline case-prefix verification did not stop at call seven")
        next_call = verifier.calls[-1]
        if next_call["stage"] != _FIRST_CURRENT_STAGE or next_call["role"] != _FIRST_CURRENT_ROLE:
            raise ValueError("current engine continuation boundary changed")
        for item in plan.imported_stages:
            replayed = _read_object(root / "run" / "stages" / f"{item.stage}.json")
            if (
                replayed.get("inputs_hash") != item.payload_hash
                or replayed.get("output_hash") != item.output_hash
            ):
                raise ValueError("offline replay checkpoint differs from source")
    assert_case_recovery_source_unchanged(plan)
    return VerifiedCaseRecoveryPlan(
        prepared=plan,
        next_stage=str(next_call["stage"]),
        next_role=str(next_call["role"]),
        next_payload_hash=str(next_call["payload_hash"]),
    )


def _validate_continuation_request(
    plan: VerifiedCaseRecoveryPlan,
    authorization: CaseRecoveryAuthorization,
    request: ResearchRequest,
    frozen: dict[str, bytes],
) -> None:
    prepared = plan.prepared
    if authorization.plan_sha256 != plan.plan_sha256:
        raise ValueError("case-recovery authorization is for a different plan")
    if request.output_dir.resolve() == prepared.source_run:
        raise ValueError("case recovery requires a new destination")
    if request.dossier_dir is not None:
        raise ValueError("case recovery cannot write or promote a dossier")
    output = request.output_dir.resolve()
    protected_directories = {
        prepared.source_run,
        *(
            Path(getattr(prepared.source_request, name)).resolve().parent
            for name in prepared.frozen_inputs
        ),
    }
    if any(
        output == protected or output.is_relative_to(protected) or protected.is_relative_to(output)
        for protected in protected_directories
    ):
        raise ValueError("case-recovery output overlaps source directories")
    if request.budget != authorization.incremental_budget:
        raise ValueError("continuation request does not use the authorized incremental budget")
    if _research_settings(request) != _research_settings(prepared.source_request):
        raise ValueError("continuation changed source research settings")
    if set(frozen) != set(prepared.frozen_inputs):
        raise ValueError("continuation changed the frozen input set")
    if any(frozen[name] != prepared.frozen_inputs[name] for name in frozen):
        raise ValueError("continuation changed frozen evidence or reviewed case bytes")
    identity = _bound_continuation_identity(request, frozen)
    if identity != authorization.continuation_request_identity:
        raise ValueError("continuation request identity does not match authorization")


def _revalidate_verified_plan(
    plan: VerifiedCaseRecoveryPlan,
) -> VerifiedCaseRecoveryPlan:
    if not isinstance(plan, VerifiedCaseRecoveryPlan):
        raise ValueError("case recovery requires a verified plan")
    prepared = plan.prepared
    _validate_prepared_plan_content(prepared)
    fresh = verify_case_recovery_prefix(
        prepare_case_recovery(
            prepared.source_request,
            prepared.source_run,
            prepared.source_codex_home,
        )
    )
    if fresh.manifest() != plan.manifest() or fresh.plan_sha256 != plan.plan_sha256:
        raise ValueError("verified case-recovery plan content changed")
    return fresh


def authorize_case_recovery(
    plan: VerifiedCaseRecoveryPlan,
    authorization: CaseRecoveryAuthorization,
    continuation_request: ResearchRequest,
    frozen: dict[str, bytes] | None = None,
) -> AuthorizedCaseRecoveryPlan:
    """Bind a separately supplied authorization to an exact continuation request."""
    plan = _revalidate_verified_plan(plan)
    authorization = CaseRecoveryAuthorization.model_validate_json(
        authorization.model_dump_json(warnings="error")
    )
    continuation_request = ResearchRequest.model_validate_json(
        continuation_request.model_dump_json(warnings="error")
    )
    if frozen is None:
        frozen = {
            name: _read_regular(Path(getattr(continuation_request, name)))
            for name in plan.prepared.frozen_inputs
        }
    _validate_continuation_request(plan, authorization, continuation_request, frozen)
    assert_case_recovery_source_unchanged(plan)
    return AuthorizedCaseRecoveryPlan(plan, authorization, continuation_request)


class CaseRecoveryModelService:
    """Serve the exact case prefix, then delegate under the fresh authorization."""

    kind = "case_recovery"

    def __init__(self, plan: AuthorizedCaseRecoveryPlan, live_service):
        if not isinstance(plan, AuthorizedCaseRecoveryPlan):
            raise ValueError("case recovery requires an authorized plan")
        verified = _revalidate_verified_plan(plan.verified)
        authorization = CaseRecoveryAuthorization.model_validate_json(
            plan.authorization.model_dump_json(warnings="error")
        )
        continuation_request = ResearchRequest.model_validate_json(
            plan.continuation_request.model_dump_json(warnings="error")
        )
        frozen = {
            name: _read_regular(Path(getattr(continuation_request, name)))
            for name in verified.prepared.frozen_inputs
        }
        _validate_continuation_request(verified, authorization, continuation_request, frozen)
        self.plan = AuthorizedCaseRecoveryPlan(verified, authorization, continuation_request)
        self.live_service = live_service
        live_identity = getattr(live_service, "identity", None)
        if (
            not isinstance(live_identity, str)
            or len(live_identity) != 64
            or any(character not in "0123456789abcdef" for character in live_identity)
        ):
            raise ValueError("case recovery requires an identity-bound live service")
        self.supports_hard_output_cap = getattr(live_service, "supports_hard_output_cap", False)
        self.identity = digest(
            {
                "service": "explicit-same-case-recovery-v1",
                "wire": WIRE_SCHEMA_VERSION,
                "plan_sha256": verified.plan_sha256,
                "authorization_sha256": authorization.authorization_sha256,
                "continuation_request_identity": (authorization.continuation_request_identity),
                "live_service_identity": live_identity,
            }
        )
        self._imported = {
            item.stage: {
                "stage": item.stage,
                "role": item.role,
                "payload_hash": item.payload_hash,
                "output_hash": item.output_hash,
                "output_bytes": canonical_json(item.output),
                "usage": item.usage,
            }
            for item in verified.prepared.imported_stages
        }
        self._current_calls: list[dict[str, Any]] = []
        self._validated_request_identity: str | None = None

    @property
    def case_recovery_context(self) -> dict[str, Any]:
        prepared = self.plan.verified.prepared
        source_usage = prepared.source_usage.model_dump(mode="json")
        return {
            "schema_version": CASE_RECOVERY_SCHEMA_VERSION,
            "mode": "same_case_fixed_six_stage_prefix",
            "plan_sha256": self.plan.verified.plan_sha256,
            "source_identity": prepared.source_identity,
            "source_request_identity": prepared.source_request_identity,
            "source_artifact_hashes": prepared.source_artifact_hashes,
            "source_usage": source_usage,
            "source_known_usage": prepared.source_known_usage.model_dump(mode="json"),
            "previous_usage": source_usage,
            "initial_budget_usage": Usage().model_dump(mode="json"),
            "previous_elapsed_seconds": 0,
            "source_elapsed_seconds": prepared.source_elapsed_seconds,
            "source_elapsed_is_last_checkpoint_not_total": True,
            "source_dispatched": True,
            "whole_run_usage_complete": False,
            "authorization": {
                **self.plan.authorization.model_dump(mode="json"),
                "authorization_sha256": self.plan.authorization.authorization_sha256,
                "automatic_recovery": False,
                "budget_scope": "new_incremental_calls_only",
            },
            "scope": {
                "imported_stages": list(CASE_RECOVERY_IMPORTED_STAGES),
                "first_current_stage": _FIRST_CURRENT_STAGE,
            },
            "imported_stages": [
                {
                    "stage": item.stage,
                    "role": item.role,
                    "payload_hash": item.payload_hash,
                    "output_hash": item.output_hash,
                    "usage": item.usage.model_dump(mode="json"),
                    "usage_origin": "imported_historical",
                }
                for item in prepared.imported_stages
            ],
            "current_calls": deepcopy(self._current_calls),
        }

    def validate_request(self, request: ResearchRequest, frozen: dict[str, bytes]) -> None:
        authorization = CaseRecoveryAuthorization.model_validate_json(
            self.plan.authorization.model_dump_json(warnings="error")
        )
        request = ResearchRequest.model_validate_json(request.model_dump_json(warnings="error"))
        _validate_continuation_request(self.plan.verified, authorization, request, frozen)
        assert_case_recovery_source_unchanged(self.plan)
        self._validated_request_identity = _bound_continuation_identity(request, frozen)

    def restore_recovery_context(self, saved: dict[str, Any]) -> None:
        expected = self.case_recovery_context
        static_keys = set(expected) - {"current_calls"}
        if not isinstance(saved, dict) or any(
            saved.get(key) != expected[key] for key in static_keys
        ):
            raise ValueError("case-recovery checkpoint provenance mismatch")
        calls = saved.get("current_calls")
        if not isinstance(calls, list) or not all(isinstance(item, dict) for item in calls):
            raise ValueError("case-recovery current-call provenance is invalid")
        self._current_calls = deepcopy(calls)

    @staticmethod
    def _engine_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"timeout_seconds", "max_output_tokens"}
        }

    def _require_validated_request(self, request: ResearchRequest) -> None:
        if (
            self._validated_request_identity
            != self.plan.authorization.continuation_request_identity
            or request != self.plan.continuation_request
        ):
            raise ValueError("case-recovery request was not validated by the engine")

    def call_origin(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> str:
        self._require_validated_request(request)
        engine_payload = self._engine_payload(payload)
        stage = engine_payload.get("stage")
        imported = self._imported.get(stage)
        if imported is not None:
            if (
                role != imported["role"]
                or digest(engine_payload) != imported["payload_hash"]
                or hashlib.sha256(imported["output_bytes"]).hexdigest() != imported["output_hash"]
            ):
                raise ValueError("case-recovery imported payload mismatch")
            return "imported_historical"
        if not self._current_calls and (
            stage != self.plan.verified.next_stage or role != self.plan.verified.next_role
        ):
            raise ValueError("case-recovery first continuation stage changed")
        if (
            not self._current_calls
            and digest(engine_payload) != self.plan.verified.next_payload_hash
        ):
            raise ValueError("case-recovery continuation payload changed")
        assert_case_recovery_source_unchanged(self.plan)
        return "current_live"

    def complete(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> ModelReply:
        origin = self.call_origin(role, payload, request)
        engine_payload = self._engine_payload(payload)
        stage = str(engine_payload["stage"])
        if origin == "imported_historical":
            item = self._imported[stage]
            return ModelReply(data=parse_json(item["output_bytes"]), usage=item["usage"])
        assert_case_recovery_source_unchanged(self.plan)
        try:
            reply = ModelReply.model_validate(self.live_service.complete(role, payload, request))
        except BaseException:
            self._current_calls.append(
                {
                    "stage": stage,
                    "role": role,
                    "payload_hash": digest(engine_payload),
                    "origin": "current_live",
                    "status": "failed_usage_unknown",
                }
            )
            raise
        self._current_calls.append(
            {
                "stage": stage,
                "role": role,
                "payload_hash": digest(engine_payload),
                "origin": "current_live",
                "status": ("completed" if reply.usage.complete else "completed_usage_unknown"),
                "usage": reply.usage.model_dump(mode="json"),
            }
        )
        return reply


def write_case_recovery_capsule(
    plan: VerifiedCaseRecoveryPlan,
    destination: Path,
) -> CaseRecoveryCapsule:
    """Copy the exact verified source bytes into a new offline-only capsule."""
    # The dataclasses are frozen only shallowly. Rebuild from the immutable
    # source before publishing so nested request/model mutations cannot produce
    # a request file that disagrees with the plan identity in the manifest.
    plan = _revalidate_verified_plan(plan)
    destination = Path(destination)
    destination_resolved = destination.resolve()
    prepared = plan.prepared
    protected_directories = {
        prepared.source_run,
        *(
            Path(getattr(prepared.source_request, name)).resolve().parent
            for name in prepared.frozen_inputs
        ),
    }
    if any(
        destination_resolved == protected or destination_resolved.is_relative_to(protected)
        for protected in protected_directories
    ):
        raise ValueError("case-recovery capsule cannot be written under source directories")
    parent = destination_resolved.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        destination_resolved.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("case-recovery capsule destination must not exist") from exc
    try:
        files: dict[str, bytes] = {
            "source/request.json": canonical_json(prepared.source_request),
            **{
                f"source/{name}": content
                for name, content in prepared.source_checkpoint_bytes.items()
            },
            **{f"inputs/{name}.json": content for name, content in prepared.frozen_inputs.items()},
        }
        authorization_request = {
            "schema_version": 1,
            "status": "explicit_authorization_required",
            "plan_sha256": plan.plan_sha256,
            "continuation_request_identity": None,
            "incremental_budget": None,
            "required_acknowledgements": {
                "acknowledge_source_usage_incomplete": True,
                "authorize_live_continuation": True,
            },
            "source_known_usage": prepared.source_known_usage.model_dump(mode="json"),
            "cumulative_usage_complete": False,
            "unknown_dispatched_call_usage": "unknown_not_zero",
            "budget_scope": "new_incremental_calls_only",
            "automatic_or_live_action": False,
        }
        files["authorization_request.json"] = canonical_json(authorization_request)
        for name, content in files.items():
            atomic_write(destination_resolved / name, content)
        capsule_manifest = {
            **plan.manifest(),
            "plan_sha256": plan.plan_sha256,
            "capsule_files": {
                name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
            },
        }
        manifest_bytes = canonical_json(capsule_manifest)
        # Exclusive directory creation prevents overwrites; manifest publication
        # is the completion marker for readers of the capsule.
        atomic_write(destination_resolved / "manifest.json", manifest_bytes)
    except BaseException:
        # Leave an incomplete, exclusively owned directory for diagnosis.  Never
        # remove or replace a path that another process could have populated.
        raise
    assert_case_recovery_source_unchanged(plan)
    return CaseRecoveryCapsule(
        directory=destination_resolved,
        plan_sha256=plan.plan_sha256,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def continuation_request_identity(
    plan: VerifiedCaseRecoveryPlan,
    request: ResearchRequest,
    frozen: dict[str, bytes] | None = None,
) -> str:
    """Compute the identity an explicit authorization must name, without authorizing it."""
    if frozen is None:
        frozen = {
            name: _read_regular(Path(getattr(request, name)))
            for name in plan.prepared.frozen_inputs
        }
    identity = _bound_continuation_identity(request, frozen)
    placeholder = CaseRecoveryAuthorization(
        authorization_id="identity-check-only",
        authorized_at=datetime.now().astimezone(),
        plan_sha256=plan.plan_sha256,
        continuation_request_identity=identity,
        incremental_budget=request.budget,
        acknowledge_source_usage_incomplete=True,
        authorize_live_continuation=True,
    )
    _validate_continuation_request(plan, placeholder, request, frozen)
    return identity
