"""Standalone configuration boundary for the opt-in research workflow.

Dry run supports side-effect-free validation. Execution is offline replay unless
the caller explicitly selects Codex and passes the separate live opt-in flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.evidence import load_snapshot
from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import read_json

_PATH_FIELDS = ("output_dir", "evidence_path", "prior_dossier_path", "dossier_dir", "financial_case_path")
_REPLAY_MINIMUM_RESPONSES = {
    "planner": 1,
    "challenger": 2,
    "business": 1,
    "accounting": 1,
    "expectations": 1,
    "management": 1,
    "valuation": 1,
    "verifier": 2,
    "editor": 1,
}


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
    parser.add_argument(
        "--responses",
        type=Path,
        help="Frozen replay-response JSON; relative paths are resolved from the caller's cwd.",
    )
    parser.add_argument("--allow-live", action="store_true",
                        help="Explicitly authorize live research calls for this invocation.")
    parser.add_argument("--codex-home", type=Path,
                        help="Existing isolated research runtime home; never the shared ~/.codex.")
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
        "financial_case_path": str(request.financial_case_path) if request.financial_case_path else None,
        "internal_language": request.internal_language,
        "model_roles": {role: setting.effort for role, setting in sorted(request.models.items())},
        "model_selections": {
            role: {"model": setting.model, "effort": setting.effort}
            for role, setting in sorted(request.models.items())
        },
        "output_dir": str(request.output_dir),
        "prior_dossier_path": str(request.prior_dossier_path)
        if request.prior_dossier_path
        else None,
        "report_language": request.report_language,
        "quality_revision": request.quality_revision,
        "coverage_batch_policy": request.coverage_batch_policy,
        "valuation_method": request.valuation_method,
        "share_count_basis": request.share_count_basis,
        "additional_report_languages": list(request.additional_report_languages),
        "return_months": request.return_months,
        "schema_version": request.schema_version,
        "source_policy": request.source_policy,
        "ticker": request.ticker,
        "timezone": request.timezone,
        "valuation_months": request.valuation_months,
    }


def load_responses(path: Path, *, case_backed: bool = False) -> dict[str, list[dict[str, object]]]:
    """Load bounded, strict frozen model replies without exposing malformed input."""
    try:
        payload = read_json(path.resolve(), max_bytes=32 * 1024 * 1024)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("responses must be a readable bounded JSON object") from None
    if not isinstance(payload, dict):
        raise ValueError("responses must be a JSON object")

    normalized: dict[str, list[dict[str, object]]] = {}
    for role, replies in payload.items():
        if not isinstance(role, str) or not isinstance(replies, list):
            raise ValueError("responses must map role names to reply lists")
        validated = []
        for reply in replies:
            try:
                validated.append(ModelReply.model_validate(reply).model_dump(mode="json"))
            except ValidationError:
                raise ValueError("responses contain an invalid model reply") from None
        normalized[role] = validated
    if any(
        len(normalized.get(role, [])) < count for role, count in _REPLAY_MINIMUM_RESPONSES.items()
        if not (case_backed and role == "valuation")
    ):
        raise ValueError("responses do not cover the required initial replay stages")
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = load_request(args.config)
    except (ValidationError, ValueError):
        # Validation errors can contain arbitrary raw input, including secrets.
        print(
            "research configuration validation failed; check fields, types and cutoff",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(json.dumps(safe_summary(request), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if request.backend == "codex":
        if not args.allow_live or args.codex_home is None:
            print("live Codex execution requires --allow-live and --codex-home", file=sys.stderr)
            return 2
        if args.responses is not None:
            print("saved responses cannot be combined with live execution", file=sys.stderr)
            return 2
        sec_user_agent = os.environ.get("SEC_USER_AGENT")
        if request.evidence_path is None and (not sec_user_agent or request.instrument is None):
            print("live acquisition requires instrument identity and SEC_USER_AGENT; or provide frozen evidence",
                  file=sys.stderr)
            return 2
        if not args.codex_home.is_dir():
            print("use an existing isolated Codex runtime home; authentication setup is separate", file=sys.stderr)
            return 2
        from tradingagents.research.live import CodexResearchWorker
        from tradingagents.research.supervisor import run_supervised

        try:
            outcome = run_supervised(CodexResearchWorker(args.codex_home, sec_user_agent), request)
        except (OSError, ValueError):
            print("live research could not start; check configuration and checkpoint integrity", file=sys.stderr)
            return 2
        if outcome.result is None:
            print(json.dumps({"status": outcome.status, "code": outcome.code}), file=sys.stderr)
            return 1
        result = outcome.result
        print(json.dumps({"assessment": result.assessment.status, "artifacts": result.artifacts,
                          "stop_reason": result.stop_reason, "ticker": result.ticker}, ensure_ascii=False))
        return 0 if result.stop_reason == "completed_needs_review" else 1

    if request.backend == "replay":
        if args.responses is None:
            print("replay execution requires --responses frozen JSON", file=sys.stderr)
            return 2
        if request.evidence_path is None:  # Defensive: the request contract also enforces this.
            print("replay execution requires evidence_path", file=sys.stderr)
            return 2
        try:
            responses = load_responses(args.responses, case_backed=request.financial_case_path is not None)
            snapshot = load_snapshot(request.evidence_path, request)
            services = ResearchServices(
                evidence=SnapshotEvidenceService(snapshot),
                models=ReplayModelService(responses),
            )
            result = run_research(request, services)
        except (OSError, ValidationError, ValueError):
            print("offline replay failed; check frozen evidence and responses", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "assessment": result.assessment.status,
                    "artifacts": result.artifacts,
                    "stop_reason": result.stop_reason,
                    "ticker": result.ticker,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if result.stop_reason == "completed_needs_review" else 1

    print(
        "live research execution is not yet configured; use --dry-run to validate the request.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through Python's module runner.
    raise SystemExit(main())
