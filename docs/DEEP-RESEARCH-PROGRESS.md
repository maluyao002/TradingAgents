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

## Current implementation

- M0 foundation: versioned contracts, explicit backend/request, standalone side-
  effect-free validation CLI, injected service protocols, local atomic checkpoints,
  request/content identity, conservative budget accounting, and offline tests.
- M0 acceptance remains pending integration review and the full comparison harness.
- M1-M6 are not claimed complete. Live pilots, human financial/reference review,
  real-source coverage, report preference, and language benchmarks remain unrun.

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
