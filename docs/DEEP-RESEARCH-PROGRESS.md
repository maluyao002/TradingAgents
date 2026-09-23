# Deep Research V2 — implementation record

Updated September 23, 2026. This is the concise record of completed engineering
and validation checkpoints, **not a second work queue**. For current priorities,
open gates and user decisions, use the [current roadmap](DEEP-RESEARCH-STATUS.md).
The [original implementation plan](DEEP-RESEARCH-PLAN.md) is preserved.

## Latest completed increment — PR #24

PR #24 merged as `d9bb516` into `codex/research-substantive-closure`.
The fully merged `codex/codex-runtime-model-alignment` branch was removed locally
and remotely. PR #23 remains open against `codex/deep-research-v2`; main and the
frozen integration branch are unchanged. Status synchronization was committed as
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
| PR #23 work, still open | Nine-question work register, conditional cash-flow integration, new source packet and source/arithmetic review attachment | Typed financial reconciliation, full fresh reader path and HOOD remain open |
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

Documentation changes are isolated on `codex/docs-consolidation`, based on
`codex/research-substantive-closure`, for a separate PR. No merge or new research
run is implied by this cleanup. Add future completed checkpoints here briefly;
put large dated workpapers in the archive rather than creating another active plan.
