"""Offline coverage for the durable weekly watchlist runner."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tradingagents.weekly import (
    BatchLockedError,
    PreflightError,
    batch_lock,
    load_config,
    read_manifest,
    run_batch,
    write_json,
)


def _raw_config(tmp_path: Path, *, tickers=None, company_timeout=2) -> dict:
    return {
        "schema_version": 1,
        "timezone": "America/Los_Angeles",
        "tickers": tickers or ["AMD", "INTC"],
        "analysts": ["market", "social", "news", "fundamentals"],
        "model_profile": "balanced",
        "output_language": "English",
        "backend": "codex",
        "checkpoint_enabled": True,
        "call_timeout_seconds": 0.5,
        "company_timeout_seconds": company_timeout,
        "termination_grace_seconds": 0.05,
        "transient_retries": 1,
        "temperature": None,
        "max_tokens": None,
        "llm_max_retries": 0,
        "paths": {
            "output_root": "output",
            "cache_dir": "cache",
            "memory_log_path": "memory/trading_memory.md",
            "codex_home": "codex-home",
        },
        "credentials": {
            "fred_keychain_label": "test-label",
            "fred_keychain_account": "api-key",
        },
        "data_vendors": {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
            "macro_data": "fred",
            "prediction_markets": "polymarket",
        },
        "tool_vendors": {},
    }


def _config(tmp_path: Path, **kwargs) -> dict:
    path = tmp_path / "config" / "weekly.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_raw_config(tmp_path, **kwargs)), encoding="utf-8")
    return load_config(path)


def _state(ticker: str, analysis_date: str) -> dict:
    return {
        "company_of_interest": ticker,
        "trade_date": analysis_date,
        "market_report": "Market evidence",
        "sentiment_report": "Sentiment evidence",
        "news_report": "News evidence",
        "fundamentals_report": "Fundamentals evidence",
        "investment_debate_state": {"judge_decision": "Research decision"},
        "trader_investment_plan": "Trading plan",
        "risk_debate_state": {"judge_decision": "Portfolio decision"},
        "final_trade_decision": "**Rating**: Hold",
        "evidence_packets": {"market": {"report": "fixture"}},
        "prepared_data": {"market": {"analysis_date": analysis_date}},
        "research_quality": {
            "accepted": False,
            "signal": "REVIEW",
            "reasons": [{"role": "fixture", "code": "offline_fixture"}],
        },
        "_run_metadata": {"elapsed_seconds": 0.01, "usage": {"llm_calls": 0}},
    }


def successful_worker(ticker, analysis_date, _request, state_path):
    write_json(state_path, _state(ticker, analysis_date))
    return {
        "ok": True,
        "state_path": str(Path(state_path).resolve()),
        "quality": {
            "accepted": False,
            "signal": "REVIEW",
            "reasons": [{"role": "fixture", "code": "offline_fixture"}],
        },
    }


def dishonest_quality_worker(ticker, analysis_date, request, state_path):
    result = successful_worker(ticker, analysis_date, request, state_path)
    result["quality"] = {"accepted": True, "signal": "Buy", "reasons": []}
    return result


def mismatched_state_path_worker(ticker, analysis_date, _request, state_path):
    wrong_path = Path(state_path).parent.parent / "wrong-ticker-state.json"
    write_json(wrong_path, _state("WRONG", analysis_date))
    return {
        "ok": True,
        "state_path": str(wrong_path.resolve()),
        "quality": {"accepted": True, "signal": "Buy", "reasons": []},
    }


def marker_worker(ticker, analysis_date, request, state_path):
    Path(request["marker_path"]).write_text(ticker, encoding="utf-8")
    return successful_worker(ticker, analysis_date, request, state_path)


def mixed_worker(ticker, analysis_date, request, state_path):
    if ticker == "AMD":
        return {"ok": False, "category": "company", "code": "analysis_failed"}
    return successful_worker(ticker, analysis_date, request, state_path)


def shared_failure_worker(_ticker, _analysis_date, _request, _state_path):
    return {"ok": False, "category": "shared", "code": "usage_limit"}


def hanging_then_success_worker(ticker, analysis_date, request, state_path):
    if ticker == "AMD":
        time.sleep(5)
    return successful_worker(ticker, analysis_date, request, state_path)


def retry_on_first_date_worker(ticker, analysis_date, request, state_path):
    if analysis_date == "2026-09-16":
        return {"ok": False, "category": "transient", "code": "transient_failure"}
    return successful_worker(ticker, analysis_date, request, state_path)


def crashing_worker(_ticker, _analysis_date, _request, _state_path):
    os._exit(17)


def no_preflight(_config, _graph_config):
    return None


@pytest.mark.unit
def test_load_config_resolves_relative_paths_and_snapshots_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "99")
    config = _config(tmp_path)
    assert config["paths"]["output_root"] == str(
        (tmp_path / "config" / "output").resolve()
    )
    manifest_path = run_batch(
        config,
        dry_run=True,
        batch_id="dry-profile",
        worker=lambda *args: pytest.fail("dry run called worker"),
        preflight_check=lambda *args: pytest.fail("dry run called preflight"),
    )
    manifest = read_manifest(manifest_path)
    assert manifest["config"]["model_profile"] == "balanced"
    assert manifest["config"]["model_profile_snapshot"]["debate_rounds"] == 2
    assert manifest["config"]["paths"] == config["paths"]
    assert "secret" not in json.dumps(manifest).lower()
    assert all(company["status"] == "pending" for company in manifest["companies"].values())


@pytest.mark.unit
def test_batch_lock_rejects_overlap(tmp_path):
    with batch_lock(tmp_path), pytest.raises(BatchLockedError), batch_lock(tmp_path):
        pass


@pytest.mark.unit
def test_output_root_lock_rejects_a_different_batch_id(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    output_root = Path(config["paths"]["output_root"])
    with batch_lock(output_root), pytest.raises(BatchLockedError):
        run_batch(config, dry_run=True, batch_id="another-batch")


@pytest.mark.unit
def test_success_persists_state_then_exports_complete_tree(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(
        config,
        batch_id="success",
        worker=successful_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    company = read_manifest(manifest_path)["companies"]["AMD"]
    assert company["status"] == "degraded"
    assert Path(company["state_path"]).is_file()
    report_dir = Path(company["report_dir"])
    assert (report_dir / "complete_report.md").is_file()
    assert (report_dir / "run_metadata.json").is_file()
    assert (report_dir / "evidence.json").is_file()
    assert [attempt["kind"] for attempt in company["attempts"]] == ["analysis", "export"]


@pytest.mark.unit
def test_manifest_quality_is_recomputed_from_state_not_worker_ipc(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(
        config,
        batch_id="quality-authority",
        worker=dishonest_quality_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    company = read_manifest(manifest_path)["companies"]["AMD"]
    assert company["status"] == "degraded"
    assert company["quality"]["accepted"] is False
    assert company["quality"]["signal"] == "REVIEW"


@pytest.mark.unit
def test_worker_cannot_redirect_state_path_to_another_attempt(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(
        config,
        batch_id="state-path-authority",
        worker=mismatched_state_path_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    company = read_manifest(manifest_path)["companies"]["AMD"]
    assert company["status"] == "failed"
    assert company["error"]["code"] == "analysis_failed"
    assert company["state_path"].endswith("state/attempt-1/state.json")
    assert not Path(company["state_path"]).exists()


@pytest.mark.unit
def test_export_failure_resumes_from_saved_state_without_reinference(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])

    def fail_export(*_args):
        raise OSError("private destination detail")

    manifest_path = run_batch(
        config,
        batch_id="export-recovery",
        worker=successful_worker,
        preflight_check=no_preflight,
        exporter=fail_export,
        date_provider=lambda _timezone: "2026-09-16",
    )
    failed = read_manifest(manifest_path)["companies"]["AMD"]
    assert failed["status"] == "failed"
    assert Path(failed["state_path"]).is_file()
    assert "private destination detail" not in json.dumps(failed).lower()

    marker = tmp_path / "worker-called"

    def should_not_run(*_args):
        marker.write_text("called", encoding="utf-8")
        raise AssertionError("saved state should suppress inference")

    run_batch(
        config,
        batch_id="export-recovery",
        resume=True,
        worker=should_not_run,
        preflight_check=no_preflight,
    )
    recovered = read_manifest(manifest_path)["companies"]["AMD"]
    assert recovered["status"] == "degraded"
    assert not marker.exists()
    assert recovered["attempts"][-1]["output_dir"].endswith("reports/attempt-2")


@pytest.mark.unit
def test_terminal_report_is_skipped_on_resume(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(
        config,
        batch_id="skip-terminal",
        worker=successful_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    before = read_manifest(manifest_path)["companies"]["AMD"]["attempts"]

    def should_not_run(*_args):
        raise AssertionError("terminal report should be skipped")

    def should_not_preflight(*_args):
        raise AssertionError("completed batch should not preflight")

    run_batch(
        config,
        batch_id="skip-terminal",
        resume=True,
        worker=should_not_run,
        preflight_check=should_not_preflight,
    )
    after = read_manifest(manifest_path)["companies"]["AMD"]["attempts"]
    assert after == before


@pytest.mark.unit
def test_resume_recovers_attempt_state_written_before_parent_manifest_update(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(config, dry_run=True, batch_id="parent-crash")
    manifest = read_manifest(manifest_path)
    state_path = manifest_path.parent / "companies" / "AMD" / "state" / "attempt-1" / "state.json"
    write_json(state_path, _state("AMD", "2026-09-16"))
    company = manifest["companies"]["AMD"]
    company.update(status="running", analysis_date="2026-09-16")
    company["attempts"].append(
        {
            "kind": "analysis",
            "number": 1,
            "analysis_date": "2026-09-16",
            "status": "running",
            "state_path": str(state_path.resolve()),
        }
    )
    write_json(manifest_path, manifest)

    def should_not_run(*_args):
        raise AssertionError("durable attempt state should suppress inference")

    def should_not_preflight(*_args):
        raise AssertionError("export-only recovery should not preflight")

    run_batch(
        config,
        batch_id="parent-crash",
        resume=True,
        worker=should_not_run,
        preflight_check=should_not_preflight,
    )
    recovered = read_manifest(manifest_path)["companies"]["AMD"]
    assert recovered["status"] == "degraded"
    assert recovered["state_path"] == str(state_path.resolve())


@pytest.mark.unit
def test_max_companies_keeps_full_snapshot_and_resume_uses_saved_profile(tmp_path):
    config = _config(tmp_path)
    manifest_path = run_batch(
        config,
        batch_id="bounded-pilot",
        max_companies=1,
        worker=successful_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    first = read_manifest(manifest_path)
    assert first["config"]["tickers"] == ["AMD", "INTC"]
    assert first["companies"]["AMD"]["status"] == "degraded"
    assert first["companies"]["INTC"]["status"] == "pending"

    # Current config changes apply to a future batch. This existing batch keeps
    # the profile, timeouts, and graph snapshot with which it was created.
    changed = json.loads(json.dumps(config))
    changed["model_profile"] = "quick"
    changed["termination_grace_seconds"] = 1.5
    run_batch(
        changed,
        batch_id="bounded-pilot",
        resume=True,
        worker=successful_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-17",
    )
    resumed = read_manifest(manifest_path)
    assert resumed["config"]["model_profile"] == "balanced"
    assert resumed["config"]["termination_grace_seconds"] == 0.05
    assert resumed["companies"]["INTC"]["status"] == "degraded"


@pytest.mark.unit
def test_company_failure_continues_but_shared_limit_blocks_remaining(tmp_path):
    config = _config(tmp_path)
    per_company = run_batch(
        config,
        batch_id="continue",
        worker=mixed_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    companies = read_manifest(per_company)["companies"]
    assert companies["AMD"]["status"] == "failed"
    assert companies["INTC"]["status"] == "degraded"

    shared = run_batch(
        config,
        batch_id="shared-stop",
        worker=shared_failure_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    companies = read_manifest(shared)["companies"]
    assert companies["AMD"]["status"] == "blocked"
    assert companies["INTC"]["status"] == "blocked"
    assert companies["INTC"]["attempts"] == []


@pytest.mark.unit
def test_hung_company_is_terminated_and_next_company_runs(tmp_path):
    config = _config(tmp_path, company_timeout=0.2)
    manifest_path = run_batch(
        config,
        batch_id="timeout",
        worker=hanging_then_success_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    companies = read_manifest(manifest_path)["companies"]
    assert companies["AMD"]["status"] == "failed"
    assert companies["AMD"]["error"]["code"] == "company_timeout"
    assert companies["INTC"]["status"] == "degraded"


@pytest.mark.unit
def test_worker_crash_records_only_safe_numeric_exit_diagnostic(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    manifest_path = run_batch(
        config,
        batch_id="worker-crash",
        worker=crashing_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: "2026-09-16",
    )
    company = read_manifest(manifest_path)["companies"]["AMD"]
    assert company["status"] == "failed"
    assert company["error"]["code"] == "worker_crashed"
    assert company["diagnostics"] == {"worker_exit_code": 17}
    assert company["attempts"][-1]["diagnostics"] == {"worker_exit_code": 17}


@pytest.mark.unit
def test_transient_retry_uses_actual_new_date(tmp_path):
    config = _config(tmp_path, tickers=["AMD"])
    dates = iter(["2026-09-16", "2026-09-17"])
    manifest_path = run_batch(
        config,
        batch_id="date-retry",
        worker=retry_on_first_date_worker,
        preflight_check=no_preflight,
        date_provider=lambda _timezone: next(dates),
    )
    company = read_manifest(manifest_path)["companies"]["AMD"]
    analysis_attempts = [a for a in company["attempts"] if a["kind"] == "analysis"]
    assert [attempt["analysis_date"] for attempt in analysis_attempts] == [
        "2026-09-16",
        "2026-09-17",
    ]
    assert company["analysis_date"] == "2026-09-17"


@pytest.mark.unit
def test_preflight_failure_blocks_without_starting_workers(tmp_path):
    config = _config(tmp_path)

    def failed_preflight(*_args):
        raise PreflightError("authentication_unavailable")

    manifest_path = run_batch(
        config,
        batch_id="preflight-failure",
        worker=lambda *args: pytest.fail("worker started after failed preflight"),
        preflight_check=failed_preflight,
    )
    companies = read_manifest(manifest_path)["companies"]
    assert {company["status"] for company in companies.values()} == {"blocked"}
    assert {company["error"]["code"] for company in companies.values()} == {
        "authentication_unavailable"
    }


@pytest.mark.unit
def test_cli_dry_run_prints_one_compact_json_object(tmp_path, capsys):
    from cli.weekly import main

    config_path = tmp_path / "weekly.json"
    config_path.write_text(json.dumps(_raw_config(tmp_path, tickers=["AMD"])), encoding="utf-8")
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--dry-run",
            "--batch-id",
            "cli-dry-run",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.count("\n") == 1
    result = json.loads(output)
    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert Path(result["manifest_path"]).is_file()
