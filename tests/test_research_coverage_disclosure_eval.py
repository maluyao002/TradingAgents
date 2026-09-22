import copy

import pytest

from tradingagents.research.coverage_disclosure_eval import (
    EXPLICIT_FINANCIAL_DRAFT,
    FINANCIAL_DRAFT_SENTENCE,
    GENERAL_STATUS_ONLY,
    OPERATING_REVIEW_ONLY,
    OPERATING_REVIEW_SENTENCE,
    disclosure_controls,
    score_disclosure_control,
)
from tradingagents.research.report_review import ReaderVerification


def _issues():
    return [
        {
            "issue_id": "limitation-other",
            "text": "A separate limitation.",
            "origins": [{"origin_id": "other"}],
            "reader_coverage_required": False,
        },
        {
            "issue_id": "limitation-financial-draft",
            "text": "Financial schedules are an unreviewed draft.",
            "origins": [{"origin_id": "case.review.draft"}],
            "reader_coverage_required": True,
        },
    ]


def _review(decision, reader, *, reviewed_report=True, excerpt=None, findings=()):
    draft_id = "limitation-financial-draft"
    if excerpt is None and decision == "reader_covered":
        excerpt = FINANCIAL_DRAFT_SENTENCE
    disposition = {
        "issue_id": draft_id,
        "decision": decision,
        "rationale": "Synthetic model label used only by this offline harness.",
    }
    if excerpt is not None:
        disposition["reader_excerpt"] = excerpt
    return ReaderVerification(
        reviewed_report=reviewed_report,
        findings=findings,
        limitation_dispositions=[disposition],
    )


def test_controls_are_fixed_reader_only_variants_and_preserve_issue_evidence():
    issues = _issues()
    original = copy.deepcopy(issues)
    reader = "Needs review / Unrated. Research foundation preview."

    controls = disclosure_controls(reader, issues)

    assert [item["id"] for item in controls] == [
        GENERAL_STATUS_ONLY, OPERATING_REVIEW_ONLY, EXPLICIT_FINANCIAL_DRAFT,
    ]
    assert controls[0]["reader"] == reader
    assert controls[1]["reader"] == reader + "\n\n### Review scope\n\n" + OPERATING_REVIEW_SENTENCE
    assert controls[2]["reader"] == controls[1]["reader"] + " " + FINANCIAL_DRAFT_SENTENCE
    assert all("Stage 2" not in item["reader"] for item in controls)
    assert "financial" not in OPERATING_REVIEW_SENTENCE.lower()
    assert all(item["id"] not in item["reader"] for item in controls)
    assert all(
        all(value not in item["reader"] for value in item["expected_decisions"].values())
        for item in controls
    )
    assert [item["expected_decisions"] for item in controls] == [
        {"limitation-financial-draft": "unresolved"},
        {"limitation-financial-draft": "unresolved"},
        {"limitation-financial-draft": "reader_covered"},
    ]
    assert all(set(item) == {"id", "reader", "expected_decisions", "rationale"} for item in controls)
    assert issues == original


@pytest.mark.parametrize("control_id", [GENERAL_STATUS_ONLY, OPERATING_REVIEW_ONLY])
def test_generic_status_and_operating_review_do_not_prove_financial_draft_coverage(control_id):
    control = next(item for item in disclosure_controls("Needs review / Unrated.", _issues()) if item["id"] == control_id)

    result = score_disclosure_control(
        _review("unresolved", control["reader"]), _issues(), control["reader"], control["expected_decisions"]
    )

    assert result["passed"]
    assert result["observed"] == {"limitation-financial-draft": ("unresolved",)}


def test_negative_control_accepts_provider_missing_disclosure_finding_with_unresolved_label():
    control = disclosure_controls("Needs review / Unrated.", _issues())[0]
    review = _review("unresolved", control["reader"], findings=[{
        "code": "missing_financial_draft", "severity": "critical", "category": "research",
        "message": "Financial schedules remain an unreviewed draft.",
        "affected_ids": ["limitation-financial-draft"],
    }])

    result = score_disclosure_control(review, _issues(), control["reader"], control["expected_decisions"])

    assert result["passed"]
    assert review.findings[0].code == "missing_financial_draft"


def test_negative_control_rejects_nonexact_supplied_quote():
    control = disclosure_controls("Needs review / Unrated.", _issues())[0]
    result = score_disclosure_control(
        _review("unresolved", control["reader"], excerpt="not in reader"),
        _issues(),
        control["reader"],
        control["expected_decisions"],
    )

    assert not result["passed"]
    assert any("nonexact witness" in reason for reason in result["mismatch_reasons"])


def test_explicit_financial_draft_control_evaluates_mocked_model_label_not_live_semantics():
    control = disclosure_controls("Needs review / Unrated.", _issues())[2]
    accepted_label = score_disclosure_control(
        _review("reader_covered", control["reader"]), _issues(), control["reader"], control["expected_decisions"]
    )
    rejected_label = score_disclosure_control(
        _review("unresolved", control["reader"]), _issues(), control["reader"], control["expected_decisions"]
    )

    assert accepted_label["passed"]
    assert not rejected_label["passed"]
    assert any("unexpected decision" in reason for reason in rejected_label["mismatch_reasons"])


@pytest.mark.parametrize(
    "review_factory, expected_reason",
    [
        (lambda reader: ReaderVerification(reviewed_report=True), "missing decision"),
        (lambda reader: ReaderVerification(reviewed_report=False, limitation_dispositions=[{
            "issue_id": "limitation-financial-draft", "decision": "reader_covered", "rationale": "Label.",
            "reader_excerpt": FINANCIAL_DRAFT_SENTENCE,
        }]), "unreviewed report"),
        (lambda reader: ReaderVerification(reviewed_report=True, limitation_dispositions=[
            {"issue_id": "limitation-financial-draft", "decision": "reader_covered", "rationale": "One.", "reader_excerpt": FINANCIAL_DRAFT_SENTENCE},
            {"issue_id": "limitation-financial-draft", "decision": "reader_covered", "rationale": "Two.", "reader_excerpt": FINANCIAL_DRAFT_SENTENCE},
        ]), "duplicate decision"),
        (lambda reader: _review("reader_covered", reader, excerpt="not in reader"), "nonexact witness"),
        (lambda reader: _review("reader_covered", reader, findings=[{
            "code": "provider", "severity": "critical", "category": "research",
            "message": "Financial disclosure remains blocked.", "affected_ids": ["limitation-financial-draft"],
        }]), "critical affected finding"),
        (lambda reader: _review("reader_covered", reader, findings=[{
            "code": "provider", "severity": "warning", "category": "research",
            "message": "Financial disclosure remains disputed.", "affected_ids": ["limitation-financial-draft"],
        }]), "reader coverage did not validate"),
    ],
)
def test_scoring_rejects_malformed_or_blocked_positive_controls(review_factory, expected_reason):
    control = disclosure_controls("Base reader.", _issues())[2]
    result = score_disclosure_control(
        review_factory(control["reader"]), _issues(), control["reader"], control["expected_decisions"]
    )

    assert not result["passed"]
    assert any(expected_reason in reason for reason in result["mismatch_reasons"])


@pytest.mark.parametrize(
    "expectations, reason",
    [
        ({"limitation-other": "unresolved"}, "unexpected expectation identifier"),
        ({
            "limitation-financial-draft": "unresolved",
            "limitation-other": "unresolved",
        }, "unexpected expectation identifier"),
        ({"limitation-financial-draft": "audit_only_operational"}, "unexpected expectation value"),
    ],
)
def test_scoring_rejects_expectations_outside_the_protected_case(expectations, reason):
    control = disclosure_controls("Base reader.", _issues())[0]
    result = score_disclosure_control(_review("unresolved", control["reader"]), _issues(), control["reader"], expectations)

    assert not result["passed"]
    assert any(reason in item for item in result["mismatch_reasons"])


@pytest.mark.parametrize(
    "issues, message",
    [
        ([{"issue_id": "one", "origins": [], "reader_coverage_required": True}], "exactly one"),
        ([
            {"issue_id": "one", "origins": [{"origin_id": "case.review.draft"}], "reader_coverage_required": True},
            {"issue_id": "two", "origins": [{"origin_id": "case.review.draft"}], "reader_coverage_required": True},
        ], "exactly one"),
        ([{"issue_id": "one", "origins": [{"origin_id": "case.review.draft"}], "reader_coverage_required": False}], "must require"),
    ],
)
def test_controls_require_one_protected_financial_draft_issue(issues, message):
    with pytest.raises(ValueError, match=message):
        disclosure_controls("Base reader.", issues)
