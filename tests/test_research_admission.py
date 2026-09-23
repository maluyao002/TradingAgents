from hashlib import sha256

import pytest

from tradingagents.research.admission import evaluate_admission
from tradingagents.research.contracts import ReviewFinding, Usage
from tradingagents.research.result_scope import ComponentEligibility, ModelResultScope

READER_HASH = sha256(b"frozen reader").hexdigest()


def _scope(*, operating="conditional", equity="conditional", funding="conditional", opening="conditional"):
    def component(status):
        return ComponentEligibility(status=status, reasons=(f"{status} by independent scope",))
    return ModelResultScope(
        operating_asset_value=component(operating),
        equity_per_share_value=component(equity),
        funding_assessment=component(funding),
        opening_date_alignment=component(opening),
    )


def _verification(*, exported=True, hash_value=READER_HASH, reviewed=True, issues=(), dispositions=(), **review):
    return {
        "exported": exported,
        "reader_sha256": hash_value,
        "required_limitation_ids": list(issues),
        "validated_limitation_ids": list(issues),
        "review": {
            "reviewed_report": reviewed,
            "limitation_dispositions": list(dispositions),
            **review,
        },
    }


def _evaluate(**changes):
    values = {
        "stop_reason": "completed_needs_review", "reader_exported": True,
        "reader_sha256": READER_HASH, "verification": _verification(),
        "usage": Usage(input_tokens=2, output_tokens=3), "findings": (),
        "scope": _scope(), "case_reviewed": True,
    }
    values.update(changes)
    return evaluate_admission(**values)


def test_completed_verified_report_is_needs_review_not_an_activation_or_recommendation():
    admission = _evaluate()
    assert admission.report_completion == "complete"
    assert admission.qualitative_analysis.status == "eligible"
    assert admission.acceptance_eligibility.status == "eligible"
    assert admission.assessment_status == "needs_review"
    assert admission.production_activation is False
    assert admission.recommendation_status == admission.target_status == "withheld"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_operating_review_attestation_requires_a_real_boolean(value):
    with pytest.raises(TypeError, match="booleans"):
        _evaluate(operating_scenarios_reviewed=value)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_cashflow_review_attestation_requires_a_real_boolean(value):
    with pytest.raises(TypeError, match="booleans"):
        _evaluate(cashflow_bridge_reviewed=value)


def test_cashflow_review_does_not_clear_valuation_funding_or_missing_reader():
    result = _evaluate(cashflow_bridge_reviewed=True,
                       scope=_scope(operating="blocked", equity="blocked", funding="blocked"))
    assert result.cashflow_bridge.status == "conditional"
    assert result.model_conclusions.operating_asset_value.status == "blocked"
    assert result.acceptance_eligibility.status == "blocked"
    assert _evaluate(cashflow_bridge_reviewed=True, reader_exported=False).cashflow_bridge.status == "blocked"


def test_operating_scenario_review_is_separate_from_valuation_and_report_completion():
    result = _evaluate(operating_scenarios_reviewed=True,
                       scope=_scope(operating="blocked", equity="blocked", funding="blocked"))
    assert result.operating_scenarios.status == "conditional"
    assert result.model_conclusions.operating_asset_value.status == "blocked"
    assert result.acceptance_eligibility.status == "blocked"
    failed = _evaluate(operating_scenarios_reviewed=True, reader_exported=False)
    assert failed.operating_scenarios.status == "blocked"


@pytest.mark.parametrize("verification", [
    _verification(exported=False),
    _verification(hash_value="0" * 64),
    {"exported": True, "reader_sha256": READER_HASH, "review": {"reviewed_report": True}},
    _verification(reviewed=False),
])
def test_failed_tampered_or_coverage_less_verification_makes_report_incomplete(verification):
    admission = _evaluate(verification=verification)
    assert admission.report_completion == "incomplete"
    assert admission.acceptance_eligibility.status == "blocked"


def test_omitted_coverage_attestation_is_not_the_same_as_explicit_empty_coverage():
    omitted = _verification()
    del omitted["required_limitation_ids"]
    del omitted["validated_limitation_ids"]
    admission = _evaluate(verification=omitted)
    assert admission.report_completion == "incomplete"
    assert "malformed" in admission.qualitative_analysis.reasons[0].lower()


@pytest.mark.parametrize("field", ["exported", "reviewed_report"])
@pytest.mark.parametrize("value", ["true", 1])
def test_review_prerequisites_do_not_coerce_strings_or_numbers(field, value):
    packet = _verification()
    if field == "exported":
        packet[field] = value
    else:
        packet["review"][field] = value
    result = _evaluate(verification=packet)
    assert result.report_completion == "incomplete"
    assert result.acceptance_eligibility.status == "blocked"


def test_engine_audit_metadata_is_ignored_after_policy_surface_normalization():
    verification = _verification()
    verification.update({"stage": "verify_reader", "coverage_batches": [{"audit": "only"}]})
    assert _evaluate(verification=verification).report_completion == "complete"


def test_unknown_usage_blocks_acceptance_but_not_substantively_verified_completion():
    admission = _evaluate(usage=Usage(input_tokens=2, output_tokens=3, complete=False))
    assert admission.report_completion == "complete"
    assert admission.qualitative_analysis.status == "eligible"
    assert admission.acceptance_eligibility.status == "blocked"
    assert "telemetry" in admission.acceptance_eligibility.reasons[0]


def test_withheld_equity_keeps_a_useful_report_and_conditional_never_becomes_accepted():
    admission = _evaluate(scope=_scope(operating="conditional", equity="blocked"))
    assert admission.report_completion == "complete"
    assert admission.qualitative_analysis.status == "eligible"
    assert admission.model_conclusions.operating_asset_value.status == "conditional"
    assert admission.model_conclusions.equity_per_share_value.status == "blocked"
    assert admission.model_conclusions.operating_asset_value.status != "eligible"
    assert admission.acceptance_eligibility.status == "blocked"


def test_unreviewed_financial_case_blocks_acceptance_only():
    admission = _evaluate(case_reviewed=False)
    assert admission.report_completion == "complete"
    assert admission.acceptance_eligibility.status == "blocked"
    assert "financial case" in admission.acceptance_eligibility.reasons[0].lower()


def test_unknown_scope_blocks_every_model_output_without_promoting_production():
    admission = _evaluate(scope=None)
    assert {
        admission.model_conclusions.operating_asset_value.status,
        admission.model_conclusions.equity_per_share_value.status,
        admission.model_conclusions.funding_assessment.status,
        admission.model_conclusions.opening_date_alignment.status,
    } == {"blocked"}
    assert admission.production_activation is False


def test_warning_critical_contradiction_and_unresolved_disposition_block_completion():
    disposition = {
        "issue_id": "limitation-x", "decision": "unresolved", "rationale": "Still open",
    }
    admission = _evaluate(
        verification=_verification(
            issues=("limitation-x",), dispositions=(disposition,), contradicted_claim_ids=["claim-x"],
        ),
        findings=(ReviewFinding(code="warning", severity="warning", message="review me"),),
    )
    assert admission.report_completion == "incomplete"
    assert admission.assessment_status == "incomplete"
