"""Offline batch-inventory proof and atomic supersession regressions."""

from copy import deepcopy
from hashlib import sha256

import pytest

from tradingagents.research.contracts import ReviewFinding
from tradingagents.research.coverage_payload import coverage_review_data
from tradingagents.research.report_review import (
    LimitationDisposition,
    ReaderVerification,
    check_dispositions,
)
from tradingagents.research.revision_inventory import (
    assert_inventory_prefix_proof,
    inventory_eligibility,
    inventory_finding_hashes,
    project_inventory_issues,
    resolve_inventory,
)
from tradingagents.research.storage import digest

READER = "Material customer exposure remains uncertain. The audit process was logged."
ARTIFACT_HASHES = {name: letter * 64 for name, letter in (
    ("finalization_checkpoint.json", "a"),
    ("reader_verification.json", "b"),
    ("recovery_provenance.json", "c"),
    ("result.json", "d"),
)}
IDS = ("limitation-material", "limitation-audit", "limitation-missing")
FOREIGN = "limitation-foreign"


def _issues():
    return [
        {"issue_id": IDS[0], "text": "Customer concentration requires a material caveat.",
         "status": "open", "reader_coverage_required": True,
         "resolution_protected": True, "missing_claim_ids": []},
        {"issue_id": IDS[1], "text": "The audit process was logged.",
         "status": "open", "reader_coverage_required": False,
         "resolution_protected": False, "missing_claim_ids": []},
        {"issue_id": IDS[2], "text": "The original source extract was truncated.",
         "status": "open", "reader_coverage_required": False,
         "resolution_protected": False, "missing_claim_ids": []},
    ]


def _disposition(identifier, decision="reader_covered", excerpt=None):
    if excerpt is None:
        excerpt = ("Material customer exposure remains uncertain."
                   if decision == "reader_covered" else "")
    return LimitationDisposition(issue_id=identifier, decision=decision,
                                 rationale="Judged against the exact reader.",
                                 reader_excerpt=excerpt)


def _source(raw=None, issues=None, groups=None):
    issues = _issues() if issues is None else issues
    raw = raw or ReaderVerification(reviewed_report=True, limitation_dispositions=(
        _disposition(IDS[0]), _disposition(IDS[1], "audit_only_operational"),
        _disposition(FOREIGN),
    ))
    generated = check_dispositions(raw, issues, READER).findings
    output = raw.model_dump(mode="json")
    stage = "verify_revised_report-4-coverage-0"
    batch = {"stage": stage, "reader_sha256": sha256(READER.encode()).hexdigest(),
             "issue_ids": list(IDS),
             "equivalent_groups": groups or {identifier: [identifier] for identifier in IDS},
             "review": output}
    verification = {
        "stage": "verify_revised_report-4",
        "reader_sha256": sha256(READER.encode()).hexdigest(),
        "review": {"findings": [item.model_dump(mode="json") for item in generated]},
        "issue_lifecycle": {"issues": issues}, "coverage_batches": [batch],
    }
    stages = {stage: {"role": "verifier", "inputs_hash": digest(_source_payload(issues)),
                      "output_hash": digest(output), "output": output,
                      "usage": {"complete": True}}}
    return verification, stages


def _source_payload(issues=None):
    issues = _issues() if issues is None else issues
    return {"stage": "verify_revised_report-4-coverage-0",
            "revision_contract_sha256": "f" * 64,
            "research": coverage_review_data([
                {key: value for key, value in item.items() if key not in {"status", "decision"}}
                for item in issues], READER)}


def _eligible(verification=None, stages=None):
    if verification is None:
        verification, stages = _source()
    return inventory_eligibility(
        verification, stages, READER, source_generation=4,
        source_contract_sha256="f" * 64,
        source_artifact_hashes=ARTIFACT_HASHES,
    )


def _fresh(entries, *, decisions=None, issues=None, reader=READER):
    issues = (project_inventory_issues([], entries) if issues is None else issues)
    decisions = decisions or (
        _disposition(IDS[0]), _disposition(IDS[1], "audit_only_operational"),
        _disposition(IDS[2], "audit_only_operational"),
    )
    raw = ReaderVerification(reviewed_report=True, limitation_dispositions=decisions)
    output = raw.model_dump(mode="json")
    stage = "verify_revised_report-5-coverage-0"
    batch = {"stage": stage, "reader_sha256": sha256(reader.encode()).hexdigest(),
             "issue_ids": list(IDS),
             "equivalent_groups": {identifier: [identifier] for identifier in IDS},
             "review": output}
    checkpoint = {stage: {"role": "verifier", "inputs_hash": "1" * 64,
                          "output_hash": digest(output), "output": output,
                          "usage": {"complete": True}}}
    return {"generation": 5, "contract_sha256": "2" * 64,
            "reader_sha256": sha256(reader.encode()).hexdigest(), "reader_text": reader,
            "issues": issues, "batches": (issues,), "batch_audit": (batch,),
            "model_checkpoints": checkpoint}


def test_real_shape_one_missing_one_foreign_has_exact_full_inventory():
    verification, stages = _source()
    entries = _eligible(verification, stages)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["expected_issue_ids"] == list(IDS)
    assert entry["missing_issue_id"] == IDS[2]
    assert entry["foreign_issue_id"] == FOREIGN
    assert entry["observed_counts"] == [
        {"issue_id": IDS[0], "count": 1},
        {"issue_id": IDS[1], "count": 1},
        {"issue_id": FOREIGN, "count": 1},
    ]
    assert len(inventory_finding_hashes(entries)) == 2
    assert entry["source_coverage_inputs_hash"] == stages[entry["source_coverage_stage"]][
        "inputs_hash"]
    assert_inventory_prefix_proof(entries, stages, {
        "verify_revised_report-4-coverage-0": _source_payload()})


def test_full_fresh_exact_inventory_receipts_both_or_neither():
    entries = _eligible()
    receipt, failures = resolve_inventory(entries, **_fresh(entries))
    assert not failures and len(receipt) == 1
    assert receipt[0]["expected_issue_ids"] == list(IDS)
    assert set(receipt[0]["source_finding_hashes"]) == inventory_finding_hashes(entries)
    assert [item["issue_id"] for item in receipt[0]["coverage"]] == list(IDS)
    assert receipt[0]["receipt_sha256"] == digest({
        key: value for key, value in receipt[0].items() if key != "receipt_sha256"})


@pytest.mark.parametrize("change", [
    "missing", "duplicate", "inexact_span", "material_audit", "unresolved",
    "incomplete_usage", "wrong_input", "wrong_output", "wrong_reader",
    "aliased", "lost_issue", "changed_issue", "retired_issue", "authored_warning",
    "extra_foreign",
])
def test_no_partial_receipt_for_bad_fresh_inventory(change):
    entries = _eligible()
    fresh = _fresh(entries)
    if change in {"missing", "duplicate", "inexact_span", "material_audit", "unresolved"}:
        decisions = list(ReaderVerification.model_validate(
            fresh["batch_audit"][0]["review"]).limitation_dispositions)
        if change == "missing":
            decisions.pop()
        elif change == "duplicate":
            decisions.append(decisions[-1])
        elif change == "inexact_span":
            decisions[0] = _disposition(IDS[0], excerpt="Not an exact reader span")
        elif change == "material_audit":
            decisions[0] = _disposition(IDS[0], "audit_only_operational")
        else:
            decisions[0] = _disposition(IDS[0], "unresolved")
        output = ReaderVerification(reviewed_report=True,
                                    limitation_dispositions=tuple(decisions)).model_dump(mode="json")
        fresh["batch_audit"][0]["review"] = output
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output"] = output
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output_hash"] = digest(output)
    elif change == "incomplete_usage":
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["usage"]["complete"] = False
    elif change == "wrong_input":
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["inputs_hash"] = "not-a-hash"
    elif change == "wrong_output":
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output_hash"] = "3" * 64
    elif change == "wrong_reader":
        fresh["reader_sha256"] = "4" * 64
    elif change == "aliased":
        fresh["batch_audit"][0]["equivalent_groups"][IDS[0]] = [IDS[0], IDS[1]]
    elif change == "lost_issue":
        fresh["issues"] = [item for item in fresh["issues"] if item["issue_id"] != IDS[2]]
    elif change == "changed_issue":
        fresh["issues"][2]["text"] += " changed"
    elif change == "retired_issue":
        fresh["issues"][2]["status"] = "resolved"
    elif change == "authored_warning":
        output = fresh["batch_audit"][0]["review"]
        output["findings"] = [ReviewFinding(
            code="material_gap", severity="warning", category="research",
            message="Material source caveat remains.", affected_ids=(IDS[0],)
        ).model_dump(mode="json")]
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output"] = output
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output_hash"] = digest(output)
    elif change == "extra_foreign":
        output = fresh["batch_audit"][0]["review"]
        output["limitation_dispositions"].append(_disposition(FOREIGN).model_dump(mode="json"))
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output"] = output
        fresh["model_checkpoints"]["verify_revised_report-5-coverage-0"]["output_hash"] = digest(output)
    receipt, failures = resolve_inventory(entries, **fresh)
    assert receipt == ()
    assert {digest(item.model_dump(mode="json")) for item in failures} == inventory_finding_hashes(entries)


@pytest.mark.parametrize("change", [
    "two_missing", "duplicate", "semantic", "inexact_other", "protected_other",
    "aliased", "incomplete", "tampered_terminal", "foreign_is_other_expected",
])
def test_source_must_be_exact_isolated_pair(change):
    verification, stages = _source()
    output = deepcopy(stages["verify_revised_report-4-coverage-0"]["output"])
    if change == "two_missing":
        output["limitation_dispositions"] = output["limitation_dispositions"][:1]
    elif change == "duplicate":
        output["limitation_dispositions"].append(deepcopy(output["limitation_dispositions"][0]))
    elif change == "semantic":
        output["findings"] = [ReviewFinding(
            code="other_gap", severity="warning", category="research",
            message="Other semantic gap.", affected_ids=(IDS[0],)
        ).model_dump(mode="json")]
    elif change == "inexact_other":
        output["limitation_dispositions"][0]["reader_excerpt"] = "not in the reader"
    elif change == "protected_other":
        output["limitation_dispositions"][0]["decision"] = "audit_only_operational"
        output["limitation_dispositions"][0]["reader_excerpt"] = ""
    elif change == "foreign_is_other_expected":
        output["limitation_dispositions"][2]["issue_id"] = IDS[0]
    elif change == "aliased":
        verification["coverage_batches"][0]["equivalent_groups"][IDS[0]] = [IDS[0], IDS[1]]
    elif change == "incomplete":
        stages["verify_revised_report-4-coverage-0"]["usage"]["complete"] = False
    elif change == "tampered_terminal":
        verification["review"]["findings"][0]["message"] += " changed"
    if change in {"two_missing", "duplicate", "semantic", "inexact_other", "protected_other",
                  "foreign_is_other_expected"}:
        raw = ReaderVerification.model_validate(output)
        verification["review"]["findings"] = [item.model_dump(mode="json") for item in
                                               check_dispositions(raw, _issues(), READER).findings]
        verification["coverage_batches"][0]["review"] = output
        stages["verify_revised_report-4-coverage-0"]["output"] = output
        stages["verify_revised_report-4-coverage-0"]["output_hash"] = digest(output)
    if change == "aliased":
        # The terminal pair still exists, but a group can no longer be mapped
        # to exactly one original obligation.
        pass
    if change == "incomplete":
        pass
    with pytest.raises(ValueError, match="one exact eligible batch"):
        _eligible(verification, stages)


def test_writer_omission_is_pinned_and_changed_context_rejected():
    entries = _eligible()
    projected = project_inventory_issues([], entries)
    assert [item["issue_id"] for item in projected] == list(IDS)
    assert all("status" not in item for item in projected)
    changed = deepcopy(projected)
    changed[2]["reader_coverage_required"] = True
    with pytest.raises(ValueError, match="collides"):
        project_inventory_issues(changed, entries)


def test_prefix_proof_rejects_replaced_saved_reply_and_reader():
    verification, stages = _source()
    entries = _eligible(verification, stages)
    altered = deepcopy(stages)
    altered["verify_revised_report-4-coverage-0"]["inputs_hash"] = "0" * 64
    with pytest.raises(ValueError, match="stage proof"):
        assert_inventory_prefix_proof(entries, altered, {
            "verify_revised_report-4-coverage-0": _source_payload()})
    wrong_reader_entry = deepcopy(entries[0])
    wrong_reader_entry["source_reader_sha256"] = "0" * 64
    wrong_reader_entry["envelope_sha256"] = digest({
        key: value for key, value in wrong_reader_entry.items() if key != "envelope_sha256"})
    with pytest.raises(ValueError, match="reader"):
        assert_inventory_prefix_proof((wrong_reader_entry,), stages, {
            "verify_revised_report-4-coverage-0": _source_payload()})
    changed_context = _source_payload()
    changed_context["research"]["limitation_review"][0]["text"] += " changed"
    altered = deepcopy(stages)
    altered["verify_revised_report-4-coverage-0"]["inputs_hash"] = digest(changed_context)
    changed_entry = deepcopy(entries[0])
    changed_entry["source_coverage_inputs_hash"] = digest(changed_context)
    changed_entry["envelope_sha256"] = digest({
        key: value for key, value in changed_entry.items() if key != "envelope_sha256"})
    with pytest.raises(ValueError, match="full issue packet"):
        assert_inventory_prefix_proof((changed_entry,), altered, {
            "verify_revised_report-4-coverage-0": changed_context})
    assert_inventory_prefix_proof(entries, stages, {
        "verify_revised_report-4-coverage-0": _source_payload()})
