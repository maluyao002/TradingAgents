"""Offline v6 integration: inventory proof, fresh receipts, replay and resume."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_research_generic_revision import PendingGenericFixture
from tests.test_research_revision_source_witnesses import setup_claim, source
from tests.test_research_revision_v5 import V5FreshCoverage, _v5
from tradingagents.research.budget import BudgetExhausted
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import (
    FinalizationRecoveryModelService,
    assert_finalization_source_unchanged,
    authorize_finalization_continuation,
    prepare_finalization_continuation,
)
from tradingagents.research.reader_revision import generation_stages
from tradingagents.research.revision_contracts import (
    V3_CONTRACT,
    V4_CONTRACT,
    V5_CONTRACT,
    V6_CONTRACT,
)
from tradingagents.research.revision_inventory import inventory_finding_hashes
from tradingagents.research.revision_witness_selection import revision_witness_catalog
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import CheckpointStore, digest, read_json


@pytest.mark.parametrize("contract", [V3_CONTRACT, V4_CONTRACT, V5_CONTRACT])
def test_historical_witness_catalogs_ignore_new_case_locator_context(contract):
    snapshot, finding, issue = setup_claim(source())
    baseline = revision_witness_catalog(contract, snapshot, [finding], [issue])
    assert revision_witness_catalog(contract, snapshot, [finding], [issue],
                                    case_context=object()) == baseline


def test_v6_selector_binds_case_locator_without_mutating_context():
    doc = source()
    snapshot, finding, _ = setup_claim(doc)
    finding["affected_ids"] = [doc.id]
    material = [{"source_id": doc.id, "source_sha256": doc.content_sha256,
                 "start": 16, "end": 43, "text": doc.content[16:43]}]
    before = deepcopy(material)
    context = SimpleNamespace(operating_scenarios=SimpleNamespace(
        model_context={"source_material": material}))
    catalog = revision_witness_catalog(V6_CONTRACT, snapshot, [finding], [], case_context=context)
    reference = f"source_passage:{digest(finding)}:{doc.id}:{doc.content_sha256}:16:43"
    assert catalog[reference] == doc.content[16:43]
    assert material == before


class V5InventoryError(V5FreshCoverage):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"] == generation_stages(3)[1]:
            # An eligible origin batch has no current correction decorations.
            # Keep this separate substantive finding open for the next review.
            for item in reply.data["source_finding_followups"]:
                item["disposition"] = "still_open"
                item["reader_excerpts"] = []
                item["witnesses"] = []
        if payload["stage"].startswith(generation_stages(3)[1] + "-coverage-"):
            # Change only a response identifier, never its meaning or the reader.
            candidates = [item for item in payload["research"]["limitation_review"]
                          if item["reader_coverage_required"] is True]
            assert candidates
            target = candidates[0]["issue_id"]
            next(item for item in reply.data["limitation_dispositions"]
                 if item["issue_id"] == target)["issue_id"] = "foreign-uninterpreted-id"
        return reply


class V6Fixture(PendingGenericFixture):
    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if payload["stage"].startswith("verify_revised_report-") and "-coverage-" not in payload["stage"]:
            deferred = inventory_finding_hashes(payload["research"].get("pending_inventory", ()))
            reply.data["source_finding_followups"] = [
                item for item in reply.data["source_finding_followups"]
                if item["source_finding_sha256"] not in deferred]
        return reply


def _v6(tmp_path, provider=None):
    _, source, _, evidence, source_service, _ = _v5(tmp_path, V5InventoryError())
    result = run_research(source, ResearchServices(evidence, source_service))
    assert result.stop_reason == "verification_failed"
    destination = source.model_copy(update={"output_dir": tmp_path / "v6-destination"})
    plan = prepare_finalization_continuation(source.output_dir, destination, revise_reader=True)
    authorization = source_service.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget,
    })
    provider = provider or V6Fixture()
    service = FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)
    return destination, evidence, service, provider


def test_v6_replays_v5_and_closes_inventory_pair_only_after_full_coverage(tmp_path):
    request, evidence, service, provider = _v6(tmp_path)
    plan = service.plan.plan
    assert plan.reader_revision_policy == V6_CONTRACT.policy
    assert plan.prior_revision_contracts[-1]["contract_sha256"] == V5_CONTRACT.sha256
    assert len(plan.pending_inventory) == 1
    expected = plan.pending_inventory[0]["expected_issue_ids"]
    before = plan.source_artifact_hashes.copy()
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "completed_needs_review", read_json(request.output_dir / "run_metadata.json")
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    lifecycle = terminal["issue_lifecycle"]
    assert terminal["exported"]
    assert lifecycle["pending_inventory_unresolved"] == []
    assert len(lifecycle["inventory_receipts"]) == 1
    assert lifecycle["inventory_receipts"][0]["expected_issue_ids"] == expected
    assert len(lifecycle["inventory_receipts"][0]["source_finding_hashes"]) == 2
    assert [payload["stage"] for _, payload in provider.calls][0] == generation_stages(4)[0]
    factual = next(payload for _, payload in provider.calls if payload["stage"] == generation_stages(4)[1])
    assert factual["research"]["pending_inventory"] == list(plan.pending_inventory)
    for payload in (provider.calls[0][1], factual):
        assert payload["research"]["source_text_witnesses"] == plan.source_witness_catalog
        assert digest(payload["research"]["source_text_witnesses"]) == plan.source_witness_catalog_sha256
    assert not inventory_finding_hashes(plan.pending_inventory).intersection(
        item["source_finding_sha256"] for item in lifecycle["source_finding_followups"])
    assert_finalization_source_unchanged(plan)
    assert plan.source_artifact_hashes == before


def test_v6_cannot_dispatch_from_saved_hashes_without_reconstructed_inventory(tmp_path):
    request, _, service, provider = _v6(tmp_path)
    plan = service.plan.plan
    service.validate_request(request, plan.frozen_inputs)
    service._imported_stage_names = {item.stage for item in plan.imported_stages}
    with pytest.raises(ValueError):
        service._require_candidate_boundary()
    assert not provider.calls


def test_v6_rejects_changed_envelope_even_with_rehashed_plan(tmp_path):
    _, _, service, _ = _v6(tmp_path)
    plan = service.plan.plan
    entry = deepcopy(plan.pending_inventory[0])
    entry["origin_issues"][0]["text"] += " weakened"
    entry["envelope_sha256"] = digest({key: value for key, value in entry.items()
                                      if key != "envelope_sha256"})
    changed = replace(plan, pending_inventory=(entry,), pending_inventory_sha256=digest((entry,)))
    with pytest.raises(ValueError):
        assert_finalization_source_unchanged(changed)


def test_v6_factual_cannot_retire_original_inventory_obligation(tmp_path):
    class Retires(V6Fixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(4)[1]:
                identifier = payload["research"]["pending_inventory"][0]["expected_issue_ids"][0]
                next(item for item in reply.data["issue_resolutions"]
                     if item["issue_id"] == identifier)["status"] = "superseded"
            return reply

    request, evidence, service, provider = _v6(tmp_path, Retires())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason != "completed_needs_review"
    assert not any("-coverage-" in payload["stage"] for _, payload in provider.calls)


def _next(request, service, provider, name):
    destination = request.model_copy(update={"output_dir": request.output_dir.parent / name})
    plan = prepare_finalization_continuation(request.output_dir, destination, revise_reader=True)
    authorization = service.plan.authorization.model_copy(update={
        "plan_sha256": plan.plan_sha256, "new_request_identity": plan.new_request_identity,
        "incremental_budget": destination.budget,
    })
    return destination, FinalizationRecoveryModelService(
        authorize_finalization_continuation(plan, authorization), provider)


def test_v6_unresolved_pair_carries_atomically_into_next_generation(tmp_path):
    class StillOpen(V6Fixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"].startswith(generation_stages(4)[1] + "-coverage-"):
                item = reply.data["limitation_dispositions"][0]
                item.update(decision="unresolved", reader_excerpt="", reader_excerpts=[])
            return reply

    request, evidence, service, _ = _v6(tmp_path, StillOpen())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    lifecycle = read_json(request.output_dir / "reader_verification.json")["English"]["issue_lifecycle"]
    assert lifecycle["inventory_receipts"] == []
    assert set(lifecycle["pending_inventory_unresolved"]) == inventory_finding_hashes(
        service.plan.plan.pending_inventory)
    provider = V6Fixture()
    destination, continuation = _next(request, service, provider, "v6-next")
    assert continuation.plan.plan.pending_inventory == service.plan.plan.pending_inventory
    assert len(continuation.plan.plan.prior_pending_inventory) == 1
    result = run_research(destination, ResearchServices(evidence, continuation))
    assert result.stop_reason == "completed_needs_review"
    terminal = read_json(destination.output_dir / "reader_verification.json")["English"]
    assert terminal["exported"]
    assert terminal["issue_lifecycle"]["pending_inventory_unresolved"] == []
    assert len(terminal["issue_lifecycle"]["inventory_receipts"]) == 1
    assert provider.calls[0][1]["stage"] == generation_stages(5)[0]


def test_v6_valid_inventory_does_not_erase_unrelated_finding(tmp_path):
    class OtherWarning(V6Fixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if payload["stage"] == generation_stages(4)[1]:
                reply.data["findings"] = [*reply.data.get("findings", ()), {
                    "code": "unrelated_material_scope", "severity": "warning",
                    "category": "research", "message": "An independent material issue remains.",
                    "affected_ids": [],
                }]
            return reply

    request, evidence, service, _ = _v6(tmp_path, OtherWarning())
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_failed"
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    assert len(terminal["issue_lifecycle"]["inventory_receipts"]) == 1
    assert any(item["code"] == "unrelated_material_scope" for item in terminal["review"]["findings"])
    destination, continuation = _next(request, service, V6Fixture(), "v6-after-receipt")
    assert continuation.plan.plan.pending_inventory == ()
    assert len(continuation.plan.plan.prior_pending_inventory) == 1
    assert run_research(destination, ResearchServices(evidence, continuation)).stop_reason == (
        "completed_needs_review")


def test_v6_predispatch_resume_reproves_cached_prefix_without_repaying(tmp_path, monkeypatch):
    request, evidence, service, provider = _v6(tmp_path)
    original_save = CheckpointStore.save_stage
    cost_stage = generation_stages(4)[1] + "-cost-plan"
    count = 0

    def stop_before_coverage(store, stage, inputs, output):
        nonlocal count
        if stage == cost_stage:
            count += 1
            if count == 2:
                raise BudgetExhausted("synthetic_predispatch_stop")
        return original_save(store, stage, inputs, output)

    monkeypatch.setattr(CheckpointStore, "save_stage", stop_before_coverage)
    first = run_research(request, ResearchServices(evidence, service))
    assert first.stop_reason == "synthetic_predispatch_stop" and first.usage.complete
    assert [payload["stage"] for _, payload in provider.calls] == list(generation_stages(4))
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not terminal["exported"]
    assert not terminal["issue_lifecycle"].get("inventory_receipts")
    monkeypatch.setattr(CheckpointStore, "save_stage", original_save)
    resumed_provider = V6Fixture()
    resumed_service = FinalizationRecoveryModelService(service.plan, resumed_provider)
    resumed = run_research(request, ResearchServices(evidence, resumed_service))
    assert resumed.stop_reason == "completed_needs_review"
    assert resumed_service._inventory_prefix_verified
    assert resumed_provider.calls and all(payload["stage"].startswith(
        generation_stages(4)[1] + "-coverage-") for _, payload in resumed_provider.calls)
    terminal = read_json(request.output_dir / "reader_verification.json")["English"]
    assert terminal["exported"] and len(terminal["issue_lifecycle"]["inventory_receipts"]) == 1
