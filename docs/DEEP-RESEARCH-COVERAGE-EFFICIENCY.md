# Coverage efficiency — bounded offline increment

Delivery: [PR #19](https://github.com/maluyao002/TradingAgents/pull/19).
Integration target: `codex/deep-research-v2`, never main. Work branch:
`codex/research-coverage-efficiency`. This supersedes the proposed 180-minute
fresh-validation allowance; it does not authorize any live research calls.

## Decision and deliverable

Add an explicit `coverage_batch_policy: "packed-24"` request option for the
bounded evidence-led workflow. The default remains `legacy-12`. The new option
keeps the complete reader and every original issue/context, with at most 24 raw
issues and 12,000 bytes of losslessly packed issue context per call. Alias IDs
and shared-context records count toward that limit. Oversized individual issues
still fail closed. Exact-context grouping is not semantic deduplication.

The output allowance rises from 6,000 to 12,000 tokens per coverage call. Actual
admission, initial/repaired coverage plans, repair-finding growth, repaired factual
prechecks and advisory optional-cycle planning all use the selected allowance.
Provider output limits remain advisory; unknown usage still blocks retries.
Packing and dispatch share one packet-construction helper.

The policy is bound to request identity and recorded in the finalization plan and
run metadata. Ordinary resume, exact-candidate continuation and case-prefix
recovery reject policy changes. Legacy request identity and provider payloads are
unchanged; serialized request, CLI and diagnostic JSON gain an additive policy
field. This is not a promise that every newly emitted artifact is byte-identical.
Preview-7 remains the engine revision because existing verification semantics and
the default provider contract are unchanged; the opt-in has its own identity.

Full-context factual review, reader hashes, complete disposition coverage,
case-sensitive exact excerpts, protected obligations, compound fan-in, budget
admission and financial acceptance gates are unchanged. New reader bytes require
new coverage. The opt-in is not enabled in any saved live request or schedule.

## Offline measurements

The read-only benchmark reconstructs the September 21 NVDA coverage inventory
from its open lifecycle rows, removing only the two audit-added fields `status`
and `decision`. It matches the historical plan's issue/call counts and all 12
completed batch assignments, checks reader/checkpoint bindings, and verifies its
source files are unchanged. It imports **no review attestations** into the engine.
Sources with compound-parent inventories require separate atomic reconstruction
and are rejected by this historical comparator.

| Same historical inventory | Legacy | Packed opt-in |
| --- | ---: | ---: |
| Original issues / exact groups | 182 / 182 | 182 / 182 |
| Coverage calls | 16 | 10 |
| Repeated reader UTF-8 bytes | 377,936 | 236,210 |
| Packed issue-packet bytes, summed | 113,275 | 112,981 |
| Reserved output allowance | 96,000 tokens | 120,000 tokens |

Calls and repeated reader bytes fall **37.5%**. There are no exact duplicates in
this packet; the benefit is fewer repetitions, not dropping obligations. The
12-KB bound prevents an eight-call result. Output reserve increases **25%**;
neither this allowance nor byte counts are measured token consumption. Full
provider-prompt totals and actual live latency are deliberately not inferred from
these partial measurements. Larger batches may take longer or produce poorer
dispositions; this remains an experiment, not a demonstrated runtime speedup.

Reconstructed issues SHA-256:
`bb7c32048ea08532a6fa9dcff134a71e49354d23971a32939de691ae6959ba1b`.
Unchanged reader SHA-256:
`36c0d231597ca3ae4a720d31783705ce1a41a53569c8f0ca21ca3e2bbeef7018`.

Reproduce without providers or artifact writes:

```sh
.venv/bin/python -m scripts.research_coverage_benchmark \
  --source-run reports/NVDA_VALIDATION_20260921/run_1
```

The JSON includes per-batch counts/packet sizes and source hashes. Matched synthetic
engine fixtures also compare complete model-boundary input bytes, preserve every
ID and the exact reader, and exercise failure/repair/recovery paths. Synthetic
dispositions are test mechanics, not financial judgments or model-quality labels.

## Review and verification

Independent Sol/high review requested stronger compatibility/recovery/cost tests,
one authoritative packed-packet builder, and durable policy visibility. The latter
two are implemented; tests cover both policy-change directions, same-policy packed
continuation, historical missing-field requests, exact dispatched coverage costs,
scaled future-path allowances and a frozen legacy provider-boundary hash,
coverage omissions, exact reuse, changed-reader review and unknown usage.
Luna/medium supplied read-only historical scouting; Terra/medium supplied bounded
regressions. Coordinator owns integration and commits.

Final tests and independent review are recorded in the implementation log;
the PR records hosted checks and integration. No research
provider calls, historical report edits or financial/reader acceptance occurred.

## What remains and next sequence

1. Review and integrate this offline increment on the feature branch.
2. Separately authorize a **small paired coverage diagnostic**, not fresh research:
   one packed batch versus the equivalent two legacy batches, identical frozen
   reader/issues/model/effort, at most three calls, 15 minutes overall and 600
   seconds per call. Prepare exact request/envelope bindings first; propose a
   250,000-token aggregate allowance, subject to exact pre-dispatch admission.
   No automatic retry, renewal, report export or recovery-attestation import.
   Historical failed-call usage remains unknown. These limits are a proposal,
   not current authorization or a promise all three calls will finish.
3. Compare total elapsed time, complete usage, every disposition and exact witness.
   Investigate disagreements; do not choose speed over material-issue coverage.
   A favorable single pair is preliminary, not a production-quality benchmark.
4. Only then decide whether to run full acceptance, repeat a diagnostic, or invest
   in isolated concurrent workers. Do not automatically increase the wall limit.

Concurrency is **not implemented**. Current POSIX main-thread alarms and separate
model-process groups require explicit worker ownership, aggregate reservations,
bounded cancellation/settlement and deterministic merge tests. Simply wrapping
the existing service in threads would be unsafe. Observed-latency estimates remain
coarse, advisory same-policy estimates; they are not normalized for batch size.
Avoiding full factual/coverage re-review after a changed reader is also not part
of this increment. Exact unchanged-reader reuse remains supported.

Stage 3 reader acceptance, financial-case closure, independent ecosystem evidence,
HOOD generality and release approval remain open. A coverage-only diagnostic
cannot establish any of them.
