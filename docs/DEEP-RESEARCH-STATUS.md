# Deep Research V2 — current status and delivery roadmap

Reconciled September 18, 2026, against implementation through `2036fe8`.
This is the current work index and recommended execution order, not new release
acceptance or authorization for additional live calls. The original M0–M6 gates
remain in [the implementation plan](DEEP-RESEARCH-PLAN.md).

## 1. Executive assessment

We have a substantial tested research-engine foundation, one older completed
bilingual research preview, and a newer independently reviewed conditional NVDA
valuation memo. We do **not** yet have the intended improved, model-backed English
NVDA/HOOD report pair or an accepted production engine.

The bottleneck has moved from runtime/schema reliability to evidence delivery,
financial completeness and integration into a useful reader report. Additional
standalone probes and more defensive infrastructure are not substitutes for that
deliverable. There is no sensible single completion percentage: utilities,
integrated features, live demonstrations and release acceptance are different states.

Most remaining work belongs to system development. User approval cannot repair
missing source passages, an incomplete equity bridge, an uncalibrated company
model or a hardcoded preview-only assessment. Conversely, engineering tests cannot
substitute for independent reference review or the user's final product acceptance.

### Settled decisions — do not ask again

- Deep fundamental research is the product. Trading, tactical signals, position
  sizing, scheduling and publication stay outside the core.
- Use English for current development and NVDA plus Robinhood as the initial pair.
  Preserve Chinese compatibility and its token-comparison TODO; defer new language work.
- Public sources first. No automatic paid-data purchase or provider fallback.
- Keep legacy behavior and existing schedules/AMD task unchanged.
- Preserve failed runs, source vintages and unknown usage; never rewrite history
  or infer zero usage from a failed call.
- Use staged local commits and mixed-model implementation/review. No implicit push,
  merge, production activation or new live-run budget.

## 2. What we actually completed

| Phase | Delivered | What it did not establish |
| --- | --- | --- |
| Original engine foundation | Separate request/result contracts and CLI; safe SEC/IR fetching/discovery utilities, a limited live SEC-baseline collector, normalization, research roles, deterministic financial utilities, review/rendering, checkpoints, budgets and dossiers | Complete integrated IR acquisition, full data coverage, calibrated company models or release acceptance; the real NVDA/HOOD packets used separate curated workflows |
| Runtime and first pilot follow-ups | Strict provider-schema repair, bounded stream handling, semantic reference checks, deadline supervision and explicit recovery preserving usage | Unlimited reliable recovery or complete usage for failed calls |
| Bilingual NVDA preview | Recovered run produced English and Chinese readers with validated financial insertions and no critical final-review findings | Accepted valuation; polished reader (175 appended limitation bullets); controlled superiority over Claude |
| English-first quality campaign | NVDA/HOOD comparator registration; reader/audit separation, footnotes, exact-text verification, bounded repair, targeted passages/investigations; HOOD equity-cash-flow model and source anchors | Completed improved English final reports: NVDA timed out at investigation review; HOOD stopped at a provider boundary |
| Financial-model follow-ups | Source-bound assumption packages; 89 NVDA facts; dated market inputs; three ten-year conditional scenarios; deterministic cash-flow/terminal checks and 27 sensitivity cells | Complete equity/funding bridge or company forecast acceptance; standalone scenario workflow is not fully integrated with the engine |
| Latest bounded diagnostic | One call completed in 63.29 seconds with 91,162 measured tokens; exact payload and artifact integrity verified | A populated model: it returned `model=null`; no subsequent full report was generated |

Latest recorded full non-live verification: **1,982 passed, 2 skips, 18 existing
warnings, 65 subtests**. This is evidence of tested behavior, not report-quality
certification. This documentation reconciliation did not rerun those tests or
consume research-model calls.

### Original milestone reconciliation

| Milestone | Engineering state today | Still open |
| --- | --- | --- |
| M0: Boundary/contracts/baselines | Foundation substantially built and exercised | Matched comparison runner and reviewed reference corpus; acceptance of full baseline scope |
| M1: Evidence/financial history/expectations | Acquisition and normalization mechanics; narrow real NVDA/HOOD packets | Eight-quarter/five-year coverage, guidance history, eligible estimates, independent ecosystem evidence and measured material-event coverage |
| M2: Research/adaptive investigation | Roles, local follow-up, challenge, usage and recovery implemented; older recovery demonstrated live | Reliable material-input delivery, gap classification, external follow-up, selective dependent reruns and cross-company assumption checks |
| M3: Financial models/valuation | Tested FCFF, broker-equity DCF and other calculation utilities; real conditional NVDA scenarios | Company-specific economic schedules, complete capital/funding/shares, source-backed horizon bridges and broad template validation |
| M4: Review/reader production | Older live previews; improved reader/audit and exact-artifact gates tested offline | Stronger English pair, semantic-quality evaluation, integrated scenarios and graded conclusion eligibility; preview-only acceptance is still hardcoded |
| M5: Dossiers/updates/evaluation | Immutable history, promotion safeguards, prior eligibility and forecast-vintage utilities | Dependency-aware reuse, full change attribution, accepted end-to-end updates and forecast scoring on real comparable actuals |
| M6: Benchmark/release | Rubrics, scoring contracts, defect fixtures and registered comparator identities | Matched legacy/V2/single-agent runs, human-reviewed labels, blind review, broader windows, repeat pilots and release decision |

No whole M0–M6 milestone is newly declared accepted here. M5 is not on the critical
path to the first useful fresh report, but remains part of the agreed full release.

## 3. Diagnosis: what is holding back report quality

1. **Collected evidence is not necessarily delivered evidence.** The last call
   omitted the selected Treasury/beta rows and the terminal ROIC derivation from
   its payload. Hashes of those artifacts were present, but usable contents were
   not. More news subscriptions would not fix this immediate defect.
2. **We conflate different kinds of incompleteness.** An operating-asset DCF can be
   conditionally calculable while an equity bridge, company-wide funding assessment
   or current capitalization remains unresolved. The correct response is scoped
   outputs with dependent conclusions withheld, not fabricated completeness or
   blanket rejection of every useful analytical section.
3. **Real financial schedules remain unfinished.** Working capital uses mixed
   accounting rows; cash-only net debt excludes material securities/lease questions;
   diluted shares are a quarterly average; the opening date is not rolled forward.
   Positive modeled FCFF does not certify company-wide funding sufficiency.
4. **The strongest new model work is a side workflow.** Reviewed assumptions,
   derivations and scenario results must flow through challenge, synthesis and
   exact-reader verification in the core—not be pasted into a final report manually.
5. **The real output loop is still unproven.** The bounded finalization fix has not
   completed a new full English report. The Claude examples remain useful editorial
   references, not factual gold or a matched benchmark.

Compared with the earlier Claude review, our unclosed product gaps are still
business-to-financial synthesis, coherent scenario reasoning, independent evidence
and concise human readability. Correct arithmetic and extensive audit trails are
necessary but insufficient. Seek fewer *intrusive* references, not less traceability.

## 4. Recommended delivery stages

Execute the narrow end-to-end path first. Each stage ends in inspectable artifacts,
relevant tests and a local commit. Broader release obligations are retained below,
not silently removed from M0–M6.

### Stage 0 — Reconcile and organize (this checkpoint)

Deliverable: this work index, current artifact register, clear document ownership,
corrected English-first instructions and a conservative cleanup inventory.
Preserve all historical reports, checkpoints and commit identities.

### Stage 1 — Complete evidence delivery and model-result scope

Maps to M1–M4. Engineering-owned; no new user decision is needed to specify it.

Deliverables:

- One reusable reviewed-input contract carrying market inputs, exact source rows
  with headers/dates/units, financial derivations and assumptions—not just hashes.
- Typed derivation support for terminal reinvestment/ROIC, without pretending an
  analyst judgment is issuer-reported fact or necessarily a new calculator primitive.
- Separate eligibility for operating-asset value, equity/per-share value, funding
  assessment and opening-date alignment. Distinguish `not assessed` from `false`.
- An offline material-input coverage audit over the **actual model payload**.

Exit: fixtures reproduce the last two delivery failures and now pass; omission or
tampering blocks the affected output; an unresolved equity bridge does not erase
supported business analysis; no new live call is required for this stage.

### Stage 2 — Close the NVDA financial case

Maps to M1/M3. Depends on Stage 1's contracts; public-source work can proceed in parallel.

Deliverables:

- Reconciled operating-WC, securities/cash availability, debt/lease classification,
  share/capitalization and opening-date schedules, with explicit unresolved items.
- Commitment timing and coverage against costs/capex/WC to avoid double-counting.
- Source-backed business drivers and independent demand/counterevidence; explicit
  authored ranges and falsifiers rather than unsupported precision.
- Refreshed three-scenario package, calculation references and mechanical sensitivities;
  independent economic/code review and an evidence-gap register.
- Explicit guidance/available-estimates/own-forecast comparison and dated market
  inputs where needed for price-implied analysis. Unavailable consensus remains
  unknown, not fabricated and not automatically a blocker to all research.

Exit: every material model leaf is a fact, explicit assumption, convention or
reproducible derivation. Every unresolved item maps to the conclusions it blocks.
Do not require the user to supply a WACC or approve every growth assumption.
If public evidence cannot support current equity value, the operating-asset result
remains explicitly limited and the acceptance gap remains open.

### Stage 3 — Deliver one integrated English NVDA report

Maps to M2–M4. Depends on Stages 1–2; reader/recovery testing can start earlier.

Deliverables:

- Reviewed-case ingestion in the core research path, with invalidation when inputs
  or assumptions change. No manual append of an unverified valuation memo.
- A complete English reader: executive thesis, central disagreement, business and
  financial drivers, management, scenarios, counter-case, falsifiers and material gaps.
- Separate audit/model appendices, bounded final verification, usage ledger,
  recovery evidence and exact exported-artifact hashes.
- Implement and test the M4 report/conclusion admission policy instead of relying
  on hardcoded `needs_review`. Distinguish report completion, supported conclusions
  and acceptance eligibility; production activation remains disabled until the
  later release decision. An unresolved critical prerequisite cannot be overridden
  by a user approval or an LLM reviewer.
- A compact side-by-side review against the supplied NVDA Claude report, clearly
  distinguishing editorial comparison from same-evidence evaluation.

Exit: offline end-to-end replay and documented readiness first, then an explicitly
budgeted **development-validation** live pilot. This does not count as an M6 release
pilot; those still require the original broader offline acceptance first.
The result must contain substantive research, not a diagnostic placeholder. All
numbers resolve; material caveats survive; supported sections remain useful when a
target is withheld. Completion, analytical eligibility and production acceptance
must be reported separately. No claim of an accepted target merely to satisfy a gate.

User checkpoint: authorize the specific live plan before dispatch; review the
completed reader for usefulness/readability afterward. A new diagnostic is optional
only if it tests a named residual risk—not the automatic next step.

### Stage 4 — Prove generality with Robinhood

Maps to M1–M4/M6. Reuse the same interfaces after the NVDA path stabilizes.

Deliverables: reconciled common-equity and capital-retention/funding model,
customer-versus-corporate balance safeguards, transaction/net-interest/subscription
drivers, regulatory scenarios, full English reader and HOOD Claude comparison.

Exit: the same evidence/model/report contracts work for a broker without special
prompt exceptions or industrial FCFF assumptions. Independent math/economic review,
offline replay and a separately authorized live pilot pass. Apply the same final
reader criteria as NVDA. This is also development validation, not an M6 release
pilot. User reviews the pair, not incomplete diagnostic files.

### Stage 5 — Updates, benchmark and opt-in release

Maps to remaining M0–M6 gates. Start fixture collection earlier; postpone expensive
broad live trials until the two-company reporting path is useful.

Deliverables:

- Dependency-aware dossier updates, change attribution and real forecast-vintage
  evaluation, with quiet/event/restatement/interruption cases.
- Wider company/history coverage, ADR/FX and recovery/mixed-business validation;
  measured source/event coverage and explicit provider gaps.
- Matched legacy/V2/strong-single-agent evaluation using the same frozen evidence,
  cutoff, eligible models, mandate and ceilings; actual spend reported separately.
- A versioned reviewed-case/label contract and finite human-review packet workflow;
  current comparator registration permits only pending review, unselected holdout
  and `human_reviewed=false`. Human review needs a supported recording/validation
  path, not a manually toggled label. Prepare label candidates before the review.
- A separate acquisition-quality experiment, human-reviewed reference labels,
  blind report review, held-out windows and the original repeat-pilot gates.
- Validate the Stage 3 admission policy together with dossier promotion/updates
  and complete the release checklist. The current engine always constructs
  `needs_review`; a user saying “looks good” cannot by itself enable accepted
  reports or accepted-history updates.

Exit: the original release criteria pass and the user accepts the sample/release.
The original five-company matrix is NVDA/AMD/INTC/AVGO/TSM; HOOD is the added early
contrast. Do not silently replace that matrix with two companies. If a smaller
release is desired, propose an explicit scope amendment before changing gates.
Chinese benchmarking stays deferred for the English campaign; any removal from
the eventual full release scope needs an explicit plan change.

Weekly adapters, publishing, default activation and scheduling remain separate
post-core decisions, not hidden dependencies of report generation.

### Cross-cutting discipline

Track four distinct outcomes: **calculable component**, **completed reader**,
**eligible analytical conclusions**, and **accepted release**. A passing probe is
not a completed reader; a polished reader is not proof of valuation correctness.
Measure per-stage tokens, latency, delivered material evidence and unresolved
critical findings. Optimize redundant context/orchestration using those measurements
after the useful end-to-end path works; do not add more agent roles by default.
Retain independent challenge and targeted review where they demonstrably catch errors.

## 5. Engineering responsibility versus user responsibility

| Item | Owner / when |
| --- | --- |
| Payload completeness, financial reconciliation, public evidence acquisition, model assumptions, integration, tests and recovery | Engineering; do not block on user approval of routine implementation details |
| Independent code/economic review and preparation of reference-label candidates | Engineering arranges and records it; automated review must not masquerade as human review |
| Material unavailable data | Engineering first exhausts reasonable public alternatives and states the blocked conclusion; ask user only for a genuine access/scope/cost choice |
| Live run or recovery with unknown prior usage | User authorizes a concrete bounded run/retry policy when ready; engineering provides readiness evidence and retains unknown counters |
| Human reference validation and blind product review | User or a designated qualified human reviewer at evaluation checkpoints; engineering supplies a finite review packet |
| Readability/usefulness of completed NVDA/HOOD reports | User review at Stages 3–4, not approval of every intermediate assumption |
| Changing benchmark scope, acceptance thresholds, paid sources or private-data access | Explicit user decision only when proposed and justified |
| Product/release acceptance, merge/push as applicable, default/weekly activation | Separate explicit actions; no implication from a local successful pilot |

**Nothing needs your financial or design sign-off to diagnose/implement Stages 1–2.**
This turn is a planning/documentation cleanup, not a new live-run authorization.
Future live requests should state the exact purpose, input identity, model, call
count, wall/token bounds, stop conditions and treatment of earlier unknown usage.
The last authorized single 600-second diagnostic has already been used; it is not
an unlimited retry allowance. Existing 90-minute/1.5-million per-company settings
are ceilings, not automatic permission to consume them.

## 6. Workspace organization and artifact register

Document ownership:

- **This file:** current state, priorities, ownership and artifact navigation.
- [Design](DEEP-RESEARCH-DESIGN.md): durable target architecture and scope.
- [Plan](DEEP-RESEARCH-PLAN.md): original milestone and acceptance requirements.
- [Progress](DEEP-RESEARCH-PROGRESS.md): historical implementation/run evidence;
  older “next” or “not yet” statements describe their checkpoints, not today's queue.
- [Usage](DEEP-RESEARCH-USAGE.md): commands, contracts and operational boundaries.
- [Weekly reports](WEEKLY-REPORTS.md): unchanged legacy application and Chinese TODO.

These local generated artifacts are intentionally not release labels:

| Artifact | Classification / purpose |
| --- | --- |
| `reports/NVDA_V2_20260917/run_recovered_1/reader_report_en.md` and `reader_report_zh.md` | Completed older research previews, needs review; historical baseline, not the new English pair |
| `reports/NVDA_V2_20260917/comparison.md` | Historical editorial review and pilot usage, not a matched benchmark |
| `reports/RESEARCH_EN_20260917/nvda_run_1` | Failed stronger English run; `reader_report.md` is a diagnostic, not a final |
| `reports/RESEARCH_EN_20260917/hood_run_1` | Failed HOOD run; retain valid stages and unknown failed-call usage |
| `reports/RESEARCH_EN_20260917/nvda_assumptions_2` | Preparation-era draft handoff, superseded for current modeling by the September 18 packet; preserve ancestry |
| `reports/RESEARCH_MODEL_20260918/scenario_packet_1` | Current conditional scenario source packet, not accepted forecasts |
| `reports/RESEARCH_MODEL_20260918/compiled_model_1/valuation_memo.md` | Three-case deterministic English valuation memo; not a full research report |
| `reports/RESEARCH_MODEL_20260918/model_probe_1` | Latest one-call review: operationally completed, model null/unavailable |
| Supplied NVDA/HOOD HTMLs in Downloads | External editorial comparators; registered identities, not issuer evidence or factual gold |

Cleanup policy: organize in place first. Preserve path/hash-bound runs, evidence,
unknown-usage records and all source/parent checkpoints required by recovery.
Do not move an old run into an archive folder merely because it failed. Do not
delete prototype scripts until their responsibilities are integrated and their
tests/references migrate. Do not rewrite the existing commit chain to make it
look cleaner. Use one new implementation-stage commit series and a concise index.

### Cleanup inventory at this checkpoint

- One worktree, on `codex/deep-research-v2`; clean before this documentation update.
  It is 50 commits ahead of local `main`, 11 ahead of
  `codex/deep-research-finalization`, and 47 ahead of `feat/weekly-watchlist-reports`.
  Those two older branches are ancestors of the current branch, not thereby
  confirmed merged into `main`. Retain them until branch disposition is explicit.
  No fetch was performed; this is local ancestry, not current remote status.
- `reports/`: approximately 36 MB, 362 files across 15 top-level directories.
  Retain it: the storage cost is small and the provenance/recovery value is high.
- `build/`: approximately 588 KB, stale ignored generated mirror; do not inspect or
  execute it as authoritative source. Source lives in `tradingagents/`, `cli/` and
  `scripts/`. Removing it is optional housekeeping, not a research blocker.
- `.venv/` (approximately 301 MB), test/lint caches and packaging metadata are
  generated development assets. Leave them intact; deleting them provides no
  meaningful clarity and can disrupt the working environment.
- No duplicate tracked research scripts were established. Keep diagnostic/evidence
  adapters until replacement and test migration are complete.
- `.env`, credentials, external tasks, schedules and comparator originals are
  untouched. No deletion, relocation, branch rewrite or remote operation occurred.

The cleanup performed here is navigational and semantic: one active work index,
document ownership, artifact classifications and removal of contradictory
Chinese-first instructions. It is not physical archival or destructive cleanup.
