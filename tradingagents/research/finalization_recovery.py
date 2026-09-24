"""Content-bound continuation from one factual-reviewed reader candidate.

Preparation is entirely offline.  It accepts only the deliberately narrow first
scope: a current-wire, bounded financial-case run in English with no optional
follow-up cycles or translated reader.  Live delegation is possible only after
a separate authorization binds the exact plan, destination and incremental
budget.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import uuid
from collections import Counter
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator

from .case_context import load_case_context
from .contracts import Budget, Contract, EvidenceSnapshot, ResearchRequest, Usage
from .evidence import validate_snapshot
from .reader_revision import (
    FROZEN_REVIEW_STAGE,
    GENERATION_PATTERN,
    READER_REVISION_POLICY,
    REVISE_STAGE,
    REVISED_REVIEW_STAGE,
    VERIFICATION_REPAIR_POLICY,
    generation_stages,
    generic_coverage_stage,
    generic_review_stage,
)
from .review_lifecycle import compound_coverage_issues
from .revision_contracts import (
    PINNED_CONTRACTS,
    PINNED_POLICIES,
    V3_CONTRACT,
    V4_CONTRACT,
    V5_CONTRACT,
    V6_CONTRACT,
    revision_contract,
)
from .revision_deferred import deferred_coverage_eligibility
from .revision_inventory import assert_inventory_prefix_proof, inventory_finding_hashes
from .revision_inventory_state import inventory_contexts_from_source, prior_inventory_lineage
from .revision_lineage import lineage_sha256, source_revision_contracts
from .revision_pending import (
    pending_contexts_from_source,
    pending_entries,
    prior_pending_context_lineage,
)
from .revision_witness_selection import revision_witness_catalog
from .services import ModelReply
from .storage import (
    ENGINE_VERSION,
    canonical_json,
    digest,
    parse_json,
    request_identity,
)
from .wire import WIRE_SCHEMA_VERSION

FINALIZATION_RECOVERY_SCHEMA_VERSION = 1
FINALIZATION_CHECKPOINT_NAME = "finalization_checkpoint.json"
_HASH_PATTERN = r"^[a-f0-9]{64}$"
_MAX_FILE_BYTES = 32 * 1024 * 1024
_RUNTIME_PAYLOAD_FIELDS = frozenset({"timeout_seconds", "max_output_tokens"})
_SUPPORTED_CANDIDATE_STAGES = frozenset({"verify_report", "verify_repaired_report"})
_CHECKPOINT_KEYS = {
    "schema_version",
    "engine_version",
    "wire_schema_version",
    "request",
    "model_service_identity",
    "run_identity",
    "frozen_input_hashes",
    "stages",
    "usage",
    "dispatched",
    "candidate",
    "candidate_review_stage",
}


def _generic_revision_contract_sha256() -> str:
    return V3_CONTRACT.sha256


class FinalizationRecoveryAuthorization(Contract):
    """A human-supplied authorization for new continuation calls only."""

    authorization_id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:-]+$")
    authorized_at: AwareDatetime
    plan_sha256: str = Field(pattern=_HASH_PATTERN)
    new_request_identity: str = Field(pattern=_HASH_PATTERN)
    incremental_budget: Budget
    authorize_live_continuation: Literal[True]

    @field_validator("authorize_live_continuation", mode="before")
    @classmethod
    def literal_true_boolean(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("live continuation authorization must be boolean true")
        return value

    @property
    def authorization_sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class ImportedFinalizationStage:
    stage: str
    role: str
    inputs_hash: str
    output_hash: str
    output: dict[str, Any]
    usage: Usage


@dataclass(frozen=True)
class FinalizationContinuationPlan:
    source_dir: Path
    source_request: ResearchRequest
    destination_request: ResearchRequest
    source_request_identity: str
    source_model_identity: str
    source_run_identity: str
    current_provider_identity: str
    source_artifact_hashes: dict[str, str]
    frozen_input_hashes: dict[str, str]
    frozen_inputs: dict[str, bytes]
    source_input_paths: dict[str, Path]
    source_checkpoint_bytes: bytes
    source_usage: Usage
    imported_stages: tuple[ImportedFinalizationStage, ...]
    candidate: dict[str, Any]
    candidate_review_stage: str
    new_request_identity: str
    reader_revision_policy: str | None = None
    verification_repair_policy: str | None = None
    revision_generation: int | None = None
    source_writer_stage: str | None = None
    source_terminal_review_sha256: str | None = None
    revision_contract_sha256: str | None = None
    source_witness_catalog: dict[str, str] | None = None
    source_witness_catalog_sha256: str | None = None
    prior_revision_contracts: tuple[dict[str, Any], ...] | None = None
    prior_revision_contracts_sha256: str | None = None
    deferred_coverage_eligibility: tuple[dict[str, Any], ...] | None = None
    deferred_coverage_eligibility_sha256: str | None = None
    source_issue_lifecycle_sha256: str | None = None
    pending_coverage_contexts: tuple[dict[str, Any], ...] | None = None
    pending_coverage_contexts_sha256: str | None = None
    pending_origin_dir: Path | None = None
    pending_origin_artifact_hashes: dict[str, str] | None = None
    prior_pending_contexts: tuple[dict[str, Any], ...] | None = None
    prior_pending_contexts_sha256: str | None = None
    pending_inventory: tuple[dict[str, Any], ...] | None = None
    pending_inventory_sha256: str | None = None
    prior_pending_inventory: tuple[dict[str, Any], ...] | None = None
    prior_pending_inventory_sha256: str | None = None

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": FINALIZATION_RECOVERY_SCHEMA_VERSION,
            "mode": "candidate_finalization_continuation",
            "engine_version": ENGINE_VERSION,
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "source_request_identity": self.source_request_identity,
            "source_model_identity": self.source_model_identity,
            "source_run_identity": self.source_run_identity,
            "current_provider_identity": self.current_provider_identity,
            "source_artifact_hashes": dict(self.source_artifact_hashes),
            "frozen_input_hashes": dict(self.frozen_input_hashes),
            "source_usage": self.source_usage.model_dump(mode="json"),
            "candidate": {
                "stage": self.candidate["stage"],
                "reader_sha256": self.candidate["reader_sha256"],
            },
            "candidate_review_stage": self.candidate_review_stage,
            "imported_stages": [
                {
                    "stage": item.stage,
                    "role": item.role,
                    "inputs_hash": item.inputs_hash,
                    "output_hash": item.output_hash,
                    "usage": item.usage.model_dump(mode="json"),
                }
                for item in self.imported_stages
            ],
            "new_request_identity": self.new_request_identity,
            "incremental_budget": self.destination_request.budget.model_dump(mode="json"),
            "automatic_or_live_action": False,
            **({"reader_revision_policy": self.reader_revision_policy}
               if self.reader_revision_policy is not None else {}),
            **({"verification_repair_policy": self.verification_repair_policy}
               if self.verification_repair_policy is not None else {}),
            **({"revision_generation": self.revision_generation,
                "source_writer_stage": self.source_writer_stage,
                "source_terminal_review_sha256": self.source_terminal_review_sha256,
                "revision_contract_sha256": self.revision_contract_sha256,
                "source_witness_catalog_sha256": self.source_witness_catalog_sha256}
               if self.revision_generation is not None else {}),
            **({"prior_revision_contracts_sha256": self.prior_revision_contracts_sha256,
                "deferred_coverage_eligibility_sha256": self.deferred_coverage_eligibility_sha256,
                "source_issue_lifecycle_sha256": self.source_issue_lifecycle_sha256}
               if self.reader_revision_policy in {V4_CONTRACT.policy, V5_CONTRACT.policy, V6_CONTRACT.policy} else {}),
            **({"pending_coverage_contexts_sha256": self.pending_coverage_contexts_sha256,
                "pending_origin_artifact_hashes": self.pending_origin_artifact_hashes,
                "prior_pending_contexts_sha256": self.prior_pending_contexts_sha256}
               if self.reader_revision_policy in PINNED_POLICIES else {}),
            **({"pending_inventory_sha256": self.pending_inventory_sha256,
                "prior_pending_inventory_sha256": self.prior_pending_inventory_sha256}
               if self.reader_revision_policy == V6_CONTRACT.policy else {}),
        }

    @property
    def plan_sha256(self) -> str:
        return digest(self.manifest())


@dataclass(frozen=True)
class AuthorizedFinalizationContinuation:
    plan: FinalizationContinuationPlan
    authorization: FinalizationRecoveryAuthorization


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_regular(path: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("finalization recovery inputs must be regular files")
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("finalization recovery input exceeds size allowance")
    return content


def _read_object(path: Path) -> dict[str, Any]:
    value = parse_json(_read_regular(path))
    if not isinstance(value, dict):
        raise ValueError("finalization recovery JSON input must be an object")
    return value


def _pending_origin_proof(origin_dir: Path | None, expected_hashes: dict[str, str],
                          source_dir: Path) -> dict[str, Any]:
    """Read an explicit locator, accepting only the hash-bound direct parent."""
    if origin_dir is None:
        raise ValueError("pending carry requires an explicit origin locator")
    origin_dir = Path(origin_dir)
    if origin_dir.is_symlink() or not origin_dir.is_dir():
        raise ValueError("pending origin must be a real directory")
    origin_dir = origin_dir.resolve()
    if origin_dir == source_dir or set(expected_hashes) != {
            FINALIZATION_CHECKPOINT_NAME, "reader_verification.json",
            "recovery_provenance.json", "result.json"}:
        raise ValueError("pending origin is not its bound direct parent")
    with _source_lock(origin_dir):
        contents = {name: _read_regular(origin_dir / name) for name in expected_hashes}
    if {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()} != expected_hashes:
        raise ValueError("pending origin artifact hashes differ from parent provenance")
    checkpoint = parse_json(contents[FINALIZATION_CHECKPOINT_NAME])
    verification = parse_json(contents["reader_verification.json"])
    result = parse_json(contents["result.json"])
    if (not isinstance(checkpoint, dict) or not isinstance(verification, dict)
            or not isinstance(verification.get("English"), dict)
            or not isinstance(result, dict) or result.get("stop_reason") != "verification_failed"
            or checkpoint.get("dispatched") is not False
            or Usage.model_validate(checkpoint.get("usage")).complete is not True):
        raise ValueError("pending origin is not a settled failed verification")
    return {"artifact_hashes": dict(expected_hashes),
            "checkpoint": checkpoint, "verification": verification["English"]}


@contextmanager
def _source_lock(source_dir: Path):
    lock_path = source_dir / ".research.lock"
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


def _request_input_paths(request: ResearchRequest) -> dict[str, Path]:
    return {
        name: Path(value)
        for name in ("evidence_path", "prior_dossier_path", "financial_case_path")
        if (value := getattr(request, name)) is not None
    }


def _read_request_inputs(request: ResearchRequest) -> tuple[dict[str, Path], dict[str, bytes]]:
    paths = _request_input_paths(request)
    return paths, {name: _read_regular(path) for name, path in paths.items()}


def _research_settings(request: ResearchRequest) -> dict[str, Any]:
    return request.model_dump(
        mode="json",
        exclude={
            "budget",
            "output_dir",
            "dossier_dir",
            "evidence_path",
            "prior_dossier_path",
            "financial_case_path",
        },
    )


def finalization_continuation_request_identity(
    request: ResearchRequest, frozen: dict[str, bytes]
) -> str:
    return digest(
        {
            "request_identity": request_identity(request, frozen),
            "output_dir": str(request.output_dir.resolve()),
        }
    )


def _validate_narrow_scope(source: ResearchRequest, destination: ResearchRequest) -> None:
    if (
        source.financial_case_path is None
        or source.quality_revision != "evidence-led-bounded"
        or source.valuation_method != "fcff"
        or source.report_language != "English"
        or source.additional_report_languages
        or source.budget.followup_cycles != 0
    ):
        raise ValueError(
            "candidate continuation currently requires a bounded English financial-case run "
            "with followup_cycles=0 and no additional report language"
        )
    if destination.budget.followup_cycles != 0:
        raise ValueError("candidate continuation must preserve followup_cycles=0")
    if destination.dossier_dir is not None:
        raise ValueError("candidate continuation cannot promote or write a dossier")
    if _research_settings(source) != _research_settings(destination):
        raise ValueError("destination changed source research settings")


def _validate_path_isolation(
    source_dir: Path, destination: Path, input_paths: dict[str, Path] | None = None,
    pending_origin_dir: Path | None = None,
) -> None:
    output = destination.resolve()
    protected = {source_dir, *(path.resolve().parent for path in (input_paths or {}).values())}
    if pending_origin_dir is not None:
        protected.add(Path(pending_origin_dir).resolve())
    if any(
        output == item or output.is_relative_to(item) or item.is_relative_to(output)
        for item in protected
    ):
        raise ValueError("candidate continuation destination overlaps source material")


def _parse_stages(raw: Any) -> tuple[ImportedFinalizationStage, ...]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("finalization checkpoint stages must be a nonempty mapping")
    imported: list[ImportedFinalizationStage] = []
    for stage, record in raw.items():
        if (
            not isinstance(stage, str)
            or not isinstance(record, dict)
            or set(record) != {"role", "inputs_hash", "output_hash", "output", "usage"}
            or not isinstance(record["role"], str)
            or not _is_hash(record["inputs_hash"])
            or not _is_hash(record["output_hash"])
            or not isinstance(record["output"], dict)
            or digest(record["output"]) != record["output_hash"]
        ):
            raise ValueError("invalid model stage in finalization checkpoint")
        usage = Usage.model_validate(record["usage"])
        if not usage.complete:
            raise ValueError("imported model stage usage must be complete")
        imported.append(
            ImportedFinalizationStage(
                stage=stage,
                role=record["role"],
                inputs_hash=record["inputs_hash"],
                output_hash=record["output_hash"],
                output=deepcopy(record["output"]),
                usage=usage,
            )
        )
    return tuple(imported)


def _validate_candidate(
    checkpoint: dict[str, Any], imported: tuple[ImportedFinalizationStage, ...],
    *, repair_verification: bool = False, generic_revision: bool = False,
) -> tuple[dict[str, Any], str]:
    candidate = checkpoint["candidate"]
    review_stage = checkpoint["candidate_review_stage"]
    allowed_stage = ({REVISED_REVIEW_STAGE} if repair_verification else
                     {FROZEN_REVIEW_STAGE} if generic_revision and review_stage == FROZEN_REVIEW_STAGE
                     else {review_stage} if generic_revision and isinstance(review_stage, str)
                     and generic_review_stage(review_stage)
                     else _SUPPORTED_CANDIDATE_STAGES)
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"stage", "reader_sha256", "reader_text"}
        or candidate.get("stage") not in allowed_stage
        or not _is_hash(candidate.get("reader_sha256"))
        or not isinstance(candidate.get("reader_text"), str)
        or hashlib.sha256(candidate["reader_text"].encode("utf-8")).hexdigest()
        != candidate["reader_sha256"]
        or review_stage != candidate["stage"]
    ):
        raise ValueError("invalid factual-reviewed reader candidate")
    by_stage = {item.stage: item for item in imported}
    factual = by_stage.get(review_stage)
    draft_stage = (REVISE_STAGE if repair_verification or review_stage == FROZEN_REVIEW_STAGE else
                   generation_stages(int(review_stage.rsplit("-", 1)[1]))[0]
                   if generic_revision and review_stage.startswith(REVISED_REVIEW_STAGE + "-") else
                   "editor" if review_stage == "verify_report" else "repair_report")
    if (
        factual is None
        or factual.role != "verifier"
        or factual.output.get("reviewed_report") is not True
        or draft_stage not in by_stage
        or by_stage[draft_stage].role != "editor"
    ):
        raise ValueError("candidate lacks its exact editor and factual-review stages")
    return deepcopy(candidate), review_stage


def _generic_source_generation(imported, review_stage):
    """Require a contiguous, unambiguous writer/review lineage before a new writer."""
    stages = {item.stage: item for item in imported}
    if not {REVISE_STAGE, REVISED_REVIEW_STAGE, FROZEN_REVIEW_STAGE} <= stages.keys():
        raise ValueError("generic revision source lacks the complete legacy revision prefix")
    if (stages[REVISE_STAGE].role != "editor"
            or any(stages[name].role != "verifier"
                   for name in (REVISED_REVIEW_STAGE, FROZEN_REVIEW_STAGE))):
        raise ValueError("generic revision legacy writer/reviewer roles differ")
    for verifier in (REVISED_REVIEW_STAGE, FROZEN_REVIEW_STAGE):
        baseline = {name for name in stages if name.startswith(verifier + "-coverage-")}
        if (not baseline or baseline != {
                f"{verifier}-coverage-{index}" for index in range(len(baseline))}
                or any(stages[name].role != "verifier" for name in baseline)):
            raise ValueError("generic revision legacy coverage stages are incomplete")
    writers, reviews, coverage = set(), set(), {}
    for name in stages:
        if name.startswith(REVISE_STAGE + "-"):
            match = re.fullmatch(rf"revise_report-({GENERATION_PATTERN})", name)
            if match is None:
                raise ValueError("generic revision stage collision")
            writers.add(int(match[1]))
        elif name.startswith(REVISED_REVIEW_STAGE + "-") and not name.startswith(
            REVISED_REVIEW_STAGE + "-coverage-"
        ):
            match = re.fullmatch(rf"verify_revised_report-({GENERATION_PATTERN})(?:-coverage-([0-9]+))?", name)
            if match is None:
                raise ValueError("generic revision stage collision")
            number = int(match[1])
            if match[2] is None:
                reviews.add(number)
            else:
                if name != f"{REVISED_REVIEW_STAGE}-{number}-coverage-{int(match[2])}":
                    raise ValueError("generic revision stage collision")
                coverage.setdefault(number, set()).add(int(match[2]))
        elif name.startswith(FROZEN_REVIEW_STAGE + "-") and not name.startswith(
            FROZEN_REVIEW_STAGE + "-coverage-"
        ):
            raise ValueError("generic revision stage collision")
    generations = writers | reviews | set(coverage)
    ordered = sorted(generations)
    latest = ordered[-1] if ordered else 1
    if (writers != generations or reviews != generations
            or any(number != index for index, number in enumerate(ordered, start=2))):
        raise ValueError("generic revision generation hole")
    for number in ordered:
        writer, verifier = generation_stages(number)
        if writer not in stages or verifier not in stages or (
            stages[writer].role != "editor" or stages[verifier].role != "verifier"
        ):
            raise ValueError("generic revision generation lacks its writer and factual review")
        batch_numbers = coverage.get(number, set())
        if (not batch_numbers or batch_numbers != set(range(len(batch_numbers)))
                or any(stages[f"{verifier}-coverage-{index}"].role != "verifier"
                       for index in batch_numbers)):
            raise ValueError("generic revision generation has incomplete coverage stages")
    expected = FROZEN_REVIEW_STAGE if latest == 1 else generation_stages(latest)[1]
    if review_stage != expected:
        raise ValueError("generic revision source generation is ambiguous")
    return latest + 1, REVISE_STAGE if latest == 1 else generation_stages(latest)[0]


def _validate_source_lineage(
    source_dir: Path,
    source_request_identity: str,
    source_model_identity: str,
    source_run_identity: str,
) -> dict[str, str]:
    ordinary_identity = digest(
        {"request": source_request_identity, "model_service": source_model_identity}
    )
    if source_run_identity == ordinary_identity:
        return {}
    provenance_path = source_dir / "recovery_provenance.json"
    if not provenance_path.exists() or provenance_path.is_symlink():
        raise ValueError("source is neither an ordinary run nor a candidate continuation")
    content = _read_regular(provenance_path)
    provenance = parse_json(content)
    if (
        not isinstance(provenance, dict)
        or provenance.get("mode") != "candidate_finalization_continuation"
        or provenance.get("source_model_identity") != source_model_identity
        or not _is_hash(provenance.get("service_identity"))
        or source_run_identity
        != digest(
            {
                "request": source_request_identity,
                "model_service": provenance["service_identity"],
            }
        )
    ):
        raise ValueError("unsupported or invalid recovery lineage")
    return {"recovery_provenance.json": hashlib.sha256(content).hexdigest()}


def _revision_artifacts(source_dir, candidate, imported, source_usage, *, repair_verification=False,
                        generic_revision=False):
    """Bind a terminal failed repair, never an in-flight or already revised reader."""
    review_stage = (candidate["stage"] if generic_revision else
                    REVISED_REVIEW_STAGE if repair_verification else "verify_repaired_report")
    if candidate["stage"] != review_stage:
        raise ValueError("reader revision requires a previously repaired candidate")
    contents = {name: _read_regular(source_dir / name)
                for name in ("result.json", "reader_verification.json")}
    result = parse_json(contents["result.json"])
    verification = parse_json(contents["reader_verification.json"])
    reader = verification.get("English", {}) if isinstance(verification, dict) else {}
    review = reader.get("review") if isinstance(reader, dict) else None
    if (not isinstance(result, dict) or result.get("stop_reason") != "verification_failed"
            or Usage.model_validate(result.get("usage")) != source_usage
            or not isinstance(reader, dict) or reader.get("exported") is not False
            or reader.get("stage") != candidate["stage"]
            or reader.get("reader_sha256") != candidate["reader_sha256"]
            or not isinstance(review, dict) or review.get("reviewed_report") is not True
            or not isinstance(review.get("findings"), list)
            or not all(isinstance(f, dict) for f in review["findings"])
            or not any(f.get("severity") in {"warning", "critical"}
                       for f in review["findings"])):
        raise ValueError("reader revision requires a settled bound verification failure")
    stages = {item.stage: item for item in imported}
    batches = reader.get("coverage_batches")
    required_ids = reader.get("required_limitation_ids")
    lifecycle = reader.get("issue_lifecycle")
    if (not isinstance(batches, list) or not all(isinstance(b, dict) for b in batches)
            or not isinstance(required_ids, list) or not all(isinstance(i, str) for i in required_ids)
            or not isinstance(lifecycle, dict) or not isinstance(lifecycle.get("issues"), list)
            or not all(isinstance(i, dict) for i in lifecycle["issues"])):
        raise ValueError("reader revision requires complete coverage history")
    open_issues = [i for i in lifecycle["issues"] if i.get("status") == "open"]
    if Counter(i.get("issue_id") for i in open_issues) != Counter(required_ids):
        raise ValueError("reader revision required obligations differ from lifecycle")
    expected_ids = [i["issue_id"] for i in compound_coverage_issues(open_issues)]
    if (any(not isinstance(b.get("issue_ids"), list)
            or not all(isinstance(i, str) for i in b["issue_ids"]) for b in batches)
            or Counter(i for b in batches for i in b["issue_ids"]) != Counter(expected_ids)):
        raise ValueError("reader revision coverage does not cover every required obligation")
    expected = {f"{review_stage}-coverage-{i}" for i in range(len(batches))}
    if (expected != {name for name in stages if name.startswith(f"{review_stage}-coverage-")}
            or any(batch.get("stage") != f"{review_stage}-coverage-{i}"
                   or batch.get("reader_sha256") != candidate["reader_sha256"]
                   or not stages[batch["stage"]].output.get("reviewed_report")
                   for i, batch in enumerate(batches))):
        raise ValueError("reader revision coverage inventory differs from checkpoint")
    if repair_verification:
        factual = stages[review_stage].output
        if (factual.get("findings") or factual.get("contradicted_claim_ids")
                or any(item.stage.startswith(FROZEN_REVIEW_STAGE) for item in imported)):
            raise ValueError("verification repair requires clean factual review and cannot renew itself")
    elif not generic_revision and any(item.stage.startswith(("revise_report", "verify_revised_report")) for item in imported):
        raise ValueError("reader revision cannot renew a previous revision")
    if generic_revision:
        _generic_source_generation(imported, review_stage)
    return {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}


def prepare_finalization_continuation(
    source_dir: Path, destination_request: ResearchRequest, *, revise_reader: bool = False,
    repair_verification: bool = False, pending_origin_dir: Path | None = None,
) -> FinalizationContinuationPlan:
    """Prepare and content-bind a candidate continuation without live calls."""
    if revise_reader and repair_verification:
        raise ValueError("writer revision and verification repair are mutually exclusive")
    if pending_origin_dir is not None and not revise_reader:
        raise ValueError("pending origin is only valid for a reader revision")
    source_dir = Path(source_dir)
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError("source run must be a real directory")
    source_dir = source_dir.resolve()
    destination_request = ResearchRequest.model_validate_json(
        destination_request.model_dump_json(warnings="error")
    )
    _validate_path_isolation(source_dir, destination_request.output_dir,
                             pending_origin_dir=pending_origin_dir)

    with _source_lock(source_dir):
        checkpoint_path = source_dir / FINALIZATION_CHECKPOINT_NAME
        checkpoint_bytes = _read_regular(checkpoint_path)
        checkpoint = parse_json(checkpoint_bytes)
        if not isinstance(checkpoint, dict) or set(checkpoint) != _CHECKPOINT_KEYS:
            raise ValueError("finalization checkpoint shape is invalid")
        if (
            checkpoint["schema_version"] != FINALIZATION_RECOVERY_SCHEMA_VERSION
            or checkpoint["engine_version"] != ENGINE_VERSION
            or checkpoint["wire_schema_version"] != WIRE_SCHEMA_VERSION
            or not _is_hash(checkpoint["model_service_identity"])
            or not _is_hash(checkpoint["run_identity"])
            or type(checkpoint["dispatched"]) is not bool
        ):
            raise ValueError("finalization checkpoint contract is not current")

        source_request = ResearchRequest.model_validate(checkpoint["request"])
        if source_request.output_dir.resolve() != source_dir:
            raise ValueError("source request output directory does not match its checkpoint")
        _validate_narrow_scope(source_request, destination_request)
        source_paths, source_inputs = _read_request_inputs(source_request)
        _validate_path_isolation(source_dir, destination_request.output_dir, source_paths,
                                 pending_origin_dir=pending_origin_dir)
        destination_paths, destination_inputs = _read_request_inputs(destination_request)
        del destination_paths
        frozen_hashes = {
            name: hashlib.sha256(content).hexdigest() for name, content in source_inputs.items()
        }
        if (
            checkpoint["frozen_input_hashes"] != frozen_hashes
            or set(destination_inputs) != set(source_inputs)
            or any(destination_inputs[name] != source_inputs[name] for name in source_inputs)
        ):
            raise ValueError("destination changed the frozen input bytes")
        source_usage = Usage.model_validate(checkpoint["usage"])
        if checkpoint["dispatched"] or not source_usage.complete:
            raise ValueError("source has an unsettled dispatch or incomplete cumulative usage")
        imported = _parse_stages(checkpoint["stages"])
        if any(sum(getattr(item.usage, field) for item in imported) > getattr(source_usage, field)
               for field in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")):
            raise ValueError("source cumulative usage omits saved stage usage")
        generic_revision = revise_reader and checkpoint["candidate_review_stage"] == FROZEN_REVIEW_STAGE
        if revise_reader and isinstance(checkpoint["candidate_review_stage"], str) and generic_review_stage(
            checkpoint["candidate_review_stage"]
        ):
            generic_revision = True
        candidate, review_stage = _validate_candidate(
            checkpoint, imported, repair_verification=repair_verification,
            generic_revision=generic_revision)
        generation, source_writer = (_generic_source_generation(imported, review_stage)
                                     if generic_revision else (None, None))
        revision_artifacts = (_revision_artifacts(source_dir, candidate, imported, source_usage,
                                                  repair_verification=repair_verification,
                                                  generic_revision=generic_revision)
                              if revise_reader or repair_verification else {})
        source_verification = (_read_object(source_dir / "reader_verification.json")[
            "English"] if generic_revision else None)
        terminal_review = source_verification["review"] if generic_revision else None
        source_terminal_review_sha256 = digest(terminal_review) if generic_revision else None
        source_request_id = request_identity(source_request, source_inputs)
        lineage_hashes = _validate_source_lineage(
            source_dir,
            source_request_id,
            checkpoint["model_service_identity"],
            checkpoint["run_identity"],
        )
        source_provenance = (_read_object(source_dir / "recovery_provenance.json")
                             if generic_revision and generation > 2 else None)
        prior_contracts = (source_revision_contracts(
            source_provenance,
            lineage_hashes["recovery_provenance.json"], imported, generation - 1)
            if generic_revision and generation > 2 else ())
        parent_contract = prior_contracts[-1]["contract_sha256"] if prior_contracts else None
        target_contract = (
            V6_CONTRACT if parent_contract in {item.sha256 for item in PINNED_CONTRACTS}
            else V5_CONTRACT if parent_contract == V4_CONTRACT.sha256 else V4_CONTRACT
        ) if generic_revision else None
        artifact_hashes = {
            FINALIZATION_CHECKPOINT_NAME: hashlib.sha256(checkpoint_bytes).hexdigest(),
            **lineage_hashes,
            **revision_artifacts,
        }
        pending_origin_proof = None
        pending_contexts = None
        if target_contract in PINNED_CONTRACTS:
            if source_provenance.get("reader_revision_policy") == V4_CONTRACT.policy and (
                    source_verification["issue_lifecycle"].get("pending_coverage")):
                pending_origin_proof = _pending_origin_proof(
                    pending_origin_dir, source_provenance["source_artifact_hashes"], source_dir)
            elif pending_origin_dir is not None:
                raise ValueError("unneeded pending origin locator")
            pending_contexts = pending_contexts_from_source(
                source_verification, checkpoint, source_provenance, candidate["reader_text"],
                current_artifact_hashes=artifact_hashes, origin_proof=pending_origin_proof)
            prior_pending_contexts = prior_pending_context_lineage(
                source_provenance, prior_contracts)
        elif pending_origin_dir is not None:
            raise ValueError("pending origin cannot change a historical revision contract")
        else:
            prior_pending_contexts = None
        deferred = (pending_entries(pending_contexts) if target_contract in PINNED_CONTRACTS
                    else deferred_coverage_eligibility(
                        source_verification, checkpoint["stages"], candidate["reader_text"])
                    if generic_revision else ())
        source_witness_catalog = None
        if generic_revision:
            witness_snapshot = validate_snapshot(EvidenceSnapshot.model_validate(
                parse_json(source_inputs["evidence_path"])), source_request)
            witness_case = (load_case_context(source_inputs["financial_case_path"],
                source_request, witness_snapshot) if target_contract == V6_CONTRACT else None)
            source_witness_catalog = revision_witness_catalog(target_contract,
                witness_snapshot, terminal_review["findings"],
                source_verification["issue_lifecycle"]["issues"], case_context=witness_case)
        pending_inventory = (inventory_contexts_from_source(
            source_verification, checkpoint, source_provenance, candidate["reader_text"],
            current_artifact_hashes=artifact_hashes)
            if target_contract == V6_CONTRACT else None)
        prior_inventory = (prior_inventory_lineage(source_provenance, prior_contracts)
                           if target_contract == V6_CONTRACT else None)
        plan = FinalizationContinuationPlan(
            source_dir=source_dir,
            source_request=source_request,
            destination_request=destination_request,
            source_request_identity=source_request_id,
            source_model_identity=checkpoint["model_service_identity"],
            source_run_identity=checkpoint["run_identity"],
            current_provider_identity=checkpoint["model_service_identity"],
            source_artifact_hashes=artifact_hashes,
            frozen_input_hashes=frozen_hashes,
            frozen_inputs=source_inputs,
            source_input_paths=source_paths,
            source_checkpoint_bytes=checkpoint_bytes,
            source_usage=source_usage,
            imported_stages=imported,
            candidate=candidate,
            candidate_review_stage=review_stage,
            new_request_identity=finalization_continuation_request_identity(
                destination_request, destination_inputs
            ),
            reader_revision_policy=(target_contract.policy if generic_revision else
                                    READER_REVISION_POLICY if revise_reader else None),
            verification_repair_policy=VERIFICATION_REPAIR_POLICY if repair_verification else None,
            revision_generation=generation,
            source_writer_stage=source_writer,
            source_terminal_review_sha256=source_terminal_review_sha256,
            revision_contract_sha256=(target_contract.sha256
                                      if generic_revision else None),
            source_witness_catalog=source_witness_catalog,
            source_witness_catalog_sha256=(digest(source_witness_catalog)
                                           if generic_revision else None),
            prior_revision_contracts=prior_contracts if generic_revision else None,
            prior_revision_contracts_sha256=(lineage_sha256(prior_contracts)
                                             if generic_revision else None),
            deferred_coverage_eligibility=deferred if generic_revision else None,
            deferred_coverage_eligibility_sha256=(digest(deferred) if generic_revision else None),
            source_issue_lifecycle_sha256=(digest(source_verification["issue_lifecycle"])
                                           if generic_revision else None),
            pending_coverage_contexts=pending_contexts,
            pending_coverage_contexts_sha256=(digest(pending_contexts)
                                              if pending_contexts is not None else None),
            pending_origin_dir=(Path(pending_origin_dir).resolve()
                                if pending_origin_proof is not None else None),
            pending_origin_artifact_hashes=(pending_origin_proof["artifact_hashes"]
                                            if pending_origin_proof is not None else None),
            prior_pending_contexts=prior_pending_contexts,
            prior_pending_contexts_sha256=(digest(prior_pending_contexts)
                                           if prior_pending_contexts is not None else None),
            pending_inventory=pending_inventory,
            pending_inventory_sha256=(digest(pending_inventory) if pending_inventory is not None else None),
            prior_pending_inventory=prior_inventory,
            prior_pending_inventory_sha256=(digest(prior_inventory) if prior_inventory is not None else None),
        )
    assert_finalization_source_unchanged(plan)
    return plan


def _validate_plan_content(plan: FinalizationContinuationPlan) -> None:
    _validate_path_isolation(plan.source_dir, plan.destination_request.output_dir,
                             plan.source_input_paths, plan.pending_origin_dir)
    checkpoint = parse_json(plan.source_checkpoint_bytes)
    if not isinstance(checkpoint, dict) or set(checkpoint) != _CHECKPOINT_KEYS:
        raise ValueError("prepared finalization checkpoint bytes are invalid")
    source_request = ResearchRequest.model_validate(checkpoint["request"])
    imported = _parse_stages(checkpoint["stages"])
    candidate, review_stage = _validate_candidate(
        checkpoint, imported, repair_verification=plan.verification_repair_policy is not None,
        generic_revision=plan.revision_generation is not None)
    expected_input_paths = _request_input_paths(source_request)
    source_request_id = request_identity(source_request, plan.frozen_inputs)
    expected_artifact_names = {FINALIZATION_CHECKPOINT_NAME}
    if plan.verification_repair_policy is not None:
        if (plan.verification_repair_policy != VERIFICATION_REPAIR_POLICY
                or plan.reader_revision_policy is not None or review_stage != REVISED_REVIEW_STAGE):
            raise ValueError("invalid verification repair policy or source candidate")
        expected_artifact_names.update({"result.json", "reader_verification.json"})
    if plan.reader_revision_policy is not None:
        if plan.revision_generation is not None:
            generation, writer = _generic_source_generation(imported, review_stage)
            target_contract = next((contract for contract in (V4_CONTRACT, V5_CONTRACT, V6_CONTRACT)
                                    if contract.policy == plan.reader_revision_policy), None)
            if (target_contract is None
                    or plan.verification_repair_policy is not None
                    or not _is_hash(plan.source_terminal_review_sha256)
                    or plan.revision_contract_sha256 != target_contract.sha256
                    or not isinstance(plan.source_witness_catalog, dict)
                    or digest(plan.source_witness_catalog) != plan.source_witness_catalog_sha256
                    or not isinstance(plan.prior_revision_contracts, tuple)
                    or lineage_sha256(plan.prior_revision_contracts)
                    != plan.prior_revision_contracts_sha256
                    or not isinstance(plan.deferred_coverage_eligibility, tuple)
                    or digest(plan.deferred_coverage_eligibility)
                    != plan.deferred_coverage_eligibility_sha256
                    or not _is_hash(plan.source_issue_lifecycle_sha256)
                    or (generation, writer) != (plan.revision_generation, plan.source_writer_stage)):
                raise ValueError("invalid generic revision policy or source writer")
            if target_contract in PINNED_CONTRACTS:
                if (not isinstance(plan.pending_coverage_contexts, tuple)
                        or digest(plan.pending_coverage_contexts)
                        != plan.pending_coverage_contexts_sha256
                        or pending_entries(plan.pending_coverage_contexts)
                        != plan.deferred_coverage_eligibility
                        or (plan.pending_origin_dir is None)
                        != (plan.pending_origin_artifact_hashes is None)
                        or not isinstance(plan.prior_pending_contexts, tuple)
                        or digest(plan.prior_pending_contexts)
                        != plan.prior_pending_contexts_sha256):
                    raise ValueError("invalid v5 pending context envelope")
            elif (plan.pending_coverage_contexts is not None
                  or plan.pending_coverage_contexts_sha256 is not None
                  or plan.pending_origin_dir is not None
                  or plan.pending_origin_artifact_hashes is not None
                  or plan.prior_pending_contexts is not None
                  or plan.prior_pending_contexts_sha256 is not None):
                raise ValueError("historical v4 plan gained pending context fields")
        elif (plan.reader_revision_policy != READER_REVISION_POLICY
              or review_stage != "verify_repaired_report" or plan.source_writer_stage is not None):
            raise ValueError("invalid reader revision policy or source candidate")
        expected_artifact_names.update({"result.json", "reader_verification.json"})
    elif (plan.revision_generation is not None or plan.source_writer_stage is not None
          or plan.source_terminal_review_sha256 is not None
          or plan.revision_contract_sha256 is not None
          or plan.source_witness_catalog is not None
          or plan.source_witness_catalog_sha256 is not None
          or plan.prior_revision_contracts is not None
          or plan.prior_revision_contracts_sha256 is not None
          or plan.deferred_coverage_eligibility is not None
          or plan.deferred_coverage_eligibility_sha256 is not None
          or plan.source_issue_lifecycle_sha256 is not None
          or plan.pending_coverage_contexts is not None
          or plan.pending_coverage_contexts_sha256 is not None
          or plan.pending_origin_dir is not None
          or plan.pending_origin_artifact_hashes is not None
          or plan.prior_pending_contexts is not None
          or plan.prior_pending_contexts_sha256 is not None):
        raise ValueError("unexpected generic revision generation")
    if plan.reader_revision_policy == V6_CONTRACT.policy:
        if (not isinstance(plan.pending_inventory, tuple)
                or digest(plan.pending_inventory) != plan.pending_inventory_sha256
                or not isinstance(plan.prior_pending_inventory, tuple)
                or digest(plan.prior_pending_inventory) != plan.prior_pending_inventory_sha256):
            raise ValueError("invalid v6 inventory envelope")
        inventory_finding_hashes(plan.pending_inventory)
    elif any(value is not None for value in (
            plan.pending_inventory, plan.pending_inventory_sha256,
            plan.prior_pending_inventory, plan.prior_pending_inventory_sha256)):
        raise ValueError("historical contract gained inventory envelope fields")
    if plan.source_run_identity != digest(
        {"request": source_request_id, "model_service": plan.source_model_identity}
    ):
        expected_artifact_names.add("recovery_provenance.json")
    if (
        source_request != plan.source_request
        or source_request.output_dir.resolve() != plan.source_dir
        or source_request_id != plan.source_request_identity
        or checkpoint["model_service_identity"] != plan.source_model_identity
        or plan.current_provider_identity != plan.source_model_identity
        or checkpoint["run_identity"] != plan.source_run_identity
        or checkpoint["frozen_input_hashes"] != plan.frozen_input_hashes
        or checkpoint["dispatched"] is not False
        or Usage.model_validate(checkpoint["usage"]) != plan.source_usage
        or not plan.source_usage.complete
        or imported != plan.imported_stages
        or candidate != plan.candidate
        or review_stage != plan.candidate_review_stage
        or expected_input_paths != plan.source_input_paths
        or set(plan.source_artifact_hashes) != expected_artifact_names
        or plan.source_artifact_hashes.get(FINALIZATION_CHECKPOINT_NAME)
        != hashlib.sha256(plan.source_checkpoint_bytes).hexdigest()
        or {
            name: hashlib.sha256(content).hexdigest()
            for name, content in plan.frozen_inputs.items()
        }
        != plan.frozen_input_hashes
        or finalization_continuation_request_identity(
            plan.destination_request, plan.frozen_inputs
        )
        != plan.new_request_identity
    ):
        raise ValueError("prepared finalization continuation content changed")


def assert_finalization_source_unchanged(
    plan: FinalizationContinuationPlan | AuthorizedFinalizationContinuation,
) -> None:
    prepared = plan.plan if isinstance(plan, AuthorizedFinalizationContinuation) else plan
    _validate_plan_content(prepared)
    with _source_lock(prepared.source_dir):
        if prepared.reader_revision_policy is not None or prepared.verification_repair_policy is not None:
            revision_hashes = _revision_artifacts(prepared.source_dir, prepared.candidate,
                                                 prepared.imported_stages, prepared.source_usage,
                                                 repair_verification=prepared.verification_repair_policy is not None,
                                                 generic_revision=prepared.revision_generation is not None)
            if any(prepared.source_artifact_hashes.get(name) != value
                   for name, value in revision_hashes.items()):
                raise ValueError("reader revision terminal artifacts changed")
            if prepared.revision_generation is not None and digest(_read_object(
                prepared.source_dir / "reader_verification.json")["English"]["review"]
            ) != prepared.source_terminal_review_sha256:
                raise ValueError("reader revision terminal findings changed")
            if prepared.reader_revision_policy in {V4_CONTRACT.policy, V5_CONTRACT.policy, V6_CONTRACT.policy}:
                source_verification = _read_object(
                    prepared.source_dir / "reader_verification.json")["English"]
                checkpoint = parse_json(prepared.source_checkpoint_bytes)
                source_provenance = _read_object(
                    prepared.source_dir / "recovery_provenance.json")
                prior = (source_revision_contracts(
                    source_provenance,
                    prepared.source_artifact_hashes["recovery_provenance.json"],
                    prepared.imported_stages, prepared.revision_generation - 1)
                    if prepared.revision_generation > 2 else ())
                if prepared.reader_revision_policy in PINNED_POLICIES:
                    origin_proof = (_pending_origin_proof(
                        prepared.pending_origin_dir, prepared.pending_origin_artifact_hashes,
                        prepared.source_dir) if prepared.pending_origin_dir is not None else None)
                    expected_contexts = pending_contexts_from_source(
                        source_verification, checkpoint, source_provenance,
                        prepared.candidate["reader_text"],
                        current_artifact_hashes=prepared.source_artifact_hashes,
                        origin_proof=origin_proof)
                    expected_deferred = pending_entries(expected_contexts)
                    expected_prior_pending = prior_pending_context_lineage(
                        source_provenance, prior)
                else:
                    expected_contexts = None
                    expected_prior_pending = None
                    expected_deferred = deferred_coverage_eligibility(
                        source_verification, checkpoint["stages"], prepared.candidate["reader_text"])
                snapshot = validate_snapshot(EvidenceSnapshot.model_validate(
                    parse_json(prepared.frozen_inputs["evidence_path"])), prepared.source_request)
                contract = revision_contract(prepared.reader_revision_policy,
                                             prepared.revision_contract_sha256)
                expected_catalog = revision_witness_catalog(contract,
                    snapshot, source_verification["review"]["findings"],
                    source_verification["issue_lifecycle"]["issues"],
                    case_context=(load_case_context(prepared.frozen_inputs["financial_case_path"],
                        prepared.source_request, snapshot) if contract == V6_CONTRACT else None))
                expected_inventory = (inventory_contexts_from_source(
                    source_verification, checkpoint, source_provenance,
                    prepared.candidate["reader_text"],
                    current_artifact_hashes=prepared.source_artifact_hashes)
                    if contract == V6_CONTRACT else None)
                expected_inventory_lineage = (prior_inventory_lineage(source_provenance, prior)
                                              if contract == V6_CONTRACT else None)
                if (prior != prepared.prior_revision_contracts
                        or expected_deferred != prepared.deferred_coverage_eligibility
                        or expected_contexts != prepared.pending_coverage_contexts
                        or expected_prior_pending != prepared.prior_pending_contexts
                        or expected_catalog != prepared.source_witness_catalog
                        or expected_inventory != prepared.pending_inventory
                        or expected_inventory_lineage != prepared.prior_pending_inventory
                        or digest(source_verification["issue_lifecycle"])
                        != prepared.source_issue_lifecycle_sha256):
                    raise ValueError("numbered revision source contract or eligibility changed")
        artifact_hashes = {
            name: hashlib.sha256(_read_regular(prepared.source_dir / name)).hexdigest()
            for name in prepared.source_artifact_hashes
        }
        input_hashes = {
            name: hashlib.sha256(_read_regular(path)).hexdigest()
            for name, path in prepared.source_input_paths.items()
        }
    if (
        artifact_hashes != prepared.source_artifact_hashes
        or input_hashes != prepared.frozen_input_hashes
        or hashlib.sha256(prepared.source_checkpoint_bytes).hexdigest()
        != prepared.source_artifact_hashes[FINALIZATION_CHECKPOINT_NAME]
    ):
        raise ValueError("finalization source or frozen inputs changed")


def authorize_finalization_continuation(
    plan: FinalizationContinuationPlan,
    authorization: FinalizationRecoveryAuthorization,
    destination_request: ResearchRequest | None = None,
    frozen: dict[str, bytes] | None = None,
) -> AuthorizedFinalizationContinuation:
    """Bind a separate incremental authorization to the exact offline plan."""
    if not isinstance(plan, FinalizationContinuationPlan):
        raise ValueError("finalization continuation requires a prepared plan")
    authorization = FinalizationRecoveryAuthorization.model_validate_json(
        authorization.model_dump_json(warnings="error")
    )
    request = destination_request or plan.destination_request
    request = ResearchRequest.model_validate_json(request.model_dump_json(warnings="error"))
    if frozen is None:
        _, frozen = _read_request_inputs(request)
    _validate_narrow_scope(plan.source_request, request)
    _validate_path_isolation(plan.source_dir, request.output_dir, plan.source_input_paths,
                             plan.pending_origin_dir)
    identity = finalization_continuation_request_identity(request, frozen)
    if (
        authorization.plan_sha256 != plan.plan_sha256
        or authorization.new_request_identity != plan.new_request_identity
        or identity != plan.new_request_identity
    ):
        raise ValueError("authorization is for a different continuation plan or request")
    if request.budget != authorization.incremental_budget:
        raise ValueError("continuation request does not use the authorized incremental budget")
    if set(frozen) != set(plan.frozen_inputs) or any(
        frozen[name] != plan.frozen_inputs[name] for name in plan.frozen_inputs
    ):
        raise ValueError("continuation changed frozen input bytes")
    assert_finalization_source_unchanged(plan)
    return AuthorizedFinalizationContinuation(plan=plan, authorization=authorization)


def load_finalization_authorization(path: Path) -> FinalizationRecoveryAuthorization:
    return FinalizationRecoveryAuthorization.model_validate(_read_object(Path(path)))


def write_finalization_continuation_plan(
    plan: FinalizationContinuationPlan, path: Path
) -> None:
    assert_finalization_source_unchanged(plan)
    path = Path(path)
    resolved = path.resolve()
    if (path.exists() or path.is_symlink() or resolved.is_relative_to(plan.source_dir)
            or resolved in {item.resolve() for item in plan.source_input_paths.values()}):
        raise ValueError("continuation plan requires a fresh path outside historical inputs")
    _validate_path_isolation(plan.source_dir, resolved, plan.source_input_paths,
                             plan.pending_origin_dir)
    payload = {
        **plan.manifest(),
        "plan_sha256": plan.plan_sha256,
        "destination_request": plan.destination_request.model_dump(mode="json"),
        "authorization_required": {
            "new_request_identity": plan.new_request_identity,
            "incremental_budget": plan.destination_request.budget.model_dump(mode="json"),
            "authorize_live_continuation": True,
        },
    }
    _write_new_plan(resolved, canonical_json(payload))


def _write_new_plan(path: Path, content: bytes) -> None:
    """Publish a complete new file without replacing any concurrent target.

    Walk/open directories without following symlinks, then keep their descriptor
    pinned across writing and linking. Unlike replace(), link() fails if the
    destination appears concurrently, including a planted symlink.
    """
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".finalization-plan-" + uuid.uuid4().hex
    created = False
    try:
        for component in path.parent.parts[1:]:
            with suppress(FileExistsError):
                os.mkdir(component, mode=0o700, dir_fd=directory)
            next_directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


class FinalizationRecoveryModelService:
    """Replay exact saved calls through one candidate, then delegate missing calls."""

    kind = "candidate_recovery"

    def __init__(self, authorized: AuthorizedFinalizationContinuation, live_service):
        if not isinstance(authorized, AuthorizedFinalizationContinuation):
            raise ValueError("candidate recovery requires an authorized continuation")
        authorize_finalization_continuation(
            authorized.plan,
            authorized.authorization,
            authorized.plan.destination_request,
            dict(authorized.plan.frozen_inputs),
        )
        live_identity = getattr(live_service, "identity", None)
        if live_identity != authorized.plan.current_provider_identity:
            raise ValueError("live provider identity differs from the prepared plan")
        self.plan = authorized
        self.live_service = live_service
        self.source_model_identity = authorized.plan.source_model_identity
        self.supports_hard_output_cap = getattr(live_service, "supports_hard_output_cap", False)
        self.max_prompt_utf8_bytes = getattr(live_service, "max_prompt_utf8_bytes", None)
        self.identity = digest(
            {
                "service": "candidate-finalization-recovery-v1",
                "wire": WIRE_SCHEMA_VERSION,
                "plan_sha256": authorized.plan.plan_sha256,
                "authorization_sha256": authorized.authorization.authorization_sha256,
                "new_request_identity": authorized.plan.new_request_identity,
                "live_service_identity": live_identity,
            }
        )
        self._imported_stage_names: set[str] = set()
        self._imported_calls: list[dict[str, str]] = []
        self._live_started = False
        self._live_boundary: dict[str, Any] | None = None
        self._current_calls: list[dict[str, Any]] = []
        self._validated_request_identity: str | None = None
        self._inventory_replay_payloads: dict[str, dict[str, Any]] = {}
        self._inventory_prefix_verified = False

    @property
    def candidate_recovery_context(self) -> dict[str, Any]:
        plan = self.plan.plan
        return {
            "schema_version": FINALIZATION_RECOVERY_SCHEMA_VERSION,
            "mode": "candidate_finalization_continuation",
            "service_identity": self.identity,
            "plan_sha256": plan.plan_sha256,
            "source_run_identity": plan.source_run_identity,
            "source_request_identity": plan.source_request_identity,
            "source_model_identity": plan.source_model_identity,
            "current_provider_identity": plan.current_provider_identity,
            "source_artifact_hashes": dict(plan.source_artifact_hashes),
            "frozen_input_hashes": dict(plan.frozen_input_hashes),
            "previous_usage": plan.source_usage.model_dump(mode="json"),
            "initial_budget_usage": Usage().model_dump(mode="json"),
            "previous_elapsed_seconds": 0,
            "source_dispatched": False,
            "whole_run_usage_complete": plan.source_usage.complete,
            "candidate": {
                "stage": plan.candidate["stage"],
                "reader_sha256": plan.candidate["reader_sha256"],
            },
            "candidate_review_stage": plan.candidate_review_stage,
            **({"reader_revision_policy": plan.reader_revision_policy}
               if plan.reader_revision_policy is not None else {}),
            **({"verification_repair_policy": plan.verification_repair_policy}
               if plan.verification_repair_policy is not None else {}),
            **({"revision_generation": plan.revision_generation,
                "source_writer_stage": plan.source_writer_stage,
                "source_terminal_review_sha256": plan.source_terminal_review_sha256,
                "revision_contract_sha256": plan.revision_contract_sha256,
                "source_witness_catalog_sha256": plan.source_witness_catalog_sha256}
               if plan.revision_generation is not None else {}),
            **({"prior_revision_contracts": list(plan.prior_revision_contracts or ()),
                "prior_revision_contracts_sha256": plan.prior_revision_contracts_sha256,
                "deferred_coverage_eligibility": list(plan.deferred_coverage_eligibility or ()),
                "deferred_coverage_eligibility_sha256": plan.deferred_coverage_eligibility_sha256,
                "source_issue_lifecycle_sha256": plan.source_issue_lifecycle_sha256}
               if plan.reader_revision_policy in {V4_CONTRACT.policy, V5_CONTRACT.policy, V6_CONTRACT.policy} else {}),
            **({"pending_coverage_contexts": list(plan.pending_coverage_contexts or ()),
                "pending_coverage_contexts_sha256": plan.pending_coverage_contexts_sha256,
                "pending_origin_artifact_hashes": plan.pending_origin_artifact_hashes,
                "prior_pending_contexts": list(plan.prior_pending_contexts or ()),
                "prior_pending_contexts_sha256": plan.prior_pending_contexts_sha256}
               if plan.reader_revision_policy in PINNED_POLICIES else {}),
            **({"pending_inventory": list(plan.pending_inventory or ()),
                "pending_inventory_sha256": plan.pending_inventory_sha256,
                "prior_pending_inventory": list(plan.prior_pending_inventory or ()),
                "prior_pending_inventory_sha256": plan.prior_pending_inventory_sha256}
               if plan.reader_revision_policy == V6_CONTRACT.policy else {}),
            "authorization": {
                **self.plan.authorization.model_dump(mode="json"),
                "authorization_sha256": self.plan.authorization.authorization_sha256,
                "automatic_recovery": False,
                "budget_scope": "new_incremental_calls_only",
            },
            "imported_stages": [
                {
                    "stage": item.stage,
                    "role": item.role,
                    "inputs_hash": item.inputs_hash,
                    "output_hash": item.output_hash,
                    "usage": item.usage.model_dump(mode="json"),
                    "usage_origin": "imported_historical",
                }
                for item in plan.imported_stages
            ],
            "replay_state": {
                "imported_stage_names": sorted(self._imported_stage_names),
                "imported_calls": deepcopy(self._imported_calls),
                "live_started": self._live_started,
                "live_boundary": deepcopy(self._live_boundary),
            },
            "current_calls": deepcopy(self._current_calls),
        }

    def validate_request(self, request: ResearchRequest, frozen: dict[str, bytes]) -> None:
        request = ResearchRequest.model_validate_json(request.model_dump_json(warnings="error"))
        authorize_finalization_continuation(
            self.plan.plan, self.plan.authorization, request, frozen
        )
        self._validated_request_identity = finalization_continuation_request_identity(
            request, frozen
        )

    def restore_recovery_context(self, saved: dict[str, Any]) -> None:
        expected = self.candidate_recovery_context
        dynamic = {"replay_state", "current_calls"}
        if not isinstance(saved, dict) or set(saved) != set(expected) or any(
            saved.get(key) != value for key, value in expected.items() if key not in dynamic
        ):
            raise ValueError("candidate-recovery checkpoint provenance mismatch")
        state = saved.get("replay_state")
        calls = saved.get("current_calls")
        if (
            not isinstance(state, dict)
            or set(state)
            != {"imported_stage_names", "imported_calls", "live_started", "live_boundary"}
            or not isinstance(state["imported_stage_names"], list)
            or not all(isinstance(item, str) for item in state["imported_stage_names"])
            or len(state["imported_stage_names"]) != len(set(state["imported_stage_names"]))
            or not set(state["imported_stage_names"]) <= {
                item.stage for item in self.plan.plan.imported_stages
            }
            or not isinstance(state["imported_calls"], list)
            or not all(isinstance(item, dict) for item in state["imported_calls"])
            or type(state["live_started"]) is not bool
            or (state["live_boundary"] is not None and not isinstance(state["live_boundary"], dict))
            or not isinstance(calls, list)
            or not all(isinstance(item, dict) for item in calls)
        ):
            raise ValueError("candidate-recovery dynamic provenance is invalid")
        if state["live_started"] and state["live_boundary"] is None:
            raise ValueError("candidate-recovery live boundary is missing")
        saved_by_stage = {item.stage: item for item in self.plan.plan.imported_stages}
        call_names: list[str] = []
        for call in state["imported_calls"]:
            if set(call) != {"stage", "role", "inputs_hash", "output_hash"}:
                raise ValueError("candidate-recovery imported-call proof is invalid")
            item = saved_by_stage.get(call["stage"])
            if item is None or call != {
                "stage": item.stage,
                "role": item.role,
                "inputs_hash": item.inputs_hash,
                "output_hash": item.output_hash,
            }:
                raise ValueError("candidate-recovery imported-call proof is invalid")
            call_names.append(item.stage)
        if (
            len(call_names) != len(set(call_names))
            or set(call_names) != set(state["imported_stage_names"])
        ):
            raise ValueError("candidate-recovery imported-call proof is incomplete")
        seen_current = set()
        for index, call in enumerate(calls):
            status = call.get("status")
            required = {"stage", "role", "inputs_hash", "origin", "status"}
            if status in {"completed", "completed_usage_unknown"}:
                required.add("usage")
            if (set(call) != required or status not in {
                    "completed", "completed_usage_unknown", "failed_usage_unknown"}
                    or not isinstance(call.get("stage"), str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", call["stage"])
                    or call["stage"] in seen_current
                    or call.get("role") not in self.plan.plan.destination_request.models
                    or not _is_hash(call.get("inputs_hash")) or call.get("origin") != "current_live"):
                raise ValueError("candidate-recovery current-call proof is invalid")
            seen_current.add(call["stage"])
            if "usage" in call:
                usage = Usage.model_validate(call["usage"])
                if usage.complete != (status == "completed"):
                    raise ValueError("candidate-recovery current-call usage is inconsistent")
            if status != "completed" and index != len(calls) - 1:
                raise ValueError("candidate-recovery continued after unknown usage")
        if state["live_started"]:
            if (self.plan.plan.candidate_review_stage not in call_names or not calls
                    or state["live_boundary"] != {key: calls[0][key]
                                                   for key in ("stage", "role", "inputs_hash")}):
                raise ValueError("candidate-recovery live state lacks its exact candidate/boundary proof")
            if self.plan.plan.reader_revision_policy == READER_REVISION_POLICY and (
                set(call_names) != set(saved_by_stage)
                or state["live_boundary"]["stage"] != REVISE_STAGE
                or state["live_boundary"]["role"] != "editor"
            ):
                raise ValueError("reader revision restored state lacks the complete prefix/revision boundary")
            if self.plan.plan.verification_repair_policy is not None and (
                set(call_names) != set(saved_by_stage)
                or state["live_boundary"]["stage"] != FROZEN_REVIEW_STAGE
                or any(call["role"] != "verifier" or not self._verification_stage(call["stage"])
                       for call in calls)
            ):
                raise ValueError("verification repair restored state violates its frozen verifier boundary")
            if self.plan.plan.revision_generation is not None and (
                set(call_names) != set(saved_by_stage)
                or state["live_boundary"]["stage"] != generation_stages(
                    self.plan.plan.revision_generation)[0]
                or state["live_boundary"]["role"] != "editor"
                or any(call["stage"] not in {
                    generation_stages(self.plan.plan.revision_generation)[0],
                    generation_stages(self.plan.plan.revision_generation)[1],
                } and not (call["stage"].startswith(
                    generation_stages(self.plan.plan.revision_generation)[1] + "-coverage-")
                    and generic_coverage_stage(call["stage"]))
                    for call in calls)
                or any(call["role"] != ("editor" if call["stage"] == generation_stages(
                    self.plan.plan.revision_generation)[0] else "verifier") for call in calls)
            ):
                raise ValueError("generic revision restored state violates its writer boundary")
        elif state["live_boundary"] is not None or calls:
            raise ValueError("candidate-recovery non-live state contains current calls")
        self._imported_stage_names = set(state["imported_stage_names"])
        self._imported_calls = deepcopy(state["imported_calls"])
        self._live_started = state["live_started"]
        self._live_boundary = deepcopy(state["live_boundary"])
        self._current_calls = deepcopy(calls)

    @staticmethod
    def _engine_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key not in _RUNTIME_PAYLOAD_FIELDS}

    def _require_validated_request(self, request: ResearchRequest) -> None:
        plan = self.plan.plan
        authorization = self.plan.authorization
        if (
            authorization.plan_sha256 != plan.plan_sha256
            or authorization.new_request_identity != plan.new_request_identity
            or authorization.incremental_budget != plan.destination_request.budget
            or self._validated_request_identity != plan.new_request_identity
            or request != plan.destination_request
        ):
            raise ValueError("candidate-recovery request was not validated by the engine")

    def has_saved_reply(self, stage: str, payload: dict[str, Any]) -> bool:
        """Return exact availability without advancing or otherwise mutating replay state."""
        if self._live_started or stage in self._imported_stage_names:
            return False
        engine_payload = self._engine_payload(payload)
        for item in self.plan.plan.imported_stages:
            if item.stage == stage:
                return (
                    engine_payload.get("stage") == stage
                    and digest(engine_payload) == item.inputs_hash
                    and digest(item.output) == item.output_hash
                )
        return False

    def observe_replayed_payload(self, role, payload, request):
        """Reprove cached imported inputs without advancing dispatch/replay state."""
        if self.plan.plan.reader_revision_policy != V6_CONTRACT.policy:
            return
        self._require_validated_request(request)
        value = self._engine_payload(payload)
        saved = next((item for item in self.plan.plan.imported_stages
                      if item.stage == value.get("stage")), None)
        if saved is not None:
            if role != saved.role or digest(value) != saved.inputs_hash:
                raise ValueError("inventory replay payload differs from imported input")
            if generic_coverage_stage(saved.stage):
                self._inventory_replay_payloads[saved.stage] = deepcopy(value)

    def _validate_candidate_payload(
        self, saved: ImportedFinalizationStage, engine_payload: dict[str, Any]
    ) -> None:
        plan = self.plan.plan
        if saved.stage != plan.candidate_review_stage:
            return
        research = engine_payload.get("research")
        if (
            not isinstance(research, dict)
            or research.get("rendered_reader") != plan.candidate["reader_text"]
            or research.get("rendered_reader_sha256") != plan.candidate["reader_sha256"]
            or hashlib.sha256(research["rendered_reader"].encode("utf-8")).hexdigest()
            != research["rendered_reader_sha256"]
        ):
            raise ValueError("saved factual stage does not review the bound reader candidate")

    def _require_candidate_boundary(self) -> None:
        if self.plan.plan.candidate_review_stage not in self._imported_stage_names:
            raise ValueError(
                "candidate factual-review stage must be replayed before live continuation"
            )
        if (self.plan.plan.reader_revision_policy is not None
                or self.plan.plan.verification_repair_policy is not None) and self._imported_stage_names != {
            item.stage for item in self.plan.plan.imported_stages
        }:
            raise ValueError("reader revision requires the complete exact source prefix")
        if self.plan.plan.reader_revision_policy == V6_CONTRACT.policy:
            stages = {item.stage: {
                "role": item.role, "inputs_hash": item.inputs_hash, "output_hash": item.output_hash,
                "output": item.output, "usage": item.usage.model_dump(mode="json"),
            } for item in self.plan.plan.imported_stages}
            assert_inventory_prefix_proof(
                self.plan.plan.pending_inventory, stages, self._inventory_replay_payloads)
            self._inventory_prefix_verified = True

    @staticmethod
    def _verification_stage(stage):
        return stage == FROZEN_REVIEW_STAGE or (
            isinstance(stage, str) and stage.startswith(FROZEN_REVIEW_STAGE + "-coverage-")
            and stage.removeprefix(FROZEN_REVIEW_STAGE + "-coverage-").isdigit())

    def call_origin(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> str:
        self._require_validated_request(request)
        assert_finalization_source_unchanged(self.plan)
        engine_payload = self._engine_payload(payload)
        stage = engine_payload.get("stage")
        if not isinstance(stage, str):
            raise ValueError("candidate-recovery call lacks a stage")
        if any(call["stage"] == stage for call in self._current_calls):
            # Resources/usage may have been persisted before the stage output.
            # A missing output cache is not permission to pay for this call again.
            raise ValueError("candidate-recovery stage already dispatched; output recovery required")
        stages = {item.stage: item for item in self.plan.plan.imported_stages}
        if stage in self._imported_stage_names:
            raise ValueError("candidate-recovery engine repeated an imported stage")
        saved = stages.get(stage)
        if not self._live_started and saved is not None:
            if (
                role != saved.role
                or digest(engine_payload) != saved.inputs_hash
                or digest(saved.output) != saved.output_hash
            ):
                raise ValueError("current engine payload differs from the saved model stage")
            self._validate_candidate_payload(saved, engine_payload)
            if (self.plan.plan.reader_revision_policy == V6_CONTRACT.policy
                    and generic_coverage_stage(stage)):
                self._inventory_replay_payloads[stage] = deepcopy(engine_payload)
            return "imported_historical"
        if (self.plan.plan.reader_revision_policy == V6_CONTRACT.policy
                and not self._inventory_prefix_verified):
            self._require_candidate_boundary()
        if not self._live_started:
            self._require_candidate_boundary()
            if self.plan.plan.revision_generation is not None and stage != generation_stages(
                self.plan.plan.revision_generation)[0]:
                raise ValueError("generic revision must start at its authorized writer")
            if self.plan.plan.reader_revision_policy == READER_REVISION_POLICY and stage != REVISE_STAGE:
                raise ValueError("reader revision must start at its explicit revision boundary")
            if self.plan.plan.verification_repair_policy is not None and stage != FROZEN_REVIEW_STAGE:
                raise ValueError("verification repair must start at its explicit factual boundary")
        if self.plan.plan.verification_repair_policy is not None:
            research = engine_payload.get("research", {})
            if (role != "verifier" or not self._verification_stage(stage)
                    or engine_payload.get("verification_repair_policy") != VERIFICATION_REPAIR_POLICY
                    or research.get("rendered_reader") != self.plan.plan.candidate["reader_text"]
                    or research.get("rendered_reader_sha256") != self.plan.plan.candidate["reader_sha256"]):
                raise ValueError("verification repair may only verify the unchanged authorized reader")
        if self.plan.plan.revision_generation is not None:
            writer, verifier = generation_stages(self.plan.plan.revision_generation)
            allowed = stage in {writer, verifier} or (
                stage.startswith(verifier + "-coverage-") and generic_coverage_stage(stage))
            if (not allowed or role != ("editor" if stage == writer else "verifier")
                    or engine_payload.get("reader_revision_policy")
                    != self.plan.plan.reader_revision_policy
                    or engine_payload.get("revision_contract_sha256")
                    != self.plan.plan.revision_contract_sha256):
                raise ValueError("generic revision call is outside the authorized generation")
            if stage == writer:
                research = engine_payload.get("research")
                if (not isinstance(research, dict)
                        or research.get("source_candidate") != self.plan.plan.candidate
                        or research.get("source_writer_stage") != self.plan.plan.source_writer_stage
                        or digest(research.get("repair_findings"))
                        != self.plan.plan.source_terminal_review_sha256
                        or research.get("source_terminal_review_sha256")
                        != self.plan.plan.source_terminal_review_sha256
                        or research.get("reader_revision_policy")
                        != self.plan.plan.reader_revision_policy
                        or research.get("source_text_witnesses")
                        != self.plan.plan.source_witness_catalog
                        or (self.plan.plan.reader_revision_policy in {
                                V4_CONTRACT.policy, V5_CONTRACT.policy, V6_CONTRACT.policy}
                            and research.get("pending_coverage")
                            != list(self.plan.plan.deferred_coverage_eligibility))
                        or (self.plan.plan.reader_revision_policy in PINNED_POLICIES
                            and research.get("pending_coverage_contexts")
                            != list(self.plan.plan.pending_coverage_contexts))):
                    raise ValueError("generic revision writer lacks the bound source candidate")
                if (self.plan.plan.reader_revision_policy == V6_CONTRACT.policy
                        and research.get("pending_inventory") != list(self.plan.plan.pending_inventory)):
                    raise ValueError("generic revision writer lacks the bound inventory obligations")
            if stage == verifier:
                research = engine_payload.get("research")
                source_review = _read_object(self.plan.plan.source_dir /
                    "reader_verification.json")["English"]["review"]
                findings = {digest(item): item for item in source_review["findings"]}
                expected = {"stage": self.plan.plan.candidate_review_stage,
                    "reader_sha256": self.plan.plan.candidate["reader_sha256"],
                    "findings": [{"source_finding_sha256": key, **value}
                                 for key, value in findings.items()]}
                if (not isinstance(research, dict)
                        or research.get("source_terminal_review") != expected
                        or research.get("source_terminal_review_sha256")
                        != self.plan.plan.source_terminal_review_sha256
                        or research.get("source_text_witnesses")
                        != self.plan.plan.source_witness_catalog
                        or (self.plan.plan.reader_revision_policy in {
                                V4_CONTRACT.policy, V5_CONTRACT.policy, V6_CONTRACT.policy}
                            and research.get("pending_coverage")
                            != list(self.plan.plan.deferred_coverage_eligibility))
                        or (self.plan.plan.reader_revision_policy in PINNED_POLICIES
                            and research.get("pending_coverage_contexts")
                            != list(self.plan.plan.pending_coverage_contexts))):
                    raise ValueError("numbered revision factual review omitted bound source findings")
                if (self.plan.plan.reader_revision_policy == V6_CONTRACT.policy
                        and research.get("pending_inventory") != list(self.plan.plan.pending_inventory)):
                    raise ValueError("numbered revision factual review omitted inventory obligations")
        return "current_live"

    def complete(self, role: str, payload: dict[str, Any], request: ResearchRequest) -> ModelReply:
        origin = self.call_origin(role, payload, request)
        engine_payload = self._engine_payload(payload)
        stage = str(engine_payload["stage"])
        if origin == "imported_historical":
            item = next(item for item in self.plan.plan.imported_stages if item.stage == stage)
            self._imported_calls.append(
                {
                    "stage": item.stage,
                    "role": item.role,
                    "inputs_hash": item.inputs_hash,
                    "output_hash": item.output_hash,
                }
            )
            self._imported_stage_names.add(stage)
            return ModelReply(data=deepcopy(item.output), usage=item.usage)
        if not self._live_started:
            self._require_candidate_boundary()
            self._live_started = True
            self._live_boundary = {
                "stage": stage,
                "role": role,
                "inputs_hash": digest(engine_payload),
            }
        assert_finalization_source_unchanged(self.plan)
        try:
            reply = ModelReply.model_validate(self.live_service.complete(role, payload, request))
        except BaseException:
            self._current_calls.append(
                {
                    "stage": stage,
                    "role": role,
                    "inputs_hash": digest(engine_payload),
                    "origin": "current_live",
                    "status": "failed_usage_unknown",
                }
            )
            raise
        self._current_calls.append(
            {
                "stage": stage,
                "role": role,
                "inputs_hash": digest(engine_payload),
                "origin": "current_live",
                "status": "completed" if reply.usage.complete else "completed_usage_unknown",
                "usage": reply.usage.model_dump(mode="json"),
            }
        )
        return reply
