# Deep Research V2 — current status and next deliverables

Updated September 24, 2026. **This is the single active work queue.**
Start at the [documentation index](README.md) for other purposes.
The [original design](DEEP-RESEARCH-DESIGN.md) and
[implementation / acceptance plan](DEEP-RESEARCH-PLAN.md) remain authoritative
for scope and release requirements. This summary does not replace their gates.

## Current position

The engine foundation and several review/integration increments are implemented,
but **Stage 3 is still open: no admitted final improved English NVDA reader
has been delivered**. The HOOD contrast remains Stage 4 work. A successful
diagnostic or merged code increment is not a completed or accepted report.

- PR #24 merged as `d9bb516` into `codex/research-substantive-closure`.
  Its local/remote sub-branch was removed. All six checks passed on final PR head
  `7aca54c`; its review finding is resolved and re-review found no new inline findings.
- PR #23 merged into `codex/deep-research-v2` as `bb580cb` on September 23,
  after all six checks passed and all seven review threads were resolved.
  The fully merged local/remote `codex/research-substantive-closure` sub-branch
  was removed; its history remains in the merge. Main remains unchanged.
- PR #26 merged into `codex/deep-research-v2` as `b957806` on September 23
  at 22:06 PDT (September 24 at 05:06 UTC). All six hosted checks passed on
  final reviewed head `0eb9387`; both new source-validation findings were fixed
  and independently re-reviewed. Its local/remote sub-branch and temporary
  review worktree were removed. Main and the unrelated configuration branch
  were unchanged.
- Active Sol/Luna selections now use GPT-6. Runtime capability checks, API effort
  forwarding, conservative input-size admission and lossless packing are implemented.
- The latest exact-reader factual diagnostic succeeded in **237.17 seconds /
  282,304 tokens**, with complete telemetry for that call. It returned one warning:
  expose scenario tax, working-capital and depreciation assumptions in the reader.
- No final report was exported by that diagnostic; its proposed issue resolutions
  were not applied by the engine. Earlier failed-call usage remains unknown.
  A previous transient lifecycle failure did not recur, but its cause is unproven.
- The approved next increment covers integration, reader assumptions, bounded
  fresh NVDA validation/inspection, and targeted financial/evidence closure.
  HOOD and release acceptance are not part of this increment. Reader inputs are
  now code-owned and tested (`f3a7967`); typed reconciliation and immutable packet
  preparation are implemented (`ecd2fc3`). Fresh source/operating and cash-flow
  reviews passed and are attached in `reviewed_inputs_2`. These engineering
  changes, including complete source-cell validation and exact anchor-value
  comparisons, are integrated through [PR #26](https://github.com/maluyao002/TradingAgents/pull/26).
  Historical reports and live allowances are unchanged.
- Fresh English attempt 3 completed 28 model calls, including all fresh analyses,
  the writer, factual review and 18 coverage batches. It stopped before repair at
  `repair_path_budget_insufficient`: **1,642,733 tokens / 3,593.84 seconds**, with
  complete telemetry for this attempt. Repair/rechecks required a conservative
  3,157,123-token reserve against 1,357,267 remaining; the time estimate also did
  not fit. This was not an external-source or transport failure. No final reader
  was exported; `reader_report.md` is explicitly a diagnostic placeholder.
- An exact-input candidate continuation is prepared offline in
  `finalization_plan_3`, allowing at most 4m additional tokens / 5,400 seconds /
  600 seconds per call. It reuses attempt 3's 28 completed calls, not old attempt
  2 analyses. The user subsequently approved this work and the necessary budget.
  Offline applicability/readiness passed: 24 packet hashes, unchanged research/CLI
  runtime, supported model selections and all 28 replayed stages verified before
  any new call. The supervised `finalization_run_3` launched around 05:29 UTC
  September 24 (September 23 PDT) under `authorization_20260924.json`.
  All 20 new calls completed, then final verification withheld export:
  **876,454 additional tokens / 2,282.52 seconds**, complete telemetry.
  Coverage improved to **196/202** required IDs; 12 combined findings remain
  (six authored plus six deterministic, not 12 distinct defects).
  This is `verification_failed`, not a provider/source-access or budget failure.
  Changing inputs/runtime still requires a new compatibility assessment.
- PR #28 is open against `codex/deep-research-v2`, not main. It adds an explicitly
  authorized single revision after complete exact-prefix replay, scoped procedural
  lineage and full new factual/coverage checks. Independent review fixes and
  114 targeted tests passed (one optional packet test skipped); all six hosted
  checks passed on `2f0b8d2`. The user-approved `revision_run_1` launched around
  06:30 UTC September 24 with a 4m additional-token/90-minute/600-second-call cap.
  It completed in **33m49s / 872,657 additional tokens**, complete telemetry,
  but export was withheld at `verification_failed`. Factual review is clean;
  **195/200** coverage IDs validated. One mixed review-status/dependency obligation
  remains uncovered and four audit-only decisions incorrectly contain reader spans.
  The original substantive wording/disclosure defects are addressed at candidate
  level; this is not an exported or accepted reader. A later reserve-planning
  review fix, `740015e`, passed 42 focused tests and all six hosted checks; its
  review thread is resolved. The run stayed frozen at `2f0b8d2`; PR #28 is not merged.
- Follow-up verification-only repair is implemented and reviewed in PR #28;
  155 targeted tests passed (one optional packet test skipped). The unchanged candidate can receive fresh factual/complete coverage
  checks after replaying all 68 saved calls, without another analyst or writer run.
  A narrow evidence-bound split addresses the mixed pending-review/dependency
  statement; financial scope limits remain reader-required. The new attestation
  contract explicitly requires empty spans for audit-only decisions. Offline
  rehearsal reached the exact frozen-reader boundary, reserving 1,983,755 tokens.
  The first attempt stopped locally before inference: its 1,065,321-byte prompt
  exceeded the 1,048,576-byte cap; the conservative unknown-usage marker is
  preserved. Follow-up lossless nested packing and pre-dispatch size admission
  passed 139 targeted tests and independent review. `verification_plan_2` retains
  3m additional tokens / 60 minutes / 600 seconds per call, using the completed
  original revision as source. Supported Sol/xhigh preflight passed; the
  subsequent terminal outcome is recorded below.
  Exact offline rehearsal confirms all 20 planned prompts fit, with the largest
  at 1,044,357 bytes and lossless canonical roundtrip equality; reserve 1,962,791.
- `verification_run_2` completed on frozen `2d20333`: 19 new verifier calls,
  **644,634 additional tokens / 33m55s**, complete telemetry. All six hosted checks
  passed. All five earlier failed obligations now validate; the unchanged candidate
  reaches **198/200** coverage IDs but remains **`verification_failed`**, without
  export. Three subjects remain: omitted qualified guarantee scale, incentive-table
  prerequisite scope, and current typed residual review versus historical/economic
  limits. The latter two also produce deterministic disposition failures. A bounded
  one-writer transition with explicit prior-finding follow-up is implemented and
  independently reviewed in PR #28 at `2b60ba3`. It adds exact finding-bound source
  witnesses, linked-critical follow-up, generation guards and lossless packing.
  167 distinct worker/main targeted tests passed; legacy recovery/engine and
  roundtrip checks also passed. The actual 87-stage no-provider rehearsal fits
  writer and synthetic factual/coverage requests without changing evidence.
  `reader_repair_run_1` completed under a fresh 5m-token best-effort / 90-minute
  maximum / 600-second-call allowance: **909,980 additional tokens / 38m22s**,
  complete telemetry, 87 imported + 20 new stages. All six hosted checks passed
  on `2b60ba3`. All five prior finding follow-ups corrected, but export remains
  withheld at **197/207** coverage. Remaining blockers are outcome-passage delivery,
  material concentration/financing caveats, DOE historical/current source scope and
  three malformed audit-only decisions. The first targeted delivery fix is reviewed
  and integrated as `01a662e`; no follow-up live run has started. No new external
  evidence is required for the identified source-supported corrections.
- Follow-up offline closure is reviewed in PR #28 at `fa997fb` / `b1b2654`:
  exact issue-linked source excerpts, versioned generation ownership, strict
  fresh-coverage receipts for three proven response-format errors and indexed
  lossless packing. The other nine findings retain factual follow-up. All 231
  distinct targeted tests pass; independent review fixes are closed. Full replay
  of 107 saved stages and synthetic sizing fit the unchanged prompt cap. The
  separately bound `reader_repair_plan_2` allows 6m additional tokens / 90 minutes /
  600 seconds per call, against a 5,034,886-token conservative reserve. Preparation
  is not live completion; no admitted reader or financial approval is implied.

See the [concise implementation record](DEEP-RESEARCH-PROGRESS.md) for completed
checkpoints and the [latest validation evidence](archive/deep-research/DEEP-RESEARCH-FRESH-VALIDATION.md)
for exact attempts, bounds and usage.

## Next execution sequence

| Order | Deliverable | Completion evidence |
| --- | --- | --- |
| 1 — Done (engineering) | Integrate reviewed reader inputs and typed reconciliation | PRs #23 and #26 merged; reader assumption table, typed reconciliation and packet-validation fixes tested/reviewed. This is not final-reader delivery |
| 2 — Ready for bounded validation | Complete bounded NVDA finalization | Latest settled attempt is `verification_failed`, 197/207. Reviewed source delivery, versioned replay and response-error handling pass targeted tests and the full 107-stage rehearsal; `reader_repair_plan_2` binds the next writer/factual/complete-coverage attempt |
| 3 | Inspect the actual exported English reader | Still open: the revised candidate was independently inspected and its footnotes/calculation links checked, but no admitted export exists. Verify exported bytes and reader usefulness before user review |
| 4 | Close targeted financial and independent-evidence gaps | Typed issuer-FCF/residual reconciliation and fresh source/arithmetic reviews delivered in PR #26; dependent fresh analysis completed, but the reader is blocked and economic/independent-demand gaps remain |
| 5 | Prove HOOD generality | Distinct broker-equity/funding workflow, English reader and comparator review using the same core contracts |
| 6 | Complete broader update/evaluation and release gates | Original M0–M6 requirements, human reference review, held-out/repeat pilots and explicit release decision |

Steps 2–3 may produce a useful conditional operating report without a price target.
They do not close Step 4's financial underwriting. Do not transplant the successful
diagnostic into an admitted continuation: that capsule forbids attestation reuse.
Do not run another probe unless it addresses a named residual risk.

## Delivery stages: implementation versus acceptance

These Stage 0–5 labels are the delivery roadmap, **not** the original M0–M6 milestones.

| Stage | Delivered | Still open |
| --- | --- | --- |
| 0 — Organize and reconcile | Baseline, scope decisions and current work index; documentation cleanup | Keep this queue and implementation record current |
| 1 — Evidence delivery and result scope | Reviewed-input contracts, actual-payload coverage audit, typed derivations and scoped conclusions; PR #11 merged | Broader evidence/model completeness is not implied |
| 2 — NVDA financial case | PR #12 schedules, conditional operating/cash-flow calculations, exact passages and source/arithmetic reviews; merged PR #26 typed CFO/issuer-FCF reconciliation and packet hardening | Economic underwriting, equity/share/funding and opening-date closure |
| 3 — Integrated English NVDA reader | PR #13 and later review/rendering/recovery increments; merged PR #26 reviewed-input presentation; fresh analyses and first full review pass | Complete admitted export, coverage/repair, reader inspection and user acceptance |
| 4 — Robinhood contrast | Comparator and broker-model groundwork | Complete distinct HOOD workflow and English report; independent economic review and user review of the pair |
| 5 — Updates, benchmark and release | Dossier/evaluation utilities and benchmark contracts | Full original release scope, matched trials, human labels, held-out/repeat pilots and activation decision |

No entire original M0–M6 milestone is newly accepted here. Keep the original
NVDA/AMD/INTC/AVGO/TSM release matrix; HOOD is an added early contrast, not a
replacement. Longitudinal updates (M5) are not on the critical path to the first
useful fresh reader but remain part of the full release.

## Open engineering work — not requests for user sign-off

- **Financial case:** typed, tested CFO/residual/issuer-FCF reconciliation and
  the new packet's dependent source/arithmetic reviews are complete; fresh
  analysis, repair and complete rechecks finished, but final verification withheld export.
  Finish fiscal/calendar alignment where required by the model, economic forecasts, opening-date roll-forward,
  usable cash/securities, debt/leases, capitalization and commitment/guarantee
  coverage. Unknown public-source relationships stay explicit; positive FCFF does
  not establish company-wide funding sufficiency.
- **Evidence:** extend guidance/actual chronology and targeted independent
  demand/competition evidence. The SEC contact blocker is already resolved.
  The packet retains 12 follow-up passages from six selected sources; the newly
  reviewed reconciliation admits 36 operating and 65 cash-flow values. Source/arithmetic review is not
  economic approval or a complete current-data refresh.
- **Reader and review:** make business-to-financial reasoning and material
  assumptions readable; keep citations precise and exhaustive audit detail separate.
  Attempt 3's provisional independent review identifies audit lineage and unused
  valuation conventions routed into reader-required caveats. Correct applicability
  with explicit evidence and negative controls; do not blanket-downgrade warnings
  or rewrite the preserved run's findings. Separate genuine reader omissions
  (including guarantee exclusions and unquantified linked-intermediary exposure)
  from audit procedure and assumptions unused by the displayed conclusions.
  Preserve semantic negative controls: prior coverage checks falsely closed a
  missing-disclosure control. Do not switch coverage defaults on efficiency alone.
  The latest revision addresses repurchase wording, partial working-capital scope,
  commitment timing, guarantee terms, source-extraction limits and the false
  incentive-evidence absence. The later verification-only pass closed the mixed
  inherited review-status/dependency obligation and four invalid audit-only
  dispositions. The numbered repair corrected those prior finding subjects but
  exposed specific concentration omissions, an outcome-quote delivery gap, DOE
  historical/current excerpt scope and three new malformed audit-only responses.
  Finish exact witness delivery and versioned response-error supersession before
  another writer/full-verification run; do not simply waive or normalize them.
  Each generation needs its own bound
  allowance; unused allowance never authorizes an automatic writer loop.
  Editorial depth still needs
  lighter tables, better use of supplied evidence and concrete observation plans.
- **Broader release:** dependency-aware reuse/updates, forecast-vintage evaluation,
  wider source/history/company coverage, ADR/FX/mixed-business cases, matched
  comparisons and finite human-label review packets remain required.

Details and evidence are retained in the [archive index](archive/deep-research/README.md),
especially the substantive-closure, packet, disclosure-control and validation records.

## What needs the user

| Checkpoint | User action |
| --- | --- |
| Concrete live run / recovery | The approved one-revision run completed within its cap but withheld export. Remaining work is engineering, not approval to waive findings; any later live path needs its own concrete bounds/plan. No automatic retry has been launched. Reader and production acceptance remain separate |
| Completed NVDA / HOOD reader | Review usefulness and readability, not every intermediate assumption |
| Human reference validation / blind comparison | Review a prepared finite packet, or designate a qualified reviewer |
| Paid/private data, changed scope or acceptance thresholds | Decide only after engineering explains the actual need and alternatives |
| PR merge, production acceptance, weekly/default activation | Separate explicit authorization; a successful probe does not imply any of these |

Routine public-source acquisition, code fixes, assumptions clearly labeled as
assumptions, tests and independent technical review are engineering responsibilities.
Never ask the user to approve away unsupported conclusions.

## Working rules and current artifacts

- English first; preserve Chinese compatibility and the [token-comparison TODO](WEEKLY-REPORTS.md).
- Research only: trading, tactical signals, position sizing, scheduling and
  publishing remain outside the core. Preserve the existing AMD task and schedules.
- Public sources first; no automatic paid fallback or credentials copied into reports.
- New work uses separate `codex/` branches and reviewed PRs. Preserve baseline
  tag `research-v2-baseline-20260918` at `4cbbb07`; do not rewrite history.
- Use targeted offline tests and required hosted CI. A live call is not a substitute.
- Preserve original reports, source packets, hashes and unknown usage in place.
  Documentation archival does not move or reauthorize any run.
- Track calculable components, completed readers, eligible conclusions and accepted
  releases as different outcomes.

| Local artifact under `reports/` | Use / limitation |
| --- | --- |
| `RESEARCH_SUBSTANTIVE_20260922/reviewed_inputs_1/` | Preserved prior targeted source/arithmetic inputs; financial case remains draft |
| `RESEARCH_SUBSTANTIVE_20260922/reviewed_inputs_2/` | New nine-fact typed reconciliation, fresh dependent reviews, 36 operating and 65 cash-flow values; financial case still draft |
| `RESEARCH_SUBSTANTIVE_20260922/fresh_validation_plan_3/` and `fresh_validation_run_3/` | Preserved 28-call fresh attempt, complete 1,642,733-token telemetry; stopped before repair, no final reader; provisional candidate comparison retained |
| `RESEARCH_SUBSTANTIVE_20260922/finalization_plan_3/` and `finalization_run_3/` | Approved continuation completed: 28 saved stages reused, 20 new calls, 876,454 additional tokens; `verification_failed`, 196/202 IDs validated, no admitted export |
| `RESEARCH_SUBSTANTIVE_20260922/revision_plan_1/` and `revision_run_1/` | Explicit one-revision policy; 48 imported stages + 20 new calls, 872,657 additional tokens, complete telemetry; `verification_failed`, 195/200 items, no admitted export |
| `RESEARCH_SUBSTANTIVE_20260922/fresh_validation_run_2/` | Nine completed stages; failed factual boundary; 1,159,911 known tokens with incomplete usage; not a final report |
| `RESEARCH_SUBSTANTIVE_20260922/factual_diagnostic_sol6_lifecycle_1/run_1/` | Successful exact-reader diagnostic and reply; no export or recovery-attestation reuse |
| `NVDA_V2_20260917/run_recovered_1/` | Older bilingual previews; historical baseline, not the intended new English deliverable |

Older artifacts and exact chronological decisions are in the
[pre-cleanup roadmap snapshot](archive/deep-research/DEEP-RESEARCH-STATUS-20260923.md)
and [full implementation history](archive/deep-research/DEEP-RESEARCH-PROGRESS-20260923.md).

## Keeping documentation current

Reconcile this roadmap and the implementation record by default whenever a PR
is merged, following the [repository merge-closeout rules](../AGENTS.md#pr-merge-closeout).
Verify the actual merge target/commit and checks, remove stale current-status
claims, mark only proven deliverables done, and retain outstanding user gates.
For documentation-only merges that leave these records accurate, verify that
without starting a recursive bookkeeping PR.

Update this file for priorities, gates and ownership. Record completed changes in
[the implementation record](DEEP-RESEARCH-PROGRESS.md), and commands in
[the operating guide](DEEP-RESEARCH-USAGE.md). Do not add a new top-level delivery
document for every PR. Put substantial dated evidence in the archive and link it
from the implementation record. Historical “next steps” never override this queue.
