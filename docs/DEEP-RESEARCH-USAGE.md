# Deep Research V2: preview operation and diagnostics

Document role: **operating reference**, not the active delivery plan. Start with
the [documentation index](README.md) and [current roadmap](DEEP-RESEARCH-STATUS.md).
Dated packet paths and stage-specific examples below illustrate their original
workflow; they are not authorization to rerun a live job or proof of current
report acceptance. Use fresh destinations and explicit bounded authorization.

This is an opt-in, research-only implementation. It does not change the existing
weekly runner, trading graph, model settings, schedules, publishing or AMD task.
No live provider is instantiated without the separate opt-in flag. Reports are
`Needs review / Unrated`, not accepted investment assessments.
For today's priorities and the distinction between engineering work and user
acceptance, see the [current status and roadmap](DEEP-RESEARCH-STATUS.md).

## Configuration and replay

### Typed historical cash-flow reconciliation (offline)

The optional cash-flow package `historical_reconciliation` selects a complete,
signed source-statement row set. It reconstructs reported CFO, attributes the
unchanged operating-tax/working-capital proxy residual, and distinguishes CFO
less asset purchases from issuer FCF after asset-principal payments. It does not
normalize earnings, approve forecast economics, or clear valuation/funding gates.
Absent selectors preserve legacy package bytes and saved preview identities.

The dated NVDA adapter accepts only the exact H1 FY2027 filing structure:

```sh
.venv/bin/python -m scripts.research_nvda_reconciliation_packet \
  --source FROZEN_REVIEWED_INPUT_DIRECTORY \
  --mapping EXACT_RESIDUAL_SOURCE_MAPPING_JSON \
  --output NEW_UNREVIEWED_PACKET_DIRECTORY
```

It appends nine typed facts, preserves source bytes, verifies the reused NI/SBC/
D&A/CFO rows, and drops old reviews from changed identities. Obtain and attach a
fresh operating review with `scripts.research_operating_review`; then call
`prepare_pending_cashflow(operating_source, draft_source, output)` from the
reconciliation adapter to evaluate the pending bridge. Obtain a separate current
cash-flow review and attach it with `scripts.research_cashflow_review`. All outputs
must be fresh directories. Independent review is an actual review step, not a
hash rewrite. These operations do not launch or authorize research.

Preview-12 readers include a code-owned table of separately reviewed cash-flow
assumptions and dates. Unreviewed/stale inputs remain withheld. The table is part
of the exact factual-review/provenance binding; old previews retain their original
rendering rather than receiving unverified new prose.

### Stage 2 financial-case preparation (offline by default)

```sh
.venv/bin/python -m scripts.research_nvda_financial_case \
  reports/RESEARCH_MODEL_20260918/scenario_packet_1 \
  reports/RESEARCH_STAGE2_20260919/case_1
```

The output must be new and outside the source packet. The command validates the
old manifest, extracts exact Q2 FY27 filing-note rows, reconciles classifications,
and produces a **draft** financial case, evidence-gap register (inside
`reconciliation.json`), source passages, guidance/forecast comparison,
three-scenario mechanical audit with 27 sensitivity cells, and a new
`scenario_packet/`. Historical packets and review records are not modified or
reused as approval. No provider is called. Equity/funding conclusions stay blocked.

Optional independent customer evidence uses a frozen cache:

```sh
.venv/bin/python -m scripts.research_nvda_financial_case \
  reports/RESEARCH_MODEL_20260918/scenario_packet_1 \
  reports/RESEARCH_STAGE2_20260919/case_with_customer_evidence \
  --demand-cache reports/RESEARCH_STAGE2_20260918/demand_cache
```

To acquire the single public Microsoft FY26 Q4 transcript explicitly, use
`scripts.research_nvda_demand_evidence --fetch --destination-cache NEW_PATH` via
the same Python module invocation. Fetching is opt-in, bounded and separate from
offline case construction. The actual retrieval time advances the new case cutoff;
no source is backdated to the old packet. Treasury/ERP/beta vintages remain explicit
older observations. The independent source is one commercial participant, not
broad customer coverage or a mapping from customer capex to NVIDIA revenue.

The parser is intentionally dated/source-specific. Changed row structure, units,
headers, missing counterevidence or changed source bytes fail closed. This workpaper
does not close the fiscal-period bridge, economic underwriting, distributable cash,
point-in-time diluted capitalization, opening-date roll-forward or company-wide
funding schedule. It is not yet the Stage 3 core-engine reader path.

### Stage 1 reviewed-input delivery (offline)

The reviewed NVDA scenario compiler now publishes `reviewed_inputs.json`,
`material_coverage.json` and `scoped_results.json` alongside the existing audit
bundle. It reads the already hash-verified packet bytes, not a second unverified
copy. The material envelope includes exact market rows with headers/dates/units,
historical facts and ancestry, complete assumptions/scenarios, and typed terminal
reinvestment/ROIC derivations. Forecast judgments remain labeled analyst inputs.

The bounded model probe rebuilds this envelope from its separately verified packet,
including older real packets whose authored context originally carried only hashes.
It checks the actual serialized model prompt before dispatch and persists coverage
in `provenance.json`. Omitted/changed material fails without spending a model call.
The envelope is included in admission estimates and is not silently truncated.
Legacy contexts whose packet has neither market nor economic material are not
Stage 1-assured; the full core research reader integration remains Stage 3 work.

For new compiled memos and scoped probe results, conditional operating-asset values
are separate from equity/per-share eligibility. The latter is withheld pending
reconciliation; company-wide funding and opening-date alignment are `not_assessed`.
Raw `compiled.json` and sensitivity numbers are retained for mechanical auditing,
not for publication as eligible targets. A passing material audit establishes
delivery/integrity, not a true forecast, full data coverage or human acceptance.

Portable tests reproduce missing rows, hash-only references, changed assumptions
and dropped terminal derivations. Optional local tests replay the preserved NVDA
packet into temporary output, without modifying original reports or dispatching
research calls. No live authorization is granted by offline compilation.

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
  "report_language": "English"
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

### One separately authorized reader revision

`cli.research_finalize prepare` and `run` accept `--revise-reader` for one new
revision of a **terminal `verification_failed` repaired candidate**. The source
must have complete usage, matching terminal reader/checkpoint identities and
settled coverage history. The legacy revision cannot renew itself. A completed
verification-only failure or numbered revision failure can instead enter the
versioned numbered transition described below, under a fresh bound allowance.
Use a fresh destination and a new plan-bound incremental authorization; neither
the flag nor a successful offline preparation is live-run permission.

```sh
.venv/bin/python -m cli.research_finalize prepare \
  --source-dir /path/to/failed-repair --config /path/to/new-request.json \
  --output /path/to/new-plan.json --revise-reader
.venv/bin/python -m cli.research_finalize run \
  --source-dir /path/to/failed-repair --config /path/to/new-request.json \
  --authorization-file /path/to/new-authorization.json \
  --codex-home /path/to/isolated-runtime --allow-live --revise-reader
```

The explicit `frozen-candidate-revision-v1` policy is bound into the plan,
authorization and service/cache identity. It leaves the default preview-12
rendering and historical payloads unchanged: the entire exact prefix must replay
before `revise_report`. `verify_revised_report` and every coverage batch receive
the new policy identity and must run against the new candidate, even if the writer
returns identical text. No old retirement or coverage decision attests new bytes.
Writer-path estimates and exact post-writer reserves include full factual/coverage
work; per-call and total bounds remain mandatory. A lost paid output is not
permission to redispatch or reset the allowance.

Only the exact known reconciliation-lineage obligation receives an opt-in
procedural applicability component. Its original text/ID stays open and protected
in the audit. Source-quality, financial, security and critical numerical caveats
are not blanket-reclassified. Exact reader excerpts remain mandatory; mismatches
are failures, never silently normalized. Failure still withholds export. Reader,
financial and production acceptance remain separate gates.

### Numbered, evidence-backed reader repair

The same `--revise-reader` flag selects `frozen-candidate-revision-v3` when the
source is a terminal failed `verify_frozen_report` or numbered revised reader.
Preparation binds the exact candidate, source writer, complete terminal review,
policy contract, eligible exact source-passage witnesses and full saved prefix.
Generation 2 uses `revise_report-2` / `verify_revised_report-2`; each later
generation requires a separate plan, fresh destination and explicit allowance.
There is no automatic retry loop or analyst redispatch. Incomplete usage,
missing coverage, inconsistent generation history and lost paid outputs fail
closed. Original policies and historical payloads retain their identities.

One writer is followed by new factual review and full atomic coverage. Every
terminal source finding needs an explicit follow-up; a corrected disposition
requires exact new-reader spans and exact evidence witnesses. Source passages
bind their source content hash and character bounds. Availability of a table,
arithmetic review and economic underwriting are distinct claims. A scoped
`review:cashflow_bridge` witness does not grant valuation or funding approval.
Old warnings remain immutable audit history, not automatic claims of current
evidence absence. Unsupported or still-open findings continue to block export.

Pre-writer admission reserves the complete reopened verification path with
growth allowances. The writer's exact prompt is checked before dispatch; once
the new candidate exists, exact factual and coverage prompt sizes and remaining
resources are checked again. Lossless sharing is opt-in to this policy; evidence
is not truncated. Estimates are not token/wall-time guarantees, and a passing
offline rehearsal is not an admitted reader or authorization by itself.

### Frozen-reader verification repair

Use `--repair-verification` (instead of `--revise-reader`) on both preparation
and execution to recheck a **terminal failed `verify_revised_report` candidate**
whose factual review is clean and complete coverage history/usage are settled.
This mode binds `frozen-candidate-verification-v2` into the plan and authorization,
replays the complete exact prefix, then permits only `verify_frozen_report` and
its coverage batches. It cannot write a new reader, change the candidate hash,
renew itself or redispatch a paid call whose output was lost. The exact factual
and complete coverage path is reserved before the first new call; unknown usage
stops further dispatch. Use a fresh destination and a separately authorized
incremental allowance, under the existing process supervisor and call deadlines.
The v2 policy enables lossless nested sharing of repeated context, using the
existing hash-checked decoder. Legacy prompt bytes remain unchanged. Exact packed
prompt sizes for the known factual/coverage path are checked against the provider's
local prompt cap before the first new call, and again before each dispatch; no
evidence is truncated to fit. A local size stop here preserves complete usage.

The new attestation explicitly distinguishes audit-only decisions (empty excerpt
fields) from material reader coverage (literal supporting spans). Saved malformed
responses are retained unchanged, not stripped or silently accepted. An exact
known mixed dependency-review obligation can be split only when current evaluated
operating and cash-flow reviews and calculation identities bind together; its
procedural history remains protected, and conditional-review economic/financial
limits remain reader-required. Historical payloads do not acquire this policy.
Any new warning or invalid disposition still withholds export. This is verification
of unchanged reader bytes, not fresh analysis, financial approval or user acceptance.

### Separate one-call valuation diagnostic

After explicit live authorization, `scripts.research_model_probe` accepts a normal
research request with a frozen `evidence_path`, a fresh output directory and an
existing isolated Codex home:

```sh
.venv/bin/python -m scripts.research_model_probe \
  --config /path/to/request.json --output /path/to/new_probe \
  --codex-home /path/to/isolated-runtime --allow-live
```

It makes at most one valuation inference after preflight. Explicit request budgets
can allow up to a 600-second call and parent deadline; the default 300-second call
retains a 360-second parent deadline. At the 600-second limit, the parent may stop
the worker before a full call allowance plus startup/cleanup fits. Process cleanup
can add bounded overhead after that deadline. Output-token allowances are advisory;
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

### Offline forecast-assumption preparation

`scripts.research_assumption_package` prepares the specific frozen NVDA FCFF case
without fetching data or calling a model. It requires the explicit quarterly share
proxy and the four exact opening-anchor fact IDs from the NVDA enrichment adapter.
It rejects other companies/methods rather than applying industrial assumptions to
Robinhood. This is a preparation/validation workflow, not an engine input adapter.

```sh
.venv/bin/python -m scripts.research_assumption_package \
  --config /path/to/nvda_request.json --output /path/to/new_package
.venv/bin/python -m scripts.research_assumption_package \
  --config /path/to/nvda_request.json --validate /path/to/new_package/assumptions.json
```

The bundle contains `assumptions.json`, `calibration.json`, the exact captured
`evidence.json`, a human-readable `workbook.md`, and `manifest.json` (written last
as the completion marker). Existing
destinations and outputs inside frozen evidence inputs are rejected. A publication
failure can leave an inspectable incomplete directory without a manifest; use a
new directory instead of overwriting it. The raw evidence hash binds the package,
and source bytes are checked before and during publication. The manifest binds
the captured evidence bytes, not continued immutability of the original path:
another process can change that path after a check. The bundle's captured copy
remains independently hash-checkable. Neither original source snapshots nor old
run artifacts are edited.

Entries distinguish historical anchors, external inputs, analyst assumptions and
model conventions, each with explicit `missing`, `draft` or `reviewed` status.
Historical numeric ranges must equal the exact normalized base-currency/share
fact value (including source-derived amounts). Generated amount/share scales are
1, consistent with those base-unit values; a future million-unit model needs an
explicit conversion, never an additional million-fold multiplication. Forward
ranges are ordered low/base/high fractions, not
percent strings or scenario probabilities. They require evidence context and a
rationale, and reviewed entries require reviewer identity and timestamps. Structural
validation checks provenance and numeric boundaries, not economic entailment or
whether a claimed human review actually occurred. Do not auto-fill review metadata.

The builder fills four historical anchors and proposed conventions as drafts;
forecast values, funding needs and cost-of-capital inputs stay explicitly missing.
Historical GAAP operating-margin, capex/revenue and SBC/revenue observations are
recomputed separately from compatible duration facts. Capex cash outflows are
negated explicitly. Quarter/half-year overlap is disclosed; these are not independent
samples or automatically justified forecast ranges. Source passages retain exact
offsets and hashes, including the filing's existing consolidated D&A table: lack
of a normalized D&A fact is not absence of D&A source text.

Read-only validation returns `ready_for_model_review`, not readiness to execute
the valuation engine. Missing/draft required entries remain blockers. A reviewed
package still needs explicit dated forecast schedules, conversion to typed model
inputs, semantic/economic review and engine integration. No ready package is
automatically generated, adopted, or sent to a model. Creation time is recorded
separately from evidence cutoff; a newly created historical-case package must not
be presented as a forecast that existed at the earlier cutoff. New external data
needs a separately validated snapshot/vintage under existing acquisition policy;
do not append later mutable data to the frozen old case.

### Reviewed conditional scenario development

The dated NVDA development workflow now has three separate layers:

1. `scripts.research_nvda_forecast_facts` normalizes exact consolidated D&A,
   pretax-income and tax/capex comparators from the frozen issuer documents. It
   preserves all original facts and source bytes; half-year facts are not relabeled
   as quarter facts, and effective tax rates are not statutory operating taxes.
2. `scripts.research_market_inputs` parses separately cached public Fed Treasury,
   Damodaran ERP/sector beta and Fed SEP pages. Source availability is conservatively
   first-observed retrieval time, distinct from the dataset observation date.
   `scripts.research_nvda_scenarios prepare` creates a **new-cutoff** seven-file
   packet with explicit analyst-authored paths and a manifest. It never changes an
   earlier frozen evidence vintage or claims those forecasts existed earlier.
3. `scripts.research_nvda_scenarios compile` requires a separately recorded actual
   automated review, bound to the manifest, assumptions and scenarios. It converts
   the reviewed paths to typed model inputs and calls the existing deterministic
   engine. Output is a conditional valuation memo, **not a final deep research report**.

The standalone market-evidence merge syncs file contents and uses no-replace links.
Existing files or symlinks are rejected. On errors, published paths are preserved:
automatic rollback could race another writer and delete its replacement. Only
private temporary staging names are cleaned up. Ownership is rechecked before
success, but paths are not locked against later changes. Directory-entry persistence
and ordering are not guaranteed across a crash: either file or any subset may
remain, and neither evidence nor metadata alone is a completion marker. Validate
both files and the metadata's snapshot hash before reuse. Retain incomplete output
for inspection and use a fresh destination when retrying.
Forecast-fact reuse validates exact source locations as well as values and periods;
prefixed/adjusted metric labels are not accepted as the requested GAAP row.

```sh
.venv/bin/python -m scripts.research_nvda_forecast_facts \
  /path/to/frozen_evidence.json /path/to/new_forecast_evidence.json
.venv/bin/python -m scripts.research_nvda_scenarios prepare \
  --config /path/to/nvda_request.json --evidence /path/to/new_forecast_evidence.json \
  --market-cache /path/to/public_market_cache --output /path/to/new_packet
# Perform independent economic/code review before recording an approval artifact.
.venv/bin/python -m scripts.research_nvda_scenarios compile \
  --packet /path/to/new_packet --review /path/to/actual_review.json \
  --output /path/to/new_compiled_model
```

The generic compiler accepts one to three FCFF cases with dated schedules, source-bound
opening inputs, compatible scales, reviewed ranges and explicit limitations. The
NVDA adapter supplies ten-year downside/base/upside paths; these are development
judgments, not consensus, management forecasts or probabilities. GAAP margins retain
SBC without a duplicate deduction. Terminal capex reconciles reinvestment to the
authored terminal growth/ROIC assumption. Positive modeled FCFF is checked in every
year but does not certify off-model financing needs. The rate is an all-equity
operating-asset reference with mixed-vintage public inputs, not a measured NVIDIA
WACC. The nine sensitivity cells per case hold operating cash flows fixed; they are
mechanical rate/growth sensitivities, not recalibrated economic scenarios.

Review records require `decision: conditional_modeling_cleared`,
`reviewer_kind: automated_agent`, actual reviewer/time, nonempty limitations,
`prerequisites: []`, and exact canonical `manifest_sha256`, `assumptions_sha256`
and `scenarios_sha256`. Do not invent clearance or automatically fill these fields.
Their integrity is checked; they are not cryptographic identity verification or
human sign-off. Reviewer and source limitations propagate into the model and memo.
All destinations must be fresh; `manifest.json` is the completion marker.

The opt-in one-call probe can consume the compiled base case:

```sh
.venv/bin/python -m scripts.research_model_probe \
  --config /path/to/new_packet/request.json --output /path/to/new_probe \
  --codex-home /path/to/isolated_runtime --allow-live \
  --authored-context /path/to/new_compiled_model/authored_context.json \
  --approved-review-sha256 SEPARATELY_SELECTED_APPROVED_REVIEW_DIGEST \
  --reviewed-packet /path/to/new_packet
```

Use only after live-call authorization. The approved review digest must be selected
from the actual approved record, **not taken on trust from the context**. The probe
verifies every packet file, uses captured verified request bytes, rebuilds the base
case and requires exact proposal equality before dispatch. Context and packet reads
are bounded and reject final-component symlinks/nonregular files. The full authored
context enters budget admission. Existing one-call/no-retry rules, returned usage
retention, evidence/context hashes and at-most-600-second call/parent bounds remain;
bounded cleanup can add overhead. A model reply is untrusted: changed assumptions
need a fresh review. This diagnostic does not import the case into every core
research stage, resume a failed run, settle old unknown usage, or publish a report.

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
