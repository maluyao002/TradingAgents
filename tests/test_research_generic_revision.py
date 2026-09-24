"""Numbered candidate revision uses only offline synthetic replies."""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest

from tests.test_research_case_engine import CaseFixture
from tests.test_research_verification_repair import _verification
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
    GENERIC_REVISION_POLICY,
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
from tradingagents.research.storage import canonical_json, digest, read_json
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
    assert service.plan.plan.reader_revision_policy == GENERIC_REVISION_POLICY
    assert service.plan.plan.manifest()["revision_contract_sha256"] == (
        service.plan.plan.revision_contract_sha256)
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review"
    calls = [payload for _, payload in provider.calls]
    writer, verifier = generation_stages(2)
    assert [payload["stage"] for payload in calls[:2]] == [writer, verifier]
    assert all(payload["stage"].startswith(verifier + "-coverage-") for payload in calls[2:])
    assert all(payload["reader_revision_policy"] == GENERIC_REVISION_POLICY for payload in calls)
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
            "reader_revision_policy": GENERIC_REVISION_POLICY,
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
