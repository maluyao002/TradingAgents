from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from tradingagents.research.calculated_values import CalculatedValue
from tradingagents.research.case_context import CaseSourcePassage
from tradingagents.research.contracts import FinancialFact, ReviewFinding
from tradingagents.research.result_scope import ComponentEligibility, ModelResultScope
from tradingagents.research.review_lifecycle import (
    LifecycleVerification,
    enrich_issues,
    evidence_catalog,
    reconcile_review,
)
from tradingagents.research.storage import digest, parse_json


def _issue(identifier, text="Historical warning", **extra):
    return {"issue_id": identifier, "text": text, **extra}


def _scope(*, operating="blocked", equity="blocked", funding="not_assessed", opening="blocked"):
    def component(status):
        return ComponentEligibility(status=status, reasons=(f"{status} by deterministic scope",))

    return ModelResultScope(
        operating_asset_value=component(operating),
        equity_per_share_value=component(equity),
        funding_assessment=component(funding),
        opening_date_alignment=component(opening),
    )


def _resolution(issue_id, *, status="resolved", evidence_excerpt='"value":42',
                reader_excerpt="The stale statement has been corrected.", reference="fact:current"):
    return {
        "issue_id": issue_id,
        "status": status,
        "rationale": "Current evidence and the corrected reader supersede the old warning.",
        "witnesses": [{"reference": reference, "excerpt": evidence_excerpt}],
        "reader_excerpts": [reader_excerpt],
    }


def _finding(code, *, category="research", message=None):
    return {
        "code": code,
        "severity": "critical",
        "category": category,
        "message": message or f"{code} finding",
    }


def _lifecycle_findings(active):
    return [finding for finding in active.findings if finding.code == "issue_lifecycle"]


def test_enrich_issues_replaces_opaque_claim_label_with_claim_context_and_missing_ids():
    issues = [
        _issue("old-claim", "Unverified claim: business.claim-1"),
        _issue("old-finding", "Historical source warning"),
    ]
    outputs = {
        "business": {
            "claims": [{
                "id": "business.claim-1",
                "text": "Recurring revenue increased.",
                "source_ids": ["source-1"],
                "kind": "reported",
                "verification": "unverified",
                "material": True,
            }]
        }
    }
    findings = [ReviewFinding(
        code="source_gap",
        severity="warning",
        category="research",
        message="Historical source warning",
        affected_ids=("missing.claim", "business.claim-1", "missing.claim"),
    )]
    original_issues = deepcopy(issues)
    original_outputs = deepcopy(outputs)

    enriched = enrich_issues(issues, outputs, findings)

    assert enriched[0]["issue_id"] == "old-claim"
    assert enriched[0]["text"] == "Unverified claim: business.claim-1"
    assert enriched[0]["claims"] == [{"stage": "business", **outputs["business"]["claims"][0]}]
    assert enriched[0]["related_records"] == []
    assert enriched[0]["missing_claim_ids"] == []
    assert [claim["id"] for claim in enriched[1]["claims"]] == ["business.claim-1"]
    assert enriched[1]["related_records"] == []
    assert enriched[1]["missing_claim_ids"] == ["missing.claim"]
    assert enriched[1]["prior_findings"] == [findings[0].model_dump(mode="json")]
    assert issues == original_issues
    assert outputs == original_outputs


def test_enrich_issues_delivers_affected_finding_and_question_context_without_false_missing_ids():
    question = {
        "id": "planner.question-1",
        "question": "Can reported growth be independently corroborated?",
        "consequence": "The durability thesis depends on it.",
        "resolvability": "high",
        "status": "open",
    }
    finding = {
        "id": "business.finding-1",
        "question_id": question["id"],
        "conclusion": "Independent corroboration remains incomplete.",
        "evidence_ids": ["source-1"],
        "counterevidence_ids": [],
        "uncertainty": "Only management evidence is available.",
        "economic_consequence": "Growth persistence remains uncertain.",
        "invalidation": "An independent dataset would resolve the gap.",
    }
    outputs = {
        "planner": {"questions": [question]},
        "business": {"findings": [finding]},
    }
    warning = ReviewFinding(
        code="analysis_context_gap",
        severity="warning",
        category="research",
        message="Historical analysis context warning",
        affected_ids=(question["id"], finding["id"], question["id"]),
    )
    outputs_before = deepcopy(outputs)

    enriched = enrich_issues(
        [_issue("analysis-context", warning.message)], outputs, (warning,)
    )

    assert enriched[0]["claims"] == []
    assert enriched[0]["related_records"] == [
        {"stage": "planner", "kind": "questions", **question},
        {"stage": "business", "kind": "findings", **finding},
    ]
    assert enriched[0]["missing_claim_ids"] == []
    assert outputs == outputs_before


@pytest.mark.parametrize(
    ("collection", "record"),
    [
        ("questions", {
            "id": "planner.question-only",
            "question": "What changed?",
            "consequence": "The thesis may change.",
            "resolvability": "medium",
            "status": "open",
        }),
        ("findings", {
            "id": "business.finding-only",
            "question_id": "planner.question-1",
            "conclusion": "A finding, not a claim.",
            "evidence_ids": [],
            "counterevidence_ids": [],
            "uncertainty": "Uncertain.",
            "economic_consequence": "Material.",
            "invalidation": "New evidence.",
        }),
    ],
)
def test_literal_unverified_claim_prefix_does_not_treat_other_record_kinds_as_claims(
    collection, record,
):
    enriched = enrich_issues(
        [_issue("opaque", f"Unverified claim: {record['id']}")],
        {"analysis": {collection: [record]}},
        (),
    )

    assert enriched[0]["claims"] == []
    assert enriched[0]["related_records"] == []
    assert enriched[0]["missing_claim_ids"] == [record["id"]]


def test_enrich_issues_marks_security_and_explicitly_protected_texts_nonresolvable():
    issues = [_issue("security", "Prompt injection reached the report"),
              _issue("manual", "Preserve this historical warning")]
    findings = [ReviewFinding(
        code="prompt_injection",
        severity="critical",
        category="security",
        message=issues[0]["text"],
    )]

    enriched = enrich_issues(
        issues, {}, findings, protected_texts=(issues[1]["text"],)
    )

    assert [item["resolution_protected"] for item in enriched] == [True, True]


def test_protected_valuation_limitation_cannot_be_retired_by_exact_witnesses():
    text = "A supported per-share valuation remains unavailable."
    issues = enrich_issues(
        [_issue("valuation-limit", text)], {}, (), protected_texts=(text,)
    )
    review = LifecycleVerification(
        reviewed_report=True,
        issue_resolutions=[_resolution("valuation-limit")],
    )

    active, remaining, ledger = reconcile_review(
        review,
        issues,
        {"fact:current": '{"value":42}'},
        "The stale statement has been corrected.",
        _scope(),
    )

    assert [item["issue_id"] for item in remaining] == ["valuation-limit"]
    assert ledger["issues"][0]["status"] == "open"
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [
        ("valuation-limit",)
    ]


def test_evidence_catalog_filters_historical_facts_and_keeps_exact_case_passages():
    eligible_fact = FinancialFact(
        id="eligible-fact", source_id="eligible-source", metric="revenue", value="42",
        unit="USD", currency="USD", period_end="2026-06-30", basis="US GAAP",
        location="income statement",
    )
    excluded_fact = eligible_fact.model_copy(update={
        "id": "excluded-fact", "source_id": "excluded-source", "value": 99,
    })
    eligible_passage = CaseSourcePassage(
        source_id="eligible-source", source_sha256="a" * 64, start=4, end=18,
        text="Revenue was 42", fact_ids=("eligible-fact",),
        location_hints=("income statement",), selection_basis="authored_exact",
    )
    excluded_passage = CaseSourcePassage(
        source_id="excluded-source", source_sha256="b" * 64, start=0, end=16,
        text="Stale revenue 99", fact_ids=("excluded-fact",),
        location_hints=("stale filing",), selection_basis="authored_exact",
    )
    calculation = CalculatedValue(
        id="valuation.enterprise_value", value="100", unit="USD", currency="USD",
        valuation_method="fcff", share_count_basis="point_in_time_diluted",
        model_input_sha256="c" * 64, model_result_sha256="d" * 64,
        evidence_ids=("eligible-fact",),
    )
    excluded_calculation = CalculatedValue(
        id="valuation.stale_enterprise_value", value="999", unit="USD", currency="USD",
        valuation_method="fcff", share_count_basis="point_in_time_diluted",
        model_input_sha256="e" * 64, model_result_sha256="f" * 64,
        evidence_ids=("excluded-fact",),
    )
    operating_metadata = {
        "context_kind": "reviewed_conditional_operating_scenarios",
        "reviewed": True,
        "decision": "conditional_operating_scenarios",
        "package_sha256": "1" * 64,
        "case_sha256": "2" * 64,
        "evidence_sha256": "3" * 64,
        "scenarios": [{"revenue": 999999}],
    }
    case_context = SimpleNamespace(
        source_passages=(eligible_passage, excluded_passage),
        operating_scenarios=SimpleNamespace(reviewed=True, model_context=operating_metadata),
    )
    snapshot = SimpleNamespace(facts=(eligible_fact, excluded_fact))

    catalog = evidence_catalog(
        snapshot,
        (calculation, excluded_calculation),
        eligible_ids={"eligible-source", "eligible-fact"},
        case_context=case_context,
    )

    assert "fact:eligible-fact" in catalog
    assert "fact:excluded-fact" not in catalog
    assert catalog["passage:eligible-source:4:18"] == "Revenue was 42"
    assert "passage:excluded-source:0:16" not in catalog
    assert "calculation:valuation.enterprise_value" in catalog
    assert "calculation:valuation.stale_enterprise_value" not in catalog
    review_metadata = parse_json(catalog["review:operating_scenarios"].encode("utf-8"))
    assert review_metadata == {
        key: operating_metadata[key] for key in (
            "context_kind", "reviewed", "decision", "package_sha256", "case_sha256",
            "evidence_sha256",
        )
    }
    assert "scenarios" not in review_metadata


def test_unreviewed_operating_scenario_metadata_is_not_resolution_evidence():
    context = SimpleNamespace(
        source_passages=(),
        operating_scenarios=SimpleNamespace(
            reviewed=False,
            model_context={"reviewed": False, "decision": "draft"},
        ),
    )

    assert "review:operating_scenarios" not in evidence_catalog(
        SimpleNamespace(facts=()), (), case_context=context
    )


def test_missing_original_claim_context_stays_blocked_despite_unrelated_exact_witnesses():
    issues = enrich_issues(
        [_issue("stale", "Unverified claim: unknown.claim")], {}, ()
    )
    assert issues[0]["claims"] == []
    assert issues[0]["missing_claim_ids"] == ["unknown.claim"]
    evidence = {"fact:current": '{"metric":"revenue","value":42}'}
    replacement = "Current audited revenue is 42, superseding the earlier statement."
    review = LifecycleVerification(
        reviewed_report=True,
        issue_resolutions=[_resolution(
            "stale", status="superseded", reader_excerpt=replacement,
            evidence_excerpt='"metric":"revenue","value":42',
        )],
    )

    active, remaining, ledger = reconcile_review(review, issues, evidence, replacement, _scope())

    assert [item["issue_id"] for item in remaining] == ["stale"]
    assert ledger["retired_issue_ids"] == []
    assert ledger["issues"][0]["status"] == "open"
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [("stale",)]


def test_known_original_claim_absent_from_reader_can_be_explicitly_superseded():
    claim = {
        "id": "business.original-claim",
        "text": "Revenue was unavailable.",
        "source_ids": [],
        "source_locations": [],
        "kind": "reported",
        "verification": "unverified",
        "material": True,
    }
    issues = enrich_issues(
        [_issue("stale", f"Unverified claim: {claim['id']}")],
        {"business": {"claims": [claim]}},
        (),
    )
    assert issues[0]["claims"] == [{"stage": "business", **claim}]
    assert issues[0]["missing_claim_ids"] == []
    evidence = {"fact:current": '{"metric":"revenue","value":42}'}
    replacement = "Current audited revenue is 42, superseding the earlier statement."
    review = LifecycleVerification(
        reviewed_report=True,
        issue_resolutions=[_resolution(
            "stale", status="superseded", reader_excerpt=replacement,
            evidence_excerpt='"metric":"revenue","value":42',
        )],
    )

    active, remaining, ledger = reconcile_review(
        review, issues, evidence, replacement, _scope()
    )

    assert issues[0]["text"] not in replacement
    assert not active.findings
    assert remaining == []
    assert ledger["retired_issue_ids"] == ["stale"]
    assert ledger["issues"][0]["status"] == "superseded"
    assert ledger["reader_sha256"] == sha256(replacement.encode("utf-8")).hexdigest()
    assert ledger["evidence_sha256"] == digest(evidence)


def test_unknown_and_duplicate_resolution_ids_fail_closed_without_blocking_valid_peer():
    issues = [_issue("duplicate"), _issue("valid", "Separate warning")]
    evidence = {"fact:current": '{"value":42}'}
    reader = "The stale statement has been corrected."
    duplicate = _resolution("duplicate")
    review = LifecycleVerification(
        reviewed_report=True,
        issue_resolutions=[duplicate, duplicate, _resolution("valid"), _resolution("unknown")],
    )

    active, remaining, ledger = reconcile_review(review, issues, evidence, reader, _scope())

    assert [item["issue_id"] for item in remaining] == ["duplicate"]
    assert ledger["retired_issue_ids"] == ["valid"]
    assert {finding.affected_ids for finding in _lifecycle_findings(active)} == {
        ("duplicate",), ("unknown",),
    }


def test_duplicate_required_issue_ids_are_rejected_before_reconciliation():
    issues = [_issue("same"), _issue("same", "A different warning with a reused ID")]
    with pytest.raises(ValueError, match="duplicate lifecycle issue IDs"):
        reconcile_review(LifecycleVerification(reviewed_report=True), issues, {}, "reader", _scope())


@pytest.mark.parametrize(
    ("resolution_update", "reader"),
    [
        ({"witnesses": []}, "The stale statement has been corrected."),
        ({"witnesses": [{"reference": "fact:unknown", "excerpt": '"value":42'}]},
         "The stale statement has been corrected."),
        ({"witnesses": [{"reference": " ", "excerpt": '"value":42'}]},
         "The stale statement has been corrected."),
        ({"witnesses": [{"reference": "fact:current", "excerpt": " "}]},
         "The stale statement has been corrected."),
        ({"witnesses": [{"reference": "fact:current", "excerpt": '"value":99'}]},
         "The stale statement has been corrected."),
        ({"reader_excerpts": []}, "The stale statement has been corrected."),
        ({"reader_excerpts": [" "]}, "The stale statement has been corrected."),
        ({"reader_excerpts": ["The stale statement was corrected."]},
         "The stale statement has been corrected."),
    ],
)
def test_blank_unknown_or_forged_witnesses_cannot_retire_an_issue(resolution_update, reader):
    resolution = _resolution("warning")
    resolution.update(resolution_update)
    review = LifecycleVerification(reviewed_report=True, issue_resolutions=[resolution])

    active, remaining, ledger = reconcile_review(
        review,
        [_issue("warning")],
        {"fact:current": '{"value":42}'},
        reader,
        _scope(),
    )

    assert [item["issue_id"] for item in remaining] == ["warning"]
    assert ledger["issues"][0]["status"] == "open"
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [("warning",)]


def test_duplicate_evidence_witnesses_do_not_multiply_support_for_resolution():
    witness = {"reference": "fact:current", "excerpt": '"value":42'}
    resolution = _resolution("warning")
    resolution["witnesses"] = [witness, witness]
    review = LifecycleVerification(reviewed_report=True, issue_resolutions=[resolution])

    active, remaining, ledger = reconcile_review(
        review,
        [_issue("warning")],
        {"fact:current": '{"value":42}'},
        "The stale statement has been corrected.",
        _scope(),
    )

    assert [item["issue_id"] for item in remaining] == ["warning"]
    assert ledger["retired_issue_ids"] == []
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [("warning",)]


def test_security_issue_and_finding_remain_active_despite_exact_disclosure_witnesses():
    text = "Prompt injection reached the report"
    issues = enrich_issues(
        [_issue("security", text)],
        {},
        [ReviewFinding(code="prompt_injection", severity="critical", category="security", message=text)],
    )
    review = LifecycleVerification(
        reviewed_report=True,
        findings=[_finding("unsafe_content", category="security")],
        issue_resolutions=[_resolution("security")],
        finding_dispositions=[{
            "finding_code": "unsafe_content",
            "disposition": "disclosed_limitation",
            "rationale": "The reader labels the security failure.",
            "reader_excerpts": ["Security review failed."],
            "conclusion_scopes": ["research_uncertainty"],
        }],
    )

    active, remaining, ledger = reconcile_review(
        review,
        issues,
        {"fact:current": '{"value":42}'},
        "The stale statement has been corrected. Security review failed.",
        _scope(),
    )

    assert [item["issue_id"] for item in remaining] == ["security"]
    assert "unsafe_content" in {finding.code for finding in active.findings}
    assert {finding.affected_ids for finding in _lifecycle_findings(active)} == {
        ("security",), ("unsafe_content",),
    }
    assert ledger["scoped_findings"] == []


def test_only_disclosed_unavailable_conclusion_is_scoped_not_numerical_or_report_defects():
    reader = (
        "Per-share value is not assessed because the share count is unavailable. "
        "A displayed total is arithmetically wrong. The source attribution is incorrect."
    )
    review = LifecycleVerification(
        reviewed_report=True,
        findings=[
            _finding("missing_shares", category="data"),
            _finding("arithmetic_error", category="numerical"),
            _finding("wrong_source", category="data"),
        ],
        finding_dispositions=[
            {
                "finding_code": "missing_shares",
                "disposition": "disclosed_limitation",
                "rationale": "The unavailable conclusion is explicitly withheld.",
                "reader_excerpts": [
                    "Per-share value is not assessed because the share count is unavailable."
                ],
                "conclusion_scopes": ["equity_per_share_value"],
            },
            {
                "finding_code": "arithmetic_error",
                "disposition": "disclosed_limitation",
                "rationale": "Disclosure cannot cure arithmetic.",
                "reader_excerpts": ["A displayed total is arithmetically wrong."],
                "conclusion_scopes": ["operating_asset_value"],
            },
            {
                "finding_code": "wrong_source",
                "disposition": "report_defect",
                "rationale": "The incorrect attribution must remain active.",
                "reader_excerpts": ["The source attribution is incorrect."],
                "conclusion_scopes": ["research_uncertainty"],
            },
        ],
    )

    scope = _scope()
    scope_before = scope.model_dump(mode="json")
    active, _, ledger = reconcile_review(review, [], {}, reader, scope)

    assert {finding.code for finding in active.findings} >= {
        "arithmetic_error", "wrong_source", "issue_lifecycle",
    }
    assert "missing_shares" not in {finding.code for finding in active.findings}
    assert [item["finding"]["code"] for item in ledger["scoped_findings"]] == ["missing_shares"]
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [
        ("arithmetic_error",)
    ]
    assert scope.model_dump(mode="json") == scope_before
    assert scope.equity_per_share_value.status == "blocked"


def test_research_uncertainty_scope_accepts_research_findings_but_not_data_defects():
    review = LifecycleVerification(
        reviewed_report=True,
        findings=[
            _finding("bounded_uncertainty", category="research"),
            _finding("missing_datum", category="data"),
        ],
        finding_dispositions=[
            {
                "finding_code": "bounded_uncertainty",
                "disposition": "disclosed_limitation",
                "rationale": "The uncertainty is correctly bounded.",
                "reader_excerpts": ["Customer concentration remains uncertain."],
                "conclusion_scopes": ["research_uncertainty"],
            },
            {
                "finding_code": "missing_datum",
                "disposition": "disclosed_limitation",
                "rationale": "A missing datum is not research uncertainty.",
                "reader_excerpts": ["The source does not report the amount."],
                "conclusion_scopes": ["research_uncertainty"],
            },
        ],
    )
    reader = "Customer concentration remains uncertain. The source does not report the amount."

    active, _, ledger = reconcile_review(review, [], {}, reader, _scope())

    assert "bounded_uncertainty" not in {finding.code for finding in active.findings}
    assert "missing_datum" in {finding.code for finding in active.findings}
    assert [item["finding"]["code"] for item in ledger["scoped_findings"]] == [
        "bounded_uncertainty"
    ]
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [
        ("missing_datum",)
    ]


def test_disclosure_cannot_scope_away_a_conclusion_that_deterministic_scope_allows():
    review = LifecycleVerification(
        reviewed_report=True,
        findings=[_finding("operating_gap", category="data")],
        finding_dispositions=[{
            "finding_code": "operating_gap",
            "disposition": "disclosed_limitation",
            "rationale": "The report mentions the gap.",
            "reader_excerpts": ["The report mentions the gap."],
            "conclusion_scopes": ["operating_asset_value"],
        }],
    )

    active, _, ledger = reconcile_review(
        review, [], {}, "The report mentions the gap.", _scope(operating="conditional")
    )

    assert "operating_gap" in {finding.code for finding in active.findings}
    assert ledger["scoped_findings"] == []
    assert [finding.affected_ids for finding in _lifecycle_findings(active)] == [
        ("operating_gap",)
    ]


def test_old_issues_and_provider_warnings_remain_when_no_explicit_decisions_exist():
    issues = [_issue("old-warning")]
    old_finding = ReviewFinding(
        code="provider_warning",
        severity="warning",
        category="research",
        message="Provider remained unavailable.",
    )
    review = LifecycleVerification(reviewed_report=True, findings=(old_finding,))

    active, remaining, ledger = reconcile_review(review, issues, {}, "reader", _scope())

    assert remaining == issues
    assert active.findings == (old_finding,)
    assert ledger["issues"][0]["status"] == "open"
    assert ledger["issues"][0]["decision"] is None


def test_any_contradicted_claim_prevents_resolution_and_disclosure_bypass():
    review = LifecycleVerification(
        reviewed_report=True,
        contradicted_claim_ids=("claim.contradicted",),
        findings=[_finding("unavailable_input", category="data")],
        issue_resolutions=[_resolution("old-warning")],
        finding_dispositions=[{
            "finding_code": "unavailable_input",
            "disposition": "disclosed_limitation",
            "rationale": "The conclusion is withheld.",
            "reader_excerpts": ["The conclusion is withheld."],
            "conclusion_scopes": ["funding_assessment"],
        }],
    )
    reader = "The stale statement has been corrected. The conclusion is withheld."

    active, remaining, ledger = reconcile_review(
        review,
        [_issue("old-warning")],
        {"fact:current": '{"value":42}'},
        reader,
        _scope(),
    )

    assert [item["issue_id"] for item in remaining] == ["old-warning"]
    assert "unavailable_input" in {finding.code for finding in active.findings}
    assert ledger["scoped_findings"] == []
    assert {finding.affected_ids for finding in _lifecycle_findings(active)} == {
        ("old-warning",), ("unavailable_input",),
    }


def test_unknown_and_duplicate_finding_codes_fail_closed():
    disclosed = {
        "disposition": "disclosed_limitation",
        "rationale": "The uncertainty is disclosed.",
        "reader_excerpts": ["Uncertainty remains."],
        "conclusion_scopes": ["research_uncertainty"],
    }
    review = LifecycleVerification(
        reviewed_report=True,
        findings=[_finding("duplicate"), _finding("duplicate")],
        finding_dispositions=[
            {"finding_code": "duplicate", **disclosed},
            {"finding_code": "duplicate", **disclosed},
            {"finding_code": "unknown", **disclosed},
        ],
    )

    active, _, ledger = reconcile_review(review, [], {}, "Uncertainty remains.", _scope())

    assert [finding.code for finding in active.findings].count("duplicate") == 2
    assert ledger["scoped_findings"] == []
    assert {finding.affected_ids for finding in _lifecycle_findings(active)} == {
        ("duplicate",), ("unknown",),
    }


def test_reconciliation_preserves_all_mutable_inputs():
    issues = [_issue("warning", claims=[{"id": "claim"}], missing_claim_ids=[])]
    evidence = {"fact:current": '{"value":42}'}
    reader = "The stale statement has been corrected."
    scope = _scope()
    review = LifecycleVerification(
        reviewed_report=True,
        supported_claim_ids=("claim",),
        issue_resolutions=[_resolution("warning")],
    )
    issues_before = deepcopy(issues)
    evidence_before = deepcopy(evidence)
    review_before = review.model_dump(mode="json")
    scope_before = scope.model_dump(mode="json")

    reconcile_review(review, issues, evidence, reader, scope)

    assert issues == issues_before
    assert evidence == evidence_before
    assert review.model_dump(mode="json") == review_before
    assert scope.model_dump(mode="json") == scope_before


@pytest.mark.parametrize("mutated", ["input", "remaining", "ledger"])
def test_returned_lifecycle_provenance_is_independent_of_inputs_and_coverage(mutated):
    issues = [_issue("warning", claims=[{"id": "claim", "evidence_ids": ["fact"]}],
                     origins=[{"retirable": True}], missing_claim_ids=[])]
    _, remaining, ledger = reconcile_review(
        LifecycleVerification(reviewed_report=True), issues, {}, "Reader", _scope())
    copies = {"input": issues, "remaining": remaining, "ledger": ledger["issues"]}
    copies[mutated][0]["origins"][0]["retirable"] = False
    copies[mutated][0]["claims"][0]["evidence_ids"].append("forged")
    for name, records in copies.items():
        if name != mutated:
            assert records[0]["origins"][0]["retirable"] is True
            assert records[0]["claims"][0]["evidence_ids"] == ["fact"]
