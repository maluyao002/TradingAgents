# Fresh NVDA validation and financial closure

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
