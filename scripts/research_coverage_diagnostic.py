"""Bounded matched coverage diagnostics, never reports or reusable attestations.

Prepare offline, then execute one hash-approved capsule once. No retries or
fallbacks. The existing supervisor owns the whole model process tree/deadline.
"""

import argparse
import hashlib
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cli.research import load_request
from scripts.research_coverage_benchmark import benchmark_source
from tradingagents.codex.adapter import codex_failure_reason
from tradingagents.research.budget import BudgetExhausted, BudgetTracker
from tradingagents.research.contracts import Assessment, Budget, ResearchRequest, ResearchResult
from tradingagents.research.coverage_payload import build_coverage_payload
from tradingagents.research.coverage_policy import (
    coverage_batches_for_policy,
    coverage_output_envelope,
)
from tradingagents.research.models import CodexModelService
from tradingagents.research.prompt_context import model_input_bytes
from tradingagents.research.report_review import (
    ReaderVerification,
    check_dispositions,
    validated_disposition_ids,
)
from tradingagents.research.review_batches import fanout_group_dispositions, group_equivalent_issues
from tradingagents.research.services import ModelReply
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
    read_json,
)
from tradingagents.research.supervisor import run_supervised

TOKEN_CAP = 250_000
CONTROL_TOKEN_CAP = 350_000
WALL_CAP = 900
WORKER_SECONDS = 880
SUPERVISOR_SECONDS = 890
CALL_CAP = 600


def runtime_binding():
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__).resolve(), root / "scripts/research_coverage_benchmark.py"]
    for directory in ("tradingagents/research", "tradingagents/codex"):
        paths.extend(sorted((root / directory).glob("*.py")))
    return {str(path.relative_to(root)): hashlib.sha256(read_bytes(path)).hexdigest() for path in paths}


def verify_runtime(plan):
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if plan["code_revision"] != revision or plan["runtime_sha256"] != runtime_binding():
        raise ValueError("diagnostic implementation changed after preparation")


def diagnostic_budget():
    return Budget(total_tokens=TOKEN_CAP, wall_seconds=WALL_CAP, call_timeout_seconds=CALL_CAP,
                  reserve_seconds=0, reserve_tokens=0, followup_cycles=0)


def control_budget():
    return diagnostic_budget().model_copy(update={"total_tokens": CONTROL_TOKEN_CAP})


def build_plan(request, issues, reader):
    if (request.budget != diagnostic_budget() or request.backend != "codex"
            or request.quality_revision != "evidence-led-bounded" or request.report_language != "English"):
        raise ValueError("diagnostic requires the exact authorized English Codex limits")
    selected = None
    for index, batch in enumerate(coverage_batches_for_policy(issues, "packed-24")):
        legacy = coverage_batches_for_policy(batch, "legacy-12")
        if len(legacy) == 2 and len(batch) > 12:
            selected = index, batch, legacy
            break
    if selected is None:
        raise ValueError("no matched one-versus-two batch comparison fits")
    index, batch, legacy = selected
    calls = []
    for policy, batches in (("legacy-12", legacy), ("packed-24", (batch,))):
        variant = request.model_copy(update={"coverage_batch_policy": policy})
        for i, items in enumerate(batches):
            payload = build_coverage_payload(variant, items, reader,
                stage=f"verify_report-coverage-{i}", role_call_index=i, language="English")
            envelope = coverage_output_envelope(policy)
            size = model_input_bytes(payload, role="verifier", output_token_envelope=envelope,
                                     valuation_method=request.valuation_method)
            calls.append({"id": f"{policy}-{i}", "policy": policy,
                "issues": list(items), "payload": payload, "payload_sha256": digest(payload),
                "input_bytes": size, "output_envelope": envelope, "reserve_tokens": size + envelope})
    reserve = sum(call["reserve_tokens"] for call in calls)
    if reserve >= TOKEN_CAP:
        raise BudgetExhausted("complete_diagnostic_does_not_fit")
    return {"kind": "paired-coverage-diagnostic-v1", "request": request.model_dump(mode="json"),
        "calls": calls, "selection": "first_packed_batch_spanning_two_legacy_batches",
        "selected_packed_batch_index": index, "selected_issue_count": len(batch),
        "reader_sha256": hashlib.sha256(reader.encode()).hexdigest(),
        "selected_issues_sha256": digest(batch), "aggregate_reserve_tokens": reserve,
        "worker_seconds": WORKER_SECONDS, "supervisor_seconds": SUPERVISOR_SECONDS,
        "historical_total_usage_unknown": True, "report_export_authorized": False,
        "attestation_reuse_authorized": False, "automatic_retry_authorized": False,
        "token_enforcement": "conservative_admission_and_post_call_not_provider_hard_cap"}


def build_control_plan(request, issues, reader):
    """Two fixed disclosure controls, six calls and one shared allowance.

    Expected labels stay outside provider payloads. Policy order reverses between
    controls; this is a semantic pilot, not an unconfounded latency benchmark.
    """
    from tradingagents.research.coverage_disclosure_eval import disclosure_controls

    if request.budget != control_budget():
        raise ValueError("disclosure controls require the exact authorized limits")
    bounded = request.model_copy(update={"budget": diagnostic_budget()})
    baseline = build_plan(bounded, issues, reader)
    selected = baseline["calls"][-1]["issues"]
    controls = disclosure_controls(reader, selected)
    by_id = {control["id"]: control for control in controls}
    calls = []
    for control_id, order in (("operating_review_only", (2, 0, 1)),
                              ("explicit_financial_draft", (0, 1, 2))):
        variant = build_plan(bounded, selected, by_id[control_id]["reader"])
        for index in order:
            call = variant["calls"][index]
            calls.append({**call, "id": f"{control_id}/{call['id']}", "control_id": control_id})
    reserve = sum(call["reserve_tokens"] for call in calls)
    if reserve >= CONTROL_TOKEN_CAP:
        raise BudgetExhausted("complete_diagnostic_does_not_fit")
    return {**baseline, "kind": "disclosure-control-diagnostic-v1",
        "request": request.model_dump(mode="json"), "calls": calls,
        "baseline_reader": reader, "controls": controls,
        "aggregate_reserve_tokens": reserve,
        "order_limitation": "Policy order is balanced across different controls, not within each control."}


def prepare_capsule(source, request, capsule, code_revision, *, disclosure=False):
    source, capsule = Path(source).resolve(), Path(capsule).absolute()
    if capsule.exists() or capsule.resolve() != capsule or capsule.is_relative_to(source):
        raise ValueError("capsule requires a new non-symlink destination outside the source")
    benchmark = benchmark_source(source)
    verification = read_json(source / "reader_verification.json")["English"]
    candidate = read_json(source / f"stages/{verification['stage']}-reader-candidate.json")["output"]
    issues = [{k: v for k, v in row.items() if k not in {"status", "decision"}}
              for row in verification["issue_lifecycle"]["issues"] if row["status"] == "open"]
    if digest(issues) != benchmark["issues_sha256"]:
        raise ValueError("source issue inventory changed")
    metadata_bytes = read_bytes(source / "run_metadata.json")
    metadata = parse_json(metadata_bytes)
    if (metadata["ticker"] != request.ticker
            or datetime.fromisoformat(metadata["cutoff"].replace("Z", "+00:00")) != request.cutoff):
        raise ValueError("source ticker/cutoff does not match")
    prior = metadata["usage_by_stage"]["verify_report-coverage-0"][0]
    setting = request.models["verifier"]
    if (prior["model"], prior["effort"]) != (setting.model, setting.effort):
        raise ValueError("diagnostic must retain historical verifier model and effort")
    request = request.model_copy(update={"budget": control_budget() if disclosure else diagnostic_budget(),
        "output_dir": capsule / "run_1",
        "additional_report_languages": (), "dossier_dir": None, "coverage_batch_policy": "legacy-12"})
    builder = build_control_plan if disclosure else build_plan
    plan = builder(request, issues, candidate["reader_text"])
    plan.update(source_run=str(source), source_artifact_sha256=benchmark["source_artifact_sha256"],
                code_revision=code_revision, runtime_sha256=runtime_binding())
    plan["source_artifact_sha256"]["run_metadata.json"] = hashlib.sha256(metadata_bytes).hexdigest()
    verify_runtime(plan)
    for name, expected in plan["source_artifact_sha256"].items():
        if hashlib.sha256(read_bytes(source / name)).hexdigest() != expected:
            raise ValueError("source changed during preparation")
    capsule.mkdir(parents=True, exist_ok=False)
    atomic_write(capsule / "plan.json", canonical_json(plan))
    return plan


def validate_plan(plan):
    request = ResearchRequest.model_validate(plan["request"])
    calls = plan["calls"]
    if plan.get("kind") == "disclosure-control-diagnostic-v1":
        if len(calls) != 6:
            raise ValueError("disclosure diagnostic requires exactly six calls")
        expected = build_control_plan(request, calls[-1]["issues"], plan["baseline_reader"])
        for key, value in expected.items():
            if key != "selected_packed_batch_index" and plan.get(key) != value:
                raise ValueError("disclosure plan differs from its bounded contract")
        return request
    if len(calls) != 3 or [c["policy"] for c in calls] != ["legacy-12", "legacy-12", "packed-24"]:
        raise ValueError("diagnostic must contain exactly two legacy calls and one packed call")
    reader = calls[-1]["payload"]["research"]["rendered_reader"]
    # Rebuild using the selected inventory; selection index may refer to a later
    # source batch, but all three payloads and budgets must reproduce exactly.
    expected = build_plan(request, calls[-1]["issues"], reader)
    for key in ("calls", "selected_issue_count", "reader_sha256", "selected_issues_sha256",
                "aggregate_reserve_tokens", "worker_seconds", "supervisor_seconds",
                "historical_total_usage_unknown", "report_export_authorized",
                "attestation_reuse_authorized", "automatic_retry_authorized", "kind", "selection",
                "token_enforcement"):
        if plan[key] != expected[key]:
            raise ValueError("diagnostic plan differs from its bounded contract")
    return request


def execute_plan(plan, request, service, *, clock=time.monotonic):
    if validate_plan(plan) != request:
        raise ValueError("worker request differs from approved plan")
    directory = request.output_dir
    directory.mkdir(parents=True, exist_ok=False)
    tracker = BudgetTracker(request.budget.model_copy(update={"wall_seconds": WORKER_SECONDS}), clock=clock)
    trace = {"kind": plan["kind"], "plan_sha256": digest(plan), "status": "running",
        "historical_total_usage_unknown": True, "report_exported": False,
        "attestation_reusable": False, "calls": [], "pending_dispatch": False}
    files = {"diagnostic.json"}

    def save():
        trace.update(elapsed_seconds=tracker.elapsed_seconds, usage=tracker.usage.model_dump(mode="json"))
        atomic_write(directory / "diagnostic.json", canonical_json(trace))

    save()
    try:
        for index, call in enumerate(plan["calls"]):
            # Preserve capacity for the complete remaining comparison, not just
            # this call. Actual outputs are advisory-capped, so recheck each time.
            remaining_reserve = sum(c["reserve_tokens"] for c in plan["calls"][index:])
            tracker.admit(finalization=True, estimated_tokens=remaining_reserve)
            permit = tracker.reserve(call["reserve_tokens"], finalization=True)
            row = {"id": call["id"], "policy": call["policy"], "status": "dispatched",
                   "payload_sha256": call["payload_sha256"], "timeout_seconds": permit.timeout_seconds}
            if "control_id" in call:
                row["control_id"] = call["control_id"]
            trace["calls"].append(row)
            trace["pending_dispatch"] = True
            save()  # Durable intent precedes every potentially billable call.
            started = clock()
            reply = ModelReply.model_validate(service.complete("verifier", {**call["payload"],
                "timeout_seconds": permit.timeout_seconds, "max_output_tokens": call["output_envelope"]},
                request.model_copy(update={"coverage_batch_policy": call["policy"]})))
            row.update(duration_seconds=max(0, clock() - started), usage=reply.usage.model_dump(mode="json"))
            tracker.complete(permit, reply.usage)
            trace["pending_dispatch"] = False
            row["status"] = "reply_received"
            save()  # Known counters survive invalid output or later validation.
            reply_name = f"call-{index}-reply.json"
            atomic_write(directory / reply_name, canonical_json(reply))
            files.add(reply_name)
            if not reply.usage.complete:
                raise BudgetExhausted("usage_incomplete")
            if tracker.usage.total_tokens > request.budget.total_tokens or tracker.elapsed_seconds >= WORKER_SECONDS:
                raise BudgetExhausted("diagnostic_budget_exceeded")
            review = ReaderVerification.model_validate(reply.data)
            groups = group_equivalent_issues(call["issues"])
            if any(len(group.issue_ids) > 1 for group in groups):
                review = fanout_group_dispositions(review, groups)
            if review.supported_claim_ids or review.contradicted_claim_ids:
                raise ValueError("coverage diagnostic cannot adjudicate factual claims")
            reader = call["payload"]["research"]["rendered_reader"]
            checked = check_dispositions(review, call["issues"], reader)
            row.update(status="reviewed", checked_review=checked.model_dump(mode="json"),
                       valid_disposition_ids=list(validated_disposition_ids(checked, call["issues"], reader)))
            if "control_id" in call:
                from tradingagents.research.coverage_disclosure_eval import score_disclosure_control
                control = next(item for item in plan["controls"] if item["id"] == call["control_id"])
                # A legacy batch may not contain the target issue. Do not score it
                # as an omitted answer, or count it as a separate semantic trial.
                issue_ids = {item["issue_id"] for item in call["issues"]}
                expected = {key: value for key, value in control["expected_decisions"].items()
                            if key in issue_ids}
                if expected:
                    row["control_score"] = score_disclosure_control(
                        checked, call["issues"], reader, expected)
            save()
        trace["status"] = "completed"
    except BaseException as exc:
        if trace["pending_dispatch"]:
            from tradingagents.research.contracts import Usage
            tracker.record(Usage(complete=False))
        if trace["calls"]:
            row = trace["calls"][-1]
            if row["status"] != "reviewed":
                row.update(status="failed", duration_seconds=max(0, clock() - started))
        trace.update(status="failed", failure_type=type(exc).__name__, failure_reason=codex_failure_reason(exc))
        if isinstance(exc, BudgetExhausted):
            trace["budget_stop"] = str(exc) if str(exc) in {
                "usage_incomplete", "diagnostic_budget_exceeded", "budget_exhausted"
            } else "budget_exhausted"
    finally:
        try:
            service.close()
        except Exception:
            trace["cleanup_failure_reason"] = "cleanup_failed"
            if trace["status"] != "failed":
                trace.update(failure_type="CleanupError", failure_reason="cleanup_failed")
            trace["status"] = "failed"
        save()
    return ResearchResult(ticker=request.ticker, cutoff=request.cutoff,
        artifacts={name: str(directory / name) for name in sorted(files)},
        artifact_hashes={name: hashlib.sha256(read_bytes(directory / name)).hexdigest() for name in sorted(files)},
        assessment=Assessment(status="needs_review"), usage=tracker.usage,
        stop_reason="coverage_diagnostic_" + trace["status"])


@dataclass(frozen=True)
class CoverageDiagnosticWorker:
    plan: dict
    home: Path

    def __call__(self, request):
        verify_runtime(self.plan)
        return execute_plan(self.plan, request, CodexModelService(self.home))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--source-run", type=Path, required=True)
    prepare.add_argument("--capsule", type=Path, required=True)
    prepare.add_argument("--code-revision", required=True)
    prepare.add_argument("--disclosure-controls", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--approved-plan-sha256", required=True)
    run.add_argument("--codex-home", type=Path, required=True)
    run.add_argument("--allow-live", action="store_true")
    run.add_argument("--allow-advisory-token-cap", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        plan = prepare_capsule(args.source_run, load_request(args.config), args.capsule,
                               args.code_revision, disclosure=args.disclosure_controls)
        print(canonical_json({"plan_sha256": digest(plan), "reserve_tokens": plan["aggregate_reserve_tokens"],
                              "selected_issue_count": plan["selected_issue_count"]}).decode())
        return 0
    plan = read_json(args.plan)
    if not args.allow_live or digest(plan) != args.approved_plan_sha256:
        raise ValueError("exact plan approval and explicit live permission required")
    if not args.allow_advisory_token_cap:
        raise ValueError("explicit best-effort token-limit acknowledgement required")
    request = validate_plan(plan)
    verify_runtime(plan)
    if request.output_dir.exists() or request.output_dir.resolve() != request.output_dir:
        raise ValueError("diagnostic output must be fresh and non-symlink")
    if request.output_dir != args.plan.resolve().parent / "run_1":
        raise ValueError("diagnostic output is not capsule-bound")
    for name, expected in plan["source_artifact_sha256"].items():
        if hashlib.sha256(read_bytes(Path(plan["source_run"]) / name)).hexdigest() != expected:
            raise ValueError("diagnostic source changed")
    # An exclusive attempt marker prevents same-capsule renewal even when the
    # supervisor fails before it can launch a worker. It is never automatically cleared.
    (args.plan.resolve().parent / "attempt_started").mkdir(exist_ok=False)
    outcome = run_supervised(CoverageDiagnosticWorker(plan, args.codex_home.resolve()), request,
                             timeout_seconds=SUPERVISOR_SECONDS)
    summary = {"status": outcome.status, "code": outcome.code,
        "stop_reason": outcome.result.stop_reason if outcome.result else None,
        "usage": outcome.result.usage.model_dump(mode="json") if outcome.result else None}
    atomic_write(args.plan.resolve().parent / "supervisor_result.json", canonical_json(summary))
    print(canonical_json(summary).decode())
    return 0 if outcome.result and outcome.result.stop_reason == "coverage_diagnostic_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
