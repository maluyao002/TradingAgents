"""Regression proofs for material-gap disposition and exact reader export."""

from copy import deepcopy
from hashlib import sha256

from tests.test_research_engine import replies, setup
from tradingagents.research.engine import run_research
from tradingagents.research.replay import ReplayModelService
from tradingagents.research.report_review import limitation_packet
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import read_json

MATERIAL_GAP = "Customer concentration evidence is materially incomplete."
EXPECTED_GAPS = (
    "V2 source coverage and company-specific model acceptance remain pending.",
    MATERIAL_GAP,
    "Synthetic fixture has no financials",
    "No eligible source text available; empty context does not establish absence.",
)


def evidence_led(tmp_path, data):
    request, services = setup(tmp_path, data)
    request = request.model_copy(
        update={"quality_revision": "evidence-led", "report_language": "English"}
    )
    return request, ResearchServices(services.evidence, ReplayModelService(data))


def audit_only_dispositions(gaps):
    return [
        {
            "issue_id": item["issue_id"],
            "decision": "audit_only_immaterial",
            "rationale": "Explicit synthetic test disposition for the terminal-review fixture.",
            "reader_excerpt": "",
        }
        for item in limitation_packet(gaps)
    ]


def test_boolean_review_with_explicit_empty_dispositions_cannot_export_omitted_gap(tmp_path):
    data = replies()
    data["business"][0]["data"]["unresolved_gaps"] = [MATERIAL_GAP]
    data["verifier"][1]["data"] = {
        "reviewed_report": True,
        "limitation_dispositions": [],
    }
    data["verifier"].append(deepcopy(data["verifier"][1]))
    data["editor"].append(deepcopy(data["editor"][0]))
    request, services = evidence_led(tmp_path, data)

    result = run_research(request, services)

    assert result.stop_reason == "verification_failed"
    assert MATERIAL_GAP in result.unresolved_gaps
    assert [payload["stage"] for _, payload in services.models.calls].count("repair_report") == 1
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Diagnostic only" in reader
    assert MATERIAL_GAP not in reader
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["review"]["reviewed_report"] is True
    assert verification["review"]["limitation_dispositions"] == []
    assert any(
        finding["code"] == "limitation_disposition"
        and finding["severity"] == "critical"
        for finding in verification["review"]["findings"]
    )
    assert verification["exported"] is False


def test_explicit_complete_reader_coverage_exports_exact_reviewed_bytes(tmp_path):
    data = replies()
    data["business"][0]["data"]["unresolved_gaps"] = [MATERIAL_GAP]
    data["editor"][0]["data"]["limitations"] = list(EXPECTED_GAPS)
    dispositions = [
        {
            "issue_id": item["issue_id"],
            "decision": "reader_covered",
            "rationale": "The exact caveat is retained in the rendered material limitations.",
            "reader_excerpt": item["text"],
        }
        for item in limitation_packet(EXPECTED_GAPS)
    ]
    data["verifier"][1]["data"] = {
        "reviewed_report": True,
        "limitation_dispositions": dispositions,
    }
    request, services = evidence_led(tmp_path, data)

    result = run_research(request, services)

    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    review_payload = next(
        payload for _, payload in services.models.calls if payload["stage"] == "verify_report"
    )["research"]
    assert MATERIAL_GAP in reader
    assert review_payload["rendered_reader"] == reader
    assert review_payload["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
    assert result.artifact_hashes["reader_report.md"] == sha256(reader.encode()).hexdigest()
    assert {item["issue_id"] for item in review_payload["limitation_review"]} == {
        item["issue_id"] for item in dispositions
    }
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["reader_sha256"] == result.artifact_hashes["reader_report.md"]
    assert [
        {key: value for key, value in item.items() if key != "schema_version"}
        for item in verification["review"]["limitation_dispositions"]
    ] == [{**item, "reader_excerpts": []} for item in dispositions]
    assert verification["exported"] is True
    audit = read_json(request.output_dir / "reader_limitations.json")
    material_issue = next(
        item
        for item in audit["unresolved_issues"]["consolidated_exact_text"]
        if item["original_text"] == MATERIAL_GAP
    )
    verified_disposition = next(
        item
        for item in verification["review"]["limitation_dispositions"]
        if item["issue_id"] == material_issue["disposition_id"]
    )
    assert material_issue["verified_disposition"] == verified_disposition
    assert material_issue["reader_display"] == "verified_editorial_representation"
    assert material_issue["displayed_in_reader"] is True
    assert material_issue["issue_id"] not in audit["unresolved_issues"][
        "unrepresented_issue_ids"
    ]
    assert audit["exported_reader_sha256"] == result.artifact_hashes["reader_report.md"]


def test_terminal_warning_after_one_repair_cannot_export(tmp_path):
    first_warning = "Qualify the initial causal wording."
    terminal_warning = "The repaired reader still overstates customer evidence."
    data = replies()
    data["business"][0]["data"]["unresolved_gaps"] = [MATERIAL_GAP]
    data["verifier"][1]["data"] = {
        "reviewed_report": True,
        "findings": [
            {
                "code": "initial-wording",
                "severity": "warning",
                "message": first_warning,
            }
        ],
        "limitation_dispositions": audit_only_dispositions(EXPECTED_GAPS),
    }
    data["verifier"].append(
        {
            "data": {
                "reviewed_report": True,
                "findings": [
                    {
                        "code": "terminal-wording",
                        "severity": "warning",
                        "message": terminal_warning,
                    }
                ],
                "limitation_dispositions": audit_only_dispositions(
                    (*EXPECTED_GAPS, first_warning)
                ),
            },
            "usage": {},
        }
    )
    data["editor"].append(deepcopy(data["editor"][0]))
    request, services = evidence_led(tmp_path, data)

    result = run_research(request, services)

    assert result.stop_reason == "verification_failed"
    assert terminal_warning in result.unresolved_gaps
    stages = [payload["stage"] for _, payload in services.models.calls]
    assert stages.count("repair_report") == 1
    assert stages.count("verify_repaired_report") == 1
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Diagnostic only" in reader
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["stage"] == "verify_repaired_report"
    assert any(
        finding["code"] == "terminal-wording"
        and finding["severity"] == "warning"
        for finding in verification["review"]["findings"]
    )
    assert verification["exported"] is False
