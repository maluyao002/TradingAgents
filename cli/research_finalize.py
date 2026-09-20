"""Prepare or supervise an explicitly authorized reader-candidate continuation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from cli.research import load_request
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest, ResearchResult
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryModelService,
    authorize_finalization_continuation,
    load_finalization_authorization,
    prepare_finalization_continuation,
    write_finalization_continuation_plan,
)
from tradingagents.research.models import CodexModelService
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import parse_json
from tradingagents.research.supervisor import run_supervised


@dataclass(frozen=True)
class _FinalizationWorker:
    source_dir: Path
    authorization_file: Path
    codex_home: Path

    def __call__(self, request: ResearchRequest) -> ResearchResult:
        plan = prepare_finalization_continuation(self.source_dir, request)
        authorization = load_finalization_authorization(self.authorization_file)
        authorized = authorize_finalization_continuation(plan, authorization, request)
        snapshot = EvidenceSnapshot.model_validate(parse_json(plan.frozen_inputs["evidence_path"]))
        with CodexModelService(self.codex_home) as live:
            service = FinalizationRecoveryModelService(authorized, live)
            return run_research(
                request,
                ResearchServices(SnapshotEvidenceService(snapshot), service),
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="validate and write an offline plan")
    prepare.add_argument("--source-dir", required=True, type=Path)
    prepare.add_argument("--config", required=True, type=Path, help="destination request JSON")
    prepare.add_argument("--output", required=True, type=Path, help="reviewable plan JSON")

    run = subparsers.add_parser("run", help="run only under the existing process supervisor")
    run.add_argument("--source-dir", required=True, type=Path)
    run.add_argument("--config", required=True, type=Path, help="destination request JSON")
    run.add_argument("--authorization-file", required=True, type=Path)
    run.add_argument("--codex-home", required=True, type=Path)
    run.add_argument("--allow-live", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = load_request(args.config.resolve())
        plan = prepare_finalization_continuation(args.source_dir, request)
        if args.command == "prepare":
            write_finalization_continuation_plan(plan, args.output)
            payload = {
                "status": "prepared_offline_authorization_required",
                "plan": str(args.output.resolve()),
                "plan_sha256": plan.plan_sha256,
                "new_request_identity": plan.new_request_identity,
                "current_provider_identity": plan.current_provider_identity,
                "incremental_budget": request.budget.model_dump(mode="json"),
                "live_dispatch": False,
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if not args.allow_live:
            print("candidate continuation requires --allow-live", file=sys.stderr)
            return 2
        codex_home = args.codex_home.resolve()
        shared_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        if (
            request.backend != "codex"
            or args.codex_home.is_symlink()
            or not args.codex_home.is_dir()
            or codex_home == shared_home
        ):
            print("use an existing isolated Codex runtime home", file=sys.stderr)
            return 2
        authorization = load_finalization_authorization(args.authorization_file)
        authorize_finalization_continuation(plan, authorization, request)
        worker = _FinalizationWorker(
            args.source_dir.resolve(),
            args.authorization_file.resolve(),
            codex_home,
        )
        outcome = run_supervised(worker, request)
        if outcome.result is None:
            print(json.dumps({"status": outcome.status, "code": outcome.code}), file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": outcome.status,
                    "stop_reason": outcome.result.stop_reason,
                    "artifacts": outcome.result.artifacts,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if outcome.result.stop_reason == "completed_needs_review" else 1
    except (OSError, TypeError, ValueError):
        print("candidate finalization validation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
