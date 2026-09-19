"""Continue an exact reviewed-case prefix only under a new explicit authorization.

This command never treats the original failed run's unused allowance as a known
remaining budget. The supplied authorization binds a new incremental budget and
destination; cumulative historical usage remains incomplete.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from cli.research import load_request
from tradingagents.research.case_recovery import (
    CaseRecoveryAuthorization,
    CaseRecoveryModelService,
    assert_case_recovery_source_unchanged,
    authorize_case_recovery,
    prepare_case_recovery,
    verify_case_recovery_prefix,
)
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.models import CodexModelService
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import parse_json, read_bytes
from tradingagents.research.supervisor import run_supervised


@dataclass(frozen=True)
class CaseContinuationWorker:
    source_request: ResearchRequest
    codex_home: Path
    authorization: CaseRecoveryAuthorization

    def __call__(self, request: ResearchRequest):
        verified = verify_case_recovery_prefix(prepare_case_recovery(
            self.source_request, self.source_request.output_dir, self.codex_home,
        ))
        authorized = authorize_case_recovery(verified, self.authorization, request)
        snapshot = EvidenceSnapshot.model_validate(
            parse_json(verified.prepared.frozen_inputs["evidence_path"])
        )
        with CodexModelService(self.codex_home) as live:
            models = CaseRecoveryModelService(authorized, live)
            result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
        assert_case_recovery_source_unchanged(authorized)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="new incremental continuation request")
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--acknowledge-unknown-usage", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run and not (args.allow_live and args.acknowledge_unknown_usage):
        print("continuation requires --allow-live and --acknowledge-unknown-usage", file=sys.stderr)
        return 2
    try:
        source = load_request(args.source_config.resolve())
        request = load_request(args.config.resolve())
        if request.output_dir.exists() or request.output_dir.is_symlink():
            raise ValueError("continuation output must be a fresh destination")
        authorization = CaseRecoveryAuthorization.model_validate_json(
            read_bytes(args.authorization, max_bytes=1024 * 1024)
        )
        prepared = prepare_case_recovery(source, source.output_dir, args.codex_home)
        verified = verify_case_recovery_prefix(prepared)
        authorize_case_recovery(verified, authorization, request)
    except (OSError, ValueError, TypeError):
        print("case continuation validation failed; check exact inputs and authorization", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({
            "status": "validated_offline_no_live_dispatch", "plan_sha256": verified.plan_sha256,
            "known_source_tokens": prepared.source_known_usage.total_tokens,
            "source_usage_complete": False, "incremental_budget": request.budget.model_dump(mode="json"),
        }, sort_keys=True))
        return 0
    worker = CaseContinuationWorker(source, args.codex_home.resolve(), authorization)
    try:
        outcome = run_supervised(worker, request)
    except (OSError, ValueError):
        print("case continuation could not start", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": outcome.status, "code": outcome.code,
        "stop_reason": outcome.result.stop_reason if outcome.result else None,
        "cumulative_usage_complete": False,
    }, sort_keys=True))
    return 0 if outcome.result and outcome.result.stop_reason == "completed_needs_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
