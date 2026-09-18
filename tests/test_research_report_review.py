import pytest

from tradingagents.research.report_review import (
    ReaderVerification,
    check_dispositions,
    limitation_packet,
)
from tradingagents.research.wire import codec_for, validate_strict_schema


def test_boolean_verification_alone_cannot_hide_material_limitations():
    packet = limitation_packet(["Capital adequacy has not been established."])
    review = check_dispositions(ReaderVerification(reviewed_report=True), packet, "A persuasive report.")
    assert any(item.severity == "critical" for item in review.findings)


@pytest.mark.parametrize("excerpt", ["", "Not actually in the reader"])
def test_material_coverage_requires_a_traceable_reader_excerpt(excerpt):
    packet = limitation_packet(["Material uncertainty"])
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=[{
        "issue_id": packet[0]["issue_id"], "decision": "reader_covered",
        "rationale": "Explicit judgment", "reader_excerpt": excerpt,
    }])
    assert check_dispositions(review, packet, "A persuasive report.").findings


def test_every_exact_issue_requires_a_single_disposition():
    packet = limitation_packet(["A", "A", "B"])
    assert len(packet) == 2
    dispositions = [{"issue_id": item["issue_id"], "decision": "reader_covered",
                     "rationale": "Material caveat remains", "reader_excerpt": item["text"]} for item in packet]
    review = ReaderVerification(reviewed_report=True, limitation_dispositions=dispositions)
    assert not check_dispositions(review, packet, "A and B").findings
    duplicated = review.model_copy(update={"limitation_dispositions": (*review.limitation_dispositions, review.limitation_dispositions[0])})
    assert check_dispositions(duplicated, packet, "A and B").findings


def test_reader_review_has_strict_wire_schema_and_explicit_dispositions():
    codec = codec_for("verifier", ReaderVerification.model_json_schema())
    validate_strict_schema(codec.output_schema)
    assert "limitation_dispositions" in codec.output_schema["required"]
