# Deep Research V2 — current status and next deliverables

Updated September 23, 2026. **This is the single active work queue.**
Start at the [documentation index](README.md) for other purposes.
The [original design](DEEP-RESEARCH-DESIGN.md) and
[implementation / acceptance plan](DEEP-RESEARCH-PLAN.md) remain authoritative
for scope and release requirements. This summary does not replace their gates.

## Current position

The engine foundation and several review/integration increments are implemented,
but **Stage 3 is still open: no accepted improved English NVDA/HOOD report pair
has been delivered**. A successful diagnostic is not a completed or accepted report.

- PR #24 merged as `d9bb516` into `codex/research-substantive-closure`.
  Its local/remote sub-branch was removed. All six checks passed on final PR head
  `7aca54c`; its review finding is resolved and re-review found no new inline findings.
- PR #23 merged into `codex/deep-research-v2` as `bb580cb` on September 23,
  after all six checks passed and all seven review threads were resolved.
  Main remains unchanged. Follow-up work is on `codex/nvda-reader-financial-closure`.
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
  preparation are implemented (`ecd2fc3`). Source/operating review passed; cash-flow
  input review is pending. No new live research run has started yet.

See the [concise implementation record](DEEP-RESEARCH-PROGRESS.md) for completed
checkpoints and the [latest validation evidence](archive/deep-research/DEEP-RESEARCH-FRESH-VALIDATION.md)
for exact attempts, bounds and usage.

## Next execution sequence

| Order | Deliverable | Completion evidence |
| --- | --- | --- |
| 1 | Complete feature-branch integration and prepare the next English reader | Reviewed PR #23 path; explicit material cash-flow scenario assumptions; no unsupported valuation or funding claim |
| 2 | Budget and run the complete fresh NVDA path | Exact input/model identities; analyst, writer, factual, coverage and repair reserves; token/wall bounds; all failed-call unknowns preserved |
| 3 | Inspect the actual exported English reader | Readability, paragraph citations, material caveats and exact-artifact admission checked; comparison with the supplied Claude reference, not a claim it is factual gold |
| 4 | Close targeted financial and independent-evidence gaps | Typed issuer-FCF/residual reconciliation; dated financial relationships; dependent analyses/reviews refreshed when input or case identities change |
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
| 2 — NVDA financial case | PR #12 schedules, conditional operating/cash-flow calculations, exact new passages and source/arithmetic reviews | Economic underwriting, typed reconciliation, equity/share/funding and opening-date closure |
| 3 — Integrated English NVDA reader | PR #13 and later review/rendering/recovery increments; fresh analyses and a successful exact-reader factual diagnostic | Complete admitted export, coverage/repair, reader inspection and user acceptance |
| 4 — Robinhood contrast | Comparator and broker-model groundwork | Complete distinct HOOD workflow and English report; independent economic review and user review of the pair |
| 5 — Updates, benchmark and release | Dossier/evaluation utilities and benchmark contracts | Full original release scope, matched trials, human labels, held-out/repeat pilots and activation decision |

No entire original M0–M6 milestone is newly accepted here. Keep the original
NVDA/AMD/INTC/AVGO/TSM release matrix; HOOD is an added early contrast, not a
replacement. Longitudinal updates (M5) are not on the critical path to the first
useful fresh reader but remain part of the full release.

## Open engineering work — not requests for user sign-off

- **Financial case:** typed, tested CFO/residual/issuer-FCF reconciliation is
  implemented; complete the new packet's dependent reviews and fresh validation.
  Finish fiscal/calendar alignment where required by the model, economic forecasts, opening-date roll-forward,
  usable cash/securities, debt/leases, capitalization and commitment/guarantee
  coverage. Unknown public-source relationships stay explicit; positive FCFF does
  not establish company-wide funding sufficiency.
- **Evidence:** extend guidance/actual chronology and targeted independent
  demand/competition evidence. The SEC contact blocker is already resolved.
  The new packet has 12 exact passages from six selected sources; delivery checks
  cover 36 operating and 57 cash-flow values. Source/arithmetic review is not
  economic approval or a complete current-data refresh.
- **Reader and review:** make business-to-financial reasoning and material
  assumptions readable; keep citations precise and exhaustive audit detail separate.
  Preserve semantic negative controls: prior coverage checks falsely closed a
  missing-disclosure control. Do not switch coverage defaults on efficiency alone.
- **Broader release:** dependency-aware reuse/updates, forecast-vintage evaluation,
  wider source/history/company coverage, ADR/FX/mixed-business cases, matched
  comparisons and finite human-label review packets remain required.

Details and evidence are retained in the [archive index](archive/deep-research/README.md),
especially the substantive-closure, packet, disclosure-control and validation records.

## What needs the user

| Checkpoint | User action |
| --- | --- |
| Concrete live run / recovery | Authorize the bounded purpose and retry policy where not already covered; old run allowances are not renewed by these docs |
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
| `RESEARCH_SUBSTANTIVE_20260922/reviewed_inputs_1/` | Current targeted reviewed source/arithmetic inputs; financial case remains draft |
| `RESEARCH_SUBSTANTIVE_20260922/fresh_validation_run_2/` | Nine completed stages; failed factual boundary; 1,159,911 known tokens with incomplete usage; not a final report |
| `RESEARCH_SUBSTANTIVE_20260922/factual_diagnostic_sol6_lifecycle_1/run_1/` | Successful exact-reader diagnostic and reply; no export or recovery-attestation reuse |
| `NVDA_V2_20260917/run_recovered_1/` | Older bilingual previews; historical baseline, not the intended new English deliverable |

Older artifacts and exact chronological decisions are in the
[pre-cleanup roadmap snapshot](archive/deep-research/DEEP-RESEARCH-STATUS-20260923.md)
and [full implementation history](archive/deep-research/DEEP-RESEARCH-PROGRESS-20260923.md).

## Keeping documentation current

Update this file for priorities, gates and ownership. Record completed changes in
[the implementation record](DEEP-RESEARCH-PROGRESS.md), and commands in
[the operating guide](DEEP-RESEARCH-USAGE.md). Do not add a new top-level delivery
document for every PR. Put substantial dated evidence in the archive and link it
from the implementation record. Historical “next steps” never override this queue.
