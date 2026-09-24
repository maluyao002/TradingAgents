"""Numbered candidate revision uses only offline synthetic replies."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest

from tests.test_research_case_engine import CaseFixture
from tests.test_research_verification_repair import _verification
from tradingagents.research import engine as engine_module
from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryModelService,
    _generic_source_generation,
    assert_finalization_source_unchanged,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
)
from tradingagents.research.finalization_timing import call_family
from tradingagents.research.reader_revision import (
    GENERIC_REVISION_POLICY_V4,
    generation_stages,
    generic_coverage_stage,
    generic_review_stage,
    generic_writer_stage,
)
from tradingagents.research.review_lifecycle import (
    RevisionLifecycleVerification,
    source_passage_witness_valid,
    source_passage_witnesses,
)
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import CheckpointStore, canonical_json, digest, read_json
from tradingagents.research.wire import codec_for, validate_strict_schema


class FrozenWarning(CaseFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] == "verify_frozen_report":
            reply.data["findings"] = [{"code": "material_guarantee_omitted", "severity": "warning",
                "message": "Disclose the material aggregate gross guarantee scale and phasing."}]
        return reply


class GenericFixture(CaseFixture):
    """Mechanics-only changed reader with explicit source-defect followups."""

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        stage = payload["stage"]
        if stage.startswith("revise_report-"):
            reply.data["sections"][0]["text"] += (
                f"\n\nSynthetic corrected source disclosure for generation {stage.rsplit('-', 1)[1]}.")
        if stage.startswith("verify_revised_report-") and "-coverage-" not in stage:
            research = payload["research"]
            reference, evidence = next((key, value) for key, value in
                research["resolution_evidence"].items() if value.strip())
            reply.data["source_finding_followups"] = [{
                "source_finding_sha256": finding["source_finding_sha256"],
                "disposition": "corrected",
                "rationale": "Synthetic fixture checks exact reader and source spans.",
                "reader_excerpts": [f"Synthetic corrected source disclosure for generation "
                                    f"{stage.rsplit('-', 1)[1]}."],
                "witnesses": [{"reference": reference, "excerpt": evidence[:30]}],
            } for finding in research["source_terminal_review"]["findings"]]
        return reply


class FrozenAuditSpan(CaseFixture):
    """Create a saved, deterministic audit-only response-format failure."""

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] == "verify_frozen_report":
            reply.data["findings"] = [{"code": "source_disclosure_gap", "severity": "warning",
                "message": "A source-supported qualification remains to be corrected."}]
        if payload["stage"].startswith("verify_frozen_report-coverage-"):
            audit_ids = {issue["issue_id"] for issue in payload["research"]["limitation_review"]
                         if issue["reader_coverage_required"] is False}
            for decision in reply.data.get("limitation_dispositions", ()):
                if decision["issue_id"] in audit_ids:
                    decision["decision"] = "audit_only_operational"
        return reply


class PendingGenericFixture(GenericFixture):
    """Factual followups cover substantive findings; coverage owns deferred ones."""

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"].startswith("verify_revised_report-") and "-coverage-" not in payload["stage"]:
            pending = {item["source_finding_sha256"] for item in payload["research"]["pending_coverage"]}
            reply.data["source_finding_followups"] = [
                item for item in reply.data["source_finding_followups"]
                if item["source_finding_sha256"] not in pending]
        return reply


def _pending_generic(tmp_path, provider=None):
    _, frozen_request, evidence, frozen_service, _ = _verification(
        tmp_path, FrozenAuditSpan())
    source_result = run_research(frozen_request, ResearchServices(evidence, frozen_service))
    assert source_result.stop_reason == "verification_failed"
    destination = frozen_request.model_copy(update={
        "output_dir": tmp_path / "pending-revision",
    })
    plan = prepare_finalization_continuation(
        frozen_request.output_dir, destination, revise_reader=True)
    assert len(plan.deferred_coverage_eligibility) == 1
    authorization = frozen_service.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget,
    })
    provider = provider or PendingGenericFixture()
    service = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    return destination, evidence, service, provider


def test_source_passage_witness_is_finding_bound_exact_and_cutoff_eligible():
    cutoff = datetime(2026, 9, 22, tzinfo=timezone.utc)
    text = ("Executive compensation threshold payout target table. "
            "Performance goals: threshold 50 percent, target 100 percent, "
            "maximum 200 percent; earned outcome 150 percent. ") * 8
    source = SourceDocument(id="issuer_incentive_proxy", url="https://example.test/proxy",
        title="Issuer incentive plan table", publisher="Issuer", published_at=cutoff,
        retrieved_at=cutoff, content=text,
        content_sha256=sha256(text.encode()).hexdigest())
    ineligible = source.model_copy(update={"id": "unavailable_incentive_proxy",
        "published_at": None})
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=cutoff, sources=(source, ineligible))
    finding = {"code": "incentive_tables_gap", "message": "Threshold and payout table missing.",
               "affected_ids": ["limitation-pay"]}
    linked = {"code": "limitation_disposition", "message": "Unresolved reader limitation",
              "affected_ids": ["limitation-pay"]}
    unrelated = {"code": "limitation_disposition", "message": "Unrelated unresolved limitation",
                 "affected_ids": ["limitation-other"]}
    catalog = source_passage_witnesses(snapshot, [finding, linked, unrelated])
    assert catalog and all("issuer_incentive_proxy" in key for key in catalog)
    finding_hash = digest(finding)
    linked_hash = digest(linked)
    assert {reference.split(":")[1] for reference in catalog} == {finding_hash, linked_hash}
    reference, excerpt = next((reference, excerpt) for reference, excerpt in catalog.items()
                              if reference.split(":")[1] == linked_hash)
    assert source_passage_witness_valid(reference, excerpt[:30], catalog, snapshot, linked_hash)
    assert not source_passage_witness_valid(reference, excerpt[:30], catalog, snapshot, finding_hash)
    assert not source_passage_witness_valid(reference, excerpt[:30], catalog, snapshot, "a" * 64)
    assert not source_passage_witness_valid(reference, "   ", catalog, snapshot, linked_hash)
    altered_reference = reference.rsplit(":", 1)[0] + ":" + str(int(reference.rsplit(":", 1)[1]) - 1)
    assert not source_passage_witness_valid(altered_reference,
                                            excerpt[:30], catalog, snapshot, linked_hash)


def test_five_terminal_findings_bind_proxy_to_linked_critical_only():
    """The duplicate critical issue must not silently lose its eligible table witness."""
    cutoff = datetime(2026, 9, 22, tzinfo=timezone.utc)

    def source(identifier, title, content):
        return SourceDocument(id=identifier, url=f"https://example.test/{identifier}",
            title=title, publisher="Issuer", published_at=cutoff, retrieved_at=cutoff,
            content=content, content_sha256=sha256(content.encode()).hexdigest())

    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=cutoff, sources=(
        source("issuer_guarantee_filing", "Issuer guarantee filing",
               "Maximum gross guarantees total 108.5, including 105 phased support "
               "and 3.5 other commitments. " * 8),
        source("issuer_incentive_proxy", "Issuer incentive compensation table",
               "Compensation threshold payout target maximum earned outcomes table: "
               "50 percent, 100 percent, 200 percent. " * 10),
    ))
    findings = [
        {"code": "guarantee_scale_omitted", "message": "Gross guarantee 108.5 = 105 + 3.5.",
         "affected_ids": ["issuer_guarantee_filing"]},
        {"code": "incentive_tables_gap", "message": "Review incentive tables.",
         "affected_ids": ["limitation-pay"]},
        {"code": "limitation_disposition", "message": "Unresolved limitation-pay",
         "affected_ids": ["limitation-pay"]},
        {"code": "residual_review_boundary", "message": "Separate residual review remains unclear.",
         "affected_ids": ["limitation-residual"]},
        {"code": "limitation_disposition", "message": "Unresolved limitation-residual",
         "affected_ids": ["limitation-residual"]},
    ]
    catalog = source_passage_witnesses(snapshot, findings)
    by_finding = {digest(finding): [reference for reference in catalog
                                    if reference.split(":")[1] == digest(finding)]
                  for finding in findings}
    assert by_finding[digest(findings[0])]
    assert by_finding[digest(findings[1])]
    assert by_finding[digest(findings[2])]
    assert not by_finding[digest(findings[3])]
    assert not by_finding[digest(findings[4])]
    critical_reference = by_finding[digest(findings[2])][0]
    assert "issuer_incentive_proxy" in critical_reference
    assert source_passage_witness_valid(critical_reference,
        catalog[critical_reference][:40], catalog, snapshot, digest(findings[2]))
    assert not source_passage_witness_valid(critical_reference,
        catalog[critical_reference][:40], catalog, snapshot, digest(findings[4]))


def test_new_incentive_outcome_finding_adds_separate_certified_result_witness():
    cutoff = datetime(2026, 9, 22, tzinfo=timezone.utc)
    goals = ("Incentive compensation threshold, target and maximum payout goals. " * 12)
    results = ("In March the committee certified performance achievement with the "
               "following payouts: PERFORMANCE ACHIEVEMENT AND PAYOUTS. Revenue "
               "reached 215.9 billion and the variable cash plan paid 200 percent "
               "of target opportunity. ") * 6
    content = goals + ("Other governance discussion. " * 80) + results
    source = SourceDocument(id="issuer_incentive_proxy", url="https://example.test/proxy",
        title="Issuer incentive compensation tables", publisher="Issuer",
        published_at=cutoff, retrieved_at=cutoff, content=content,
        content_sha256=sha256(content.encode()).hexdigest())
    unrelated = source.model_copy(update={"id": "other_company_governance",
        "title": "Other company governance", "content": results,
        "content_sha256": sha256(results.encode()).hexdigest()})
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=cutoff, sources=(source, unrelated))
    prior = {"code": "incentive_tables_gap", "message": "Integrate incentive tables."}
    current = {"code": "unsupported_incentive_outcome_inventory",
               "message": "Selected realized outcomes need exact source support."}
    old_catalog = source_passage_witnesses(snapshot, [prior])
    catalog = source_passage_witnesses(snapshot, [current])
    certified = [(reference, excerpt) for reference, excerpt in catalog.items()
                 if "PERFORMANCE ACHIEVEMENT AND PAYOUTS" in excerpt
                 and "215.9 billion" in excerpt]
    assert certified and len(catalog) > len(old_catalog)
    assert all("other_company_governance" not in reference for reference in catalog)
    reference, excerpt = certified[0]
    assert source_passage_witness_valid(reference, excerpt[:50], catalog, snapshot, digest(current))
    assert not source_passage_witness_valid(reference, excerpt[:50], catalog, snapshot, digest(prior))
    damaged = reference.replace(source.content_sha256, "a" * 64)
    assert not source_passage_witness_valid(damaged, excerpt[:50], catalog, snapshot, digest(current))
    future = snapshot.model_copy(update={"sources": (
        source.model_copy(update={"published_at": cutoff + timedelta(days=1)}), unrelated)})
    assert not source_passage_witness_valid(reference, excerpt[:50], catalog, future, digest(current))
    assert reference not in source_passage_witnesses(future, [current])


def _generic(tmp_path, provider=None, *, tokens=4_000_000, wall_seconds=None):
    _, frozen_request, evidence, frozen_service, _ = _verification(
        tmp_path, FrozenWarning())
    source_result = run_research(frozen_request, ResearchServices(evidence, frozen_service))
    assert source_result.stop_reason == "verification_failed"
    destination = frozen_request.model_copy(update={
        "output_dir": tmp_path / "generic-revision",
        "budget": frozen_request.budget.model_copy(update={
            "total_tokens": tokens,
            **({"wall_seconds": wall_seconds, "reserve_seconds": 0,
                "call_timeout_seconds": min(20, wall_seconds - 1)}
               if wall_seconds is not None else {}),
        }),
    })
    plan = prepare_finalization_continuation(
        frozen_request.output_dir, destination, revise_reader=True)
    authorization = frozen_service.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget,
    })
    provider = provider or GenericFixture()
    service = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    return frozen_request, destination, evidence, service, provider


def test_generic_revision_one_writer_and_fresh_full_review(tmp_path):
    source, request, evidence, service, provider = _generic(tmp_path)
    before = {path: path.read_bytes() for path in source.output_dir.rglob("*") if path.is_file()}
    assert service.plan.plan.revision_generation == 2
    assert service.plan.plan.source_writer_stage == "revise_report"
    assert service.plan.plan.reader_revision_policy == GENERIC_REVISION_POLICY_V4
    assert service.plan.plan.manifest()["revision_contract_sha256"] == (
        service.plan.plan.revision_contract_sha256)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review"
    calls = [payload for _, payload in provider.calls]
    writer, verifier = generation_stages(2)
    assert [payload["stage"] for payload in calls[:2]] == [writer, verifier]
    assert all(payload["stage"].startswith(verifier + "-coverage-") for payload in calls[2:])
    assert all(payload["reader_revision_policy"] == GENERIC_REVISION_POLICY_V4
               for payload in calls)
    assert all(payload["revision_contract_sha256"] == service.plan.plan.revision_contract_sha256
               for payload in calls)
    assert calls[0]["research"]["source_candidate"] == service.plan.plan.candidate
    assert "issue_lifecycle" not in calls[0]["research"]
    assert calls[0]["research"]["source_terminal_review_sha256"] == (
        service.plan.plan.source_terminal_review_sha256)
    assert digest(calls[0]["research"]["repair_findings"]) == (
        service.plan.plan.source_terminal_review_sha256)
    assert calls[0]["research"]["source_text_witnesses"] == (
        service.plan.plan.source_witness_catalog)
    assert "disclosed incentive thresholds" in calls[0]["research"]["repair_policy"]
    assert "SB Energy" not in calls[0]["research"]["repair_policy"]
    assert "Reevaluate each historical absence finding" in calls[1]["system"]
    assert calls[1]["research"]["source_terminal_review"]["reader_sha256"] == (
        service.plan.plan.candidate["reader_sha256"])
    assert "material_guarantee_omitted" in {
        finding["code"] for finding in calls[0]["research"]["repair_findings"]["findings"]}
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert terminal["exported"] and terminal["stage"] == verifier
    assert terminal["reader_sha256"] != service.plan.plan.candidate["reader_sha256"]
    assert len(terminal["coverage_batches"]) == len(calls) - 2
    lifecycle = terminal["issue_lifecycle"]
    assert lifecycle["source_terminal_review_sha256"] == service.plan.plan.source_terminal_review_sha256
    assert lifecycle["source_terminal_review"]["reader_sha256"] == service.plan.plan.candidate["reader_sha256"]
    assert lifecycle["source_finding_followups"]
    assert {path: path.read_bytes() for path in before} == before


def test_generic_revision_next_generation_requires_new_authorization(tmp_path):
    class StillWrong(GenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(2)[1]:
                reply.data["findings"] = [{"code": "still_wrong", "severity": "warning",
                    "message": "The guarantee treatment remains incomplete."}]
            return reply

    _, request, evidence, service, _ = _generic(tmp_path, StillWrong())
    first = run_research(request, ResearchServices(evidence, service))
    assert first.stop_reason == "verification_failed"
    with pytest.raises(ValueError):
        prepare_finalization_continuation(request.output_dir,
            request.model_copy(update={"output_dir": tmp_path / "forbidden"}),
            repair_verification=True)
    next_request = request.model_copy(update={"output_dir": tmp_path / "generation-3"})
    next_plan = prepare_finalization_continuation(request.output_dir, next_request, revise_reader=True)
    assert next_plan.revision_generation == 3
    assert next_plan.source_writer_stage == generation_stages(2)[0]
    authorization = service.plan.authorization.model_copy(update={
        "plan_sha256": next_plan.plan_sha256,
        "new_request_identity": next_plan.new_request_identity,
        "incremental_budget": next_request.budget,
    })
    provider = GenericFixture()
    next_service = FinalizationRecoveryModelService(
        authorize_finalization_continuation(next_plan, authorization), provider)
    second = run_research(next_request, ResearchServices(evidence, next_service))
    assert second.stop_reason == "completed_needs_review"
    assert [payload["stage"] for _, payload in provider.calls[:2]] == list(generation_stages(3))


def test_generic_source_findings_cannot_be_silently_waived(tmp_path):
    class Unchanged(GenericFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == generation_stages(2)[0]:
                return CaseFixture.complete(self, role, payload, request)
            return super().complete(role, payload, request)

    _, request, evidence, service, provider = _generic(tmp_path, Unchanged())
    result = run_research(request, ResearchServices(evidence, service))
    stages = [payload["stage"] for _, payload in provider.calls]
    assert result.stop_reason == "verification_failed"
    assert stages[:2] == list(generation_stages(2))
    assert any(stage.startswith(generation_stages(2)[1] + "-coverage-") for stage in stages)
    review = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not review["exported"]
    assert any(finding["code"] == "material_guarantee_omitted"
               for finding in review["review"]["findings"])


def test_generic_residual_coverage_warning_blocks_export(tmp_path):
    class CoverageWarning(GenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"].startswith(generation_stages(2)[1] + "-coverage-"):
                reply.data["findings"] = [{"code": "coverage_still_open", "severity": "warning",
                    "message": "This atomic caveat remains unresolved."}]
            return reply

    _, request, evidence, service, provider = _generic(tmp_path, CoverageWarning())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    assert any(payload["stage"].startswith(generation_stages(2)[1] + "-coverage-")
               for _, payload in provider.calls)
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_generic_missing_terminal_finding_followup_fails_closed(tmp_path):
    class MissingFollowup(GenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(2)[1]:
                reply.data["source_finding_followups"] = []
            return reply

    _, request, evidence, service, provider = _generic(tmp_path, MissingFollowup())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "stage_failed"
    assert len(provider.calls) == 2


def test_deferred_span_error_requires_fresh_coverage_receipt_to_export(tmp_path):
    request, evidence, service, provider = _pending_generic(tmp_path)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review"
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    lifecycle = terminal["issue_lifecycle"]
    assert terminal["exported"]
    assert len(lifecycle["pending_coverage"]) == 1
    assert len(lifecycle["deferred_coverage_receipts"]) == 1
    assert lifecycle["pending_coverage_unresolved"] == []
    receipt = lifecycle["deferred_coverage_receipts"][0]
    assert receipt["source_finding_sha256"] == lifecycle["pending_coverage"][0][
        "source_finding_sha256"]
    assert receipt["coverage_stage"].startswith(generation_stages(2)[1] + "-coverage-")
    assert any(payload["stage"] == receipt["coverage_stage"] for _, payload in provider.calls)


def test_deferred_span_error_interrupted_fresh_coverage_never_exports(tmp_path):
    class Interrupted(PendingGenericFixture):
        def complete(self, role, payload, request):
            if payload["stage"].startswith(generation_stages(2)[1] + "-coverage-"):
                raise TimeoutError("synthetic fresh coverage interruption")
            return super().complete(role, payload, request)

    request, evidence, service, _ = _pending_generic(tmp_path, Interrupted())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason != "completed_needs_review"
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_deferred_span_error_repeated_in_fresh_coverage_remains_open(tmp_path):
    class RepeatedSpan(PendingGenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"].startswith(generation_stages(2)[1] + "-coverage-"):
                pending_ids = {item["issue_id"] for item in payload["research"][
                    "limitation_review"] if item["reader_coverage_required"] is False}
                for decision in reply.data.get("limitation_dispositions", ()):
                    if decision["issue_id"] in pending_ids:
                        decision["decision"] = "audit_only_operational"
                        decision["reader_excerpt"] = payload["research"]["rendered_reader"][:24]
            return reply

    request, evidence, service, _ = _pending_generic(tmp_path, RepeatedSpan())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    lifecycle = terminal["issue_lifecycle"]
    assert lifecycle["deferred_coverage_receipts"] == []
    assert lifecycle["pending_coverage_unresolved"] == [
        lifecycle["pending_coverage"][0]["source_finding_sha256"]]
    assert any(item["code"] == "limitation_disposition" for item in terminal["review"]["findings"])


def test_deferred_receipt_waits_for_entire_fresh_coverage_after_late_interruption(
        tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "coverage_batches_for_policy",
                        lambda issues, _policy: tuple((item,) for item in issues))

    class LateInterruption(PendingGenericFixture):
        def complete(self, role, payload, request):
            if payload["stage"] == generation_stages(2)[1] + "-coverage-1":
                raise TimeoutError("synthetic later-batch interruption")
            return super().complete(role, payload, request)

    request, evidence, service, provider = _pending_generic(tmp_path, LateInterruption())
    pending_id = service.plan.plan.deferred_coverage_eligibility[0]["issue_id"]
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "stage_failed" and not result.usage.complete
    assert any(payload["stage"] == generation_stages(2)[1] + "-coverage-0"
               and pending_id in {item["issue_id"] for item in payload["research"]["limitation_review"]}
               for _, payload in provider.calls)
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    assert terminal["issue_lifecycle"]["pending_coverage"][0]["issue_id"] == pending_id
    assert not terminal["issue_lifecycle"].get("deferred_coverage_receipts")
    provenance = read_json(request.output_dir / "recovery_provenance.json")
    assert provenance["deferred_coverage_eligibility"][0]["issue_id"] == pending_id

    resumed_provider = PendingGenericFixture()
    resumed_service = FinalizationRecoveryModelService(service.plan, resumed_provider)
    resumed = run_research(request, ResearchServices(evidence, resumed_service))
    assert resumed.stop_reason != "completed_needs_review"
    assert not resumed_provider.calls
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_deferred_safe_predispatch_resume_reuses_completed_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "coverage_batches_for_policy",
                        lambda issues, _policy: tuple((item,) for item in issues))
    request, evidence, service, provider = _pending_generic(tmp_path)
    original_save = CheckpointStore.save_stage
    cost_stage = generation_stages(2)[1] + "-cost-plan"
    count = 0

    def interrupt_before_second_batch(store, stage, inputs, output):
        nonlocal count
        if stage == cost_stage:
            count += 1
            if count == 3:
                raise BudgetExhausted("synthetic_predispatch_stop")
        return original_save(store, stage, inputs, output)

    monkeypatch.setattr(CheckpointStore, "save_stage", interrupt_before_second_batch)
    first = run_research(request, ResearchServices(evidence, service))
    assert first.stop_reason == "synthetic_predispatch_stop" and first.usage.complete
    first_stages = [payload["stage"] for _, payload in provider.calls]
    assert first_stages[-1] == generation_stages(2)[1] + "-coverage-0"
    first_terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not first_terminal["exported"]
    assert first_terminal["issue_lifecycle"]["pending_coverage"]
    assert not first_terminal["issue_lifecycle"].get("deferred_coverage_receipts")

    monkeypatch.setattr(CheckpointStore, "save_stage", original_save)
    resumed_provider = PendingGenericFixture()
    resumed_service = FinalizationRecoveryModelService(service.plan, resumed_provider)
    resumed = run_research(request, ResearchServices(evidence, resumed_service))
    assert resumed.stop_reason == "completed_needs_review"
    resumed_stages = [payload["stage"] for _, payload in resumed_provider.calls]
    assert resumed_stages and all(stage.startswith(generation_stages(2)[1] + "-coverage-")
                                  and stage != generation_stages(2)[1] + "-coverage-0"
                                  for stage in resumed_stages)
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert terminal["exported"]
    assert len(terminal["issue_lifecycle"]["deferred_coverage_receipts"]) == 1
    assert terminal["issue_lifecycle"]["pending_coverage_unresolved"] == []


def test_deferred_span_error_does_not_waive_substantive_followup(tmp_path):
    class Omitted(PendingGenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(2)[1]:
                reply.data["source_finding_followups"] = []
            return reply

    request, evidence, service, provider = _pending_generic(tmp_path, Omitted())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "stage_failed"
    assert len(provider.calls) == 2
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


@pytest.mark.parametrize("stage_kind", ["writer", "factual"])
def test_deferred_ledger_is_bound_at_both_new_paid_boundaries(tmp_path, stage_kind):
    request, _, service, provider = _pending_generic(tmp_path)
    plan = service.plan.plan
    source_review = read_json(plan.source_dir / "reader_verification.json")["English"]["review"]
    writer, factual = generation_stages(plan.revision_generation)
    if stage_kind == "writer":
        stage, role = writer, "editor"
        research = {"source_candidate": plan.candidate,
            "source_writer_stage": plan.source_writer_stage,
            "repair_findings": source_review,
            "source_terminal_review_sha256": plan.source_terminal_review_sha256,
            "reader_revision_policy": plan.reader_revision_policy,
            "source_text_witnesses": plan.source_witness_catalog}
    else:
        stage, role = factual, "verifier"
        findings = {digest(item): item for item in source_review["findings"]}
        research = {"source_terminal_review": {"stage": plan.candidate_review_stage,
            "reader_sha256": plan.candidate["reader_sha256"],
            "findings": [{"source_finding_sha256": key, **value}
                         for key, value in findings.items()]},
            "source_terminal_review_sha256": plan.source_terminal_review_sha256,
            "source_text_witnesses": plan.source_witness_catalog}
    service.validate_request(request, plan.frozen_inputs)
    service._imported_stage_names = {item.stage for item in plan.imported_stages}
    if stage_kind == "factual":
        service._live_started = True
    payload = {"stage": stage, "reader_revision_policy": plan.reader_revision_policy,
        "revision_contract_sha256": plan.revision_contract_sha256,
        "research": {**research, "pending_coverage": []}}
    with pytest.raises(ValueError, match="bound source|bound source findings"):
        service.call_origin(role, payload, request)
    assert not provider.calls


def test_generic_policy_bytes_are_bound_to_plan_and_authorization(tmp_path):
    _, _, _, service, _ = _generic(tmp_path)
    with pytest.raises(ValueError, match="invalid generic revision policy"):
        assert_finalization_source_unchanged(replace(
            service.plan.plan, revision_contract_sha256="b" * 64))


@pytest.mark.parametrize("mutation", ["blank_rationale", "blank_witness", "duplicate_span",
                                      "duplicate_witness"])
def test_generic_finding_followup_requires_exact_nonblank_unique_witnesses(tmp_path, mutation):
    class BadFollowup(GenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(2)[1]:
                item = reply.data["source_finding_followups"][0]
                if mutation == "blank_rationale":
                    item["rationale"] = "   "
                elif mutation == "blank_witness":
                    item["witnesses"][0]["excerpt"] = " "
                elif mutation == "duplicate_span":
                    item["reader_excerpts"].append(item["reader_excerpts"][0])
                else:
                    item["witnesses"].append(dict(item["witnesses"][0]))
            return reply

    _, request, evidence, service, provider = _generic(tmp_path, BadFollowup())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == ("stage_failed" if mutation == "blank_rationale"
                                  else "verification_failed")
    assert len(provider.calls) >= 2
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]


def test_numbered_factual_wire_contract_is_strict_and_roundtrips():
    codec = codec_for("verifier", RevisionLifecycleVerification.model_json_schema())
    validate_strict_schema(codec.output_schema)
    output = {"schema_version": 1, "supported_claim_ids": [], "contradicted_claim_ids": [],
        "findings": [], "reviewed_report": True, "issue_resolutions": [],
        "finding_dispositions": [], "source_finding_followups": [{
            "schema_version": 1, "source_finding_sha256": "a" * 64,
            "disposition": "still_open", "rationale": "Needs a source-bound correction.",
            "reader_excerpts": [], "witnesses": [],
        }]}
    assert codec.decode(output)["source_finding_followups"][0]["source_finding_sha256"] == "a" * 64


def test_generation_names_remain_canonical_through_ten_and_hundred():
    stages = [SimpleNamespace(stage=name, role=role) for name, role in (
        ("revise_report", "editor"), ("verify_revised_report", "verifier"),
        ("verify_revised_report-coverage-0", "verifier"),
        ("verify_frozen_report", "verifier"),
        ("verify_frozen_report-coverage-0", "verifier"))]
    for generation in range(2, 10):
        writer, verifier = generation_stages(generation)
        stages.extend((SimpleNamespace(stage=writer, role="editor"),
                       SimpleNamespace(stage=verifier, role="verifier"),
                       SimpleNamespace(stage=verifier + "-coverage-0", role="verifier")))
    assert _generic_source_generation(stages, generation_stages(9)[1]) == (
        10, generation_stages(9)[0])
    assert generation_stages(10) == ("revise_report-10", "verify_revised_report-10")
    assert generic_writer_stage("revise_report-100")
    assert generic_review_stage("verify_revised_report-100")
    assert generic_coverage_stage("verify_revised_report-100-coverage-0")
    assert call_family("revise_report-10") == "writer"
    assert call_family("verify_revised_report-10") == "factual"
    assert call_family("verify_revised_report-10-coverage-0") == "coverage"
    assert not generic_writer_stage("revise_report-02")
    assert not generic_review_stage("verify_revised_report-01")
    assert not generic_coverage_stage("verify_revised_report-10-coverage-00")


@pytest.mark.parametrize("mutation", ["generation_hole", "huge_generation", "stage_collision", "missing_coverage",
                                      "wrong_writer", "changed_input", "active_dispatch",
                                      "unknown_usage"])
def test_generic_revision_rejects_malformed_source_before_dispatch(tmp_path, mutation):
    source, request, _, service, provider = _generic(tmp_path)
    checkpoint_path = source.output_dir / "finalization_checkpoint.json"
    checkpoint = read_json(checkpoint_path)
    if mutation == "generation_hole":
        checkpoint["stages"]["revise_report-3"] = checkpoint["stages"]["revise_report"]
    elif mutation == "huge_generation":
        checkpoint["stages"]["revise_report-100000000000000000000"] = checkpoint["stages"]["revise_report"]
    elif mutation == "stage_collision":
        checkpoint["stages"]["revise_report-2-coverage-0"] = checkpoint["stages"]["revise_report"]
    elif mutation == "missing_coverage":
        del checkpoint["stages"]["verify_frozen_report-coverage-0"]
    elif mutation == "wrong_writer":
        checkpoint["stages"]["revise_report"]["role"] = "verifier"
    elif mutation == "active_dispatch":
        checkpoint["dispatched"] = True
    elif mutation == "unknown_usage":
        checkpoint["usage"]["complete"] = False
    else:
        request.financial_case_path.write_bytes(b"changed")
    if mutation != "changed_input":
        checkpoint_path.write_bytes(canonical_json(checkpoint))
    with pytest.raises(ValueError):
        prepare_finalization_continuation(source.output_dir, request, revise_reader=True)
    assert not provider.calls


def test_generic_writer_boundary_and_lost_output_cannot_redispatch(tmp_path):
    from tradingagents.research.storage import CheckpointStore

    class LostOutput(CheckpointStore):
        def save_stage(self, stage, inputs, output):
            if stage == generation_stages(2)[0]:
                raise OSError("synthetic lost paid output")
            return super().save_stage(stage, inputs, output)

    _, request, evidence, service, provider = _generic(tmp_path)
    plan = service.plan.plan
    service.validate_request(request, plan.frozen_inputs)
    service._imported_stage_names = {plan.candidate_review_stage}
    with pytest.raises(ValueError, match="complete exact source prefix"):
        service.call_origin("editor", {"stage": generation_stages(2)[0]}, request)
    service._imported_stage_names = {item.stage for item in plan.imported_stages}
    with pytest.raises(ValueError, match="bound source candidate"):
        service.call_origin("editor", {"stage": generation_stages(2)[0],
            "reader_revision_policy": plan.reader_revision_policy,
            "revision_contract_sha256": plan.revision_contract_sha256, "research": {}}, request)
    service._imported_stage_names.clear()
    first = run_research(request, ResearchServices(evidence, service, storage=LostOutput))
    assert first.stop_reason == "stage_failed" and len(provider.calls) == 1
    next_provider = GenericFixture()
    resumed = run_research(request, ResearchServices(evidence,
        FinalizationRecoveryModelService(service.plan, next_provider)))
    assert resumed.stop_reason == "stage_failed" and not next_provider.calls
    assert resumed.usage == first.usage


def test_generic_writer_unknown_usage_cannot_redispatch(tmp_path):
    class UnknownUsage(GenericFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(2)[0]:
                return reply.model_copy(update={"usage": reply.usage.model_copy(update={"complete": False})})
            return reply

    _, request, evidence, service, provider = _generic(tmp_path, UnknownUsage())
    first = run_research(request, ResearchServices(evidence, service))
    assert not first.usage.complete and len(provider.calls) == 1
    next_provider = GenericFixture()
    resumed = run_research(request, ResearchServices(evidence,
        FinalizationRecoveryModelService(service.plan, next_provider)))
    assert not resumed.usage.complete and not next_provider.calls


def test_generic_revision_stops_on_tight_budget_and_prompt_before_writer(tmp_path):
    _, request, evidence, service, provider = _generic(tmp_path, tokens=30_000)
    stopped = run_research(request, ResearchServices(evidence, service))
    assert stopped.stop_reason == "revision_path_budget_insufficient"
    assert not provider.calls

    _, request, evidence, service, provider = _generic(tmp_path / "prompt")
    provider.max_prompt_utf8_bytes = 1
    service.max_prompt_utf8_bytes = 1
    stopped = run_research(request, ResearchServices(evidence, service))
    assert stopped.stop_reason == "revision_prompt_size_limit"
    assert not provider.calls

    _, request, evidence, service, provider = _generic(tmp_path / "time", wall_seconds=10)
    stopped = run_research(request, ResearchServices(evidence, service))
    assert stopped.stop_reason == "revision_path_time_insufficient"
    assert not provider.calls


def test_generic_baseline_factual_headroom_stops_before_paid_writer(tmp_path, monkeypatch):
    import tradingagents.research.engine as engine_module

    _, request, evidence, service, provider = _generic(tmp_path)
    service.max_prompt_utf8_bytes = 10_000_000
    original = engine_module.model_prompt

    def oversized_baseline(payload):
        prompt = original(payload)
        if payload.get("stage") == generation_stages(2)[1]:
            return prompt + b" " * 10_000_000
        return prompt

    monkeypatch.setattr(engine_module, "model_prompt", oversized_baseline)
    stopped = run_research(request, ResearchServices(evidence, service))
    assert stopped.stop_reason == "revision_future_factual_prompt_headroom_insufficient"
    assert not provider.calls
    admission = read_json(request.output_dir / "stages" / "revision-admission.json")["output"]
    assert admission["writer_prompt_admission"]["fits"]
    assert not admission["baseline_factual_prompt_admission"]["fits"]
