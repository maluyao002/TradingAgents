# Deep research evidence follow-up

## Purpose and boundary

This document records a discrete public-source follow-up for NVDA performed on
September 22, 2026. It supplements neither the historical NVDA research snapshot
nor any financial-case schedule. The frozen case cutoff remains
`2026-09-19T06:57:53.244995Z`; materials published after that time are not
eligible case evidence.

The follow-up did not run a model, amend a report, update a valuation, accept a
financial case, or make an investment recommendation. It uses no paid data or
private accounts.

Artifact: `reports/RESEARCH_SUBSTANTIVE_20260922/evidence_followup_1/`.

## Frozen-cutoff admission

Every item in this follow-up was retrieved on 2026-09-22, after the frozen
2026-09-19 cutoff. Publication before the cutoff does not override the frozen
retrieval control. This packet therefore requires a new cutoff and
new packet for use; it is not eligible for automatic merge or admission to the
historical frozen snapshot.

## What changed in the evidence record

1. Source acquisition now includes NVIDIA's latest pre-cutoff proxy, filed May
   12, 2026. It reports that 96% of CEO and 48% of other NEO Fiscal 2026 target
   pay depended on corporate performance; annual revenue drives variable cash,
   annual non-GAAP operating income drives SY PSUs, and three-year relative TSR
   drives MY PSUs. It also records the H20 export-control target-adjustment
   mechanism and maximum-payout certification. This is issuer disclosure—not an
   independent assessment of incentive quality or a closed governance finding.
2. Three sequential FY2026 original-guidance versus subsequent GAAP-revenue
   actual pairs are now retained. Every actual exceeded its original published
   range. This is evidence of three historical observations only, not a stable
   guidance-bias conclusion or FY2027 forecast.
3. NVIDIA's August 26, 2026 **Q2 FY2027 results release guiding Q3 FY2027**
   authenticates the frozen
   Q3 FY2027 $108.0 billion +/-2% revenue anchor, 74.0% +/-50bp GAAP/non-GAAP
   gross-margin anchor, approximately $9.2/$9.0 billion opex anchors, and the
   stated exclusion of Data Center compute revenue from China. It does not
   authenticate Q4 stress cases or any valuation/funding conclusion.
4. Meta's SEC-filed FY2025 10-K independently documents its own $69.69 billion
   property-and-equipment investment and associated infrastructure commitments.
   This is evidence that a potential customer has funded infrastructure spending;
   it does not identify NVIDIA as a supplier or establish hardware demand for
   NVIDIA.
5. CoreWeave's SEC-filed Q2 2026 10-Q states that its then-current infrastructure
   used NVIDIA GPUs and that current customers contractually specified them. It
   separately reports a $2B NVIDIA private placement and debt/equity/OEM funding
   of capital investment. This is substantive counterparty-concentration and
   financing evidence, but the NVIDIA financing linkage prevents treating it as
   clean independent end-demand proof. The GPU-concentration statement is the
   delivered literal excerpt; the financing facts are source-verified notes, not
   separately delivered full passages.
6. DOE/NNSA documents the AMD/HPE El Capitan deployment. This establishes a
   concrete competitor architecture in one government exascale deployment; it is
   not a general NVIDIA performance or commercial-market conclusion.

## Provenance discipline

Source-specific negative boundaries, publication dates, retrieval dates, periods,
exact locations, and same-issuer flags reside in the JSON manifest. The only
source that speaks directly to FY2027 Q3 NVIDIA guidance is NVIDIA's own
pre-cutoff release. The Meta and DOE sources are independent evidence about
their own facts, not corroboration of NVIDIA's reported revenue or guidance.

### Current-case anchor versus older-period context

The 2026-08-26 NVIDIA Q2 FY2027 results release guiding Q3 FY2027 is the
**current-case anchor**: it was
published before the 2026-09-19 frozen cutoff and directly contains the Q3
FY2027 outlook used locally. The other sources are **older-period context** and
are visibly separate: the 2025 FY2026 guidance point, 2025 proxy compensation
record, Meta year-ended-2025 funding record, and 2024 DOE deployment. They do
not backfill or corroborate newer FY2027 local claims.

### Conditional interpretation

- Meta's disclosed spending is a constructive external demand backdrop only if
  it reflects durable AI deployment; it is not NVIDIA revenue evidence because
  it does not name a supplier or volumes.
- DOE's AMD/HPE deployment proves a concrete alternative architecture in a
  specific public deployment; it does not establish broad NVIDIA displacement,
  commercial pricing, or total cost of ownership.
- NVIDIA's proxy makes both near-term (revenue and operating income) and
  longer-term (relative TSR) incentive exposures observable. It cannot answer
  whether these incentives produced a specific capital-allocation outcome.
- CoreWeave makes a direct customer/vendor concentration and financing linkage
  observable. That can support an ecosystem-dependence risk path, not a finding
  that demand is artificial or that collections will fail.

No older source was used to backfill a newer local claim. The detailed evidence
audit declares that the FY2027 Q3 anchor is publicly present before the cutoff;
the other local forecast assumptions remain exactly as bounded in the frozen
case.

## Open work

Still required before any substantive durability or funding conclusion:

- Counterparty-level independently published NVIDIA demand, utilization,
  renewal/cancellation and payment-quality evidence, plus funding evidence from
  additional counterparties. CoreWeave's concentration and financing disclosure
  is already documented by the web-source review; it does not supply these remaining operating and
  collection facts.
- Extend the existing three-pair original-guidance versus GAAP-actual revenue
  series with a complete revision chronology, additional quarters, and explicit
  accounting-basis checks where a metric is adjusted.
- Independent like-for-like NVIDIA/competitor benchmark, price, deployment or
  TCO evidence.
- A separately scoped governance review if board oversight, voting power,
  related-party controls, or the FY2026/FY2027 compensation program is material.
- The existing financial-case reconciliations: commitments, cash timing,
  liquidity restrictions, receivables, shares, equity bridge, and valuation
  inputs.

These open items are not silently converted into negative conclusions.

## Full-text delivery and the remaining user input

The public-source downloader saved three complete documents (DOE deployment,
NVIDIA Q2 FY2026 release and Q2 FY2027 release). Four SEC documents are still
blocked by `sec_identity_required`: Meta's filing, two NVIDIA proxies and the
CoreWeave filing. They were inspected through web retrieval, but their complete
text is not yet stored for engine delivery. No configured SEC contact identifier
was found in the process environment or workspace `.env`; only presence checks
were printed, not environment contents.

The requested user input is a name/organization and contact email to identify
public-filing requests to the SEC. Do not invent it, commit it, or silently use
another account. After it is supplied, collect into a fresh directory, assemble
an eligible new packet and refresh dependent analyses/reviews.

The acquisition prototype produced `evidence_capture_1` and `evidence_capture_2`
under the follow-up directory, each with three successful captures and four
identity blocks. The original DNS-failure attempt is retained separately. A
prototype metadata repair subsequently attached source-manifest hashes to those
records; these are **post-hoc associations, not proof of the original input
bytes**. The raw/text blobs and their recorded hashes remain useful for inspection,
but these prototype manifests are not an accepted engine packet.

Review removed the metadata-repair and in-place-retry paths. The final utility
requires a fresh destination, checks the supplied mapping against captured input
bytes before retrieval, retains those exact bytes as `source_manifest.json`,
persists raw/text bodies, and flags input mutation before publishing the manifest.
No historical capture is rewritten by the corrected utility. Its tests cover
identity blocks, duplicate IDs, bounded acquisition, actual body persistence,
input mismatch/mutation and refusal to overwrite a failed attempt.
