"""One fresh factual review of the exact saved editor reader; diagnostic only.

Preparation reconstructs the current engine boundary offline. Execution is a
single supervised provider call, with no research continuation or report export.
"""

import argparse
import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cli.research import load_request
from scripts.research_coverage_diagnostic import (
    execution_binding,
    runtime_binding,
    verify_execution_binding,
)
from scripts.research_writer_capture import PREFIX, SOURCE_NAMES, capture_payload, source_material
from tradingagents.codex.adapter import (
    codex_failure_diagnostic,
    codex_failure_reason,
    safe_failure_type,
)
from tradingagents.research.budget import BudgetExhausted, BudgetTracker
from tradingagents.research.contracts import (
    Assessment,
    Budget,
    ResearchRequest,
    ResearchResult,
)
from tradingagents.research.models import CodexModelService
from tradingagents.research.prompt_context import model_input_bytes
from tradingagents.research.review_lifecycle import LifecycleVerification
from tradingagents.research.services import ModelReply
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    read_bytes,
    read_json,
)
from tradingagents.research.supervisor import run_supervised

TOKEN_CAP = 1_500_000
WALL_CAP = 660
WORKER_SECONDS = 640
SUPERVISOR_SECONDS = 650
CALL_CAP = 600
OUTPUT_ENVELOPE = 16_000
EXTRA_SOURCE_NAMES = ("stages/editor.json", "stages/verify_report-reader-candidate.json",
                      "stages/verify_report-rendering-provenance.json")
SOURCE_FILES = (*SOURCE_NAMES, *EXTRA_SOURCE_NAMES)
KIND = "single-exact-reader-factual-diagnostic-v1"


def diagnostic_budget():
    return Budget(total_tokens=TOKEN_CAP, wall_seconds=WALL_CAP,
                  call_timeout_seconds=CALL_CAP, reserve_tokens=0,
                  reserve_seconds=0, followup_cycles=0)


def _source_hashes(source):
    source = Path(source)
    contents = source_material(source)
    for name in EXTRA_SOURCE_NAMES:
        path = source / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("diagnostic source artifact cannot be a symlink")
        contents[name] = read_bytes(path)
    identity = read_json(source / "run_metadata.json")["identity"]
    for name in EXTRA_SOURCE_NAMES:
        record = read_json(source / name)
        if record.get("identity") != identity or digest(record.get("output")) != record.get("output_hash"):
            raise ValueError("diagnostic source checkpoint differs from its hash or identity")
    return {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}


def _request(request):
    if (request.backend != "codex" or request.budget != diagnostic_budget()
            or request.report_language != "English"
            or request.quality_revision != "evidence-led-bounded"
            or request.additional_report_languages or request.prior_dossier_path
            or (request.models["verifier"].model, request.models["verifier"].effort)
            != ("gpt-6-sol", "xhigh")):
        raise ValueError("factual diagnostic requires bounded English Codex GPT-6 Sol/xhigh")


def _exact_reader(source, payload):
    if payload.get("stage") != "verify_report":
        raise ValueError("capture did not reach factual review")
    research = payload.get("research", {})
    reader = research.get("rendered_reader")
    if not isinstance(reader, str) or hashlib.sha256(reader.encode()).hexdigest() != research.get(
            "rendered_reader_sha256"):
        raise ValueError("captured reader hash is invalid")
    candidate = read_json(Path(source) / EXTRA_SOURCE_NAMES[1])["output"]
    provenance = read_json(Path(source) / EXTRA_SOURCE_NAMES[2])["output"]
    if (candidate.get("reader_text") != reader
            or candidate.get("reader_sha256") != research["rendered_reader_sha256"]
            or provenance.get("reader_sha256") != research["rendered_reader_sha256"]
            or research.get("rendering_provenance") != {
                key: value for key, value in provenance.items() if key != "rendering_inputs"}):
        raise ValueError("captured reader or rendering provenance differs from source")
    return research["rendered_reader_sha256"], digest(provenance)


def _binding_rows(capture):
    rows = capture.get("fixture_bindings")
    if (not isinstance(rows, list) or len(rows) != 8
            or [row.get("stage") for row in rows] != list(PREFIX)
            or any(row.get("matches_current_payload") is not True or
                   row.get("current_inputs_sha256") != row.get("historical_inputs_sha256")
                   for row in rows)):
        raise ValueError("one or more historical prefix inputs differ from reconstruction")
    return rows


def _call(payload, request):
    size = model_input_bytes(payload, role="verifier", output_token_envelope=OUTPUT_ENVELOPE,
                             valuation_method=request.valuation_method)
    reserve = size + OUTPUT_ENVELOPE
    if reserve >= TOKEN_CAP:
        raise BudgetExhausted("exact_factual_payload_does_not_fit")
    return {"payload_sha256": digest(payload), "input_bytes": size,
            "output_envelope": OUTPUT_ENVELOPE, "reserve_tokens": reserve}


def prepare_capsule(source, request, capsule, code_revision, codex_home):
    source, capsule, home = Path(source).resolve(), Path(capsule).absolute(), Path(codex_home).resolve()
    if (capsule.exists() or capsule.resolve() != capsule or capsule.is_relative_to(source)
            or source.is_relative_to(capsule) or not home.is_dir()):
        raise ValueError("requires a fresh external capsule and existing isolated Codex home")
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if code_revision != revision:
        raise ValueError("preparation revision differs from checkout")
    request = request.model_copy(update={"budget": diagnostic_budget(),
        "models": {**request.models, "verifier": request.models["verifier"].model_copy(
            update={"model": "gpt-6-sol", "effort": "xhigh"})},
        "output_dir": capsule / "run_1"})
    _request(request)
    provider = execution_binding(home)
    hashes = _source_hashes(source)
    editor = read_json(source / EXTRA_SOURCE_NAMES[0])["output"]
    capsule.mkdir(parents=True, exist_ok=False)
    payload = capture_payload(source, request, capsule / "capture", editor)
    capture = read_json(capsule / "capture/offline_capture.json")
    rows = _binding_rows(capture)
    reader_hash, provenance_hash = _exact_reader(source, payload)
    call = _call(payload, request)
    if _source_hashes(source) != hashes:
        raise ValueError("diagnostic source changed during preparation")
    atomic_write(capsule / "captured_payload.json", canonical_json(payload))
    plan = {"kind": KIND, "request": request.model_dump(mode="json"),
            "request_sha256": digest(request.model_dump(mode="json")),
            "source_run": str(source), "source_artifact_sha256": hashes,
            "code_revision": revision, "runtime_sha256": runtime_binding(), **provider,
            "call": call, "reader_sha256": reader_hash,
            "rendering_provenance_sha256": provenance_hash,
            "fixture_bindings": rows, "fixture_bindings_sha256": digest(rows),
            "historical_total_usage_unknown": True,
            "fixture_replies_are_not_fresh": True, "report_export_authorized": False,
            "attestation_reuse_authorized": False, "automatic_retry_authorized": False,
            "token_enforcement": "conservative_admission_and_post_call_not_provider_hard_cap",
            "worker_seconds": WORKER_SECONDS, "supervisor_seconds": SUPERVISOR_SECONDS}
    atomic_write(capsule / "plan.json", canonical_json(plan))
    return plan


def verify_runtime(plan):
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if plan.get("code_revision") != revision or plan.get("runtime_sha256") != runtime_binding():
        raise ValueError("diagnostic implementation changed after preparation")


def validate_plan(plan, capsule, *, home=None):
    capsule = Path(capsule).resolve()
    request = ResearchRequest.model_validate(plan["request"])
    _request(request)
    if (plan.get("kind") != KIND or request.output_dir != capsule / "run_1"
            or plan.get("request_sha256") != digest(plan["request"])
            or plan.get("fixture_bindings_sha256") != digest(plan.get("fixture_bindings"))
            or plan.get("source_run") is None
            or plan.get("historical_total_usage_unknown") is not True
            or plan.get("fixture_replies_are_not_fresh") is not True
            or plan.get("report_export_authorized") is not False
            or plan.get("attestation_reuse_authorized") is not False
            or plan.get("automatic_retry_authorized") is not False
            or plan.get("token_enforcement") != "conservative_admission_and_post_call_not_provider_hard_cap"
            or plan.get("worker_seconds") != WORKER_SECONDS
            or plan.get("supervisor_seconds") != SUPERVISOR_SECONDS):
        raise ValueError("factual diagnostic plan contract differs")
    _binding_rows({"fixture_bindings": plan["fixture_bindings"]})
    if home is not None:
        verify_execution_binding(plan, home)
    source = Path(plan["source_run"])
    if _source_hashes(source) != plan.get("source_artifact_sha256"):
        raise ValueError("diagnostic source changed")
    payload = read_json(capsule / "captured_payload.json")
    if _call(payload, request) != plan.get("call"):
        raise ValueError("captured payload differs from approved plan")
    reader_hash, provenance_hash = _exact_reader(source, payload)
    if (reader_hash != plan.get("reader_sha256")
            or provenance_hash != plan.get("rendering_provenance_sha256")):
        raise ValueError("reader binding differs from approved plan")
    capture = read_json(capsule / "capture/offline_capture.json")
    if (capture.get("payload_sha256") != digest(payload)
            or capture.get("target") != "verify_report"
            or capture.get("live_calls") != 0
            or _binding_rows(capture) != plan["fixture_bindings"]):
        raise ValueError("offline capture differs from approved plan")
    return request, payload


def execute_plan(plan, capsule, request, payload, service, *, clock=time.monotonic):
    approved_request, approved_payload = validate_plan(plan, capsule, home=getattr(service, "home", None))
    if approved_request != request or approved_payload != payload:
        raise ValueError("worker inputs differ from approved plan")
    directory = request.output_dir
    directory.mkdir(parents=True, exist_ok=False)
    tracker = BudgetTracker(request.budget.model_copy(update={"wall_seconds": WORKER_SECONDS}), clock=clock)
    trace = {"kind": KIND, "plan_sha256": digest(plan), "status": "running",
             "request_sha256": plan["request_sha256"], "payload_sha256": plan["call"]["payload_sha256"],
             "reader_sha256": plan["reader_sha256"],
             "rendering_provenance_sha256": plan["rendering_provenance_sha256"],
             "historical_total_usage_unknown": True, "usage_complete": True,
             "pending_dispatch": False, "fresh_live_calls": 0,
             "report_exported": False, "acceptance": False,
             "fixture_replies_are_not_fresh": True}

    def save():
        trace["usage"] = tracker.usage.model_dump(mode="json")
        trace["usage_complete"] = tracker.usage.complete
        trace["elapsed_seconds"] = tracker.elapsed_seconds
        atomic_write(directory / "diagnostic.json", canonical_json(trace))

    permit = None
    try:
        tracker.admit(finalization=True, estimated_tokens=plan["call"]["reserve_tokens"])
        permit = tracker.reserve(plan["call"]["reserve_tokens"], finalization=True)
        trace.update(pending_dispatch=True, fresh_live_calls=1,
                     timeout_seconds=permit.timeout_seconds)
        try:
            save()
        except BaseException:
            tracker.cancel(permit, dispatched=False)
            permit = None
            trace.update(pending_dispatch=False, fresh_live_calls=0)
            raise
        reply = ModelReply.model_validate(service.complete("verifier", {
            **payload, "timeout_seconds": permit.timeout_seconds,
            "max_output_tokens": OUTPUT_ENVELOPE}, request))
        tracker.complete(permit, reply.usage)
        permit = None
        trace["pending_dispatch"] = False
        trace["reply_usage"] = reply.usage.model_dump(mode="json")
        atomic_write(directory / "factual_reply.json", canonical_json(reply))
        trace["reply_sha256"] = hashlib.sha256(read_bytes(directory / "factual_reply.json")).hexdigest()
        if not reply.usage.complete:
            raise BudgetExhausted("usage_incomplete")
        if tracker.usage.total_tokens > TOKEN_CAP or tracker.elapsed_seconds >= WORKER_SECONDS:
            raise BudgetExhausted("diagnostic_budget_exceeded")
        checked = LifecycleVerification.model_validate(reply.data)
        trace["structured_review"] = checked.model_dump(mode="json")
        trace["status"] = "completed"
    except BaseException as exc:
        if permit is not None:
            tracker.cancel(permit, dispatched=True)
            trace["pending_dispatch"] = False
        trace.update(status="failed", failure_type=safe_failure_type(exc),
                     failure_reason=codex_failure_reason(exc))
        diagnostic = codex_failure_diagnostic(exc)
        if diagnostic is not None:
            trace["failure_diagnostic"] = diagnostic
    finally:
        try:
            service.close()
        except Exception:
            trace.update(status="failed", cleanup_failure_reason="cleanup_failed")
        save()
    path = directory / "diagnostic.json"
    return ResearchResult(ticker=request.ticker, cutoff=request.cutoff,
        artifacts={"diagnostic.json": str(path)},
        artifact_hashes={"diagnostic.json": hashlib.sha256(read_bytes(path)).hexdigest()},
        assessment=Assessment(status="needs_review"), usage=tracker.usage,
        stop_reason="factual_diagnostic_" + trace["status"])


@dataclass(frozen=True)
class FactualDiagnosticWorker:
    plan: dict
    capsule: Path
    home: Path

    def __call__(self, request):
        verify_runtime(self.plan)
        approved, payload = validate_plan(self.plan, self.capsule, home=self.home)
        if approved != request:
            raise ValueError("supervised request differs from approved plan")
        return execute_plan(self.plan, self.capsule, request, payload, CodexModelService(self.home))


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--source-run", type=Path, required=True)
    prepare.add_argument("--capsule", type=Path, required=True)
    prepare.add_argument("--code-revision", required=True)
    prepare.add_argument("--codex-home", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--approved-plan-sha256", required=True)
    run.add_argument("--codex-home", type=Path, required=True)
    run.add_argument("--allow-live", action="store_true")
    run.add_argument("--allow-advisory-token-cap", action="store_true")
    run.add_argument("--acknowledge-unknown-historical-usage", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        plan = prepare_capsule(args.source_run, load_request(args.config), args.capsule,
                               args.code_revision, args.codex_home)
        print(canonical_json({"plan_sha256": digest(plan),
              "input_bytes": plan["call"]["input_bytes"],
              "reserve_tokens": plan["call"]["reserve_tokens"],
              "reader_sha256": plan["reader_sha256"]}).decode())
        return 0
    plan_path = args.plan.resolve()
    plan = read_json(plan_path)
    if (not args.allow_live or not args.allow_advisory_token_cap
            or not args.acknowledge_unknown_historical_usage
            or digest(plan) != args.approved_plan_sha256):
        raise ValueError("exact plan approval and three live-risk acknowledgements required")
    capsule = plan_path.parent
    if plan_path != capsule / "plan.json":
        raise ValueError("diagnostic plan must be capsule-bound")
    request, _ = validate_plan(plan, capsule, home=args.codex_home)
    verify_runtime(plan)
    if request.output_dir.exists() or request.output_dir.resolve() != request.output_dir:
        raise ValueError("diagnostic output must be fresh")
    (capsule / "attempt_started").mkdir(exist_ok=False)
    outcome = run_supervised(FactualDiagnosticWorker(plan, capsule, args.codex_home.resolve()),
                             request, timeout_seconds=SUPERVISOR_SECONDS)
    summary = {"status": outcome.status, "code": outcome.code,
               "stop_reason": outcome.result.stop_reason if outcome.result else None,
               "usage": outcome.result.usage.model_dump(mode="json") if outcome.result else None}
    atomic_write(capsule / "supervisor_result.json", canonical_json(summary))
    print(canonical_json(summary).decode())
    return 0 if outcome.result and outcome.result.stop_reason == "factual_diagnostic_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
