import json
import os
import subprocess
import sys
from io import StringIO
from unittest.mock import Mock

import pytest
from rich.console import Console
from typer.testing import CliRunner

import cli.fundamentals as pilot
import cli.main as main
from cli.backend import Backend


@pytest.mark.parametrize('backend', ['api', 'codex'])
def test_explicit_pilot_routes_without_full_pipeline(monkeypatch, backend):
    run = Mock()
    monkeypatch.setattr(pilot, 'run_pilot', run)
    monkeypatch.setattr(main, 'run_analysis', Mock(side_effect=AssertionError('full pipeline')))
    monkeypatch.setattr(main, 'run_codex_setup', Mock(side_effect=AssertionError('setup')))
    result = CliRunner().invoke(main.app, ['--backend', backend, '--fundamentals'])
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(main.console, Backend(backend))


def test_pilot_rejects_checkpoint_deletion(monkeypatch):
    run = Mock()
    monkeypatch.setattr(pilot, 'run_pilot', run)
    result = CliRunner().invoke(main.app, ['--backend', 'api', '--fundamentals', '--clear-checkpoints'])
    assert result.exit_code == 2
    run.assert_not_called()


def test_replay_uses_evidence_not_previous_answer(tmp_path):
    path = tmp_path / 'result.json'
    path.write_text(json.dumps({'ticker': 'AMD', 'analysis_date': '2026-09-13',
                                'prepared_data': {'sources': []},
                                'fundamentals_report': 'Do not replay this answer'}))
    assert pilot.load_evidence(path, 'AMD', '2026-09-13') == {'sources': []}
    with pytest.raises(ValueError, match='same ticker'):
        pilot.load_evidence(path, 'NVDA', '2026-09-13')
    with pytest.raises(ValueError):
        pilot.load_evidence(path, 'AMD', '2026-09-12')


def test_reports_are_distinct_and_replayable(tmp_path):
    result = {'ticker': 'AMD', 'analysis_date': '2026-09-13',
              'prepared_data': {'sources': []}, 'fundamentals_report': 'Research'}
    first = pilot.save_result(result, tmp_path)
    second = pilot.save_result(result, tmp_path)
    assert first != second
    assert pilot.load_evidence(first / 'result.json', 'AMD', '2026-09-13') == {'sources': []}
    assert (first / 'fundamentals_report.md').read_text() == 'Research'


def test_api_pilot_never_starts_codex(monkeypatch, tmp_path):
    import tradingagents.codex.adapter as adapter
    import tradingagents.codex.fundamentals as core
    monkeypatch.setattr(adapter, 'CodexAdapter', Mock(side_effect=AssertionError('Codex started')))
    run = Mock(return_value={'ticker': 'AMD', 'analysis_date': '2026-09-13',
                            'prepared_data': {}, 'fundamentals_report': 'Research',
                            'evidence_packet': {'status': 'partial'}})
    monkeypatch.setattr(core, 'run_fundamentals', run)
    pilot.run_pilot(Console(file=StringIO()), Backend.API, ticker='AMD', date='2026-09-13',
                    profile='balanced', output=tmp_path)
    assert run.call_args.kwargs['adapter'] is None
    assert run.call_args.kwargs['model'] == 'gpt-5.6-sol'
    assert run.call_args.kwargs['effort'] == 'high'


@pytest.mark.parametrize("ticker, analysis_date", [
    ("../private", "2026-09-13"),
    ("BTCUSD", "2026-09-13"),
    ("GC=F", "2026-09-13"),
    ("^GSPC", "2026-09-13"),
    ("AMD", "9999-12-31"),
])
def test_invalid_inputs_fail_before_codex_start(monkeypatch, tmp_path, ticker, analysis_date):
    import tradingagents.codex.adapter as adapter
    constructor = Mock(side_effect=AssertionError('Codex started'))
    monkeypatch.setattr(adapter, 'CodexAdapter', constructor)
    result = CliRunner().invoke(pilot.app, ['--backend', 'codex', '--ticker', ticker,
                                          '--date', analysis_date, '--output', str(tmp_path)])
    assert result.exit_code == 1
    constructor.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_codex_failure_never_saves_or_falls_back(monkeypatch, tmp_path):
    import tradingagents.codex.adapter as adapter
    import tradingagents.codex.fundamentals as core
    error = adapter.CodexAdapterError('Codex tool feature isolation could not be verified')
    error.__cause__ = RuntimeError('secret detail')
    monkeypatch.setattr(adapter, 'CodexAdapter', Mock(side_effect=error))
    run = Mock(side_effect=AssertionError('fallback'))
    monkeypatch.setattr(core, 'run_fundamentals', run)
    result = CliRunner().invoke(pilot.app, ['--backend', 'codex', '--ticker', 'AMD',
                                          '--date', '2026-09-13', '--output', str(tmp_path)])
    assert result.exit_code == 1
    assert 'No API fallback' in ' '.join(result.output.split())
    assert 'Reason: Codex tool feature isolation could not be verified' in ' '.join(result.output.split())
    assert 'secret detail' not in result.output
    run.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_unicode_artifacts_round_trip_under_ascii_locale(tmp_path):
    # Run outside this interpreter's UTF-8 locale to reproduce the real failure.
    script = """
import json
import sys
from pathlib import Path
from cli.fundamentals import save_result, load_evidence
result = json.loads(sys.argv[2])
folder = save_result(result, Path(sys.argv[1]))
assert load_evidence(folder / 'result.json', 'AMD', '2026-09-13') == result['prepared_data']
assert (folder / 'fundamentals_report.md').read_text(encoding='utf-8') == result['fundamentals_report']
"""
    result = {'ticker': 'AMD', 'analysis_date': '2026-09-13',
              'prepared_data': {'caveats': ['现金流缺少披露']},
              'fundamentals_report': '现金流分析 — €100'}
    completed = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path), json.dumps(result)],
        env={**os.environ, 'LC_ALL': 'C', 'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0'},
        capture_output=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
