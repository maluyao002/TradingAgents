"""Reviewed NVDA Q2 FY27 table adapter for this pilot only; no network or LLM.

Fail closed on changed headers/rows. Keep quarter, half-year and instant facts
distinct. Does not construct unsupported TTM/DCF inputs or relabel weighted
average shares as point-in-time diluted shares.
"""
import argparse
import re
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact
from tradingagents.research.sources import FileSourceCache
from tradingagents.research.storage import atomic_write, canonical_json, read_json


def numbers(cells):
    text = " ".join(cells).replace("$", "").replace(",", "")
    pattern = r"\(\s*\d+(?:\.\d+)?\s*\)|\d+(?:\.\d+)?"
    if re.sub(pattern, "", text).strip():
        raise ValueError("unrecognized numeric cell")
    return [Decimal(x.replace(" ", "").replace("(", "-").replace(")", ""))
            for x in re.findall(pattern, text)]


def extract_facts(raw):
    tables = BeautifulSoup(raw, "html.parser").find_all("table")
    if len(tables) != 7 or "Q2 FY27" not in tables[0].get_text():
        raise ValueError("unexpected issuer table layout")
    groups = [
        (2, "STATEMENTS OF INCOME", {
            "Revenue": "revenue", "Cost of revenue": "cost_of_revenue",
            "Operating income": "operating_income", "Net income": "net_income",
            "Other income, net": "other_income_net", "Income tax expense": "income_tax_expense"}),
        (3, "BALANCE SHEETS", {
            "Cash and cash equivalents": "cash", "Marketable debt securities": "marketable_debt_securities",
            "Marketable equity securities": "marketable_equity_securities",
            "Accounts receivable, net": "accounts_receivable", "Inventories": "inventory",
            "Short-term debt": "short_term_debt", "Long-term debt": "long_term_debt",
            "Non-marketable securities": "nonmarketable_securities", "Total assets": "assets",
            "Total liabilities": "liabilities", "Shareholders' equity": "shareholders_equity"}),
        (4, "STATEMENTS OF CASH FLOWS", {
            "Net cash provided by operating activities": "operating_cash_flow",
            "Stock-based compensation expense": "stock_based_compensation",
            "Gains from equity securities, net": "equity_gains_cashflow_adjustment",
            "Accounts receivable": "receivables_cashflow_change",
            "Inventories": "inventory_cashflow_change",
            "Purchases of equity securities": "equity_security_purchases_cashflow",
            "Purchases related to property and equipment and intangible assets": "capex_cashflow",
            "Principal payments on property and equipment and intangible assets": "asset_principal_cashflow"}),
        (5, "RECONCILIATION OF GAAP TO NON-GAAP", {"Free cash flow": "issuer_free_cash_flow"}),
    ]
    facts = []
    for index, header, mapping in groups:
        table = tables[index]
        if header not in table.get_text() or "July 26," not in table.get_text() or "2026" not in table.get_text():
            raise ValueError("unexpected table header")
        seen = set()
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"], recursive=False)]
            cells = [c for c in cells if c]
            if not cells or cells[0] not in mapping:
                continue
            label = cells[0]
            if label in seen:
                raise ValueError("ambiguous table row")
            seen.add(label)
            values = numbers(cells[1:])
            expected = 2 if index == 3 else 5 if index == 5 else 4
            if len(values) != expected:
                raise ValueError("unexpected table column count")
            periods = ([(0, "2026-07-26", None, "q2-end"), (1, "2026-01-25", None, "fy26-end")]
                       if index == 3 else [(0, "2026-07-26", "2026-04-27", "q2"),
                        (3 if index == 5 else 2, "2026-07-26", "2026-01-26", "h1")])
            for col, end, start, suffix in periods:
                metric = mapping[label]
                facts.append(FinancialFact(id=f"nvda-{metric}-{suffix}", source_id="nvda-q2-release",
                    metric=metric, value=values[col], scale=Decimal(1_000_000), unit="USD", currency="USD",
                    period_start=start, period_end=end, period_type="duration" if start else "instant",
                    basis="issuer non-GAAP FCF" if index == 5 else "US GAAP",
                    location=f"HTML table {index + 1}, {header}, row '{label}', numeric column {col + 1}; in USD millions"))
        if seen != set(mapping):
            raise ValueError("missing required table rows: " + str(set(mapping) - seen))
    return tuple(facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    args = parser.parse_args()
    output = args.inputs / "evidence_with_facts.json"
    if output.exists():
        parser.error("frozen fact snapshot already exists")
    snapshot = EvidenceSnapshot.model_validate(read_json(args.inputs / "evidence.json"))
    source = next(s for s in snapshot.sources if s.id == "nvda-q2-release")
    raw = FileSourceCache(args.inputs / "source-cache").get(source.url)
    if raw is None or raw.text_sha256 != source.content_sha256:
        raise ValueError("raw acquisition does not match frozen source")
    facts = extract_facts(raw.raw)
    data = snapshot.model_dump(mode="json")
    data["facts"] = [fact.model_dump(mode="json") for fact in facts]
    enriched = EvidenceSnapshot.model_validate(data)
    atomic_write(output, canonical_json(enriched))
    print(len(facts), "source-bound facts; frozen cutoff", snapshot.cutoff.isoformat())


if __name__ == "__main__":
    main()
