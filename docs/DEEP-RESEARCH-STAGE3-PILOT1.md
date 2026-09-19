# Stage 3 English NVDA pilot 1 — failed supervision, preserved research

September 19, 2026. Code: `a36d69c`, branch `codex/research-stage-3`, PR #13
against `codex/deep-research-v2`. No merge or production activation.

## Outcome

The user explicitly authorized one English report attempt: 1,500,000 tokens,
90 minutes overall, 600 seconds per call, no optional research follow-ups or
provider fallback, and at most one reader repair. The new `live_plan_1/request.json`
uses the immutable `operating_reviewed_1` evidence/case and finalization reserves
inside those limits. Input hashes and the 36 reviewed calculation references were
validated before dispatch. No new external evidence collection was enabled.

Six calls completed: independent challenge, planner, business, accounting,
expectations and management. The supervisor then returned
`{"status":"failed","code":"supervisor_failed"}` while the next stage,
`reconcile_challenge`, was unsettled. No reader, final assessment or report
comparison was produced. Stage 3 is **not complete**.

| Measured item | Value |
| --- | ---: |
| Known input tokens | 505,546 |
| Known output tokens | 53,062 |
| Known total | 558,608 |
| Cached input tokens, included in input | 3,712 |
| Reasoning tokens, included in output | 14,929 |
| Last checkpoint elapsed time | 1,138.748 seconds |
| Interrupted call usage | Unknown, not zero |

The last checkpoint is not total wall time. The checkpoint's `usage.complete=true`
describes settled calls only; `dispatched=true` means **whole-run usage is incomplete**.
Reservation alone does not prove that the provider accepted the interrupted call.
No run retry, provider fallback or fabricated usage estimate was performed.
Separate editorial/diagnostic subagent token usage is not available in this ledger.

Local records are preserved under `reports/RESEARCH_STAGE3_20260919/`:

- `live_plan_1/request.json`, `AUTHORIZATION.md`, `outcome.json` — authorized limits
  and a separate failure summary; these do not rewrite the engine checkpoint.
- `live_run_1/stages/` — six analysis checkpoints, evidence and unsettled resources.
- `operating_reviewed_1/` — original reviewed input, unchanged.

## Diagnosis: confirmed versus probable

Confirmed: `supervisor_failed` is emitted by the parent supervisor's broad exception
handler. Worker exceptions, model/whole-run timeout, startup inspection failure,
invalid checkpoint and cleanup failure have distinct paths/codes. There are no
downstream stage/result/export artifacts. Cleanup did not report failure.

The environment has no `psutil`. The fallback launches `/bin/ps` on every 50 ms
poll with a 0.3-second timeout. Any single timeout, subprocess/OS error or malformed
process-table response aborts supervision. A transient polling failure is the
leading explanation, **not a proven root cause**: the exact exception was discarded.
One hundred later checks with the same elevated permissions all passed (maximum
10.9 ms), which does not exclude one transient failure over thousands of polls.
Sandbox-denied inspection probes are not evidence about the elevated live run.

Independent Sol/high diagnosis concurs. Existing offline verification passes:
44 supervisor tests plus six selected budget/failure/interrupt tests. These checks
do not reproduce or prove the specific production exception. No core code was
changed to speculate around it.

## Post-failure engineering — implemented, reviewed offline

The subsequent PR #13 patch addresses the engineering items below without claiming
to reproduce the original exception. Process inspection now retries narrowly
classified transient failures at most three times within the absolute deadline;
malformed data and identity mismatches fail closed. Deadline-bound inspection uses
a timeout-enforced subprocess even when an optional process library is installed.
Cleanup remains bounded and checks process identities. Sanitized fixed-code
diagnostics are preserved without overwriting source checkpoints or an existing
diagnostic.

Separate same-case recovery validates the exact six saved replies against the
**current engine's actual payloads** and stops offline before reconciliation. It
binds unchanged reviewed inputs, plan identity, a fresh destination and an explicit
new incremental budget. Imported stages are not dispatched or charged again;
historical known counters remain in cumulative reporting and its completeness
remains false. Strict authorization validation, source preservation, unsafe-file
handling and continuation accounting have targeted regression coverage.

The real failed NVDA prefix passes offline validation with 558,608 known tokens;
the plan hash is
`70fd539f30194be2e063c3dfad48959c49f4e41196b3ca921e4bc7c61a899d37`.
No provider connection or new research call is needed for this validation.
An offline capsule was prepared at `recovery_capsule_1/`, with manifest hash
`7f5b91c1e43830238cbf8a1f031ec2ccd607570cb29c8fe67f6deed854e575f6`.
It contains exact copies and an unfilled authorization request; original sources
remain unchanged. Final targeted supervisor/recovery/engine/admission/storage
verification: **163 passed**; independent supervisor and recovery reviews clear.
See the [recovery procedure](DEEP-RESEARCH-CASE-RECOVERY.md) for the two separate
commands and approval boundary. No final reader exists yet.

### Original checklist and remaining authorization

1. Preserve sanitized, fixed failure classifications for polling, worker identity
   and status validation; never emit raw exception text or credentials.
2. Handle narrowly identified transient inspection failures with a small,
   absolute-deadline-bounded retry policy; keep persistent/malformed inspection
   fail-closed and preserve process-tree cleanup. Installing `psutil` alone is not
   a complete remedy.
3. Add injected transient/persistent polling, malformed-output and deadline tests,
   retaining existing cleanup, unsafe-artifact and privacy tests.
4. Design and test recovery for **the same reviewed case and exact frozen evidence**.
   Ordinary resume correctly blocks new calls with unknown usage; historical-prefix
   recovery intentionally rejects case-backed runs; the new separate same-case
   path is validated explicitly. Do not clear `dispatched`, replace
   unknown usage with zero, or import the old non-case recovery path unchecked.
5. Obtain new explicit live authorization and budget treatment for the unknown call
   before a continuation or fresh run. The original one-attempt approval is not an
   automatic-retry authorization.

Once a final reader exists, compare thesis hierarchy, causal reasoning, scenario
discipline, management evidence, counter-case/falsifiers and readability with the
prior NVDA reader and supplied Claude R2a report. Reference inspection is complete;
an actual-reader comparison remains pending. Different evidence sets preclude
calling this a controlled factual-quality benchmark.
