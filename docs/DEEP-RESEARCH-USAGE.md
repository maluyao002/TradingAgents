# Deep Research V2: offline preview

This is an opt-in, research-only implementation. It does not change the existing
weekly runner, trading graph, model settings, schedules, publishing or AMD task.
No live provider is instantiated without the separate opt-in flag. Reports are
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

### Evidence-led English development revision

For the current NVDA/Robinhood campaign, explicitly add:

```json
{
  "quality_revision": "evidence-led",
  "report_language": "English",
  "additional_report_languages": [],
  "valuation_method": "fcff",
  "share_count_basis": "point_in_time_diluted"
}
```

Use `valuation_method: "equity_fcfe"` for an eligible broker-equity case, not
industrial FCFF. This method requires common-net-income anchors, explicit retained
capital assumptions and cost of equity. It does not subtract customer liabilities
as corporate debt or add back SBC. It is not a regulatory-capital certification.

Only if a current diluted capitalization is unavailable, a caller may explicitly
select `share_count_basis: "latest_quarter_diluted_proxy"`. This binds the denominator
to a recent **quarterly duration** `weighted_average_diluted_shares` fact at the
common opening period end. It does not relabel it as an instant. The resulting
per-share output is an illustrative proxy and carries a mandatory reader caveat;
it is not a supported current fully diluted capitalization or an accepted target.

The revision adds question-specific local passages, valuation-blocker follow-ups,
an explicit investigation ledger, typed `{{calc:ID}}` numerical insertions,
source footnotes and a complete linked limitations audit. A verifier checks the
**exact rendered text** and supplies a disposition for every input limitation.
Reader-covered caveats require traceable literal excerpts; operational/immaterial
audit-only decisions require explicit rationale. One bounded repair is allowed;
unresolved critical findings withhold the reader. Materiality/entailment remain
model judgments requiring independent review, not guarantees from substring tests.

Additional artifacts are `reader_limitations.json`, `reader_verification.json`,
`calculated_values.json` and `investigation.json`. Numerical calculations are
assumptions-based, distinct from reported facts, and rounded to two decimals for
display while exact values and input/result hashes remain in the audit. The reader
bytes are not rewritten after verification; later failures remain in the audit.

Local follow-up searches the frozen full text and may revise analyses; it does not
acquire missing external sources. Repeated packets and budget limits stop retries.
Keyword matches never automatically resolve investigations. External acquisition
still requires an injected follow-up provider; missing consensus or independent
evidence stays missing. This is not a completed autonomous discovery capability.

`foundation` remains the compatibility default for historical checkpoints and
explicit recovery. New revision requests have separate identities and must use new
output directories. Existing weekly/AMD settings and saved reports are unchanged.
The offline reference registry can verify the supplied English comparator files:

```sh
.venv/bin/python scripts/research_reference_cases.py validate \
  --manifest benchmarks/research-v2/reference_cases.json \
  --analysis-cutoff 2026-09-17 \
  --reference /absolute/path/HOOD_Equity_Research_2026-09-18.html \
  --reference /absolute/path/NVDA_Equity_Research_R2a_2026-09-17.html
```

Registration proves byte identity only. It does not certify facts, select a held-out
window, replace human review or establish a measured quality improvement.

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

## Explicit live Codex entrypoint

For a request with `backend: "codex"`, an authorized caller can use:

```sh
.venv/bin/python -m cli.research --config request.json \
  --allow-live --codex-home /absolute/path/to/existing/isolated-runtime
```

An existing authenticated, isolated runtime is required; the shared `~/.codex`
runtime is rejected by the existing adapter. The command does not switch models,
copy authentication, create a login, publish, or schedule anything. Model/effort
choices are preflighted exactly, with no silent fallback. Generic API execution is
still unconfigured.

Prefer an explicitly frozen evidence snapshot for the first comparison. If no
snapshot is supplied, the request must contain complete instrument identity and
the environment must provide `SEC_USER_AGENT` identifying the SEC client. The live
worker then acquires a limited public SEC baseline with raw caching under the run
directory; missing sources/coverage remain explicit gaps. No live call occurs in
`--dry-run`, even when these live flags are present.

The adapter uses per-turn structured output and provider usage events, as described
in the [official Codex App Server documentation](https://learn.chatgpt.com/docs/app-server).
The research service uses closed, required-field wire schemas; valuation maps are
serialized as unique key/value entries and converted back to the unchanged domain
contracts. Unknown schemas are rejected before adapter construction. These follow
the [strict structured-output requirements](https://developers.openai.com/api/docs/guides/structured-outputs).
Malformed output is not silently repaired; known usage remains charged. Missing
usage blocks subsequent calls. Codex's output-token allowance is advisory here,
not a hard provider cap; metadata says so, and observed overshoot stops the run.

The September 17 first NVDA pilot attempt failed at the planner provider boundary
without returned usage counters. The authorized one-call diagnostic retry streamed
output but hit the adapter's 10,000-notification guard before completion. Neither
is successful live validation. See the progress log; preserve both failed attempts
and their unknown usage. The adapter now separates up to 250,000 validated nonempty
agent-message chunks (4,000,000 cumulative characters) from 10,000 control events.
Empty chunks and retired/unrelated traffic still use the control limit. Completed
text has its own unchanged 4,000,000-character limit, and transport message-byte,
absolute-deadline and tool-isolation protections remain in place. Streamed chunks
are not reconstructed; completed items remain authoritative. These engineering
bounds are not token allowances or evidence of successful live research.

The subsequent authorized one-call diagnostic succeeded within the original
timeout after 258.30 seconds and 10,010 text chunks, returning a validated planner
reply with 37,272 known tokens. This validates the repaired planner path only;
English/Chinese final reports remain ungenerated. Keep all three attempts separate;
the successful diagnostic neither settles earlier unknown usage nor creates an
engine checkpoint that can safely resume those failed runs.

Further preflight found that this schema-valid diagnostic used generated claim
IDs as finding evidence links, which the engine correctly rejects. It also mixed
languages internally. Prompts now state exact evidence-reference semantics,
stage-qualified generated IDs and one language per call; the diagnostic now runs
the engine evidence-ID check plus planner count/link/uniqueness checks. The earlier
successful diagnostic therefore demonstrates transport/schema success only, not
engine acceptance. Its original result and usage remain preserved.

The subsequent full NVDA run completed six research calls but stopped on a
provider structured-output schema rejection at valuation. It has 197,067 known
tokens plus an unknown valuation-call amount; its unsettled checkpoint must not
be reset for automatic continuation. Valid stages are retained in `run_final_1`,
but neither English nor Chinese final reports have been generated. See the
progress log for the required scoped schema diagnostic/recovery next step.

## Explicit recovery after an acknowledged unmeasured call

Normal resume still stops on unknown usage. The separate recovery command requires
explicit acknowledgement and a new output directory; it never resets the old run:

```sh
.venv/bin/python -m scripts.research_diagnostic --config original_request.json \
  --output valuation_diagnostic --role valuation --source-run failed_run \
  --codex-home /absolute/path/to/isolated-runtime --allow-live
.venv/bin/python -m scripts.research_recover --config original_request.json \
  --source-run failed_run --output recovered_run \
  --valuation-diagnostic valuation_diagnostic \
  --codex-home /absolute/path/to/isolated-runtime \
  --allow-live --acknowledge-unknown-usage
```

This deliberately narrow migration supports the validated six-stage wire-v1
prefix ending at management. Request, evidence, model settings, payload and output
hashes must match; mismatches fail rather than rerunning the prefix. Wire-v2
valuation uses exact decimal strings with local finite-value/domain checks. A
successful diagnostic is bound to its exact payload and source hashes and reused
without another valuation call. Missing financial inputs still yield unavailable
valuation, not fabricated assumptions.

Recovery provenance distinguishes imported historical counters, the diagnostic,
and new provider calls. Known prior spend and elapsed time remain charged against
the original budget; unknown historical spend cannot be guaranteed to fit a
measurable token ceiling. Cumulative usage therefore remains incomplete and the
reported token count is a known lower bound. New unknown usage still stops further
admissions. The old artifacts and unsettled flag remain unchanged. Recovery is not
production acceptance or permission to publish, trade, or change schedules.

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

For paired final reports from one analysis, set `report_language: "English"` and
`additional_report_languages: ["Chinese"]` (or reverse them). The additional
editor translates the verified primary draft; a separate verifier checks it
against the original and the shared evidence. Specialist research is not rerun.
Both finalization calls share the same budget and durable usage ledger. Successful
pairs add `reader_report_en.md` and `reader_report_zh.md`; `reader_report.md` remains
the primary language. A failed translation/review is withheld and the run is not
marked complete. This paired export measures incremental finalization usage, not
the cost of researching independently in Chinese versus English.

## Service integration and limits

### Already-downloaded public sources

`scripts.research_local_evidence` imports an explicitly curated local packet when
ordinary browser downloads are available but direct retrieval is not. It performs
no network requests and does not authenticate the original URL: the operator must
establish the document's public origin. A manifest has `schema_version: 1`, `ticker`,
`gaps`, and `sources`. Each source requires `id`, `local_file`, `original_url`,
`title`, `publisher`, `published_at` (nullable), timezone-aware `retrieved_at`,
`kind`, and `acquisition` (`browser` or `local`). Unknown publication availability
remains unknown and is not admitted to historical reasoning.

```sh
.venv/bin/python -m scripts.research_local_evidence \
  --manifest /path/to/manifest.json --output /path/to/new_frozen_packet \
  --pdf-python /path/to/python-with-pypdf
.venv/bin/python -m scripts.research_hood_model_facts \
  /path/to/new_frozen_packet /path/to/new_derived/evidence.json
```

The importer archives exact raw bytes, extracted text, hashes and extractor
metadata. PDF text extraction is not OCR or proof that charts/tables were completely
represented; omissions and empty text pages are explicitly retained as gaps. It
refuses existing destinations, including a concurrently created empty directory.
`evidence.json` is published last as the completeness marker. A publication failure
may leave an inspectable partial directory without that marker; use a new output
path, never treat the partial packet as complete or overwrite it to hide failure.
`--pdf-python` is unnecessary for HTML/text-only packets.

The HOOD adapter is deliberately narrow: the specific FY2025 and Q2 2026 release
tables, common-shareholder income rather than consolidated income, mechanical TTM
bridges and an explicitly labeled Q2 diluted-share proxy. It verifies the frozen
packet and writes a new derived snapshot outside it. It does not create forecasts,
normalize unusual gains, certify retained regulatory capital, or turn customer
assets into corporate cash. Other release layouts fail rather than silently reuse
column positions.

`model_sensitivity.recompute_sensitivity` accepts explicit rate/growth axes for
either typed DCF model. Every cell is recomputed and hashed to its exact inputs and
result, with the same current-share denominator. It is a standalone mechanical
stress utility, not yet a reader-integrated economic scenario engine or a forecast
calibration method.

### Opt-in bounded finalization

Set `quality_revision: "evidence-led-bounded"` for the new finalization path.
The existing `foundation` and `evidence-led` request modes remain available; do not
change an interrupted request's revision in place or overwrite historical artifacts.
This revision is offline-tested, not yet live-validated or a production default.

The editor retains every unresolved limitation while the audit retains full ledger
provenance. A separate global factual review and lossless coverage batches (at most
12 issues / 12,000 serialized UTF-8 bytes each) verify the exact same reader hash.
Oversized individual issues fail admission instead of being truncated. A repaired
reader requires fresh review of every batch. Explicit factual contradictions block
export, including in older modes. Literal coverage excerpts do not establish
semantic entailment: global factual review remains mandatory.

Planning reserves estimated headroom for remaining analysis and finalization before
admitting optional investigation. It is not a hard token guarantee. Bulk closure
verification is skipped in this revision because no current producer supplies
explicit evidence-linked closure candidates; all unresolved issues remain open.
Selective closure and semantic rerun routing are still future work. Failed review
retains an unverified reader candidate and partial batches for diagnosis, not a
publishable report. Unknown usage stops subsequent admissions and automatic retry.

### Separate one-call valuation diagnostic

After explicit live authorization, `scripts.research_model_probe` accepts a normal
research request with a frozen `evidence_path`, a fresh output directory and an
existing isolated Codex home:

```sh
.venv/bin/python -m scripts.research_model_probe \
  --config /path/to/request.json --output /path/to/new_probe \
  --codex-home /path/to/isolated-runtime --allow-live
```

It makes at most one valuation inference after preflight, capped at a 300-second
call deadline / 360-second parent deadline. Output-token allowances are advisory;
admission uses a documented byte heuristic, excludes provider overhead, and cannot
guarantee actual spend. Overshoot and incomplete usage remain visible. Historical
anchors must bind eligible reported or source-derived facts; forecast choices need
explicit defensible assumptions, not invented reported data. `model=null` remains
valid when the economic basis is unsupported.

Success requires all five exact hash-bound artifacts: `probe.json`, `proposal.json`,
`calculation_result.json`, `usage.json`, and `provenance.json`. A successful null
proposal validates the diagnostic path, not a populated valuation. This tool is not
engine recovery: it imports no earlier stages, acquires no sources, retries nothing,
settles no prior unknown usage, and produces no final report or accepted target.
The failed source run must remain unchanged.

### Runtime boundaries

`run_research(request, ResearchServices(...))` accepts evidence, model and optional
storage implementations. The safe public SEC/IR fetcher and baseline collector are
available as separate, explicitly instantiated services. SEC acquisition requires
an identified User-Agent and explicit instrument identity; no email, CIK, ADR ratio
or currency is guessed. No data subscription is purchased.

Injected model services must report actual usage and declare their output-cap
behavior. Synchronous injection alone cannot forcibly interrupt an arbitrary
noncompliant service. The live CLI adds a POSIX parent supervisor and per-call Codex
deadline; cleanup protects transport ownership from timeout interruption. Admission
reservations, finalization reserves, overshoot recording and unknown-usage stops
are implemented. Detached descendants observed by the supervisor are cleaned up;
unobserved rapid double-fork/reparenting requires OS containment beyond this trusted-
worker boundary. The supervisor is not a sandbox for arbitrary hostile code.

Source context is deterministically excerpted with exact character offsets and
original content hashes. Truncation/omission is visible in the prompt and report.
Structured records remain retained; the complete serialized prompt is subject to
budget admission. Keyword excerpts do not establish complete semantic coverage.

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

## Before production acceptance

Complete the remaining gates in `DEEP-RESEARCH-PLAN.md`: calibrated company schedules,
eight-quarter/five-year source reconciliation, evaluated context selection and live
recovery, semantic claim review, matched single-agent comparison, human-reviewed
reference cases, and explicitly authorized live pilots. No production-default switch
is part of this preview. An initial engineering validation run is not a completed
M6 benchmark or release acceptance; the plan's full milestone gates remain required.
