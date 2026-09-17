"""Standalone M0 CLI contract tests: validation only, with no side effects."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from cli.research import safe_summary
from tests.test_research_contracts import request_data
from tradingagents.research.contracts import ResearchRequest


def run_cli(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cli.research", "--config", str(config), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def write_config(path: Path, **overrides: object) -> None:
    path.write_text(json.dumps(request_data(**overrides)), encoding="utf-8")


def write_evidence(path: Path) -> None:
    content = "frozen evidence"
    path.write_text(
        json.dumps(
            {
                "ticker": "AMD",
                "cutoff": "2026-09-17T12:00:00+00:00",
                "sources": [
                    {
                        "id": "source-1",
                        "url": "https://example.test/filing",
                        "title": "Filing",
                        "publisher": "Issuer",
                        "retrieved_at": "2026-09-16T12:00:00+00:00",
                        "published_at": "2026-09-16T12:00:00+00:00",
                        "content": content,
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def replay_responses() -> dict[str, list[dict[str, object]]]:
    usage = {"input_tokens": 1, "output_tokens": 1}
    analysis = {"data": {"summary": "offline"}, "usage": usage}
    planner = {
        "data": {
            "summary": "offline plan",
            "questions": [
                {
                    "id": f"question-{index}",
                    "question": "Question",
                    "consequence": "Material",
                    "resolvability": "high",
                }
                for index in range(1, 4)
            ],
        },
        "usage": usage,
    }
    return {
        "planner": [planner],
        "challenger": [analysis, analysis],
        "business": [analysis],
        "accounting": [analysis],
        "expectations": [analysis],
        "management": [analysis],
        "valuation": [{"data": {"model": None, "unsupported_inputs": ["offline"]}, "usage": usage}],
        "verifier": [
            {"data": {"reviewed_report": False}, "usage": usage},
            {"data": {"reviewed_report": True}, "usage": usage},
        ],
        "editor": [{"data": {"sections": [{"title": "研究摘要", "text": "合成测试，证据不足。"}],
                              "limitations": []}, "usage": usage}],
    }


def test_dry_run_prints_normalized_safe_summary_without_creating_paths(tmp_path):
    config = tmp_path / "request.json"
    write_config(
        config,
        output_dir="out/../out/research",
        evidence_path="snapshots/evidence.json",
        prior_dossier_path="dossiers/previous.json",
        dossier_dir="dossiers/new",
    )

    result = run_cli(config, "--dry-run")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ticker"] == "AMD"
    assert summary["output_dir"] == str(tmp_path / "out/research")
    assert summary["evidence_path"] == str(tmp_path / "snapshots/evidence.json")
    assert summary["prior_dossier_path"] == str(tmp_path / "dossiers/previous.json")
    assert summary["dossier_dir"] == str(tmp_path / "dossiers/new")
    assert "mandate" not in summary
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "snapshots").exists()
    assert not (tmp_path / "dossiers").exists()


def test_dry_run_rejects_invalid_json_and_invalid_contract_without_side_effects(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    invalid = tmp_path / "invalid.json"
    write_config(invalid, cutoff="2026-09-17T12:00:00", output_dir="would-create")

    for config in (malformed, invalid):
        result = run_cli(config, "--dry-run")
        assert result.returncode == 2
        assert "validation failed" in result.stderr
    assert not (tmp_path / "would-create").exists()


def test_non_dry_run_does_not_claim_success_or_create_output(tmp_path):
    config = tmp_path / "request.json"
    write_config(config, output_dir="would-create")

    result = run_cli(config)

    assert result.returncode == 2
    assert "live research execution is not yet configured" in result.stderr
    assert not (tmp_path / "would-create").exists()


def test_safe_summary_omits_unbounded_raw_text():
    request = ResearchRequest.model_validate(request_data(mandate="private instruction"))
    assert "mandate" not in safe_summary(request)


def test_validation_does_not_echo_secret_or_accept_duplicate_keys(tmp_path):
    config = tmp_path / "bad.json"
    write_config(config, secret="SYNTHETIC_PRIVATE_TOKEN")
    result = run_cli(config, "--dry-run")
    assert result.returncode == 2
    assert "SYNTHETIC_PRIVATE_TOKEN" not in result.stderr
    config.write_text('{"ticker":"AMD","ticker":"NVDA"}')
    assert run_cli(config, "--dry-run").returncode == 2


def test_offline_replay_uses_frozen_evidence_and_responses(tmp_path):
    evidence = tmp_path / "evidence.json"
    responses = tmp_path / "responses.json"
    config = tmp_path / "request.json"
    write_evidence(evidence)
    responses.write_text(json.dumps(replay_responses()), encoding="utf-8")
    write_config(
        config,
        backend="replay",
        evidence_path="evidence.json",
        output_dir="offline-output",
    )

    result = run_cli(config, "--responses", os.path.relpath(responses, Path.cwd()))

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ticker"] == "AMD"
    assert summary["assessment"] == "needs_review"
    assert (tmp_path / "offline-output" / "result.json").is_file()


def test_replay_rejects_invalid_responses_before_creating_output(tmp_path):
    evidence = tmp_path / "evidence.json"
    config = tmp_path / "request.json"
    responses = tmp_path / "responses.json"
    write_evidence(evidence)
    responses.write_text("{", encoding="utf-8")
    write_config(
        config,
        backend="replay",
        evidence_path="evidence.json",
        output_dir="must-not-exist",
    )

    result = run_cli(config, "--responses", str(responses))

    assert result.returncode == 2
    assert "offline replay failed" in result.stderr
    assert not (tmp_path / "must-not-exist").exists()


def test_dry_run_does_not_read_unavailable_replay_responses(tmp_path):
    evidence = tmp_path / "evidence.json"
    config = tmp_path / "request.json"
    write_evidence(evidence)
    write_config(
        config, backend="replay", evidence_path="evidence.json", output_dir="must-not-exist"
    )

    result = run_cli(config, "--dry-run", "--responses", "missing-responses.json")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "must-not-exist").exists()


def test_codex_execution_requires_explicit_live_flag_and_runtime(tmp_path):
    config = tmp_path / "request.json"
    write_config(config, backend="codex", output_dir="must-not-exist")
    result = run_cli(config)
    assert result.returncode == 2
    assert "--allow-live" in result.stderr
    assert not (tmp_path / "must-not-exist").exists()
    result = run_cli(config, "--allow-live")
    assert result.returncode == 2
    assert "--codex-home" in result.stderr


def test_live_dry_run_remains_completely_passive(tmp_path):
    config = tmp_path / "request.json"
    write_config(config, backend="codex", output_dir="must-not-exist")
    result = run_cli(config, "--dry-run", "--allow-live", "--codex-home", str(tmp_path / "no-runtime"))
    assert result.returncode == 0
    assert not (tmp_path / "no-runtime").exists()
    assert not (tmp_path / "must-not-exist").exists()


def test_live_acquisition_missing_identity_never_launches(tmp_path):
    config = tmp_path / "request.json"
    write_config(config, backend="codex", output_dir="must-not-exist")
    result = run_cli(config, "--allow-live", "--codex-home", str(tmp_path / "no-runtime"))
    assert result.returncode == 2
    assert "identity" in result.stderr
    assert not (tmp_path / "no-runtime").exists()
