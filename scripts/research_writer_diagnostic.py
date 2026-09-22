"""One writer plus one fresh factual-review diagnostic; never a research run."""

import argparse
import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cli.research import load_request
from scripts.research_coverage_diagnostic import (
    execution_binding,
    runtime_binding as coverage_runtime_binding,
    verify_execution_binding,
)
from scripts.research_writer_capture import SOURCE_NAMES, capture_payload, source_material
from tradingagents.codex.adapter import codex_failure_reason
from tradingagents.research.budget import BudgetExhausted, BudgetTracker
from tradingagents.research.contracts import (
    Assessment,
    Budget,
    ResearchRequest,
    ResearchResult,
    Usage,
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

TOKEN_CAP, WALL_CAP, WORKER_SECONDS, SUPERVISOR_SECONDS, CALL_CAP = 2_000_000, 900, 880, 890, 600
WRITER_OUTPUT_ENVELOPE, FACTUAL_OUTPUT_ENVELOPE = 16_000, 16_000
SECOND_PHASE_ALGORITHM = {
    "capture": "research_writer_capture.capture_payload",
    "target": "verify_report",
    "writer_schema": "CaseReportDraft",
    "source_names": list(SOURCE_NAMES),
    "reader_growth_estimate_bytes": 64_000,
}
FIXTURE_POLICY = {
    "classification": "offline_fixture_not_current_validated_recovery",
    "input_mismatches": "disclosed_not_rejected",
    "recovery_authorized": False,
    "acceptance_authorized": False,
    "attestation_reuse_authorized": False,
}


def diagnostic_budget():
    return Budget(
        total_tokens=TOKEN_CAP,
        wall_seconds=WALL_CAP,
        call_timeout_seconds=CALL_CAP,
        reserve_seconds=0,
        reserve_tokens=0,
        followup_cycles=0,
    )


def runtime_binding():
    """Use the shared diagnostic closure, including CLI/script/profile imports."""
    return coverage_runtime_binding()


def _validate_request(request):
    if (
        request.backend != "codex"
        or request.budget != diagnostic_budget()
        or request.report_language != "English"
        or request.quality_revision != "evidence-led-bounded"
        or request.additional_report_languages
        or request.budget.followup_cycles
    ):
        raise ValueError("writer diagnostic requires exact bounded English Codex settings")
    if (request.models["editor"].model, request.models["editor"].effort) != ("gpt-6-astra", "high"):
        raise ValueError("writer diagnostic requires original Astra/high editor")
    if (request.models["verifier"].model, request.models["verifier"].effort) != (
        "gpt-5.6-sol",
        "high",
    ):
        raise ValueError("writer diagnostic requires original Sol/high verifier")


def _call(payload, role, request, envelope):
    size = model_input_bytes(
        payload,
        role=role,
        output_token_envelope=envelope,
        valuation_method=request.valuation_method,
    )
    return {
        "role": role,
        "payload": payload,
        "payload_sha256": digest(payload),
        "input_bytes": size,
        "output_envelope": envelope,
        "reserve_tokens": size + envelope,
    }


def prepare_capsule(source, request, capsule, code_revision, codex_home):
    source, capsule, home = (
        Path(source).resolve(),
        Path(capsule).absolute(),
        Path(codex_home).resolve(),
    )
    if (
        capsule.exists()
        or capsule.resolve() != capsule
        or capsule.is_relative_to(source)
        or not home.is_dir()
    ):
        raise ValueError("requires fresh external capsule and existing isolated Codex home")
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if code_revision != revision:
        raise ValueError("preparation revision must match the current checkout")
    provider = execution_binding(home)
    request = request.model_copy(
        update={"budget": diagnostic_budget(), "output_dir": capsule / "run_1"}
    )
    _validate_request(request)
    materials = source_material(source)
    capsule.mkdir(parents=True, exist_ok=False)
    writer_payload = capture_payload(source, request, capsule / "writer-capture")
    if writer_payload.get("stage") != "editor":
        raise ValueError("capture did not produce initial writer payload")
    writer = _call(writer_payload, "editor", request, WRITER_OUTPUT_ENVELOPE)
    fixture_bindings = read_json(capsule / "writer-capture/offline_capture.json")["fixture_bindings"]
    # The second payload is generated only from the schema-validated new writer output.
    # This is admission planning, not a provider hard cap: current full evidence/case
    # context is never truncated and the actual factual payload is admitted again.
    second_estimate = second_phase_reserve(writer)
    if writer["reserve_tokens"] + second_estimate >= TOKEN_CAP:
        raise BudgetExhausted("complete_diagnostic_does_not_fit")
    plan = {
        "kind": "writer-factual-diagnostic-v1",
        "request": request.model_dump(mode="json"),
        "source_run": str(source),
        "source_artifact_sha256": {
            name: hashlib.sha256(value).hexdigest() for name, value in materials.items()
        },
        "code_revision": code_revision,
        "runtime_sha256": runtime_binding(),
        **provider,
        "writer": writer,
        "fixture_policy": FIXTURE_POLICY,
        "fixture_bindings": fixture_bindings,
        "fixture_bindings_sha256": digest(fixture_bindings),
        "second_phase_algorithm": SECOND_PHASE_ALGORITHM,
        "initial_reserve_tokens": writer["reserve_tokens"],
        "second_phase_reserve_estimate": second_estimate,
        "worker_seconds": WORKER_SECONDS,
        "supervisor_seconds": SUPERVISOR_SECONDS,
        "historical_total_usage_unknown": True,
        "report_export_authorized": False,
        "automatic_retry_authorized": False,
        "formal_sol_review_required": True,
    }
    atomic_write(capsule / "plan.json", canonical_json(plan))
    return plan


def second_phase_reserve(writer):
    return (2 * writer["input_bytes"] + SECOND_PHASE_ALGORITHM["reader_growth_estimate_bytes"]
            + FACTUAL_OUTPUT_ENVELOPE)


def validate_plan(plan, *, home=None):
    request = ResearchRequest.model_validate(plan["request"])
    _validate_request(request)
    if (
        plan.get("kind") != "writer-factual-diagnostic-v1"
        or plan.get("second_phase_algorithm") != SECOND_PHASE_ALGORITHM
        or plan.get("formal_sol_review_required") is not True
        or plan.get("fixture_policy") != FIXTURE_POLICY
        or not isinstance(plan.get("fixture_bindings"), list)
        or plan.get("fixture_bindings_sha256") != digest(plan.get("fixture_bindings"))
    ):
        raise ValueError("writer diagnostic plan contract differs")
    for key, value in {
        "worker_seconds": WORKER_SECONDS,
        "supervisor_seconds": SUPERVISOR_SECONDS,
        "historical_total_usage_unknown": True,
        "report_export_authorized": False,
        "automatic_retry_authorized": False,
    }.items():
        if plan.get(key) != value:
            raise ValueError("writer diagnostic bounds differ from approved plan")
    expected = _call(plan["writer"]["payload"], "editor", request, WRITER_OUTPUT_ENVELOPE)
    if (
        plan.get("writer") != expected
        or plan.get("initial_reserve_tokens") != expected["reserve_tokens"]
        or plan.get("second_phase_reserve_estimate") != second_phase_reserve(expected)
    ):
        raise ValueError("initial writer payload differs from approved plan")
    if expected["payload"].get("stage") != "editor":
        raise ValueError("initial diagnostic payload must target editor")
    if expected["reserve_tokens"] + second_phase_reserve(expected) >= TOKEN_CAP:
        raise BudgetExhausted("complete_diagnostic_does_not_fit")
    if home is not None:
        verify_execution_binding(plan, home)
    return request


def verify_source(plan):
    materials = source_material(plan["source_run"])
    actual = {name: hashlib.sha256(value).hexdigest() for name, value in materials.items()}
    if actual != plan["source_artifact_sha256"]:
        raise ValueError("diagnostic source changed")


def verify_runtime(plan):
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if plan["code_revision"] != revision or plan["runtime_sha256"] != runtime_binding():
        raise ValueError("diagnostic implementation changed after preparation")


def execute_plan(plan, request, service, *, clock=time.monotonic):
    if validate_plan(plan, home=getattr(service, "home", None)) != request:
        raise ValueError("worker request differs from approved plan")
    verify_source(plan)
    directory = request.output_dir
    directory.mkdir(parents=True, exist_ok=False)
    tracker = BudgetTracker(
        request.budget.model_copy(update={"wall_seconds": WORKER_SECONDS}), clock=clock
    )
    trace = {
        "kind": plan["kind"],
        "plan_sha256": digest(plan),
        "status": "running",
        "calls": [],
        "pending_dispatch": False,
        "historical_total_usage_unknown": True,
        "report_exported": False,
    }

    def save():
        trace.update(
            elapsed_seconds=tracker.elapsed_seconds, usage=tracker.usage.model_dump(mode="json")
        )
        atomic_write(directory / "diagnostic.json", canonical_json(trace))

    def dispatch(call):
        tracker.admit(finalization=True, estimated_tokens=call["reserve_tokens"])
        permit = tracker.reserve(call["reserve_tokens"], finalization=True)
        row = {
            "role": call["role"],
            "payload_sha256": call["payload_sha256"],
            "status": "dispatched",
            "timeout_seconds": permit.timeout_seconds,
        }
        trace["calls"].append(row)
        trace["pending_dispatch"] = True
        try:
            save()
        except BaseException:
            tracker.cancel(permit, dispatched=False)
            trace["pending_dispatch"] = False
            row["status"] = "pre_dispatch_persist_failed"
            raise
        started = clock()
        reply = ModelReply.model_validate(
            service.complete(
                call["role"],
                {
                    **call["payload"],
                    "timeout_seconds": permit.timeout_seconds,
                    "max_output_tokens": call["output_envelope"],
                },
                request,
            )
        )
        tracker.complete(permit, reply.usage)
        trace["pending_dispatch"] = False
        row.update(
            status="reply_received",
            duration_seconds=max(0, clock() - started),
            usage=reply.usage.model_dump(mode="json"),
        )
        save()
        return reply, row

    try:
        tracker.admit(
            finalization=True,
            estimated_tokens=plan["initial_reserve_tokens"] + plan["second_phase_reserve_estimate"],
        )
        writer_reply, writer_row = dispatch(plan["writer"])
        atomic_write(directory / "writer-reply.json", canonical_json(writer_reply))
        writer_row["reply"] = "writer-reply.json"
        save()
        if not writer_reply.usage.complete:
            raise BudgetExhausted("usage_incomplete")
        if tracker.usage.total_tokens > TOKEN_CAP or tracker.elapsed_seconds >= WORKER_SECONDS:
            raise BudgetExhausted("diagnostic_budget_exceeded")
        verify_source(plan)
        factual_payload = capture_payload(
            plan["source_run"], request, directory / "factual-capture", writer_reply.data
        )
        if factual_payload.get("stage") != "verify_report":
            raise ValueError("capture did not produce factual payload")
        factual = _call(factual_payload, "verifier", request, FACTUAL_OUTPUT_ENVELOPE)
        atomic_write(directory / "factual-payload.json", canonical_json(factual_payload))
        trace["factual_payload_sha256"] = factual["payload_sha256"]
        research = factual_payload.get("research", {})
        reader = research.get("rendered_reader")
        if not isinstance(reader, str) or hashlib.sha256(reader.encode()).hexdigest() != research.get(
                "rendered_reader_sha256"):
            raise ValueError("fresh factual payload lacks an exact reader binding")
        atomic_write(directory / "reader_candidate.md", reader.encode())
        trace["reader_candidate_sha256"] = research["rendered_reader_sha256"]
        trace["reader_candidate_status"] = "diagnostic_only_not_accepted_or_coverage_verified"
        save()
        factual_reply, factual_row = dispatch(factual)
        atomic_write(directory / "factual-reply.json", canonical_json(factual_reply))
        factual_row["reply"] = "factual-reply.json"
        save()
        if not factual_reply.usage.complete:
            raise BudgetExhausted("usage_incomplete")
        if tracker.usage.total_tokens > TOKEN_CAP or tracker.elapsed_seconds >= WORKER_SECONDS:
            raise BudgetExhausted("diagnostic_budget_exceeded")
        checked = LifecycleVerification.model_validate(factual_reply.data)
        factual_row["checked_factual_review"] = checked.model_dump(mode="json")
        factual_row["factual_reviewed_report"] = checked.reviewed_report
        save()
        trace["status"] = "completed"
    except BaseException as exc:
        if trace["pending_dispatch"]:
            tracker.record(Usage(complete=False))
        trace.update(
            status="failed",
            failure_type=type(exc).__name__,
            failure_reason=codex_failure_reason(exc),
        )
    finally:
        try:
            service.close()
        except Exception:
            trace["status"] = "failed"
            trace["cleanup_failure_reason"] = "cleanup_failed"
        save()
    return ResearchResult(
        ticker=request.ticker,
        cutoff=request.cutoff,
        artifacts={"diagnostic.json": str(directory / "diagnostic.json")},
        artifact_hashes={
            "diagnostic.json": hashlib.sha256(read_bytes(directory / "diagnostic.json")).hexdigest()
        },
        assessment=Assessment(status="needs_review"),
        usage=tracker.usage,
        stop_reason="writer_diagnostic_" + trace["status"],
    )


@dataclass(frozen=True)
class WriterDiagnosticWorker:
    plan: dict
    home: Path

    def __call__(self, request):
        verify_runtime(self.plan)
        verify_execution_binding(self.plan, self.home)
        return execute_plan(self.plan, request, CodexModelService(self.home))


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
    args = parser.parse_args(argv)
    if args.command == "prepare":
        plan = prepare_capsule(
            args.source_run,
            load_request(args.config),
            args.capsule,
            args.code_revision,
            args.codex_home,
        )
        print(
            canonical_json(
                {
                    "plan_sha256": digest(plan),
                    "initial_reserve_tokens": plan["initial_reserve_tokens"],
                    "second_phase_reserve_estimate": plan["second_phase_reserve_estimate"],
                }
            ).decode()
        )
        return 0
    plan = read_json(args.plan)
    if (
        not args.allow_live
        or not args.allow_advisory_token_cap
        or digest(plan) != args.approved_plan_sha256
    ):
        raise ValueError("exact plan approval and explicit live permission required")
    request = validate_plan(plan, home=args.codex_home)
    verify_runtime(plan)
    if (
        request.output_dir.exists()
        or request.output_dir.resolve() != request.output_dir
        or request.output_dir != args.plan.resolve().parent / "run_1"
    ):
        raise ValueError("diagnostic output must be fresh and capsule-bound")
    verify_source(plan)
    (args.plan.resolve().parent / "attempt_started").mkdir(exist_ok=False)
    outcome = run_supervised(
        WriterDiagnosticWorker(plan, args.codex_home.resolve()),
        request,
        timeout_seconds=SUPERVISOR_SECONDS,
    )
    summary = {"status": outcome.status, "code": outcome.code,
        "stop_reason": outcome.result.stop_reason if outcome.result else None,
        "usage": outcome.result.usage.model_dump(mode="json") if outcome.result else None}
    atomic_write(args.plan.resolve().parent / "supervisor_result.json", canonical_json(summary))
    print(canonical_json(summary).decode())
    return (
        0 if outcome.result and outcome.result.stop_reason == "writer_diagnostic_completed" else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
