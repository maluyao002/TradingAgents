# Stage 3 — case-backed reader engineering checkpoint

September 19, 2026. Development branch: `codex/research-stage-3`; integration
target: `codex/deep-research-v2`, never `main`. This is an engineering checkpoint,
**not completion of Stage 3's model-backed NVDA report or permission for live calls**.

## Delivered boundary

The core engine accepts an optional `financial_case_path` with
`quality_revision="evidence-led-bounded"` and `valuation_method="fcff"`.
The input is an immutable JSON envelope:

```json
{
  "case": {"...": "FinancialCase fields"},
  "review": null,
  "source_passages": []
}
```

`FinancialCaseEnvelope` / `FinancialCaseReview` / `CaseSourcePassage` in
`tradingagents/research/case_context.py` are the authoritative schemas. The
example above is explanatory, not a runnable case. Omitted review means draft,
not approved. An optional completed independent review binds the canonical case,
snapshot and any supplied exact passages; its findings and limitations survive.
Reviewer identity is recorded provenance, not a cryptographic proof of independence.
Neither a reviewer nor user approval can clear deterministic financial blockers.

- Recompute reconciliation from the case and snapshot; do not load a cached
  reconciliation as authority. Validate ticker, cutoff, timezone, units, ancestry
  and source material using the existing financial contracts.
- Freeze case bytes into request identity alongside evidence. A changed assumption,
  review, passage or evidence snapshot invalidates prior checkpoints. A follow-up
  that changes evidence stops before further analysis with the old case.
- Deliver actual schedules, selected facts, conventions, exact source passages,
  review, limitations and result scope to analysts, synthesis, reconciliation and
  factual verification. The first challenger remains blinded to the lead case;
  coverage-only checks receive the exact reader and required issue inventory.
- Preserve source passage spans and hashes. Authored passages are checked against
  frozen source text; source-level associations are not an entailment judgment.
  Generic fallback excerpts are explicitly labeled and omissions remain limitations.
- Export case/reconciliation/context, serialized-payload delivery audit, separate
  model appendix, exact-reader review and hashes, usage ledger and admission result.
  Delivery audit measures orchestration payloads, not provider token counters.

## Reader and admission

The opt-in reader has one section for each purpose: executive thesis, central
disagreement, business/financial drivers, management, scenarios, counter-case,
falsifiers and material gaps. Section presence is a structural check only.
The editor is instructed to prioritize the decisive disagreement, make causal
links and uncertainty explicit, and rank gaps without erasing material caveats.
Unsupported numerical scenarios are not required to fill a section.

`report_admission.json` separates:

| Decision | Meaning |
| --- | --- |
| Report completion | Completed run, exported exact-reader hash, factual review and explicit engine-validated limitation coverage |
| Qualitative analysis | Eligible only under that report-review boundary; not a blanket certification of every claim or an investment recommendation |
| Model conclusions | Operating, equity/per-share, funding and opening-date statuses remain independently scoped |
| Acceptance eligibility | Blocked by incomplete report/telemetry, unreviewed case or unresolved model prerequisites; eligibility is not activation |
| Production activation | Always false at this stage; recommendation and target remain withheld |

A completed useful report can therefore remain `needs_review` and unrated, with
valuation blocked. Failed admission returns `incomplete` and cannot be cached or
reported by the CLI as a successfully completed run. Repaired reader findings
remain in the audit; only active findings govern the final admission decision.

**Current deliberate limitation:** financial schedules are not yet bound to a
freshly reviewed company forecast. The case-backed workflow makes no unconstrained
valuation-model call and exports no numerical valuation result. All four model
conclusion gates remain blocked. It does not ingest the old conditional scenario
memo as though it were current reviewed financial clearance. Legacy runs without
the opt-in retain their identity and behavior.

Ordinary checkpoint replay is supported. Historical-prefix recovery from the old
six-stage workflow is explicitly rejected for this new case path; importing those
analyses would falsely claim they saw the new case. Unknown usage remains unknown.
No new recovery bypass has been introduced.

## Offline NVDA input preparation

New immutable local artifacts:

- `reports/RESEARCH_STAGE3_20260919/case_1/`: regenerated Stage 2 draft with the
  merged timezone/commitment-kind fixes, using the existing frozen source cache.
- `reports/RESEARCH_STAGE3_20260919/preflight_1/`: core-reader input, exact passages,
  reconciliation, replay-only request, readiness record and hashes. **182 facts;
  zero model calls; no research report generated.**

The reusable preparation command verifies both source manifests and their binding
before creating a new destination; it never overwrites an existing directory:

```sh
.venv/bin/python -m scripts.research_case_preflight \
  --case-dir reports/RESEARCH_STAGE3_20260919/case_1 \
  --output reports/RESEARCH_STAGE3_20260919/preflight_2
```

The generated request uses `backend="replay"`. Running the engine offline additionally
requires explicitly supplied saved model responses; preparation itself is not a run.
The synthetic end-to-end regression responses are labeled mechanics-only and are
not presented as a new NVDA reader or evidence of improved research quality.
The real 182-fact packet also passed an end-to-end mechanics replay using those
synthetic responses in a temporary test directory; no fabricated report was saved
as a deliverable. Final local validation: **240 targeted tests passed**, with
changed-file lint/whitespace checks. Independent integration/admission review has
no remaining findings after strict-boolean fixes. Required hosted CI is unchanged.

## Editorial comparison and acceptance rubric

References inspected: supplied
`/Users/luyaoma/Downloads/NVDA_Equity_Research_R2a_2026-09-17.html` and historical
`reports/NVDA_V2_20260917/run_recovered_1/reader_report_en.md`.
This is an **editorial comparison across different evidence/assumption sets**, not
a same-evidence quality score or validation of Claude's factual claims.

| Observation | Stage 3 check | Still needs a real reader |
| --- | --- | --- |
| Historical reader explains strong operating execution versus unresolved investment value, but can read as parallel essays | One prioritized central disagreement and evidence-to-cash-economics links | Judge whether synthesis is genuinely hierarchical and concise |
| Comparator §§7.2–7.5 use pre-mortem, triggers and kill criteria | Explicit counter-case and observable falsifiers; do not invent thresholds | Check that disconfirmation is consequential, not boilerplate |
| Comparator M12 flags limits of a two-quarter management sample | Management judgment must retain sample-size and evidence limits | Review guidance credibility, incentives and allocation with adequate history |
| Comparator §06 makes scenario mechanisms and update rules explicit | Qualitative scenarios allowed when numbers lack support | Complete period-aligned, financially reconciled, independently reviewed scenarios |
| Historical preview's limitations/appendices repeat many caveats | Separate reader and audit; prioritize material gaps while verifying each required issue | Verify that readability improves without suppressing uncertainty |

For each major judgment, the reviewer should be able to locate evidence, inference,
uncertainty and a falsifier. The opening should reveal the thesis and decisive
disagreement; the body should explain business/financial drivers, management,
counter-case and at least two observable falsifiers. Material gaps should be
prioritized, and unsupported targets/probabilities/certainty must be absent.
Headings, passing schema tests and a green replay do not establish these outcomes.

## Remaining deliverables and ownership

Engineering-owned (no user choice of WACC or assumptions needed):

1. Finish Stage 2 fiscal-guidance/forecast alignment, economic underwriting,
   opening-date roll-forward, capitalization/realization/tax and model-bound
   commitments/funding coverage; preserve genuinely unavailable disclosures.
2. Bind reviewed scenarios and material model inputs to those schedules in this
   core-reader path, with deterministic scoped calculations and numeric references.
   This is not delivered by the interim narrative-only case path.
3. Run the completed model-backed offline replay and a final readiness review,
   including context/budget estimates and substantive editorial evaluation.
4. After an authorized development-validation pilot, compare its actual English
   reader against the supplied report. No new report or superiority claim exists yet.

User checkpoints remain separate: authorize a **specific** bounded live plan before
dispatch, review the completed report for usefulness/readability, and authorize
merge/release when requested. No live budget, merge or production activation is
implied by this engineering checkpoint. The initial development pilot is not the
broader M6 release pilot.
