"""Explicit one-call research diagnostic; never retries or weakens isolation.

The diagnostic log retains only protocol names, allowlisted settings and error
signatures. A successful validated research reply is a separate local artifact.
No raw prompts, upstream error prose, credentials or message deltas are logged.
One inference turn is attempted; configured models also receive capability checks.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from cli.research import load_request
from tradingagents.codex.adapter import CodexAdapterError
from tradingagents.research.contracts import Assessment, ResearchResult, Usage
from tradingagents.research.engine import _calculate, _prompt_evidence, _validate_analysis
from tradingagents.research.evidence import load_snapshot
from tradingagents.research.models import CodexModelService, _ClosingSafeAdapter
from tradingagents.research.stages import AnalysisOutput, ValuationProposal, instruction
from tradingagents.research.storage import atomic_write, canonical_json, digest, parse_json, read_bytes, request_identity
from tradingagents.research.supervisor import run_supervised
from tradingagents.research.valuation import FCFFModelInput
from tradingagents.research.wire import WIRE_SCHEMA_VERSION


def load_valuation_context(directory, request, home):
    """Read an immutable, hash-bound v1 analysis prefix; no checkpoint writes."""
    if directory is None or directory.is_symlink():
        raise ValueError("valuation diagnostic requires an original source run")
    hashes = {}

    def read(name, *, json=True):
        path = directory / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("source diagnostic paths cannot be symlinks")
        content = read_bytes(path)
        hashes[name] = hashlib.sha256(content).hexdigest()
        return parse_json(content) if json else content

    result = ResearchResult.model_validate(read("result.json"))
    for name, expected in result.artifact_hashes.items():
        if Path(name).name != name:
            raise ValueError("invalid source artifact name")
        read(name, json=False)
        if hashes[name] != expected:
            raise ValueError("source artifact hash mismatch")
    metadata = read("run_metadata.json")
    source_identity = digest({"request": request_identity(request), "model_service": digest({
        "service": "isolated-codex-v1", "wire": "research-wire-v1", "home": str(home.resolve())})})
    if (metadata["identity"] != source_identity or result.ticker != request.ticker
            or result.cutoff != request.cutoff or result.stop_reason != "stage_failed"
            or metadata.get("failure_type") != "CodexStructuredOutputError"
            or result.usage.complete or read("research_checkpoint.json") != {
                "schema_version": 1, "identity": source_identity}):
        raise ValueError("source run is not the matching failed valuation run")
    research = read("research.json")
    stages = {"planner": "planner", "independent_challenge": "challenger",
              "business": "business", "accounting": "accounting",
              "expectations": "expectations", "management": "management"}
    if set(research) != set(stages.values()):
        raise ValueError("source run lacks the six-stage analysis prefix")
    for stage, key in stages.items():
        saved = read(f"stages/{stage}.json")
        if (saved.get("identity") != source_identity or saved.get("output") != research[key]
                or saved.get("output_hash") != digest(saved.get("output"))):
            raise ValueError("source stage hash mismatch")
    resources = read("stages/resources.json")
    if (resources.get("identity") != source_identity
            or resources.get("output_hash") != digest(resources.get("output"))
            or resources["output"].get("dispatched") is not True):
        raise ValueError("source unsettled usage record mismatch")
    return research, hashes


def summarize_event(event):
    method = event.get("method", "")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Za-z/]+", method):
        method = "unrecognized"
    result = {"method": method}
    params = event.get("params", {})
    if not isinstance(params, dict):
        return result
    if method == "thread/settings/updated":
        settings = params.get("threadSettings", {})
        if isinstance(settings, dict):
            for key in ("model", "effort", "modelProvider", "approvalPolicy", "approvalsReviewer"):
                value = settings.get(key)
                result[key] = value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,60}", value) else "unrecognized"
            sandbox = settings.get("sandboxPolicy", {})
            sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else None
            result["sandbox_type"] = sandbox_type if sandbox_type in ("readOnly", "workspaceWrite", "dangerFullAccess") else "unrecognized"
    error = params.get("error")
    if method == "turn/completed" and isinstance(params.get("turn"), dict):
        error = params["turn"].get("error")
    if isinstance(error, dict):
        info = error.get("codexErrorInfo")
        codes = {"badRequest", "other", "unauthorized", "usageLimitExceeded", "sessionBudgetExceeded",
                 "internalServerError", "rateLimitExceeded", "serverOverloaded", "contextWindowExceeded"}
        result["error_code"] = info if isinstance(info, str) and info in codes else "unclassified"
        message = str(error.get("message", "")).lower()
        result["error_signatures"] = [s for s in (
            "invalid schema", "additionalproperties", "required", "invalid_json_schema",
            "quota", "usage limit", "unauthorized", "unsupported", "not supported",
            "pattern", "lookahead", "look-ahead", "regex", "format", "anyof", "minitems", "maxitems",
        ) if s in message]
    return result


@dataclass(frozen=True)
class DiagnosticWorker:
    home: Path
    role: str = "planner"
    source_run: Path | None = None

    def __call__(self, request):
        directory = request.output_dir
        directory.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        trace = {"mode": f"one_{self.role}_call", "status": "started", "events": [], "phases": [],
                 "usage": Usage(complete=False).model_dump(mode="json")}
        path = directory / "diagnostic.json"

        def save():
            trace["elapsed_seconds"] = time.monotonic() - started
            atomic_write(path, canonical_json(trace))

        class ObservedAdapter(_ClosingSafeAdapter):
            def preflight(self, *args, **kwargs):
                result = super().preflight(*args, **kwargs)
                trace["phases"].append("model_preflight_passed")
                save()
                return result

            def _wait_for_turn(self, *args, **kwargs):
                transport = self._require_transport()
                original = transport.wait_notification

                def observe(*a, **k):
                    event = original(*a, **k)
                    # Text chunks are numerous and contain no useful diagnostic
                    # content here. Do not fsync on every streamed token.
                    if event.get("method") == "item/agentMessage/delta":
                        trace["stream_delta_events"] = trace.get("stream_delta_events", 0) + 1
                        return event
                    trace["events"] = (trace["events"] + [summarize_event(event)])[-80:]
                    try:
                        save()
                    except OSError:
                        # Observability must not abort the provider call. Final
                        # artifact persistence is still required before success.
                        trace["intermediate_write_failed"] = True
                    return event

                transport.wait_notification = observe
                trace["phases"].append("turn_started_waiting")
                save()
                try:
                    return super()._wait_for_turn(*args, **kwargs)
                finally:
                    transport.wait_notification = original

        save()
        reply = None
        try:
            snapshot = load_snapshot(request.evidence_path, request)
            if self.role not in {"planner", "valuation"}:
                raise ValueError("unsupported diagnostic role")
            research, source_hashes = load_valuation_context(self.source_run, request, self.home) \
                if self.role == "valuation" else ({}, {})
            for item in research.values():
                _validate_analysis(AnalysisOutput.model_validate(item), snapshot)
            schema = ValuationProposal if self.role == "valuation" else AnalysisOutput
            payload = {**instruction(self.role, schema), "evidence": _prompt_evidence(snapshot),
                       "research": research, "cutoff": request.cutoff.isoformat(), "mandate": request.mandate,
                       "stage": self.role, "role_call_index": 0,
                       "valuation_months": request.valuation_months, "return_months": request.return_months,
                       "language": request.internal_language}
            if self.role == "valuation":
                payload["financial_model_schema"] = TypeAdapter(FCFFModelInput).json_schema()
            payload_hash = digest(payload)
            with CodexModelService(self.home, adapter_factory=ObservedAdapter) as service:
                reply = service.complete(self.role, {**payload, "timeout_seconds": 300,
                                                     "max_output_tokens": 16000}, request)
                trace["usage"] = reply.usage.model_dump(mode="json")
            if not reply.usage.complete:
                raise ValueError("diagnostic reply lacks complete usage")
            if self.role == "valuation":
                _calculate(ValuationProposal.model_validate(reply.data), request, snapshot)
            else:
                analysis = _validate_analysis(AnalysisOutput.model_validate(reply.data), snapshot)
                if not 3 <= len(analysis.questions) <= 5:
                    raise ValueError("planner must supply 3-5 decisive questions")
                if len({claim.id for claim in analysis.claims}) != len(analysis.claims):
                    raise ValueError("duplicate claim identifiers within a stage")
                if any(finding.question_id not in {q.id for q in analysis.questions}
                       for finding in analysis.findings):
                    raise ValueError("finding references an unknown research question")
            content = canonical_json(reply)
            atomic_write(directory / f"{self.role}_reply.json", content)
            atomic_write(directory / "provenance.json", canonical_json({
                "schema_version": 1, "role": self.role, "payload_hash": payload_hash,
                "request_identity": request_identity(request),
                "source_artifact_hashes": source_hashes, "wire_schema_version": WIRE_SCHEMA_VERSION,
                "reply_sha256": hashlib.sha256(content).hexdigest()}))
            trace["status"] = "succeeded"
        except Exception as exc:
            trace["status"] = "failed"
            trace["failure_type"] = type(exc).__name__
            if isinstance(exc, CodexAdapterError):
                # This exception family contains fixed/redacted local messages.
                trace["safe_error"] = str(exc)
        finally:
            save()
        files = [path] + ([directory / f"{self.role}_reply.json", directory / "provenance.json"]
                         if trace["status"] == "succeeded" else [])
        return ResearchResult(ticker=request.ticker, cutoff=request.cutoff,
            artifacts={p.name: str(p) for p in files},
            artifact_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            assessment=Assessment(status="needs_review"),
            usage=reply.usage if reply else Usage(complete=False), stop_reason="diagnostic_" + trace["status"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--role", choices=("planner", "valuation"), default="planner")
    parser.add_argument("--source-run", type=Path)
    args = parser.parse_args()
    if not args.allow_live or args.output.exists():
        parser.error("explicit live authorization and a new output directory are required")
    if (args.role == "valuation") != (args.source_run is not None):
        parser.error("valuation diagnostics require --source-run; planner diagnostics do not")
    request = load_request(args.config).model_copy(update={"output_dir": args.output.resolve()})
    outcome = run_supervised(DiagnosticWorker(args.codex_home, args.role, args.source_run),
                             request, timeout_seconds=360)
    print(canonical_json({"status": outcome.status, "code": outcome.code,
                         "stop_reason": outcome.result.stop_reason if outcome.result else None}).decode())
    return 0 if outcome.result and outcome.result.stop_reason == "diagnostic_succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
