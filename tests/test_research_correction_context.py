"""Correction context never replaces independent whole-issue coverage."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from tradingagents.research import revision_correction_context as context_module
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.coverage_policy import packed_issue_context
from tradingagents.research.review_batches import expand_coverage_context, group_equivalent_issues
from tradingagents.research.revision_correction_context import (
    CORRECTION_CONTEXT_FIELD,
    attach_current_factual_corrections,
)
from tradingagents.research.storage import canonical_json, digest


def setup():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    text = "Vendor deployed the system.\nCommercial comparative costs are not established."
    source = SourceDocument(id="source", url="https://example.test/source", title="Deployment",
        publisher="Issuer", published_at=now, retrieved_at=now,
        content=text, content_sha256=sha256(text.encode()).hexdigest())
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=now, sources=(source,))
    issue = {"issue_id": "issue-a", "text": "No retained passage; commercial cost remains unknown.",
             "reader_coverage_required": True, "resolution_protected": False}
    finding = {"code": "source_absence", "severity": "warning", "category": "research",
               "message": "No passage retained.", "affected_ids": ["issue-a"]}
    finding_hash = digest(finding)
    reference = f"source_passage:{finding_hash}:source:{source.content_sha256}:0:{len(text)}"
    reader = "The record supports a deployment. Commercial cost comparisons remain unavailable."
    followup = {"source_finding_sha256": finding_hash, "disposition": "corrected",
        "rationale": "Retained deployment support does not close commercial economics.",
        "reader_excerpts": [reader], "witnesses": [{"reference": reference, "excerpt": text}]}
    review = {"reviewed_report": True, "contradicted_claim_ids": [], "findings": [],
              "source_finding_followups": [followup]}
    args = {"accepted_finding_hashes": {finding_hash}, "factual_review": review,
        "source_terminal_review": {"reader_sha256": "a" * 64,
            "findings": [{**finding, "source_finding_sha256": finding_hash}]},
        "source_terminal_review_sha256": "b" * 64, "reader_text": reader,
        "factual_stage": "verify_revised_report-4", "resolution_evidence": {},
        "source_text_witnesses": {reference: text}, "snapshot": snapshot}
    refresh(args, [issue])
    return issue, args


def refresh(args, issues):
    args["factual_payload"] = {"stage": args["factual_stage"], "research": {
        "rendered_reader": args["reader_text"],
        "rendered_reader_sha256": sha256(args["reader_text"].encode()).hexdigest(),
        "source_terminal_review": deepcopy(args["source_terminal_review"]),
        "source_terminal_review_sha256": args["source_terminal_review_sha256"],
        "resolution_evidence": deepcopy(args["resolution_evidence"]),
        "source_text_witnesses": deepcopy(args["source_text_witnesses"]),
        "inherited_issues": deepcopy(issues),
    }}
    args["checkpoint"] = {"role": "verifier", "inputs_hash": digest(args["factual_payload"]),
        "output": deepcopy(args["factual_review"]),
        "output_hash": digest(args["factual_review"]), "usage": {"complete": True}}


def test_valid_context_keeps_original_parent_and_exact_proof():
    issue, args = setup()
    original = deepcopy(issue)
    output = attach_current_factual_corrections([issue], **args)
    assert issue == original
    record = output[0].pop(CORRECTION_CONTEXT_FIELD)[0]
    assert output == [original]
    assert record["issue_sha256"] == digest(original)
    assert record["reader_sha256"] == sha256(args["reader_text"].encode()).hexdigest()
    assert record["context_sha256"] == digest({k: v for k, v in record.items()
                                              if k != "context_sha256"})


@pytest.mark.parametrize("mutation", ["unaccepted", "open", "unreviewed", "contradicted",
    "same_reader", "wrong_issue", "multi_issue", "compound", "compound_parent",
    "coverage_component", "conflicting_factual",
    "collapsed_newline", "outside_excerpt", "wrong_finding_ref", "blank", "wrong_reader_span"])
def test_invalid_or_broad_followups_never_enter_context(mutation):
    issue, args = setup()
    followup = args["factual_review"]["source_finding_followups"][0]
    witness = followup["witnesses"][0]
    if mutation == "unaccepted":
        args["accepted_finding_hashes"] = set()
    elif mutation == "open":
        followup["disposition"] = "still_open"
    elif mutation == "unreviewed":
        args["factual_review"]["reviewed_report"] = False
    elif mutation == "contradicted":
        args["factual_review"]["contradicted_claim_ids"] = ["claim-a"]
    elif mutation == "same_reader":
        args["source_terminal_review"]["reader_sha256"] = sha256(args["reader_text"].encode()).hexdigest()
    elif mutation == "wrong_issue":
        issue["issue_id"] = "issue-b"
    elif mutation == "multi_issue":
        finding = args["source_terminal_review"]["findings"][0]
        finding["affected_ids"].append("issue-b")
        old_hash = finding.pop("source_finding_sha256")
        new_hash = digest(finding)
        finding["source_finding_sha256"] = new_hash
        followup["source_finding_sha256"] = new_hash
        args["accepted_finding_hashes"] = {new_hash}
        witness["reference"] = witness["reference"].replace(old_hash, new_hash)
    elif mutation == "compound":
        issue["compound_obligation"] = {"unresolved": "cost"}
    elif mutation in {"compound_parent", "coverage_component"}:
        issue[mutation] = "parent-a"
    elif mutation == "conflicting_factual":
        args["factual_review"]["findings"] = [{"severity": "critical", "affected_ids": ["issue-a"]}]
    elif mutation == "collapsed_newline":
        witness["excerpt"] = witness["excerpt"].replace("\n", " ")
    elif mutation == "outside_excerpt":
        witness["excerpt"] = "Not in the referenced excerpt."
    elif mutation == "wrong_finding_ref":
        witness["reference"] = witness["reference"].replace(followup["source_finding_sha256"], "d" * 64)
    elif mutation == "blank":
        witness["excerpt"] = " "
    else:
        followup["reader_excerpts"] = ["A different reader."]
    refresh(args, [issue])
    assert attach_current_factual_corrections([issue], **args) == [issue]


@pytest.mark.parametrize("field,value", [("inputs_hash", "bad"), ("output_hash", "d" * 64),
                                        ("usage", {"complete": False}), ("role", "editor")])
def test_checkpoint_binding_fails_closed(field, value):
    issue, args = setup()
    args["checkpoint"][field] = value
    with pytest.raises(ValueError, match="checkpoint"):
        attach_current_factual_corrections([issue], **args)


def test_context_cannot_be_carried_as_input_or_leak_to_an_alias():
    issue, args = setup()
    alias = {**issue, "issue_id": "issue-alias"}
    refresh(args, [issue, alias])
    output = attach_current_factual_corrections([issue, alias], **args)
    assert CORRECTION_CONTEXT_FIELD in output[0]
    assert CORRECTION_CONTEXT_FIELD not in output[1]
    with pytest.raises(ValueError, match="fresh issues"):
        attach_current_factual_corrections(output, **args)


def test_exact_resolution_catalog_witness_remains_a_separate_supported_route():
    issue, args = setup()
    witness = args["factual_review"]["source_finding_followups"][0]["witnesses"][0]
    witness["reference"] = "passage:source:0:26"
    witness["excerpt"] = "Vendor deployed the system."
    args["resolution_evidence"] = {witness["reference"]: witness["excerpt"]}
    refresh(args, [issue])
    record = attach_current_factual_corrections([issue], **args)[0][CORRECTION_CONTEXT_FIELD][0]
    assert record["resolution_evidence_sha256"] == digest(args["resolution_evidence"])


def test_duplicate_followup_and_bounded_growth_fail_closed(monkeypatch):
    issue, args = setup()
    followups = args["factual_review"]["source_finding_followups"]
    followups.append(deepcopy(followups[0]))
    refresh(args, [issue])
    with pytest.raises(ValueError, match="duplicate"):
        attach_current_factual_corrections([issue], **args)
    followups.pop()
    refresh(args, [issue])
    monkeypatch.setattr(context_module, "MAX_CORRECTION_CONTEXT_BYTES", 1)
    with pytest.raises(ValueError, match="admission bound"):
        attach_current_factual_corrections([issue], **args)


@pytest.mark.parametrize("mutation", ["reader", "stage", "terminal_hash", "terminal_reader"])
def test_substitution_preserving_exact_quotes_does_not_reuse_a_checkpoint(mutation):
    issue, args = setup()
    if mutation == "reader":
        args["reader_text"] += " This additional assertion was never reviewed."
    elif mutation == "stage":
        args["factual_stage"] = "verify_revised_report-99"
    elif mutation == "terminal_hash":
        args["source_terminal_review_sha256"] = "f" * 64
    else:
        args["source_terminal_review"]["reader_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="exact factual input"):
        attach_current_factual_corrections([issue], **args)


def test_utf8_64000_byte_boundary_and_no_mutation_on_rejection():
    issue, args = setup()
    record = attach_current_factual_corrections([issue], **args)[0][CORRECTION_CONTEXT_FIELD]
    remaining = 64_000 - len(canonical_json(record))
    assert remaining > 0
    followup = args["factual_review"]["source_finding_followups"][0]
    followup["rationale"] += "é" * (remaining // 2) + "x" * (remaining % 2)
    refresh(args, [issue])
    record = attach_current_factual_corrections([issue], **args)[0][CORRECTION_CONTEXT_FIELD]
    assert len(canonical_json(record)) == 64_000
    original = deepcopy(issue)
    followup["rationale"] += "é"
    refresh(args, [issue])
    with pytest.raises(ValueError, match="admission bound"):
        attach_current_factual_corrections([issue], **args)
    assert issue == original


def test_coverage_packing_preserves_context_and_prevents_alias_grouping():
    issue, args = setup()
    alias = {**issue, "issue_id": "issue-alias"}
    assert len(group_equivalent_issues([issue, alias])) == 1
    refresh(args, [issue, alias])
    decorated = attach_current_factual_corrections([issue, alias], **args)
    assert len(group_equivalent_issues(decorated)) == 2
    packet = packed_issue_context(decorated)
    assert expand_coverage_context(packet) == tuple(decorated)
    assert digest(packet) != digest(packed_issue_context([issue, alias]))
