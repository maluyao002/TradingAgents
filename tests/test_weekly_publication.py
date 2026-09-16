import json
from pathlib import Path

import pytest

from tradingagents import weekly_publication as publication


def _manifest(tmp_path, companies):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "batch_id": "2026-09-13-weekly",
        "created_at": "2026-09-13T00:00:00Z",
        "updated_at": "2026-09-13T00:00:00Z",
        "timezone": "America/Los_Angeles",
        "config": {"tickers": sorted(companies)},
        "companies": companies,
        "publication": {"folder_id": None, "digest": {}, "companies": {}},
    }), encoding="utf-8")
    return path


def _company(tmp_path, ticker="AAPL", *, status="completed", accepted=True):
    report_dir = tmp_path / "reports" / ticker
    report_dir.mkdir(parents=True)
    (report_dir / "complete_report.md").write_text(
        "# Trading Analysis Report: AAPL\n\n"
        "A **[source](https://example.test/source?a=1&b=\"two\")** and inline evidence reference.\n\n"
        "| Metric | Value |\n|---|---|\n| Revenue \\| adjusted<br>line two | $10 |\n\n- First\n  - Nested\n- Second\n"
        "\nUnsafe <script>alert(1)</script> remains text.\n",
        encoding="utf-8",
    )
    (report_dir / "evidence.json").write_text('{"private": "diagnostic"}', encoding="utf-8")
    return {"status": status, "analysis_date": "2026-09-13", "report_dir": str(report_dir),
            "quality": {"accepted": accepted, "signal": "BUY", "reasons": []},
            "error": None, "attempts": 1}


@pytest.mark.unit
def test_prepare_preserves_report_structure_and_only_uses_complete_report(tmp_path):
    manifest = _manifest(tmp_path, {"AAPL": _company(tmp_path)})
    result = publication.prepare_publication(manifest)
    entry = result["companies"][0]
    staged = Path(entry["html_file"]).read_text(encoding="utf-8")
    assert "<h1>Trading Analysis Report: AAPL</h1>" in staged
    assert '<a href="https://example.test/source?a=1&amp;b=%22two%22">source</a>' in staged
    assert "<strong><a " in staged
    assert "<table" in staged and "Revenue | adjusted" in staged
    assert "<ul>" in staged and "Nested" in staged
    assert "adjusted<br>line two" in staged
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in staged
    assert "private" not in staged
    assert entry["source_file"].endswith("complete_report.md")
    assert entry["content_sha256"]


@pytest.mark.unit
def test_incomplete_batch_is_explicit_and_not_uploadable(tmp_path):
    manifest = _manifest(tmp_path, {
        "AAPL": _company(tmp_path),
        "MSFT": {"status": "blocked", "analysis_date": "2026-09-13", "report_dir": None,
                 "quality": {"accepted": False, "signal": None, "reasons": ["missing data"]},
                 "error": "provider unavailable", "attempts": 1},
    })
    result = publication.prepare_publication(manifest)
    blocked = next(item for item in result["companies"] if item["ticker"] == "MSFT")
    assert blocked["upload_status"] == "not_ready"
    digest = json.loads(manifest.read_text())["publication"]["digest"]["markdown"]
    assert "MSFT: blocked. provider unavailable" in digest
    assert "Needs review" in digest


@pytest.mark.unit
def test_uploaded_document_is_resumed_and_digest_invalidates_when_link_changes(tmp_path):
    manifest = _manifest(tmp_path, {"AAPL": _company(tmp_path)})
    publication.prepare_publication(manifest)
    doc_id = "abcdefghijklmnopqrstuvwxyz123456"
    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    publication.record_document(manifest, "AAPL", doc_id, url)
    publication.mark_verified(manifest, "AAPL")
    publication.prepare_publication(manifest)
    state = json.loads(manifest.read_text())["publication"]
    assert state["companies"]["AAPL"]["verified"] is True
    assert state["digest"]["verified"] is False
    assert publication.publication_resume_plan(manifest) == [{
        "kind": "digest", "target_folder_id": None, **publication._public_entry(state["digest"])
    }]


@pytest.mark.unit
def test_record_document_rejects_arbitrary_urls_and_unverified_upload_is_resumable(tmp_path):
    manifest = _manifest(tmp_path, {"AAPL": _company(tmp_path)})
    publication.prepare_publication(manifest)
    doc_id = "abcdefghijklmnopqrstuvwxyz123456"
    with pytest.raises(ValueError, match="Google Docs URL"):
        publication.record_document(manifest, "AAPL", doc_id, "https://attacker.test/document/d/" + doc_id)
    publication.record_document(manifest, "AAPL", doc_id, f"https://docs.google.com/document/d/{doc_id}/edit")
    plan = publication.publication_resume_plan(manifest)
    company_plan = next(item for item in plan if item["kind"] == "company")
    assert company_plan["ticker"] == "AAPL"
    assert company_plan["document_id"] == doc_id
    with pytest.raises(ValueError, match="prepare publication"):
        publication.record_document(manifest, "UNKNOWN", doc_id, f"https://docs.google.com/document/d/{doc_id}/edit")


@pytest.mark.unit
def test_absolute_report_dir_is_required(tmp_path):
    manifest = _manifest(tmp_path, {"AAPL": {
        "status": "completed", "report_dir": "relative/reports", "quality": {}, "attempts": 1,
    }})
    with pytest.raises(ValueError, match="absolute"):
        publication.prepare_publication(manifest)


@pytest.mark.unit
def test_changed_content_preserves_observed_remote_identity_and_requires_reconcile(tmp_path):
    company = _company(tmp_path)
    manifest = _manifest(tmp_path, {"AAPL": company})
    publication.prepare_publication(manifest)
    doc_id = "abcdefghijklmnopqrstuvwxyz123456"
    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    publication.record_document(manifest, "AAPL", doc_id, url)
    publication.mark_verified(manifest, "AAPL")
    report = Path(company["report_dir"]) / "complete_report.md"
    report.write_text(report.read_text() + "\nUpdated source text.\n", encoding="utf-8")
    publication.prepare_publication(manifest)
    entry = json.loads(manifest.read_text())["publication"]["companies"]["AAPL"]
    assert entry["document_id"] == doc_id and entry["url"] == url
    assert entry["upload_status"] == "needs_update"
    assert entry["verified"] is False


@pytest.mark.unit
def test_resume_plan_skips_failed_and_nonready_companies(tmp_path):
    manifest = _manifest(tmp_path, {"MSFT": {
        "status": "failed", "report_dir": None, "quality": {"accepted": False, "reasons": []},
        "error": {"code": "timeout", "message": "model timed out"}, "attempts": 1,
    }})
    publication.prepare_publication(manifest)
    plan = publication.publication_resume_plan(manifest)
    assert all(item.get("ticker") != "MSFT" for item in plan)


def test_publication_respects_runner_lock_without_mutating_manifest(tmp_path):
    from tradingagents.weekly import BatchLockedError, batch_lock

    manifest = _manifest(tmp_path, {"AAPL": _company(tmp_path)})
    before = manifest.read_bytes()
    with batch_lock(tmp_path), pytest.raises(BatchLockedError):
        publication.prepare_publication(manifest)
    assert manifest.read_bytes() == before


def test_changed_digest_keeps_remote_id_and_cannot_be_verified_without_update(tmp_path):
    manifest = _manifest(tmp_path, {"AAPL": _company(tmp_path)})
    publication.prepare_publication(manifest)
    digest_id = "digest_abcdefghijklmnopqrstuvwxyz"
    publication.record_document(manifest, "digest", digest_id,
                                f"https://docs.google.com/document/d/{digest_id}/edit", digest=True)
    publication.mark_verified(manifest, "digest", digest=True)
    company_id = "company_abcdefghijklmnopqrstuvwxyz"
    publication.record_document(manifest, "AAPL", company_id,
                                f"https://docs.google.com/document/d/{company_id}/edit")
    publication.mark_verified(manifest, "AAPL")
    publication.prepare_publication(manifest)
    digest = json.loads(manifest.read_text())["publication"]["digest"]
    assert digest["document_id"] == digest_id
    assert digest["upload_status"] == "needs_update"
    assert digest["verified"] is False
    with pytest.raises(ValueError):
        publication.mark_verified(manifest, "digest", digest=True)


def test_degraded_object_reasons_and_actual_executive_summary(tmp_path):
    company = _company(tmp_path, status="degraded", accepted=False)
    company["quality"]["reasons"] = [{"code": "source_unavailable", "role": "news"}]
    manifest = _manifest(tmp_path, {"AAPL": company})
    publication.prepare_publication(manifest)
    digest = json.loads(manifest.read_text())["publication"]["digest"]["markdown"]
    assert "news: source unavailable" in digest
    assert "REVIEW" in digest and "BUY" not in digest
    assert publication._portfolio_excerpt(
        "## Portfolio Manager Decision\n\n### Portfolio Manager\n**Rating**: Hold\n\n"
        "**Executive Summary**: Revenue improved; cash generation remains uncertain.\n\n"
        "**Investment Thesis**: Much longer content.\n\n## Analysts\nOther sections."
    ) == "Revenue improved; cash generation remains uncertain."


def test_summary_survives_resume_but_not_new_research(tmp_path):
    company = _company(tmp_path)
    manifest = _manifest(tmp_path, {"AAPL": company})
    summary = tmp_path / "summary.md"
    summary.write_text("Source-grounded weekly overview.")
    publication.prepare_publication(manifest, summary)
    publication.prepare_publication(manifest)
    digest = json.loads(manifest.read_text())["publication"]["digest"]["markdown"]
    assert "Source-grounded weekly overview." in digest
    report = Path(company["report_dir"]) / "complete_report.md"
    report.write_text(report.read_text() + "\nNew research changes the summary's inputs.\n")
    publication.prepare_publication(manifest)
    digest = json.loads(manifest.read_text())["publication"]["digest"]["markdown"]
    assert "Source-grounded weekly overview." not in digest
