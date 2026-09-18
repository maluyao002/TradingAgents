from copy import deepcopy
from hashlib import sha256

import pytest

from tests.test_research_engine import replies, setup
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.replay import ReplayModelService
from tradingagents.research.services import ModelReply, ResearchServices
from tradingagents.research.storage import digest, read_json, request_identity


class BoundedFixture:
    kind = "replay"
    identity = digest("bounded-finalization-fixture-v1")

    def __init__(self, count=30, timeout=None, warning_once=False, followup_only=False, contradict=False):
        self.calls = []
        self.count = count
        self.timeout = timeout
        self.warning_once = warning_once
        self.followup_only = followup_only
        self.contradict = contradict

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        stage = payload["stage"]
        if stage == self.timeout:
            raise TimeoutError("Synthetic unavailable usage")
        data = deepcopy(replies()[role][0]["data"])
        if role == "business":
            data["followup_questions" if self.followup_only else "unresolved_gaps"] = [
                f"Economic unknown {i}" for i in range(self.count)]
        if role == "editor":
            data["sections"][0]["text"] = "Repaired reader." if stage == "repair_report" else "Original reader."
        if role == "verifier":
            data = {"reviewed_report": stage != "verify_claims"}
            issues = payload["research"].get("limitation_review")
            if issues is not None:
                data["limitation_dispositions"] = [{
                    "issue_id": item["issue_id"], "decision": "audit_only_operational",
                    "rationale": "Synthetic mechanics fixture only, not real materiality review.",
                    "reader_excerpt": "",
                } for item in issues]
            if self.warning_once and stage == "verify_report":
                data["findings"] = [{"code": "wording", "severity": "warning", "message": "Qualify original wording."}]
            if self.contradict and stage in {"verify_report", "verify_repaired_report"}:
                data["contradicted_claim_ids"] = ["reader-claim"]
        return ModelReply(data=data, usage={"input_tokens": 100, "output_tokens": 30})


def run_fixture(tmp_path, models):
    request, services = setup(tmp_path)
    request = request.model_copy(update={"quality_revision": "evidence-led-bounded", "report_language": "English"})
    services = ResearchServices(services.evidence, models)
    return request, services, run_research(request, services)


def test_bounded_revision_has_distinct_identity_and_preserves_all_open_issues(tmp_path):
    models = BoundedFixture(count=234)
    request, services, result = run_fixture(tmp_path, models)
    assert request_identity(request) != request_identity(request.model_copy(update={"quality_revision": "evidence-led"}))
    assert ResearchRequest.model_validate(request.model_dump()).quality_revision == "evidence-led-bounded"
    assert result.stop_reason == "completed_needs_review"
    stages = [payload["stage"] for _, payload in models.calls]
    assert "verify_investigations" not in stages
    assert "revision-0" not in stages  # Mandatory review capacity takes priority.
    ledger = read_json(request.output_dir / "investigation.json")["ledger"]
    assert len(ledger["entries"]) >= 234
    assert all(entry["status"] == "still_open" for entry in ledger["entries"])
    coverage = [payload for _, payload in models.calls if "-coverage-" in payload["stage"]]
    assert len(coverage) >= 20
    assert all(len(payload["research"]["limitation_review"]) <= 12 for payload in coverage)
    assert all("sources" not in payload["evidence"] for payload in coverage)
    reader = (request.output_dir / "reader_report.md").read_text()
    assert all(payload["research"]["rendered_reader"] == reader for payload in coverage)
    assert all(payload["research"]["rendered_reader_sha256"] == sha256(reader.encode()).hexdigest() for payload in coverage)
    assert read_json(request.output_dir / "reader_verification.json")["English"]["exported"]
    assert run_research(request, services) == result


def test_coverage_timeout_preserves_candidate_and_unknown_usage_blocks_retry(tmp_path):
    models = BoundedFixture(timeout="verify_report-coverage-1")
    request, services, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "stage_failed" and not result.usage.complete
    assert models.calls[-1][1]["stage"] == "verify_report-coverage-1"
    saved = read_json(request.output_dir / "stages/verify_report-reader-candidate.json")["output"]
    assert saved["verification_status"] == "unverified"
    assert "Original reader" in saved["reader_text"]
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not verification["exported"] and len(verification["coverage_batches"]) == 1
    assert "Diagnostic only" in (request.output_dir / "reader_report.md").read_text()
    count = len(models.calls)
    repeated = run_research(request, services)
    assert not repeated.usage.complete
    assert len(models.calls) == count


def test_repair_rechecks_every_issue_against_new_reader_bytes(tmp_path):
    models = BoundedFixture(count=15, warning_once=True)
    request, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "completed_needs_review"
    original = [p["research"]["rendered_reader_sha256"] for _, p in models.calls if p["stage"].startswith("verify_report-coverage-")]
    repaired = [p["research"]["rendered_reader_sha256"] for _, p in models.calls if p["stage"].startswith("verify_repaired_report-coverage-")]
    assert original and repaired and set(original).isdisjoint(repaired)
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert verification["reader_sha256"] == result.artifact_hashes["reader_report.md"]


def test_followup_cap_does_not_drop_mandatory_coverage_questions(tmp_path):
    models = BoundedFixture(count=40, followup_only=True)
    request, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "completed_needs_review"
    texts = {item["text"] for _, payload in models.calls
             for item in payload["research"].get("limitation_review", [])}
    assert {f"Economic unknown {i}" for i in range(40)} <= texts
    ledger = read_json(request.output_dir / "investigation.json")["ledger"]
    assert len(ledger["entries"]) > 20
    editor = next(p["research"] for _, p in models.calls if p["stage"] == "editor")
    assert {f"Economic unknown {i}" for i in range(40)} <= set(editor["limitations"])


def test_global_contradiction_blocks_export_even_without_a_finding(tmp_path):
    models = BoundedFixture(contradict=True)
    request, _, result = run_fixture(tmp_path, models)
    assert result.stop_reason == "verification_failed"
    assert sum(p["stage"] == "repair_report" for _, p in models.calls) == 1
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    assert not verification["exported"]
    assert any(f["code"] == "reader_contradicted_claims" for f in verification["review"]["findings"])


def test_optional_cycle_must_fit_after_finalization_token_allowance(tmp_path):
    first, _, _ = run_fixture(tmp_path / "first", BoundedFixture())
    plan = read_json(first.output_dir / "stages/finalization-plan-0.json")["output"]
    models = BoundedFixture()
    request, services = setup(tmp_path / "second")
    request = request.model_copy(update={
        "quality_revision": "evidence-led-bounded", "report_language": "English",
        "budget": request.budget.model_copy(update={
            "wall_seconds": 100_000, "total_tokens": plan["estimated_tokens"] + 2_000,
        }),
    })
    result = run_research(request, ResearchServices(services.evidence, models))
    assert result.stop_reason == "completed_needs_review"
    revised = read_json(request.output_dir / "stages/finalization-plan-0.json")["output"]
    assert revised["optional_cycle_skipped"]
    assert revised["optional_cycle_token_envelope"] > 2_000
    assert all(p["stage"] != "revision-0" for _, p in models.calls)


@pytest.mark.parametrize("severity", ["info", "warning"])
def test_legacy_gate_also_rejects_contradiction_with_lower_severity_match(tmp_path, severity):
    data = replies()
    data["verifier"][1]["data"].update({
        "contradicted_claim_ids": ["c"],
        "findings": [{"code": "reader_contradicted_claims", "affected_ids": ["c"],
                      "severity": severity, "message": "Not enough to block legacy by itself."}],
    })
    request, services = setup(tmp_path, data)
    result = run_research(request, ResearchServices(services.evidence, ReplayModelService(data)))
    assert result.stop_reason == "verification_failed"
