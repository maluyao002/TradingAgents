"""Append-only v5 pending context and witness-scope regressions (offline)."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from tests.test_research_generic_revision import PendingGenericFixture, _pending_generic
from tests.test_research_review_lifecycle import _issue, _resolution, _scope
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.coverage_policy import coverage_batches_for_policy
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryModelService,
    assert_finalization_source_unchanged,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
    write_finalization_continuation_plan,
)
from tradingagents.research.reader_revision import GENERIC_REVISION_POLICY_V5, generation_stages
from tradingagents.research.review_lifecycle import (
    LifecycleVerification,
    claim_change_evidence,
    enrich_issues,
    reconcile_review,
    source_passage_witness_valid,
)
from tradingagents.research.revision_contracts import (
    V4_CONTRACT,
    V5_CONTRACT,
    V6_CONTRACT,
    revision_contract,
)
from tradingagents.research.revision_correction_context import CORRECTION_CONTEXT_FIELD
from tradingagents.research.revision_pending import pending_entries, project_pending_issues
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, digest, read_json


class OpenV4Coverage(PendingGenericFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"].startswith(generation_stages(2)[1] + "-coverage-"):
            pending = {item["issue_id"] for item in payload["research"]["limitation_review"]
                       if item["reader_coverage_required"] is False}
            for decision in reply.data.get("limitation_dispositions", ()):
                if decision["issue_id"] in pending:
                    decision["decision"] = "unresolved"
                    decision["reader_excerpt"] = ""
                    decision["reader_excerpts"] = []
        return reply


class V5FreshCoverage(PendingGenericFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] == generation_stages(3)[0]:
            pending_texts = {item["origin_issue"]["text"] for item in
                             payload["research"]["pending_coverage_contexts"]}
            reply.data["limitations"] = [item for item in reply.data["limitations"]
                                          if item not in pending_texts]
        return reply


class V4SourceCorrection(OpenV4Coverage):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] == generation_stages(2)[1]:
            issue = next((item for item in payload["research"]["inherited_issues"]
                          if item.get("reader_coverage_required") is True
                          and "compound_obligation" not in item), None)
            assert issue is not None, payload["research"]["inherited_issues"]
            self.correction_issue_id = issue["issue_id"]
            reply.data["findings"] = [*reply.data.get("findings", ()), {
                "code": "source_absence", "severity": "warning", "category": "research",
                "message": "A retained passage is missing from the source account; "
                           "the remaining commercial inference still needs review.",
                "affected_ids": [self.correction_issue_id],
            }]
        return reply


def _v5(tmp_path, provider=None, old_provider=None):
    source, evidence, old_service, _ = _pending_generic(
        tmp_path, old_provider or OpenV4Coverage())
    old = run_research(source, ResearchServices(evidence, old_service))
    assert old.stop_reason == "verification_failed"
    source_review = read_json(source.output_dir / "reader_verification.json")["English"]
    assert len(source_review["issue_lifecycle"]["pending_coverage_unresolved"]) == 1
    destination = source.model_copy(update={"output_dir": tmp_path / "v5-destination"})
    origin = old_service.plan.plan.source_dir
    plan = prepare_finalization_continuation(
        source.output_dir, destination, revise_reader=True, pending_origin_dir=origin)
    authorization = old_service.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget,
    })
    provider = provider or V5FreshCoverage()
    service = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    return source, destination, origin, evidence, service, provider


def test_v5_contract_preserves_v4_and_origin_is_explicit(tmp_path):
    source, _, origin, _, service, _ = _v5(tmp_path)
    plan = service.plan.plan
    assert revision_contract(V4_CONTRACT.policy, V4_CONTRACT.sha256) is V4_CONTRACT
    assert revision_contract(V5_CONTRACT.policy, V5_CONTRACT.sha256) is V5_CONTRACT
    assert plan.reader_revision_policy == GENERIC_REVISION_POLICY_V5
    assert plan.revision_generation == 3
    assert plan.pending_origin_dir == origin
    assert len(plan.pending_coverage_contexts) == 1
    assert pending_entries(plan.pending_coverage_contexts) == plan.deferred_coverage_eligibility
    assert_finalization_source_unchanged(plan)
    request = plan.destination_request.model_copy(update={
        "output_dir": tmp_path / "not-authorized-without-origin"})
    with pytest.raises(ValueError, match="explicit origin"):
        prepare_finalization_continuation(source.output_dir, request, revise_reader=True)
    changed = deepcopy(plan.pending_coverage_contexts[0])
    changed["origin_issue"]["text"] += " changed"
    with pytest.raises(ValueError):
        assert_finalization_source_unchanged(replace(
            plan, pending_coverage_contexts=(changed,)))


def test_v5_pins_omitted_issue_and_requires_fresh_same_id_coverage(tmp_path):
    _, request, _, evidence, service, provider = _v5(tmp_path)
    plan = service.plan.plan
    pending_id = plan.deferred_coverage_eligibility[0]["issue_id"]
    projected = project_pending_issues([], plan.pending_coverage_contexts)
    assert [item["issue_id"] for item in projected] == [pending_id]
    assert projected[0]["text"] == plan.pending_coverage_contexts[0]["origin_issue"]["text"]
    assert "status" not in projected[0] and "decision" not in projected[0]
    with pytest.raises(ValueError, match="collides"):
        project_pending_issues([{"issue_id": pending_id, "text": "paraphrased"}],
                               plan.pending_coverage_contexts)

    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review"
    calls = [payload for _, payload in provider.calls]
    assert calls[0]["stage"] == generation_stages(3)[0]
    assert calls[0]["research"]["pending_coverage_contexts"] == list(
        plan.pending_coverage_contexts)
    factual = next(item for item in calls if item["stage"] == generation_stages(3)[1])
    assert pending_id in {item["issue_id"] for item in factual["research"]["inherited_issues"]}
    coverage = [item for item in calls if item["stage"].startswith(
        generation_stages(3)[1] + "-coverage-")]
    assert sum(pending_id in {issue["issue_id"] for issue in item["research"]["limitation_review"]}
               for item in coverage) == 1
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    receipts = terminal["issue_lifecycle"]["deferred_coverage_receipts"]
    assert terminal["exported"] and len(receipts) == 1
    assert receipts[0]["issue_id"] == pending_id
    assert terminal["issue_lifecycle"]["pending_coverage_unresolved"] == []


def test_v5_accepted_factual_correction_reaches_only_its_issue_coverage(tmp_path):
    class ResidualOpen(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"].startswith(generation_stages(3)[1] + "-coverage-"):
                targets = {item["issue_id"] for item in payload["research"]["limitation_review"]
                           if CORRECTION_CONTEXT_FIELD in item}
                for decision in reply.data.get("limitation_dispositions", ()):
                    if decision["issue_id"] in targets:
                        decision["decision"] = "unresolved"
                        decision["reader_excerpt"] = ""
                        decision["reader_excerpts"] = []
            return reply

    old_provider = V4SourceCorrection()
    _, request, _, evidence, service, provider = _v5(
        tmp_path, ResidualOpen(), old_provider=old_provider)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    coverage = [payload for _, payload in provider.calls if payload["stage"].startswith(
        generation_stages(3)[1] + "-coverage-")]
    correction_items = [item for payload in coverage
                        for item in payload["research"]["limitation_review"]
                        if CORRECTION_CONTEXT_FIELD in item]
    assert sum(item["issue_id"] == old_provider.correction_issue_id
               for item in correction_items) == 1
    assert all(record["issue_id"] == item["issue_id"] for item in correction_items
               for record in item[CORRECTION_CONTEXT_FIELD])
    corrected = next(item for item in correction_items
                     if item["issue_id"] == old_provider.correction_issue_id)
    assert any(record["source_finding"]["code"] == "source_absence"
               for record in corrected[CORRECTION_CONTEXT_FIELD])
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    assert len(terminal["issue_lifecycle"]["current_factual_correction_contexts"]) >= 1


def test_v5_invalid_source_followup_does_not_become_coverage_context(tmp_path):
    class InvalidFollowup(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(3)[1]:
                finding_hash = next(item["source_finding_sha256"] for item in payload[
                    "research"]["source_terminal_review"]["findings"]
                    if item["code"] == "source_absence")
                followup = next(item for item in reply.data["source_finding_followups"]
                                if item["source_finding_sha256"] == finding_hash)
                followup["witnesses"][0]["excerpt"] = "Not a literal supplied witness."
            return reply

    old_provider = V4SourceCorrection()
    _, request, _, evidence, service, provider = _v5(
        tmp_path, InvalidFollowup(), old_provider=old_provider)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    coverage = [payload for _, payload in provider.calls if payload["stage"].startswith(
        generation_stages(3)[1] + "-coverage-")]
    target = next(item for payload in coverage
                  for item in payload["research"]["limitation_review"]
                  if item["issue_id"] == old_provider.correction_issue_id)
    assert all(record["source_finding"]["code"] != "source_absence"
               for record in target.get(CORRECTION_CONTEXT_FIELD, ()))
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    assert any(item["code"] == "source_absence" for item in terminal["review"]["findings"])


def test_v5_large_valid_context_cannot_create_a_second_coverage_call(tmp_path):
    class LargeCorrection(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(3)[1]:
                finding_hash = next(item["source_finding_sha256"] for item in payload[
                    "research"]["source_terminal_review"]["findings"]
                    if item["code"] == "source_absence")
                followup = next(item for item in reply.data["source_finding_followups"]
                                if item["source_finding_sha256"] == finding_hash)
                followup["rationale"] += "R" * 6500
            return reply

    _, request, _, evidence, service, provider = _v5(
        tmp_path, LargeCorrection(), old_provider=V4SourceCorrection())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason in {"verification_failed", "completed_needs_review"}
    precheck = read_json(request.output_dir / "stages" / "reverification-admission.json")["output"]
    assert precheck["remaining_path"]["call_count"] == 2  # factual + one pinned slot
    coverage = [payload for _, payload in provider.calls if payload["stage"].startswith(
        generation_stages(3)[1] + "-coverage-")]
    assert len(coverage) == 1
    items = coverage[0]["research"]["limitation_review"]
    assert len(coverage_batches_for_policy(items, request.coverage_batch_policy)) == 2
    lifecycle = read_json(request.output_dir / "reader_verification.json")[
        "English"]["issue_lifecycle"]
    assert len(lifecycle["pinned_coverage_schedule"]["slots"]) == 1
    assert 0 < lifecycle["pinned_coverage_schedule"]["positive_input_delta_bytes"] <= 64_000


def test_v5_oversized_correction_stops_before_any_coverage_dispatch(tmp_path):
    class OversizedCorrection(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(3)[1]:
                finding_hash = next(item["source_finding_sha256"] for item in payload[
                    "research"]["source_terminal_review"]["findings"]
                    if item["code"] == "source_absence")
                followup = next(item for item in reply.data["source_finding_followups"]
                                if item["source_finding_sha256"] == finding_hash)
                followup["rationale"] += "R" * 70_000
            return reply

    _, request, _, evidence, service, provider = _v5(
        tmp_path, OversizedCorrection(), old_provider=V4SourceCorrection())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason != "completed_needs_review"
    stages = [payload["stage"] for _, payload in provider.calls]
    assert stages == list(generation_stages(3))
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]


def test_v5_origin_artifact_change_and_context_hash_fail_before_dispatch(tmp_path):
    _, _, origin, _, service, provider = _v5(tmp_path)
    plan = service.plan.plan
    origin_reader = origin / "reader_verification.json"
    origin_reader.write_bytes(origin_reader.read_bytes() + b" ")
    with pytest.raises(ValueError, match="artifact hashes"):
        assert_finalization_source_unchanged(plan)
    assert not provider.calls


def test_v5_origin_directory_is_protected_from_output_and_plan_writes(tmp_path):
    source, _, origin, _, service, _ = _v5(tmp_path)
    plan = service.plan.plan
    nested_output = origin / "must-not-create-output"
    with pytest.raises(ValueError, match="overlaps source material"):
        prepare_finalization_continuation(source.output_dir,
            plan.destination_request.model_copy(update={"output_dir": nested_output}),
            revise_reader=True, pending_origin_dir=origin)
    assert not nested_output.exists()
    nested_plan = origin / "must-not-create-plan.json"
    with pytest.raises(ValueError, match="overlaps source material"):
        write_finalization_continuation_plan(plan, nested_plan)
    assert not nested_plan.exists()


@pytest.mark.parametrize("mutation", ["receipt", "unresolved", "terminal_finding"])
def test_v5_rejects_inconsistent_source_pending_partition(tmp_path, mutation):
    source, _, origin, _, service, provider = _v5(tmp_path)
    terminal_path = source.output_dir / "reader_verification.json"
    terminal = read_json(terminal_path)
    lifecycle = terminal["English"]["issue_lifecycle"]
    if mutation == "receipt":
        lifecycle["deferred_coverage_receipts"] = [{"source_finding_sha256":
            lifecycle["pending_coverage_unresolved"][0], "receipt_sha256": "f" * 64}]
        lifecycle["deferred_coverage_receipts_sha256"] = digest(
            lifecycle["deferred_coverage_receipts"])
    elif mutation == "unresolved":
        lifecycle["pending_coverage_unresolved"] = []
    else:
        finding = lifecycle["pending_coverage"][0]["source_finding"]
        terminal["English"]["review"]["findings"].remove(finding)
    terminal_path.write_bytes(canonical_json(terminal))
    with pytest.raises(ValueError):
        prepare_finalization_continuation(source.output_dir,
            service.plan.plan.destination_request.model_copy(update={
                "output_dir": tmp_path / "changed-source"}),
            revise_reader=True, pending_origin_dir=origin)
    assert not provider.calls


def test_v5_writer_and_factual_binding_include_pending_context(tmp_path):
    _, request, _, _, service, _ = _v5(tmp_path)
    plan = service.plan.plan
    writer, verifier = generation_stages(3)
    service.validate_request(request, plan.frozen_inputs)
    service._imported_stage_names = {item.stage for item in plan.imported_stages}
    base = {"stage": writer, "reader_revision_policy": plan.reader_revision_policy,
            "revision_contract_sha256": plan.revision_contract_sha256,
            "research": {"source_candidate": plan.candidate,
                         "source_writer_stage": plan.source_writer_stage,
                         "repair_findings": read_json(plan.source_dir /
                             "reader_verification.json")["English"]["review"],
                         "source_terminal_review_sha256": plan.source_terminal_review_sha256,
                         "reader_revision_policy": plan.reader_revision_policy,
                         "source_text_witnesses": plan.source_witness_catalog,
                         "pending_coverage": list(plan.deferred_coverage_eligibility),
                         "pending_coverage_contexts": []}}
    with pytest.raises(ValueError, match="bound source candidate"):
        service.call_origin("editor", base, request)
    assert verifier == "verify_revised_report-3"
    assert canonical_json(plan.manifest())


def test_v5_unresolved_receipt_carries_to_latest_contract_without_guessing_origin(tmp_path):
    class StillOpen(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"].startswith(generation_stages(3)[1] + "-coverage-"):
                pending = {item["issue_id"] for item in payload["research"]["limitation_review"]
                           if item["reader_coverage_required"] is False}
                for decision in reply.data.get("limitation_dispositions", ()):
                    if decision["issue_id"] in pending:
                        decision["decision"] = "unresolved"
                        decision["reader_excerpt"] = ""
                        decision["reader_excerpts"] = []
            return reply

    _, request, _, evidence, service, _ = _v5(tmp_path, StillOpen())
    first = run_research(request, ResearchServices(evidence, service))
    assert first.stop_reason == "verification_failed"
    source = read_json(request.output_dir / "reader_verification.json")["English"]
    assert source["issue_lifecycle"]["deferred_coverage_receipts"] == []
    assert len(source["issue_lifecycle"]["pending_coverage_unresolved"]) == 1
    destination = request.model_copy(update={"output_dir": tmp_path / "next-v5"})
    plan = prepare_finalization_continuation(request.output_dir, destination, revise_reader=True)
    assert plan.reader_revision_policy == V6_CONTRACT.policy
    assert plan.revision_generation == 4
    assert plan.pending_origin_dir is None
    assert len(plan.pending_coverage_contexts) == len(plan.prior_pending_contexts) == 1
    assert plan.pending_coverage_contexts == tuple(plan.prior_pending_contexts[0]["contexts"])
    assert_finalization_source_unchanged(plan)


def test_v5_factual_cannot_retire_pending_issue(tmp_path):
    class AttemptRetirement(V5FreshCoverage):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(3)[1]:
                pending_id = payload["research"]["pending_coverage"][0]["issue_id"]
                for item in reply.data["issue_resolutions"]:
                    if item["issue_id"] == pending_id:
                        item["status"] = "superseded"
                        break
            return reply

    _, request, _, evidence, service, provider = _v5(tmp_path, AttemptRetirement())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "stage_failed"
    assert [payload["stage"] for _, payload in provider.calls] == list(generation_stages(3))
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]


def test_v5_witness_fields_remain_separate_and_excerpts_literal():
    assert "issue_resolutions[].witnesses" in V5_CONTRACT.factual_requirements
    assert "source_finding_followups[].witnesses" in V5_CONTRACT.factual_requirements
    assert "spacing and line breaks" in V5_CONTRACT.factual_requirements
    claim = {"id": "business.old", "text": "Unsupported demand was certain.",
             "source_ids": [], "kind": "reported", "verification": "unverified",
             "material": True}
    issues = enrich_issues([_issue("stale", "Unverified claim: business.old")],
                           {"business": {"claims": [claim]}}, ())
    reader = "Demand remains uncertain and requires future evidence."
    evidence = claim_change_evidence(issues, reader)
    reference, literal = next(iter(evidence.items()))
    good = _resolution("stale", status="superseded", reader_excerpt=reader,
                       reference=reference, evidence_excerpt=literal)
    accepted, remaining, _ = reconcile_review(
        LifecycleVerification(reviewed_report=True, issue_resolutions=[good]),
        issues, evidence, reader, _scope())
    assert not accepted.findings and not remaining
    bad = deepcopy(good)
    bad["witnesses"].append({
        "reference": "source_passage:" + "a" * 64 + ":issuer:" + "b" * 64 + ":0:5",
        "excerpt": "text"})
    rejected, remaining, _ = reconcile_review(
        LifecycleVerification(reviewed_report=True, issue_resolutions=[bad]),
        issues, evidence, reader, _scope())
    assert remaining and any(item.code == "issue_lifecycle" for item in rejected.findings)

    cutoff = datetime(2026, 9, 22, tzinfo=timezone.utc)
    content = "Three direct \ncustomers represented 16%, 15%, and 13%. Risk remains."
    source = SourceDocument(id="issuer", url="https://example.test/issuer", title="Issuer filing",
        publisher="Issuer", published_at=cutoff, retrieved_at=cutoff, content=content,
        content_sha256=sha256(content.encode()).hexdigest())
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=cutoff, sources=(source,))
    finding_hash, other_hash = "a" * 64, "b" * 64
    key = f"source_passage:{finding_hash}:issuer:{source.content_sha256}:0:{len(content)}"
    catalog = {key: content}
    assert source_passage_witness_valid(key, "direct \ncustomers", catalog, snapshot,
                                        finding_hash)
    assert not source_passage_witness_valid(key, "direct customers", catalog, snapshot,
                                            finding_hash)
    assert not source_passage_witness_valid(key, "Risk remains.", catalog, snapshot,
                                            other_hash)
    narrower = {key: content[:30]}
    assert not source_passage_witness_valid(key, "Risk remains.", narrower, snapshot,
                                            finding_hash)
