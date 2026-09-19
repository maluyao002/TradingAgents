"""Prepare or dry-run a fixed six-stage same-case recovery capsule offline.

This command never opens a provider connection, dispatches a model call, or
continues a run. The resulting capsule requests a separate destination-bound
incremental budget authorization.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli.research import load_request
from tradingagents.research.case_recovery import (
    prepare_case_recovery,
    verify_case_recovery_prefix,
    write_case_recovery_capsule,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="original source request")
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new capsule directory")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        source_request = load_request(args.config.resolve())
        prepared = prepare_case_recovery(
            source_request,
            args.source_run,
            args.codex_home,
        )
        verified = verify_case_recovery_prefix(prepared)
        if args.dry_run:
            payload = {
                "status": "validated_offline_no_capsule_written",
                "plan_sha256": verified.plan_sha256,
                "known_source_tokens": prepared.source_known_usage.total_tokens,
                "source_usage_complete": False,
                "next_stage": verified.next_stage,
            }
        else:
            capsule = write_case_recovery_capsule(verified, args.output)
            payload = {
                "status": "capsule_prepared_authorization_required",
                "plan_sha256": capsule.plan_sha256,
                "manifest_sha256": capsule.manifest_sha256,
                "capsule": str(capsule.directory),
                "known_source_tokens": prepared.source_known_usage.total_tokens,
                "source_usage_complete": False,
                "live_dispatch": False,
            }
    except (OSError, TypeError, ValueError):
        print("case-recovery source validation or capsule preparation failed", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
