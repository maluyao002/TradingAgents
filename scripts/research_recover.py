"""Explicitly recover a validated failed research run into a new directory.

This command never runs automatically.  It requires separate acknowledgement of
both live calls and the source run's permanently unknown usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from cli.research import load_request
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.models import CodexModelService
from tradingagents.research.recovery import (
    RecoveryModelService,
    assert_source_unchanged,
    validate_recovery_source,
)
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.supervisor import run_supervised


@dataclass(frozen=True)
class RecoveryWorker:
    source_request: ResearchRequest
    source_run: Path
    codex_home: Path
    valuation_diagnostic: Path | None = None

    def __call__(self, request):
        plan = validate_recovery_source(
            self.source_request,
            self.source_run,
            self.codex_home,
            valuation_diagnostic=self.valuation_diagnostic,
        )
        expected = self.source_request.model_copy(update={"output_dir": request.output_dir})
        if request != expected:
            raise ValueError("recovery request differs from the validated source request")
        with CodexModelService(self.codex_home) as live_models:
            models = RecoveryModelService(
                plan,
                live_models,
                allow_live=True,
                acknowledge_unknown_usage=True,
            )
            result = run_research(
                request,
                ResearchServices(SnapshotEvidenceService(plan.snapshot), models),
            )
        assert_source_unchanged(plan)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="original source request JSON")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--valuation-diagnostic", type=Path)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--acknowledge-unknown-usage", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_live or not args.acknowledge_unknown_usage:
        print(
            "recovery requires --allow-live and --acknowledge-unknown-usage",
            file=sys.stderr,
        )
        return 2
    output = args.output.resolve()
    if output.exists():
        print("recovery output must be a new directory", file=sys.stderr)
        return 2
    if not args.codex_home.is_dir():
        print("Codex home must be an existing isolated runtime directory", file=sys.stderr)
        return 2
    try:
        source_request = load_request(args.config.resolve())
        # Parent-side validation guarantees every mismatch is detected before a
        # supervised worker can construct the live model service.
        plan = validate_recovery_source(
            source_request,
            args.source_run,
            args.codex_home,
            valuation_diagnostic=args.valuation_diagnostic,
        )
    except (OSError, ValueError, ValidationError):
        print("recovery source or diagnostic validation failed", file=sys.stderr)
        return 2
    request = source_request.model_copy(update={"output_dir": output})
    remaining = request.budget.wall_seconds - (
        plan.source_elapsed_seconds + plan.valuation_diagnostic_elapsed_seconds
    )
    if remaining <= 0:
        print("recovery has no remaining wall-clock budget", file=sys.stderr)
        return 1
    worker = RecoveryWorker(
        source_request=source_request,
        source_run=args.source_run.resolve(),
        codex_home=args.codex_home.resolve(),
        valuation_diagnostic=(
            args.valuation_diagnostic.resolve() if args.valuation_diagnostic else None
        ),
    )
    try:
        outcome = run_supervised(worker, request, timeout_seconds=remaining)
    except (OSError, ValueError):
        print("recovery could not start", file=sys.stderr)
        return 2
    payload = {
        "status": outcome.status,
        "code": outcome.code,
        "stop_reason": outcome.result.stop_reason if outcome.result else None,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.result and outcome.result.stop_reason == "completed_needs_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
