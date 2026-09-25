"""Offline source binding and reader construction for opt-in disclosures."""

from copy import deepcopy
from datetime import datetime

import pytest

from tests.test_research_reader import _case_draft, _request, _source
from tradingagents.research.contracts import EvidenceSnapshot
from tradingagents.research.controlled_disclosure import (
    prior_disclosure_lineage,
    validate_disclosure_packet,
)
from tradingagents.research.finalization_recovery import _open_required_issue_ids
from tradingagents.research.reader import render_reader
from tradingagents.research.storage import digest


def _packet(source):
    return {
        "schema_version": 1,
        "source_candidate_stage": "verify_revised_report-4",
        "source_candidate_sha256": "a" * 64,
        "source_terminal_review_sha256": "b" * 64,
        "evidence_sha256": "c" * 64,
        "case_context_sha256": "d" * 64,
        "entries": [{
            "issue_ids": ["limitation-one"],
            "terminal_finding_sha256s": ["e" * 64],
            "text": "This exposure is not quantified in the frozen record.",
            "source_passages": [{
                "source_id": source.id,
                "document_sha256": source.content_sha256,
                "start": 0,
                "end": len(source.content),
                "exact_text": source.content,
            }],
        }],
    }


def _validate(packet, snapshot):
    return validate_disclosure_packet(
        packet, snapshot,
        candidate_stage="verify_revised_report-4",
        candidate_sha256="a" * 64,
        terminal_review_sha256="b" * 64,
        evidence_sha256="c" * 64,
        case_context_sha256="d" * 64,
        issue_ids={"limitation-one"},
        finding_affected_ids={"e" * 64: {"limitation-one"},
                              "f" * 64: {"another-issue"}},
    )


def test_exact_source_packet_is_bound_and_rendered_in_fresh_review_reader(tmp_path):
    request = _request(tmp_path)
    source = _source()
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff,
                                sources=(source, _source("competitor")))
    packet = _validate(_packet(source), snapshot)
    draft = _case_draft()
    ordinary = render_reader(request, draft, snapshot, (), compact=True)
    controlled = render_reader(request, draft, snapshot, (), compact=True,
                               controlled_disclosure=packet)
    assert "Source-bound material disclosures" not in ordinary.reader_text
    assert "This exposure is not quantified in the frozen record. [^1]" in controlled.reader_text
    assert controlled.limitations_audit["controlled_disclosure"] == {
        "policy": "frozen-source-controlled-disclosure-v1",
        "packet_sha256": digest(packet),
        "section_index": len(draft.sections) + 1,
        "entries": [{"issue_ids": ["limitation-one"],
                     "terminal_finding_sha256s": ["e" * 64],
                     "source_ids": [source.id]}],
    }
    assert any(item["section_index"] == len(draft.sections) + 1
               and item["source_ids"] == [source.id]
               for item in controlled.limitations_audit["paragraph_citations"])
    assert controlled.reader_text != ordinary.reader_text


@pytest.mark.parametrize("change", [
    lambda p: p["entries"][0]["source_passages"][0].update(start=1),
    lambda p: p["entries"][0]["source_passages"][0].update(exact_text="invented"),
    lambda p: p["entries"][0].update(issue_ids=["foreign-issue"]),
    lambda p: p["entries"][0].update(terminal_finding_sha256s=["f" * 64]),
    lambda p: p["entries"][0].update(terminal_finding_sha256s=["e" * 64, "f" * 64]),
    lambda p: p.update(source_candidate_sha256="f" * 64),
    lambda p: p["entries"][0].update(text="false citation [^1]"),
    lambda p: p["entries"][0].update(text="line one\n- uncited second line"),
])
def test_packet_rejects_stale_identity_unbound_issue_or_forged_passage(tmp_path, change):
    request = _request(tmp_path)
    source = _source()
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(source,))
    packet = deepcopy(_packet(source))
    change(packet)
    with pytest.raises(ValueError):
        _validate(packet, snapshot)


def test_retired_issue_is_not_eligible_for_a_controlled_disclosure(tmp_path):
    request = _request(tmp_path)
    source = _source()
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(source,))
    verification = {
        "required_limitation_ids": ["limitation-one"],
        "issue_lifecycle": {"issues": [
            {"issue_id": "limitation-one", "status": "open"},
            {"issue_id": "retired-issue", "status": "resolved"},
        ]},
    }
    packet = _packet(source)
    packet["entries"][0]["issue_ids"] = ["retired-issue"]
    with pytest.raises(ValueError, match="issue or finding binding"):
        validate_disclosure_packet(
            packet, snapshot, candidate_stage="verify_revised_report-4",
            candidate_sha256="a" * 64, terminal_review_sha256="b" * 64,
            evidence_sha256="c" * 64, case_context_sha256="d" * 64,
            issue_ids=_open_required_issue_ids(verification),
            finding_affected_ids={"e" * 64: {"retired-issue"}},
        )


@pytest.mark.parametrize("published_at", [None, "2026-09-18T00:00:00Z"])
def test_packet_rejects_source_without_eligible_publication(tmp_path, published_at):
    request = _request(tmp_path)
    date = datetime.fromisoformat(published_at.replace("Z", "+00:00")) if published_at else None
    source = _source().model_copy(update={"published_at": date})
    if published_at is not None:
        with pytest.raises(ValueError, match="after evidence cutoff"):
            EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(source,))
        return
    snapshot = EvidenceSnapshot(ticker="TEST", cutoff=request.cutoff, sources=(source,))
    with pytest.raises(ValueError, match="frozen full text"):
        _validate(_packet(source), snapshot)


def test_prior_v7_packet_lineage_is_exact_and_hash_bound(tmp_path):
    source = _source()
    packet = _packet(source)
    record = {"generation": 5, "contract_sha256": "a" * 64,
              "provenance_sha256": "b" * 64}
    provenance = {
        "revision_contract_sha256": "a" * 64,
        "prior_controlled_disclosures": [],
        "prior_controlled_disclosures_sha256": digest([]),
        "controlled_disclosure": packet,
        "controlled_disclosure_sha256": digest(packet),
    }
    lineage = prior_disclosure_lineage(provenance, (record,), "a" * 64)
    assert lineage[0]["packet"] == packet
    following = {"revision_contract_sha256": "c" * 64,
                 "prior_controlled_disclosures": list(lineage),
                 "prior_controlled_disclosures_sha256": digest(lineage)}
    assert prior_disclosure_lineage(following, (record,), "a" * 64) == lineage
    following["prior_controlled_disclosures"][0]["packet"]["entries"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="lineage hash"):
        prior_disclosure_lineage(following, (record,), "a" * 64)
