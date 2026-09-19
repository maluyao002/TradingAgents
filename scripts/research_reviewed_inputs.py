"""Offline adapter from the frozen September NVDA packet to reusable model inputs.

The source-specific selectors intentionally fail closed on changed table layouts.
No fetching, inference, packet rewrite or investment acceptance occurs here.
"""
from __future__ import annotations

import re
from decimal import Context, Decimal, localcontext

from tradingagents.research.derivations import terminal_derivation
from tradingagents.research.reviewed_inputs import (
    ConditionalEconomicAudit,
    ExactSourceMaterial,
    ReviewedModelInputs,
    validate_material,
)


def _between(source, start_marker, end_marker, identifier, unit, observation_date, context):
    if source.content.count(start_marker) != 1:
        raise ValueError(f"ambiguous material start: {identifier}")
    start = source.content.index(start_marker)
    end = source.content.find(end_marker, start + len(start_marker))
    if end < 0:
        raise ValueError(f"missing material boundary: {identifier}")
    return ExactSourceMaterial(
        id=identifier, source_id=source.id, source_sha256=source.content_sha256,
        start=start, end=end, text=source.content[start:end], context=context,
        unit=unit, observation_date=observation_date,
    )


def market_material(snapshot, market):
    sources = {source.id: source for source in snapshot.sources}
    required = {"market-fed-h15", "market-damodaran-erp", "market-damodaran-beta", "market-fed-sep"}
    if set(market.get("source_ids", ())) != required or not required <= sources.keys():
        raise ValueError("reviewed market source inventory is incomplete")
    specs = (
        ("market-fed-h15", "Release date: September 17, 2026\n", "\nInflation indexed 10\n",
         "treasury-table", "percent_per_annum", "2026-09-16", "Nominal constant-maturity 10-year; last dated column, not TIPS."),
        ("market-damodaran-erp", "Implied ERP on September 1, 2026", "\nDay-to-day ERP",
         "erp-statement", "percent", "2026-09-01", "Adjusted-payout trailing-12-month ERP, with original Treasury pairing and default spread."),
        ("market-damodaran-beta", "Date of Analysis:", "\nAdvertising\n",
         "beta-headers", "ratio", "2026-01", "US industry table date and complete column headers."),
        ("market-damodaran-beta", "\nSemiconductor\n", "\nSemiconductor Equip",
         "beta-row", "ratio", "2026-01", "Semiconductor sector, not equipment or a measured NVDA beta."),
        ("market-fed-sep", "For release at 2:00 p.m., EDT, September 16, 2026\n", "\nCore PCE inflation",
         "macro-table", "percent", "2026-09-16", "SEP longer-run median GDP/PCE, not a company forecast."),
    )
    materials = tuple(_between(sources[sid], start, end, identifier, unit, observed, context)
                      for sid, start, end, identifier, unit, observed, context in specs)
    text = {item.id: item.text for item in materials}
    nominal = text["treasury-table"].split("\nNominal 9\n", 1)
    if len(nominal) != 2 or "Yields in percent per annum\nInstruments\n" not in nominal[0]:
        raise ValueError("Treasury units or nominal section missing")
    header = nominal[0].split("Instruments\n", 1)[1]
    if not header.startswith("2026\nSep\n10\n2026\nSep\n11\n2026\nSep\n14\n2026\nSep\n15\n2026\nSep\n16\n"):
        raise ValueError("Treasury dated headers changed")
    row = re.findall(r"\n10-year\n((?:\d+\.\d+\n){5})20-year\n", nominal[1])
    if len(row) != 1:
        raise ValueError("Treasury exact row missing or changed")
    erp = re.findall(r"=\s*(\d+\.\d+)% \(Trailing 12 month, with adjusted payout\)", text["erp-statement"])
    paired = re.findall(r"US treasury rate of (\d+\.\d+)% used as the riskfree rate", text["erp-statement"])
    beta = text["beta-row"].strip().splitlines()
    beta_headers = (
        "Industry Name\nNumber of firms\nBeta\nD/E Ratio\nEffective Tax rate\nUnlevered beta\n"
        "Cash/Firm value\nUnlevered beta corrected for cash\nHiLo Risk\nStandard deviation of equity\n"
        "Standard deviation in operating income (last 10\nyears)"
    )
    if (len(beta) != 11 or beta[:3] != ["Semiconductor", "66", "1.52"]
            or "Data used is as of January 2026" not in text["beta-headers"]
            or not text["beta-headers"].endswith(beta_headers)
            or len(erp) != 1 or len(paired) != 1):
        raise ValueError("ERP or beta exact context changed")
    macro = text["macro-table"]
    if "Percent\nVariable\nMedian1\nCentral Tendency2\nRange3\n" not in macro:
        raise ValueError("SEP units/headers changed")
    dates = "2026\n2027\n2028\n2029\nLonger run\n" * 3
    if dates not in macro:
        raise ValueError("SEP date columns changed")
    with localcontext(Context(prec=40)):
        expected = {
            "risk_free_rate": Decimal(row[0].splitlines()[-1]) / 100,
            "erp": Decimal(erp[0]) / 100, "erp_risk_free_rate": Decimal(paired[0]) / 100,
            "unlevered_beta_cash_corrected": Decimal(beta[7]),
        }
        for key, label in (("real_growth_long_run", "Change in real GDP"), ("inflation_long_run", "PCE inflation")):
            rows = re.findall(rf"\n{label}\n(.*?)\nJune projection(?=\n|\Z)", macro, re.DOTALL)
            if len(rows) != 1 or len(rows[0].splitlines()) != 15:
                raise ValueError("SEP exact median row missing or ambiguous")
            expected[key] = Decimal(rows[0].splitlines()[4]) / 100
        if any(Decimal(str(market.get(key))) != value for key, value in expected.items()):
            raise ValueError("market values differ from delivered exact source rows")
    if any(market.get(key) != value for key, value in {
        "risk_free_as_of": "2026-09-16", "erp_as_of": "2026-09-01", "beta_as_of": "2026-01",
    }.items()):
        raise ValueError("market observation dates differ from source context")
    return materials


def build_reviewed_inputs(snapshot, package, scenarios, market, economic_audit, review_sha256):
    """Build from previously verified packet bytes; source and arithmetic checks add depth."""
    if set(economic_audit) != {case.id for case in scenarios}:
        raise ValueError("economic derivation inventory differs from scenarios")
    for audit in economic_audit.values():
        ConditionalEconomicAudit.model_validate(audit)
    derivations = tuple(terminal_derivation(case, economic_audit[case.id]) for case in scenarios)
    with localcontext(Context(prec=40)):
        for case in scenarios:
            audit = economic_audit[case.id]
            rate, erp = Decimal(market["risk_free_rate"]), Decimal(market["erp"])
            if (Decimal(str(audit["treasury_reference"])) != rate
                    or Decimal(str(audit["erp"])) != erp
                    or Decimal(str(audit["beta_reference_not_nvda_beta"])) != Decimal(market["unlevered_beta_cash_corrected"])
                    or abs(rate + Decimal(str(audit["analyst_beta"])) * erp - case.discount_rate) > Decimal("1e-24")
                    or Decimal(str(audit["discount_rate"])) != case.discount_rate):
                raise ValueError("discount-rate derivation differs from reviewed market inputs")
    bundle = ReviewedModelInputs(
        ticker=snapshot.ticker, cutoff=snapshot.cutoff, evidence_sha256=package.evidence_sha256,
        review_sha256=review_sha256, source_material=market_material(snapshot, market),
        market_inputs=market, economic_derivations=economic_audit,
        financial_facts=snapshot.facts, assumptions=package,
        scenarios=scenarios, terminal_derivations=derivations,
        limitations=tuple(dict.fromkeys((*package.limitations, *market.get("limitations", ()),
            "Mechanical delivery and arithmetic checks are not economic or human acceptance.",
            "Company-wide funding and opening-date alignment are not assessed; equity/per-share conclusions are withheld."))),
    )
    validate_material(bundle, snapshot)
    return bundle
