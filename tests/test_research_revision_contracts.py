"""Offline contract ownership and deferred-response lifecycle regressions."""

from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from tradingagents.research.reader_revision import generation_stages
from tradingagents.research.report_review import ReaderVerification, check_dispositions
from tradingagents.research.revision_contracts import V3_CONTRACT, V4_CONTRACT, revision_contract
from tradingagents.research.revision_deferred import (
    deferred_coverage_eligibility,
    resolve_deferred_coverage,
)
from tradingagents.research.revision_lineage import source_revision_contracts
from tradingagents.research.storage import digest


def _source_failure():
    reader = "The conditional package is reviewed only for its stated arithmetic."
    reader_hash = sha256(reader.encode()).hexdigest()
    issue_id = "limitation-" + "a" * 64
    issue = {"issue_id": issue_id, "text": "Historical review lineage is audit-only.",
             "status": "open", "reader_coverage_required": False,
             "resolution_protected": False, "claims": [], "missing_claim_ids": [],
             "prior_findings": []}
    stage = "verify_revised_report-2-coverage-0"
    raw = ReaderVerification(reviewed_report=True, limitation_dispositions=({
        "issue_id": issue_id, "decision": "audit_only_operational",
        "rationale": "The issue concerns review provenance, not economics.",
        "reader_excerpt": reader[:29], "reader_excerpts": [],
    },)).model_dump(mode="json")
    finding = check_dispositions(ReaderVerification.model_validate(raw), (issue,), reader).findings[0]
    verification = {"stage": "verify_revised_report-2", "reader_sha256": reader_hash,
        "review": {"findings": [finding.model_dump(mode="json")]},
        "issue_lifecycle": {"issues": [issue]},
        "coverage_batches": [{"stage": stage, "reader_sha256": reader_hash,
            "issue_ids": [issue_id], "equivalent_groups": {issue_id: [issue_id]},
            "review": raw}]}
    stages = {stage: {"role": "verifier", "inputs_hash": "b" * 64,
                      "output_hash": digest(raw), "output": raw}}
    return reader, issue, verification, stages


def test_deferred_classifier_proves_only_exact_saved_response_format_failure():
    reader, issue, verification, stages = _source_failure()
    eligible = deferred_coverage_eligibility(verification, stages, reader)
    assert len(eligible) == 1
    assert eligible[0]["kind"] == "audit_only_has_reader_spans"
    assert eligible[0]["source_finding"] == verification["review"]["findings"][0]
    assert eligible[0]["source_coverage_output_hash"] == stages[
        "verify_revised_report-2-coverage-0"]["output_hash"]

    # A deterministic finding with the same code is not enough: this one is
    # instead a missing-proposition failure from the historical validator.
    wrong = deepcopy(verification)
    wrong_issue = wrong["issue_lifecycle"]["issues"][0]
    wrong_issue["missing_claim_ids"] = ["missing-claim"]
    raw = ReaderVerification.model_validate(stages["verify_revised_report-2-coverage-0"]["output"])
    wrong["review"]["findings"] = [check_dispositions(
        raw, (wrong_issue,), reader).findings[0].model_dump(mode="json")]
    assert deferred_coverage_eligibility(wrong, stages, reader) == ()

    for mutation in ("protected", "authored", "duplicate", "wrong_stage", "wrong_hash"):
        changed = deepcopy(verification)
        changed_stages = deepcopy(stages)
        if mutation == "protected":
            changed["issue_lifecycle"]["issues"][0]["reader_coverage_required"] = True
        elif mutation == "authored":
            changed_stages["verify_revised_report-2-coverage-0"]["output"]["findings"] = [
                changed["review"]["findings"][0]]
            changed["coverage_batches"][0]["review"] = changed_stages[
                "verify_revised_report-2-coverage-0"]["output"]
            changed_stages["verify_revised_report-2-coverage-0"]["output_hash"] = digest(
                changed_stages["verify_revised_report-2-coverage-0"]["output"])
        elif mutation == "duplicate":
            changed["review"]["findings"].append(changed["review"]["findings"][0])
        elif mutation == "wrong_stage":
            changed["coverage_batches"][0]["stage"] = "verify_revised_report-2-coverage-1"
        else:
            changed_stages["verify_revised_report-2-coverage-0"]["output_hash"] = "f" * 64
        assert deferred_coverage_eligibility(changed, changed_stages, reader) == (), mutation


def test_pending_failure_requires_exact_new_generation_coverage_receipt():
    reader, issue, source, stages = _source_failure()
    pending = deferred_coverage_eligibility(source, stages, reader)
    new_reader = reader + " A newly checked reader."
    new_hash = sha256(new_reader.encode()).hexdigest()
    new_stage = "verify_revised_report-3-coverage-0"
    valid = ReaderVerification(reviewed_report=True, limitation_dispositions=({
        "issue_id": issue["issue_id"], "decision": "audit_only_operational",
        "rationale": "Review lineage remains an audit record.",
        "reader_excerpt": "", "reader_excerpts": [],
    },)).model_dump(mode="json")
    batch = {"stage": new_stage, "reader_sha256": new_hash, "review": valid}
    checkpoint = {new_stage: {"role": "verifier", "inputs_hash": "c" * 64,
                              "output_hash": digest(valid), "output": valid,
                              "usage": {"complete": True}}}

    def resolve(*, audit=(batch,), saved=checkpoint, reader_hash=new_hash, obligations=((issue,),)):
        return resolve_deferred_coverage(
            pending, source_stage=source["stage"], generation=3,
            contract_sha256=V4_CONTRACT.sha256,
            reader_sha256=reader_hash, reader_text=new_reader, issues=(issue,),
            batches=obligations, batch_audit=audit, model_checkpoints=saved)

    receipts, failures = resolve()
    assert len(receipts) == 1 and failures == ()
    assert receipts[0]["coverage_stage"] == new_stage
    assert receipts[0]["source_finding_sha256"] == pending[0]["source_finding_sha256"]
    assert receipts[0]["receipt_sha256"] == digest({
        key: value for key, value in receipts[0].items() if key != "receipt_sha256"})
    assert resolve(audit=(), saved={}, obligations=())[0] == ()
    assert resolve(reader_hash="f" * 64)[0] == ()
    old_stage = "verify_revised_report-2-coverage-0"
    assert resolve(audit=({**batch, "stage": old_stage},),
                   saved={old_stage: checkpoint[new_stage]})[0] == ()
    malformed = deepcopy(valid)
    malformed["limitation_dispositions"][0]["reader_excerpt"] = reader[:10]
    assert resolve(audit=({**batch, "review": malformed},), saved={new_stage: {
        **checkpoint[new_stage], "output": malformed, "output_hash": digest(malformed)}})[0] == ()
    with pytest.raises(ValueError, match="numbered revision contract"):
        resolve_deferred_coverage(pending, source_stage=source["stage"], generation=3,
            contract_sha256=V3_CONTRACT.sha256, reader_sha256=new_hash,
            reader_text=new_reader, issues=(issue,), batches=((issue,),),
            batch_audit=(batch,), model_checkpoints=checkpoint)


def _generation_stages(generation):
    writer, verifier = generation_stages(generation)
    return {name: SimpleNamespace(stage=name,
        role="editor" if name == writer else "verifier",
        inputs_hash=sha256((name + "-input").encode()).hexdigest(),
        output_hash=sha256((name + "-output").encode()).hexdigest())
        for name in (writer, verifier, verifier + "-coverage-0")}


def _provenance(generation, contract, stages, parent_hash, prior=()):
    writer, verifier = generation_stages(generation)
    return {"revision_generation": generation, "reader_revision_policy": contract.policy,
        "revision_contract_sha256": contract.sha256,
        "source_artifact_hashes": {"recovery_provenance.json": parent_hash},
        "prior_revision_contracts": list(prior),
        "current_calls": [{"stage": name, "role": stages[name].role,
            "origin": "current_live", "status": "completed",
            "inputs_hash": stages[name].inputs_hash, "usage": {"complete": True}}
            for name in (writer, verifier, verifier + "-coverage-0")]}


def test_mixed_contract_lineage_is_contiguous_and_hash_bound():
    assert V3_CONTRACT.sha256 == "5ee46e8ab938b68bb814aa532f81172726501e66a5e60d7f6fb9ee83fb48e022"
    assert revision_contract(V3_CONTRACT.policy, V3_CONTRACT.sha256) is V3_CONTRACT
    stages = _generation_stages(2)
    parent = "1" * 64
    first_hash = "2" * 64
    first = source_revision_contracts(_provenance(2, V3_CONTRACT, stages, parent),
                                      first_hash, tuple(stages.values()), 2)
    assert [(record["generation"], record["policy"]) for record in first] == [
        (2, V3_CONTRACT.policy)]
    newer = _generation_stages(3)
    stages.update(newer)
    second_hash = "3" * 64
    second = source_revision_contracts(_provenance(3, V4_CONTRACT, stages, first_hash, first),
                                       second_hash, tuple(stages.values()), 3)
    assert [(record["generation"], record["policy"]) for record in second] == [
        (2, V3_CONTRACT.policy), (3, V4_CONTRACT.policy)]
    with pytest.raises(ValueError, match="cumulative"):
        source_revision_contracts(_provenance(3, V4_CONTRACT, stages, first_hash),
                                  second_hash, tuple(stages.values()), 3)
    with pytest.raises(ValueError, match="direct parent"):
        source_revision_contracts(_provenance(3, V4_CONTRACT, stages, "f" * 64, first),
                                  second_hash, tuple(stages.values()), 3)
    damaged = deepcopy(first)
    damaged[0]["stage_proofs"][generation_stages(2)[0]]["inputs_hash"] = "f" * 64
    with pytest.raises(ValueError, match="ownership differs"):
        source_revision_contracts(_provenance(3, V4_CONTRACT, stages, first_hash, damaged),
                                  second_hash, tuple(stages.values()), 3)
    mixed = _provenance(3, V4_CONTRACT, stages, first_hash, first)
    mixed["revision_contract_sha256"] = V3_CONTRACT.sha256
    with pytest.raises(ValueError, match="unknown numbered revision contract"):
        source_revision_contracts(mixed, second_hash, tuple(stages.values()), 3)
