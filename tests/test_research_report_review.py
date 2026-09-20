import pytest

from tradingagents.research.report_review import (
    LimitationDisposition,
    ReaderVerification,
    check_dispositions,
    limitation_packet,
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
