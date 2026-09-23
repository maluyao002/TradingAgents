"""Matched offline coverage-policy runs; never a live quality/latency claim."""

import json
from hashlib import sha256

import pytest

from tests.test_research_bounded_finalization import BoundedFixture
from tests.test_research_case_engine import CaseFixture, case_setup
from tests.test_research_engine import setup
from tests.test_research_finalization_engine import _budget_stopped_case
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.finalization_recovery import prepare_finalization_continuation
from tradingagents.research.prompt_context import (
    CONTEXT_POLICY,
    _shared_packet,
    expand_prompt_context,
    model_boundary,
    model_input_bytes,
)
from tradingagents.research.review_lifecycle import _OPERATING_REVIEW_BOUNDARY
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, digest, read_json, request_identity


def run_policy(path, policy, models):
    request, services = setup(path)
    request = request.model_copy(update={
        "quality_revision": "evidence-led-bounded", "report_language": "English",
        "coverage_batch_policy": policy,
    })
    services = ResearchServices(services.evidence, models)
    return request, services, run_research(request, services)


def test_matched_packed_run_reduces_calls_not_obligations_or_reader_checks(tmp_path):
    observed = {}
    for policy in ("legacy-12", "packed-24"):
        models = BoundedFixture(count=182)
        request, _, result = run_policy(tmp_path / policy, policy, models)
        assert result.stop_reason == "completed_needs_review"
        batches = [payload for _, payload in models.calls if "-coverage-" in payload["stage"]]
        reader = (request.output_dir / "reader_report.md").read_text()
        assert all(p["research"]["rendered_reader"] == reader for p in batches)
        assert all(p["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest()
                   for p in batches)
        factual = [p for _, p in models.calls if p["stage"] == "verify_report"]
        assert len(factual) == 1 and "sources" in factual[0]["evidence"]
        coverage = read_json(request.output_dir / "reader_verification.json")["English"]
        assert coverage["exported"]
        observed[policy] = {
            "reader": reader,
            "ids": [identifier for b in coverage["coverage_batches"] for identifier in b["issue_ids"]],
            "calls": len(batches),
            "bytes": sum(model_input_bytes(p, role="verifier", output_token_envelope=p["max_output_tokens"],
                                           valuation_method=request.valuation_method) for p in batches),
        }
    old, new = observed["legacy-12"], observed["packed-24"]
    assert new["reader"] == old["reader"]
    assert new["ids"] == old["ids"] and len(new["ids"]) >= 182
    assert new["calls"] < old["calls"]
    assert new["bytes"] < old["bytes"]


def test_packed_repair_plans_and_dispatch_use_scaled_output_allowance(tmp_path):
    models = BoundedFixture(count=30, warning_once=True)
    request, _, result = run_policy(tmp_path, "packed-24", models)
    assert result.stop_reason == "completed_needs_review"
    batches = [p for _, p in models.calls if "-coverage-" in p["stage"]]
    assert all(p["max_output_tokens"] == 12_000 and p["coverage_batch_policy"] == "packed-24"
               for p in batches)
    plans = read_json(request.output_dir / "finalization_plan.json")
    dispatched = {payload["stage"]: payload for _, payload in models.calls if "-coverage-" in payload["stage"]}
    for stage in ("verify_report", "verify_repaired_report"):
        plan = plans[stage]
        assert plan["coverage_batch_policy"] == "packed-24"
        for call in plan["remaining_workload"]["calls"]:
            if "-coverage-" in call["call_id"]:
                assert call["output_token_envelope"] == 12_000
                assert call["conservative_reserve_tokens"] == call["serialized_input_bytes"] + 12_000
        for call in plan["current_pass"]["calls"]:
            payload = dispatched[call["call_id"]]
            exact_bytes = model_input_bytes(
                payload, role="verifier", output_token_envelope=12_000,
                valuation_method=request.valuation_method,
            )
            assert call["serialized_input_bytes"] == exact_bytes
            assert call["conservative_reserve_tokens"] == exact_bytes + 12_000
    assert plans["verify_report"]["reader_sha256"] != plans["verify_repaired_report"]["reader_sha256"]
    precheck = plans["reverification_admission"]["remaining_path"]["calls"]
    assert all(c["output_token_envelope"] == 12_000 for c in precheck if "-coverage-" in c["call_id"])
    all_payloads = {p["stage"]: p for _, p in models.calls}
    for call in precheck:
        payload = all_payloads[call["call_id"]]
        assert call["serialized_input_bytes"] == model_input_bytes(
            payload, role="verifier", output_token_envelope=call["output_token_envelope"],
            valuation_method=request.valuation_method,
        )
    repair = next(c for c in plans["repair_admission"]["repair_path"]["calls"]
                  if c["call_id"] == "repair_report")
    assert repair["serialized_input_bytes"] == model_input_bytes(
        all_payloads["repair_report"], role="editor", output_token_envelope=16_000,
        valuation_method=request.valuation_method,
    )


def test_legacy_provider_boundary_preserves_feature_base_content_under_v2(tmp_path):
    # Feature-base 3ec145a used v1 packing. Its domain content and trusted
    # instruction/schema are unchanged; only the lossless prompt encoding moved.
    models = BoundedFixture(count=182)
    request, _, result = run_policy(tmp_path, "legacy-12", models)
    assert result.stop_reason == "completed_needs_review"
    payload = next(p for _, p in models.calls if p["stage"] == "verify_report-coverage-0")
    boundary = model_boundary("verifier", payload, output_token_envelope=6_000,
                              valuation_method=request.valuation_method)
    domain = {key: value for key, value in payload.items()
              if key not in {"system", "response_schema", "timeout_seconds", "max_output_tokens"}}
    v1 = {"context_encoding": "exact-shared-context-v1", "context_policy": CONTEXT_POLICY,
          **_shared_packet(domain)}
    historical_prompt = min((canonical_json(domain), canonical_json(v1)), key=len)
    assert digest({"instructions": boundary.instructions, "prompt_utf8": historical_prompt.decode(),
                   "output_schema": boundary.output_schema}) == (
        "c1c9cbad5ab5ec9cd7670cf396976c6ff43ef72fbcb5b81ac464300e8703da0d")
    assert canonical_json(expand_prompt_context(json.loads(boundary.prompt))) == canonical_json(domain)
    assert digest({"instructions": boundary.instructions, "prompt_utf8": boundary.prompt.decode(),
                   "output_schema": boundary.output_schema}) == (
        "ebd1334b526487fb65c84d8fd9d374b7bc4c567e691c8777b94d18eb1eb99fe2")


def test_packed_failure_preserves_unknown_usage_and_cannot_retry(tmp_path):
    models = BoundedFixture(count=50, timeout="verify_report-coverage-1")
    request, services, result = run_policy(tmp_path, "packed-24", models)
    assert result.stop_reason == "stage_failed" and not result.usage.complete
    assert not read_json(request.output_dir / "reader_verification.json")["English"]["exported"]
    calls = len(models.calls)
    assert not run_research(request, services).usage.complete
    assert len(models.calls) == calls


def test_policy_is_identity_bound_and_only_allowed_for_bounded_workflow(tmp_path):
    request, _ = setup(tmp_path)
    raw = request.model_dump(mode="json")
    raw.pop("coverage_batch_policy")
    assert request_identity(ResearchRequest.model_validate(raw)) == request_identity(request)
    with pytest.raises(ValueError, match="bounded"):
        ResearchRequest.model_validate({**raw, "coverage_batch_policy": "packed-24"})
    bounded = {**raw, "quality_revision": "evidence-led-bounded"}
    old = ResearchRequest.model_validate(bounded)
    packed = ResearchRequest.model_validate({**bounded, "coverage_batch_policy": "packed-24"})
    assert request_identity(old) != request_identity(packed)


def test_exact_candidate_continuation_rejects_policy_change(tmp_path):
    source, _, _, _ = _budget_stopped_case(tmp_path)
    with pytest.raises(ValueError, match="settings"):
        prepare_finalization_continuation(source.output_dir, source.model_copy(update={
            "output_dir": tmp_path / "continued", "coverage_batch_policy": "packed-24",
        }))


def test_packed_missing_coverage_disposition_blocks_export_after_repair(tmp_path):
    class MissingCoverage(BoundedFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if "-coverage-" in payload["stage"]:
                reply.data["limitation_dispositions"].pop()
            return reply

    models = MissingCoverage(count=30, warning_once=True)
    request, _, result = run_policy(tmp_path, "packed-24", models)

    stages = [payload["stage"] for _, payload in models.calls]
    assert result.stop_reason == "verification_failed"
    assert "repair_report" in stages and "verify_repaired_report" in stages
    assert any(stage.startswith("verify_repaired_report-coverage-") for stage in stages)
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not verification["exported"]
    assert any(finding["code"] == "limitation_disposition" for finding in verification["review"]["findings"])


def test_packed_clean_same_reader_reuses_validated_coverage(tmp_path):
    class SameReader(BoundedFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["text"] = "Unchanged reader."
            return reply

    models = SameReader(count=30, warning_once=True)
    request, _, result = run_policy(tmp_path, "packed-24", models)

    assert result.stop_reason == "completed_needs_review"
    assert not any(payload["stage"].startswith("verify_repaired_report-coverage-")
                   for _, payload in models.calls)
    coverage = [payload for _, payload in models.calls if "-coverage-" in payload["stage"]]
    assert coverage and all(payload["coverage_batch_policy"] == "packed-24" for payload in coverage)
    metadata = read_json(request.output_dir / "run_metadata.json")
    reused = [row for stage, rows in metadata["usage_by_stage"].items()
              if stage.startswith("verify_repaired_report-coverage-") for row in rows]
    assert reused and all(row["usage_origin"] == "validated_reuse" for row in reused)
    repaired_pass = read_json(request.output_dir / "finalization_plan.json")[
        "verify_repaired_report"
    ]["current_pass"]
    assert repaired_pass["cached_call_count"] == len(repaired_pass["calls"])
    assert repaired_pass["dispatch_call_count"] == repaired_pass["conservative_reserve_tokens"] == 0


def test_packed_changed_reader_bytes_force_full_coverage_reverification(tmp_path):
    models = BoundedFixture(count=30, warning_once=True)
    _, _, result = run_policy(tmp_path, "packed-24", models)

    assert result.stop_reason == "completed_needs_review"
    initial = [payload for _, payload in models.calls
               if payload["stage"].startswith("verify_report-coverage-")]
    repaired = [payload for _, payload in models.calls
                if payload["stage"].startswith("verify_repaired_report-coverage-")]
    assert initial and len(repaired) == len(initial)
    assert {payload["research"]["rendered_reader_sha256"] for payload in initial}.isdisjoint(
        {payload["research"]["rendered_reader_sha256"] for payload in repaired}
    )
    assert [item["issue_id"] for payload in initial for item in payload["research"]["limitation_review"]] == [
        item["issue_id"] for payload in repaired for item in payload["research"]["limitation_review"]
    ]


def test_packed_case_fixture_covers_compound_issue_components_offline(tmp_path):
    class CompoundCaseFixture(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "business":
                reply.data["unresolved_gaps"] = [_OPERATING_REVIEW_BOUNDARY]
            return reply

    models = CompoundCaseFixture()
    request, services = case_setup(tmp_path, models)
    request = request.model_copy(update={"coverage_batch_policy": "packed-24"})

    result = run_research(request, services)

    assert result.stop_reason == "completed_needs_review"
    coverage = [payload for _, payload in models.calls if "-coverage-" in payload["stage"]]
    assert coverage and all(payload["coverage_batch_policy"] == "packed-24" for payload in coverage)
    components = [item for payload in coverage for item in payload["research"]["limitation_review"]
                  if "coverage_component" in item]
    assert components and all("#component:" in item["issue_id"] for item in components)
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["exported"]
