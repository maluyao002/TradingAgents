"""Reproduce the September 22 analyst triage; never retire historical issues.

The explicit index selections below were authored against one exact inventory.
Changed inputs require new triage, not silent keyword-based reassignment.
"""

import argparse
from pathlib import Path

from tradingagents.research.research_questions import question_register, render_question_register
from tradingagents.research.result_scope import ModelResultScope
from tradingagents.research.review_lifecycle import LifecycleVerification, reconcile_review
from tradingagents.research.storage import atomic_write, canonical_json, digest, read_json

INVENTORY_SHA256 = "c9cb96a778c6c53292c78b9220459212a3483ea3bb24e8191c90f3f6ceabbdc4"


def nvda_questions(issues):
    if digest(issues) != INVENTORY_SHA256:
        raise ValueError("NVDA triage requires its exact reviewed inventory; author a new mapping")

    def q(identifier, priority, question, kinds, conclusions, work, done, search, fallback, indices):
        return {"id": identifier, "priority": priority, "question": question, "kinds": kinds,
                "owner": "research engineering + financial analyst" if "unfinished_analysis" in kinds
                else "primary-source research + independent reviewer",
                "affected_conclusions": conclusions, "required_work": work, "completion_criteria": done,
                "bounded_search": search, "fallback": fallback,
                "issue_ids": [issues[n]["issue_id"] for n in sorted(set(indices))]}

    return [
        q("source-integrity", 1, "Which evidence is authentic, complete and actually delivered?",
          ["missing_evidence", "review_workflow"], ["all factual conclusions", "historical trend confidence"],
          ["Authenticate material frozen anchors against dated primary publications; retain exact passages.",
           "Deliver required rows and qualifications, not only document hashes; reconcile unsupported old claims.",
           "Build comparable eight-quarter/five-year history, preserving accounting and period changes."],
          ["Each adopted material claim has a delivered, eligible passage with date, period and basis.",
           "Stale missing-input assertions receive evidence-backed lifecycle review; history remains immutable."],
          "Issuer filings/releases, customer filings and market originals; log each unavailable passage and acquisition date.",
          "Omit unsupported numerical claims; distinguish unverified authenticity from incomplete coverage.",
          [0,37,41,42,43,45,*range(79,89),128,*range(145,164),179,180,181,182,184]),
        q("cash-conversion", 1, "How much after-SBC operating profit becomes conditional operating cash flow?",
          ["unfinished_analysis", "missing_evidence", "potentially_undisclosed"],
          ["cash conversion", "reinvestment", "conditional operating value"],
          ["Bind dated operating income to explicit cash-tax, D&A, capex and working-capital assumptions.",
           "Separate mixed tax/lease/nonoperating rows; reconcile reported cash flow without annualizing H1 silently.",
           "Inspect receivable aging, subsequent collections and inventory realization; bound missing detail."],
          ["Reproducible period-correct cash-flow bridge and sensitivities pass independent arithmetic review.",
           "Every assumption is labeled; no missing row is treated as zero or economic approval."],
          "Current and prior filings: cash flow, working-capital, tax, credit-loss and inventory notes.",
          "Publish conditional reinvestment ranges, not a claim of normalized cash generation or funding sufficiency.",
          [1,3,9,10,16,36,40,44,46,48,49,51,54,59,65,70,74,90,96,97,101,108,114,118,122,124,129,144,166,167,168,171,174,175,176]),
        q("commitment-overlap", 1, "Which commitments create incremental cash requirements?",
          ["unfinished_analysis", "missing_evidence", "potentially_undisclosed"],
          ["incremental cash uses", "funding risk", "operating/equity bridge"],
          ["Map each dated obligation to cost of sales, capex, working capital or a separate contingent scenario.",
           "Separate totals from components, cancellation rights, timing, gross guarantees and expected losses."],
          ["No double counting between the bridge and commitments; unresolved overlap stays explicit.",
           "Contingent maximum exposure is never deducted as certain debt or expected loss."],
          "Commitment, lease, guarantee and subsequent-event notes plus material contract exhibits.",
          "Show separately bounded exposure and timing sensitivities; neither funding shortfall nor adequacy is inferred.",
          [7,18,19,20,36,60,66,71,75,91,103,106,111,112,113,123,136,137,141,170]),
        q("equity-funding", 2, "What converts operating value into dated equity value and a separate funding assessment?",
          ["unfinished_analysis", "missing_evidence"], ["equity/per-share value", "funding adequacy", "cutoff alignment"],
          ["Reconcile legally available cash, securities realization/tax, debt and consistent lease treatment.",
           "Roll forward shares, options/RSUs, repurchases and opening balances to the valuation date.",
           "Keep equity valuation and company-wide funding as distinct output scopes."],
          ["Source-bound reconciliations and explicit assumptions support each separate scope.",
           "Weighted-average shares and unobserved roll-forwards are never passed off as current capitalization."],
          "Latest filing, securities/debt/lease notes, equity awards and subsequent capital transactions through cutoff.",
          "Withhold per-share targets and funding clearance while retaining supported operating analysis.",
          [4,5,6,*range(11,18),20,21,22,28,31,47,52,53,61,63,67,68,77,78,92,94,100,104,116,120,121,133,138,139,143,169]),
        q("independent-demand", 1, "How much demand is independently funded and economically durable?",
          ["missing_evidence", "potentially_undisclosed", "future_uncertainty"],
          ["growth durability", "customer-credit exposure", "financing dependence"],
          ["Build counterparty map of supplier support, procurement, financing, collections and end-customer cash generation.",
           "Use customer/regulator disclosures independent of NVIDIA; distinguish company assertions from corroborated outcomes."],
          ["At least one decision-material customer linkage is documented with source-specific limitations.",
           "Published aggregate capex is not represented as NVIDIA orders, utilization or customer returns."],
          "Named major customer filings, financing agreements and deployment disclosures; retain search failures.",
          "Use observable concentration/funding proxies and demand stresses; do not quantify an undisclosed supported-revenue share.",
          [58,64,69,73,89,98,99,102,109,110,115,119,125,126,127,130,131,134,135,142,180]),
        q("management", 2, "Do incentives and the guidance record support management credibility?",
          ["missing_evidence", "unfinished_analysis"], ["incentive alignment", "forecast credibility", "capital allocation"],
          ["Read latest available proxy compensation, ownership and oversight disclosures.",
           "Pair original guidance with subsequent actuals on identical periods/bases; preserve revisions.",
           "Separate allocation intent from observable return/impairment and per-share outcomes."],
          ["A sourced incentive assessment and comparable guidance table include alternative interpretations.",
           "A small sample is not called a statistically established forecasting record."],
          "Latest two proxies and at least three consecutive comparable guidance/actual release pairs.",
          "State what alignment is observable and what deal-level return attribution remains undisclosed.",
          [62,72,76,77,93,95,117,140,*range(151,156)]),
        q("competition", 2, "What independent evidence could invalidate the competitive-margin thesis?",
          ["missing_evidence", "future_uncertainty"], ["pricing power", "share durability", "margin convergence"],
          ["Collect third-party procurement, comparable workload benchmarks and deployment economics.",
           "Separate one deployed alternative from broad displacement, like-for-like performance and total cost."],
          ["One concrete alternative deployment is documented and its scope limited.",
           "Thesis includes a falsifier linked to procurement/pricing or workload economics, not vague competition risk."],
          "Customer procurement disclosures, public operator benchmarks and regulator/government deployment records.",
          "Use conditional margin/share sensitivities; do not claim verified relative TCO without comparable measurements.",
          [105,132,134,135,180]),
        q("forecast-judgment", 2, "Which assumptions drive the thesis and what would overturn it?",
          ["future_uncertainty", "unfinished_analysis", "missing_evidence"],
          ["scenario interpretation", "operating value", "price-implied expectations"],
          ["Connect demand, mix, margins and reinvestment; stress guidance ranges rather than only Q4 levels.",
           "Respect fiscal dates and unequal quarter lengths; fiscal cash flows need dated valuation, not forced calendar conversion.",
           "Bind dated discount/terminal inputs before valuation; obtain dated quote/consensus only for dependent comparisons."],
          ["Reader states judgment, confidence, strongest alternative and observable falsifiers.",
           "Supplied calculations are labeled conditional; no scenario probabilities or targets are invented."],
          "Delivered financial packet and eligible dated market originals; no generic-news expansion.",
          "Show causal qualitative conclusions and approved sensitivities, not a false most-likely or exhaustive range.",
          [8,*range(24,28),29,30,*range(32,36),38,39,40,44,50,54,55,56,57,107,116,165,166,172,173,177,178,183]),
        q("integrated-acceptance", 3, "Does the enriched exact reader meet the product gate, and does the workflow generalize to HOOD?",
          ["review_workflow", "unfinished_analysis"], ["report completeness", "financial acceptance", "cross-company generality"],
          ["Integrate new evidence and reviewed calculations before rerunning affected analyses and writer.",
           "Review the exact exported reader independently for factual, causal, citation and scope correctness.",
           "Apply broker-specific cash/obligation treatment to HOOD; customer assets are not corporate excess cash."],
          ["Bounded NVDA verification and actual-reader review pass with declared residual limits.",
           "HOOD demonstrates distinct broker economics; user acceptance and production release remain separate gates."],
          "No new external search here: consume reviewed inputs, exact reader and complete usage telemetry.",
          "Keep draft status explicit; no repeated writer-only run on an unchanged evidence packet.",
          [0,2,23,29,30,37,38,39,40,161,164,165,182,183,184]),
    ]


def build(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("requires a fresh output outside the historical source")
    payload = read_json(source / "factual-payload.json")
    reply = read_json(source / "factual-reply.json")
    research = payload["research"]
    issues = research["inherited_issues"]
    questions = nvda_questions(issues)
    review, active, lifecycle = reconcile_review(
        LifecycleVerification.model_validate(reply["data"]), issues,
        research["resolution_evidence"], research["rendered_reader"],
        ModelResultScope.model_validate(research["conclusion_scope"]))
    if any(item.severity == "critical" for item in review.findings):
        raise ValueError("source lifecycle cannot be validated")
    register = question_register(issues, questions, analyst="Codex analyst-authored triage, not independent acceptance",
                                 source_identity=digest({"payload": payload, "reply": reply}))
    register["existing_lifecycle"] = lifecycle
    register["existing_active_issue_count"] = len(active)
    destination.mkdir(parents=True, exist_ok=False)
    atomic_write(destination / "question_register.json", canonical_json(register))
    atomic_write(destination / "QUESTION_REGISTER.md", render_question_register(register).encode())
    return register


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = build(args.source, args.destination)
    print(f"{result['question_count']} questions; {result['original_issue_count']} preserved records; "
          f"{len(result['unassigned_issue_ids'])} unassigned; no new resolutions")


if __name__ == "__main__":
    main()
