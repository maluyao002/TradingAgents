"""Prepare new, unreviewed NVDA reconciliation inputs from exact frozen rows.

Historical bundles and reviews are never edited or transferred to new identities.
This offline preparation does not authorize a live run or approve the economics.
"""

import argparse
import re
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from scripts.research_cashflow_review import _flat_verified_blobs, _unchanged
from tradingagents.research.case_context import FinancialCaseEnvelope, load_case_context
from tradingagents.research.cashflow_bridge import (
    CashFlowBridgePackage,
    HistoricalCashFlowReconciliationSelector,
    _exact_product,
    evaluate_cashflow_bridge,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, ResearchRequest
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import operating_scenario_package_sha256
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)

_NEW_ROLES = (
    "deferred_tax", "equity_gains", "other_adjustment", "receivables_movement",
    "inventory_movement", "prepaid_other_assets_movement", "payables_movement",
    "accrued_current_liabilities_movement", "other_long_term_liabilities_movement",
)
_ROW_LABELS = dict(zip(
    _NEW_ROLES,
    ("Deferred income taxes", "Gains from equity securities, net", "Other", "Accounts receivable",
     "Inventories", "Prepaid expenses and other assets", "Accounts payable",
     "Accrued and other current liabilities", "Other long-term liabilities"), strict=True))
_ROW_LABELS.update(net_income="Net income", stock_compensation="Stock-based compensation expense",
                   depreciation_amortization="Depreciation and amortization",
                   reported_cfo="Net cash provided by operating activities")
_TABLE_HEADER = ("Condensed Consolidated Statements of Cash Flows\n(In millions)\n(Unaudited)\n"
                 "\u00a0 Six Months Ended\n\u00a0 Jul 26, 2026 Jul 27, 2025\nCash flows from operating activities:\n")
_INTEGER_CELL = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_SIGNED_CELL = rf"(?:-?{_INTEGER_CELL}|\({_INTEGER_CELL}\))"


def prepare_packet(source: Path, mapping_path: Path, output: Path):
    if any(path.is_symlink() for path in (source, mapping_path, output)):
        raise ValueError("symlink inputs or destinations are not accepted")
    source, output = source.resolve(), output.resolve()
    if output.exists() or source in output.parents or output in source.parents:
        raise ValueError("output must be fresh and outside the source")
    blobs, manifest = _flat_verified_blobs(source, frozenset({
        "request.json", "evidence.json", "case_input.json",
    }))
    original = EvidenceSnapshot.model_validate_json(blobs["evidence.json"])
    old = FinancialCaseEnvelope.model_validate_json(blobs["case_input.json"])
    request = ResearchRequest.model_validate_json(blobs["request.json"])
    load_case_context(blobs["case_input.json"], request, original)
    mapping_bytes = read_bytes(mapping_path)
    mapping = parse_json(mapping_bytes)
    if mapping["evidence_sha256"] != evidence_snapshot_sha256(original):
        raise ValueError("source mapping belongs to a different evidence snapshot")
    filing = next(s for s in original.sources if s.id == mapping["source_id"])
    if filing.content_sha256 != mapping["source_content_sha256"] or mapping["unit"] != "USD millions":
        raise ValueError("source mapping identity or unit differs")
    if (original.ticker != "NVDA" or filing.id != "nvda-q2-filing"
            or mapping["period_start"] != "2026-01-26" or mapping["period_end"] != "2026-07-26"
            or filing.content.count(_TABLE_HEADER) != 1):
        raise ValueError("source mapping statement dates or header differ")
    table_start = filing.content.index(_TABLE_HEADER) + len(_TABLE_HEADER)
    table_end = filing.content.find("Cash flows from investing activities:", table_start)
    if table_end < 0:
        raise ValueError("cash-flow statement boundary missing")
    rows = {r["role"]: r for r in mapping["rows"]}
    if len(rows) != len(mapping["rows"]) or set(rows) != {
        *_NEW_ROLES, "net_income", "stock_compensation", "depreciation_amortization", "reported_cfo",
    }:
        raise ValueError("source mapping must contain the complete unique statement rows")
    facts = []
    anchor_ids = {"net_income": "nvda-net_income-h1", "stock_compensation": "nvda-stock_based_compensation-h1",
                  "depreciation_amortization": old.cashflow_bridge.historical_anchor.depreciation_amortization_fact_id,
                  "reported_cfo": old.cashflow_bridge.historical_anchor.operating_cash_flow_fact_id}
    original_facts = {f.id: f for f in original.facts}
    for role, row in rows.items():
        if (not table_start <= row["start"] < row["end"] <= table_end
                or filing.content[row["start"]:row["end"]] != row["text"]):
            raise ValueError("source mapping excerpt differs from exact source")
        label = re.split(r"\(?-?\d", row["text"], maxsplit=1)[0].replace("$", "").strip()
        if label != _ROW_LABELS[role]:
            raise ValueError("source mapping role differs from the reported row label")
        line_start = filing.content.rfind("\n", 0, row["start"]) + 1
        line_end = filing.content.find("\n", row["end"])
        if line_end < 0:
            line_end = len(filing.content)
        if ("\n" in row["text"] or "\r" in row["text"]
                or filing.content[line_start:row["start"]].strip()
                or filing.content[row["end"]:line_end].strip()):
            raise ValueError("source mapping must select a complete statement row")
        # The first numeric cell belongs to current six-month period; preserve
        # complete cells, including balanced parentheses and thousands groups.
        cells = re.fullmatch(
            re.escape(_ROW_LABELS[role]) + rf"\s+\$?\s*({_SIGNED_CELL})\s+\$?\s*{_SIGNED_CELL}\s*",
            row["text"],
        )
        if cells is None:
            raise ValueError("source mapping requires two complete integer cells")
        first = cells[1].replace(",", "").replace("(", "-").replace(")", "")
        if Decimal(first) != Decimal(str(row["signed_value_millions"])):
            raise ValueError("source mapping number differs from the first reported cell")
        if role in anchor_ids:
            fact = original_facts[anchor_ids[role]]
            if (_exact_product(fact.value, fact.scale) != _exact_product(Decimal(first), Decimal("1000000"))
                    or str(fact.period_start) != mapping["period_start"]
                    or str(fact.period_end) != mapping["period_end"]
                    or fact.unit != "USD" or fact.currency != "USD"):
                raise ValueError("source mapping reused statement row differs from existing anchor fact")
        if role in _NEW_ROLES:
            facts.append(FinancialFact(
                id=f"nvda-cfo-{role}-h1-fy27", metric=role,
                value=str(row["signed_value_millions"]), unit="USD", currency="USD", scale="1000000",
                period_type="duration", period_start=mapping["period_start"], period_end=mapping["period_end"],
                basis="US GAAP", source_id=filing.id,
                location=(f"source_sha256={filing.content_sha256}; chars=[{row['start']},{row['end']}); "
                          f"Cash flows, USD millions; six months ended July 26, 2026; "
                          f"first numeric column; exact_row={row['text']!r}"),
            ))
    if {f.id for f in facts} & {f.id for f in original.facts}:
        raise ValueError("new fact identities already exist")
    snapshot = EvidenceSnapshot.model_validate({**original.model_dump(mode="json"),
        "facts": [*original.facts, *facts]})
    case = old.case.model_copy(update={"snapshot_sha256": evidence_snapshot_sha256(snapshot)})
    operating = old.operating_scenarios.model_copy(update={
        "case_sha256": digest(case), "evidence_sha256": evidence_snapshot_sha256(snapshot), "review": None})
    selected = {"net_income": "nvda-net_income-h1", "stock_compensation": "nvda-stock_based_compensation-h1",
                **{role: f"nvda-cfo-{role}-h1-fy27" for role in _NEW_ROLES}}
    selector = HistoricalCashFlowReconciliationSelector(
        anchor_source_id="nvda-q2-release", cashflow_statement_source_id=filing.id,
        rows=tuple({"role": role, "fact_id": identifier,
                    "sign": "positive" if rows[role]["signed_value_millions"] > 0 else
                            "negative" if rows[role]["signed_value_millions"] < 0 else "zero"}
                   for role, identifier in selected.items()),
        asset_principal_cashflow_fact_id="nvda-asset_principal_cashflow-h1",
        issuer_free_cash_flow_fact_id="nvda-issuer_free_cash_flow-h1")
    cashflow = CashFlowBridgePackage.model_validate({**old.cashflow_bridge.model_dump(mode="json"),
        "case_sha256": digest(case), "evidence_sha256": evidence_snapshot_sha256(snapshot),
        "operating_package_sha256": operating_scenario_package_sha256(operating),
        "historical_reconciliation": selector, "review": None})
    # Cash-flow evaluation cannot precede a fresh operating review. Keep its
    # pending package outside the case envelope until that dependency is reviewed.
    envelope = FinancialCaseEnvelope(case=case, operating_scenarios=operating,
                                     source_passages=old.source_passages)
    request = request.model_copy(update={"backend": "replay", "output_dir": output / "run",
        "evidence_path": output / "evidence.json", "financial_case_path": output / "case_input.json"})
    context = load_case_context(canonical_json(envelope), request, snapshot)
    outputs = {"request.json": canonical_json(request), "evidence.json": canonical_json(snapshot),
        "case_input.json": canonical_json(envelope), **context.artifacts,
        "pending_cashflow_package.json": canonical_json(cashflow),
        "source_mapping.json": mapping_bytes,
        "provenance.json": canonical_json({"source_directory": str(source),
            "source_manifest_sha256": sha256(manifest).hexdigest(),
            "source_hashes": {name: sha256(raw).hexdigest() for name, raw in blobs.items()},
            "source_mapping_sha256": sha256(mapping_bytes).hexdigest(),
            "status": "draft_requires_fresh_operating_then_cashflow_review", "live_calls": 0,
            "new_fact_ids": [f.id for f in facts], "historical_reviews_transferred": False})}
    _unchanged(source, blobs, manifest)
    if read_bytes(mapping_path) != mapping_bytes:
        raise ValueError("source mapping changed during preparation")
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in outputs.items()},
        "status": "draft_requires_fresh_dependent_reviews"}))
    return {"output": str(output), "new_facts": len(facts), "live_calls": 0}


def prepare_pending_cashflow(operating_source: Path, draft_source: Path, output: Path):
    """Validate pending bridge only after a fresh operating review, never transfer reviews."""
    if any(p.is_symlink() for p in (operating_source, draft_source, output)):
        raise ValueError("symlink bundles are not accepted")
    operating_source, draft_source, output = (p.resolve() for p in (operating_source, draft_source, output))
    if output.exists() or any(output == p or p in output.parents or output in p.parents
                              for p in (operating_source, draft_source)):
        raise ValueError("output must be fresh and outside source bundles")
    names = frozenset({"request.json", "evidence.json", "case_input.json", "financial_case.json",
                       "operating_scenario_package.json", "operating_scenario_context.json"})
    blobs, manifest = _flat_verified_blobs(operating_source, names)
    pending, draft_manifest = _flat_verified_blobs(draft_source, frozenset({"pending_cashflow_package.json"}))
    request = ResearchRequest.model_validate_json(blobs["request.json"])
    snapshot = EvidenceSnapshot.model_validate_json(blobs["evidence.json"])
    context = load_case_context(blobs["case_input.json"], request, snapshot)
    if request.backend != "replay" or context.cashflow_bridge is not None:
        raise ValueError("source must be an operating-only replay bundle")
    if not context.operating_scenarios or not context.operating_scenarios.reviewed:
        raise ValueError("fresh operating review is required")
    if any(context.artifacts[name] != blobs[name] for name in names - {
            "request.json", "evidence.json", "case_input.json"}):
        raise ValueError("standalone operating artifacts differ from validated context")
    package = CashFlowBridgePackage.model_validate_json(pending["pending_cashflow_package.json"])
    if package.review is not None or package.historical_reconciliation is None:
        raise ValueError("pending package must be an unreviewed reconciliation")
    result = evaluate_cashflow_bridge(package, context.case, snapshot, context.operating_scenarios)
    outputs = {**result.artifacts, "provenance.json": canonical_json({
        "source_artifact_sha256": {name: sha256(blobs[name]).hexdigest() for name in (
            "evidence.json", "financial_case.json", "operating_scenario_package.json", "operating_scenario_context.json")},
        "draft_manifest_sha256": sha256(draft_manifest).hexdigest(),
        "pending_package_sha256": sha256(pending["pending_cashflow_package.json"]).hexdigest(),
        "independent_review_claimed": False, "model_calls": 0})}
    _unchanged(operating_source, blobs, manifest)
    _unchanged(draft_source, pending, draft_manifest)
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in outputs.items()},
        "status": "draft_requires_fresh_cashflow_review"}))
    return {"output": str(output), "reviewed": False, "model_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_packet(args.source, args.mapping, args.output))
