"""Command-line entry point for unattended weekly watchlist analysis."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tradingagents.weekly import WeeklyRunError, load_config, read_manifest, run_batch


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the TradingAgents weekly watchlist")
    parser.add_argument("--config", required=True, help="Path to weekly JSON configuration")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and create the manifest without provider/model calls",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check output, data credentials, Codex auth, and selected models without inference",
    )
    parser.add_argument("--resume", action="store_true", help="Resume an existing batch")
    parser.add_argument("--batch-id", help="Stable batch identifier (defaults to weekly Saturday)")
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Configured ticker subset for a pilot run, for example: AMD INTC",
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        help="Run at most this many currently eligible companies; resume later for the rest",
    )
    return parser


def _summary(manifest_path: Path) -> dict:
    manifest = read_manifest(manifest_path)
    counts: dict[str, int] = {}
    for company in manifest["companies"].values():
        status = company.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "ok": not any(status in counts for status in ("failed", "blocked")),
        "batch_id": manifest["batch_id"],
        "manifest_path": str(manifest_path.resolve()),
        "statuses": counts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run and args.preflight_only:
        _parser().error("--dry-run and --preflight-only are mutually exclusive")
    manifest_path: Path | None = None
    try:
        config = load_config(args.config)
        manifest_path = run_batch(
            config,
            dry_run=args.dry_run,
            preflight_only=args.preflight_only,
            resume=args.resume,
            batch_id=args.batch_id,
            tickers=args.tickers,
            max_companies=args.max_companies,
        )
        result = _summary(manifest_path)
        if args.dry_run:
            result["mode"] = "dry_run"
        elif args.preflight_only:
            result["mode"] = "preflight"
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0 if result["ok"] else 1
    except WeeklyRunError as exc:
        result = {
            "ok": False,
            "error": exc.code,
            "manifest_path": str(manifest_path.resolve()) if manifest_path else None,
        }
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
