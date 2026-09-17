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
| M2 | Planner and four specialist roles, blinded challenger, bounded follow-ups and thesis-revision fixtures, stage usage, safe interruption/resume | Live model adapter, process-level hard deadline, bounded evidence/context selection, cross-company assumption reconciliation and realistic coverage fixtures |
| M3 | FCFF/DCF, SOTP, comparables, sensitivity, reverse valuation, horizon bridges, dated discounting, integrated one-period statements and sector driver mechanics | Source-calibrated multi-period company models, full ADR/FX/corporate-action integration, independent financial review and economic constraints on realistic cases |
| M4 | Structured reports, Chinese financial-value rendering, separate claim/final-review hooks, limitations, hashed reader/audit exports | Measured semantic entailment quality, complete editorial numerical admission, report preference tests and production acceptance rules |
| M5 | Immutable dossiers, accepted-history protection, eligible priors, change metadata, real forecast vintage timing, comparable-actual scoring | Full evidence/expectation/assumption/value change attribution, dependency-aware cross-run reuse and calibrated forecast integration |
| M6 | Offline score contracts, synthetic defect fixtures, regression/recovery tests | Human-reviewed gold labels, matched comparison/language trials and explicitly authorized live pilots |

No whole milestone is declared accepted just because its mechanical tests pass.
The standalone replay path works, but the live CLI intentionally refuses execution
until its service/deadline/coverage integration is implemented and validated.
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
# 215 passed
.venv/bin/ruff check tradingagents/research cli/research.py tests/test_research_*.py
# All checks passed
git diff --check
# Clean
.venv/bin/pytest -q tests -m 'not integration'
# 1,521 passed, 1 skipped, 1 deselected; 65 subtests passed
```

The complete offline suite ran with permission to create local test sockets and
fake child processes. The two baseline sandbox failures passed in that environment;
the subprocess/socket subset separately passed 55 tests. The remaining skip is the
pre-existing absent optional Bedrock dependency; the deselected test is live
integration. Existing model-catalog warnings remain unchanged. No live research,
data purchases, publication, or schedule changes occurred.

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

Later integration/documentation commits are available in the branch log. All commits
are local; no push, merge, deletion of old branches, or change to other user work
is included in this implementation task.

## Next decisions and work

Continue with a bounded, explicit live-backend adapter and process supervisor, then
company-specific source reconciliation and context selection. These are additional
implementation tasks, not silently completed features. Before any live pilot,
obtain explicit authorization and agree the reference window; the existing NVDA
report and Claude comparison are candidate baselines, not gold factual labels.
Human source/model review and matched evaluations remain mandatory before acceptance.
The separate Chinese token-comparison TODO remains in `WEEKLY-REPORTS.md`.
