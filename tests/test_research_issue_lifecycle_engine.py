"""Offline report-lifecycle integration; synthetic judgments are not acceptance."""

from hashlib import sha256
from pathlib import Path

import pytest

from tests.test_research_case_engine import CaseFixture, case_setup
from tradingagents.research.engine import run_research
from tradingagents.research.storage import read_json

OLD = "Historical claim says supplied revenue is missing."
CORRECTED = "Reported revenue is supplied; this remains conditional research, not a valuation."
WITHHELD = "Equity value and company-wide funding adequacy are unavailable."


@pytest.mark.parametrize("mutation", [None, "unrelated_witness", "wrong_package", "protected_collision"])
def test_operating_metadata_retirement_requires_current_review_and_all_origins(mutation):
    from tradingagents.research.report_review import limitation_packet
    from tradingagents.research.review_lifecycle import (
        LifecycleVerification,
        enrich_issues,
        reconcile_review,
    )

    text = "Historical operating note"
    origin = {"origin_id": "operating.package.0", "retirable": True,
              "required_witness_reference": "review:operating_scenarios", "package_sha256": "a" * 64}
    origins = [origin]
    if mutation == "protected_collision":
        origins.append({"origin_id": "case.scope.0", "retirable": False,
                        "required_witness_reference": None, "package_sha256": None})
    packet = enrich_issues(limitation_packet([text]), {}, [], limitation_origins={text: origins})
    current = '{"reviewed":true,"package_sha256":"' + "a" * 64 + '"}'
    evidence = {"review:operating_scenarios": current, "fact:unrelated": "123"}
    witness = {"reference": "review:operating_scenarios", "excerpt": current}
    if mutation == "unrelated_witness":
        witness = {"reference": "fact:unrelated", "excerpt": "123"}
    elif mutation == "wrong_package":
        origin["package_sha256"] = "b" * 64
        packet = enrich_issues(limitation_packet([text]), {}, [], limitation_origins={text: origins})
    review = LifecycleVerification(reviewed_report=True, issue_resolutions=[{
        "issue_id": packet[0]["issue_id"], "status": "superseded", "rationale": "Current review replaces old note.",
        "reader_excerpts": [CORRECTED], "witnesses": [witness],
    }])
    active, remaining, ledger = reconcile_review(review, packet, evidence, CORRECTED, None)
    if mutation is None:
        assert not active.findings and not remaining
        assert ledger["retired_issue_ids"] == [packet[0]["issue_id"]]
    else:
        assert active.findings and remaining and not ledger["retired_issue_ids"]


class LifecycleFixture(CaseFixture):
    def __init__(self, *, resolve=True, scoped=False, forged=False):
        super().__init__()
        self.resolve = resolve
        self.scoped = scoped
        self.forged = forged

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        stage, research = payload["stage"], payload["research"]
        data = reply.data
        if stage == "verify_claims" and not self.scoped:
            data["findings"] = [{"code": "old_revenue_absence", "severity": "warning",
                                 "message": OLD, "category": "data"}]
        if role == "editor":
            data["sections"][0]["text"] = CORRECTED + " " + WITHHELD
        if stage in {"verify_report", "verify_repaired_report"}:
            if self.resolve and not self.scoped:
                issue = next(i for i in research["inherited_issues"] if i["text"] == OLD)
                reference = next(iter(research["resolution_evidence"]))
                evidence = research["resolution_evidence"][reference]
                data["issue_resolutions"] = [{
                    "issue_id": issue["issue_id"], "status": "superseded",
                    "rationale": "Synthetic evidence now supplies the previously missing input.",
                    "reader_excerpts": [CORRECTED],
                    "witnesses": [{"reference": "forged" if self.forged else reference,
                                   "excerpt": evidence}],
                }]
            if self.scoped:
                data["findings"] = [{"code": "valuation_unavailable", "severity": "critical",
                    "message": "The reader correctly withholds equity and funding conclusions.",
                    "category": "data"}]
                data["finding_dispositions"] = [{
                    "finding_code": "valuation_unavailable", "disposition": "disclosed_limitation",
                    "rationale": "Explicitly withheld conclusions remain deterministically unavailable.",
                    "reader_excerpts": [WITHHELD],
                    "conclusion_scopes": ["equity_per_share_value", "funding_assessment"],
                }]
        return reply.model_copy(update={"data": data})


def test_stale_warning_is_repaired_with_hash_bound_history_and_no_sticky_admission(tmp_path):
    models = LifecycleFixture()
    request, services = case_setup(tmp_path, models)
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert OLD not in reader and CORRECTED in reader
    stages = [payload["stage"] for _, payload in models.calls]
    assert "repair_report" in stages
    verification = read_json(request.output_dir / "reader_verification.json")["English"]
    ledger = verification["issue_lifecycle"]
    assert ledger["reader_sha256"] == sha256(reader.encode()).hexdigest()
    assert next(i for i in ledger["issues"] if i["text"] == OLD)["status"] == "superseded"
    assert any(f.code == "old_revenue_absence" for f in result.assessment.findings)
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "complete"
    assert OLD in (request.output_dir / "reader_limitations.json").read_text()
    assert read_json(request.output_dir / "stages/verify_report-reader-candidate.json")["output"][
        "reader_text"].find(OLD) >= 0


@pytest.mark.parametrize("options", [{"resolve": False}, {"forged": True}])
def test_unresolved_or_forged_prior_warning_still_blocks(tmp_path, options):
    request, services = case_setup(tmp_path, LifecycleFixture(**options))
    result = run_research(request, services)
    assert result.assessment.status == "incomplete"
    assert read_json(request.output_dir / "report_admission.json")["report_completion"] == "incomplete"
    assert not (request.output_dir / "stages/completed-result.json").exists()


def test_scoped_withheld_conclusions_allow_reader_but_never_model_acceptance(tmp_path):
    request, services = case_setup(tmp_path, LifecycleFixture(scoped=True))
    result = run_research(request, services)
    assert result.stop_reason == "completed_needs_review"
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["report_completion"] == "complete"
    assert admission["acceptance_eligibility"]["status"] == "blocked"
    assert admission["model_conclusions"]["equity_per_share_value"]["status"] == "blocked"
    assert admission["model_conclusions"]["funding_assessment"]["status"] == "blocked"
    assert not admission["production_activation"]
    lifecycle = read_json(request.output_dir / "reader_verification.json")["English"]["issue_lifecycle"]
    assert lifecycle["scoped_findings"][0]["finding"]["severity"] == "critical"


@pytest.mark.parametrize("mutation", [None, "not_exported", "wrong_hash"])
def test_retired_operating_notice_is_audit_only_not_reported_as_reader_content(tmp_path, monkeypatch, mutation):
    from tests.test_research_case_scenarios import operating_setup
    from tradingagents.research import engine
    from tradingagents.research.operating_scenarios import (
        _LEGACY_REVIEW_STATUS_LIMITATION,
        operating_scenario_package_sha256,
    )
    from tradingagents.research.replay import SnapshotEvidenceService
    from tradingagents.research.services import ResearchServices
    from tradingagents.research.storage import canonical_json

    request, snapshot, package = operating_setup(tmp_path)
    package = package.model_copy(update={
        "limitations": (*package.limitations, _LEGACY_REVIEW_STATUS_LIMITATION),
    })
    package = package.model_copy(update={"review": package.review.model_copy(update={
        "package_sha256": operating_scenario_package_sha256(package),
    })})
    envelope = read_json(request.financial_case_path)
    envelope["operating_scenarios"] = package.model_dump(mode="json")
    request.financial_case_path.write_bytes(canonical_json(envelope))
    if mutation == "wrong_hash":
        reconcile = engine.reconcile_review

        def wrong_hash(*args):
            active, remaining, ledger = reconcile(*args)
            ledger["reader_sha256"] = "0" * 64
            return active, remaining, ledger

        monkeypatch.setattr(engine, "reconcile_review", wrong_hash)

    class MetadataFixture(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            if role == "editor":
                reply.data["sections"][0]["text"] = CORRECTED
            if payload["stage"] in {"verify_report", "verify_repaired_report"}:
                research = payload["research"]
                issue = next(i for i in research["inherited_issues"]
                             if i["text"] == _LEGACY_REVIEW_STATUS_LIMITATION)
                reply.data["issue_resolutions"] = [{
                    "issue_id": issue["issue_id"], "status": "superseded",
                    "rationale": "Current independent review supersedes draft-status metadata.",
                    "reader_excerpts": [CORRECTED], "witnesses": [{
                        "reference": "review:operating_scenarios",
                        "excerpt": research["resolution_evidence"]["review:operating_scenarios"],
                    }],
                }]
                if mutation == "not_exported":
                    reply.data["findings"] = [{
                        "code": "terminal_defect", "severity": "critical", "category": "numerical",
                        "message": "Synthetic numerical defect prevents export.",
                    }]
            return reply

    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), MetadataFixture()))
    assert result.stop_reason == ("verification_failed" if mutation == "not_exported"
                                  else "completed_needs_review")
    reader = (request.output_dir / "reader_report.md").read_text()
    audit = read_json(request.output_dir / "reader_limitations.json")
    issue = next(i for i in audit["unresolved_issues"]["consolidated_exact_text"]
                 if i["original_text"] == _LEGACY_REVIEW_STATUS_LIMITATION)
    if mutation:
        verification = read_json(request.output_dir / "reader_verification.json")["English"]
        assert verification["exported"] is (mutation != "not_exported")
        assert issue["reader_display"] != "audit_only_retired"
        assert "lifecycle_status" not in issue
        return
    assert _LEGACY_REVIEW_STATUS_LIMITATION not in reader
    assert issue["displayed_in_reader"] is False
    assert issue["reader_display"] == "audit_only_retired"
    assert issue["lifecycle_status"] == "superseded"
    assert issue["issue_id"] not in audit["unresolved_issues"]["unrepresented_issue_ids"]
    assert audit["exported_reader_sha256"] == sha256(reader.encode()).hexdigest()
    # Current economic caveats are still present and correctly marked as displayed.
    current = next(i for i in audit["unresolved_issues"]["consolidated_exact_text"]
                   if i["original_text"] == package.limitations[0])
    assert current["displayed_in_reader"] is True and package.limitations[0] in reader


def test_saved_nvda_opaque_claims_are_enriched_without_changing_source(tmp_path):
    from tradingagents.research.contracts import ReviewFinding
    from tradingagents.research.report_review import (
        ReaderVerification,
        limitation_packet,
        validated_disposition_ids,
    )
    from tradingagents.research.review_batches import coverage_batches
    from tradingagents.research.review_lifecycle import enrich_issues
    from tradingagents.research.storage import digest

    directory = Path("reports/RESEARCH_STAGE3_20260919/live_continuation_run_2")
    if not directory.exists():
        pytest.skip("optional frozen NVDA artifacts are absent")
    source = directory / "reader_verification.json"
    before = source.read_bytes()
    data = read_json(source)["English"]
    old_review = read_json(directory / "stages/verify_claims.json")["output"]
    analyses = read_json(directory / "research.json")
    # Recover the exact historical issue texts from the audit rather than infer
    # propositions from a hash. Every coverage batch is unchanged on disk.
    required = set(data["required_limitation_ids"])
    audit = read_json(directory / "reader_limitations.json")
    gaps = [item["original_text"] for item in audit["unresolved_issues"]["consolidated_exact_text"]
            if "limitation-" + digest(item["original_text"]) in required]
    packet = enrich_issues(limitation_packet(gaps), analyses,
                           [ReviewFinding.model_validate(f) for f in old_review["findings"]])
    assert len(packet) == len(required) == 205
    assert all(not item["missing_claim_ids"] for item in packet)
    assert sum(bool(item["claims"]) for item in packet) == 20
    assert len(coverage_batches(packet)) == 18
    reader = read_json(directory / "stages/verify_report-reader-candidate.json")["output"]["reader_text"]
    assert len(validated_disposition_ids(ReaderVerification.model_validate(data["review"]), packet, reader)) == 112
    # This diagnoses deterministic traceability, not a new factual review or an
    # accepted export. Original unknown usage and failed admission remain intact.
    assert read_json(directory / "report_admission.json")["report_completion"] == "incomplete"
    assert source.read_bytes() == before


def test_saved_reviewed_operating_metadata_can_retire_without_clearing_financial_case(tmp_path):
    from cli.research import load_request
    from tests.test_research_financial_case import _snapshot
    from tradingagents.research.replay import SnapshotEvidenceService
    from tradingagents.research.services import ResearchServices

    config = Path("reports/RESEARCH_STAGE3_20260919/live_plan_1/request.json")
    if not config.exists():
        pytest.skip("optional reviewed NVDA operating package is absent")
    original = load_request(config)
    source_before = original.financial_case_path.read_bytes()
    corrected = "The operating package received independent review; valuation and funding remain withheld."

    class ReviewedMetadata(CaseFixture):
        def complete(self, role, payload, request):
            reply = super().complete(role, payload, request)
            data = reply.data
            if role == "editor":
                data["sections"][0]["text"] = corrected
            if payload["stage"] in {"verify_report", "verify_repaired_report"}:
                research = payload["research"]
                issue = next(i for i in research["inherited_issues"]
                             if i["text"].startswith("Draft-only package;"))
                assert issue["origins"] and not issue["resolution_protected"]
                data["issue_resolutions"] = [{
                    "issue_id": issue["issue_id"], "status": "superseded",
                    "rationale": "Synthetic verification of current hash-bound operating-review status.",
                    "reader_excerpts": [corrected], "witnesses": [{
                        "reference": "review:operating_scenarios",
                        "excerpt": research["resolution_evidence"]["review:operating_scenarios"],
                    }],
                }]
            return reply.model_copy(update={"data": data})

    request = original.model_copy(update={"output_dir": tmp_path / "metadata-mechanics", "backend": "replay"})
    models = ReviewedMetadata()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(_snapshot()), models))
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert "Draft-only package;" not in reader
    assert corrected in reader
    admission = read_json(request.output_dir / "report_admission.json")
    assert admission["model_conclusions"]["operating_asset_value"]["status"] == "blocked"
    assert admission["model_conclusions"]["funding_assessment"]["status"] == "blocked"
    assert admission["acceptance_eligibility"]["status"] == "blocked"
    assert original.financial_case_path.read_bytes() == source_before
