"""Attach an independently authored review to a frozen cash-flow bridge draft.

This adapter is deliberately offline and replay-only.  It authenticates two
immutable bundles and a separately authored ``CashFlowBridgeReview``; it does
not make an economic decision, alter assumptions, or authorize live research.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from tradingagents.research.case_context import FinancialCaseEnvelope, load_case_context
from tradingagents.research.cashflow_bridge import CashFlowBridgePackage, CashFlowBridgeReview
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.financial_case import FinancialCase, evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)

_OPERATING_REQUIRED = frozenset({
    "request.json", "evidence.json", "case_input.json", "financial_case.json",
    "operating_scenario_package.json", "operating_scenario_context.json",
})
_DRAFT_REQUIRED = frozenset({"cashflow_bridge_package.json", "provenance.json"})
_DRAFT_SOURCE_KEYS = frozenset({
    "evidence.json", "financial_case.json", "operating_scenario_package.json",
    "operating_scenario_context.json",
})
_READER_MANDATE = (
    "Produce an English replay-only reader from frozen evidence and the independently reviewed "
    "conditional cash-flow bridge. Preserve limitations and distinguish reported facts, issuer "
    "guidance, analyst assumptions, and calculated cash flow. Do not make a quote, valuation, "
    "funding, trading, portfolio, or economic-approval conclusion."
)


def _flat_verified_blobs(directory: Path, required: frozenset[str]) -> tuple[dict[str, bytes], bytes]:
    """Read a closed, manifest-bound, non-symlink source directory."""

    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("source bundle must be a real directory")
    entries = list(directory.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise ValueError("source bundle cannot contain symlinks or nested paths")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("source manifest is unavailable")
    manifest_bytes = read_bytes(manifest_path)
    manifest = parse_json(manifest_bytes)
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("source artifact inventory is missing")
    if not required <= set(hashes):
        raise ValueError("source artifact inventory omits required files")
    if {item.name for item in entries} != {"manifest.json", *hashes}:
        raise ValueError("source directory differs from its manifest inventory")
    blobs: dict[str, bytes] = {}
    for name, expected in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("invalid source artifact path")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("invalid source artifact hash")
        raw = read_bytes(directory / name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("source artifact hash mismatch")
        blobs[name] = raw
    return blobs, manifest_bytes


def _unchanged(directory: Path, blobs: dict[str, bytes], manifest_bytes: bytes) -> None:
    if directory.is_symlink() or read_bytes(directory / "manifest.json") != manifest_bytes:
        raise ValueError("source manifest changed before publication")
    for name, captured in blobs.items():
        if read_bytes(directory / name) != captured:
            raise ValueError("source artifact changed before publication")


def attach_cashflow_review(
    operating_source: Path, cashflow_draft: Path, review_path: Path, output: Path
) -> dict:
    """Publish a fresh reviewed replay bundle without changing either input bundle."""

    if any(path.is_symlink() for path in (operating_source, cashflow_draft, review_path, output)):
        raise ValueError("symlink inputs are not accepted")
    if output.exists():
        raise ValueError("output must be a new directory outside both source bundles")
    operating_source, cashflow_draft, output = (
        operating_source.resolve(), cashflow_draft.resolve(), output.resolve()
    )
    if (
        output.exists()
        or output in {operating_source, cashflow_draft}
        or operating_source in output.parents
        or cashflow_draft in output.parents
    ):
        raise ValueError("output must be a new directory outside both source bundles")
    source_blobs, source_manifest_bytes = _flat_verified_blobs(operating_source, _OPERATING_REQUIRED)
    draft_blobs, draft_manifest_bytes = _flat_verified_blobs(cashflow_draft, _DRAFT_REQUIRED)
    source_request = ResearchRequest.model_validate(parse_json(source_blobs["request.json"]))
    if source_request.backend != "replay":
        raise ValueError("source request must be offline replay")
    snapshot = EvidenceSnapshot.model_validate(parse_json(source_blobs["evidence.json"]))
    if validate_snapshot(snapshot, source_request) != snapshot:
        raise ValueError("source evidence requires normalization")
    envelope = FinancialCaseEnvelope.model_validate(parse_json(source_blobs["case_input.json"]))
    if envelope.cashflow_bridge is not None:
        raise ValueError("operating source must not already contain a cash-flow bridge")
    case = FinancialCase.model_validate(parse_json(source_blobs["financial_case.json"]))
    package = OperatingScenarioPackage.model_validate(
        parse_json(source_blobs["operating_scenario_package.json"])
    )
    if envelope.case != case or envelope.operating_scenarios != package:
        raise ValueError("case envelope differs from standalone case or operating package")
    source_context = load_case_context(source_blobs["case_input.json"], source_request, snapshot)
    if source_context.operating_scenarios is None or not source_context.operating_scenarios.reviewed:
        raise ValueError("source operating package is not independently reviewed")
    if canonical_json(source_context.operating_scenarios.model_context) != source_blobs[
        "operating_scenario_context.json"
    ]:
        raise ValueError("stored operating context differs from fresh offline evaluation")
    cashflow = CashFlowBridgePackage.model_validate(parse_json(draft_blobs["cashflow_bridge_package.json"]))
    if cashflow.review is not None:
        raise ValueError("cash-flow draft must have no review")
    expected_case_hash = digest(case.model_dump(mode="json"))
    expected_evidence_hash = evidence_snapshot_sha256(snapshot)
    expected_operating_hash = operating_scenario_package_sha256(package)
    if (cashflow.case_sha256, cashflow.evidence_sha256, cashflow.operating_package_sha256) != (
        expected_case_hash, expected_evidence_hash, expected_operating_hash,
    ):
        raise ValueError("cash-flow package hashes differ from current operating inputs")
    draft_provenance = parse_json(draft_blobs["provenance.json"])
    recorded = draft_provenance.get("source_artifact_sha256")
    actual_source_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in source_blobs.items()}
    if not isinstance(recorded, dict) or set(recorded) != _DRAFT_SOURCE_KEYS:
        raise ValueError("cash-flow draft provenance must bind the exact four operating artifacts")
    if recorded is not None:
        if not set(recorded) <= set(actual_source_hashes):
            raise ValueError("cash-flow draft provenance has unknown source artifacts")
        if recorded != {name: actual_source_hashes[name] for name in recorded}:
            raise ValueError("cash-flow draft provenance differs from operating source bytes")
    review_bytes = read_bytes(review_path)
    review = CashFlowBridgeReview.model_validate_json(review_bytes)
    reviewed_cashflow = CashFlowBridgePackage.model_validate({
        **cashflow.model_dump(mode="json"), "review": review.model_dump(mode="json"),
    })
    updated = FinancialCaseEnvelope.model_validate({
        **envelope.model_dump(mode="json"), "cashflow_bridge": reviewed_cashflow.model_dump(mode="json"),
    })
    request = ResearchRequest.model_validate({
        **source_request.model_dump(mode="json"), "backend": "replay", "mandate": _READER_MANDATE,
        "report_language": "English",
        "internal_language": "English", "additional_report_languages": (),
        "financial_case_path": output / "case_input.json", "evidence_path": output / "evidence.json",
        "output_dir": output / "run", "prior_dossier_path": None, "dossier_dir": None,
    })
    context = load_case_context(canonical_json(updated), request, snapshot)
    if context.cashflow_bridge is None or not context.cashflow_bridge.reviewed:
        raise ValueError("independent review did not clear the exact cash-flow package")
    if context.reviewed:
        raise ValueError("financial case must remain a draft in this cash-flow-only adapter")
    cashflow_scope = context.cashflow_bridge.model_context["output_scope"]
    readiness = {
        "status": "cashflow_reviewed_conditional_financial_case_still_draft",
        "cashflow_reviewed": True,
        "financial_case_still_draft": True,
        "replay_only": True,
        "model_calls": 0,
        "live_authorized": False,
        "report_generated": False,
        "output_scope": cashflow_scope,
        "output_scope_blocked": all(
            cashflow_scope[name]["status"] == "blocked"
            for name in ("operating_asset_value", "equity_per_share_value", "funding_assessment")
        ),
        "limitations": context.limitations,
    }
    regenerated = {"financial_case.json", "operating_scenario_package.json",
                   "operating_scenario_context.json", "cashflow_bridge_package.json",
                   "cashflow_bridge_context.json", "cashflow_bridge_result.json",
                   "cashflow_bridge_calculated_values.json"}
    retained = {
        f"operating_source_{name}": raw
        for name, raw in source_blobs.items()
        if name not in _OPERATING_REQUIRED and name not in regenerated
    }
    retained.update({
        f"cashflow_draft_{name}": raw
        for name, raw in draft_blobs.items()
        if name not in regenerated
    })
    outputs = {
        **retained,
        "request.json": canonical_json(request),
        "evidence.json": source_blobs["evidence.json"],
        "case_input.json": canonical_json(updated),
        **context.artifacts,
        "readiness.json": canonical_json(readiness),
        "provenance.json": canonical_json({
            "source_directory": str(operating_source),
            "cashflow_draft_directory": str(cashflow_draft),
            "source_hashes": actual_source_hashes,
            "cashflow_draft_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in draft_blobs.items()},
            "review_file_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "review_meaning": "independent conditional cash-flow bridge review only; no valuation, funding, quote, or economic approval",
        }),
    }
    _unchanged(operating_source, source_blobs, source_manifest_bytes)
    _unchanged(cashflow_draft, draft_blobs, draft_manifest_bytes)
    if review_path.is_symlink() or read_bytes(review_path) != review_bytes:
        raise ValueError("review changed before publication")
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "schema_version": 1,
        "artifact_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()},
        "status": readiness["status"],
    }))
    return readiness


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operating-source", type=Path, required=True)
    parser.add_argument("--cashflow-draft", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(canonical_json(attach_cashflow_review(
        args.operating_source, args.cashflow_draft, args.review, args.output
    )).decode())


if __name__ == "__main__":
    main()
