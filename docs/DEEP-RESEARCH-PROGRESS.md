# Deep Research V2 implementation log

Start with the [current status and roadmap](DEEP-RESEARCH-STATUS.md) for the
reconciled priorities, deliverables, user checkpoints and artifact register. This
file preserves historical checkpoints: older “next” / “not yet” statements are
dated history, not active instructions or current blockers.

Branch: `codex/deep-research-v2`, based on `ebc3383` (the current weekly-report
checkout). Live schedules, provider settings, historical reports, and user tasks
are unchanged. Local commits are used as reviewable checkpoints; no remote push
or pull request is implicit.

## Frozen baseline and separate review branch — September 18, 2026

Publication follow-through: the user confirmed keeping the work on the feature
branch, not `main`. The frozen branch/tag and separate review branch were then
published to origin, and [PR #10](https://github.com/maluyao002/TradingAgents/pull/10)
was opened with base `codex/deep-research-v2` and head
`codex/research-baseline-review`. The publication blocker described below is historical
and resolved. Hosted CI and automatic PR review are checked separately from the
local validation record; no merge or change to `main` is authorized by this action.

Hosted CI subsequently exposed a Python 3.10 importer compatibility defect:
`datetime.fromisoformat` on that version rejects a trailing UTC `Z`. The review
branch normalizes only that suffix to `+00:00` before parsing, preserving offset
validation and adding explicit UTC/offset/malformed-input regressions. This fix
does not change the frozen feature branch or historical evidence artifacts.

At the user's request, `codex/deep-research-v2` is preserved at
`4cbbb0799887c0cd2fb0fd7f634db2ea77dc59cc`, with local annotated tag
`research-v2-baseline-20260918`. Review fixes are isolated on
`codex/research-baseline-review`; the baseline branch and tag have not moved.
The intended PR base is the frozen feature branch, not `main`. Stage 1 work has
not begun and no merge is implicit.

Focused Sol/high and Terra/medium review covered the latest source/forecast/model
and probe/CI changes, not an exhaustive re-audit of every historical commit.
All identified findings were resolved and independently rechecked:

- CI's offline test step now excludes `integration`, rather than relying on missing
  credentials to prevent a live DeepSeek test. Collection with a sentinel key
  proves that the live class is deselected without dispatching a request.
- Authored-probe tests declare their POSIX-only file-guard requirement.
- Three repository-wide Ruff import-order failures are fixed.
- Market snapshot/metadata publication uses synced staging and exclusive links;
  collisions never overwrite an existing output. Hosted review subsequently found
  a race in ownership-checked rollback, so failures now preserve all published paths
  and clean up only private staging names. Final ownership checks detect observed
  replacement. Directory-entry durability/order and crash-atomic pairing are not
  claimed: consumers must validate both artifacts and the snapshot hash, not rely
  on evidence-file presence as a completion marker.
- Forecast rows require exact label/cell boundaries. Reused generated facts must
  retain exact source locations. The original valid filing spans are preserved,
  and the existing 89-fact snapshot still validates idempotently, unchanged.

Local review-fix commits: `8ceff80` (CI/portability/lint) and `238046f`
(publication/provenance). Final validation: **1,996 passed, 1 skipped, 1 deselected,
18 existing warnings, 65 subtests passed** using `pytest -q -m 'not integration'`
with the configured PDF runtime. Focused source/model tests passed 41 cases;
repository-wide Ruff and `git diff --check` pass. No research-model calls occurred.

Public publication is blocked pending explicit confirmation: origin
`maluyao002/TradingAgents` is public, and the approval check rejected publication
of the 51 unpublished baseline commits without that confirmation. No workaround,
remote push, tag publication or PR creation was attempted after the rejection.
The Git checkpoint excludes ignored reports, credentials and local run artifacts;
it is not a remote backup of those files. Once confirmed, publish the baseline/tag
and review branch, create the focused PR, check hosted CI/comments and address
actionable feedback. No automatic merge or Stage 1/live-research execution follows.

## Roadmap reconciliation and workspace organization — September 18, 2026

The original M0–M6 plan, follow-up commits, actual artifacts and current code were
reconciled into `DEEP-RESEARCH-STATUS.md`. It separates engineering deliverables,
live-run authorization and human/product acceptance, and indexes current versus
historical outputs. Luna/medium inventoried the workspace; Sol/high independently
checked the milestone/ownership mapping. Review clarified limited SEC acquisition,
the missing reviewed-label workflow, Stage 3 report-admission implementation and
the distinction between development pilots and M6 release pilots.

Stale Chinese-first design/usage instructions were aligned with the already agreed
English campaign. Original release gates and historical run records are preserved.
Local Markdown links and whitespace checks passed; no runtime code or configuration
changed, so the previous test count remains the latest executed suite, not a new run.
No reports, branches, generated assets or credentials were deleted or relocated;
no live calls, external operations or schedule/task changes occurred.

## Reviewed NVDA scenarios — September 18, 2026

This checkpoint advances the agreed financial-input → economic-assumption →
deterministic-model sequence. It does **not** mark the full English research
campaign or production acceptance complete. English remains the only new output
language; Robinhood remains the contrasting company, not an FCFF-template target.

| Current step | Delivered | Boundary / remaining work |
| --- | --- | --- |
| 1: Normalize financial and market inputs | Eight exact-source facts added, preserving all 81 prior facts; D&A, pretax/tax comparators and reinvestment context; four dated public market sources cached | Full operating-WC, available-cash/securities/lease and point-in-time share reconciliation still incomplete |
| 2: Author economic scenarios | Explicit ten-year downside/base/upside growth, margin, tax, WC, capex, D&A and SBC paths; dated schedules, rate rationale and terminal ROIC/reinvestment bridge | Conditional analyst judgments, not company/consensus forecasts, probabilities or human-accepted cases |
| 3: Integrate and review | Typed FCFF compilation through the existing deterministic engine; all 30 annual cash-flow rows, positive modeled FCFF, three terminal reinvestment reconciliations and 27 recomputed sensitivity cells; English valuation memo | Standalone reviewed-case adapter, not yet integrated throughout the core planner, challenges and final reader |
| 4: Conditional bounded diagnostic | One Sol/high call completed in 63.29 seconds under 600-second call/parent bounds; complete returned usage | Returned `model=null` / valuation unavailable; no automatic retry, stage replay or final-report publication |

### Inputs and economic choices

Local artifacts: `reports/RESEARCH_MODEL_20260918/market_cache`,
`nvda_forecast_evidence.json`, `scenario_packet_1`, `conditional_review_1.json`,
`compiled_model_1`, and `model_probe_1` under that same campaign directory.
Generated artifacts are local/ignored outputs; implementation and this log are
version-controlled. Old snapshots and failed run directories are unchanged.

The eight new facts are tied to exact original table/line/offset/hash locations.
They include consolidated H1 D&A and compatible pretax/tax values; no quarter
D&A or statutory operating tax is inferred. The new snapshot contains 89 facts
and nine sources. It retains the prior source documents and advances its cutoff to
`2026-09-18T07:37:28.073967Z` for separately acquired market evidence; this is not a
retroactive change to the old report vintage.

Public references are the [Fed H.15 Treasury table](https://www.federalreserve.gov/releases/h15/),
[Damodaran ERP page](https://pages.stern.nyu.edu/adamodar/New_Home_Page/home.htm),
[Damodaran sector beta table](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/Betas.html),
and [September Fed projections](https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm).
The cached observations use a September 16 nominal 10-year Treasury reference,
September 1 ERP, January 2026 US semiconductor cash-corrected unlevered beta and
September 16 long-run macro context. Retrieval/first-observed availability and
dataset observation dates are separate. No subscription or new paid data provider
was purchased. These rate inputs do not independently corroborate NVIDIA demand.

Discount references are 12.669%, 11.220% and 10.185%; terminal growth is 2%, 3% and
4%, with terminal ROIC of 15%, 20% and 25%. These are authored conditional cases,
not measured company WACC. The older ERP is explicitly held fixed against a newer
Treasury yield. GAAP after-SBC margins keep SBC's economic cost without an addback
or duplicate charge. Terminal capex is solved to match g/ROIC reinvestment after
D&A and working-capital needs. Sensitivities hold operating cash flows fixed;
they do not claim economically recalibrated reinvestment at each changed growth rate.

Deterministic per-current-share present values are approximately $42.15 / $148.57 /
$297.80, with terminal value representing 30.4% / 44.8% / 56.3% of enterprise value.
These are **conditional development outputs, not price targets or trading returns**.
Their spread is driven by the authored economics and does not establish probability
or predictive accuracy. The incomplete equity bridge and share proxy remain material.
The memo includes every annual revenue→NOPAT→reinvestment→FCFF bridge and falsifiers.

### Review, verification and version control

Sol/high workers owned financial normalization and the generic compiler;
Terra/medium owned market parsing and independently reviewed the probe;
Astra/high independently reviewed the economics, packet and release gates.
Review found and fixed loss of material limitations, incomplete manifest admission,
self-attested review approval, unsafe path re-reading, and relative-path resolution.
Regression tests include coherent replacement of the entire embedded review/proposal
chain, actual packet tampering, symlink substitution and nonregular-file rejection.

The actual automated review clears only conditional modeling and one untrusted
no-retry diagnostic. The probe receives an independently selected approved-review
digest, verifies every packet artifact, parses captured verified request bytes and
rebuilds the exact base proposal before dispatch. This establishes artifact integrity,
not authenticated human sign-off. Review limitations propagate into the memo and
candidate. A model-returned change is not covered by the original review.

Final full non-live suite: **1,982 passed, 2 existing skips, 18 existing warnings,
65 subtests passed**. Focused final probe/scenario suite: **52 passed**.
The test suite requires the configured PDF runtime for its PDF fixtures. No live
provider integration suite was run as part of these regression counts. Changed-file
Ruff and `git diff --check` passed. Sixteen manifest/result-bound artifact hashes
were independently recomputed; the model input and authored-context hashes remained
unchanged before/after the diagnostic.

Reviewable local implementation commits:

- `1e27b7f`: exact-source financial normalization and dated market-input parsing.
- `d6980c9`: reviewed conditional scenario compilation, economic cases and memo.
- `d3aa1ac`: externally anchored reviewed-packet admission for the bounded probe.

### Live diagnostic outcome and remaining work

The new candidate passed offline preflight with a 327,197-token **admission
heuristic envelope**, including the 16,000-output-token allowance; this is not
measured usage or a guaranteed provider cap. The request retains a 1.5-million-token
admission budget and 600-second call/parent deadline.

The one authorized Sol/high call completed in **63.29 seconds**, with zero retries:
**87,993 input + 3,169 output = 91,162 total tokens**. Reasoning output is 2,447
tokens within output, not additional usage; cached input is zero. Counters are
complete and no observed budget overshoot occurred. This brings the English
campaign's known report-runtime lower bound from 719,674 to **810,836 tokens**;
the prior NVDA and HOOD failed calls still have unknown usage. Coding, review and
coordinator agents are excluded because their usage counters are unavailable.

Operational status is `model_probe_completed`, but the proposal is `model=null`
and deterministic calculation is `unavailable`. The diagnostic must not be labeled
a successful valuation or completed final report. It recognized the source-derived
historical anchors and conditional forecast status, but raised five objections:

- The delivered excerpts do not expose the exact selected Treasury maturity/yield
  and semiconductor beta row, preventing reproduction of the discount rate from
  its received evidence. Cached source availability alone is not delivered evidence.
- Cash-only net debt leaves securities, leases and availability/classification
  unresolved in the enterprise-to-equity bridge.
- The blanket no-external-funding assumption is not reconciled against purchase,
  cloud, investment, lease and capital commitments.
- Terminal ROIC/reinvestment is calculated in the sidecar but is not an explicit
  typed model-input contract available for independent reconstruction downstream.
- July 26 opening proxies are not rolled forward to the September 18 cutoff.

The deterministic memo remains a separately reviewed **conditional case**. The
model's rejection is retained unchanged, not overwritten with the authored model.
No second call or full-report run followed. Exact input/candidate bytes remained
unchanged; the saved payload hash reproduces. The new evidence-delivery defect
should be addressed offline before another live run, without suppressing the
substantive capital/funding/timing objections.

Post-call Astra/high review independently reproduced the exact payload and
confirmed **two delivery gaps**: market-input values/selected rows and the economic
audit's terminal ROIC/formula were not delivered, although their artifact hashes
were present. Hashes are not usable supporting evidence. This is not evidence that
the underlying rate or reconciled terminal calculation is numerically wrong.
ROIC can remain an analyst assumption carried in a typed derivation; it need not
become another calculator primitive or be “proved” by issuer forecasts.

The reviewer also distinguished operating-asset calculability from accepted equity
valuation. A disclosed static July opening proxy can support a September conditional
calculation, but not certify September capitalization. Positive modeled FCFF does
not establish company-wide funding sufficiency: broader funding should remain
`not assessed` until reconciled. Commitment reconciliation must avoid deducting
costs, capex or working-capital needs twice. Lease classification must likewise be
consistent with the operating-profit convention; automatically subtracting every
lease liability could double-count costs. These distinctions should become explicit
result-contract states rather than a single all-or-nothing “model available” gate.

Next core work, irrespective of a mechanically successful diagnostic:

1. Deliver the verified market-input and economic-audit contents plus pinned source
   rows, headers, observation dates, units and provenance. Add offline assertions
   that each material input's support reaches the actual payload; source-ID presence
   and a hash alone are insufficient. Separate operating-asset calculability,
   equity-bridge completeness, funding assessment and opening-date reconciliation.
2. Reconcile operating working capital, securities/leases/cash availability and
   diluted capitalization; broaden issuer history and independent demand evidence.
3. Promote the reviewed-case adapter into an explicit core-engine input contract,
   carrying the same assumptions, hashes, challenges and caveats through valuation,
   scenario tables, narrative synthesis and exact-reader verification.
4. Calibrate Robinhood separately with the common-equity cash-flow model and its
   capital-retention/regulatory economics; do not transplant NVDA industrial FCFF.
5. Produce verified English final readers and then compare them with the supplied
   Claude HTMLs. A valuation development memo alone cannot establish superiority
   in business depth, narrative synthesis, independent evidence or final readability.

No trading, position sizing, scheduling, Chinese generation, user-task changes,
remote push or final-report release is included in this checkpoint.

## English NVDA / Robinhood campaign — September 17, 2026

The approved sequence now uses Robinhood as the early contrast and English-only
new outputs. Existing Chinese compatibility and its comparison TODO are preserved.
The campaign used opt-in `quality_revision: "evidence-led"`. A subsequent,
offline-tested `evidence-led-bounded` revision adds bounded finalization; it has
not yet been exercised live. Historical `foundation` request identities and
recovery payloads remain compatible.

| Approved step | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1: Freeze evaluation cases | Both supplied English HTMLs registered by basename/hash; decisive questions, required areas and defect-case inventory saved | Human-reviewed primary-source labels, held-out windows and matched model runs; comparator files are not gold |
| 2: Reader and repair | Separate lossless audit, footnotes, typed per-limitation dispositions, exact rendered-text/hash verification, one budgeted repair, warning/critical withholding after repair | Real-report readability and semantic verification evaluation; literal excerpts do not prove entailment |
| 3: Targeted investigation | Bounded question-specific full-text passages; valuation blockers included; cycle/provenance ledger; explicit evidence-linked closure; tests for revised analysis and still-open gaps | Autonomous external discovery/follow-up provider, semantic classification of unstructured gaps and selective dependent-role reruns |
| 4: NVDA numerical slice | 52 original facts plus 29 source-bound anchors, subsequently eight forecast-context facts; typed model references; three reviewed conditional scenarios and sensitivities in the September 18 memo | Full equity/funding/timing reconciliation, core-reader integration and live verified report; no accepted target |
| 5: HOOD contrast | Independently tested common-equity cash-flow DCF, source binding, attribution/scale/timing checks, matching strict wire codec, no customer-debt/FCFF mixing; six primary issuer PDFs frozen and 32 source-bound facts normalized | Bounded live contrast and economic review; automatic discovery, capital-retention calibration and regulatory-event model remain incomplete |
| 6: Broader evaluation/release | Existing evaluation contracts retained; engineering regressions expanded | Matched workflow versus acquisition experiments, broader companies/windows, human/blind review, longitudinal and release gates |

The source-bound NVDA enrichment is a narrow development adapter, not eight-quarter/
five-year coverage. It produces 81 total facts with unchanged source bytes and
retained original fact objects. Working capital is an explicitly qualified
aggregate-row proxy; cash-only net debt excludes securities/leases and does not
certify cash availability. An optional quarterly weighted-average diluted-share
proxy remains a duration fact, must be the latest eligible quarter and is visibly
labeled in per-share outputs. Default point-in-time binding is not relaxed.

Independent review caught and fixed material-caveat disappearance, missing terminal
review dispositions, audit/verification ID disagreement, segmented common-income
binding, and a stale-quarter share proxy. The numeric catalogue now includes method,
denominator basis, input/result hashes, opening inputs, rates, and discount timing,
separate from reported facts. Materiality and forecasts still need independent
financial review. A successful illustrative calculation is not production acceptance.

Initial campaign coordinator full non-live suite: **1,806 passed, 2 skipped, 18 existing
warnings, 65 subtests passed**. Separate final attribution/materiality/recovery tests
passed 18 cases. Changed-file Ruff and `git diff --check` passed. A broader research
test-file Ruff invocation also surfaced two pre-existing import-order warnings in
untouched diagnostic/stage-instruction tests; they were not silently rewritten.
The actual original recovery source and diagnostic still validate with source
identity `8a9c4e24312e85ef44242f5df98605f307a939b7043baaefe6e033846a5ad40a`.

Commits in this campaign:

- `698aa1b`: English-first NVDA/Robinhood design and sequence.
- `ef91aa0`: immutable comparator reference registration.
- `70fc87e`: bounded broker equity-cash-flow calculator and independent math tests.
- `1586128`: local targeted passages, pure reader/audit rendering, investigation ledger.
- `519c6f5`: frozen NVDA anchor normalization without rewriting source evidence.
- `34ad11b`: opt-in engine integration, strict provider schemas and exact-report gates.

Sol/high workers owned reader, retrieval, investigation and financial components;
Terra/medium owned reference registration. Independent Sol/high review led to the
fixes above. Worker token totals are unavailable and are not included in report
runtime usage. No scheduling, trading, publishing, default application change,
remote push or new Chinese run occurred.

The bounded English-only live NVDA run is under
`reports/RESEARCH_EN_20260917/nvda_run_1`, using the new 81-fact snapshot and a
1.5-million-token/90-minute ceiling, one investigation cycle and a finalization
reserve. A first launch was denied by automatic privacy review. Read-only provenance
checks established that the payload consists only of public NVIDIA IR documents,
derived facts and the public-company research mandate—not reference HTMLs, user
holdings, credentials or chat history. The same launch was then approved; no
restriction was bypassed. The run subsequently stopped at `verify_investigations`
with `ModelCallTimeout`: one 300-second call attempted to review a 234-entry ledger,
all entries still classified `unclassified`. It completed 13 earlier stage calls,
including the bounded local follow-up, but never reached editor/final verification.
Elapsed time was 2,195.76 seconds. Known usage is 472,031 input plus 95,426 output
= **567,457 tokens**, with 36,419 reasoning tokens included within output. The failed
call returned no counters; total usage remains incomplete, not zero or reset.
`reader_report.md` is explicitly a diagnostic stub, **not a final research report**.
The failed run and its unsettled dispatch are preserved. A separate one-call
valuation diagnostic was requested asynchronously from the user; it must not run
without approval acknowledging the unknown spend. Offline diagnostic implementation
and unrelated HOOD work can continue.

Robinhood's original direct-download blocker was resolved through ordinary browser
access to public issuer links. The Q2 2026 10-Q, FY2025 10-K, two earnings releases,
Q2 call transcript and August operating update were downloaded. No CAPTCHA,
security interstitial, login or paid service was bypassed. The earlier asynchronous
request for user-supplied files is no longer needed. The September 18 comparison
report remains an editorial comparator, never substitute primary evidence.

The independently reviewed standalone sensitivity utility (`18c37f2`) recomputes
every FCFF/equity-DCF rate/growth cell and binds it to exact model/result hashes.
It passed 11 focused tests and the combined 56-test valuation subset. This is a
mechanical sensitivity, not economic scenarios or yet an integrated reader table.

The first live NVDA valuation pass accepted the historical anchor availability but
returned no model. Its unsupported-input list mixes genuine evidence gaps with
analyst modeling choices (including forecast dates and labels). The bounded
investigation subsequently timed out as recorded above; this is evidence of a remaining
forecast-assumption workflow gap, not successful completion of the numerical slice.

The Robinhood packet's current-cutoff import and normalization passed independent
review. It retains six exact raw PDFs and extracted text at cutoff
`2026-09-18T05:32:58Z`; the undated transcript is archived but excluded from model
reasoning. The two issuer releases supply 27 reported facts and five TTM derived
facts. Revenue is 4,932 USD million and common net income is 2,072 USD million;
the Q2 share proxy is 912 million over 91 days. The common-income bridge preserves
the distinction from consolidated income and does not invent an after-tax RVI gain
adjustment. Key table pages were visually checked as well as parsed.

Reviewed local commits `6eed589` and `5c5dc35` cover immutable offline import and
HOOD normalization respectively. The latest full non-live suite with bundled PDF
fixtures passed **1,847 tests, 2 existing skips, 18 existing warnings and 65 subtests**.
All four importer review findings (PDF extraction gaps, future timestamps,
concurrent destination overwrite and optional PDF runtime) are resolved and tested.

The bounded English-only HOOD run is under
`reports/RESEARCH_EN_20260917/hood_run_1`, using the explicit equity-cash-flow method
and share proxy. It has the same per-run budget limits as NVDA and no new Chinese
output. It stopped after planner, challenger, business and accounting at a
`CodexTransientError` before the next analysis completed. Elapsed time was 653.83
seconds. Known usage is 87,211 input plus 29,391 output = **116,602 tokens**, including
3,712 cached input tokens and 8,107 reasoning output tokens within those totals.
The failed call's usage is unknown; no automatic retry was attempted. The recorded
exception category does not establish the underlying provider/network cause.
No authored/verified final report was produced. The case differs in company,
method, source cutoff and a clarified analyst-
assumption mandate; it is a development contrast, **not** a matched causal experiment
isolating company, prompt, acquisition or language effects. The two runs together
have a known lower bound of **684,059 tokens**, excluding both unknown failed calls,
the coordinator and subagent work. A completed-report preference comparison is not
possible from diagnostic placeholders.

### Offline finalization repair and next decision

Commits `0797018` and `77aab41` preserve transitive financial-source citations and
introduce opt-in bounded finalization. Derived TTM references now include every
eligible source in their input ancestry, not just the carrier source. Reader
verification separates global factual review from lossless batches of at most 12
limitations / 12,000 serialized UTF-8 bytes. Every batch checks the same frozen
rendered-reader hash. Missing, duplicate, foreign or unresolved dispositions block
export; explicit factual contradictions are blocking even without separate warnings.
Unverified candidates and partial review records survive failures, never as finals.

Finalization planning subtracts optional investigation costs before admitting them
and accounts for remaining reconciliation, claim review, all requested languages
and one repair pass. This is an admission estimate, not a guaranteed spend cap.
The bounded revision skips the bulk investigation-closure call while no producer
supplies explicit closure candidates: all issues stay open and all enter coverage,
including those beyond the retrieval cap. Selective evidence-linked closure and
semantic dependency routing remain unfinished; this repair does not silently
declare 234 issues immaterial or resolved. Synthetic tests cover their retention,
20 coverage batches and timeout/unknown-usage stops without automatic retry.

A separate one-call valuation probe (`89a8278`) is implemented for the analyst-assumption
workflow. It distinguishes reported/source-derived historical anchors from explicit
forecast assumptions, records exact request/source/model/result provenance, and
requires all five hash-bound artifacts before CLI success. Known usage survives
output-validation or cleanup failures; unknown usage remains unknown. It performs
no acquisition, stage replay, automatic retry or report export. The maximum call
deadline is 300 seconds and parent supervision 360 seconds; output limits remain
advisory. No live probe has run. User approval acknowledging the failed calls'
unknown usage is pending before one separate NVDA probe; no HOOD retry is authorized
by that proposed diagnostic.

Independent Sol/high and Astra/high reviews drove citation, artifact-integrity,
complete-coverage, contradiction-gate and reserve-accounting fixes. These changes
are engineering progress, not accepted source-calibrated reports or completed plan
milestones. Next: authorize the small valuation probe, assess its economic validity,
then separately decide how to validate bounded full-report finalization. Reader
scenario/sensitivity integration, calibrated company forecasts, independent evidence,
matched English comparator review and broader release gates remain outstanding.

Final combined offline validation after all repairs: **1,894 passed, 2 existing
skips, 18 existing warnings and 65 subtests passed** in 26.77 seconds, including
bundled-PDF fixtures and local process/socket tests. The external integration test
file was excluded; no live provider calls were made by this suite. Changed-file
Ruff and `git diff --check` passed. Independent final review found no remaining
P1/P2 findings in the bounded-finalization or one-call probe changes. All commits
remain local on `codex/deep-research-v2`; no push, publication or schedule change.

### Authorized 600-second valuation diagnostic follow-up

The user authorized exactly one NVDA valuation diagnostic with a 600-second cap.
Commit `891d43a` allows an explicit request to extend the call to 600 seconds and
caps parent supervision at 600 seconds as well (bounded cleanup may follow).
Default 300-second calls retain 360-second supervision. No prior request or failed
run was altered. Six new deadline cases passed; the full offline suite passed
**1,900 tests, 2 existing skips, 18 existing warnings and 65 subtests**. A first
restricted-sandbox supervisor run failed because process inspection was unavailable;
the same focused suite passed all 76 cases with authorized process permissions.
Ruff and whitespace checks passed.

The one-call Sol/high probe at
`reports/RESEARCH_EN_20260917/nvda_model_probe_600_1` completed in **106.10 seconds**,
with no retry. Usage is complete: **30,085 input + 5,530 output = 35,615 tokens**,
including 4,257 reasoning-output tokens within output; cached input was zero.
All five output artifacts passed the CLI's path/hash checks. The frozen source
hash before/after matches, and all 52 files checked across the two failed runs and
NVDA input directory are unchanged. Campaign known usage is now **719,674 tokens**;
the two earlier failed calls remain unknown and coordinator/subagent usage is not
included.

The valid proposal remains `model=null`, with calculation status `unavailable`.
It recognizes the source-derived revenue, working-capital and cash-only net-debt
anchors and the explicit share proxy. Unlike the earlier proposal, it does not
treat forecast dates or period labels as unavailable reported facts. Remaining
objections concern the cost-of-capital and perpetual-growth basis, calibrated
multi-period revenue/margin/tax paths, and normalized reinvestment, working-capital
and stock-compensation assumptions. This demonstrates a successful diagnostic
runtime, not a populated valuation or a causal proof that the prompt change alone
caused the narrower objections: this standalone probe omitted prior stage context.

Recommendation: build a source-grounded assumption/calibration packet with explicit
analyst ranges, economic rationales and limits before another valuation-bearing
full run. Separate externally observable inputs from model conventions and uncertain
forecasts; do not force a target by relabeling unsupported inputs. Neither a further
probe nor a full-report retry was launched. Bounded full-report finalization and
completed-report comparator evaluation remain unvalidated live.

### Forecast-assumption package preparation

The user approved the next offline preparation step after the 600-second probe.
New FCFF-only contracts distinguish historical anchors, external market inputs,
analyst forecasts and model conventions; missing/draft/reviewed states are explicit.
Raw evidence hashes, point-in-time eligibility, derived ancestry, accounting basis,
unit/period alignment, range bounds and review metadata are checked. A structurally
reviewed package is not an executable model or an accepted forecast. No model engine
or prior request identity was changed.

The standalone preparation CLI emits a source-bound NVDA workbook, assumption JSON,
historical calibration, exact captured evidence and a last-written hash manifest.
Four opening anchors use normalized base USD/share units and scale-1 conventions;
no future consumer should multiply them by a million again. Six historical
operating-margin, capex/revenue and SBC/revenue ratios are recomputed from compatible
Q2/H1 facts. Quarter/half-year overlap and cash-outflow sign conversion are explicit.
No historical ratio was promoted into a forecast range. Exact-offset passages bring
the existing consolidated D&A filing table and guidance into the preparation packet:
un-normalized local evidence is not the same as unavailable source evidence.

The resulting package has 20 entries: four historical anchors and six conventions
in draft, plus ten explicitly missing forecast/funding/discount inputs. It is not
ready for model review or another valuation call. The original missing economic
calibration remains work to do, not a fabricated numerical target. Next substantive
work is normalized D&A/tax/reinvestment schedules, dated external cost-of-capital
inputs and explicitly justified company forecast ranges, followed by semantic
review and typed-engine integration. No external acquisition, paid service, live
report/probe call, scheduling or trading action occurred in this preparation step.

Luna/medium mapped the frozen fact inventory; Sol/high implemented the contracts;
Terra/medium independently reviewed the builder. Review drove the scale-1 default
and captured-evidence publication boundary. Source checks detect observed drift but
do not lock the original path against later changes; the manifest binds captured
bytes. Incomplete publication has no completion manifest and is never overwritten.

Local checkpoints: `ff5265e` (contracts and validation) and `c750961` (offline NVDA
builder and publication tests). The reviewed preparation bundle is
`reports/RESEARCH_EN_20260917/nvda_assumptions_2`; `_1` is an earlier draft retained
for audit, not the handoff package. All four final artifact hashes, three source
passage offsets and exact captured/original evidence equality were checked. Its
read-only validation passes while correctly returning `ready_for_model_review=false`.
Final full offline suite: **1,933 passed, 2 existing skips, 18 existing warnings,
65 subtests passed**; 33 focused package/builder cases passed. Changed-file Ruff
and `git diff --check` passed. Independent review has no remaining P1/P2 findings.
No research-runtime tokens were consumed by this step; coordinator/subagent usage
is separate and unavailable in the report ledger. All changes remain local.

## Baseline (2026-09-17)

- Luna/medium read-only scout: 1,325 existing tests passed, two failed, one skipped,
  one deselected using the offline command below. The two failures are existing
  Unix-socket creation probe cases in the restricted sandbox (`TransportClosed`
  rather than expected `ProbeError`), not new research behavior. Do not hide them.
- Optional Bedrock dependency is unavailable; its existing test skips.
- Process-lifecycle suites are excluded from the initial fast baseline, not from
  eventual lifecycle validation. They spawn fake local processes, not live models.

```sh
.venv/bin/pytest -q tests --ignore=tests/test_codex_transport.py \
  --ignore=tests/test_weekly_process_cleanup.py -m 'not integration'
```

## Historical foundation checkpoint: tested offline preview

| Area | Implemented and exercised offline | Still required for milestone acceptance |
| --- | --- | --- |
| M0 | Typed/versioned contracts, independent package, dry-run/replay CLI, frozen input hashes, atomic checkpoints, budget reservations, scoring rubric | Matched legacy/V2/strong-single-agent experiment runners and reviewed reference corpus |
| M1 | Pinned-public-IP HTTPS, SEC/IR discovery, bounded fetching/cache, accession-aware financial normalization, news date/relevance/dedup rules, quarter derivation, explicit SEC baseline collector | Full eight-quarter/five-year reconciliation for five companies, transcripts/guidance history, broader material-event coverage and independent-source evidence |
| M2 | Planner and four specialists, blinded challenger, bounded follow-ups and thesis-revision fixtures, durable stage usage, safe resume, bounded provenance-preserving excerpts, explicit Codex adapter and POSIX hard-deadline supervisor | Live recovery validation, evaluated retrieval/context quality, cross-company assumption reconciliation and realistic coverage fixtures |
| M3 | FCFF/DCF, SOTP, comparables, sensitivity, reverse valuation, horizon bridges, dated discounting, integrated one-period statements and sector driver mechanics | Source-calibrated multi-period company models, full ADR/FX/corporate-action integration, independent financial review and economic constraints on realistic cases |
| M4 | Structured reports, Chinese financial-value rendering, separate claim/final-review hooks, limitations, hashed reader/audit exports | Measured semantic entailment quality, complete editorial numerical admission, report preference tests and production acceptance rules |
| M5 | Immutable dossiers, accepted-history protection, eligible priors, change metadata, real forecast vintage timing, comparable-actual scoring | Full evidence/expectation/assumption/value change attribution, dependency-aware cross-run reuse and calibrated forecast integration |
| M6 | Offline score contracts, synthetic defect fixtures, regression/recovery tests | Human-reviewed gold labels, matched comparison/language trials and explicitly authorized live pilots |

No whole milestone is declared accepted just because its mechanical tests pass.
The standalone replay path works. The live Codex entrypoint and supervisor are
implemented and tested with fake providers/workers, but have not been live-validated.
Live execution requires a separate `--allow-live` flag, an explicit existing isolated
runtime, and frozen evidence or SEC identification plus instrument identity.
Every current engine report is `Needs review / Unrated`; production acceptance is
deliberately disabled. See [usage and limitations](DEEP-RESEARCH-USAGE.md).

## Delegation

- Sol/high: independent design review.
- Luna/medium: baseline and neutral-utility scouting.
- Terra/medium: bounded CLI/contracts tests, then evidence normalization fixtures.
- Sol/high: deterministic valuation implementation and accounting edge-case tests.
- Coordinator: shared contracts, boundaries, integration, review, tests and commits.

Each worker owns disjoint paths and does not commit or initiate live research.
Delegation telemetry is not an efficiency benchmark; aggregate subagent token cost
is unavailable here.

## Review resolutions

Independent Sol/high review identified real contract gaps before orchestration:
token admission did not reserve concurrent calls; source hashes and publication
cutoffs were not authoritative; ADR identity was underspecified; derived-fact
cycles were possible; accepted artifact manifests could be empty; replay hashing
could reread changed files; storage lacked an injected protocol.

The follow-up hardening adds atomic reservations, source-text hash checks,
cutoff/reference validation, instrument identity, raw-times-scale semantics and
duration metadata, DAG validation, accepted artifact requirements, bounded read-
once input helpers, and a storage interface. Semantic verification and historical
source availability still require M1/M4 implementation and external acceptance.

Subsequent independent review led to tested fixes for:

- Weak completed-result manifests and mismatched ticker/cutoff on resume.
- Follow-up evidence bypassing baseline checks or replacing immutable records.
- Empty/blank reader sections incorrectly appearing completed.
- Unsupported valuations admitted by arbitrary rationale text.
- Discount exponents unrelated to forecast dates and quarterly terminal flows
  incorrectly capitalized as annual perpetuities.
- Edited news/IR content retrieved after a historical cutoff, cache raw/text
  mismatch, and cross-host redirects falsely receiving SEC provenance.
- Context-dependent Decimal calculations and levered/unlevered tax-shield errors.
- Backdated forecast vintages and later-created dossiers leaking into historical work.

## Verification

At the integrated offline checkpoint:

```sh
.venv/bin/pytest -q tests/test_research_*.py
# 297 research tests (all included in the passing full suite below)
.venv/bin/ruff check tradingagents/research cli/research.py tests/test_research_*.py
# All checks passed
git diff --check
# Clean
.venv/bin/pytest -q tests -m 'not integration'
# 1,603 passed, 1 skipped, 1 deselected; 65 subtests passed
```

The complete offline suite ran with permission to create local test sockets and
fake child processes. The two baseline sandbox failures passed in that environment;
the subprocess/socket subset separately passed 55 tests. The remaining skip is the
pre-existing absent optional Bedrock dependency; the deselected test is live
integration. Existing model-catalog warnings remain unchanged. No live research,
data purchases, publication, or schedule changes occurred.

The new supervisor also deliberately fails before launch when process inspection is
denied by a sandbox. Its process tests therefore require the same approved local-
process test environment. This is not bypassed or silently converted to success.

Additional reviewed runtime fixes preserve unsettled dispatch across interruptions
after provider return, prevent alarms from interrupting threaded transport cleanup,
subtract verified checkpoint elapsed time on resume, validate artifact hashes in
the parent, and attempt cleanup even when a later process inspection fails. Source
excerpt limits and financial bounds have explicit regression coverage. Official
OpenAI documentation was used to verify the reused adapter's structured-output and
usage boundary; the implementation does not invent a hard output-token cap.

## Commit checkpoints

- `a2b9135`: agreed design, delivery plan and baseline.
- `4116214`: M0 contracts, dry-run CLI, recovery primitives.
- `06b2c02`: evidence identity, budget reservations, replay integrity.
- `ca3ac32`: bounded public SEC/IR source access.
- `8726b6c`: deterministic valuation and horizon calculations.
- `c073ecb`: financial/news normalization and explicit SEC baseline collection.
- `3b4464f`: dossier history, prior eligibility and honest forecast vintages.
- `874e90f`: mutable-lookahead and source-provenance review fixes.
- `5c71004`: integrated statement mechanics and dated discount timing.
- `2849838`: recoverable offline orchestration and reviewed reader exports.
- `139f013`: usage documentation and explicit remaining acceptance gates.
- `1f07a7a`: bounded source context, quantitative admission and durable usage settlement.
- `968a3f3`: explicit live Codex worker, per-call deadline and parent supervision.

Later integration/documentation commits are available in the branch log. All commits
are local; no push, merge, deletion of old branches, or change to other user work
is included in this implementation task.

## Historical pilot decisions and work

### September 17 bilingual NVDA pilot

The user authorized one live NVDA pilot with English and Chinese final reports.
Commit `01f06e2` adds paired finalization using one shared research pass, a faithful
translation, separate verification, one budget/usage ledger and hashed outputs.
It also adds explicitly curated pilot acquisition/table helpers; these are not
automatic production discovery or calibrated financial models.

Five public NVIDIA sources were acquired and frozen at 2026-09-17 15:21:06 UTC,
with 52 source-table-bound financial facts. The paired-export regression subset
passed 69 tests; the research suite passed 309 tests; the full offline suite passed
1,615 tests and 65 subtests (2 skips, 18 existing warnings).

The first live attempt stopped at the planner provider boundary after 4.64 seconds
with `CodexInferenceError`, before a research stage completed. No substantive final
reports were produced. The provider returned no token counters; usage is unknown,
not zero. Its artifacts/checkpoint remain under `reports/NVDA_V2_20260917/run` and
must not be reset to conceal unsettled dispatch. The comparison in the parent
folder is explicitly interim. A separate bounded diagnostic retry was requested
from the user, not automatically launched.

Inspection found Pydantic/domain schemas do not directly meet the strict provider
wire requirements (all object fields required, no open dictionaries). The initial
error alone does not prove this was its cause. The schema adapter now uses closed,
required-field wire contracts and typed valuation input; unique key/value arrays
round-trip into the unchanged domain maps. Unknown schemas fail before adapter
startup; decoding failures preserve returned usage. Wire identity is versioned and
field-drift guards cover the financial dataclasses. Safe schema-error classification
does not expose upstream prose or authorize retries. Final offline validation:
**1,639 passed, 2 skipped, 18 existing warnings, 65 subtests passed**. The live
schema repair was then exercised in the explicitly authorized one-call diagnostic
retry described below, but did not produce a completed research response.

The diagnostic retry passed all model preflights and isolation settings, then
streamed output until **287.74 seconds**, when the adapter raised
`Codex turn exceeded the notification safety limit`. The exact observed trigger
was the local `_MAX_TURN_EVENTS = 10_000` cap, which counts text-delta notifications
alongside lifecycle/control events. It was not a reported subscription quota error
or the 300-second deadline. The desktop account had 57% weekly allowance remaining
at the check; this is not asserted to identify the separate runtime account.

Both attempts still lack returned token counters and remain usage-incomplete.
No further diagnostic call was launched. The original five-second error has no
retained precise upstream payload, so the schema defect is not retroactively
declared its proven cause. Final bilingual reports remain ungenerated.

The scoped diagnostic helper retains redacted protocol metadata and permits one
inference turn under parent supervision. It also preflights configured models.
After reviewing the diagnostic, token-delta log writes were removed to avoid
per-token disk overhead in future diagnostics; this change was not exercised by
a second live call. A completed validated planner reply, if any, is explicitly
separate from diagnostic logs. Next: redesign the stream/control-event bounds
while retaining deadline, payload-size and tool-isolation protections, then
validate before returning to bilingual generation. At that point the production
cap was unchanged.

The next user-authorized repair separates the 10,000 control-event limit from
250,000 validated nonempty agent-message deltas, bounded additionally by 4,000,000
cumulative streamed characters. Empty chunks, advisory messages and retired or
unrelated traffic still consume the control budget. Deltas require a started turn
and matching uncompleted agent-message item; completed item types cannot change.
The original absolute deadline, completed-output cap, transport message-byte
limit and isolation checks remain in force. Stream fragments are not retained.
Regression coverage exercises more than 10,000 legitimate deltas, all count/size
boundaries, Unicode, empty/malformed/out-of-order streams, usage retention and
interrupt/unsubscribe/invalidation on cap failure. One further diagnostic is
authorized under unchanged 300-second call / 360-second parent bounds, using the
same frozen evidence and planner configuration in a separate output directory.
Independent Terra/medium review found no remaining issue after checking an initial
false-positive empty-delta finding against the code and added regressions. Final
offline validation: **1,676 passed, 2 skipped, 18 existing warnings, 65 subtests**.

The authorized second diagnostic succeeded after **258.30 seconds** with the same
Sol/high planner, frozen evidence and 300-second call / 360-second parent bounds.
It received **10,010 agent-message delta events**, demonstrating why a shared
10,000-event control/stream cap was insufficient. The separate validated planner
reply and redacted diagnostic are under `reports/NVDA_V2_20260917/diagnostic_retry_2`.
Returned usage is complete: **23,154 input + 14,118 output = 37,272 total tokens**,
including 4,076 reasoning-output tokens (a subset of output, not added again),
with zero cached-input tokens reported. Exactly one planner inference was made;
no full-engine or bilingual finalization calls followed. The first two failed
attempts still have unknown usage and unchanged artifacts. This is successful live
planner/wire/stream validation, not full-pipeline or research-quality acceptance.
Next: an authorized fresh full-engine run, keeping the failed run's unsettled
checkpoint intact; this diagnostic artifact is not an engine resume checkpoint.

Preflight for the user-authorized full bilingual run found that the successful
diagnostic was **wire/schema-valid, not engine-semantic-valid**: its findings cite
generated `C1`-style claim IDs instead of eligible evidence IDs. It also mixes
Chinese into the English intermediate analysis. No additional inference was made
to discover this. The original diagnostic is preserved, not relabeled or patched.
Prompts now explicitly distinguish evidence references from generated record IDs,
namespace new IDs by stage (avoiding independent-stage collisions), preserve
question links and use only the current payload language. The planner is scoped
as a concise plan rather than duplicating a bilingual final report. Diagnostics
now also enforce evidence eligibility, planner question count, finding/question
links and unique claim IDs while preserving returned usage on semantic failure.
Full generation will use `request_final_1.json` / `run_final_1`, with unchanged
frozen evidence, model settings and budget. Prior failures remain untouched.

The prompt/diagnostic repair passed **1,682 tests, 2 skips, 18 existing warnings
and 65 subtests** and was committed as `0e9b1b0`. Independent Terra/medium review
found no remaining prompt-contract blocker. The authorized full run then completed
and checkpointed planner, independent challenge, business, accounting, expectations
and management. It stopped at the valuation provider boundary after **1,148.27
seconds**, with `CodexStructuredOutputError` (provider schema rejection), not the
streaming-event guard or a timeout. No English or Chinese final was generated.

Known spend for those six completed calls is **142,599 input + 54,468 output =
197,067 tokens**, including 17,164 reasoning-output tokens within output. The
valuation call returned no counters; aggregate usage is incomplete, not zero for
that call. The unsettled dispatch flag and all valid stages are preserved under
`reports/NVDA_V2_20260917/run_final_1`. No automatic retry/resume was launched.

Offline inspection confirms the valuation schema differs from the successfully
used analysis schema: it includes typed dates and Decimal number/string unions
with Pydantic-generated lookahead patterns. Those are compatibility candidates,
not a proven exact cause: the retained safe error class does not identify the
rejected keyword/path. Next is a scoped valuation-schema diagnostic and repair,
plus explicit recovery that retains the six valid stages and the failed call's
unknown usage. Do not clear `dispatched`, invent zero usage, or silently rerun the
entire research pass. Live valuation validation and continuation require direction.

The user subsequently authorized valuation-schema repair, bounded validation and
explicit recovery of the six-stage prefix. Wire v2 represents financial Decimals
as exact strings, avoiding Pydantic's generated lookahead regex while keeping
finite-value/domain validation and date validation. The old rejection did not
retain a precise keyword path, so regex compatibility remains a hypothesis until
the repaired schema is exercised. Nonportable regex patterns now fail offline.
The diagnostic supports a single valuation call using the original six analyses;
it checks source identity/artifact/stage hashes before provider startup and binds
a successful reply to its exact payload, source hashes and wire version. It does
not edit source checkpoints or settle prior unknown usage. Initial focused tests:
52 passed; real source-prefix verification passed without live calls.

The repaired schema diagnostic then succeeded in **59.31 seconds**, returning
**61,532 input + 3,091 output = 64,623 tokens**, including 2,033 reasoning-output
tokens within output. It returned a valid `model=null` proposal with explicit
missing inputs rather than an unsupported target; this validates the wire path,
not a populated valuation model. The artifact/provenance are retained under
`valuation_diagnostic_1`. The six source stages plus the diagnostic have **261,690
known tokens**; the original rejected valuation call remains unknown. Before the
probe, 1,699 offline tests and 65 subtests passed (2 skips, 18 existing warnings).

Explicit recovery is implemented with source/payload/provenance validation and a
new output directory. It imports the six stages and validated valuation reply,
seeds all **261,690 known tokens and 1,207.58 elapsed seconds** before the first
admission, and never changes the original incomplete-usage record. Imported calls
are not dispatched or charged again; any new unmeasured call stops continuation.
The accounting regression tests cover early stops and diagnostic replay, along
with tampering, symlinks/FIFOs, explicit authorization and bilingual outputs.
Integration validation: **1,712 passed, 2 skipped, 18 existing warnings and 65
subtests**. The next live stage is challenge reconciliation, followed by claim
verification, English editing/verification and Chinese translation/verification.

The authorized recovery completed successfully under `run_recovered_1`, producing
both English and Chinese research previews with `completed_needs_review`. All six
new calls returned complete counters; aggregate usage remains incomplete because
of the original valuation rejection. Known spend is **648,933 input + 100,189
output = 749,122 tokens**: 197,067 historical + 64,623 diagnostic + 487,432 new.
Reasoning output (35,133) is included in output, not added again. Earlier diagnostic
attempts and coding/reviewer agents are outside this ledger. Chinese translation
and verification add 185,102 tokens (92,213 + 92,889); this is not a controlled
Chinese-versus-English research comparison. Retained elapsed time is 2,320.45s.

Final checks passed: 11 artifact hashes, 17 unchanged source-run hashes, eight
sections per language, and identical ordering of all 46 fact insertions and
section evidence IDs. Both verifiers returned no critical findings; the combined
assessment has eight warnings and eight informational findings. Independent
Terra/medium review found a more coherent question-led narrative than legacy,
while Claude remains more complete as a valuation/scenario memo. Different input
scopes prevent a controlled quality claim. No price target or production acceptance
is supported by this issuer-only, truncated evidence packet.

Next quality work exposed by the actual rendered artifacts:

- Separate concise decision-relevant reader limitations from the full audit log:
  each current reader appends 175 bullets, overwhelming the main narrative.
- Fully localize the Chinese reader wrapper and audit presentation; its narrative
  is translated but raw gaps/status boilerplate still include English.
- Add a compact synthesis and ranked evidence/monitoring agenda, improve pinpoint
  citation scope, and remove stale placeholder/process prose from the reader.
- Supply complete relevant filing context, independent evidence and reconciled
  valuation inputs before attempting an accepted valuation-bearing report.

The saved pilot artifacts remain unchanged. The detailed three-way comparison and
usage breakdown are in `reports/NVDA_V2_20260917/comparison.md` (local run output).

Baseline review also illustrates why the Claude report is not a gold label and
why reviewers need checks: an initial criticism of its 18.6% EPS premium mistakenly
used Q2 instead of H1; H1 EPS $4.85/$4.09 supports 18.6%. That criticism was withdrawn.
Human source/model review and matched evaluations remain outstanding.

Next extend validation beyond this narrow pilot, and use verified reference
evidence to calibrate company models, coverage and comparison runners.
These are additional acceptance/implementation tasks, not silently completed features.
Before additional live pilots, obtain explicit authorization and agree the reference window; the existing NVDA
report and Claude comparison are candidate baselines, not gold factual labels.
Human source/model review and matched evaluations remain mandatory before acceptance.
The separate Chinese token-comparison TODO remains in `WEEKLY-REPORTS.md`.

Known operational boundaries: Codex output-token limits are advisory (actual spend
and overshoot are recorded); total wall supervision has bounded cleanup overhead;
resume cannot recover elapsed time that was never checkpointed before a hard crash;
very rapid unobserved double-fork descendants need stronger OS containment. These
limitations must be evaluated before unattended production use.
