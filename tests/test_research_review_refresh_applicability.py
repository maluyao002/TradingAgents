"""Opt-in dependent-review applicability; no live calls or historical fixtures."""

import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.test_research_cashflow_bridge import bridge_setup
from tradingagents.research import review_lifecycle
from tradingagents.research.cashflow_bridge import evaluate_cashflow_bridge
from tradingagents.research.operating_scenarios import evaluate_operating_scenarios
from tradingagents.research.report_review import (
    ReaderVerification,
    fan_in_compound_dispositions,
    limitation_packet,
    validated_disposition_ids,
)
from tradingagents.research.review_lifecycle import (
    _DEPENDENT_REVIEW_REFRESH,
    EVIDENCE_REFERENCE_CONTRACT,
    LifecycleVerification,
    compound_coverage_issues,
    compound_obligation_contract_valid,
    compound_obligation_evidence_valid,
    evidence_catalog,
    reconcile_review,
    resolution_witness_contract,
    split_compound_obligations,
)
from tradingagents.research.storage import canonical_json, digest


@pytest.fixture
def evaluated(tmp_path):
    snapshot, case, operating_package, bridge = bridge_setup(tmp_path, reviewed=True)
    operating = evaluate_operating_scenarios(operating_package, case, snapshot)
    cashflow = evaluate_cashflow_bridge(bridge, case, snapshot, operating)
    context = SimpleNamespace(source_passages=(), operating_scenarios=operating,
                              cashflow_bridge=cashflow)
    values = (*operating.calculated_values, *cashflow.calculated_values)
    return snapshot, values, context


def _catalog(evaluated, **kwargs):
    snapshot, values, context = evaluated
    return evidence_catalog(snapshot, values, case_context=context, **kwargs)


def _issue():
    issue = limitation_packet([_DEPENDENT_REVIEW_REFRESH])[0]
    assert issue["issue_id"] == (
        "limitation-3cd60a8072ca288cdc65fe99a0d095e2b9e90f91b2f6234f6f420629360e7a50")
    issue.update(reader_coverage_required=True, resolution_protected=True,
                 resolution_protection_reasons=("nonretirable_origin",),
                 origins=[{"origin_id": "operating.review.limitation.5", "retirable": False}],
                 claims=[], missing_claim_ids=[], prior_findings=[])
    return issue


def _split(catalog, issue=None):
    return split_compound_obligations([issue or _issue()], catalog, verification_repair=True)[0]


def test_old_catalog_and_split_remain_byte_compatible(evaluated):
    snapshot, values, context = evaluated
    expected = {"fact:" + f.id: canonical_json(f).decode() for f in snapshot.facts}
    expected.update({"calculation:" + v.id: canonical_json(v).decode() for v in values})
    expected["review:operating_scenarios"] = canonical_json({
        key: context.operating_scenarios.model_context[key] for key in (
            "context_kind", "reviewed", "decision", "package_sha256", "case_sha256", "evidence_sha256",
        )}).decode()
    legacy = _catalog(evaluated)
    assert canonical_json(legacy) == canonical_json(expected)
    assert _catalog(evaluated, verification_repair=False) == legacy
    assert resolution_witness_contract(legacy, "Reader")["catalog_key_formats"] == EVIDENCE_REFERENCE_CONTRACT
    original = _issue()
    opted_in = _catalog(evaluated, verification_repair=True)
    assert "review:cashflow_bridge" in opted_in and "review:cashflow_bridge" not in legacy
    assert split_compound_obligations([original], opted_in) == [original]
    assert split_compound_obligations([original], opted_in, reader_revision=True) == [original]
    assert _split(legacy) == original  # Both opt-ins are necessary.


def test_split_preserves_history_and_binds_only_current_review(evaluated):
    catalog = _catalog(evaluated, verification_repair=True)
    original = _issue()
    before = deepcopy(original)
    parent = _split(catalog, original)
    assert original == before
    assert parent["issue_id"] == before["issue_id"] and parent["text"] == before["text"]
    assert parent["origins"] == before["origins"]
    assert parent["resolution_protected"] and not parent["claim_change_eligible"]
    assert parent["reader_coverage_required"]
    assert compound_obligation_contract_valid(parent)
    assert compound_obligation_evidence_valid(parent, catalog)
    components = parent["compound_obligation"]["components"]
    assert [c["reader_treatment"] for c in components] == [
        "audit_only_satisfied", "audit_only_procedural", "reader_required"]
    assert components[0]["scope"] == "conditional_cashflow_review_only"
    assert "not an attestation" in components[1]["text"]
    binding = parent["compound_obligation"]["evidence_bindings"][0]
    for witness in binding["witnesses"]:
        assert witness["excerpt"] in catalog[witness["reference"]]
    assert "review:cashflow_bridge" in resolution_witness_contract(catalog, "Reader")["catalog_key_formats"]


@pytest.mark.parametrize("suffix", ["Z", "+00:00"])
def test_aware_review_dates_support_python310(evaluated, monkeypatch, suffix):
    catalog = _catalog(evaluated, verification_repair=True)
    for reference in ("review:operating_scenarios", "review:cashflow_bridge"):
        record = json.loads(catalog[reference])
        record["reviewed_at"] = "2026-09-23T16:49:01" + suffix
        catalog[reference] = canonical_json(record).decode()

    def python310_fromisoformat(value):
        if value.endswith("Z"):
            raise ValueError("Python 3.10 rejects terminal Z")
        return datetime.fromisoformat(value)

    monkeypatch.setattr(review_lifecycle, "datetime",
                        SimpleNamespace(fromisoformat=python310_fromisoformat))
    parent = _split(catalog)
    assert "compound_obligation" in parent
    assert compound_obligation_evidence_valid(parent, catalog)


@pytest.mark.parametrize("mutation", [
    "no_cash_review", "unreviewed", "wrong_case", "wrong_evidence", "wrong_operating",
    "wrong_operating_review", "predates_operating", "naive_date", "missing_calculation",
    "wrong_calculation_package", "wrong_calculation_result", "shrunk_catalog", "bad_json",
])
def test_missing_or_inconsistent_dependent_evidence_cannot_split(evaluated, mutation):
    catalog = _catalog(evaluated, verification_repair=True)
    reference = "review:cashflow_bridge"
    record = json.loads(catalog[reference])
    if mutation == "no_cash_review":
        del catalog[reference]
    elif mutation == "bad_json":
        catalog[reference] = "[]"
    elif mutation.startswith("wrong_calculation") or mutation == "missing_calculation":
        key = "calculation:cashflow_bridge.historical.bridge_cash_flow"
        if mutation == "missing_calculation":
            del catalog[key]
        else:
            value = json.loads(catalog[key])
            value["model_input_sha256" if mutation.endswith("package") else "model_result_sha256"] = "0" * 64
            catalog[key] = canonical_json(value).decode()
    else:
        updates = {
            "unreviewed": ("reviewed", False),
            "wrong_case": ("case_sha256", "0" * 64),
            "wrong_evidence": ("evidence_sha256", "0" * 64),
            "wrong_operating": ("operating_package_sha256", "0" * 64),
            "wrong_operating_review": ("operating_review_sha256", "0" * 64),
            "predates_operating": ("reviewed_at", "2020-01-01T00:00:00Z"),
            "naive_date": ("reviewed_at", "2026-09-20T00:00:00"),
            "shrunk_catalog": ("calculated_value_ids", ["cashflow_bridge.historical.bridge_cash_flow"]),
        }
        key, value = updates[mutation]
        record[key] = value
        catalog[reference] = canonical_json(record).decode()
    assert _split(catalog) == _issue()


def test_unreviewed_cashflow_produces_no_review_witness(evaluated):
    snapshot, values, context = evaluated
    context.cashflow_bridge = SimpleNamespace(reviewed=False)
    catalog = evidence_catalog(snapshot, values, case_context=context, verification_repair=True)
    assert "review:cashflow_bridge" not in catalog
    assert _split(catalog) == _issue()


@pytest.mark.parametrize("change", ["suffix", "source_quality", "security", "numerical"])
def test_exact_match_and_protected_findings_fail_closed(evaluated, change):
    issue = _issue()
    if change in {"suffix", "source_quality"}:
        issue["text"] += " " if change == "suffix" else " Source authenticity is unresolved."
        issue["issue_id"] = "limitation-" + digest(issue["text"])
    else:
        issue["prior_findings"] = [{"category": change, "severity": "critical"}]
    assert _split(_catalog(evaluated, verification_repair=True), issue) == issue


@pytest.mark.parametrize("bad_coverage", [False, "audit_residual", "inexact", "audit_span"])
def test_residual_needs_exact_reader_coverage_and_audit_needs_empty_spans(evaluated, bad_coverage):
    catalog = _catalog(evaluated, verification_repair=True)
    parent = _split(catalog)
    children = compound_coverage_issues([parent])
    assert len(children) == 2  # The review-status witness is code-satisfied, not model-attested.
    procedural, residual = children
    reader = residual["text"]
    dispositions = [
        {"issue_id": procedural["issue_id"], "decision": "audit_only_operational",
         "rationale": "Preserved dependency controls, not evidence that analysis was refreshed."},
        {"issue_id": residual["issue_id"], "decision": "reader_covered",
         "rationale": "Narrow reviewed scope remains explicit.", "reader_excerpt": reader},
    ]
    if bad_coverage == "audit_residual":
        dispositions[1].update(decision="audit_only_operational", reader_excerpt="")
    elif bad_coverage == "inexact":
        dispositions[1]["reader_excerpt"] = "Not in the reader"
    elif bad_coverage == "audit_span":
        dispositions[0]["reader_excerpt"] = reader
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=dispositions)
    combined = fan_in_compound_dispositions(review, [parent], reader)
    assert bool(validated_disposition_ids(combined, [parent], reader)) is (bad_coverage is False)


def test_whole_parent_retirement_and_stale_binding_are_rejected(evaluated):
    catalog = _catalog(evaluated, verification_repair=True)
    parent = _split(catalog)
    reader = "The conditional bridge review does not approve the financial schedules."
    review = LifecycleVerification(reviewed_report=True, issue_resolutions=[{
        "issue_id": parent["issue_id"], "status": "resolved", "rationale": "Must not clear the parent.",
        "reader_excerpts": [reader], "witnesses": [{"reference": "review:cashflow_bridge",
                                                   "excerpt": catalog["review:cashflow_bridge"]}],
    }])
    checked, active, ledger = reconcile_review(review, [parent], catalog, reader, None)
    assert checked.findings and active and ledger["issues"][0]["status"] == "open"
    del catalog["review:cashflow_bridge"]
    assert not compound_obligation_evidence_valid(parent, catalog)
