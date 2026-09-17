# Deep Research V2 implementation log

Branch: `codex/deep-research-v2`, based on `ebc3383` (the current weekly-report
checkout). Live schedules, provider settings, historical reports, and user tasks
are unchanged. Local commits are used as reviewable checkpoints; no remote push
or pull request is implicit.

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

## Current implementation: tested offline preview

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

## Next decisions and work

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
validate before returning to bilingual generation. The production cap is unchanged.

Baseline review also illustrates why the Claude report is not a gold label and
why reviewers need checks: an initial criticism of its 18.6% EPS premium mistakenly
used Q2 instead of H1; H1 EPS $4.85/$4.09 supports 18.6%. That criticism was withdrawn.
Human source/model review and matched evaluations remain outstanding.

Next validate the opt-in backend on a bounded authorized pilot, and use verified
reference evidence to calibrate company models, coverage and comparison runners.
These are additional acceptance/implementation tasks, not silently completed features.
Before any live pilot, obtain explicit authorization and agree the reference window; the existing NVDA
report and Claude comparison are candidate baselines, not gold factual labels.
Human source/model review and matched evaluations remain mandatory before acceptance.
The separate Chinese token-comparison TODO remains in `WEEKLY-REPORTS.md`.

Known operational boundaries: Codex output-token limits are advisory (actual spend
and overshoot are recorded); total wall supervision has bounded cleanup overhead;
resume cannot recover elapsed time that was never checkpointed before a hard crash;
very rapid unobserved double-fork descendants need stronger OS containment. These
limitations must be evaluated before unattended production use.
