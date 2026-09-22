# Disclosure controls — bounded follow-up, September 22, 2026

The [first paired diagnostic](DEEP-RESEARCH-COVERAGE-DIAGNOSTIC.md) found a
financial-draft false closure despite exact quotations. Its harness is integrated
by PR #20 (`bd604c7`) into `codex/deep-research-v2`. Main is unchanged; the merged
`codex/coverage-paired-diagnostic` branch was removed locally and remotely.

## Purpose and protocol

Develop on `codex/coverage-disclosure-controls`, with a separate PR targeting the
feature branch. This increment adds evaluation controls and a six-call campaign,
not a production semantic classifier, report repair or default-policy change.

The offline evaluation set retains the exact issue inventory and original reader,
then creates three variants for the `case.review.draft` obligation:

1. Original general report status only: expected unresolved.
2. Original reader plus a statement that the conditional operating package was
   reviewed only for operating inputs/calculations: expected unresolved.
3. Variant 2 plus an explicit statement that the financial schedules remain an
   unreviewed draft and importing them is not approval: expected reader-covered.

The positive control does not require the internal phrase “Stage 2.” Both edited
variants use the same neutral review-scope heading. Labels and grading rationales
are kept outside the provider payload. These are synthetic evaluation additions,
not source facts or modifications to an accepted report. The unchanged original
already has the first diagnostic's live observations; it is not dispatched again
in this campaign.

Live order on the same 18 frozen issues, using the unchanged Sol/high verifier:

| Reader control | First | Second | Third |
| --- | --- | --- | --- |
| Operating review only (negative) | Packed 18 | Legacy 12 | Legacy 6 |
| Explicit financial draft (positive) | Legacy 12 | Legacy 6 | Packed 18 |

This balances policy order **across different controls**, not within each one.
Reader condition, warm-up, order and model variation still confound a causal
speed claim. Four target-containing calls provide four semantic observations;
the two legacy-six calls check the remaining issues, not additional target trials.
The intended result is correct negative/positive discrimination under each policy,
not mere policy agreement or full-report acceptance.

## Authorized bounds and fail-closed behavior

The user approved this next sequence and reasonable bounded follow-ups. The
recorded campaign limits are six calls, 900 seconds overall, 350,000 tokens
best-effort aggregate admission/post-call enforcement, and 600 seconds per call.
The larger token reserve covers two complete matched comparisons. Worker 880s
and supervisor 890s preserve cleanup headroom. There is one allowance for all six
calls, not a reset between controls. Do not extend the deadline to finish a pair.

No retry, fallback, automatic renewal, report export, historical edit or reusable
attestation is allowed. Unknown usage stops dispatch and remains unknown. Each
capsule is fresh and single-use, bound to source hashes, runtime/code revision,
full payloads and the exact plan digest. Old three-call plans retain their
separate original contract. The existing OpenAI Codex destination and disclosed
reader/issue scope remain unchanged apart from the stated synthetic paragraphs.

## Offline gates and interpretation

Targeted tests cover the control transformations, unchanged issues, no expected
label leakage, exact source/reader bindings, missing/duplicate decisions, malformed
quotes, wrong decisions, unreviewed responses, budget exhaustion, shared deadlines,
incomplete usage and old-plan compatibility. Mock replies test the harness, not
model semantic competence.

A correct negative answer can include a critical missing-disclosure finding;
that is expected detection, not a failed control or an accepted reader. All
findings remain in the raw/checked review. A positive answer must also pass the
ordinary exact-witness and affected-finding checks. Success on these narrow
synthetic controls does not establish that the real writer consistently supplies
the disclosure, or that other material limitations are semantically covered.

Results, exact binding hashes and review outcomes will be recorded after the
campaign. Keep the legacy default and all financial acceptance gates unchanged.
Only then decide whether evidence supports further bounded validation; a full
research run is not automatic.

Pre-live verification: 72 affected diagnostic/payload/engine/model-service tests
passed, followed by the final 30-test control/helper scope after scoring fixes
(overlapping groups, not additive). Lint and whitespace checks pass. The actual
historical v1 plan still validates; replaying its saved replies through the scorer
correctly flags legacy's false closure and accepts packed's unresolved negative.
Terra/medium implemented the controls. Independent Sol/high review reports no
remaining technical blocker. No provider calls were used for this code review.
