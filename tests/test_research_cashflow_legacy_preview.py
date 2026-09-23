"""Keep saved preview-11 cash-flow readers reproducible after optional schema growth."""

from hashlib import sha256

from tests.test_research_cashflow_engine import CashFixture, setup
from tradingagents.research import engine, storage
from tradingagents.research.case_context import FinancialCaseEnvelope
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_bytes, read_json


def test_absent_reconciliation_preserves_preview_11_artifact_and_provenance(tmp_path, monkeypatch):
    request, snapshot = setup(tmp_path)
    envelope = FinancialCaseEnvelope.model_validate_json(
        request.financial_case_path.read_bytes()
    )
    package = envelope.cashflow_bridge
    assert package is not None and package.historical_reconciliation is None
    legacy_bytes = canonical_json(package.model_dump(
        mode="json", exclude={"historical_reconciliation"}
    ))
    assert canonical_json(package) == legacy_bytes

    with monkeypatch.context() as prior:
        prior.setattr(storage, "ENGINE_VERSION", "research-v2-preview-11")
        prior.setattr(engine, "ENGINE_VERSION", "research-v2-preview-11")
        result = run_research(
            request, ResearchServices(SnapshotEvidenceService(snapshot), CashFixture())
        )
    assert result.stop_reason == "completed_needs_review"
    assert read_json(request.output_dir / "run_metadata.json")["engine"] == "research-v2-preview-11"
    saved_package = read_bytes(request.output_dir / "cashflow_bridge_package.json")
    assert saved_package == legacy_bytes
    provenance = read_json(
        request.output_dir / "stages/verify_report-rendering-provenance.json"
    )["output"]
    disclosure = provenance["rendering_inputs"]["case_review_disclosure"]
    assert disclosure["cashflow_bridge_package_sha256"] == sha256(saved_package).hexdigest()
    preview = preview_saved_reader(request.output_dir, request, tmp_path / "legacy-preview")
    assert preview["source_reader_binding"] == "exported_final_reader"
    assert preview["live_calls"] == 0
