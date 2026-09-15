"""Explicit, fundamentals-only pilot with reusable evidence for matched runs."""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console

from cli.backend import Backend, _ask
from cli.utils import get_analysis_date, get_ticker
from tradingagents.model_profiles import MODEL_PROFILES

app = typer.Typer(add_completion=False)


def load_evidence(path: Path, ticker: str, date: str) -> dict:
    """Replay only the prepared snapshot, never the previous model's answer."""
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(bundle, dict) or bundle.get('ticker') != ticker
                or bundle.get('analysis_date') != date
                or not isinstance(bundle.get('prepared_data'), dict)):
            raise ValueError
        return bundle['prepared_data']
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Evidence must be a pilot bundle for the same ticker and analysis date.') from None


def save_result(result: dict, root: Path) -> Path:
    # Random suffix prevents overwriting another matched run. Reports are ignored
    # by Git; no runtime settings, credentials, thread IDs, or environment are saved.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    directory = root / f"fundamentals_{stamp}_{uuid4().hex[:8]}"
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / 'fundamentals_report.md').write_text(result['fundamentals_report'], encoding="utf-8")
    return directory


def run_pilot(console: Console, backend: Backend, *, ticker: str | None = None,
              date: str | None = None, profile: str | None = None,
              evidence: Path | None = None, output: Path = Path('reports')) -> None:
    from tradingagents.codex.adapter import CodexAdapter, CodexAdapterError
    from tradingagents.codex.fundamentals import (
        FundamentalsRunError,
        run_fundamentals,
        validate_inputs,
    )
    from tradingagents.codex.transport import TransportError

    console.print('[bold]Fundamentals-only pilot[/bold]')
    console.print('One analyst; no debates, portfolio decision, or order execution.')
    ticker = ticker or get_ticker()
    date = date or get_analysis_date()
    profile = profile or _ask('Fundamentals model profile', [
        f'{name}' for name in MODEL_PROFILES
    ], default='balanced')
    if profile not in MODEL_PROFILES:
        raise typer.BadParameter('Choose quick, balanced, or deep.')
    settings = MODEL_PROFILES[profile]['agents']['fundamentals']
    console.print(f"{backend.value}: {settings['model']}, {settings['reasoning_effort']} effort",
                  markup=False)
    try:
        ticker, date, _ = validate_inputs(ticker, date)
        prepared = load_evidence(evidence, ticker, date) if evidence else None
        ticker, date, prepared = validate_inputs(ticker, date, prepared)
        context = (CodexAdapter(home=os.environ.get('TRADINGAGENTS_CODEX_HOME',
                                                   '~/.tradingagents/codex'), timeout=300)
                   if backend == Backend.CODEX else nullcontext(None))
        with context as adapter, console.status('Preparing evidence and analyzing fundamentals…'):
            result = run_fundamentals(
                ticker, date, backend=backend.value, model=settings['model'],
                effort=settings['reasoning_effort'], prepared=prepared, adapter=adapter,
            )
        directory = save_result(result, output)
    except (CodexAdapterError, TransportError) as exc:
        console.print('Codex analysis failed. Check sign-in, model access and runtime compatibility. '
                      'No API fallback was attempted.', markup=False)
        # These adapter/transport exception types contain safe diagnostics, not
        # raw server errors, stderr, credentials, or filesystem details.
        console.print(f'Reason: {exc}', markup=False)
        raise typer.Exit(code=1) from None
    except (FundamentalsRunError, ValueError, OSError):
        console.print('Pilot could not complete. Check ticker/date, evidence bundle, provider '
                      'configuration and output permissions.', markup=False)
        raise typer.Exit(code=1) from None
    console.print(result['fundamentals_report'], markup=False)
    packet = result['evidence_packet']
    console.print(f"Evidence status: {packet.get('status', 'unknown')}. "
                  'Citation checks do not establish factual accuracy; review the report.', markup=False)
    console.print(f'Saved report and reusable evidence: {directory}', markup=False)


@app.command()
def analyze(
    backend: Annotated[Backend, typer.Option('--backend', help='Explicit codex or api; never falls back.')],
    ticker: Annotated[str, typer.Option('--ticker')],
    date: Annotated[str, typer.Option('--date', help='Analysis date, YYYY-MM-DD.')],
    profile: Annotated[str, typer.Option('--profile')] = 'balanced',
    evidence: Annotated[Path | None, typer.Option('--evidence', help='Replay result.json from a prior pilot.')] = None,
    output: Annotated[Path, typer.Option('--output')] = Path('reports'),
):
    run_pilot(Console(), backend, ticker=ticker, date=date, profile=profile,
              evidence=evidence, output=output)


if __name__ == '__main__':
    app()
