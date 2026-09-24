# Fresh NVDA validation and financial closure

> Historical delivery / validation record. Preserve its evidence and decisions;
> dated next steps and run allowances are not current instructions or reusable approval.
> Use the [current roadmap](../../DEEP-RESEARCH-STATUS.md) for active work and
> the [archive index](README.md) to navigate history.

## Scope and authorization

The user requested budgeting and bounded fresh analysis followed by typed
financial reconciliation, targeted evidence closure and distinct HOOD validation.
Standing approval covers reasonable bounded follow-ups. This attempt is separately
recorded; historical spent or unknown usage is not reset or imported as capacity.
Reader acceptance and production release remain user gates.

Development branch: `codex/research-fresh-validation`, forked from the reviewed
PR #23 correction `fcfcd52`. Neither `main` nor `codex/deep-research-v2` changes.
No merge of PR #23 is implied. Execution uses engine `research-v2-preview-10`.

## Attempt 1 budget

Frozen inputs: `reports/RESEARCH_SUBSTANTIVE_20260922/reviewed_inputs_1/`.
Request: `fresh_validation_plan_1/request.json` under the same root; output is the
new sibling `fresh_validation_run_1/`. No prior responses, recovery provider,
prior dossier, additional language or evidence-follow-up cycle is supplied.

| Phase | Planning tokens | Planning minutes |
| --- | ---: | ---: |
| Fresh independent challenge, planner, four analysts, reconciled challenge | 900,000 | 25 |
| Claim verifier, English writer, exact-reader factual verifier | 650,000 | 15 |
| Complete initial coverage (assume 16–20 legacy batches) | 400,000 | 20 |
| One writer repair, factual recheck and fresh coverage if required | 800,000 | 25 |
| Growth/cleanup contingency | 250,000 | 5 |
| Aggregate ceiling | 3,000,000 | 90 |

The engine separately ring-fences 1,200,000 tokens and 2,400 seconds from the
non-finalization analysis phase. This is part of, not additional to, the total.
The repair path is admitted against remaining capacity and may be withheld;
neither completion nor all planned calls are guaranteed. Per-call deadline is
600 seconds. Preserve `legacy-12` coverage; do not conflate this validation with
a coverage-policy or concurrency experiment. Sol/high handles analyses and
verification; Astra/high handles challenge and writing.

Sizing evidence: current delivered case context is 287,469 UTF-8 bytes; initial
retrieved evidence payload is 170,488 bytes. These are not token counts. The
September 21 run measured about 46k tokens for each blind/planning call,
104–107k per substantive analyst, 139k reconciled challenge, 142k claim review,
159k writing, 250k factual review and 14–17k per coverage batch. It stopped after
about 82 minutes with incomplete usage; those timings are warning evidence, not
proof this fresh attempt will finish. The older September 20 repair alone used
about 261k tokens and its factual recheck 250k. New evidence and model revisions
make these sizing analogues, never validation of current substantive outputs.

Every actual call uses the engine's serialized provider-boundary estimate and
output allowance for conservative admission. Complete coverage and repair paths
are replanned from the actual candidate. Provider output allowances and token
ceilings remain advisory/admission-and-post-call enforced, not provider-hard
spend caps. Unknown usage, unsettled dispatch, invalid output or a reached limit
stops this attempt; no automatic allowance renewal. A new candidate continuation
would require its own settled-usage, exact-input and budget checks.

## Required deliverables and gates

1. Preserve this budget, request, frozen input hashes and actual usage/latency.
2. Generate wholly fresh analysis and inspect the actual exported English reader:
   causal depth, explicit counterarguments/falsifiers, paragraph citations,
   concision, independent-source distinctions and correct conditional scope.
   A candidate or green writing check is not exported-reader/financial acceptance.
3. Only after the baseline attempt, implement typed source-grounded residual
   attribution with regression tests. Any evidence/case/package identity change
   clears dependent reviews; a separate review must bind the new exact inputs.
4. Close targeted evidence gaps and prove HOOD-specific corporate/customer cash,
   regulatory capital and obligations. Do not impose an industrial FCFF template.
5. Present the actual readers and remaining limits for user acceptance; do not
   activate schedules or release to production.

## Preflight outcome and explicit execution blocker

All 25 source-bundle artifact hashes passed verification. The financial case
remains draft; operating and cash-flow source/arithmetic reviews are current.
All six hosted PR #23 checks passed. No code implementation was changed for
this preflight, so the prior targeted regressions remain applicable.

Execution permission was denied **before process startup**: auto-review requires
explicit user consent to transmit the frozen NVDA evidence and internal case
materials to OpenAI's external Codex model service. The new run directory does
not exist; this attempt made zero research-model calls and exported no reader.
No alternate transport, indirect execution or retry bypass was attempted.
The 3m-token/90-minute allowance is unspent, not a renewed historical budget.
The preflight binds code `fcfcd52`; the subsequent `d9f4baf` commit is this
budget document only, with no runtime changes.

## Offline residual implementation handoff

Independent Sol/high inspection found that the previously described standalone
`nvda_cashflow_case_4_review/attribution.json` is absent. Its existing review notes
state the exact residual, but do not contain that claimed full attribution file.
Preserve them; do not manufacture a historical reviewer artifact. Newly checked
exact source spans and recomputed arithmetic are instead recorded as
`fresh_validation_plan_1/residual_source_mapping.json`, explicitly **not** typed
reconciliation or independent approval.

The source-statement split of bridge less reported FCF, in USD billions, is:
operating-tax proxy versus net income **−20.145402729651…**; the negative of
omitted noncash/other CFO adjustments **+18.549**; balance-sheet working-capital
proxy versus reported cash-flow movements **−2.354**. The sum is the unchanged
**−3.950402729651…** residual. Capex cancels. These are mechanical differences,
not a calibrated forward cash forecast or a single causal working-capital story.

Implementation after the baseline attempt:

- Add nine signed, H1-duration, USD-million `FinancialFact` records from exact
  filing rows: deferred tax, equity gains, other adjustment and six reported
  cash-flow movements. Reuse existing net income, SBC and D&A with filing checks.
- Add an optional typed historical reconciliation selector with complete-role
  validation. Rebuild reported CFO, retain the three residual components and
  fact ancestry, and distinguish `mechanically_attributed` from economic approval.
  Do not zero the existing residual or overwrite the proxy bridge.
- Reject missing/duplicate/swapped rows, wrong sign/basis/period, source changes
  and CFO subtotal mismatch; test fixed-precision decomposition and old packages.
- Produce a new immutable packet, clear obsolete dependent reviews, and obtain
  fresh operating/cash-flow reviews before controlled reader delivery.

Remaining financial work includes operating-tax economics, mixed working capital,
opening-to-cutoff roll-forward, commitment rights/timing, investment cash use,
guarantees, available liquidity and longer-horizon valuation/capitalization.
Independent evidence still needs counterparty utilization/collections/renewals,
guidance revisions and comparable competitive economics. HOOD needs corporate
versus customer assets, required regulatory capital, common-equity income and
dated dilution inputs; existing calculator mechanics do not establish generality.

## Authorized attempt 1 outcome: model preflight failure

The user explicitly approved transmission of the frozen evidence and internal
case materials to OpenAI's Codex service. The ordinary supervised invocation
then started, but stopped after **1.81 seconds** with `CodexSelectionError` in
the initial `independent_challenge` stage. No analysis response, writer response,
factual review or coverage disposition was produced. `reader_report.md` is only
the engine's explicitly labeled diagnostic placeholder, not an exported reader.

Read-only capability discovery, saved separately as
`fresh_validation_plan_1/runtime_catalog_diagnostic.json`, confirms that the
isolated runtime offers `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`,
`gpt-5.6-luna` and `gpt-5.5`; it does **not** offer the requested `gpt-6-sol`.
The coordinator incorrectly used a model ID available to coding subagents as if
it were also available to the research runtime. Those are separate catalogs.
The supported fallback is `gpt-5.6-sol` at the same planned efforts; Astra/high
can remain unchanged. No fallback or model substitution ran automatically.

The model service validates all selected model/effort pairs before its first
`complete_with_usage` call. Thus this exception path is a pre-inference selection
failure, not a substantive model failure. However, the engine reserves and marks
dispatch before entering provider preflight, so the immutable run records zero
reported tokens **with `complete=false` and `dispatched=true`**. Do not rewrite
that record as complete zero spend or import it into an accepted report. The
read-only capability check makes no inference call and transmits no NVDA payload.

Next execution should first validate every requested selection against this
runtime, then use a fresh explicitly bound request/output and the supported Sol
fallback. Resolve the conservative preflight/dispatch accounting distinction
before automatic recovery; no silent budget reset or historical artifact edit.
The baseline reader inspection, typed reconciliation and HOOD deliverables remain
open. User acceptance and production release are unchanged gates.

OpenAI Docs was used to check the capability-discovery route against the
[official App Server documentation](https://learn.chatgpt.com/docs/app-server);
the account/runtime availability conclusion above comes from the actual local
catalog, not from assuming that a documented model is available to this runtime.

## Authorized attempt 2 after updated routing and review fixes

The user requested review resolution first, updated subagent routing, and a retry.
PR #23's three new commitment comments are fixed in `28838dd` and resolved. A
Sol/high test worker and Sol/xhigh reviewer followed the updated local guide;
97 targeted tests pass. The reviewer also caught old completed-result reuse,
so this attempt uses engine preview-11. Validation branch execution revision is
`997eb08f6281d2bf4579de4290edac8ef0b854b0` (review fixes integrated locally,
not merged into `codex/deep-research-v2` or main).

`fresh_validation_plan_2/` records explicit authorization, the new request and
a successful read-only runtime preflight. Every selected model/effort pair was
advertised and all 25 input manifest entries passed hashing before dispatch.
Runtime fallback: Sol 5.6/high for routine analysis, Sol 5.6/xhigh for consequential
verification, Astra/high for challenge/writing; the unused events role maps to
available Luna 5.6/high. Coding subagents use their separately available GPT-6 IDs.

Request SHA-256: `503f2cd9c9721f2e86a66e8b798f7447f3355a0cb9ed0fd2a6098d578dc6ec59`.
The supervisor launched `fresh_validation_run_2/` after the successful preflight
at 2026-09-23 04:45:55 UTC. Bounds remain 3m tokens best-effort, 90 minutes total,
600 seconds per call, including the existing finalization/repair reserves. No old
analysis response is imported. Attempt 1 and its incomplete telemetry remain
unchanged; attempt-2 usage must not be described as complete campaign usage.

## Attempt 2 terminal outcome

The attempt stopped after **2,195.77 seconds (36m36s)** with `stage_failed` at
`verify_report`. The safe recorded classification is `transport_error`, exception
type `CodexInferenceError`; this is **not** a recurrence of the unsupported-model
selection error. The precise transport cause is not established by these records.

Nine stages completed with accepted outputs: independent challenge, planner,
business, accounting, expectations, management, reconciled challenge, claim
verification and editor. The failed exact-reader factual-verification call ran
for approximately 0.52 seconds and supplied no settled usage. No coverage batch
or repair ran. Neither the 90-minute overall deadline nor the 3m known-token
allowance was the recorded stop reason.

Known reported usage is **1,091,847 input + 68,064 output = 1,159,911 tokens**,
including 23,681 reasoning tokens within output, not additional to it. Cached
input is reported as zero. Overall attempt usage is **incomplete** because the
failed call has unknown usage; these numbers are not a complete total or proof
that the failed call cost nothing. Earlier attempt/campaign unknown usage remains
unchanged as well.

`reader_verification.json` records `exported=false`, `reviewed_report=false` and
zero coverage batches; admission remains incomplete/blocked. An authored draft
exists, but `reader_report.md` is not an admitted final reader. The retained
candidate hash is
`aea622fc67a4161b9c116ce5102d55ddd7f63e2b3efc0027ba922d156a0c9dd1`.
No reader acceptance, financial acceptance or release conclusion is claimed.

The temporary monitoring heartbeat is paused after this terminal notification.
No retry, budget renewal, historical artifact edit, PR merge or production change
was made. The next safe step is offline diagnosis of the transport boundary and
recovery eligibility; any further paid attempt needs separate authorization and
must preserve the unknown usage rather than reset it.

## September 23 runtime alignment and authorized diagnosis

The user authorized the recommended runtime/model-selection updates, safer
diagnostics and bounded investigation without further supervision. Implementation
is isolated on `codex/codex-runtime-model-alignment`; neither main nor the frozen
`codex/deep-research-v2` checkout is changed by this work.

The standalone research CLI was 0.153.4, separate from the desktop's coding-agent
runtime. The official updater installed 0.156.1. A metadata-only check in the
existing isolated research home now advertises GPT-6 Sol/high and xhigh,
GPT-6 Luna/high, and GPT-6 Astra/high. No credentials were copied or reconfigured.
The previous Sol 5.6 selections remain unchanged in historical run requests.

Offline reconstruction of attempt 2 matched all eight pre-editor input hashes,
the saved reader candidate, and its rendering provenance. The factual-review
boundary contains 1,173,651 input bytes; its serialized turn request is about
1.27 MB, below the local 4 MiB transport limit. An in-memory transport test passes
serialization without sending a request. This rules out that local outbound
limit, not every possible server/runtime rejection. The historical error lacks
the underlying subtype and cannot establish an external outage.

The existing finalization-continuation admission correctly rejects this source:
the candidate has no completed factual review, and source usage is incomplete.
Do not weaken this gate or turn an offline fixture into a validated continuation.
Instead, the next diagnostic makes only one fresh factual call on the exact
saved reader using GPT-6 Sol/xhigh, with a 600-second call limit, a 660-second
overall budget and a 1.5m-token best-effort ceiling (conservative byte-based
admission plus post-call accounting, not a provider-hard output cap). It must
use a fresh, hash-bound capsule, preserve historical unknown usage, and cannot
export a final report, reuse its result as a recovery attestation, or retry itself.

### Confirmed exact-reader rejection

`factual_diagnostic_sol6_1` reproduced an immediate `turn/start` RPC rejection
(`-32602`, invalid parameters) on CLI 0.156.1 / GPT-6 Sol xhigh. After adding a
narrow, tested classifier that never saves raw provider prose,
`factual_diagnostic_sol6_2` identified `transport_input_length_limit` and the
runtime-reported maximum length **1,048,576**. It stopped after 0.997 seconds.
The exact prompt is 1,168,617 UTF-8 bytes. The server's length unit was not retained;
a conservative UTF-8-byte preflight can stay within either a byte or character
limit without claiming that the server's unit is known.

This establishes a request-admission defect in the integration: the local 4 MiB
JSON-RPC guard did not enforce the tighter Codex text-input bound. It reproduces
the same exact-reader boundary that failed in attempt 2; the old artifact itself
still has only the generic transport reason and remains unchanged. It is not a
financial/news-source outage. A separate approximate local tokenizer count was
301,484 tokens, but model context exhaustion is not the observed rejection and
must not be substituted for the confirmed input-length classification.

Both diagnostics reported zero tokens with `complete=false`; zero reported is
not complete zero usage. No coverage, repair, final report export, acceptance or
financial approval occurred. The follow-up repair uses lossless table encoding
for repeated object fields and a local conservative input-size check, rather than
truncating evidence, changing the reader or increasing the transport limit.

### Packed-request diagnostic outcome

At revision `89dd630`, lossless shared-context/table encoding reduced the exact
prompt to **998,865 UTF-8 bytes** (1,003,899 bytes including the complete model
input boundary). Decoding reproduces the original domain payload, reader and
provenance exactly; all eight reconstructed prefix input hashes still match.
The Codex service identity includes the new encoding version so old completed
responses cannot silently satisfy the changed provider boundary.

`factual_diagnostic_sol6_packed_1` used plan hash
`72e4cd937b314d92e9b3541172a25f01d868f890b7c98491e1c21b8522627d72`.
It passed the earlier immediate input-length rejection, but stopped after
**213.38 seconds** with `protocol_invalid_item` in the adapter's item-lifecycle
validation. The saved failure does not distinguish a malformed item, mismatched
completion or unfinished item; those possibilities must not be presented as a
confirmed cause. No structured factual reply was admitted. Reported tokens are
zero with **incomplete telemetry**, not zero cost.

Read-only inspection of the isolated runtime's timestamp-bounded logs found an
internal provider-stream reconnect at 08:09:20 UTC after a WebSocket connection
reset. This is external transport evidence, unlike the earlier input-admission
defect, but does not establish that it caused the later item-lifecycle failure.
No raw event payloads, provider text or credentials are added to these records.
The next offline change records finite lifecycle subtypes and counts while
preserving strict rejection, allowing any separately bounded follow-up to
distinguish integration incompatibility from malformed runtime events.

PR #24 is stacked on PR #23. Initial hosted CI exposed stale fundamentals model
fixtures and coverage tests whose fixed byte-budget/wire-hash assumptions changed
with encoding. The repairs retain the old v1 boundary hash as a content-equivalence
check, measure the current coverage reserve to test real pre-dispatch rejection,
and use the active fundamentals model. Hosted review also found that the native
API reasoning-model allowlist omitted GPT-6 Sol/Luna; `9261fd5` fixes effort
forwarding and known-model menus, with a regression for every profile pair.
The integrated affected tests pass (96 tests and 66 subtests); separate adapter
and resource-diagnostic tests pass (136). No local full-suite rerun was needed.
No full report, reader acceptance, financial approval or release is established
by this diagnostic.

### Successful exact-reader transport and factual diagnostic

After independent review of the finite lifecycle diagnostics,
`factual_diagnostic_sol6_lifecycle_1` completed successfully at code revision
`eef39815faca691ba30cda537d7ab5d892ad54a7`, with plan SHA-256
`97cf58cd60ffc9f7a6840809c2227830812f04984e3aadc4126c19054cda8e69`.
It used **GPT-6 Sol/xhigh**, the same exact reader and payload as the preceding
packed attempt, and the same 600-second call limit. No protocol gate was relaxed.
Elapsed time was **237.17 seconds (3m57s)**. Neither the input-size rejection nor
the lifecycle failure recurred. This establishes one successful repaired-boundary
call, not that transient connection failures are impossible. The preceding
lifecycle failure's precise cause remains unproven.

Reported usage for this call is **271,498 input + 10,806 output = 282,304 tokens**,
with **complete telemetry**; 7,299 reasoning tokens are included in output and
cached input is zero. Earlier failed-call usage and campaign totals remain
incomplete. Do not combine this success with historical unknowns into a claimed
complete campaign total.

The structured verifier returned `reviewed_report=true`, no contradicted claim
IDs, and one warning: `verify_report-incomplete_cashflow_sensitivity_inputs`.
It asks the reader to expose the supplied scenario tax rates (19%/18%/17%),
quarterly working-capital assumptions (USD 10.0/8.5/7.0 billion), and the
1.2%-of-revenue depreciation/amortization assumption. These are conditional
analyst inputs, not issuer forecasts or evidence of economic likelihood.
The response also proposes retiring obsolete reader warnings while retaining
the protected issuer-FCF reconciliation and independent-evidence limitations.
These are model-proposed dispositions, **not engine-applied lifecycle closures**.

Artifacts are in
`reports/RESEARCH_SUBSTANTIVE_20260922/factual_diagnostic_sol6_lifecycle_1/run_1/`:
`diagnostic.json` records scope/usage and `factual_reply.json` preserves the reply.
The capsule cannot export a final report or be reused as a recovery attestation.
It reconstructs historical analysis inputs only to diagnose the factual boundary;
it is **not a fresh end-to-end analysis or final exported-reader acceptance run**.

All six hosted checks passed on `eef3981` (Python 3.10–3.13, locked install and
container privacy/import). The hosted API model-effort finding is fixed and
resolved. Hosted re-review of `eef3981` completed at 08:26:17 UTC with no new
inline findings; the only review thread is resolved. This final outcome entry
changes documentation only, not the validated implementation.

Next delivery sequence remains: complete PR review/integration through the
feature branches; carry the explicit cash-flow assumption presentation into the
next reader; budget the complete fresh analysis, writing, factual, coverage and
repair path and inspect its actual exported English reader. Then close typed
financial reconciliation and targeted independent evidence gaps with dependent
review before proving HOOD generality. Do not transplant this diagnostic into an
admitted continuation. User reader acceptance and production release remain
separate gates. No additional live run or PR merge is initiated by this success.

### PR #24 integration — September 23

Under the user's subsequent merge instruction, PR #24 merged at 14:58:04 UTC as
`d9bb516b78926fcecc19a8868980f05fe876e440` into
`codex/research-substantive-closure`. All six checks passed on final PR head
`7aca54c`; the only review thread is resolved. The local base was fast-forwarded,
and the fully merged `codex/codex-runtime-model-alignment` branch was deleted
locally and remotely. Its commits remain recoverable through the merge history.
PR #23 remains open against `codex/deep-research-v2`; main and the frozen branch
remain unchanged. The status overview and implementation log now link this
outcome. No additional research/model call or acceptance change accompanied
the merge and documentation synchronization.

## September 23: reviewed reconciliation and fresh attempt 3

The user's next instruction was to complete the first four recommended steps:
feature integration, reader assumptions, bounded fresh English NVDA validation /
reader inspection, and targeted financial / independent-evidence closure.
HOOD, trading, scheduling, user acceptance and production activation remain
outside this increment. No original design or implementation-plan file changed.

### Integration, implementation and independent checks

PR #23 merged into `codex/deep-research-v2` as `bb580cb`, after six successful
checks and resolution of all seven review threads. The fully merged
`codex/research-substantive-closure` branch was removed locally/remotely after
verifying its exact `00de1dd` tip and ancestry; its commits remain in the merge.
Main was unchanged. New work is on `codex/nvda-reader-financial-closure`, PR #26.

- `f3a7967`: code-owned reviewed cash-flow assumption table; exact-reader
  provenance, preview-12 cache separation, historical preview compatibility.
- `ecd2fc3`: typed statement-row CFO reconciliation, issuer-FCF distinction,
  three-component nonzero proxy residual and immutable packet preparation.
- Independent code review fixed Markdown-label injection, absent optional-field
  serialization changing historical hashes, and missing source-row cross-checks.
  Ninety-six targeted implementation tests, relevant lint and whitespace checks
  passed. All six hosted checks passed on frozen runtime head `d65b3ba`.
- Fresh source/operating and separately authored cash-flow reviews were attached
  to `reviewed_inputs_2`. Nine facts were added without changing the original
  182 facts or 16 source contents. The packet admits 36 operating and 65 cash-flow
  calculated values; the financial case remains draft. Arithmetic/source review
  does not approve economic assumptions, funding, valuation or per-share values.
- An additional 34 focused finalization recovery/engine tests passed when
  evaluating the continuation option. No full local-suite rerun was required.

### Exact live scope and terminal outcome

`fresh_validation_plan_3/{PLAN.md,request.json,preflight.json}` records authority,
the exact request and 24-artifact input-manifest check, code/source fingerprint
and supported runtime selections. The frozen runtime was
`d65b3ba6bd511c212f63bb3ecebc84e851ff8b3d`, engine `research-v2-preview-12`.
The input evidence SHA-256 is
`329cd20c70fea0753a4630e2c0f3a0d80808a6dc463095d0617957c52aef68e4`.
No analysis response from an older attempt was used as substantive validation.

The supervised run started around **16:53:20 UTC** with the unchanged
**3,000,000-token best-effort / 5,400-second total / 600-second-per-call** limits,
zero acquisition follow-ups and English only. Actual roles used GPT-6 Sol/high,
GPT-6 Sol/xhigh and GPT-6 Astra/high as selected; there was no silent fallback.

Attempt directory: `reports/RESEARCH_SUBSTANTIVE_20260922/fresh_validation_run_3`.
It completed **28 model calls**: fresh challenge/planner/analysts/reconciliation,
claim verification, writer, exact-reader factual review and **18 coverage batches**.
The terminal `stop_reason` is **`repair_path_budget_insufficient`**;
`failed_stage` is **`repair_report`**. No repair call was dispatched. This is a
pre-dispatch budget-admission stop, not an external-source, timeout or transport
failure. The generic `failure_reason` field says `unknown`; the specific result
and `stages/repair-admission.json` establish the bounded-admission classification.

Usage is **1,463,703 input + 179,030 output = 1,642,733 tokens**, complete for all
settled calls in this attempt. Cached input **36,096** and reasoning output
**91,932** are included in those totals. Run metadata elapsed time is
**3,593.84 seconds (59m54s)**. These counters exclude independent agent work;
they are not a complete campaign total. Earlier failed-call unknowns remain
unknown and historical attempts were not modified.

The remaining allowance was **1,357,267 tokens / 1,806.30 seconds**. The full
20-call repair path reserved **3,157,123 tokens** conservatively (writer 938,706;
factual 1,031,308; coverage 1,187,109). Its less-conservative planning estimate
was 894,288 tokens, not an admission guarantee. Observed-latency planning also
estimated **4,504.80 seconds**, exceeding the remaining window. Neither guard
was loosened; the allowance was not renewed.

### Reader inspection status and deficiencies

The exact first candidate hash is
`64d04b0e9333f6a69b47309ba03f4346566ec1b8727e8a95ebe61f7dd9d5c2a5`.
Factual review returned `reviewed_report=true` with a missing local issuer-FCF
citation. Complete coverage processing validated **167 of 200** required IDs.
The combined review retained 58 findings: 24 authored findings plus 34
deterministic limitation-disposition findings (38 critical / 20 warning total).
These are not 58 independent substantive defects. A malformed returned issue ID
also generated missing/unknown disposition findings; no fuzzy match or silent
repair of reviewer IDs was applied.

`reader_verification.json` has `exported=false`; report completion and assessment
are incomplete, acceptance is blocked and production activation is false.
`reader_report.md` explicitly says **Diagnostic only — no reader draft**. It is
not a completed reader despite its filename. No exported-reader navigation or
layout acceptance can be claimed.

Independent Astra/high review of the exact candidate and supplied Claude HTML
is in `fresh_validation_plan_3/reader_comparison_draft.md`. The candidate is
stronger on cash definitions, typed residual attribution and two-sided reasoning,
but underuses available evidence and needs clearer scenario interpretation,
observable follow-up tests, shorter tables and precise local citations. The
comparison is provisional; it is not a substitute for reviewing a repaired export.

Material reader omissions include guarantee exclusions/probability/timing,
conditional commitment-overlap scale, unquantified linked-intermediary exposure
and fiscal-date derivation. A distinct scope defect routes audit provenance and
unused valuation conventions into required reader prose. The advisory scope
review identifies exact origins and already-covered excerpts; it changes no
provider finding, severity or admission result. A later fix must use explicit
applicability and negative controls, not blanket warning downgrades.

Typed arithmetic closure is delivered. Customer economics/independent demand,
investment outcomes, operating cash-tax normalization, account classification,
commitment/guarantee cash timing, opening-date roll-forward, capitalization and
long-range valuation remain separate gaps. More polished disclosure cannot
resolve them or approve the financial case.

### Offline continuation proposal — not authorization

`finalization_plan_3/request.json` changes only budget/output destination versus
the original request. The offline `cli.research_finalize prepare` check passed,
binding all 28 completed calls, exact candidate and unchanged evidence/case/model
settings. Plan SHA-256:
`7c71e79968fea43b5efbc999a5918c9d51cd90c5e304288cb53f783263cd8235`.
Destination request identity:
`d6b72fd731ee2327cf0e748ac8d8545c6fbf2ec0435acf160344ed16fbb03ae6`.

Proposed single continuation: at most **4,000,000 additional tokens best-effort,
5,400 seconds total, 600 seconds per call**, reusing this fresh analysis rather
than restarting it. Preparation is offline only: approval was requested but has
not been received, no authorization file exists and no continuation was launched.
Its budget is a cap, not a completion promise. Changed runtime/input identities
require rechecking compatibility; this plan does not authorize applying new
code/evidence to old attestations. Any repaired candidate must pass its own exact
factual/coverage checks before export and user review. PR #26 remains open.
