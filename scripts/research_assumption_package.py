"""Offline NVDA forecast preparation; no inference, acquisition or target generation.

Historical calibration is descriptive, not a forecast. All generated entries are
draft or missing; a separately reviewed economic case is still required.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from cli.research import load_request
from tradingagents.research.assumptions import (
    AssumptionEntry,
    AssumptionPackage,
    AssumptionRange,
    assumption_blockers,
    validate_assumption_package,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, ResearchRequest
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.storage import canonical_json, parse_json, read_bytes

ANCHORS = {
    "current_revenue": "nvda-revenue-ttm-q2-fy27",
    "current_working_capital": "nvda-operating-working-capital-q2-fy27-end",
    "net_debt": "nvda-net-debt-q2-fy27-end",
    "current_diluted_shares": "nvda-diluted-shares-q2-fy27",
}
LIMITATIONS = (
    "Preparation artifact only: not a forecast, accepted valuation, or investment target.",
    "Historical calibration does not establish future assumptions; Q2 is contained in H1, "
    "so those observations are overlapping, not independent samples.",
    "The snapshot is issuer-centered and not a complete multi-year normalized operating model.",
    "Working capital mixes other operating/non-operating rows; cash-only net debt excludes "
    "securities and leases and does not establish legal availability of cash.",
    "Diluted shares are the explicit latest-quarter weighted-average proxy, not point-in-time "
    "capitalization; dilution/buybacks need a separate schedule.",
    "Creation time is a new retrospective preparation vintage, not proof this forecast "
    "package existed at the historical evidence cutoff.",
)
FORECAST_AGENDA = {
    "revenue_growth": (
        "Separate near-term management guidance from the longer-run demand/capacity thesis. "
        "Specify annual/stub periods, market share, competition, China exclusions and a fade path. "
        "A six-month year-over-year observation is not an annual forecast growth assumption."
    ),
    "operating_margin": (
        "Build revenue-to-GAAP-operating-profit schedules with product mix and operating expenses. "
        "Do not substitute guided gross margin for operating margin. Explain the long-run fade."
    ),
    "tax_rate": (
        "Reconcile pretax income, effective/cash taxes and discrete items before normalization. "
        "Management FY2027 guidance does not by itself justify later-year tax assumptions."
    ),
    "depreciation_amortization_pct_revenue": (
        "Normalize consolidated cash-flow D&A from the cited filing table before forecasting. "
        "Source text is available but no normalized D&A fact is in the frozen fact inventory. "
        "Reconcile capex, acquired intangibles and useful lives; avoid segment-only D&A."
    ),
    "capex_pct_revenue": (
        "Reported purchase cash outflows are negated explicitly for historical ratios, not "
        "silently treated as normalized reinvestment. Reconcile intangibles, commitments and "
        "growth capacity before selecting future ratios."
    ),
    "working_capital_pct_revenue": (
        "Disaggregate mixed other-current rows, then reconcile receivable, inventory and payable "
        "drivers against a stated annual revenue denominator. Do not divide an instant balance "
        "by a quarter's revenue and label it an annual normalized ratio."
    ),
    "sbc_pct_revenue": (
        "Keep GAAP operating margins after SBC. Do not both subtract SBC again and add dilution "
        "for the same economic cost. Explain employee grants, repurchases and normalization."
    ),
}


def _compatible_ratio(numerator: FinancialFact, denominator: FinancialFact) -> None:
    if (
        numerator.period_type != "duration" or denominator.period_type != "duration"
        or numerator.period_start != denominator.period_start
        or numerator.period_end != denominator.period_end
        or numerator.period_start is None
        or numerator.basis != denominator.basis or numerator.basis != "US GAAP"
        or numerator.unit != denominator.unit or numerator.unit != "USD"
        or numerator.currency != denominator.currency or numerator.currency != "USD"
        or numerator.segment is not None or denominator.segment is not None
        or denominator.metric != "revenue" or denominator.normalized_value <= 0
    ):
        raise ValueError("historical ratio requires matched consolidated periods, basis and units")


def historical_calibration(snapshot: EvidenceSnapshot) -> dict:
    """Recompute matched historical ratios with exact source/fact bindings."""
    facts = {fact.id: fact for fact in snapshot.facts}
    observations, gaps = [], []
    for suffix in ("q2", "h1"):
        for metric, field, negate in (
            ("operating_income", "operating_margin", False),
            ("capex_cashflow", "capex_pct_revenue", True),
            ("stock_based_compensation", "sbc_pct_revenue", False),
        ):
            ids = (f"nvda-{metric}-{suffix}", f"nvda-revenue-{suffix}")
            if any(identifier not in facts for identifier in ids):
                gaps.append(f"Historical {field}/{suffix}: missing exact fact IDs {ids}.")
                continue
            numerator, denominator = (facts[identifier] for identifier in ids)
            if numerator.metric != metric:
                raise ValueError("historical numerator metric changed")
            _compatible_ratio(numerator, denominator)
            if negate and numerator.normalized_value > 0:
                raise ValueError("capex cash outflow must retain the expected nonpositive sign")
            with localcontext() as context:
                context.prec = 40
                ratio = numerator.normalized_value / denominator.normalized_value
                if negate:
                    ratio = -ratio
            observations.append({
                "model_path": f"periods.*.{field}", "window": suffix,
                "classification": "historical_calculation_not_forecast",
                "value": str(ratio), "unit": "fraction", "fact_ids": ids,
                "formula": f"{'-' if negate else ''}{ids[0]} / {ids[1]}",
                "period_start": numerator.period_start, "period_end": numerator.period_end,
                "basis": numerator.basis,
            })
    return {"schema_version": 1, "observations": observations, "gaps": gaps,
            "limitations": [LIMITATIONS[1], "No empirical envelope is promoted to a forecast range."]}


def source_passages(snapshot: EvidenceSnapshot) -> list[dict]:
    """Locate existing context; do not reinterpret it as normalized financial facts."""
    sources = {source.id: source for source in snapshot.sources if source.published_at is not None}
    passages = []
    for source_id, pattern, purpose in (
        ("nvda-q2-filing", r"Depreciation and amortization\s+", "consolidated D&A table review"),
        ("nvda-q2-release", r"NVIDIA.s outlook for the third quarter", "near-term guidance"),
        ("nvda-q2-call", r"expect to grow revenue", "management growth assertion, not consensus"),
    ):
        source = sources.get(source_id)
        match = re.search(pattern, source.content, re.IGNORECASE) if source else None
        if source is None or match is None:
            passages.append({"source_id": source_id, "purpose": purpose, "status": "not_located"})
            continue
        start, end = max(0, match.start() - 900), min(len(source.content), match.end() + 1500)
        passages.append({"source_id": source_id, "purpose": purpose, "status": "located",
                         "content_sha256": source.content_sha256, "url": source.url,
                         "start": start, "end": end, "text": source.content[start:end],
                         "classification": "untrusted_source_excerpt_not_normalized_fact"})
    return passages


def build_package(request: ResearchRequest, evidence_bytes: bytes, *, created_at: datetime | None = None):
    if (request.ticker != "NVDA" or request.valuation_method != "fcff"
            or request.share_count_basis != "latest_quarter_diluted_proxy"):
        raise ValueError("this preparation adapter supports explicit NVDA FCFF share-proxy requests only")
    snapshot = validate_snapshot(EvidenceSnapshot.model_validate(parse_json(evidence_bytes)), request)
    facts = {fact.id: fact for fact in snapshot.facts}
    entries = []
    for path, fact_id in ANCHORS.items():
        if fact_id not in facts:
            raise ValueError(f"required opening anchor unavailable: {fact_id}")
        fact = facts[fact_id]
        entries.append(AssumptionEntry(
            id=f"nvda-anchor-{path}", model_path=path, category="historical_anchor", status="draft",
            unit=fact.unit, fact_id=fact_id, evidence_ids=(fact.id, *fact.inputs),
            range=AssumptionRange(low=fact.normalized_value, base=fact.normalized_value,
                                  high=fact.normalized_value),
            rationale=f"{'Source-derived' if fact.inputs else 'Reported'} historical opening anchor; "
                      "range stores exact unscaled base units, not the model's million-unit inputs. "
                      f"Period end {fact.period_end}; location: {fact.location}. "
                      f"Formula: {fact.formula or 'none (reported)'}. Independent review still required.",
            limitations=LIMITATIONS[3:5],
        ))
    conventions = {
        "as_of_date": (request.cutoff.astimezone(ZoneInfo(request.timezone)).date().isoformat(),
                       "date", "Valuation date follows the explicit request's local cutoff date."),
        "units.currency": ("USD", "currency_code", "Same currency as opening statements."),
        "units.amount_scale": ("1", "multiplier", "Canonical package amounts are unscaled USD; "
                               "a later million-unit model must explicitly convert them."),
        "units.share_scale": ("1", "multiplier", "Canonical package shares are unscaled shares; "
                              "a later million-unit model must explicitly convert them."),
        "periods.*.operating_margin_basis": ("after_sbc", "enum", "GAAP operating margin includes SBC."),
        "forecast_schedule": ("five annual calendar-anniversary periods beginning at as_of_date",
                              "schedule", "Proposed convention only; fiscal/stub reconciliation "
                              "and dated contiguous model periods remain required."),
    }
    for path, (value, unit, rationale) in conventions.items():
        entries.append(AssumptionEntry(id="nvda-convention-" + path.replace("*", "all"),
                                       model_path=path, category="model_convention", status="draft",
                                       unit=unit, value=value, rationale=rationale))
    entries.append(AssumptionEntry(
        id="nvda-funding", model_path="periods.*.external_funding_required",
        category="model_convention", status="missing", unit="boolean",
        rationale="Determine funding requirements from the reconciled cash-flow schedule; "
                  "do not default to false merely because the calculator has that default.",
    ))
    for path, rationale in (
        ("discount_rate", "Acquire date-consistent USD risk-free rate and explicit ERP, beta, "
         "debt-cost and capital-structure methodology. Separate observed rates from estimates; "
         "compute/reconcile WACC and document its vintage, horizon and sensitivity."),
        ("terminal_growth", "Support long-run nominal USD growth with a dated macro/industry basis "
         "and a company maturity/reinvestment thesis. It is an analyst judgment, not an issuer "
         "reported fact. A range must remain below the entire discount-rate range."),
    ):
        entries.append(AssumptionEntry(id="nvda-" + path, model_path=path,
                                       category="external_input" if path == "discount_rate"
                                       else "analyst_assumption", status="missing",
                                       unit="fraction", rationale=rationale))
    calibration = historical_calibration(snapshot)
    eligible_sources = tuple(source.id for source in snapshot.sources if source.published_at is not None)
    for field, rationale in FORECAST_AGENDA.items():
        references = tuple(dict.fromkeys(
            identifier for row in calibration["observations"]
            if row["model_path"] == f"periods.*.{field}" for identifier in row["fact_ids"]
        ))
        entries.append(AssumptionEntry(
            id="nvda-forecast-" + field, model_path=f"periods.*.{field}",
            category="analyst_assumption", status="missing", unit="fraction",
            evidence_ids=references or eligible_sources, rationale=rationale,
            limitations=("Evidence IDs identify context, not proof of an unchosen forecast value.",),
        ))
    package = AssumptionPackage(
        ticker=request.ticker, cutoff=request.cutoff, created_at=created_at or datetime.now(timezone.utc),
        evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(), valuation_method="fcff",
        entries=tuple(entries), limitations=LIMITATIONS,
    )
    package = validate_assumption_package(package, snapshot, request, evidence_bytes)
    calibration["source_passages"] = source_passages(snapshot)
    calibration["evidence_sha256"] = package.evidence_sha256
    return package, calibration


def render_workbook(package: AssumptionPackage, calibration: dict) -> str:
    lines = ["# NVDA forecast-assumption preparation", "", "Not a forecast or final research report.",
             "", f"Evidence cutoff: {package.cutoff.isoformat()}",
             f"Preparation vintage: {package.created_at.isoformat()}", "",
             "## What is ready", "", "Four historical opening anchors are bound to exact frozen facts. "
             "The ratios below are historical calibration only; Q2 and H1 overlap.", "",
             "| Historical input | Window | Ratio | Fact IDs |", "| --- | --- | ---: | --- |"]
    for row in calibration["observations"]:
        lines.append(f"| {row['model_path']} | {row['window']} | "
                     f"{Decimal(row['value']) * 100:.3f}% | {', '.join(row['fact_ids'])} |")
    lines.extend(["", "## Review and acquisition agenda", ""])
    for entry in package.entries:
        lines.extend([f"### {entry.model_path} — {entry.status}", "", entry.rationale, ""])
    lines.extend(["## Source delivery", "", "The calibration JSON includes exact-offset, hash-bound "
                  "filing D&A and guidance passages. These are not newly normalized facts. "
                  "Review the table headers and accounting scope before extracting values.", "",
                  "## Readiness", "", "Structural validation does not establish economic validity. "
                  "No model call or production-engine adoption is enabled by this package.", ""])
    lines.extend(f"- {blocker}" for blocker in assumption_blockers(package))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in package.limitations)
    return "\n".join(lines) + "\n"


def write_bundle(request: ResearchRequest, output: Path) -> dict:
    if request.evidence_path is None:
        raise ValueError("explicit frozen evidence is required")
    evidence_path = request.evidence_path
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("evidence must be a regular non-symlink file")
    evidence_path = evidence_path.resolve()
    output = output.expanduser().absolute()
    if os.path.lexists(output) or not output.parent.is_dir():
        raise ValueError("output must be new and its parent must exist")
    output = output.resolve()
    if output.is_relative_to(evidence_path.parent):
        raise ValueError("output must be outside frozen evidence inputs")
    before = read_bytes(evidence_path)
    package, calibration = build_package(request, before)
    blobs = {"evidence.json": before,
             "assumptions.json": canonical_json(package),
             "calibration.json": canonical_json(calibration),
             "workbook.md": render_workbook(package, calibration).encode("utf-8")}
    if read_bytes(evidence_path) != before:
        raise ValueError("frozen evidence changed during preparation")
    output.mkdir(exist_ok=False)
    hashes = {}
    for name, blob in blobs.items():
        with (output / name).open("xb") as stream:
            stream.write(blob)
        hashes[name] = hashlib.sha256(blob).hexdigest()
    if read_bytes(evidence_path) != before:
        raise ValueError("frozen evidence changed during publication; incomplete bundle retained")
    manifest = {"schema_version": 1, "status": "preparation_requires_review",
                "evidence_binding": "captured_bytes_not_continued_source_path_immutability",
                "evidence_sha256": package.evidence_sha256, "artifact_hashes": hashes,
                "ready_for_model_review": False, "blockers": assumption_blockers(package),
                "network_calls": 0, "model_calls": 0}
    with (output / "manifest.json").open("xb") as stream:
        stream.write(canonical_json(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--output", type=Path)
    choice.add_argument("--validate", type=Path, help="Read-only validation of an edited package")
    args = parser.parse_args(argv)
    try:
        request = load_request(args.config)
        if args.validate:
            if request.evidence_path is None:
                raise ValueError("explicit frozen evidence is required")
            raw = read_bytes(request.evidence_path)
            snapshot = EvidenceSnapshot.model_validate(parse_json(raw))
            package = AssumptionPackage.model_validate(parse_json(read_bytes(args.validate)))
            validate_assumption_package(package, snapshot, request, raw)
            blockers = assumption_blockers(package)
            result = {"structurally_valid": True, "ready_for_model_review": not blockers,
                      "blockers": blockers, "economic_validity": "not_established_by_validation"}
        else:
            result = write_bundle(request, args.output)
    except (OSError, ValueError) as error:
        print(canonical_json({"status": "invalid", "error_type": type(error).__name__}).decode())
        return 2
    print(canonical_json(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
