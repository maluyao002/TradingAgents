"""Completed cash-flow results from the prior engine cannot bypass current validation."""

from datetime import date

import pytest

from tests.test_research_case_engine import case_setup
from tests.test_research_cashflow_commitment_review import _rebind, _replace_source
from tests.test_research_cashflow_engine import CashFixture
from tests.test_research_cashflow_review_regressions import _incremental_setup, _operating_review
from tradingagents.research import case_context, engine, storage
from tradingagents.research.cashflow_bridge import evaluate_cashflow_bridge
from tradingagents.research.engine import run_research
from tradingagents.research.operating_scenarios import operating_scenario_package_sha256
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


def test_engine_11_rejects_completed_engine_10_invalid_commitment_cache(tmp_path, monkeypatch):
    snapshot, valid_case, operating_package, _, bridge = _incremental_setup(
        tmp_path / "fixture", date(2026, 7, 1), date(2026, 9, 30)
    )
    valid_operating, valid_bridge = _rebind(snapshot, valid_case, operating_package, bridge)
    legacy_result = evaluate_cashflow_bridge(valid_bridge, valid_case, snapshot, valid_operating)
    invalid_case = _replace_source(valid_case, "cash-commitment-supply-h2", amount_basis="other")
    invalid_operating, invalid_bridge = _rebind(
        snapshot, invalid_case, operating_package, valid_bridge
    )
    with pytest.raises(ValueError, match="contractual cash"):
        evaluate_cashflow_bridge(invalid_bridge, invalid_case, snapshot, invalid_operating)
    invalid_operating_package = operating_package.model_copy(
        update={"case_sha256": invalid_bridge.case_sha256, "review": None}
    )
    invalid_operating_package = invalid_operating_package.model_copy(
        update={"review": _operating_review(invalid_operating_package)}
    )
    assert operating_scenario_package_sha256(invalid_operating_package) == (
        invalid_bridge.operating_package_sha256
    )

    request, _ = case_setup(tmp_path / "run")
    request.evidence_path.write_bytes(canonical_json(snapshot))
    request.financial_case_path.write_bytes(
        canonical_json(
            {
                "case": invalid_case,
                "operating_scenarios": invalid_operating_package,
                "cashflow_bridge": invalid_bridge,
            }
        )
    )
    # Only the synthetic prior-engine run bypasses the newer commitment rule.
    with monkeypatch.context() as prior:
        prior.setattr(storage, "ENGINE_VERSION", "research-v2-preview-10")
        prior.setattr(engine, "ENGINE_VERSION", "research-v2-preview-10")
        prior.setattr(case_context, "evaluate_cashflow_bridge", lambda *_: legacy_result)
        old_models = CashFixture()
        old_result = run_research(
            request, ResearchServices(SnapshotEvidenceService(snapshot), old_models)
        )
        assert old_result.stop_reason == "completed_needs_review"
        assert old_models.calls
        saved_case = read_json(request.output_dir / "financial_case.json")
        assert (
            next(
                item
                for item in saved_case["commitments"]["items"]
                if item["id"] == "cash-commitment-supply-h2"
            )["amount_basis"]
            == "other"
        )
        assert read_json(request.output_dir / "run_metadata.json")["engine"] == (
            "research-v2-preview-10"
        )
        assert (request.output_dir / "stages/completed-result.json").exists()
        replay_models = CashFixture()
        assert (
            run_research(
                request, ResearchServices(SnapshotEvidenceService(snapshot), replay_models)
            )
            == old_result
        )
        assert replay_models.calls == []

    assert storage.ENGINE_VERSION == engine.ENGINE_VERSION == "research-v2-preview-12"
    current_models = CashFixture()
    with pytest.raises(ValueError, match="incompatible research checkpoint"):
        run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), current_models))
    assert current_models.calls == []
