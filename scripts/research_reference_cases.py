"""Validate local research reference files by bytes, with no network access.

Example:
  python scripts/research_reference_cases.py validate \
    --manifest benchmarks/research-v2/reference_cases.json \
    --analysis-cutoff 2026-09-17 \
    --reference /path/to/HOOD_Equity_Research_2026-09-18.html \
    --reference /path/to/NVDA_Equity_Research_R2a_2026-09-17.html
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from tradingagents.research.reference_cases import (
    ReferenceCaseManifest,
    validate_reference_files,
)
from tradingagents.research.storage import canonical_json, read_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate local files; does not write or fetch")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--analysis-cutoff", required=True, type=date.fromisoformat)
    validate.add_argument("--reference", required=True, type=Path, action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = ReferenceCaseManifest.model_validate(read_json(args.manifest))
    results = validate_reference_files(
        manifest, tuple(args.reference), analysis_cutoff=args.analysis_cutoff,
    )
    # Deliberately emit basenames and hashes-derived validation only, never paths.
    sys.stdout.buffer.write(canonical_json({
        "offline": True,
        "validation": [item.model_dump(mode="json") for item in results],
    }) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
