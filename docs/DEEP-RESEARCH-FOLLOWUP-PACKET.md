# Targeted evidence packet and review readiness

## Scope

This follow-up continues PR #23 on `codex/research-substantive-closure`, targeting
`codex/deep-research-v2`, not `main`. It prepares substantive inputs for a fresh
English NVDA analysis. It is not a rerun of the old writer, financial acceptance,
or an attempt to reset the failed historical run's unknown usage.

## Delivered inputs

The new `scripts/research_followup_packet.py` assembles a fresh, offline-only
packet from the old request/case and reviewed selections from captured public
documents. The first output is
`reports/RESEARCH_SUBSTANTIVE_20260922/eligible_packet_1/`.

- New cutoff: `2026-09-22T23:58:49Z`, after the actual source retrievals.
- Six new full-text sources and 12 exact passages, totaling 5,225 characters.
- Evidence covers Meta infrastructure investment, DOE's specific AMD/HPE
  deployment, NVIDIA's current proxy incentives and ex-ante H20 adjustment,
  CoreWeave's contractual GPU concentration and financing links, and two NVIDIA
  guidance releases. Full Q3 FY2027 revenue, margin and expense guidance is included.
- The older FY2025 proxy remains archived; the selected incentives material uses
  the newer FY2026 proxy. Seven captured documents do not mean seven independent
  corroborating sources, nor a complete guidance-history packet.

The authored selections and their source-review notes are preserved at
`reports/RESEARCH_SUBSTANTIVE_20260922/passage_review_1/`.

## Integrity and delivery boundary

The assembler verifies the v3 capture's original manifest bytes, source identity,
raw/text cache binding, URL, status, retrieval time and exact excerpt offsets.
It pins the input bytes actually used and rechecks them before publishing a fresh
output manifest. Historical files are not overwritten. Unsupported captures,
duplicate selections, mismatched excerpts and ineligible cutoffs fail closed.

Date-only publication evidence remains in the audit with its basis. The snapshot
uses observed retrieval as conservative availability, not an invented midnight
publication time. It is eligible for the new cutoff, not for backdating into the
old frozen case.

All old facts, events, expectations and gaps retain their dates and contents.
The later cutoff is a **targeted extension, not a complete current-data refresh**.
It does not update market quotes, consensus, share counts, balance sheets, or
commitment timing. Old analyses, reader approvals and independent-review records
are not copied as current approvals.

The new selected excerpts enter the existing conditional operating package's
exact-source-material channel. They do not become invented numerical facts.
The first assembled case and operating package remain explicitly unreviewed;
their new identities require an independent review before controlled scenario
delivery. The financial case stays a draft even if a narrow operating or cash-flow
review subsequently succeeds.

## Current reviewed-input checkpoint

The independent cash-flow review is complete and has been attached through the
new adapter. The current deliverable is
`reports/RESEARCH_SUBSTANTIVE_20260922/reviewed_inputs_1/`, with the separately
retained review and exact residual attribution at `nvda_cashflow_case_4_review/`.
The earlier `eligible_packet_1` and cash-flow case-4 drafts remain unchanged.

Read-back of the final bundle verified every artifact hash and reconstructed the
case context through the normal validator. The actual case delivery contains all
12 new exact passages, 36 operating calculated values and 57 cash-flow calculated
values. Its model-context size is 287,469 UTF-8 bytes; this is an input-size
measurement, not a token count or a complete live-run cost estimate.

| Boundary | Current result |
| --- | --- |
| Operating package | Current independent source/arithmetic review attached |
| Conditional cash-flow bridge | Separate current source/arithmetic review attached |
| Financial case | Draft; not accepted |
| Operating-asset value, equity/per-share value, funding | Blocked |
| Fresh analysis, exported reader and report acceptance | Not run / not accepted |
| Historical failed-call usage | Preserved as unknown; not reset |

The exact new cash-flow package identity is
`6a7a88af8582c86bba51bb18940af26b36fd2edaa433b0fc87b91ec7d5673334`.
Source-level residual attribution is now independently recorded with exact spans
and full-precision decimal amounts. It neither zeros the variance nor adds the
missing typed FinancialFacts. No source/arithmetic review is economic underwriting.

## Remaining sequence

1. Budget the fresh analyst, writer,
   factual and coverage path, including repair and wall-time reserves.
   The current input-delivery preflight is complete; it is not a completed
   fresh-analysis or actual-reader check.
2. Run bounded fresh analysis and inspect the exported English NVDA reader.
   Do not use old analysis responses as substantive validation of new evidence.
3. Promote the source-grounded residual attribution into typed, tested financial
   reconciliation; any changed evidence/case identities require dependent review.
   Close the other targeted financial and independent-evidence gaps, then prove the distinct
   HOOD workflow. Reader acceptance and production release remain user gates.

No new model call or live research allowance is implied by packet construction.
The replay request's copied budget is a configuration template, not remaining
historical capacity or approval to spend it.

## Independent operating review and code review

Sol/high independently checked the exact new evidence, case and operating package,
all 17 source passages (five retained and 12 new), the unchanged 182 historical
facts, fiscal periods and operating arithmetic. The new review is retained at
`eligible_packet_1_review/operating_scenario_review.json` under the same local
follow-up root. Applying it produced `operating_reviewed_1/` with 36 controlled
operating calculated values. The financial case remains a draft. This is a new
review, not the old approval with replaced hashes.

The reviewer also identified a source-grounded mechanical attribution of the
approximately $3.950bn historical cash-flow variance. The decomposition distinguishes
net income versus the operating-tax proxy, noncash adjustments, and the reported
cash-flow working-capital rows versus the narrower balance-sheet proxy. Some
rows still lack individual typed fact records; the bridge therefore retains its
nonzero residual. Attribution in review notes is not a silently completed typed
reconciliation or an endorsement of forward assumptions.

The cash-flow builder now dates its opening-to-cutoff limitation from the actual
case and reports economic limitations separately from changing review status.
The new `scripts/research_cashflow_review.py` accepts only a separate, current
independent review bound to the exact draft bridge, operating package, case and
evidence. It regenerates a fresh English replay bundle and keeps valuation,
per-share value and funding blocked. It does not invent a review or confer live
authorization.

Independent Sol/high code review found three P2 issues: a dangling output symlink,
duplicate document identities, and insufficient aggregate full-text bounds.
All were fixed with regressions. Final re-review found no remaining actionable
finding within the changed-code scope. Source manifests, blobs and the supplied
review are pinned before use and rechecked before publication. This code-review
disposition is separate from substantive financial and report acceptance.

Verification: **127 targeted tests passed** across packet capture/assembly,
operating and cash-flow reviews, case ingestion, and engine-to-reader cash-flow
delivery. The final reviewer also ran 44 focused checks. Ruff and whitespace
checks passed. The local full suite was not rerun; hosted CI requirements remain
unchanged. No live research/model call was used in place of these regressions.

## PR #23 inline-review corrections

The four subsequent GitHub inline findings are addressed separately from the
earlier local review above:

- Incremental commitments must overlap the assigned period's disclosed due
  window; inclusive date boundaries are allowed, later-year assignments rejected.
- Shared commitment treatments require identical period-ID/date mappings in
  every scenario, preventing a deduction from disappearing in another scenario.
- Bridge fact eligibility now permits pre-cutoff, exact SEC archive filings
  retrieved later, bound to the instrument CIK and accession. Mutable URLs,
  wrong identities, content mismatches and ineligible fact ancestry stay blocked.
- Period and rollup calculation provenance includes operating-income source
  ancestry and incremental commitment facts as well as cash-flow assumptions.
  Missing operating-income calculation ancestry fails closed.

Engine revision `research-v2-preview-10` invalidates old execution/cache identities
and finalization checkpoints. Offline previews support the new revision without
relaxing their frozen-case, catalog or rendering checks. Earlier saved bridge
catalogs can fail revalidation because their ancestry is incomplete; they must
not be relabeled or silently overwritten as current outputs.

A read-only reevaluation of `reviewed_inputs_1` retained all 57 cash-flow values,
their numeric fields and input/result identities, and expanded ancestry on 54
entries. Every file in the historical input bundle was verified unchanged.
The frozen derived artifacts therefore remain historical; fresh engine delivery
reconstructs the corrected context/catalog from the unchanged reviewed inputs.
This is not a new live validation or financial acceptance.

Verification for this correction: **127 targeted tests passed** across the bridge,
new inline-review regressions, review adapter, case context/scenarios, calculated
values, engine delivery, reader previews, review disclosures and finalization
recovery. This is a separate, overlapping test selection, not an additional 127
unique tests on top of the prior checkpoint. Ruff and whitespace checks passed.
An independent Sol/high read-only re-review found no actionable findings in
the correction. That reviewer could not run pytest in its shell; the targeted
test results above were run by the coordinator using the project environment.
