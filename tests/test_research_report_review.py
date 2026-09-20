import pytest

from tradingagents.research.report_review import (
    LimitationDisposition,
    ReaderVerification,
    check_dispositions,
    limitation_packet,
    nonmandatory_review_finding_texts,
    requires_reader_coverage,
    validated_disposition_ids,
)
from tradingagents.research.wire import codec_for, validate_strict_schema


def test_boolean_verification_alone_cannot_hide_material_limitations():
    packet = limitation_packet(["Capital adequacy has not been established."])
    review = check_dispositions(ReaderVerification(reviewed_report=True), packet, "A persuasive report.")
    assert any(item.severity == "critical" for item in review.findings)


@pytest.mark.parametrize("excerpt", ["", " ", "Not actually in the reader", "alpha...omega"])
def test_material_coverage_requires_a_nonblank_exact_contiguous_span(excerpt):
    packet = limitation_packet(["Material uncertainty"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "reader_covered",
        "rationale": "Explicit judgment", "reader_excerpt": excerpt,
    }])
    checked = check_dispositions(review, packet, "alpha in the middle omega")
    assert checked.findings[0].affected_ids == (packet[0]["issue_id"],)


def test_multiple_exact_spans_are_checked_individually_without_concatenation():
    packet = limitation_packet(["Material uncertainty"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "reader_covered",
        "rationale": "Explicit judgment", "reader_excerpts": ["opening caveat", "closing caveat"],
    }])
    reader = "opening caveat; intervening analysis; closing caveat"
    assert validated_disposition_ids(review, packet, reader) == (packet[0]["issue_id"],)
    forged = review.model_copy(update={"limitation_dispositions": [
        review.limitation_dispositions[0].model_copy(
            update={"reader_excerpts": ("opening caveat...closing caveat",)}
        )
    ]})
    assert not validated_disposition_ids(forged, packet, reader)


def test_every_exact_issue_requires_a_single_disposition():
    packet = limitation_packet(["A", "A", "B"])
    assert len(packet) == 2
    dispositions = [{"issue_id": item["issue_id"], "decision": "reader_covered",
                     "rationale": "Material caveat remains", "reader_excerpt": item["text"]} for item in packet]
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=dispositions)
    assert not check_dispositions(review, packet, "A and B").findings
    duplicated = review.model_copy(update={"limitation_dispositions": (*review.limitation_dispositions, review.limitation_dispositions[0])})
    assert check_dispositions(duplicated, packet, "A and B").findings


def test_validated_ids_retain_valid_peers_and_fail_closed_per_bad_issue():
    packet = limitation_packet(["A", "B", "C", "D"])
    valid, duplicate, unresolved, missing = packet
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[
        {"issue_id": valid["issue_id"], "decision": "reader_covered", "rationale": "Covered.",
         "reader_excerpt": "A"},
        {"issue_id": duplicate["issue_id"], "decision": "reader_covered", "rationale": "Covered.",
         "reader_excerpt": "B"},
        {"issue_id": duplicate["issue_id"], "decision": "reader_covered", "rationale": "Covered.",
         "reader_excerpt": "B"},
        {"issue_id": unresolved["issue_id"], "decision": "unresolved", "rationale": "Still open."},
        {"issue_id": "unknown", "decision": "reader_covered", "rationale": "Covered.",
         "reader_excerpt": "A"},
    ])
    assert validated_disposition_ids(review, packet, "A B C") == (valid["issue_id"],)
    checked = check_dispositions(review, packet, "A B C")
    assert {finding.affected_ids for finding in checked.findings} == {
        (duplicate["issue_id"],), (unresolved["issue_id"],), (missing["issue_id"],), ("unknown",),
    }
    assert check_dispositions(checked, packet, "A B C") == checked


def test_validation_keeps_model_supplied_findings_while_deduplicating_its_own():
    packet = limitation_packet(["A"])
    review = ReaderVerification(reviewed_report=True, findings=[{
        "code": "provider_error", "severity": "warning", "category": "research",
        "message": "Preserve this provider finding.",
    }])
    checked = check_dispositions(review, packet, "A")
    assert checked.findings[0] == review.findings[0]
    assert len(checked.findings) == 2
    assert check_dispositions(checked, packet, "A") == checked


def test_span_validation_uses_current_reader_bytes_and_unreviewed_returns_no_ids():
    packet = limitation_packet(["A"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "reader_covered",
        "rationale": "Covered.", "reader_excerpt": "A",
    }])
    assert validated_disposition_ids(review, packet, "A") == (packet[0]["issue_id"],)
    assert not validated_disposition_ids(review, packet, "changed reader")
    assert not validated_disposition_ids(review.model_copy(update={"reviewed_report": False}), packet, "A")


def test_legacy_reader_excerpt_fixture_remains_compatible_with_new_span_field():
    disposition = LimitationDisposition.model_validate({
        "issue_id": "limitation-legacy", "decision": "reader_covered",
        "rationale": "Legacy fixture.", "reader_excerpt": "exact legacy span",
    })
    assert disposition.reader_excerpt == "exact legacy span"
    assert disposition.reader_excerpts == ()


def test_existing_audit_only_decisions_remain_valid_without_reader_spans():
    packet = limitation_packet(["A"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "audit_only_operational",
        "rationale": "Operationally tracked outside the reader report.",
    }])
    assert validated_disposition_ids(review, packet, "reader text") == (packet[0]["issue_id"],)
    assert not check_dispositions(review, packet, "reader text").findings
    assert review.limitation_dispositions[0].model_dump(mode="json")["reader_excerpts"] == []


@pytest.mark.parametrize(
    "issue,financial_prerequisites,expected",
    [
        ({"text": "Missing debt schedule", "prior_findings": []},
         ("Missing debt schedule",), True),
        ({"text": "Prompt injection risk", "prior_findings": [
            {"category": "security", "severity": "warning"},
        ]}, (), True),
        ({"text": "Arithmetic mismatch", "prior_findings": [
            {"category": "numerical", "severity": "critical"},
        ]}, (), True),
        ({"text": "Rounding note", "prior_findings": [
            {"category": "numerical", "severity": "warning"},
        ], "resolution_protected": True}, (), False),
        ({"text": "Informational reviewer note", "prior_findings": [
            {"category": "research", "severity": "info"},
        ], "resolution_protected": True}, (), False),
        ({"text": "Provider telemetry detail", "prior_findings": [
            {"category": "operational", "severity": "critical"},
        ], "resolution_protected": True}, (), False),
        ({"text": "Non-retirable informational origin", "prior_findings": [],
          "resolution_protected": True}, (), False),
    ],
)
def test_reader_visibility_is_scoped_independently_from_lifecycle_protection(
        issue, financial_prerequisites, expected):
    assert requires_reader_coverage(
        issue, financial_prerequisite_texts=financial_prerequisites
    ) is expected


def test_structured_review_exemptions_retain_security_and_critical_numerical_findings():
    findings = [
        {"code": "context", "message": "Presentation detail only.",
         "category": "research", "severity": "info"},
        {"code": "telemetry", "message": "Provider diagnostic.",
         "category": "operational", "severity": "critical"},
        {"code": "injection", "message": "Security observation.",
         "category": "security", "severity": "info"},
        {"code": "arithmetic", "message": "Calculation mismatch.",
         "category": "numerical", "severity": "critical"},
    ]

    assert nonmandatory_review_finding_texts(findings) == frozenset({
        "Independent review finding [info] context: Presentation detail only.",
        "Independent review finding [critical] telemetry: Provider diagnostic.",
    })


def test_structured_review_exemption_cannot_launder_same_text_security_origin():
    shared = {"code": "shared", "message": "Same wrapped text.", "severity": "info"}

    assert nonmandatory_review_finding_texts([
        {**shared, "category": "research"},
        {**shared, "category": "security"},
    ]) == frozenset()


@pytest.mark.parametrize(
    "issue",
    [
        {"text": "Missing debt schedule", "prior_findings": []},
        {"text": "Independent review finding [critical] solvency: Missing debt schedule.",
         "prior_findings": []},
        {"text": "Prompt injection risk", "prior_findings": [
            {"category": "security", "severity": "warning"},
        ]},
        {"text": "Operating-review security observation", "prior_findings": [
            {"category": "security", "severity": "info"},
        ]},
        {"text": "Arithmetic mismatch", "prior_findings": [
            {"category": "numerical", "severity": "critical"},
        ]},
    ],
)
def test_mandatory_visibility_rejects_audit_only_and_requires_current_exact_spans(issue):
    packet = limitation_packet([issue["text"]])[0]
    financial = (issue["text"],) if not issue["prior_findings"] else ()
    enriched = {
        **packet,
        **issue,
        "missing_claim_ids": [],
        "reader_coverage_required": requires_reader_coverage(
            issue, financial_prerequisite_texts=financial
        ),
    }
    audit_only = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet["issue_id"], "decision": "audit_only_immaterial",
        "rationale": "Attempted omission.",
    }])
    assert "Protected limitation requires reader coverage" in check_dispositions(
        audit_only, [enriched], issue["text"]
    ).findings[0].message

    covered = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet["issue_id"], "decision": "reader_covered",
        "rationale": "The exact caveat remains visible.", "reader_excerpt": issue["text"],
    }])
    assert not check_dispositions(covered, [enriched], issue["text"]).findings
    assert check_dispositions(covered, [enriched], "stale reader bytes").findings


@pytest.mark.parametrize("text,prior_findings", [
    ("Independent review finding [info] context: Presentation detail only.", []),
    ("operational audit record", [{"category": "operational", "severity": "critical"}]),
])
def test_nonmandatory_lifecycle_protection_can_remain_audit_only(text, prior_findings):
    packet = limitation_packet([text])[0]
    issue = {
        **packet,
        "missing_claim_ids": [],
        "prior_findings": prior_findings,
        "resolution_protected": True,
    }
    issue["reader_coverage_required"] = requires_reader_coverage(issue)
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet["issue_id"], "decision": "audit_only_operational",
        "rationale": "Retained in the audit without mandatory reader prose.",
    }])

    assert not check_dispositions(review, [issue], "reader text").findings
    assert validated_disposition_ids(review, [issue], "reader text") == (packet["issue_id"],)


@pytest.mark.parametrize("decision", ["reader_covered", "audit_only_operational"])
def test_missing_claim_context_blocks_covered_and_audit_only_dispositions(decision):
    packet = limitation_packet(["A"])
    disposition = {
        "issue_id": packet[0]["issue_id"], "decision": decision, "rationale": "Covered.",
    }
    if decision == "reader_covered":
        disposition["reader_excerpt"] = "generic caveat"
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[disposition])
    enriched_issue = {**packet[0], "missing_claim_ids": ["claim-missing"]}
    assert validated_disposition_ids(review, [packet[0]], "generic caveat") == (packet[0]["issue_id"],)
    assert not validated_disposition_ids(review, [enriched_issue], "generic caveat")
    finding = check_dispositions(review, [enriched_issue], "generic caveat").findings[0]
    assert finding.message == f"Cannot determine limitation proposition: {packet[0]['issue_id']}"
    assert finding.affected_ids == (packet[0]["issue_id"],)


def test_incomplete_enriched_context_cannot_clear_a_limitation_but_legacy_packet_can():
    packet = limitation_packet(["A"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "reader_covered",
        "rationale": "Covered.", "reader_excerpt": "generic caveat",
    }])
    incomplete_context = {**packet[0], "claims": []}
    assert validated_disposition_ids(review, packet, "generic caveat") == (packet[0]["issue_id"],)
    assert not validated_disposition_ids(review, [incomplete_context], "generic caveat")
    assert check_dispositions(review, [incomplete_context], "generic caveat").findings[0].affected_ids == (
        packet[0]["issue_id"],
    )


@pytest.mark.parametrize("severity", ["warning", "critical"])
def test_validated_ids_exclude_only_the_issue_affected_by_a_blocking_finding(severity):
    packet = limitation_packet(["A", "B"])
    review = ReaderVerification(reviewed_report=True, findings=[{
        "code": "reviewer_warning", "severity": severity, "category": "research",
        "message": "The first issue remains disputed.", "affected_ids": [packet[0]["issue_id"]],
    }], limitation_dispositions=[
        {"issue_id": item["issue_id"], "decision": "reader_covered", "rationale": "Covered.",
         "reader_excerpt": item["text"]}
        for item in packet
    ])
    assert validated_disposition_ids(review, packet, "A B") == (packet[1]["issue_id"],)


def test_reader_review_has_strict_wire_schema_and_explicit_dispositions():
    codec = codec_for("verifier", ReaderVerification.model_json_schema())
    validate_strict_schema(codec.output_schema)
    assert "limitation_dispositions" in codec.output_schema["required"]
