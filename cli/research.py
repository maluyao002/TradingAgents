"""Standalone configuration boundary for the opt-in research workflow.

M0 deliberately supports validation only.  It never creates output directories,
contacts providers, or initializes a model client.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.storage import read_json

_PATH_FIELDS = ("output_dir", "evidence_path", "prior_dossier_path", "dossier_dir")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an opt-in investment research request configuration."
    )
    parser.add_argument("--config", required=True, type=Path, help="ResearchRequest JSON file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print a normalized configuration summary without execution.",
    )
    return parser


def _resolve_paths(config: Mapping[str, Any], config_parent: Path) -> dict[str, Any]:
    """Resolve declared request paths without accessing or creating their targets."""
    normalized = dict(config)
    for field in _PATH_FIELDS:
        value = normalized.get(field)
        if isinstance(value, (str, Path)):
            path = Path(value)
            normalized[field] = (path if path.is_absolute() else config_parent / path).resolve()
    return normalized


def load_request(config_path: Path) -> ResearchRequest:
    """Load a JSON request and resolve request paths relative to its parent directory."""
    try:
        payload = read_json(config_path, max_bytes=1024 * 1024)
    except (OSError, ValueError, UnicodeError):
        raise ValueError("configuration must be a readable, bounded JSON object") from None

    if not isinstance(payload, dict):
        raise ValueError("configuration must be a JSON object")

    return ResearchRequest.model_validate(_resolve_paths(payload, config_path.parent.resolve()))


def safe_summary(request: ResearchRequest) -> dict[str, object]:
    """Return stable, bounded operational metadata rather than echoing raw JSON."""
    return {
        "backend": request.backend,
        "budget": request.budget.model_dump(mode="json"),
        "cutoff": request.cutoff.isoformat(),
        "dossier_dir": str(request.dossier_dir) if request.dossier_dir else None,
        "evidence_path": str(request.evidence_path) if request.evidence_path else None,
        "internal_language": request.internal_language,
        "model_roles": {role: setting.effort for role, setting in sorted(request.models.items())},
        "output_dir": str(request.output_dir),
        "prior_dossier_path": str(request.prior_dossier_path)
        if request.prior_dossier_path
        else None,
        "report_language": request.report_language,
        "return_months": request.return_months,
        "schema_version": request.schema_version,
        "source_policy": request.source_policy,
        "ticker": request.ticker,
        "timezone": request.timezone,
        "valuation_months": request.valuation_months,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = load_request(args.config)
    except (ValidationError, ValueError):
        # Validation errors can contain arbitrary raw input, including secrets.
        print("research configuration validation failed; check fields, types and cutoff", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps(safe_summary(request), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(
        "live research execution is not yet configured; use --dry-run to validate the request.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through Python's module runner.
    raise SystemExit(main())
