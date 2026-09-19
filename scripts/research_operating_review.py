"""Attach an independently authored review to a fresh, offline-only input bundle."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from scripts.research_case_preflight import _verified_blobs
from tradingagents.research.case_context import FinancialCaseEnvelope, load_case_context
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.operating_scenarios import OperatingScenarioReview
from tradingagents.research.storage import atomic_write, canonical_json, parse_json, read_bytes

_READER_MANDATE = (
    "Produce a substantive English deep-research reader from the frozen evidence, financial case "
    "and independently reviewed conditional operating scenarios. Lead with the central investment "
    "disagreement; connect business drivers, management, accounting, scenarios, counter-case and "
    "observable falsifiers. Separate reported historical facts, management guidance, analyst "
    "sensitivities and code-calculated amounts. Use the explicit fiscal dates and supplied numeric "
    "references; never replace the fiscal anchor with TTM revenue. Conditional operating income "
    "is not cash flow, enterprise value, an equity target, or funding clearance. Keep unresolved "
    "capitalization, realization, tax, commitments and opening-to-cutoff gaps scoped to the "
    "conclusions they block. Prioritize material limitations in readable prose without suppressing "
    "them. Do not invent consensus, current quotes, probabilities or independent sources. "
    "No trading, position sizing, portfolio actions, scheduling, production acceptance or Chinese output."
)


def attach_review(source: Path, review_path: Path, output: Path) -> dict:
    """Authenticate the draft and review, preserving both in a new replay bundle.

    This validates a recorded review, not reviewer credentials or economic merit.
    The caller is responsible for obtaining a genuinely independent review.
    No provider is instantiated, and no live run is authorized by this operation.
    """
    source, output = source.resolve(), output.resolve()
    if output.exists() or output == source or source in output.parents:
        raise ValueError("output must be a new directory outside the source bundle")
    blobs = _verified_blobs(source)
    envelope = FinancialCaseEnvelope.model_validate(parse_json(blobs["case_input.json"]))
    package = envelope.operating_scenarios
    if package is None or package.review is not None:
        raise ValueError("source must contain an unreviewed operating package")
    if parse_json(blobs["operating_scenario_package.json"]) != package.model_dump(mode="json"):
        raise ValueError("source package differs from case envelope")
    if parse_json(blobs["financial_case.json"]) != envelope.case.model_dump(mode="json"):
        raise ValueError("source financial case differs from envelope")
    review_bytes = read_bytes(review_path)
    review = OperatingScenarioReview.model_validate_json(review_bytes)
    updated = FinancialCaseEnvelope.model_validate({
        **envelope.model_dump(mode="json"),
        "operating_scenarios": {**package.model_dump(mode="json"), "review": review.model_dump(mode="json")},
    })
    source_request = ResearchRequest.model_validate(parse_json(blobs["request.json"]))
    if source_request.backend != "replay":
        raise ValueError("source request must be offline replay")
    request = ResearchRequest.model_validate({
        **source_request.model_dump(mode="json"), "backend": "replay",
        "mandate": _READER_MANDATE, "report_language": "English",
        "internal_language": "English",
        "additional_report_languages": (),
        "financial_case_path": output / "case_input.json", "evidence_path": output / "evidence.json",
        "output_dir": output / "run",
    })
    snapshot = EvidenceSnapshot.model_validate(parse_json(blobs["evidence.json"]))
    context = load_case_context(canonical_json(updated), request, snapshot)
    if context.operating_scenarios is None or not context.operating_scenarios.reviewed:
        raise ValueError("independent operating review did not clear the exact package")
    readiness = {
        "status": "reviewed_conditional_operating_input_not_report_acceptance",
        "model_calls": 0, "live_authorized": False, "report_generated": False,
        "case_reviewed": context.reviewed, "operating_scenarios_reviewed": True,
        "valuation_output_scope": context.scope.model_dump(mode="json"),
        "limitations": context.limitations,
    }
    retained = {name: blobs[name] for name in (
        "operating_scenario_diagnostics.json", "closure_ledger.json",
    ) if name in blobs}
    if "provenance.json" in blobs:
        retained["source_provenance.json"] = blobs["provenance.json"]
    outputs = {
        **retained,
        "request.json": canonical_json(request), "evidence.json": blobs["evidence.json"],
        "case_input.json": canonical_json(updated), **context.artifacts,
        "readiness.json": canonical_json(readiness),
        "provenance.json": canonical_json({
            "source_directory": str(source),
            "source_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in blobs.items()},
            "review_file_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "review_meaning": "independent conditional operating review only; no valuation or live approval",
        }),
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "artifact_hashes": {name: hashlib.sha256(raw).hexdigest() for name, raw in outputs.items()},
        "status": readiness["status"],
    }))
    return readiness


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(canonical_json(attach_review(args.source, args.review, args.output)).decode())


if __name__ == "__main__":
    main()
