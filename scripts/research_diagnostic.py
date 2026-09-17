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

from cli.research import load_request
from tradingagents.codex.adapter import CodexAdapterError
from tradingagents.research.contracts import Assessment, ResearchResult, Usage
from tradingagents.research.engine import _prompt_evidence, _validate_analysis
from tradingagents.research.evidence import load_snapshot
from tradingagents.research.models import CodexModelService, _ClosingSafeAdapter
from tradingagents.research.stages import AnalysisOutput, instruction
from tradingagents.research.storage import atomic_write, canonical_json
from tradingagents.research.supervisor import run_supervised


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
        ) if s in message]
    return result


@dataclass(frozen=True)
class DiagnosticWorker:
    home: Path

    def __call__(self, request):
        directory = request.output_dir
        directory.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        trace = {"mode": "one_planner_call", "status": "started", "events": [], "phases": [],
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
            payload = {**instruction("planner", AnalysisOutput), "evidence": _prompt_evidence(snapshot),
                       "research": {}, "cutoff": request.cutoff.isoformat(), "mandate": request.mandate,
                       "stage": "planner", "role_call_index": 0,
                       "valuation_months": request.valuation_months, "return_months": request.return_months,
                       "language": request.internal_language, "timeout_seconds": 300, "max_output_tokens": 16000}
            with CodexModelService(self.home, adapter_factory=ObservedAdapter) as service:
                reply = service.complete("planner", payload, request)
                trace["usage"] = reply.usage.model_dump(mode="json")
            analysis = _validate_analysis(AnalysisOutput.model_validate(reply.data), snapshot)
            if not 3 <= len(analysis.questions) <= 5:
                raise ValueError("planner must supply 3-5 decisive questions")
            if len({claim.id for claim in analysis.claims}) != len(analysis.claims):
                raise ValueError("duplicate claim identifiers within a stage")
            if any(finding.question_id not in {q.id for q in analysis.questions}
                   for finding in analysis.findings):
                raise ValueError("finding references an unknown research question")
            trace["status"] = "succeeded"
            atomic_write(directory / "planner_reply.json", canonical_json(reply))
        except Exception as exc:
            trace["status"] = "failed"
            trace["failure_type"] = type(exc).__name__
            if isinstance(exc, CodexAdapterError):
                # This exception family contains fixed/redacted local messages.
                trace["safe_error"] = str(exc)
        finally:
            save()
        files = [path] + ([directory / "planner_reply.json"] if trace["status"] == "succeeded" else [])
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
    args = parser.parse_args()
    if not args.allow_live or args.output.exists():
        parser.error("explicit live authorization and a new output directory are required")
    request = load_request(args.config).model_copy(update={"output_dir": args.output.resolve()})
    outcome = run_supervised(DiagnosticWorker(args.codex_home), request, timeout_seconds=360)
    print(canonical_json({"status": outcome.status, "code": outcome.code,
                         "stop_reason": outcome.result.stop_reason if outcome.result else None}).decode())
    return 0 if outcome.result and outcome.result.stop_reason == "diagnostic_succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
