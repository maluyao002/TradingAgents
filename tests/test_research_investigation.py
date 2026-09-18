from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.investigation import (
    ClosureDecision,
    collect_tasks,
    make_ledger,
)
from tradingagents.research.stages import ValuationProposal


def _source(identifier="filing", availability="full_text"):
    content = f"Synthetic evidence from {identifier}".encode()
    return SourceDocument(
        id=identifier,
        url=f"https://example.test/{identifier}",
        title=f"Synthetic {identifier}",
        publisher="Fixture",
        retrieved_at="2026-09-16T01:00:00Z",
        published_at="2026-09-16T00:00:00Z",
        content=content.decode(),
        content_sha256=sha256(content).hexdigest(),
        availability=availability,
    )


def _snapshot(*sources):
    return EvidenceSnapshot(ticker="TEST", cutoff="2026-09-17T00:00:00Z", sources=sources)


def test_collects_valuation_blockers_and_preserves_every_exact_duplicate_origin():
    analyses = {
        "business": {
            "followup_questions": ["Confirm customer concentration"],
            "unresolved_gaps": ["Missing normalized revenue history"],
        },
        "accounting": {
            "unresolved_gaps": [
                "Missing normalized revenue history",
                {
                    "text": "Reconcile diluted shares",
                    "category": "normalization_needed",
                    "provenance_id": "accounting:shares",
                },
            ]
        },
    }
    proposal = ValuationProposal(
        unsupported_inputs=("Missing forecast revenue", "Missing normalized revenue history")
    )
    valuation = {
        "status": "unavailable",
        "limitations": ["Missing forecast revenue", "Discount rate unsupported"],
    }

    tasks = collect_tasks(analyses, proposal, valuation)
    by_text = {task.text: task for task in tasks}

    assert set(by_text) == {
        "Confirm customer concentration",
        "Missing normalized revenue history",
        "Reconcile diluted shares",
        "Missing forecast revenue",
        "Discount rate unsupported",
    }
    duplicate = by_text["Missing normalized revenue history"]
    assert duplicate.category == "unclassified"
    assert [origin.origin_path for origin in duplicate.origins] == [
        "analyses.accounting.unresolved_gaps[0]",
        "analyses.business.unresolved_gaps[0]",
        "valuation_proposal.unsupported_inputs[1]",
    ]
    assert all(origin.original_text == duplicate.text for origin in duplicate.origins)
    assert len({origin.occurrence_id for origin in duplicate.origins}) == 3
    assert by_text["Reconcile diluted shares"].category == "normalization_needed"
    assert tasks == collect_tasks(dict(reversed(list(analyses.items()))), proposal, valuation)

    ledger = make_ledger(tasks, _snapshot(_source()), max_followup_questions=20)
    followups = {question.question for question in ledger.followup_questions}
    assert "Missing forecast revenue" in followups
    assert "Discount rate unsupported" in followups


def test_plain_strings_stay_unclassified_and_only_explicit_tags_route():
    tasks = collect_tasks(
        {
            "business": {
                "unresolved_gaps": [
                    "Find supplier evidence",
                    {
                        "text": "Find customer evidence",
                        "category": "retrieval_needed",
                        "provenance_id": "business:customers",
                    },
                ]
            }
        }
    )
    by_text = {task.text: task for task in tasks}
    assert by_text["Find supplier evidence"].category == "unclassified"
    assert by_text["Find customer evidence"].category == "retrieval_needed"

    ledger = make_ledger(tasks, _snapshot())
    assert ledger.routes["retrieval_needed"] == (by_text["Find customer evidence"].id,)
    assert ledger.routes["unclassified"] == (by_text["Find supplier evidence"].id,)


def test_keyword_match_never_closes_an_existing_gap():
    gap = "Verify whether the gross margin forecast is supportable"
    context = {
        "retrieval_metadata": {
            "queries": [
                {
                    "query_index": 0,
                    "terms": ["gross", "margin", "forecast"],
                    "match_label": "keyword_match",
                    "unresolved": False,
                    "hit_count": 12,
                    "hits": [{"source_id": "filing", "matched_terms": ["margin"]}],
                }
            ]
        }
    }

    tasks = collect_tasks({"expectations": {"unresolved_gaps": [gap]}}, context_metadata=context)
    ledger = make_ledger(tasks, _snapshot(_source()))

    assert len(ledger.entries) == 1
    assert ledger.entries[0].text == gap
    assert ledger.entries[0].status == "still_open"
    assert ledger.entries[0].evidence_ids == ()


def test_resolution_requires_eligible_evidence_and_explicit_verifier_judgment():
    snapshot = _snapshot(_source())
    task = collect_tasks({"business": {"unresolved_gaps": ["Confirm reported demand"]}})[0]

    with pytest.raises(ValidationError, match="cited evidence"):
        ClosureDecision(
            task_id=task.id,
            status="resolved",
            verifier_judgment="resolved",
            verifier_provenance_id="verify-claims:finding-1",
            disposition="Verifier accepted the cited filing passage.",
        )
    with pytest.raises(ValidationError, match="verifier resolution"):
        ClosureDecision(
            task_id=task.id,
            status="resolved",
            evidence_ids=("filing",),
            disposition="No verifier judgment was supplied.",
        )

    decision = ClosureDecision(
        task_id=task.id,
        status="resolved",
        evidence_ids=("filing",),
        verifier_judgment="resolved",
        verifier_provenance_id="verify-claims:finding-1",
        disposition="Verifier accepted the cited filing passage.",
    )
    entry = make_ledger((task,), snapshot, (decision,)).entries[0]
    assert entry.status == "resolved"
    assert entry.evidence_ids == ("filing",)
    assert entry.verifier_provenance_id == "verify-claims:finding-1"
    ledger = make_ledger((task,), snapshot, (decision,))
    assert task.id not in ledger.routes["unclassified"]
    assert not ledger.followup_questions


def test_unknown_or_ineligible_evidence_ids_are_rejected():
    snapshot = _snapshot(_source(), _source("snippet", availability="snippet"))
    task = collect_tasks({"business": {"unresolved_gaps": ["Confirm reported demand"]}})[0]

    for evidence_id in ("invented", "snippet"):
        decision = ClosureDecision(
            task_id=task.id,
            status="resolved",
            evidence_ids=(evidence_id,),
            verifier_judgment="resolved",
            verifier_provenance_id="verify-claims:finding-1",
            disposition="Synthetic verifier disposition.",
        )
        with pytest.raises(ValueError, match="unknown or ineligible"):
            make_ledger((task,), snapshot, (decision,))


def test_unmatched_query_cannot_establish_forecast_absence_or_resolution():
    context = {
        "queries": [
            {
                "query_index": 4,
                "query": "Locate management revenue forecast",
                "terms": ["management", "revenue", "forecast"],
                "match_label": "not_found",
                "unresolved": True,
                "hit_count": 0,
                "hits": [],
            }
        ]
    }
    task = collect_tasks({}, context_metadata=context)[0]

    assert task.category == "retrieval_needed"
    assert task.text == "Locate management revenue forecast"
    ledger = make_ledger((task,), _snapshot(_source()))
    assert ledger.entries[0].status == "still_open"
    assert ledger.followup_questions[0].question == task.text
    with pytest.raises(ValidationError, match="cited evidence"):
        ClosureDecision(
            task_id=task.id,
            status="resolved",
            verifier_judgment="resolved",
            verifier_provenance_id="verify-claims:absence",
            disposition="No keyword match was found.",
        )


def test_followups_are_bounded_without_losing_ledger_entries():
    tasks = collect_tasks(
        {"business": {"followup_questions": [f"Question {index}" for index in range(25)]}}
    )
    ledger = make_ledger(tasks, _snapshot(), max_followup_questions=4)

    assert len(ledger.entries) == 25
    assert len(ledger.followup_questions) == 4
    assert ledger.followup_total == 25
    assert ledger.followup_truncated
