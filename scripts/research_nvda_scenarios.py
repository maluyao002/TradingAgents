"""Author explicit conditional NVDA scenarios from frozen company/market evidence.

This is a dated development case, not an autonomous forecast generator. Numeric
paths below are analyst judgments, never management guidance or consensus. A
separate hash-bound review record is required before deterministic compilation.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
from decimal import Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from cli.research import _resolve_paths, load_request
from scripts.research_assumption_package import build_package
from scripts.research_market_inputs import parse_market_inputs
from tradingagents.research.assumptions import AssumptionPackage, AssumptionRange
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.engine import _calculate
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.model_sensitivity import recompute_sensitivity
from tradingagents.research.result_scope import conservative_scope, scope_calculation
from tradingagents.research.scenario_compiler import (
    REVIEWED_PACKET_FILES,
    ConditionalScenario,
    apply_conditional_review,
    compile_scenarios,
)
from tradingagents.research.sources import FileSourceCache
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import canonical_json, digest, parse_json, read_bytes, read_json
from tradingagents.research.valuation import FCFFModelInput, ForecastPeriod, _calendar_anniversary

D = Decimal
SCOPE = (
    "Conditional analyst-authored development scenarios; not accepted forecasts, consensus, "
    "probabilities, investment recommendations or 12-month targets."
)
CASE_LIMITATIONS = (
    SCOPE,
    "The July 26 opening TTM/balance-sheet proxies are used at the later valuation cutoff "
    "without an interim financial roll-forward; annual periods are calendar-anniversary "
    "model conventions, not fiscal-year guidance periods.",
    "Cash-only net debt excludes securities and leases; this is not a complete equity bridge.",
    "Constant current-quarter weighted-average diluted shares are not point-in-time capitalization; "
    "no future dilution or repurchase return is implied.",
    "GAAP after-SBC margins retain the economic SBC cost; no SBC addback or second dilution "
    "charge is applied. Forecast SBC rates are disclosure, not incremental deductions.",
    "Discount rates are an all-equity operating-asset reference using a sector unlevered beta, "
    "not a measured NVIDIA regression beta or current corporate WACC. Debt tax shields are omitted.",
    "ERP, beta and Treasury inputs have different vintages. Holding the older ERP fixed with "
    "the later Treasury yield is an explicit assumption, not a newly estimated implied ERP.",
    "Terminal ROIC and margin/growth convergence are analyst assumptions; ten-year cash-flow "
    "paths and reinvestment schedules are not independently underwritten company plans.",
)

# Each list is an authored annual path, NOT copied historical growth or source forecasts.
PATHS = {
    "downside": {
        "growth": ".25 .10 .05 .03 .02 .02 .02 .02 .02 .02",
        "margin": ".58 .50 .45 .40 .37 .35 .34 .33 .32 .32",
        "tax": ".19 .20 .21 .22 .23 .23 .23 .23 .23 .23",
        "wc": ".20 .21 .22 .22 .22 .22 .22 .22 .22 .22",
        "da": ".014 .016 .018 .02 .02 .02 .02 .02 .02 .02",
        "capex": ".035 .04 .04 .035 .03 .03 .03 .03 .03",
        "sbc": ".025 .028 .03 .03 .03 .03 .03 .03 .03 .03",
        "beta": "1.85", "g": ".02", "terminal_roic": ".15",
        "thesis": "Demand digestion and competing/custom silicon reduce pricing power; "
                  "growth decelerates quickly, margins compress and customer/inventory funding "
                  "needs rise. This is a stress case, not an assigned probability.",
    },
    "base": {
        "growth": ".50 .35 .25 .18 .12 .09 .07 .05 .04 .03",
        "margin": ".64 .62 .59 .56 .53 .50 .47 .45 .43 .42",
        "tax": ".18 .18 .19 .20 .21 .21 .21 .21 .21 .21",
        "wc": ".185 .185 .18 .18 .175 .175 .17 .17 .17 .17",
        "da": ".012 .013 .014 .015 .016 .017 .018 .019 .02 .02",
        "capex": ".027 .028 .03 .03 .03 .03 .03 .03 .03",
        "sbc": ".022 .022 .023 .023 .023 .023 .023 .023 .023 .023",
        "beta": "1.50", "g": ".03", "terminal_roic": ".20",
        "thesis": "AI infrastructure demand remains strong but extraordinary expansion fades; "
                  "competition, customer bargaining power and a larger revenue base compress "
                  "growth and margins. Base is a reference path, not an expected-value estimate.",
    },
    "upside": {
        "growth": ".65 .45 .30 .22 .16 .12 .09 .07 .05 .04",
        "margin": ".66 .66 .64 .62 .60 .58 .56 .54 .52 .50",
        "tax": ".17 .17 .18 .18 .19 .19 .20 .20 .20 .20",
        "wc": ".18 .175 .17 .165 .16 .16 .16 .16 .16 .16",
        "da": ".012 .013 .014 .015 .016 .017 .018 .019 .02 .02",
        "capex": ".025 .026 .027 .028 .029 .03 .03 .03 .03",
        "sbc": ".02 .02 .02 .02 .02 .02 .02 .02 .02 .02",
        "beta": "1.25", "g": ".04", "terminal_roic": ".25",
        "thesis": "Platform/network advantages and wider inference adoption sustain a longer "
                  "growth runway and stronger profitability, while still fading to mature nominal "
                  "growth. This is a conditional optimistic case, not a management forecast.",
    },
}


def authored_cases(request, market: dict) -> tuple[tuple[ConditionalScenario, ...], dict]:
    origin = request.cutoff.astimezone(ZoneInfo(request.timezone)).date()
    rate, erp = D(market["risk_free_rate"]), D(market["erp"])
    cases, economic_audit = [], {}
    for name, path in PATHS.items():
        series = {key: list(map(D, path[key].split()))
                  for key in ("growth", "margin", "tax", "wc", "da", "capex", "sbc")}
        g, roic, beta = D(path["g"]), D(path["terminal_roic"]), D(path["beta"])
        with localcontext() as context:
            context.prec = 40
            rate_case = rate + beta * erp
            # Final year is already at stable growth. Its change in working capital
            # must use the same WC ratio in the preceding year (asserted below).
            assert series["growth"][-1] == g and series["wc"][-2] == series["wc"][-1]
            terminal_capex = (series["da"][-1] + series["margin"][-1] *
                              (1 - series["tax"][-1]) * g / roic -
                              series["wc"][-1] * g / (1 + g))
            series["capex"].append(terminal_capex)
        periods = []
        start = origin
        for i in range(10):
            end = _calendar_anniversary(origin, i + 1)
            periods.append(ForecastPeriod(
                label=f"Y{i+1}", period_start=start, period_end=end, discount_years=D(i + 1),
                revenue_growth=series["growth"][i], operating_margin=series["margin"][i],
                operating_margin_basis="after_sbc", tax_rate=series["tax"][i],
                depreciation_amortization_pct_revenue=series["da"][i],
                capex_pct_revenue=series["capex"][i], working_capital_pct_revenue=series["wc"][i],
                sbc_pct_revenue=series["sbc"][i], external_funding_required=False,
            ))
            start = end
        cases.append(ConditionalScenario(id=name, thesis=path["thesis"], periods=tuple(periods),
                                         discount_rate=rate_case, terminal_growth=g,
                                         limitations=CASE_LIMITATIONS))
        economic_audit[name] = {
            "discount_rate_formula": "Treasury_nominal_reference + analyst_unlevered_beta * held_fixed_ERP",
            "treasury_reference": rate, "erp": erp, "analyst_beta": beta,
            "discount_rate": rate_case, "target_debt_weight": "0",
            "beta_reference_not_nvda_beta": market["unlevered_beta_cash_corrected"],
            "terminal_roic_assumption": roic, "terminal_growth": g,
            "terminal_reinvestment_fraction_of_nopat": g / roic,
            "terminal_capex_pct_revenue": terminal_capex,
            "terminal_capex_formula": "DA_ratio + margin*(1-tax)*g/ROIC - WC_ratio*g/(1+g)",
            "probability": None,
        }
    return tuple(cases), economic_audit


def populate_assumptions(package, cases, snapshot):
    facts = {fact.id: fact for fact in snapshot.facts}
    source_ids = {source.id for source in snapshot.sources}
    monetary = tuple(fact.id for fact in snapshot.facts if fact.metric in {
        "depreciation_amortization", "income_before_income_tax", "income_before_taxes",
        "income_tax_expense", "capex_cashflow", "stock_based_compensation"})
    rationale = {
        "discount_rate": "All-equity operating-asset discount reference = latest retrieved nominal "
                         "Treasury yield + analyst unlevered-beta stress * held-fixed older ERP. "
                         "Sector cash-corrected beta anchors the base, not NVIDIA's observed beta; "
                         "zero target debt omits the tax shield. Mixed input vintages remain explicit.",
        "terminal_growth": "Authored 2/3/4% mature nominal scenarios bracket inflation-only growth "
                           "through approximately the Fed long-run real-plus-inflation reference. "
                           "Not an issuer fact or macro-to-company growth identity; terminal "
                           "reinvestment is normalized using an explicit 15/20/25% ROIC assumption.",
        "revenue_growth": "First-year 25/50/65% growth stresses the strong current run rate and "
                          "management demand commentary without copying fiscal guidance into a "
                          "different calendar window; paths fade over ten years as saturation, "
                          "competition and scale constrain expansion. Not consensus or probabilities.",
        "operating_margin": "Current GAAP margin around 66% anchors near-term 58/64/66% cases. "
                            "Mature 32/42/50% margins reflect progressively different competitive "
                            "outcomes; these are explicit judgments, not source-predicted endpoints.",
        "tax_rate": "Reported effective taxes include investment gains and discrete items, not "
                    "pure operating taxation. Near-term 17-19% converges to 20-23% normalized "
                    "operating tax assumptions, not a claimed statutory/geographic tax model.",
        "depreciation_amortization_pct_revenue": "H1 consolidated cash-flow D&A provides the "
                                               "historical reference; 1.2-2% forward ratios allow "
                                               "capital/intangible assets to season. This is an "
                                               "authored normalization, not a complete asset roll-forward.",
        "capex_pct_revenue": "Historical cash purchase ratios around 2.5-2.8% anchor explicit "
                             "2.5-4% near-term stress. The terminal year is recalculated for "
                             "g/ROIC reinvestment, after working-capital needs and D&A, rather "
                             "than perpetuating a growth path with insufficient capital.",
        "working_capital_pct_revenue": "Opening mixed-row WC/TTM revenue is about 18.5%; cases "
                                       "move to 16/17/22% under different inventory/receivable "
                                       "conditions. The proxy's accounting contamination remains "
                                       "unresolved and is not hidden by these ranges.",
        "sbc_pct_revenue": "Recent GAAP SBC ratios around 2.1-2.2% anchor 2-3% disclosure paths. "
                           "Operating margin already includes SBC, so no addback or duplicate "
                           "deduction is allowed; current diluted-share proxy remains limited.",
    }
    entries = []
    for entry in package.entries:
        path = entry.model_path
        updates = {}
        if path in {"discount_rate", "terminal_growth"}:
            values = [getattr(case, path) for case in cases]
            ids = (("market-fed-h15", "market-damodaran-erp", "market-damodaran-beta")
                   if path == "discount_rate" else ("market-fed-sep",))
            updates = {"status": "draft", "category": "analyst_assumption",
                       "range": AssumptionRange(low=min(values), base=values[1], high=max(values)),
                       "evidence_ids": ids, "rationale": rationale[path]}
        elif path.startswith("periods.*.") and path.rsplit(".", 1)[1] in rationale:
            field = path.rsplit(".", 1)[1]
            values = [getattr(period, field) for case in cases for period in case.periods]
            ids = tuple(dict.fromkeys((*entry.evidence_ids, *monetary, "nvda-q2-release", "nvda-q2-filing")))
            assert set(ids) <= facts.keys() | source_ids
            updates = {"status": "draft", "range": AssumptionRange(low=min(values),
                       base=getattr(cases[1].periods[0], field), high=max(values)),
                       "evidence_ids": ids, "rationale": rationale[field]}
        elif path == "periods.*.external_funding_required":
            updates = {"status": "draft", "value": "false", "rationale":
                       "Conditional cases must calculate positive FCFF in every period before "
                       "this assumption passes review. No off-model commitments are certified."}
        elif path == "forecast_schedule":
            updates = {"value": "ten annual calendar-anniversary periods beginning at as_of_date",
                       "rationale": "Ten explicit dated periods fade to stable growth; calendar "
                                    "windows are conventions, not NVIDIA fiscal guidance windows."}
        entries.append(entry.model_copy(update=updates))
    return AssumptionPackage.model_validate({**package.model_dump(), "entries": entries,
                                            "limitations": (*package.limitations, *CASE_LIMITATIONS)})


def _publish(output: Path, blobs: dict[str, bytes]) -> None:
    output.mkdir(exist_ok=False)
    for name, content in blobs.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    with (output / "manifest.json").open("xb") as stream:
        stream.write(canonical_json({"scope": SCOPE, "artifact_hashes": {
            name: hashlib.sha256(content).hexdigest() for name, content in blobs.items()}}))


def prepare(config: Path, enriched_evidence: Path, market_cache: Path, output: Path):
    output = output.resolve()
    original = EvidenceSnapshot.model_validate(read_json(enriched_evidence))
    sources, market = parse_market_inputs(FileSourceCache(market_cache))
    cutoff = max(original.cutoff, *(source.retrieved_at for source in sources))
    snapshot = EvidenceSnapshot.model_validate({**original.model_dump(), "cutoff": cutoff,
        "sources": (*original.sources, *sources), "gaps": (*original.gaps, *CASE_LIMITATIONS)})
    request = load_request(config).model_copy(update={"cutoff": cutoff, "evidence_path": output / "evidence.json",
        "output_dir": output.parent / "model_probe_1", "quality_revision": "evidence-led-bounded"})
    snapshot = validate_snapshot(snapshot, request)
    raw = canonical_json(snapshot)
    package, calibration = build_package(request, raw)
    cases, economic_audit = authored_cases(request, market)
    package = populate_assumptions(package, cases, snapshot)
    blobs = {"request.json": canonical_json(request), "evidence.json": raw,
             "assumptions.json": canonical_json(package), "scenarios.json": canonical_json(cases),
             "economic_audit.json": canonical_json(economic_audit),
             "market_inputs.json": canonical_json(market), "calibration.json": canonical_json(calibration)}
    _publish(output, blobs)
    return {"status": "draft_requires_independent_review", "output": str(output),
            "assumptions_sha256": hashlib.sha256(blobs["assumptions.json"]).hexdigest(),
            "scenarios_sha256": hashlib.sha256(blobs["scenarios.json"]).hexdigest()}


def compile_reviewed(packet: Path, review_path: Path, output: Path):
    manifest = read_json(packet / "manifest.json")
    if set(manifest.get("artifact_hashes", {})) != REVIEWED_PACKET_FILES:
        raise ValueError("prepared packet inventory incomplete")
    blobs = {}
    for name, expected in manifest["artifact_hashes"].items():
        if Path(name).name != name:
            raise ValueError("prepared packet artifact path is invalid")
        blobs[name] = read_bytes(packet / name)
        if hashlib.sha256(blobs[name]).hexdigest() != expected:
            raise ValueError("prepared packet artifact hash mismatch")
    if len(blobs["request.json"]) > 1024 * 1024:
        raise ValueError("request exceeds its 1 MiB size limit")
    request = ResearchRequest.model_validate(_resolve_paths(parse_json(blobs["request.json"]), packet.resolve()))
    raw = blobs["evidence.json"]
    snapshot = EvidenceSnapshot.model_validate_json(raw)
    package = AssumptionPackage.model_validate(parse_json(blobs["assumptions.json"]))
    cases = TypeAdapter(tuple[ConditionalScenario, ...]).validate_python(parse_json(blobs["scenarios.json"]))
    review = read_json(review_path)
    source_package = package
    package = apply_conditional_review(package, cases, review, manifest)
    compiled = compile_scenarios(package, snapshot, request, raw, cases)
    economic_audit = parse_json(blobs["economic_audit.json"])
    sensitivities = {}
    for case in compiled["cases"]:
        model = TypeAdapter(FCFFModelInput).validate_python(case["typed_input"])
        result = case["result"]
        final = result["forecasts"][-1]
        if any(D(row["fcff"]) <= 0 for row in result["forecasts"]):
            raise ValueError("conditional no-external-funding assumption failed")
        with localcontext() as context:
            context.prec = 40
            reinvestment = D(final["capex"]) - D(final["depreciation_amortization"]) + D(final["change_in_working_capital"])
            actual = reinvestment / D(final["nopat"])
            expected = model.terminal_growth / D(economic_audit[case["id"]]["terminal_roic_assumption"])
            if abs(actual - expected) > D("1e-24"):
                raise ValueError("terminal reinvestment does not reconcile to the reviewed ROIC")
        sensitivities[case["id"]] = asdict(recompute_sensitivity(
            model, discount_rates=(model.discount_rate - D(".01"), model.discount_rate, model.discount_rate + D(".01")),
            terminal_growth_rates=(model.terminal_growth - D(".005"), model.terminal_growth, model.terminal_growth + D(".005")),
            share_count_basis=request.share_count_basis,
        ))
    base = next(case for case in compiled["cases"] if case["id"] == "base")
    from scripts.research_reviewed_inputs import build_reviewed_inputs

    reviewed_inputs = build_reviewed_inputs(snapshot, package, cases,
        parse_json(blobs["market_inputs.json"]), economic_audit, digest(review))
    scoped_results = {}
    for case in compiled["cases"]:
        calculation = _calculate(ValuationProposal.model_validate(case["proposal"]), request, snapshot)
        scoped_results[case["id"]] = scope_calculation(calculation, conservative_scope(calculation))
    authored_context = {"ticker": request.ticker, "cutoff": request.cutoff.isoformat(),
                        "evidence_sha256": package.evidence_sha256, "proposal": base["proposal"],
                        "review_sha256": digest(review), "scope": SCOPE,
                        "review": review, "packet_manifest": manifest,
                        "source_assumptions": source_package.model_dump(mode="json"),
                        "source_scenarios": [case.model_dump(mode="json") for case in cases],
                        "reviewed_inputs": reviewed_inputs.model_dump(mode="json")}
    # Offline readiness audits the same builder/serialization as the bounded probe.
    from scripts.research_model_probe import build_payload
    from tradingagents.research.reviewed_inputs import require_material_coverage

    model_payload, admission = build_payload(request, snapshot, authored_context, reviewed_inputs)
    coverage = require_material_coverage(canonical_json({key: value for key, value in model_payload.items()
        if key not in {"system", "response_schema", "timeout_seconds"}}), reviewed_inputs)
    _publish(output.resolve(), {"compiled.json": canonical_json(compiled),
                               "assumptions.json": canonical_json(package),
                               "review.json": canonical_json(review),
                               "sensitivities.json": canonical_json(sensitivities),
                               "authored_context.json": canonical_json(authored_context),
                               "reviewed_inputs.json": canonical_json(reviewed_inputs),
                               "material_coverage.json": canonical_json({"mode": "offline_preflight", "dispatched": False,
                                   "coverage": coverage, "admission_estimate": admission}),
                               "scoped_results.json": canonical_json(scoped_results),
                               "valuation_memo.md": render_memo(compiled, economic_audit, snapshot,
                                                                package.limitations, scoped_results).encode()})
    return compiled


def render_memo(compiled: dict, economic_audit: dict, snapshot: EvidenceSnapshot,
                review_limitations: tuple[str, ...] = (), scoped_results: dict | None = None) -> str:
    lines = ["# NVDA — conditional valuation development memo", "", SCOPE, "",
             f"Evidence cutoff: {snapshot.cutoff.isoformat()}. Review: automated agent, not human sign-off.", "",
             "## What drives the result", "", "The key disagreement is how quickly exceptional "
             "AI-infrastructure demand and profitability normalize—not whether today's revenue "
             "is large. The three authored paths vary growth, competition-sensitive margins, "
             "working-capital demands and operating-asset discount rates. None has an assigned "
             "probability. The base case is a reference, not an expected outcome.", "",
             "## Conditional results", "", "Present values below use the current diluted-share "
             "proxy. They are not price targets or forecast trading returns. Monetary totals "
             "are converted from exact model units to USD billions; per-share values are USD.", "",
             "| Case | Discount rate | Terminal growth | Enterprise PV ($bn) | Equity PV ($bn) | $/current share | Terminal share of EV |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for case in compiled["cases"]:
        result, model = case["result"], case["typed_input"]
        scale = D(model["units"]["amount_scale"]) / D("1e9")
        lines.append(f"| {case['id']} | {D(model['discount_rate'])*100:.2f}% | {D(model['terminal_growth'])*100:.1f}% | "
                     f"{D(result['enterprise_value'])*scale:.1f} | {D(result['equity_value'])*scale:.1f} | "
                     f"{D(result['value_per_current_diluted_share']):.2f} | "
                     f"{D(result['terminal_value_share_of_enterprise_value'])*100:.1f}% |")
    if scoped_results is not None:
        # Preserve the historical audit renderer for old callers, while all newly
        # compiled packets publish only scoped conclusions in the human memo.
        table_start = lines.index("## Conditional results")
        lines[table_start:] = ["## Conditional operating-asset results", "",
            "Equity/per-share conclusions are withheld: the equity bridge and opening date remain unresolved. "
            "Company-wide funding is not assessed; positive FCFF is not a funding conclusion. "
            "Raw compiled and sensitivity artifacts are mechanical audit data, not eligible targets.", "",
            "| Case | Conditional enterprise PV ($bn) | Equity/per-share |",
            "| --- | ---: | --- |"]
        for case in compiled["cases"]:
            result = scoped_results[case["id"]].get("result", {})
            value = result.get("enterprise_value")
            scale = D(case["typed_input"]["units"]["amount_scale"]) / D("1e9")
            shown = "withheld" if value is None else f"{D(value)*scale:.1f}"
            lines.append(f"| {case['id']} | {shown} | withheld |")
    lines.extend(["", "## Scenario logic and financial bridges", ""])
    for case in compiled["cases"]:
        lines.extend([f"### {case['id'].title()}", "", case["thesis"], "",
                      f"Terminal ROIC assumption: {D(economic_audit[case['id']]['terminal_roic_assumption'])*100:.0f}%. "
                      "Terminal capex is reconciled to g/ROIC reinvestment after D&A and working capital.", "",
                      "| Year | Revenue ($bn) | GAAP margin | NOPAT ($bn) | D&A ($bn) | Capex ($bn) | ΔWC ($bn) | FCFF ($bn) |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
        scale = D(case["typed_input"]["units"]["amount_scale"]) / D("1e9")
        for inputs, row in zip(case["typed_input"]["periods"], case["result"]["forecasts"], strict=True):
            values = [D(row[key]) * scale for key in ("revenue", "nopat", "depreciation_amortization", "capex", "change_in_working_capital", "fcff")]
            lines.append(f"| {row['label']} | {values[0]:.1f} | {D(inputs['operating_margin'])*100:.1f}% | "
                         + " | ".join(f"{value:.1f}" for value in values[1:]) + " |")
    lines.extend(["", "## What would invalidate these cases", "",
                  "- A material decline in customer economics or accelerated custom-silicon adoption could undercut even the downside growth/margin path.",
                  "- Persistent inventory/receivable accumulation would weaken cash conversion despite reported profits.",
                  "- Higher required operating returns, lower mature ROIC, or less durable competitive advantage would reduce terminal value.",
                  "- Legal restrictions on cash, changes in securities values, leases and dilution could materially change the equity bridge.", "",
                  "## Sources and limitations", ""])
    lines.extend(f"- {item}" for item in dict.fromkeys((*CASE_LIMITATIONS, *review_limitations)))
    lines.extend(["", "Rate, ERP, beta and macro references are independently dated inputs, "
                  "not independent corroboration of NVIDIA demand. The numeric scenario paths "
                  "are authored judgments grounded in, but not stated by, these sources.", ""])
    lines.extend(f"- [{source.title}]({source.url})" for source in snapshot.sources)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    prep = sub.add_parser("prepare")
    for flag in ("config", "evidence", "market-cache", "output"):
        prep.add_argument("--" + flag, required=True, type=Path)
    compile_parser = sub.add_parser("compile")
    for flag in ("packet", "review", "output"):
        compile_parser.add_argument("--" + flag, required=True, type=Path)
    args = parser.parse_args(argv)
    if args.mode == "prepare":
        result = prepare(args.config, args.evidence, args.market_cache, args.output)
        print(canonical_json(result).decode())
    else:
        compile_reviewed(args.packet, args.review, args.output)
        print("Conditional scenarios compiled; not accepted research or investment targets.")


if __name__ == "__main__":
    main()
