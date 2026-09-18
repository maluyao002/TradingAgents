"""One-call live valuation-model diagnostic over explicit frozen evidence.

This is an engineering probe, not completed research, an accepted valuation, or
an investment target.  It performs exactly one valuation inference when its
estimated admission preflight passes.  It never replays prior stages, retries malformed
output, acquires evidence, or writes into an existing run.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from cli.research import load_request
from tradingagents.research.contracts import (
    Assessment,
    EvidenceSnapshot,
    ResearchRequest,
    ResearchResult,
    Usage,
)
from tradingagents.research.engine import _calculate, _prompt_evidence
from tradingagents.research.equity_valuation import EquityDCFModelInput
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.models import CodexModelService
from tradingagents.research.stages import ValuationProposal, instruction
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)
from tradingagents.research.supervisor import run_supervised
from tradingagents.research.valuation import FCFFModelInput
from tradingagents.research.wire import WIRE_SCHEMA_VERSION, codec_for, system_instruction_suffix

ROLE = "valuation"
MAX_SUPERVISOR_SECONDS = 600
MAX_CALL_SECONDS = 600
MAX_OUTPUT_TOKENS = 16_000
PROBE_ARTIFACT_NAMES = (
    "probe.json",
    "proposal.json",
    "calculation_result.json",
    "usage.json",
    "provenance.json",
)
DIAGNOSTIC_LABEL = (
    "One-call valuation model probe: engineering diagnostic only; not completed research, "
    "not an accepted valuation, and not an endorsed investment target."
)

ANALYST_AUTHORING_POLICY = (
    "Historical opening anchors must be source-bound to exact eligible reported or "
    "source-derived historical fact IDs. Source-derived anchors must retain their formula "
    "and input ancestry. Legacy AssumptionSupport.kind='reported' denotes historical fact "
    "binding for either class; it does not relabel a derived amount as directly "
    "issuer-reported. ",
    "Forecast dates, period labels, units, scale choices, and model conventions are analyst "
    "authoring choices rather than missing reported facts. ",
    "Future forecast values may be labeled assumptions when they have defensible, "
    "source-linked economic rationale; the cited source need not literally contain the "
    "future forecast value. ",
    "Never fabricate reported data, consensus, probabilities, or source IDs. ",
    "Do not force a model when the required capital basis, operating basis, or historical "
    "opening anchors cannot be supported and reconciled. Use model=null and identify the "
    "specific unsupported inputs instead. ",
)

_FIXED_QUERIES = {
    "fcff": (
        "reported annual or trailing revenue opening anchor",
        "reported working capital operating basis",
        "cash debt securities net debt operating versus customer balances",
        "diluted shares point in time capitalization",
        "operating margin tax depreciation amortization capex stock compensation",
        "management outlook demand margins investment and capital needs",
    ),
    "equity_fcfe": (
        "reported annual or trailing common net income opening anchor",
        "regulatory capital required capital retention operating basis",
        "diluted shares weighted average latest quarter capitalization",
        "common equity profitability losses credit funding and capital needs",
        "management outlook growth expenses risk and reinvestment",
    ),
}


class ModelProbeError(ValueError):
    """The probe cannot safely start or validate its bounded run."""


def targeted_queries(request: ResearchRequest) -> tuple[str, ...]:
    """Return deterministic bounded lexical queries for valuation source context."""

    queries: list[str] = []
    mandate = " ".join(request.mandate.split())[:512]
    if mandate:
        queries.append(mandate)
    for query in _FIXED_QUERIES[request.valuation_method]:
        if query not in queries:
            queries.append(query)
    return tuple(queries[:8])


def _model_schema(request: ResearchRequest) -> dict[str, Any]:
    schema = EquityDCFModelInput if request.valuation_method == "equity_fcfe" else FCFFModelInput
    return TypeAdapter(schema).json_schema()


def build_payload(
    request: ResearchRequest, snapshot: EvidenceSnapshot
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the exact one-call payload and a non-guaranteed admission estimate."""

    base = instruction(ROLE, ValuationProposal)
    base["system"] += " " + "".join(ANALYST_AUTHORING_POLICY)
    queries = targeted_queries(request)
    call_timeout = min(
        MAX_CALL_SECONDS,
        request.budget.call_timeout_seconds,
        request.budget.wall_seconds,
    )
    payload = {
        **base,
        "diagnostic_label": DIAGNOSTIC_LABEL,
        "analyst_authoring_policy": list(ANALYST_AUTHORING_POLICY),
        "evidence": _prompt_evidence(snapshot, queries=queries),
        "context_queries": list(queries),
        "cutoff": request.cutoff.isoformat(),
        "mandate": request.mandate,
        "stage": ROLE,
        "role_call_index": 0,
        "valuation_method": request.valuation_method,
        "share_count_basis": request.share_count_basis,
        "valuation_months": request.valuation_months,
        "return_months": request.return_months,
        "language": request.internal_language,
        "prior_stage_context": "none; standalone frozen-evidence probe",
        "financial_model_schema": _model_schema(request),
        "timeout_seconds": call_timeout,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    codec = codec_for(
        ROLE,
        payload["response_schema"],
        valuation_method=request.valuation_method,
    )
    instruction_text = (
        payload["system"]
        + system_instruction_suffix(ROLE)
        + f" Keep the final JSON within the requested {MAX_OUTPUT_TOKENS}-token output allowance."
    )
    prompt_bytes = canonical_json(
        {
            key: value
            for key, value in payload.items()
            if key not in {"system", "response_schema", "timeout_seconds"}
        }
    )
    components = {
        "trusted_instructions": len(instruction_text.encode("utf-8")),
        "canonical_prompt": len(prompt_bytes),
        "strict_wire_schema": len(canonical_json(codec.output_schema)),
    }
    estimated_input_bytes = sum(components.values())
    estimated_envelope = estimated_input_bytes + MAX_OUTPUT_TOKENS
    preflight = {
        "estimated_input_utf8_bytes": estimated_input_bytes,
        "estimated_input_components_utf8_bytes": components,
        "output_token_envelope": MAX_OUTPUT_TOKENS,
        "estimated_admission_envelope": estimated_envelope,
        "estimate_basis": (
            "UTF-8 byte heuristic over locally constructed trusted instructions, canonical "
            "prompt, and strict wire schema; excludes provider protocol overhead and is not "
            "a tokenizer measurement"
        ),
        "hard_output_cap": False,
        "admission_is_not_guarantee": True,
        "request_total_token_budget": request.budget.total_tokens,
        "admitted": estimated_envelope <= request.budget.total_tokens,
    }
    return payload, preflight


def _usage_record(usage: Usage, request: ResearchRequest) -> dict[str, Any]:
    observed = (
        usage.total_tokens > request.budget.total_tokens
        or usage.output_tokens > MAX_OUTPUT_TOKENS
    )
    return {
        "actual_usage": usage.model_dump(mode="json"),
        "known_token_lower_bound": usage.total_tokens,
        "usage_total_unknown": not usage.complete,
        "observed_budget_overshoot": True if observed else (False if usage.complete else None),
        "request_total_token_budget": request.budget.total_tokens,
        "output_token_envelope": MAX_OUTPUT_TOKENS,
    }


def _safe_hash(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def _artifact_hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    return {path.name: _safe_hash(path) for path in paths}


def _validate_worker_inputs(request: ResearchRequest, home: Path, output: Path) -> Path:
    if request.backend != "codex":
        raise ModelProbeError("model probe requires backend='codex'")
    if request.evidence_path is None:
        raise ModelProbeError("model probe requires explicit frozen evidence")
    if not home.is_dir() or home.is_symlink():
        raise ModelProbeError("codex home must be an existing non-symlink directory")
    evidence_path = request.evidence_path.resolve()
    if request.evidence_path.is_symlink() or not evidence_path.is_file():
        raise ModelProbeError("frozen evidence must be a regular non-symlink file")
    evidence_parent = evidence_path.parent
    if output == evidence_parent or output.is_relative_to(evidence_parent):
        raise ModelProbeError("probe output cannot be written inside frozen evidence inputs")
    if output == home or output.is_relative_to(home):
        raise ModelProbeError("probe output cannot be written inside the isolated runtime home")
    return evidence_path


@dataclass(frozen=True, slots=True)
class ModelProbeWorker:
    codex_home: Path

    def __call__(self, request: ResearchRequest) -> ResearchResult:
        supplied_output = Path(request.output_dir).expanduser().absolute()
        if os.path.lexists(supplied_output):
            raise ModelProbeError("probe output must not already exist")
        if not supplied_output.parent.is_dir():
            raise ModelProbeError("probe output parent must already exist")
        output = supplied_output.resolve(strict=False)
        if output.parent != supplied_output.parent.resolve():
            raise ModelProbeError("probe output path is not stable")
        evidence_path = _validate_worker_inputs(
            request, Path(self.codex_home).resolve(), output
        )
        output.mkdir(parents=False, exist_ok=False)
        started = time.monotonic()
        probe_path = output / "probe.json"
        proposal_path = output / "proposal.json"
        calculation_path = output / "calculation_result.json"
        usage_path = output / "usage.json"
        provenance_path = output / "provenance.json"

        usage = Usage()
        proposal_data: dict[str, Any] = {
            "status": "not_received",
            "reason_code": "model_call_not_dispatched",
        }
        calculation: dict[str, Any] = {
            "status": "not_calculated",
            "reason_code": "model_call_not_dispatched",
        }
        probe: dict[str, Any] = {
            "schema_version": 1,
            "diagnostic_label": DIAGNOSTIC_LABEL,
            "status": "started",
            "failure_code": None,
            "call_count": 0,
            "retry_count": 0,
            "valuation_method": request.valuation_method,
            "share_count_basis": request.share_count_basis,
        }
        provenance: dict[str, Any] = {
            "schema_version": 1,
            "diagnostic_label": DIAGNOSTIC_LABEL,
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "request_hash": digest(request.model_dump(mode="json")),
            "evidence_sha256": None,
            "evidence_sha256_after": None,
            "payload_sha256": None,
            "financial_model_schema_sha256": None,
            "model_binding": {
                "role": ROLE,
                "model": request.models[ROLE].model,
                "effort": request.models[ROLE].effort,
                "service_identity": None,
            },
            "result_binding": {},
        }
        preflight: dict[str, Any] | None = None
        evidence_before: bytes | None = None
        failure_code: str | None = None
        stop_reason = "model_probe_failed"

        def persist_progress() -> None:
            probe["elapsed_seconds"] = time.monotonic() - started
            probe["failure_code"] = failure_code
            probe["preflight"] = preflight
            atomic_write(probe_path, canonical_json(probe))
            atomic_write(proposal_path, canonical_json(proposal_data))
            atomic_write(calculation_path, canonical_json(calculation))
            atomic_write(usage_path, canonical_json(_usage_record(usage, request)))

        persist_progress()
        try:
            evidence_before = read_bytes(evidence_path)
            provenance["evidence_sha256"] = hashlib.sha256(evidence_before).hexdigest()
            payload_value = parse_json(evidence_before)
            if not isinstance(payload_value, dict):
                raise ModelProbeError("frozen evidence must be a JSON object")
            snapshot = validate_snapshot(EvidenceSnapshot.model_validate(payload_value), request)
            payload, preflight = build_payload(request, snapshot)
            provenance["payload_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
            provenance["financial_model_schema_sha256"] = digest(
                payload["financial_model_schema"]
            )
            probe["context_query_count"] = len(payload["context_queries"])
            if not preflight["admitted"]:
                failure_code = "token_budget_preflight_failed"
                probe["status"] = "preflight_failed"
                calculation = {
                    "status": "not_calculated",
                    "reason_code": failure_code,
                }
                stop_reason = "model_probe_preflight_failed"
            else:
                usage = Usage(complete=False)
                proposal_data = {
                    "status": "not_received",
                    "reason_code": "model_call_unsettled",
                }
                calculation = {
                    "status": "not_calculated",
                    "reason_code": "model_call_unsettled",
                }
                probe["status"] = "dispatched"
                probe["call_count"] = 1
                persist_progress()
                with CodexModelService(Path(self.codex_home).resolve()) as service:
                    provenance["model_binding"]["service_identity"] = getattr(
                        service, "identity", None
                    )
                    reply = service.complete(ROLE, payload, request)
                    usage = reply.usage
                    proposal_data = reply.data
                    persist_progress()
                    try:
                        proposal = ValuationProposal.model_validate(proposal_data)
                    except (TypeError, ValueError, ValidationError):
                        failure_code = "proposal_validation_failed"
                        probe["status"] = "invalid_output"
                        calculation = {
                            "status": "not_calculated",
                            "reason_code": failure_code,
                        }
                        stop_reason = "model_probe_invalid_output"
                    else:
                        try:
                            calculation = _calculate(proposal, request, snapshot)
                        except (TypeError, ValueError, ValidationError):
                            failure_code = "calculation_validation_failed"
                            probe["status"] = "invalid_output"
                            calculation = {
                                "status": "validation_failed",
                                "reason_code": failure_code,
                            }
                            stop_reason = "model_probe_invalid_output"
                        else:
                            probe["status"] = "completed"
                            stop_reason = "model_probe_completed"
                        persist_progress()
        except Exception:
            if failure_code is None:
                failure_code = (
                    "model_call_failed" if probe["call_count"] else "probe_input_validation_failed"
                )
            probe["status"] = "failed"
            calculation = {
                "status": "not_calculated",
                "reason_code": failure_code,
            }
            stop_reason = "model_probe_call_failed" if probe["call_count"] else "model_probe_failed"
        finally:
            if evidence_before is not None:
                try:
                    evidence_after = read_bytes(evidence_path)
                except (OSError, ValueError):
                    evidence_after = None
                provenance["evidence_sha256_after"] = (
                    hashlib.sha256(evidence_after).hexdigest()
                    if evidence_after is not None
                    else None
                )
                if evidence_after != evidence_before:
                    failure_code = "frozen_evidence_changed"
                    probe["status"] = "failed"
                    stop_reason = "model_probe_source_changed"
            usage_record = _usage_record(usage, request)
            probe["observed_budget_overshoot"] = usage_record[
                "observed_budget_overshoot"
            ]
            if (
                usage_record["observed_budget_overshoot"] is True
                and probe["status"] == "completed"
            ):
                probe["status"] = "completed_with_budget_overshoot"
                failure_code = "observed_token_budget_overshoot"
                stop_reason = "model_probe_budget_overshoot"
            elif not usage.complete and probe["status"] == "completed":
                probe["status"] = "completed_with_usage_unknown"
                failure_code = "actual_usage_unknown"
                stop_reason = "model_probe_usage_unknown"
            persist_progress()
            atomic_write(usage_path, canonical_json(usage_record))
            proposal_hash = _safe_hash(proposal_path)
            calculation_hash = _safe_hash(calculation_path)
            usage_hash = _safe_hash(usage_path)
            provenance["result_binding"] = {
                "probe_status": probe["status"],
                "calculation_status": calculation.get("status"),
                "proposal_sha256": proposal_hash,
                "calculation_result_sha256": calculation_hash,
                "usage_sha256": usage_hash,
            }
            atomic_write(provenance_path, canonical_json(provenance))

        paths = (
            probe_path,
            proposal_path,
            calculation_path,
            usage_path,
            provenance_path,
        )
        hashes = _artifact_hashes(paths)
        return ResearchResult(
            ticker=request.ticker,
            cutoff=request.cutoff,
            artifacts={path.name: str(path) for path in paths},
            artifact_hashes=hashes,
            assessment=Assessment(status="needs_review"),
            usage=usage,
            unresolved_gaps=(DIAGNOSTIC_LABEL,),
            stop_reason=stop_reason,
        )


def _fresh_output(value: Path) -> Path:
    supplied = Path(value).expanduser().absolute()
    if os.path.lexists(supplied):
        raise ModelProbeError("probe output must not already exist")
    parent = supplied.parent
    if not parent.is_dir():
        raise ModelProbeError("probe output parent must already exist")
    resolved = supplied.resolve(strict=False)
    if resolved.parent != parent.resolve():
        raise ModelProbeError("probe output path is not stable")
    return resolved


def _cli_success(outcome: Any, output: Path) -> bool:
    """Require the exact hash-bound probe artifact set before reporting CLI success."""

    result = getattr(outcome, "result", None)
    if (
        getattr(outcome, "status", None) != "completed"
        or not isinstance(result, ResearchResult)
        or result.stop_reason != "model_probe_completed"
        or set(result.artifacts) != set(PROBE_ARTIFACT_NAMES)
        or set(result.artifact_hashes) != set(PROBE_ARTIFACT_NAMES)
    ):
        return False
    for name in PROBE_ARTIFACT_NAMES:
        expected = output / name
        target = Path(result.artifacts[name])
        if (
            target.absolute() != expected
            or expected.is_symlink()
            or not expected.is_file()
            or expected.resolve() != expected
        ):
            return False
        try:
            actual_hash = hashlib.sha256(read_bytes(expected)).hexdigest()
        except (OSError, ValueError):
            return False
        if actual_hash != result.artifact_hashes[name]:
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--allow-live", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.allow_live:
            raise ModelProbeError("explicit live authorization is required")
        output = _fresh_output(args.output)
        codex_home = args.codex_home.expanduser().resolve()
        if not codex_home.is_dir() or args.codex_home.is_symlink():
            raise ModelProbeError("codex home must already exist")
        request = load_request(args.config)
        if request.backend != "codex" or request.evidence_path is None:
            raise ModelProbeError("probe requires Codex backend and explicit frozen evidence")
        request = request.model_copy(update={"output_dir": output})
        _validate_worker_inputs(request, codex_home, output)
        # Keep the existing 60-second cleanup allowance for shorter calls, but
        # never extend the authorized whole-worker deadline beyond 600 seconds.
        timeout = min(
            MAX_SUPERVISOR_SECONDS,
            request.budget.call_timeout_seconds + 60,
            request.budget.wall_seconds,
        )
        outcome = run_supervised(ModelProbeWorker(codex_home), request, timeout_seconds=timeout)
    except (OSError, ValueError, ValidationError):
        print(
            "model probe could not start; check authorization, paths, frozen evidence, and budget",
            file=sys.stderr,
        )
        return 2
    summary = {
        "status": outcome.status,
        "code": outcome.code,
        "stop_reason": outcome.result.stop_reason if outcome.result else None,
    }
    print(canonical_json(summary).decode("utf-8"))
    return 0 if _cli_success(outcome, output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
