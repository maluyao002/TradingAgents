# Same-case checkpoint recovery

> Historical delivery / validation record. Preserve its evidence and decisions;
> dated next steps and run allowances are not current instructions or reusable approval.
> Use the [current roadmap](../../DEEP-RESEARCH-STATUS.md) for active work and
> the [archive index](README.md) to navigate history.

September 19, 2026. Development tooling on PR #13, based on
`codex/deep-research-v2`. This is not an automatic retry or release mechanism.

## Supported boundary

The first Stage 3 NVDA pilot stopped in parent supervision after six settled
analysis calls. Its next call was unsettled; known usage is 558,608 tokens and
the cumulative total is unknown. Preserve the original request, reviewed inputs,
checkpoints and failed outcome. Never clear `dispatched` or treat the unknown
call as free.

The new path supports only that **shape** of case-backed run: frozen evidence and
financial case, bounded quality revision, Codex backend, and the exact six-stage
prefix (independent challenge, planner, business, accounting, expectations,
management). It is not generic recovery for any interrupted stage or provider.
Legacy non-case recovery is unchanged and still cannot import into a case.

Validation replays the current engine offline, serves the six saved replies, and
checks every actual payload against its saved hash. It stops before replying to
`reconcile_challenge`. Changed inputs, model settings, wire identity or payloads
fail closed; no prompt-builder approximation or new research response is used.

## 1. Prepare offline

```sh
.venv/bin/python -m scripts.research_case_recovery \
  --config reports/RESEARCH_STAGE3_20260919/live_plan_1/request.json \
  --source-run reports/RESEARCH_STAGE3_20260919/live_run_1 \
  --codex-home /Users/luyaoma/.tradingagents/codex \
  --output reports/RESEARCH_STAGE3_20260919/recovery_capsule_1 \
  --dry-run
```

Omit `--dry-run` to create a **new** offline capsule containing exact input and
checkpoint copies, a file-hash manifest and an authorization request. Existing
destinations are rejected; the manifest is the completion marker. An interrupted
write may leave an incomplete directory for diagnosis, not a valid capsule.
Neither form opens a provider connection, dispatches a research call or changes
the source run. The Codex home is used to check model identity, not to read auth.

The capsule is an audit bundle, not a self-contained executable or an approval.
Continuation deliberately revalidates the original source and frozen inputs.
The preserved pilot input includes legacy “draft-only” wording despite its valid
operating-scenario review. Future packages use a timeless review precondition;
historical bytes are not rewritten because their reviews/checkpoints bind them.
Inspect any resulting reader for this stale caveat against the actual scoped
review record. The separate financial/valuation case remains unreviewed.

## 2. Obtain a new authorization

The original one-attempt approval cannot authorize further spend. Before a live
continuation, obtain explicit user approval for a **new incremental** token/wall
budget, while acknowledging the unknown historical call. Do not compute a known
remaining allowance by subtracting 558,608 from the old cap.

Create a new request with the same research/model settings and exact input bytes,
a fresh output directory, no dossier promotion and the agreed incremental budget.
The reviewed replay request and failed live request stay unchanged. Bind a
`CaseRecoveryAuthorization` to:

- The verified plan hash and `continuation_request_identity()` of the new request.
  This identity includes the resolved destination as well as request/input content.
- The exact incremental budget, authorization identifier and timezone-aware time.
- Literal boolean acknowledgements of incomplete source usage and live continuation.

The authorization is an operator-recorded approval, not a signed credential or
an independent authentication mechanism. Do not manufacture it from a capsule's
placeholder fields or from code-review approval. Changing destination or budget
requires a newly bound authorization; the service revalidates at engine admission
and checks original source hashes again before new calls.

## 3. Validate, then explicitly continue

```sh
.venv/bin/python -m scripts.research_case_continue \
  --source-config /absolute/path/to/original-request.json \
  --config /absolute/path/to/authorized-continuation-request.json \
  --authorization /absolute/path/to/explicit-authorization.json \
  --codex-home /Users/luyaoma/.tradingagents/codex \
  --dry-run
```

Only after authorization, replace `--dry-run` with
`--allow-live --acknowledge-unknown-usage`. The supervised worker repeats the
validation before constructing the continuation service. The CLI accepts only a
fresh destination; it does not automatically retry an interrupted continuation.
No provider fallback or optional follow-up is added by recovery.

Imported replies incur no new calls and do not consume the incremental allowance.
New calls and continuation elapsed time consume the new budget. Artifacts retain
historical known counters plus measured new usage, but cumulative usage remains
incomplete. Source elapsed time is explicitly the last checkpoint, not total wall
time. A new unmeasured dispatch blocks further normal resume; authorization does
not override that guard. Provider output caps remain advisory where applicable.

Report completion is separate from acceptance. A completed conditional operating
report can be reviewed; historical unknown usage and existing financial/economic
gaps still block the corresponding acceptance or valuation conclusions.

## Supervisor reliability and limitations

Transient process-inspection errors get at most three attempts, bounded by the
absolute deadline. Deadline-bound inspection uses timeout-enforced `/bin/ps` even
if an optional process library is installed. Malformed process tables and worker
identity mismatches still fail closed. Cleanup has a separate bounded allowance,
divided into stop/discovery and reserved KILL/reap phases, and validates process
identities before signaling. An expired discovery phase cannot exhaust the kill
phase's inspection allowance. Persistent inspection denial still fails closed;
unresolved cleanup is a failure.

`supervisor_diagnostic.json` preserves fixed, sanitized failure classifications
without overwriting an existing diagnostic. Raw exceptions, credentials and
process command lines are not written there. This improves future diagnosis; it
does not prove which exception caused pilot 1. Extremely rapid unobserved detached
descendants remain an OS-containment limitation, not a newly solved guarantee.
