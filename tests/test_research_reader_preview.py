from hashlib import sha256

import pytest

from tests.test_research_case_engine import CaseFixture, case_setup
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.storage import canonical_json, read_json


class ChangedRepair(CaseFixture):
    def __init__(self):
        super().__init__(repair_warning=True)

    def complete(self, role, payload, request):
        reply = super().complete(role, payload, request)
        if role == "editor" and payload["stage"] == "repair_report":
            reply.data["sections"][0]["text"] += " Repaired candidate wording."
        return reply


def test_offline_preview_is_separate_unverified_and_preserves_history(tmp_path):
    request, services = case_setup(tmp_path / "source")
    result = run_research(request, services)
    hashes = {name: sha256((request.output_dir / name).read_bytes()).hexdigest()
              for name in result.artifacts}
    destination = tmp_path / "preview"
    metrics = preview_saved_reader(request.output_dir, request, destination)
    assert metrics["live_calls"] == 0 and metrics["acceptance"] is False
    assert metrics["source_reader_binding"] == "exported_final_reader"
    assert metrics["selected_draft_stage"] == "editor"
    assert metrics["selected_reader_stage"] == "verify_report"
    assert "NOT VERIFIED OR ACCEPTED" in (destination / "reader_preview.md").read_text()
    assert read_json(destination / "comparison.json")["source_hashes"]
    assert all(sha256((request.output_dir / name).read_bytes()).hexdigest() == value
               for name, value in hashes.items())
    with pytest.raises(ValueError, match="fresh destination"):
        preview_saved_reader(request.output_dir, request, destination)
    with pytest.raises(ValueError, match="fresh destination"):
        preview_saved_reader(request.output_dir, request, request.output_dir / "nested")


def test_preview_binds_to_the_repaired_exported_reader(tmp_path):
    request, services = case_setup(tmp_path / "source", ChangedRepair())
    result = run_research(request, services)
    destination = tmp_path / "preview"

    metrics = preview_saved_reader(request.output_dir, request, destination)

    final_reader = (request.output_dir / "reader_report.md").read_text()
    preview = (destination / "reader_preview.md").read_text()
    assert result.stop_reason == "completed_needs_review"
    assert "Repaired candidate wording." in final_reader
    assert "Repaired candidate wording." in preview
    assert metrics["source_reader_binding"] == "exported_final_reader"
    assert metrics["selected_draft_stage"] == "repair_report"
    assert metrics["selected_reader_stage"] == "verify_repaired_report"
    assert metrics["original_reader_bytes"] == len(final_reader.encode())


def test_failed_run_preview_keeps_the_initial_reader_candidate(tmp_path):
    request, services = case_setup(tmp_path / "source", CaseFixture(timeout="verify_report-coverage-0"))
    result = run_research(request, services)

    metrics = preview_saved_reader(request.output_dir, request, tmp_path / "preview")

    assert result.stop_reason == "stage_failed"
    assert metrics["source_reader_binding"] == "initial_candidate"
    assert metrics["selected_draft_stage"] == "editor"
    assert metrics["selected_reader_stage"] == "verify_report"


def test_preview_rejects_a_dangling_reader_verification_symlink(tmp_path):
    request, services = case_setup(tmp_path / "source")
    run_research(request, services)
    verification = request.output_dir / "reader_verification.json"
    verification.unlink()
    verification.symlink_to(tmp_path / "missing-reader-verification.json")

    with pytest.raises(ValueError, match="preview inputs cannot be symlinks"):
        preview_saved_reader(request.output_dir, request, tmp_path / "preview")


@pytest.mark.parametrize("target, message", [
    ("stages/repair_report.json", "repair_report draft output hash mismatch"),
    ("stages/verify_repaired_report-reader-candidate.json", "reader candidate output hash mismatch"),
    ("reader_report.md", "final reader hash mismatch"),
])
def test_preview_rejects_tampering_with_selected_export_binding(tmp_path, target, message):
    request, services = case_setup(tmp_path / "source", ChangedRepair())
    run_research(request, services)
    path = request.output_dir / target
    if target.endswith(".json"):
        record = read_json(path)
        record["output"] = {**record["output"], "tampered": True}
        path.write_bytes(canonical_json(record))
    else:
        path.write_text("tampered reader")

    with pytest.raises(ValueError, match=message):
        preview_saved_reader(request.output_dir, request, tmp_path / "preview")
