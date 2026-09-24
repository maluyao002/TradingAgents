# Deep Research V2 — implementation record

Updated September 23, 2026. This is the concise record of completed engineering
and validation checkpoints, **not a second work queue**. For current priorities,
open gates and user decisions, use the [current roadmap](DEEP-RESEARCH-STATUS.md).
The [original implementation plan](DEEP-RESEARCH-PLAN.md) is preserved.

## PR #26 pre-merge re-review — September 23

The coordinator and the reused independent Sol reviewer re-examined the exact
PR #26 diff. Two packet-builder defects were found and fixed: partial numeric
source excerpts could be accepted as complete cells, and ambient Decimal
rounding could hide a mismatch with an existing anchor fact. The builder now
requires complete statement rows with two valid integer cells and uses exact
multiplication for reused-fact comparisons. Nine negative regressions cover
truncation, malformed cells, low precision and long fractional mismatches.

Before the fixes, **103 targeted offline tests** passed across reconciliation,
packet preparation, reader/provenance, legacy cache/preview and finalization.
After the fixes, **46 packet/reconciliation tests** passed, with lint and
whitespace checks clean. Offline preparation from the preserved actual mapping
into a fresh temporary directory produced byte-identical evidence, case input
and pending cash-flow package. Historical bundles were not changed; no live
calls or continuation were authorized. GitHub records the final reviewed head,
hosted checks and merge outcome for [PR #26](https://github.com/maluyao002/TradingAgents/pull/26).
The same independent reviewer rechecked both fixes and the actual frozen
mapping; no remaining actionable findings were reported in the reviewed delta.

This is source-validation hardening, not acceptance of the reader or economic
model. Any continuation still needs its own allowance and runtime-compatibility
assessment; no historical run or preflight attestation is rewritten by this fix.

## Feature integration and next increment — September 23

PR #23 merged into `codex/deep-research-v2` as `bb580cb` after six successful
checks and resolution of all seven review threads. It includes PR #24 and the
PR #25 documentation consolidation. Main was not advanced.

Follow-up branch: `codex/nvda-reader-financial-closure`. Commit `f3a7967` adds
a deterministic reviewed cash-flow assumptions table, exact-reader provenance,
preview-12 cache isolation and historical preview compatibility. Independent
review identified Markdown label injection; it was fixed with a focused negative
control. The initial 29 reader/cache/preview tests passed; the added escaping
regression also passes. This is engineering completion, not a live report result.

Commit `ecd2fc3` implements typed historical CFO/issuer-FCF reconciliation and
new immutable-packet preparation. It preserves the nonzero proxy residual with
three source-grounded components and distinguishes the USD 69.987bn CFO-minus-
purchases comparator from USD 69.895bn issuer FCF (USD 92m asset-principal
payments). Changed evidence/case identities discard old dependent reviews.

Independent review found and resolved two further issues: absent optional fields
changed historical artifact hashes, and four reused source rows needed explicit
cross-checks against existing facts. Actual saved preview-11 provenance now
revalidates without modifying the saved run. **96 targeted tests**, lint and
whitespace checks passed; no actionable code-review findings remain in the
reviewed scope. Fresh source/operating and separate cash-flow reviews passed and
were attached to `reviewed_inputs_2`, admitting 36 operating and 65 cash-flow
calculation references while leaving the financial case draft and valuation/
equity/funding conclusions blocked. PR #26 is open against the integration branch.

The full-path budget includes factual, all coverage batches, possible repair and
rechecks. The unchanged 3m-token / 5,400-second / 600-second-per-call bounds may
cover one pass but do not guarantee a complete repair path. A new attempt must
stop safely at its limits, not omit checks or silently renew the allowance.
Attempt 3 launched around 16:53 UTC on September 23 from frozen revision
`d65b3ba`; its exact request, 24-file input manifest check, runtime fingerprint and
model preflight are in `fresh_validation_plan_3`. It completed 28 model calls,
including fresh analysis/writing, factual review and all 18 coverage batches,
then stopped at `repair_path_budget_insufficient` before dispatching repair.
Usage was **1,463,703 input + 179,030 output = 1,642,733 tokens**, complete for
this attempt; elapsed **3,593.84 seconds (59m54s)**. Cached input and reasoning
counts are subsets, not extra tokens. Earlier incomplete attempts remain unchanged.

The repair path reserved 3,157,123 tokens versus 1,357,267 remaining and estimated
4,504.80 seconds versus 1,806.30 remaining. The stop is budget admission, not a
source or provider-transport failure. The reader is not exported: 167 of 200
required limitation IDs were engine-validated; the combined review retains
58 findings (including 34 deterministic disposition findings, not 58 distinct
substantive defects). The generated `reader_report.md` file contains only a
diagnostic placeholder, not a completed research reader. Financial acceptance
and production activation remain blocked.

The offline `finalization_plan_3` binds the exact candidate and all 28 completed
calls for reuse, with a proposed incremental 4m-token / 5,400-second /
600-second-per-call cap. Preparation passed; user approval is pending and no
authorization file or live continuation was created. See the
[dated validation record](archive/deep-research/DEEP-RESEARCH-FRESH-VALIDATION.md)
for exact identities and next gates. All six hosted checks passed on `d65b3ba`;
PR #26 remains open and no new merge is claimed.

An independent Astra/high editorial comparison is bound to attempt 3's exact
first reader candidate in `fresh_validation_plan_3/reader_comparison_draft.md`.
It finds better cash definitions, residual attribution and two-sided reasoning,
but underused supplied evidence, weak scenario interpretation/observation plans,
dense tables and a missing local issuer-FCF citation. This is provisional
candidate review, not exported-reader inspection or acceptance. Reassess it
against any repaired/exported reader before declaring those findings closed.
The follow-up scope review identifies audit lineage and unused valuation
conventions being routed into mandatory reader caveats; it preserves all live
findings and gates. Separately, **34 targeted finalization recovery/engine tests**
passed before considering any continuation. This is offline evidence, not new
authorization or proof that a live continuation will complete.

Integration cleanup removed the fully merged local/remote
`codex/research-substantive-closure` branch after checking its exact `00de1dd`
tip and ancestry in `bb580cb`. Its commits remain recoverable through the
integration merge; the running branch, main and report artifacts were unchanged.

## Previous completed increment — PR #24

PR #24 merged as `d9bb516` into `codex/research-substantive-closure`.
The fully merged `codex/codex-runtime-model-alignment` branch was removed locally
and remotely. At that checkpoint PR #23 remained open against
`codex/deep-research-v2`; integration is recorded above. Status synchronization was committed as
`017771e`.

Delivered:

- Active GPT-6 Sol/Luna profiles, exact dry-run selections and native API reasoning
  effort forwarding, with runtime catalog validation and no silent fallback.
- Conservative Codex prompt admission and lossless, versioned context packing.
- Privacy-safe transport/lifecycle failure diagnostics without relaxing acceptance.
- A hash-bound, bounded one-call factual diagnostic; historical artifacts and
  unknown usage preserved.

Verification: all six hosted checks passed on final PR head `7aca54c`.
The API effort review finding was fixed and resolved; independent and hosted
implementation re-review found no further actionable findings in scope.
Affected offline groups passed; counts overlap and are not summed into a unique
total. Full local tests were not repeatedly rerun.

Live diagnostic: **237.17 seconds; 271,498 input + 10,806 output = 282,304 tokens**,
with complete per-call telemetry. The 7,299 reasoning tokens are included in
output. The exact-reader verifier returned one warning about missing scenario
tax/working-capital/depreciation assumptions. No final report was exported and
its proposed lifecycle dispositions were not applied. Financial/evidence gaps
and historical unknown usage remain open.

See the [exact validation/merge record](archive/deep-research/DEEP-RESEARCH-FRESH-VALIDATION.md).

## Earlier completed checkpoints

These are engineering checkpoints, not declarations that an entire stage or
original milestone is accepted.

| Checkpoint | Implementation evidence | Acceptance limitation |
| --- | --- | --- |
| Foundation and frozen baseline | Separate research contracts, roles, deterministic financial utilities, budget/recovery/dossier scaffolding; baseline tag at `4cbbb07` | Full M0–M6 acceptance not established |
| Stage 1 / PR #11 | Reviewed material delivery, typed derivations and output-scope contracts | Not financial-case or report acceptance |
| Stage 2 / PR #12 | Evidence-bound schedules and draft financial-case preparation | Economic underwriting and equity/funding closure remain open |
| Stage 3 / PR #13 | Financial-case/reader integration and deterministic admission | No accepted improved English reader |
| PRs #14–18 | Wire repair, issue lifecycle/scope, bounded finalization, reader presentation and exact-artifact interfaces | Preserved live failures; no silent warning downgrades or final-report approval |
| PRs #19–22 | Opt-in packed coverage, semantic controls, disclosure/review fixes and writer diagnostic | Negative-control failures remain material; efficiency is not semantic acceptance |
| PR #23, merged | Nine-question work register, conditional cash-flow integration, new source packet and source/arithmetic review attachment | Typed financial reconciliation, full fresh reader path and HOOD remain open |
| PR #24, merged into #23 branch | Runtime/model alignment and successful factual boundary diagnostic | One successful call, not full pipeline or release acceptance |

## Historical evidence

The [archive index](archive/deep-research/README.md) groups detailed records by
delivery topic. It includes:

- [Full implementation log through September 23](archive/deep-research/DEEP-RESEARCH-PROGRESS-20260923.md):
  original checkpoints, test counts, PR identities, failures and usage.
- [Pre-cleanup roadmap snapshot](archive/deep-research/DEEP-RESEARCH-STATUS-20260923.md):
  previous detailed reconciliation and older artifact register.
- [Substantive closure](archive/deep-research/DEEP-RESEARCH-SUBSTANTIVE-CLOSURE.md)
  and [reviewed evidence packet](archive/deep-research/DEEP-RESEARCH-FOLLOWUP-PACKET.md):
  supporting contracts and unresolved financial/evidence work.
- [Disclosure controls](archive/deep-research/DEEP-RESEARCH-DISCLOSURE-CONTROLS.md):
  negative-control failures that must not be mistaken for acceptance.

## Documentation consolidation — September 23

Sixteen increment records were moved out of the active docs directory into
`archive/deep-research/`. Full pre-cleanup status/progress snapshots preserve the
historical narrative while the active summaries remove repeated and stale queues.
Original design and implementation-plan contents and paths are unchanged.
Reports, evidence, authorization artifacts, code and schedules are untouched.

Documentation changes were merged through PR #25 into
`codex/research-substantive-closure`, then included in PR #23 integration.
The documentation sub-branch was removed locally and remotely. No new research
run was implied by this cleanup. Add future completed checkpoints here briefly;
put large dated workpapers in the archive rather than creating another active plan.
