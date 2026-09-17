# Deep Research V2: offline preview

This is an opt-in, research-only implementation. It does not change the existing
weekly runner, trading graph, model settings, schedules, publishing or AMD task.
No live provider is instantiated by the new CLI. Reports produced here are
`Needs review / Unrated`, not accepted investment assessments.

## Configuration and replay

Use the repository virtual environment:

```sh
.venv/bin/python -m cli.research --config request.json --dry-run
.venv/bin/python -m cli.research --config request.json --responses responses.json
```

Minimal request shape:

```json
{
  "ticker": "TEST",
  "cutoff": "2026-09-16T23:59:59-07:00",
  "timezone": "America/Los_Angeles",
  "backend": "replay",
  "evidence_path": "evidence.json",
  "output_dir": "output",
  "internal_language": "English",
  "report_language": "Chinese"
}
```

Request paths are relative to the config file. The `--responses` path is relative
to the shell's working directory. Dry run validates configuration only: it neither
reads response files nor creates output directories or calls providers.

`evidence.json` must match `EvidenceSnapshot`: exact ticker/cutoff, verified source
text hashes, source-backed facts and eligible publication/financial periods.
It is not a folder of prior role reports. Mutable documents acquired after the
historical cutoff cannot be treated as historical evidence merely because they
have an earlier publication date. Only validated accession-specific SEC archive
documents have the supported immutable-filing exception.

`responses.json` maps roles to ordered reply arrays. Every reply has `data` matching
that role's stage schema and `usage` containing nonnegative token counters. Initial
runs need planner, business, accounting, expectations, management, valuation and
editor replies, plus two challenger and two verifier replies. Follow-ups require
additional replies. See `tests/test_research_engine.py` for wholly synthetic examples;
they are software fixtures, not factual investment research.
Replay token counters come from the supplied responses, not new billable inference;
run metadata labels them `saved_response_counters`.

The CLI exits 0 for a completed review-required preview, 1 for a recoverable stopped
run, and 2 for configuration/input failure or an unconfigured live backend. A zero
exit code is not financial acceptance.

## Artifacts and recovery

Each run writes `reader_report.md`, `audit_report.md`, `evidence.json`, `research.json`,
`valuation_inputs.json`, `valuation_results.json`, `quality.json`, `run_metadata.json`,
and the hashed `result.json` manifest. Checkpoints reside under `stages/`. Optional
`dossier_dir` appends a review-required dossier and adds `dossier.json`; it never
replaces accepted history or advances coverage.

Use a dedicated empty output directory. A matching interrupted run can reuse
validated stages. A completed replay is read back without rewriting its artifact
hashes or telemetry. Changed settings/evidence/responses require a new directory;
corrupt or weakened manifests fail visibly. Do not delete checkpoints to conceal
unknown usage. A dispatched call interrupted before usage is known blocks automatic
new calls with `usage_incomplete`.

English analysis and direct Chinese synthesis remain the default. Financial value
placeholders such as `{{fact:revenue-id}}` are rendered by code before final review,
including the distinction between billion and 亿. This is not a language-cost
benchmark; live language comparisons remain an explicit later experiment.

## Service integration and limits

`run_research(request, ResearchServices(...))` accepts evidence, model and optional
storage implementations. The safe public SEC/IR fetcher and baseline collector are
available as separate, explicitly instantiated services. SEC acquisition requires
an identified User-Agent and explicit instrument identity; no email, CIK, ADR ratio
or currency is guessed. No data subscription is purchased.

Model services must honor each call's `timeout_seconds` and `max_output_tokens` and
return actual usage; synchronous injection cannot forcibly interrupt an arbitrary
noncompliant service. Admission reservations, finalization reserves, overshoot
recording and unknown-usage stops are implemented. A production process supervisor
and live-backend integration remain prerequisites for unattended live execution.

Financial modules contain tested deterministic mechanics, not calibrated NVDA/AMD/
TSM/INTC/AVGO models. Model assumption coverage and source-linked opening inputs are
required for even an illustrative DCF; unsupported inputs produce unavailable
valuation. The one-period statement bridge and sector driver helpers are not yet
wired into a complete multi-period company model. Synthetic tests do not prove
financial source coverage, semantic verification quality, or investment usefulness.

Accepted priors must actually have existed by the new research cutoff. The update
summary identifies input changes but does not claim full economic change attribution.
Forecast vintages use their real creation time separately from the evidence cutoff;
only subsequently published comparable actuals are scored.

## Before a live pilot or release

Complete the remaining gates in `DEEP-RESEARCH-PLAN.md`: calibrated company schedules,
eight-quarter/five-year source reconciliation, reliable context selection, hard live
deadlines, semantic claim review, matched single-agent comparison, human-reviewed
reference cases, and explicit pilot authorization. No production-default switch is
part of this preview. A live pilot is a validation exercise, not release acceptance.
