"""Offline regressions for exact, evidence-bound compound obligations."""

import json
from copy import deepcopy

import pytest

from tests.test_research_operating_scenarios import _reviewed
from tradingagents.research.operating_scenarios import evaluate_operating_scenarios
from tradingagents.research.report_review import (
    ReaderVerification,
    fan_in_compound_dispositions,
    limitation_packet,
    validated_disposition_ids,
)
from tradingagents.research.review_lifecycle import (
    LifecycleVerification,
    compound_coverage_issues,
    compound_obligation_contract_valid,
    compound_obligation_evidence_valid,
    reconcile_review,
    split_compound_obligations,
)
from tradingagents.research.storage import canonical_json

SCENARIO_AND_VALUATION = (
    "Scenario and valuation review: the payload contains no scenario schedules, "
    "code-calculated scenario bindings, independent review outputs, verified market quote "
    "or consensus vintage. The retained market passages also omit the numerical inputs "
    "required to reconstruct the described discount-rate approach."
)
FISCAL_SCENARIO_AND_EQUITY = (
    "Can the actual fiscal-year scenario schedules, calculation bindings and independent "
    "reviews be supplied together with a reconciled cutoff-date equity bridge?"
)
REVIEW_BOUNDARY = (
    "This review used the supplied frozen sources offline and verifies their internal "
    "fidelity, not independent online authentication or completeness of subsequent "
    "information. Exact arithmetic does not validate the economic likelihood of the "
    "assumptions. Changed package, case or evidence content requires fresh review and "
    "matching hashes."
)


def _actual_evaluated_catalog():
    package, case, snapshot = _reviewed()
    result = evaluate_operating_scenarios(package, case, snapshot)
    review = {
        key: result.model_context[key]
        for key in (
            "context_kind", "reviewed", "decision", "package_sha256", "case_sha256",
            "evidence_sha256",
        )
    }
    evidence = {
        "review:operating_scenarios": canonical_json(review).decode("utf-8"),
        **{
            "calculation:" + value.id: canonical_json(value).decode("utf-8")
            for value in result.calculated_values
        },
    }
    return evidence, result


def _components(issue):
    return {
        component["name"]: component
        for component in issue["compound_obligation"]["components"]
    }


def test_exact_legacy_gaps_split_without_rewriting_parent_or_asserting_residual_absence():
    evidence, result = _actual_evaluated_catalog()
    # This is the real evaluator contract: period revenue is an assumption,
    # while period operating income and fiscal totals are derived calculations.
    values = {value.id: value for value in result.calculated_values}
    assert values["operating_scenario.base.q3.revenue"].classification == (
        "operating_scenario_assumption_not_reported_fact"
    )
    assert values["operating_scenario.base.q3.operating_income"].classification == (
        "conditional_operating_scenario_calculation_not_reported_fact"
    )

    raw = limitation_packet([SCENARIO_AND_VALUATION, FISCAL_SCENARIO_AND_EQUITY])
    before = deepcopy(raw)
    split = split_compound_obligations(raw, evidence)

    assert raw == before
    assert [(item["issue_id"], item["text"]) for item in split] == [
        (item["issue_id"], item["text"]) for item in before
    ]
    assert all(compound_obligation_contract_valid(item) for item in split)
    assert all(compound_obligation_evidence_valid(item, evidence) for item in split)
    assert all(item["resolution_protected"] for item in split)
    assert all(item["reader_coverage_required"] for item in split)

    for item in split:
        components = _components(item)
        satisfied = components["reviewed_conditional_operating_package"]
        assert satisfied["status"] == "satisfied_current_evidence"
        assert satisfied["scope"] == "conditional_operating_scenarios_only"
        current_texts = [
            component["text"] for component in components.values()
            if component["status"] != "satisfied_current_evidence"
        ]
        assert all("Whether " in text or "not actual fiscal-year" in text for text in current_texts)
        assert all("remain absent" not in text for text in current_texts)
        binding = item["compound_obligation"]["evidence_bindings"][0]
        for witness in binding["witnesses"]:
            assert witness["excerpt"] in evidence[witness["reference"]]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text + " ",
        lambda text: text.replace("Scenario and valuation review", "Scenario/valuation review"),
        lambda text: text.replace("no scenario schedules", "no scenario schedule"),
    ],
)
def test_similar_or_normalized_prose_is_not_split_by_broad_matching(mutation):
    evidence, _ = _actual_evaluated_catalog()
    issue = limitation_packet([mutation(SCENARIO_AND_VALUATION)])[0]

    assert split_compound_obligations([issue], evidence) == [issue]


@pytest.mark.parametrize("mutation", ["unreviewed", "missing_output", "wrong_class", "wrong_hash"])
def test_operating_component_requires_complete_exact_current_evidence(mutation):
    evidence, _ = _actual_evaluated_catalog()
    evidence = deepcopy(evidence)
    if mutation == "unreviewed":
        review = canonical_json({
            "context_kind": "operating_scenario_audit_only",
            "reviewed": False,
        }).decode("utf-8")
        evidence["review:operating_scenarios"] = review
    else:
        reference = "calculation:operating_scenario.base.q3.revenue"
        if mutation == "missing_output":
            del evidence[reference]
        else:
            record = json.loads(evidence[reference])
            if mutation == "wrong_class":
                record["classification"] = "conditional_operating_scenario_calculation_not_reported_fact"
            else:
                record["model_input_sha256"] = "0" * 64
            evidence[reference] = canonical_json(record).decode("utf-8")
    issue = limitation_packet([SCENARIO_AND_VALUATION])[0]

    assert split_compound_obligations([issue], evidence) == [issue]


def test_parent_cannot_be_retired_even_with_exact_operating_and_reader_witnesses():
    evidence, _ = _actual_evaluated_catalog()
    parent = split_compound_obligations(
        limitation_packet([SCENARIO_AND_VALUATION]), evidence
    )[0]
    reader = (
        "Whether current market and valuation inputs are established remains unresolved. "
        "The review covers conditional operating arithmetic, not actual results or valuation."
    )
    review_record = evidence["review:operating_scenarios"]
    review = LifecycleVerification(reviewed_report=True, issue_resolutions=[{
        "issue_id": parent["issue_id"],
        "status": "superseded",
        "rationale": "Attempted broad retirement.",
        "reader_excerpts": [reader],
        "witnesses": [{
            "reference": "review:operating_scenarios",
            "excerpt": review_record,
        }],
    }])

    active, remaining, ledger = reconcile_review(review, [parent], evidence, reader, None)

    assert remaining == [parent]
    assert ledger["retired_issue_ids"] == []
    assert ledger["issues"][0]["issue_id"] == parent["issue_id"]
    assert ledger["issues"][0]["text"] == SCENARIO_AND_VALUATION
    assert ledger["issues"][0]["status"] == "open"
    assert any("protected" in finding.message for finding in active.findings)


def test_existing_financial_and_security_protections_survive_the_split():
    evidence, _ = _actual_evaluated_catalog()
    issue = limitation_packet([FISCAL_SCENARIO_AND_EQUITY])[0]
    issue.update({
        "resolution_protected": True,
        "resolution_protection_reasons": ("financial_finding", "security_finding"),
        "reader_coverage_required": True,
    })

    split = split_compound_obligations([issue], evidence)[0]

    assert split["resolution_protection_reasons"] == (
        "financial_finding",
        "security_finding",
        "compound_obligation_has_current_or_open_components",
    )
    assert split["resolution_protected"] and split["reader_coverage_required"]


def _boundary_parent_and_children():
    parent = limitation_packet([REVIEW_BOUNDARY])[0]
    parent.update({
        "resolution_protected": True,
        "resolution_protection_reasons": ("nonretirable_origin",),
        "reader_coverage_required": True,
    })
    parent = split_compound_obligations([parent], {})[0]
    return parent, compound_coverage_issues([parent])


def _complete_child_review(children, reader):
    dispositions = []
    for child in children:
        treatment = child["coverage_component"]["reader_treatment"]
        if treatment == "reader_required":
            dispositions.append({
                "issue_id": child["issue_id"],
                "decision": "reader_covered",
                "rationale": "This exact material boundary is visible.",
                "reader_excerpt": child["text"],
            })
        else:
            dispositions.append({
                "issue_id": child["issue_id"],
                "decision": "audit_only_operational",
                "rationale": "This is the immutable changed-content re-review control.",
            })
    return ReaderVerification(reviewed_report=True, limitation_dispositions=dispositions)


def test_material_review_boundaries_and_procedural_control_require_atomic_decisions():
    parent, children = _boundary_parent_and_children()
    components = {child["coverage_component"]["name"]: child for child in children}
    assert len(children) == 3
    assert components["frozen_offline_source_boundary"]["reader_coverage_required"]
    assert components["arithmetic_not_likelihood_boundary"]["reader_coverage_required"]
    assert not components["changed_content_re_review_rule"]["reader_coverage_required"]

    reader = "\n".join(
        child["text"] for child in children if child["reader_coverage_required"]
    )
    raw_review = _complete_child_review(children, reader)
    combined = fan_in_compound_dispositions(raw_review, [parent], reader)

    assert validated_disposition_ids(combined, [parent], reader) == (parent["issue_id"],)
    disposition = combined.limitation_dispositions[0]
    assert disposition.issue_id == parent["issue_id"]
    assert len(disposition.reader_excerpts) == 2
    assert components["changed_content_re_review_rule"]["text"] not in disposition.reader_excerpts


@pytest.mark.parametrize("mutation", ["generic_parent", "missing_child", "approximate_quote", "immaterial_procedure"])
def test_generic_or_inexact_coverage_cannot_satisfy_compound_parent(mutation):
    parent, children = _boundary_parent_and_children()
    reader = "\n".join(
        child["text"] for child in children if child["reader_coverage_required"]
    )
    if mutation == "generic_parent":
        review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
            "issue_id": parent["issue_id"],
            "decision": "reader_covered",
            "rationale": "Generic parent assertion.",
            "reader_excerpt": reader,
        }])
    else:
        payload = _complete_child_review(children, reader).model_dump(mode="json")
        if mutation == "missing_child":
            payload["limitation_dispositions"].pop()
        elif mutation == "approximate_quote":
            payload["limitation_dispositions"][0]["reader_excerpt"] += " approximately"
        else:
            payload["limitation_dispositions"][-1]["decision"] = "audit_only_immaterial"
        review = ReaderVerification.model_validate(payload)

    combined = fan_in_compound_dispositions(review, [parent], reader)

    assert validated_disposition_ids(combined, [parent], reader) == ()
    assert any(finding.severity == "critical" for finding in combined.findings)


def test_tampered_contract_fails_closed_and_generic_unmatched_issue_remains_open():
    evidence, _ = _actual_evaluated_catalog()
    parent = split_compound_obligations(
        limitation_packet([SCENARIO_AND_VALUATION]), evidence
    )[0]
    parent["compound_obligation"]["components"][0]["scope"] = "valuation_cleared"
    generic = limitation_packet(["Can all current inputs and reviews be supplied?"])[0]

    assert not compound_obligation_contract_valid(parent)
    assert compound_coverage_issues([parent]) == [parent]
    assert split_compound_obligations([generic], evidence) == [generic]
    active, remaining, ledger = reconcile_review(
        LifecycleVerification(reviewed_report=True), [parent, generic], evidence, "reader", None
    )
    assert remaining == [parent, generic]
    assert ledger["retired_issue_ids"] == []
    assert any("Compound obligation contract" in finding.message for finding in active.findings)
