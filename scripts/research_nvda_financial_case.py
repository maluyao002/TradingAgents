"""Dated NVDA financial-case adapter; offline, source-bound and never acceptance.

The selectors deliberately target the frozen Q2 FY27 filing. New layouts need
reviewed adapters, not heuristic substitution of similar rows. Historical inputs
are read-only; generated case directories must be new.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import asdict
from datetime import date
from decimal import Context, Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact
from tradingagents.research.reviewed_inputs import ExactSourceMaterial
from tradingagents.research.storage import canonical_json, digest, parse_json, read_bytes, read_json

D = Decimal
MILLION = D("1e6")
BALANCE_DATE = date(2026, 7, 26)
PRIOR_DATE = date(2026, 1, 25)
FILING = "nvda-q2-filing"
BUCKETS = ("fy27-h2", "fy28", "fy29", "fy30", "fy31", "fy32-plus", "total")
ACCRUALS = (
    ("customer_programs", "Customer program accruals"),
    ("taxes_payable", "Taxes payable"),
    ("deferred_revenue", "Deferred revenue (1)"),
    ("warranty", "Product warranty"),
    ("inventory_obligations", "Excess inventory purchase obligations (2)"),
    ("payroll", "Accrued payroll and related expenses"),
    ("purchase_consideration", "Accrued purchase consideration (3)"),
    ("other_accruals", "Other"),
    ("total_accruals", "Total accrued and other current liabilities"),
)
COMMITMENTS = (
    ("supply", "Supply and capacity"),
    ("cloud", "Cloud service agreements"),
    ("uncommenced_leases", "Data center leases not commenced"),
    ("investments", "Equity investments"),
    ("capex", "Capital expenditures"),
    ("commitments", "Total"),
)
ADDITIONAL = (
    ("ai_cloud", "AI cloud agreements"),
    ("third_party_leases", "Data center leases not commenced for third party"),
    ("additional_commitments", "Total"),
)
COMMITMENT_KINDS = {
    "supply": "purchase",
    "capex": "purchase",
    "cloud": "cloud",
    "ai_cloud": "cloud",
    "uncommenced_leases": "lease",
    "third_party_leases": "lease",
    "investments": "other",
}


def _section(source, start, end, identifier, unit="USD", observed="2026-07-26"):
    if source.content.count(start) != 1:
        raise ValueError(f"missing or ambiguous section: {identifier}")
    begin = source.content.index(start)
    finish = source.content.find(end, begin + len(start))
    if finish < 0:
        raise ValueError(f"missing section end: {identifier}")
    return ExactSourceMaterial(id=identifier, source_id=source.id,
        source_sha256=source.content_sha256, start=begin, end=finish,
        text=source.content[begin:finish], unit=unit, observation_date=observed,
        context="Exact dated filing disclosure; classifications are separate analyst conventions.")


def _normalize(text):
    return " ".join(text.split())


def _number(token):
    if token == "—":
        return D(0)  # Only accepted inside the explicitly headed commitments table.
    if not re.fullmatch(r"(?:0|[1-9]\d{0,2}(?:,\d{3})*)(?:\.\d+)?", token):
        raise ValueError("unexpected financial cell")
    return D(token.replace(",", ""))


def _rows(material, rows, width, suffix):
    """Parse the complete row grammar, including the next row boundary."""
    text = _normalize(material.text)
    pieces = []
    for _, label in rows:
        pieces.append(re.escape(label) + r"\s+" + r"\s+".join(
            r"\$?\s*([\d,.]+|—)" for _ in range(width)))
    expression = r"\s+".join(pieces) + r"\s*" + (re.escape(suffix) if suffix else r"\Z")
    matches = list(re.finditer(expression, text))
    if len(matches) != 1:
        raise ValueError(f"changed or ambiguous rows: {material.id}")
    cells = matches[0].groups()
    return {name: tuple(_number(value) for value in cells[i * width:(i + 1) * width])
            for i, (name, _) in enumerate(rows)}


def extract_case_evidence(snapshot: EvidenceSnapshot):
    """Return a new enriched snapshot plus exact materials; do not mutate inputs."""
    snapshot = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json())
    if snapshot.ticker != "NVDA":
        raise ValueError("adapter is for NVDA only")
    source = next((source for source in snapshot.sources if source.id == FILING), None)
    if (source is None or source.availability != "full_text" or source.published_at is None
            or source.published_at > snapshot.cutoff or source.retrieved_at > snapshot.cutoff):
        raise ValueError("eligible full filing required")
    materials = [
        _section(source, "Accrued and Other Current Liabilities:", "(1) Included customer advances", "accrual-notes"),
        _section(source, "Note 10 - Commitments and Contingencies", "Supply and capacity  –", "commitment-buckets"),
        _section(source, "Additional Commitments\nFuture commitments", "AI cloud agreements –", "additional-buckets"),
        _section(source, "Note 14 - Leases\n", "Other information related to leases", "lease-notes"),
        _section(source, "The number of shares of common stock,", "\n\n[PDF page 2]", "cover-shares", "shares", "2026-08-21"),
        _section(source, "Liquidity\nOur primary sources", "Capital Return to Shareholders", "liquidity-notes"),
        _section(source, "Supply and capacity  –", "Accrual for Product Warranty Liabilities", "commitment-terms"),
        _section(source, "(1) Included $36.9 billion", "Publicly-held equity securities are subject", "securities-restrictions"),
    ]
    by_id = {item.id: item for item in materials}
    # The accrual table's immediately preceding dated header is required too.
    prefix = source.content[:by_id["accrual-notes"].start]
    if not _normalize(prefix).endswith("Jul 26, 2026 Jan 25, 2026"):
        raise ValueError("accrual date headers changed")
    if not by_id["accrual-notes"].text.startswith("Accrued and Other Current Liabilities: (In millions)"):
        raise ValueError("accrual units changed")
    # Include the header in delivered material, not merely in validation.
    start = source.content.rfind("Jul 26, 2026", 0, by_id["accrual-notes"].start)
    accrual = by_id["accrual-notes"]
    materials[0] = accrual.model_copy(update={"start": start, "text": source.content[start:accrual.end]})
    by_id["accrual-notes"] = materials[0]
    accrued = _rows(accrual, ACCRUALS, 2, "")
    with localcontext(Context(prec=50)):
        if any(sum(accrued[key][i] for key, _ in ACCRUALS[:-1]) != accrued["total_accruals"][i] for i in (0, 1)):
            raise ValueError("accrual components do not reconcile")

    facts = []
    def fact(key, value, material, scale=MILLION, end=BALANCE_DATE, unit="USD"):
        facts.append(FinancialFact(id=f"nvda-case-{key}", source_id=FILING, metric=key,
            value=value, scale=scale, unit=unit, currency="USD" if unit == "USD" else None,
            period_end=end, basis="Reported disclosure; not available cash or accepted model input",
            location=f"{material.id}; extracted-text characters {material.start}:{material.end}"))

    for key, values in accrued.items():
        for value, period, suffix in zip(values, (BALANCE_DATE, PRIOR_DATE), ("current", "prior"), strict=True):
            fact(f"{key}-{suffix}", value, materials[0], end=period)
    for identifier, rows in (("commitment-buckets", COMMITMENTS), ("additional-buckets", ADDITIONAL)):
        material = by_id[identifier]
        if "Remainder of 2027 2028 2029 2030 2031 2032 and thereafter Total (In billions)" not in _normalize(material.text):
            raise ValueError("commitment dates or units changed")
        values = _rows(material, rows, 7, "")
        if any(sum(row[:-1]) != row[-1] for row in values.values()):
            raise ValueError("commitment row totals do not reconcile")
        if any(sum(values[key][i] for key, _ in rows[:-1]) != values[rows[-1][0]][i] for i in range(7)):
            raise ValueError("commitment column totals do not reconcile")
        for key, row in values.items():
            for bucket, value in zip(BUCKETS, row, strict=True):
                fact(f"{key}-{bucket}", value, material, scale=D("1e9"))
    lease = by_id["lease-notes"]
    if "as of July 26, 2026" not in _normalize(lease.text) or "(In millions)" not in _normalize(lease.text):
        raise ValueError("lease date or units changed")
    lease_rows = (("lease-fy27-h2", "2027 (the second half of fiscal year 2027)"),
        ("lease-fy28", "2028"), ("lease-fy29", "2029"), ("lease-fy30", "2030"),
        ("lease-fy31", "2031"), ("lease-fy32-plus", "2032 and thereafter"),
        ("lease-total", "Total"), ("lease-interest", "Less imputed interest"),
        ("lease-pv", "Present value of net future minimum lease payments"),
        ("lease-current", "Less short-term operating lease liabilities"),
        ("lease-longterm", "Long-term operating lease liabilities"))
    lease_values = _rows(lease, lease_rows, 1, "As of")
    if (sum(lease_values[key][0] for key, _ in lease_rows[:6]) != lease_values["lease-total"][0]
            or lease_values["lease-total"][0] - lease_values["lease-interest"][0] != lease_values["lease-pv"][0]
            or lease_values["lease-current"][0] + lease_values["lease-longterm"][0] != lease_values["lease-pv"][0]):
        raise ValueError("lease bridge does not reconcile")
    for key, (value,) in lease_values.items():
        fact(key, value, lease)
    cover = by_id["cover-shares"]
    match = re.fullmatch(r"The number of shares of common stock, \$0\.001 par value, outstanding as of August 21, 2026, was (\d+\.\d+) billion\.", _normalize(cover.text))
    if match is None:
        raise ValueError("cover share date or basis changed")
    fact("basic-shares-cover", D(match[1]), cover, scale=D("1e9"), end=date(2026, 8, 21), unit="shares")
    release = next((item for item in snapshot.sources if item.id == "nvda-q2-release"), None)
    call = next((item for item in snapshot.sources if item.id == "nvda-q2-call"), None)
    if any(item is None or item.availability != "full_text" or item.published_at is None
           or item.published_at > snapshot.cutoff or item.retrieved_at > snapshot.cutoff
           for item in (release, call)):
        raise ValueError("eligible issuer guidance release and call required")
    materials.extend((
        _section(release, "Outlook\nNVIDIA’s outlook", "Highlights\n", "issuer-guidance", "mixed", "2026-08-26"),
        _section(call, "Let me turn to the outlook for the third quarter.", "With that, we will now transition", "issuer-outlook-call", "mixed", "2026-08-26"),
    ))
    original = {item.id: item for item in snapshot.facts}
    for item in facts:
        if item.id in original and original[item.id] != item:
            raise ValueError(f"conflicting extracted fact: {item.id}")
        original[item.id] = item
    enriched = EvidenceSnapshot.model_validate({**snapshot.model_dump(), "facts": tuple(original.values())})
    return enriched, tuple(materials)


def case_analysis(snapshot, materials, scenarios):
    """Reproducible partial bridges; none are a distributable-cash/equity claim."""
    facts = {item.id: item for item in snapshot.facts}
    def value(identifier):
        item = facts[identifier]
        if (item.unit != "USD" or item.currency != "USD" or item.period_end != BALANCE_DATE
                or item.period_type != "instant"):
            raise ValueError(f"incompatible opening financial fact: {identifier}")
        return item.normalized_value
    with localcontext(Context(prec=50)):
        ar = value("nvda-model-accounts_receivable-q2-fy27-end")
        inventory = value("nvda-model-inventory-q2-fy27-end")
        ap = value("nvda-model-accounts_payable-q2-fy27-end")
        operating_accruals = sum(value(f"nvda-case-{key}-current") for key in (
            "customer_programs", "deferred_revenue", "warranty", "inventory_obligations", "payroll"))
        identified_wc = ar + inventory - ap - operating_accruals
        prepaid = value("nvda-model-prepaid_and_other_current_assets-q2-fy27-end")
        other = value("nvda-case-other_accruals-current")
        proxy = value("nvda-operating-working-capital-q2-fy27-end")
        taxes = value("nvda-case-taxes_payable-current")
        acquisition = value("nvda-case-purchase_consideration-current")
        if identified_wc + prepaid - other - taxes - acquisition != proxy:
            raise ValueError("classified WC does not bridge to the original proxy")
        cash = value("nvda-model-cash_and_cash_equivalents-q2-fy27-end")
        securities = value("nvda-model-marketable_debt_securities-q2-fy27-end")
        debt = value("nvda-model-short_term_debt-q2-fy27-end") + value("nvda-model-long_term_debt-q2-fy27-end")
        revenue_fact = facts["nvda-revenue-ttm-q2-fy27"]
        if (revenue_fact.unit != "USD" or revenue_fact.currency != "USD"
                or revenue_fact.period_type != "duration" or revenue_fact.period_end != BALANCE_DATE
                or revenue_fact.period_start != date(2025, 7, 28)):
            raise ValueError("incompatible TTM revenue denominator")
        revenue = revenue_fact.normalized_value
        if revenue <= 0:
            raise ValueError("positive TTM revenue reference required")
        wc = {"original_mixed_proxy": proxy, "identified_operating_components": identified_wc,
            "unclassified_prepaid_assets": prepaid, "unclassified_other_accruals": other,
            "excluded_taxes_payable": taxes, "excluded_purchase_consideration": acquisition,
            "classification_low": identified_wc - other, "classification_high": identified_wc + prepaid,
            "identified_wc_to_ttm_revenue": identified_wc / revenue,
            "classification": "analyst_convention_not_complete_operating_wc",
            "range_meaning": "Only classification of the two unresolved mixed rows; not a probabilistic forecast range.",
            "no_double_count": "Inventory provisions remain in GAAP costs; accruals are a balance-sheet funding item, not a second expense."}
    text = {item.id: _normalize(item.text) for item in materials}
    required_guidance = ("NVIDIA’s outlook for the third quarter of fiscal 2027 is as follows:",
        "Revenue is expected to be $108.0 billion, plus or minus 2%.",
        "NVIDIA is not assuming any Data Center compute revenue from China in its outlook.",
        "gross margins are expected to be 74.0%, plus or minus 50 basis points.",
        "operating expenses are expected to be approximately $9.2 billion and $9.0 billion")
    if any(item not in text["issuer-guidance"] for item in required_guidance):
        raise ValueError("guidance selectors changed")
    if "fiscal year 2028 revenue to grow approximately 70% year-over-year" not in text["issuer-outlook-call"]:
        raise ValueError("preliminary FY28 outlook changed")
    return {"unit": "USD_unscaled", "balance_date": BALANCE_DATE,
        "scope": "Partial reconciliations and explicit analyst conventions; not accepted forecasts or current equity value.",
        "working_capital": wc,
        "liquidity": {"reported_cash": cash, "marketable_debt_securities": securities,
            "cash_plus_debt_securities": cash + securities, "carrying_debt": debt,
            "cash_only_net_debt": debt - cash, "debt_less_cash_and_debt_securities": debt - cash - securities,
            "availability": "not_assessed", "excess_cash": None,
            "limitations": ["Repatriation tax exception is not a zero-value asset or an estimated tax charge.",
                "Public equity lock-ups and private investments are not cash equivalents.",
                "Operating lease costs remain in margins; adding lease debt without an earnings adjustment would double count."]},
        "expectations_comparison": {
            "guidance": {"period": "Q3 FY27", "revenue_low": D("105.84e9"), "revenue_base": D("108e9"),
                "revenue_high": D("110.16e9"), "gross_margin": D(".74"), "gross_margin_tolerance": D(".005"),
                "gaap_opex": D("9.2e9"), "source_material_id": "issuer-guidance",
                "exclusion": "No China Data Center compute revenue assumed by issuer."},
            "preliminary_outlook": {"period": "FY28", "revenue_growth": D(".70"),
                "source_material_id": "issuer-outlook-call", "kind": "management_preliminary_expectation"},
            "consensus": {"value": None, "status": "unavailable_in_verified_packet"},
            "own_forecasts": [{"scenario_id": case.id, "period_start": case.periods[0].period_start,
                "period_end": case.periods[0].period_end, "revenue_growth": case.periods[0].revenue_growth,
                "revenue": revenue * (1 + case.periods[0].revenue_growth),
                "operating_margin_after_sbc": case.periods[0].operating_margin,
                "kind": "inherited_conditional_analyst_assumption"} for case in scenarios],
            "comparable": False,
            "reason": "Annual cutoff-anniversary model periods differ from issuer fiscal quarters/years. Gross and operating margins differ. No invented beat/miss or consensus spread.",
        },
        "business_drivers": [
            {"driver": "Demand and deployment", "source_material_ids": ["issuer-outlook-call"],
                "effect": "Faster system deployment supports revenue; power/memory bottlenecks delay recognition.",
                "falsifier": "Orders or deployments weaken despite additional capacity; receipts lag shipment revenue."},
            {"driver": "Memory cost and pricing", "source_material_ids": ["issuer-outlook-call"],
                "effect": "Input inflation pressures gross margin before price recovery; operating margins must also fund R&D.",
                "falsifier": "Price increases fail to offset higher memory costs; FY28 margin recovery does not occur."},
            {"driver": "Customer funding and infrastructure commitments", "source_material_ids": ["commitment-terms", "liquidity-notes"],
                "effect": "Credit support, longer payment terms and investments can consume cash despite positive modeled FCFF.",
                "falsifier": "Receivables grow faster than sales, partner default triggers guarantees, or capacity commitments remain unused."},
        ],
        "scenario_review": "Numerical paths retained as explicit inherited scenarios, not re-underwritten or cleared by new disclosures. Fresh economic review remains required.",
    }


def build_nvda_case(snapshot, materials, request):
    """Map explicit classification conventions onto selected (nonduplicated) facts."""
    from tradingagents.research.financial_case import (
        CommitmentItem,
        CommitmentSchedule,
        ConclusionAssessment,
        EvidenceGap,
        FinancialConvention,
        FinancialReconciliationInput,
        ReconciliationSchedule,
        ScheduleComponent,
        evidence_snapshot_sha256,
    )

    facts = {item.id: item for item in snapshot.facts}
    conventions = []
    def component(identifier, fact_id, classification, effect, rationale):
        fact = facts[fact_id]
        cid = f"convention-{identifier}"
        conventions.append(FinancialConvention(id=cid, kind="convention", description=rationale,
            affected_component_ids=(identifier,)))
        return ScheduleComponent(id=identifier, fact_id=fact_id, classification=classification,
            effect=effect, normalized_value=fact.normalized_value, unit=fact.unit, currency=fact.currency,
            period_start=fact.period_start, period_end=fact.period_end, convention_ids=(cid,))
    wc = [component(key, f"nvda-model-{metric}-q2-fy27-end", classification, effect, rationale)
          for key, metric, classification, effect, rationale in (
        ("receivables", "accounts_receivable", "operating_current_asset", "add", "Trade receivables are operating funding needs; extended terms create collection risk."),
        ("inventory", "inventory", "operating_current_asset", "add", "Inventory is operating capital; expense provisions remain in GAAP margins."),
        ("payables", "accounts_payable", "operating_current_liability", "subtract", "Trade payables offset operating funding needs."),
        ("prepaid-mixed", "prepaid_and_other_current_assets", "operating_current_asset", "unresolved", "No note-level operating/non-operating disaggregation; not assumed zero."),
    )]
    for key, _ in ACCRUALS[:-1]:
        effect = "exclude" if key in {"taxes_payable", "purchase_consideration"} else (
            "unresolved" if key == "other_accruals" else "subtract")
        wc.append(component(f"wc-{key}", f"nvda-case-{key}-current", "operating_current_liability", effect,
            "Analyst classification: exclude tax/acquisition financing, retain operating accruals, leave the mixed other row unresolved; no second expense deduction."))
    cash = tuple(component(f"assets-{key}", fact_id, "other_cash_or_security", "unresolved",
        "Reported asset, not certified excess/distributable cash; realization, operating needs, taxes and restrictions remain open.")
        for key, fact_id in (
            ("cash", "nvda-model-cash_and_cash_equivalents-q2-fy27-end"),
            ("debt-securities", "nvda-model-marketable_debt_securities-q2-fy27-end"),
            ("equity-securities", "nvda-model-marketable_equity_securities-q2-fy27-end"),
            ("private-investments", "nvda-nonmarketable_securities-q2-end")))
    debt = tuple(component(f"debt-{key}", fact_id, kind, effect, rationale)
        for key, fact_id, kind, effect, rationale in (
            ("short", "nvda-model-short_term_debt-q2-fy27-end", "borrowed_debt", "add", "Reported carrying amount, not a debt fair-value claim."),
            ("long", "nvda-model-long_term_debt-q2-fy27-end", "borrowed_debt", "add", "Reported carrying amount, not a debt fair-value claim."),
            ("lease", "nvda-case-lease-pv", "operating_lease", "exclude", "Operating lease cost stays in GAAP margins; no additional debt deduction without a matched EBIT/FCFF restatement.")))
    shares = (
        component("shares-quarter-proxy", "nvda-diluted-shares-q2-fy27", "diluted_shares", "unresolved",
            "Quarterly weighted-average diluted shares are a duration proxy, not point-in-time capitalization."),
        component("shares-cover-basic", "nvda-case-basic-shares-cover", "share_adjustment", "unresolved",
            "Rounded August cover basic shares are shown for comparison only, never summed with the quarterly diluted proxy."),
    )
    commitments = []
    for key, _ in (*COMMITMENTS[:-1], *ADDITIONAL[:-1]):
        for bucket in BUCKETS[:-1]:
            identifier = f"commitment-{key}-{bucket}"
            fact = facts[f"nvda-case-{key}-{bucket}"]
            cid = f"convention-{identifier}"
            conventions.append(FinancialConvention(id=cid, kind="convention", affected_component_ids=(identifier,),
                description="Reported fiscal bucket is retained in the item ID; exact payment dates and overlap with forecast costs/capex/WC are unassessed. No incremental deduction."))
            commitments.append(CommitmentItem(id=identifier, fact_id=fact.id,
                normalized_value=fact.normalized_value, unit="USD", currency="USD", period_end=fact.period_end,
                kind=COMMITMENT_KINDS[key],
                timing="unknown", disclosed_timing=bucket, overlap="unknown",
                treatment="not_assessed", convention_ids=(cid,)))
    equity_funding = ("equity_per_share_value", "funding_assessment")
    gaps = (
        EvidenceGap(id="gap-wc", area="operating_working_capital", blocks=("operating_asset_value", *equity_funding),
            description="Two mixed rows remain unclassified. Reconciled operating value is blocked; legacy proxy scenarios remain separately labelled conditional illustrations.", evidence_ids=(FILING,)),
        EvidenceGap(id="gap-liquidity", area="cash_and_securities", blocks=equity_funding,
            description="Cash operating reserve, tax/lock-up restrictions and realizable investment values are not reconciled; debt-minus-cash is not a complete equity bridge.", evidence_ids=(FILING,)),
        EvidenceGap(id="gap-shares", area="shares", blocks=("equity_per_share_value",),
            description="No diluted point-in-time share roll-forward, option/RSU treatment or current capitalization.", evidence_ids=(FILING,)),
        EvidenceGap(id="gap-opening", area="opening_date", blocks=("opening_date_alignment", *equity_funding),
            description="July balance-sheet dates and August basic shares are not rolled forward to the case cutoff.", evidence_ids=(FILING,)),
        EvidenceGap(id="gap-commitment-coverage", area="commitments", blocks=equity_funding,
            description="Fiscal commitment buckets are disclosed but exact cash timing, scenario cost/capex/WC overlap and conditional guarantees are not modeled. Gross guarantees are not expected losses or certain debt.", evidence_ids=(FILING,)),
        EvidenceGap(id="gap-fiscal-forecast", area="expectations", blocks=("operating_asset_value",),
            description="Fiscal guidance-to-calendar model bridge and economic underwriting remain open; eligible consensus and dated stock quote are unavailable.", evidence_ids=("nvda-q2-release", "nvda-q2-call")),
    )
    schedules = tuple(ReconciliationSchedule(id=f"schedule-{kind}", kind=kind, status="partial",
        rationale=rationale, components=components, share_basis=basis)
        for kind, components, basis, rationale in (
            ("operating_working_capital", tuple(wc), None, "Identified operating components with explicit excluded and unresolved rows."),
            ("cash_and_securities", cash, None, "Reported balances only; legal/operating availability is not certified."),
            ("debt_and_leases", debt, None, "Carrying debt and lease classification; current-date market-value bridge incomplete."),
            ("shares", shares, "latest_quarter_diluted_proxy", "Two incompatible share observations, neither is current diluted capitalization.")))
    return FinancialReconciliationInput(ticker="NVDA", cutoff=snapshot.cutoff,
        timezone=request.timezone,
        snapshot_sha256=evidence_snapshot_sha256(snapshot), opening_date=request.cutoff.astimezone(
            ZoneInfo(request.timezone)).date(), schedules=schedules,
        commitments=CommitmentSchedule(id="commitments", status="partial", items=tuple(commitments),
            rationale="Separate supply/cloud/investment/lease/capex buckets; totals are not added to detail or deducted from FCFF."),
        conventions=tuple(conventions), gaps=gaps,
        assessments=tuple(ConclusionAssessment(id=f"assessment-{output}", output=output, status="not_assessed",
            rationale="No complete model-bound reconciliation; this source-classification workpaper cannot clear the conclusion.")
            for output in ("equity_bridge", "funding")))


def inherited_proxy_audit(snapshot, scenarios):
    """Recompute mechanical references; do not promote them through case gates."""
    from dataclasses import replace

    from tradingagents.research.valuation import FCFFModelInput, ValuationUnits, dcf_valuation

    facts = {item.id: item for item in snapshot.facts}
    inputs = {name: facts[identifier].normalized_value for name, identifier in {
        "current_revenue": "nvda-revenue-ttm-q2-fy27", "current_working_capital": "nvda-operating-working-capital-q2-fy27-end",
        "net_debt": "nvda-net-debt-q2-fy27-end", "current_diluted_shares": "nvda-diluted-shares-q2-fy27"}.items()}
    cases = []
    for case in scenarios:
        model = FCFFModelInput(as_of_date=case.periods[0].period_start, **inputs,
            units=ValuationUnits("USD", D(1), D(1)), periods=case.periods,
            discount_rate=case.discount_rate, terminal_growth=case.terminal_growth)
        result = dcf_valuation(model)
        cells = []
        for rate_change in (D("-.01"), D(0), D(".01")):
            for growth_change in (D("-.005"), D(0), D(".005")):
                adjusted = replace(model, discount_rate=model.discount_rate + rate_change,
                    terminal_growth=model.terminal_growth + growth_change)
                cell = dcf_valuation(adjusted)
                cells.append({"discount_rate": adjusted.discount_rate, "terminal_growth": adjusted.terminal_growth,
                    "enterprise_value": cell.enterprise_value})
        cases.append({"id": case.id, "typed_input": asdict(model), "input_sha256": digest(asdict(model)),
            "enterprise_value": result.enterprise_value, "forecasts": [asdict(row) for row in result.forecasts],
            "sensitivities": cells})
    return {"scope": "RAW MECHANICAL AUDIT ONLY: inherited mixed-WC, cash-only debt and weighted-average shares. "
            "These values are not cleared outputs of the Stage 2 reconciliation. Equity values are deliberately omitted.",
        "sensitivity_convention": "Hold forecasts/reinvestment fixed when changing rate/g; not nine economically re-underwritten scenarios.",
        "cases": cases}


def prepare_financial_case(packet: Path, output: Path, demand_cache: Path | None = None):
    """Refresh an immutable *draft* case; old conditional clearance is not copied."""
    from cli.research import _resolve_paths
    from scripts.research_assumption_package import build_package
    from scripts.research_nvda_scenarios import _publish, authored_cases, populate_assumptions
    from scripts.research_reviewed_inputs import market_material
    from tradingagents.research.contracts import ResearchRequest
    from tradingagents.research.derivations import terminal_derivation
    from tradingagents.research.evidence import validate_snapshot
    from tradingagents.research.scenario_compiler import REVIEWED_PACKET_FILES

    packet, output = packet.resolve(), output.resolve()
    if output.exists() or packet == output or packet in output.parents:
        raise ValueError("output must be a new directory outside the source packet")
    manifest = read_json(packet / "manifest.json")
    if set(manifest.get("artifact_hashes", {})) != REVIEWED_PACKET_FILES:
        raise ValueError("source packet inventory incomplete")
    blobs = {name: read_bytes(packet / name) for name in REVIEWED_PACKET_FILES}
    if any(hashlib.sha256(blob).hexdigest() != manifest["artifact_hashes"][name] for name, blob in blobs.items()):
        raise ValueError("source packet hash mismatch")
    if len(blobs["request.json"]) > 1024 * 1024:
        raise ValueError("request exceeds size limit")
    request = ResearchRequest.model_validate(_resolve_paths(parse_json(blobs["request.json"]), packet))
    original = EvidenceSnapshot.model_validate_json(blobs["evidence.json"])
    if validate_snapshot(original, request) != original:
        raise ValueError("source snapshot requires normalization before case preparation")
    snapshot, materials = extract_case_evidence(original)
    independent_claims = ()
    if demand_cache is not None:
        from scripts.research_nvda_demand_evidence import load_demand_evidence
        from tradingagents.research.sources import FileSourceCache
        source, independent_material, independent_claims = load_demand_evidence(FileSourceCache(demand_cache))
        if source.id in {item.id for item in snapshot.sources}:
            raise ValueError("independent source ID already exists")
        cutoff = max(snapshot.cutoff, source.retrieved_at, source.published_at)
        snapshot = EvidenceSnapshot.model_validate({**snapshot.model_dump(), "cutoff": cutoff,
            "sources": (*snapshot.sources, source)})
        materials = (*materials, *independent_material)
    request = ResearchRequest.model_validate({**request.model_dump(), "cutoff": snapshot.cutoff,
        "evidence_path": output / "scenario_packet/evidence.json", "output_dir": output / "not_authorized_live_run",
        "report_language": "English", "additional_report_languages": ()})
    snapshot = validate_snapshot(snapshot, request)
    market = parse_json(blobs["market_inputs.json"])
    materials = (*materials, *market_material(snapshot, market))
    raw = canonical_json(snapshot)
    package, calibration = build_package(request, raw)
    scenarios, economic_audit = authored_cases(request, market)
    package = populate_assumptions(package, scenarios, snapshot)
    analysis = case_analysis(snapshot, materials, scenarios)
    analysis["independent_demand_counterevidence"] = independent_claims
    analysis["source_packet_sha256"] = digest(manifest)
    analysis["market_vintages"] = {key: market[key] for key in ("risk_free_as_of", "erp_as_of", "beta_as_of")}
    analysis["market_refresh"] = "Dated observations retained, not refreshed to the new case cutoff. No price-implied analysis without a dated quote."
    derivations = tuple(terminal_derivation(case, economic_audit[case.id]) for case in scenarios)
    # The case and presentation are built before publishing any output. The
    # financial-case validator does not confer economic or human acceptance.
    financial_case = build_nvda_case(snapshot, materials, request)
    from tradingagents.research.financial_case import reconcile_financial_case
    reconciliation = reconcile_financial_case(financial_case, snapshot)
    proxy_audit = inherited_proxy_audit(snapshot, scenarios)
    packet_blobs = {"request.json": canonical_json(request), "evidence.json": raw,
        "assumptions.json": canonical_json(package), "scenarios.json": canonical_json(scenarios),
        "economic_audit.json": canonical_json(economic_audit), "market_inputs.json": blobs["market_inputs.json"],
        "calibration.json": canonical_json(calibration)}
    artifacts = {"financial_case.json": canonical_json(financial_case),
        "reconciliation.json": canonical_json(reconciliation), "case_analysis.json": canonical_json(analysis),
        "source_material.json": canonical_json(materials), "terminal_derivations.json": canonical_json(derivations),
        "inherited_proxy_audit.json": canonical_json(proxy_audit),
        "scenario_packet_hashes.json": canonical_json({name: hashlib.sha256(blob).hexdigest()
            for name, blob in packet_blobs.items()}),
        "README.md": render_case_summary(analysis, reconciliation).encode()}
    output.parent.mkdir(parents=True, exist_ok=True)
    _publish(output, artifacts)
    _publish(output / "scenario_packet", packet_blobs)
    return {"status": "draft_requires_fresh_independent_review", "output": str(output),
        "financial_case_sha256": digest(financial_case), "facts": len(snapshot.facts),
        "model_calls": 0, "source_packet_unchanged": True}


def render_case_summary(analysis, reconciliation):
    wc = analysis["working_capital"]
    liquidity = analysis["liquidity"]
    return "\n".join([
        "# NVDA Stage 2 financial case — review draft", "",
        "This is a financial reconciliation workpaper, not a final deep-research report or accepted equity valuation.", "",
        "## What changed", "",
        "Filing notes now expose accrued-liability classifications, lease obligations, commitment timing and securities restrictions. "
        "All source passages and numeric ancestry remain inspectable in the adjacent artifacts.", "",
        f"Identified operating working-capital components total ${wc['identified_operating_components'] / D('1e9'):.3f}bn. "
        f"The unresolved-row classification range is ${wc['classification_low'] / D('1e9'):.3f}–"
        f"${wc['classification_high'] / D('1e9'):.3f}bn, versus the old mixed proxy of ${wc['original_mixed_proxy'] / D('1e9'):.3f}bn.", "",
        f"Reported cash plus marketable debt securities is ${liquidity['cash_plus_debt_securities'] / D('1e9'):.3f}bn; "
        "this is not freely distributable excess cash. Locked-up equity holdings and private investments are not cash equivalents.", "",
        "## Forecast and evidence boundaries", "",
        "Issuer fiscal guidance and inherited calendar-anniversary forecasts are displayed separately; consensus is unknown. "
        "No artificial beat/miss comparison, new probabilities, or implied stock target is produced. "
        "The old numerical paths remain explicit conditional assumptions and require fresh economic review.", "",
        "Customer evidence includes both demand support and counterevidence when the optional independent source is supplied. "
        "One customer is not broad market validation and its capital spending is not NVIDIA revenue.", "",
        "## Still unresolved", "",
        "Prepaid/other-row classification; distributable cash and investment realization/tax; diluted point-in-time shares; "
        "July-to-cutoff opening roll-forward; cash timing and model coverage of commitments/guarantees; "
        "fiscal guidance-to-forecast bridge and company-specific economic underwriting. "
        "These are engineering/evidence gaps, not requests that the user choose a WACC.", "",
        "See reconciliation.json for machine-readable gap-to-conclusion mappings. Equity and funding conclusions remain withheld. "
        "No new live model call or report acceptance is implied.", "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--demand-cache", type=Path)
    args = parser.parse_args()
    print(canonical_json(prepare_financial_case(args.packet, args.output, args.demand_cache)).decode())


if __name__ == "__main__":
    main()
