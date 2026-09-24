"""Portable offline checks for the immutable NVDA reconciliation packet builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest

from scripts.research_cashflow_review import attach_cashflow_review
from scripts.research_nvda_reconciliation_packet import (
    _NEW_ROLES,
    _ROW_LABELS,
    _TABLE_HEADER,
    prepare_packet,
    prepare_pending_cashflow,
)
from tests.test_research_cashflow_bridge import bridge_setup
from tradingagents.research.case_context import (
    FinancialCaseEnvelope,
    FinancialCaseReview,
    load_case_context,
)
from tradingagents.research.cashflow_bridge import (
    CashFlowBridgePackage,
    CashFlowBridgeReview,
    cashflow_bridge_package_sha256,
)
from tradingagents.research.contracts import EvidenceSnapshot, FinancialFact, ResearchRequest
from tradingagents.research.financial_case import evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    OperatingScenarioPackage,
    operating_scenario_package_sha256,
)
from tradingagents.research.storage import canonical_json, digest, read_json

_SOURCE_ROWS = (
    ("net_income", "410"),
    ("stock_compensation", "10"),
    ("depreciation_amortization", "20"),
    ("deferred_tax", "5"),
    ("equity_gains", "-20"),
    ("other_adjustment", "2"),
    ("receivables_movement", "-20"),
    ("inventory_movement", "-10"),
    ("prepaid_other_assets_movement", "-5"),
    ("payables_movement", "15"),
    ("accrued_current_liabilities_movement", "20"),
    ("other_long_term_liabilities_movement", "3"),
    ("reported_cfo", "430"),
)
_DATES = {
    "2026-01-01": "2026-01-26",
    "2026-06-30": "2026-07-26",
    "2025-12-31": "2026-01-25",
    "2026-07-01": "2026-07-27",
    "2026-09-30": "2026-10-25",
    "2026-10-01": "2026-10-26",
    "2026-12-31": "2027-01-31",
}


def _redate(value):
    if isinstance(value, dict):
        return {key: _redate(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redate(item) for item in value]
    return _DATES.get(value, value) if isinstance(value, str) else value


def _source_bundle(tmp_path, *, label_override=None, value_override=None,
                   cell_override=None, net_income_value="410"):
    """A manifest-bound source with NVDA IDs and fully synthetic financial rows."""

    snapshot, case, operating, cashflow = bridge_setup(tmp_path / "base", reviewed=True)
    snapshot = EvidenceSnapshot.model_validate(_redate(snapshot.model_dump(mode="json")))
    case = type(case).model_validate(_redate(case.model_dump(mode="json")))
    operating = OperatingScenarioPackage.model_validate(_redate(operating.model_dump(mode="json")))
    cashflow = CashFlowBridgePackage.model_validate(_redate(cashflow.model_dump(mode="json")))
    original_source = snapshot.sources[0]
    content = _TABLE_HEADER
    rows = []
    for role, default_signed in _SOURCE_ROWS:
        signed = (value_override or {}).get(role, default_signed)
        cell = f"({abs(int(signed)):,})" if signed.startswith("-") else f"{int(signed):,}"
        cell = (cell_override or {}).get(role, cell)
        label = (label_override or {}).get(role, _ROW_LABELS[role])
        line = f"{label}  {cell}  1"
        start = len(content)
        content += line + "\n"
        rows.append({
            "role": role, "start": start, "end": start + len(line),
            "text": line, "signed_value_millions": int(signed),
        })
    outside_row = rows[0]["text"]
    content += f"Cash flows from investing activities:\n{outside_row}\n"
    filing = original_source.model_copy(update={
        "id": "nvda-q2-filing", "content": content,
        "content_sha256": sha256(content.encode()).hexdigest(),
        "url": "https://example.test/synthetic-nvda-filing",
    })
    release_content = "Synthetic matching H1 release cash-flow rows"
    release = original_source.model_copy(update={
        "id": "nvda-q2-release", "content": release_content,
        "content_sha256": sha256(release_content.encode()).hexdigest(),
        "url": "https://example.test/synthetic-nvda-release",
    })
    duration = {
        "source_id": release.id, "scale": "1000000", "unit": "USD", "currency": "USD",
        "basis": "US GAAP", "period_start": cashflow.historical_anchor.period_start,
        "period_end": cashflow.historical_anchor.period_end,
        "period_type": "duration", "location": "Synthetic source row",
    }
    reused = (
        FinancialFact(id="nvda-net_income-h1", metric="net_income", value=net_income_value, **duration),
        FinancialFact(id="nvda-stock_based_compensation-h1", metric="stock_based_compensation",
                      value="10", **duration),
        FinancialFact(id="nvda-asset_principal_cashflow-h1", metric="asset_principal_cashflow",
                      value="-2", **duration),
        FinancialFact(id="nvda-issuer_free_cash_flow-h1", metric="issuer_free_cash_flow",
                      value="398", **{**duration, "basis": "issuer non-GAAP FCF"}),
    )
    original_facts = tuple(
        fact.model_copy(update={"source_id": filing.id, "scale": Decimal("1000000")})
        if fact.id == cashflow.historical_anchor.depreciation_amortization_fact_id
        else fact.model_copy(update={"source_id": release.id, "scale": Decimal("1000000")})
        if fact.id in {
            cashflow.historical_anchor.operating_cash_flow_fact_id,
            cashflow.historical_anchor.capex_cashflow_fact_id,
        }
        else fact.model_copy(update={"scale": Decimal("1000000")})
        if fact.id in {"cash-pretax", "cash-tax"}
        else fact
        for fact in snapshot.facts
    )
    snapshot = EvidenceSnapshot.model_validate({
        **snapshot.model_dump(mode="json"),
        "sources": (*snapshot.sources, release, filing),
        "facts": (*original_facts, *reused),
    })
    evidence_hash = evidence_snapshot_sha256(snapshot)
    case = case.model_copy(update={"snapshot_sha256": evidence_hash})
    case_hash = digest(case)
    operating = operating.model_copy(update={
        "case_sha256": case_hash, "evidence_sha256": evidence_hash, "review": None,
    })
    operating = OperatingScenarioPackage.model_validate({
        **operating.model_dump(mode="json"),
        "review": {
            "reviewer_id": "independent_operating_reviewer",
            "reviewed_at": "2026-09-20T00:00:00Z",
            "package_sha256": operating_scenario_package_sha256(operating),
            "case_sha256": case_hash, "evidence_sha256": evidence_hash,
            "decision": "conditional_operating_scenarios",
            "limitations": ["Synthetic mechanics only."],
        },
    })
    cashflow = cashflow.model_copy(update={
        "case_sha256": case_hash, "evidence_sha256": evidence_hash,
        "operating_package_sha256": operating_scenario_package_sha256(operating),
        "review": None,
    })
    cashflow = CashFlowBridgePackage.model_validate({
        **cashflow.model_dump(mode="json"),
        "review": CashFlowBridgeReview(
            reviewer_id="independent_cashflow_reviewer",
            reviewed_at="2026-09-20T00:00:00Z",
            package_sha256=cashflow_bridge_package_sha256(cashflow),
            case_sha256=case_hash, evidence_sha256=evidence_hash,
            operating_package_sha256=cashflow.operating_package_sha256,
            decision="conditional_cash_flow_bridge",
            limitations=("Synthetic mechanics only.",),
        ),
    })
    case_review = FinancialCaseReview(
        status="reviewed", reviewer_id="independent_case_reviewer",
        case_sha256=case_hash, snapshot_sha256=evidence_hash,
    )
    envelope = FinancialCaseEnvelope(
        case=case, review=case_review, operating_scenarios=operating,
        cashflow_bridge=cashflow,
    )
    source = tmp_path / "source"
    source.mkdir()
    request = ResearchRequest(
        ticker="NVDA", cutoff=snapshot.cutoff, timezone=case.timezone,
        backend="replay", output_dir=source / "run", evidence_path=source / "evidence.json",
        financial_case_path=source / "case_input.json", quality_revision="evidence-led-bounded",
        report_language="English", budget={"followup_cycles": 0},
    )
    blobs = {
        "request.json": canonical_json(request),
        "evidence.json": canonical_json(snapshot),
        "case_input.json": canonical_json(envelope),
    }
    for name, raw in blobs.items():
        (source / name).write_bytes(raw)
    (source / "manifest.json").write_bytes(canonical_json({
        "artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in blobs.items()},
    }))
    mapping = {
        "source_id": filing.id, "source_content_sha256": filing.content_sha256,
        "evidence_sha256": evidence_hash,
        "unit": "USD millions", "period_start": str(cashflow.historical_anchor.period_start),
        "period_end": str(cashflow.historical_anchor.period_end), "rows": rows,
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_bytes(canonical_json(mapping))
    return source, mapping_path, mapping


def _source_bytes(source):
    return {item.name: item.read_bytes() for item in source.iterdir()}


def _operating_bundle(tmp_path, draft, *, reviewed):
    """Rebind only the new packet's operating package to an independent review."""

    output = tmp_path / ("operating-reviewed" if reviewed else "operating-unreviewed")
    output.mkdir()
    snapshot = EvidenceSnapshot.model_validate(read_json(draft / "evidence.json"))
    envelope = FinancialCaseEnvelope.model_validate(read_json(draft / "case_input.json"))
    operating = envelope.operating_scenarios
    assert operating is not None and operating.review is None
    if reviewed:
        operating = OperatingScenarioPackage.model_validate({
            **operating.model_dump(mode="json"),
            "review": {
                "reviewer_id": "fresh_independent_operating_reviewer",
                "reviewed_at": "2026-09-20T00:00:00Z",
                "package_sha256": operating_scenario_package_sha256(operating),
                "case_sha256": operating.case_sha256,
                "evidence_sha256": operating.evidence_sha256,
                "decision": "conditional_operating_scenarios",
                "limitations": ["Synthetic source and arithmetic review only."],
            },
        })
    envelope = envelope.model_copy(update={"operating_scenarios": operating})
    request = ResearchRequest.model_validate(read_json(draft / "request.json"))
    request = request.model_copy(update={
        "output_dir": output / "run", "evidence_path": output / "evidence.json",
        "financial_case_path": output / "case_input.json",
    })
    context = load_case_context(canonical_json(envelope), request, snapshot)
    blobs = {
        "request.json": canonical_json(request),
        "evidence.json": canonical_json(snapshot),
        "case_input.json": canonical_json(envelope),
        **context.artifacts,
    }
    for name, raw in blobs.items():
        (output / name).write_bytes(raw)
    (output / "manifest.json").write_bytes(canonical_json({
        "artifact_hashes": {name: sha256(raw).hexdigest() for name, raw in blobs.items()},
    }))
    return output


def test_prepares_exact_nine_facts_and_pending_selector_without_transferring_reviews(tmp_path):
    source, mapping_path, mapping = _source_bundle(tmp_path)
    before = _source_bytes(source)
    output = tmp_path / "new-packet"
    status = prepare_packet(source, mapping_path, output)

    assert status == {"output": str(output), "new_facts": 9, "live_calls": 0}
    assert _source_bytes(source) == before
    original = EvidenceSnapshot.model_validate(read_json(source / "evidence.json"))
    snapshot = EvidenceSnapshot.model_validate(read_json(output / "evidence.json"))
    assert snapshot.sources == original.sources
    assert snapshot.facts[:len(original.facts)] == original.facts
    added = snapshot.facts[len(original.facts):]
    assert {fact.metric for fact in added} == set(_NEW_ROLES)
    for fact in added:
        row = next(item for item in mapping["rows"] if item["role"] == fact.metric)
        assert fact.id == f"nvda-cfo-{fact.metric}-h1-fy27"
        assert fact.source_id == mapping["source_id"]
        assert fact.value == row["signed_value_millions"]
        assert fact.scale == 1_000_000
        assert (fact.period_start, fact.period_end) == (
            date(2026, 1, 26), date(2026, 7, 26)
        )
        assert fact.unit == fact.currency == "USD" and fact.basis == "US GAAP"
        assert f"chars=[{row['start']},{row['end']})" in fact.location
        assert repr(row["text"]) in fact.location

    old = FinancialCaseEnvelope.model_validate(read_json(source / "case_input.json"))
    new = FinancialCaseEnvelope.model_validate(read_json(output / "case_input.json"))
    assert old.review and old.operating_scenarios.review and old.cashflow_bridge.review
    assert "historical_reconciliation" not in read_json(source / "case_input.json")["cashflow_bridge"]
    assert "historical_reconciliation" not in old.cashflow_bridge.model_dump(mode="json")
    assert new.review is None and new.cashflow_bridge is None
    assert new.operating_scenarios.review is None
    assert new.case.snapshot_sha256 == evidence_snapshot_sha256(snapshot)
    pending = CashFlowBridgePackage.model_validate(read_json(output / "pending_cashflow_package.json"))
    assert pending.review is None
    assert pending.case_sha256 == digest(new.case)
    assert pending.evidence_sha256 == evidence_snapshot_sha256(snapshot)
    assert pending.operating_package_sha256 == operating_scenario_package_sha256(
        new.operating_scenarios
    )
    assert {row.role for row in pending.historical_reconciliation.rows} == (
        set(_NEW_ROLES) | {"net_income", "stock_compensation"}
    )
    assert read_json(output / "provenance.json")["historical_reviews_transferred"] is False
    manifest = read_json(output / "manifest.json")
    assert all(sha256((output / name).read_bytes()).hexdigest() == expected
               for name, expected in manifest["artifact_hashes"].items())


def test_refuses_overwrite_nested_destination_and_mismatched_source_manifest(tmp_path):
    source, mapping_path, _ = _source_bundle(tmp_path)
    output = tmp_path / "new-packet"
    prepare_packet(source, mapping_path, output)
    before = _source_bytes(output)
    with pytest.raises(ValueError, match="output must be fresh"):
        prepare_packet(source, mapping_path, output)
    assert _source_bytes(output) == before
    with pytest.raises(ValueError, match="outside the source"):
        prepare_packet(source, mapping_path, source / "nested")
    assert not (source / "nested").exists()
    (source / "evidence.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source artifact hash mismatch"):
        prepare_packet(source, mapping_path, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("change, message", [
    ("evidence_hash", "different evidence snapshot"),
    ("source_hash", "identity or unit differs"),
    ("unit", "identity or unit differs"),
    ("duplicate_role", "complete unique statement rows"),
    ("missing_role", "complete unique statement rows"),
    ("excerpt", "excerpt differs"),
    ("number", "number differs"),
    ("period", "statement dates or header differ"),
    ("outside_table", "excerpt differs"),
])
def test_tampered_mapping_cannot_publish(tmp_path, change, message):
    source, mapping_path, mapping = _source_bundle(tmp_path)
    if change == "evidence_hash":
        mapping["evidence_sha256"] = "0" * 64
    elif change == "source_hash":
        mapping["source_content_sha256"] = "0" * 64
    elif change == "unit":
        mapping["unit"] = "EUR millions"
    elif change == "duplicate_role":
        mapping["rows"][-1]["role"] = "net_income"
    elif change == "missing_role":
        mapping["rows"].pop()
    elif change == "excerpt":
        mapping["rows"][0]["text"] = "Different source line 410 1"
    elif change == "number":
        mapping["rows"][0]["signed_value_millions"] = 999
    elif change == "period":
        mapping["period_end"] = "2026-07-27"
    elif change == "outside_table":
        row = mapping["rows"][0]
        content = EvidenceSnapshot.model_validate(read_json(source / "evidence.json")).sources[-1].content
        start = content.rindex(row["text"])
        row["start"], row["end"] = start, start + len(row["text"])
    mapping_path.write_bytes(canonical_json(mapping))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match=message):
        prepare_packet(source, mapping_path, output)
    assert not output.exists()


def test_exact_foreign_label_inside_statement_is_rejected(tmp_path):
    source, mapping_path, _ = _source_bundle(
        tmp_path, label_override={"deferred_tax": "Cash proceeds from investments"}
    )
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="role differs from the reported row label"):
        prepare_packet(source, mapping_path, output)
    assert not output.exists()


@pytest.mark.parametrize("role,value,prefix,claimed", [
    ("inventory_movement", "-10204", "Inventories  (10", -10),
    ("payables_movement", "4125", "Accounts payable  4", 4),
    ("inventory_movement", "-10204", "Inventories  (10,204)", -10204),
])
def test_truncated_statement_rows_cannot_publish(tmp_path, role, value, prefix, claimed):
    source, mapping_path, mapping = _source_bundle(tmp_path, value_override={role: value})
    before = _source_bytes(source)
    row = next(item for item in mapping["rows"] if item["role"] == role)
    assert row["text"].startswith(prefix)
    row.update(text=prefix, end=row["start"] + len(prefix), signed_value_millions=claimed)
    mapping_path.write_bytes(canonical_json(mapping))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="complete statement row"):
        prepare_packet(source, mapping_path, output)
    assert not output.exists() and _source_bytes(source) == before


@pytest.mark.parametrize("cell", ["(10", "-10)", "(1,0)", "(10).5"])
def test_malformed_complete_numeric_cells_cannot_publish(tmp_path, cell):
    source, mapping_path, _ = _source_bundle(tmp_path, cell_override={"inventory_movement": cell})
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="two complete integer cells"):
        prepare_packet(source, mapping_path, output)
    assert not output.exists()


@pytest.mark.parametrize("precision,fact_value,row_value", [
    (2, "410", "411"),
    (28, "410.000000000000000000000000001", "410"),
])
def test_reused_row_comparison_is_exact_under_decimal_rounding(
    tmp_path, precision, fact_value, row_value
):
    source, mapping_path, _ = _source_bundle(
        tmp_path, net_income_value=fact_value, value_override={"net_income": row_value}
    )
    output = tmp_path / "rejected"
    with localcontext() as context:
        context.prec = precision
        with pytest.raises(ValueError, match="reused statement row differs from existing anchor fact"):
            prepare_packet(source, mapping_path, output)
    assert not output.exists()


@pytest.mark.parametrize("role,conflicting_value", [
    ("net_income", "411"),
    ("stock_compensation", "11"),
    ("depreciation_amortization", "21"),
    ("reported_cfo", "431"),
])
def test_reused_statement_rows_must_match_existing_anchor_facts(
    tmp_path, role, conflicting_value
):
    source, mapping_path, _ = _source_bundle(
        tmp_path, value_override={role: conflicting_value}
    )
    output = tmp_path / "rejected"
    with pytest.raises(
        ValueError, match="source mapping reused statement row differs from existing anchor fact"
    ):
        prepare_packet(source, mapping_path, output)
    assert not output.exists()


def test_pending_cashflow_handoff_after_fresh_operating_review(tmp_path):
    source, mapping_path, _ = _source_bundle(tmp_path)
    draft = tmp_path / "packet-draft"
    prepare_packet(source, mapping_path, draft)
    operating_source = _operating_bundle(tmp_path, draft, reviewed=True)
    source_before, draft_before = _source_bytes(operating_source), _source_bytes(draft)

    output = tmp_path / "cashflow-review-draft"
    status = prepare_pending_cashflow(operating_source, draft, output)

    assert status == {"output": str(output), "reviewed": False, "model_calls": 0}
    assert _source_bytes(operating_source) == source_before
    assert _source_bytes(draft) == draft_before
    package = CashFlowBridgePackage.model_validate(read_json(output / "cashflow_bridge_package.json"))
    assert package.review is None and package.historical_reconciliation is not None
    result = read_json(output / "cashflow_bridge_result.json")
    assert result["reviewed"] is False
    assert result["historical_anchor"]["reconciliation"]["status"] == (
        "mechanically_attributed_not_economic_approval"
    )
    assert "cashflow_bridge_calculated_values.json" not in _source_bytes(output)
    provenance = read_json(output / "provenance.json")
    assert provenance["independent_review_claimed"] is False
    assert provenance["model_calls"] == 0
    assert set(provenance["source_artifact_sha256"]) == {
        "evidence.json", "financial_case.json",
        "operating_scenario_package.json", "operating_scenario_context.json",
    }
    assert all(
        sha256((operating_source / name).read_bytes()).hexdigest() == value
        for name, value in provenance["source_artifact_sha256"].items()
    )
    manifest = read_json(output / "manifest.json")
    assert manifest["status"] == "draft_requires_fresh_cashflow_review"
    assert all(sha256((output / name).read_bytes()).hexdigest() == expected
               for name, expected in manifest["artifact_hashes"].items())

    review = CashFlowBridgeReview(
        reviewer_id="fresh_independent_cashflow_reviewer",
        reviewed_at="2026-09-20T00:00:00Z",
        package_sha256=cashflow_bridge_package_sha256(package),
        case_sha256=package.case_sha256,
        evidence_sha256=package.evidence_sha256,
        operating_package_sha256=package.operating_package_sha256,
        decision="conditional_cash_flow_bridge",
        limitations=("Synthetic source arithmetic only, not economic approval.",),
    )
    review_path = tmp_path / "synthetic-review.json"
    review_path.write_bytes(canonical_json(review))
    adapter_output = tmp_path / "adapter-reviewed"
    adapter_status = attach_cashflow_review(
        operating_source, output, review_path, adapter_output
    )
    assert adapter_status["cashflow_reviewed"] is True
    assert read_json(adapter_output / "cashflow_bridge_context.json")["reviewed"] is True


def test_pending_cashflow_requires_fresh_operating_review(tmp_path):
    source, mapping_path, _ = _source_bundle(tmp_path)
    draft = tmp_path / "packet-draft"
    prepare_packet(source, mapping_path, draft)
    operating_source = _operating_bundle(tmp_path, draft, reviewed=False)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="fresh operating review is required"):
        prepare_pending_cashflow(operating_source, draft, output)
    assert not output.exists()


def test_pending_cashflow_rejects_stale_package_hash(tmp_path):
    source, mapping_path, _ = _source_bundle(tmp_path)
    draft = tmp_path / "packet-draft"
    prepare_packet(source, mapping_path, draft)
    operating_source = _operating_bundle(tmp_path, draft, reviewed=True)
    pending_path = draft / "pending_cashflow_package.json"
    pending = read_json(pending_path)
    pending["operating_package_sha256"] = "0" * 64
    raw = canonical_json(pending)
    pending_path.write_bytes(raw)
    manifest = read_json(draft / "manifest.json")
    manifest["artifact_hashes"][pending_path.name] = sha256(raw).hexdigest()
    (draft / "manifest.json").write_bytes(canonical_json(manifest))

    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="operating-package binding is mismatched"):
        prepare_pending_cashflow(operating_source, draft, output)
    assert not output.exists()
