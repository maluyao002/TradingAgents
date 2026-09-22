# Matched coverage diagnostic — September 22, 2026

Delivery: [PR #20](https://github.com/maluyao002/TradingAgents/pull/20), targeting
`codex/deep-research-v2`, never main. This records a coverage-only experiment,
not a new report, reusable verification attestation or Stage 3 acceptance.

## Result

All three authorized calls completed in **405.88 seconds** (6m46s). Measured
usage is **36,497 input + 12,318 output = 48,815 tokens**. Output includes 5,264
reasoning tokens; do not add them again. Reported cached-input tokens are zero.
All three calls returned complete counters; no retry or fallback occurred.
Coding and independent-review agent usage is outside this ledger and unavailable.
The earlier failed research call's unknown usage remains unknown.

| Same frozen 18 issues and reader | Legacy 12 + 6 | Packed 18 |
| --- | ---: | ---: |
| Calls | 2 | 1 |
| Call elapsed time, summed | 217.28 s | 188.56 s |
| Input tokens | 23,313 | 13,184 |
| Output tokens, including reasoning | 6,509 | 5,809 |
| Total tokens | 29,822 | 18,993 |
| Structurally valid covered dispositions | 18 | 17 |
| Unresolved issues | 0 | 1 |

Packed used **36.31% fewer tokens** and **13.22% less measured call time** in this
single comparison. This is preliminary: fixed legacy-first order includes cold
adapter setup in the first legacy call; packed runs with a warm adapter. Zero
reported cached tokens does not eliminate setup/order effects or model variance.
Do not extrapolate these percentages to a full research run or enable packed by
default. The selected first batch covers only 18 of the historical 182 issues.

## Quality finding and adjudication

Both arms returned one disposition for every requested ID, with no missing or
unknown IDs and no non-exact witness findings. Seventeen decisions agree. The
disagreement is issue `limitation-533bed111139b23b442abde6e99b2601c87f33ab4820b382b47b00f8502f5178`,
origin `case.review.draft`: the financial case is an unreviewed draft and importing
it does not constitute approval.

Legacy marked it covered using the reader's general “Needs review / Unrated” and
“Research foundation preview; not an accepted investment assessment” labels.
Packed left it unresolved and emitted a critical missing-financial-draft finding.
Deterministic checking added an unresolved-disposition finding for the same issue;
these are **two findings for one missing disclosure**, not two independent defects.

Coordinator adjudication: retain the packed finding. Overall report acceptance
and the review status of its financial schedules are different propositions. The
reader should say plainly that the financial schedules remain an unreviewed draft;
it need not reproduce the internal phrase “Stage 2.” A separately reviewed
conditional operating package does not approve those schedules. This distinction
is already explicit in the newer writer delivery contract, but the frozen reader
predates it. This diagnostic does not test whether that newer writer now complies.

Independent Terra/medium review initially questioned issues 2, 3 and 4. On
challenge, the model-linkage and mixed-row objections did not establish additional
material defects: withheld valuation outputs and the stated limitations convey
those economic boundaries without requiring internal terminology or a literal
row count. The financial-review-status distinction remains the coordinator's
specific acceptance concern. After checking the explicit separate-review-status
product contract, the independent reviewer also concluded that issue 3 remains
unresolved. Exact quotations and model agreement alone are not
proof of semantic coverage; these judgments are not human-reviewed gold labels.

## Boundaries and artifacts

The user explicitly approved both the advisory token ceiling and transmission
of the frozen reader/18 issue contexts to OpenAI's Codex service, Sol/high.
Limits were three calls, 900 seconds overall, 600 seconds per call and 250,000
tokens best-effort aggregate admission/post-call enforcement, not a provider-hard
cap. Worker 880 seconds and supervisor 890 seconds reserved cleanup headroom.
An earlier execution-permission denial occurred before process startup and used
no diagnostic model calls; the subsequently approved capsule ran exactly once.

Local immutable experiment artifacts:

- `reports/NVDA_COVERAGE_DIAGNOSTIC_20260922/AUTHORIZATION.md`
- `reports/NVDA_COVERAGE_DIAGNOSTIC_20260922/plan_1/plan.json`
- `reports/NVDA_COVERAGE_DIAGNOSTIC_20260922/plan_1/run_1/diagnostic.json`
- Three raw `call-0-reply.json` through `call-2-reply.json` files beside the trace.
- `reports/NVDA_COVERAGE_DIAGNOSTIC_20260922/plan_1/supervisor_result.json`

Approved plan SHA-256:
`159279b7367429b60c745574ba5f844d5951a873a306df6fbc41cd52787f6ccd`.
Execution code revision: `4cfb8459ce9f4cf68cc0465e684a471b92b7ad93`.
Reader SHA-256:
`36c0d231597ca3ae4a720d31783705ce1a41a53569c8f0ca21ca3e2bbeef7018`.
All bound source-artifact hashes were rechecked after execution and unchanged.
No historical reader, checkpoint, usage flag or evidence packet was modified.

The harness uses the engine's shared exact payload builder. Offline validation:
53 affected engine/finalization/payload tests; a subsequent 22-test diagnostic/
payload group; and 12 model-service tests (groups overlap, do not sum). Lint and
whitespace checks pass. Independent Sol/high safety review found no technical
blocker after its fixes and the required explicit advisory-limit approval.
Hosted CI passed on implementation `4cfb845` for Python 3.10–3.13, clean locked
installation, and container privacy/import. Later documentation-only commits do
not change the executed implementation; their hosted status belongs to the PR.

## Next sequence

1. Preserve this comparison and integrate the reviewed diagnostic harness into
   the feature branch. Leave the default policy and all acceptance gates unchanged.
2. Create a small offline semantic evaluation set for the observed false closure:
   general report status alone is insufficient; explicit financial-draft status
   should pass; a reviewed operating package must not clear an unreviewed financial
   case. Test meaning, not the literal words “Stage 2.” Do not lower the gate.
3. Then run a fresh, separately recorded bounded comparison using negative and
   positive disclosure controls and counterbalanced policy order. Standing approval
   covers reasonable bounded follow-ups, not retries on unknown usage or unlimited
   allowance renewal. The current capsule cannot be reused or silently reordered.
4. Only after semantic stability and efficiency are demonstrated decide on the
   next bounded end-to-end acceptance run. Keep unsupported valuation/funding
   conclusions withheld. No 180-minute run or concurrency work follows automatically.

Stage 3 reader acceptance, financial-case closure, independent ecosystem evidence,
HOOD generality and release approval remain open.
