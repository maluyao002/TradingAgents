# Stage 3 — case-backed reader engineering checkpoint

> Historical delivery / validation record. Preserve its evidence and decisions;
> dated next steps and run allowances are not current instructions or reusable approval.
> Use the [current roadmap](../../DEEP-RESEARCH-STATUS.md) for active work and
> the [archive index](README.md) to navigate history.

September 19, 2026. PR #13 merged into `codex/deep-research-v2`; the Stage 3
subbranch was deleted. The case-editor fix merged through PR #14 as `261fe98`
into that integration branch, never `main`. PR #15 delivers the subsequent
[reader-review lifecycle repair](DEEP-RESEARCH-REVIEW-LIFECYCLE.md).
The authorized continuation produced a substantive English review draft but
stopped before repair/export; see [the outcome](DEEP-RESEARCH-STAGE3-CONTINUATION.md).
This is an engineering checkpoint,
**not completion of Stage 3's model-backed NVDA report or permission for live calls**.

## Delivered boundary

The core engine accepts an optional `financial_case_path` with
`quality_revision="evidence-led-bounded"` and `valuation_method="fcff"`.
The input is an immutable JSON envelope:

```json
{
  "case": {"...": "FinancialCase fields"},
  "review": null,
  "source_passages": [],
  "operating_scenarios": null
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
  factual verification. The first challenger runs before the planner, without
  planner questions or prior hypotheses; the initial planner is also case-blind.
  This closes PR #13's indirect case-to-planner-to-challenger leakage finding;
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

## Gap-closing follow-up — reviewed fiscal operating scenarios

The optional `OperatingScenarioPackage` now binds a copied historical revenue/
GAAP operating-income anchor, explicitly dated fiscal periods, source passages,
classified assumptions, rationales and falsifiers to the exact case and evidence.
An independently authored review binds all three hashes. Missing, stale, self-
authored or unresolved-warning reviews leave inputs audit-only. Review identities
are recorded provenance, not authenticated credentials.

The deterministic boundary supplies revenue, gross profit, GAAP operating income
and historical-plus-forecast fiscal totals; it does **not** supply tax, FCFF, DCF,
enterprise value, equity/per-share value or funding adequacy. Decimal arithmetic
is independent of ambient precision. Only reviewed values enter the reader's
calculation catalog. Admission records conditional operating scenarios separately
from the four still-blocked valuation/financial scopes and production acceptance.

NVDA's immutable `operating_case_2` draft derives a **new** observed-opening case
at July 26, preserving the September-cutoff roll-forward gaps and source-case
provenance. It uses FY27 H1 actuals plus Q3/Q4, not TTM plus two more quarters.
Q3 holds the disclosed GAAP guidance midpoint constant; Q4 inputs are explicit
analyst sensitivities, not probability-weighted or accepted forecasts. The
53-week fiscal calendar matters: Q4 has 14 weeks versus Q3's 13, so +5% aggregate
revenue is −2.5% per week. Q4's 71–72% management margin discussion is not separately
GAAP-qualified; it informs an explicit assumption rather than a reported GAAP fact.
Q3 guidance uncertainty is disclosed but not varied in these Q4 sensitivities.

Reusable offline commands:

```sh
.venv/bin/python -m scripts.research_nvda_operating_case \
  --case-dir reports/RESEARCH_STAGE3_20260919/case_1 \
  --preflight-dir reports/RESEARCH_STAGE3_20260919/preflight_1 \
  --output /new/draft-directory
.venv/bin/python -m scripts.research_operating_review \
  --source /new/draft-directory --review /independent/review.json \
  --output /new/reviewed-directory
```

The review-attachment command validates the actual case context, retains source
diagnostics/closure ledger/provenance, replaces the inherited diagnostic mandate
with the English reader mandate, and emits **replay-only** inputs. It neither
creates a research report nor authorizes live execution. Drafts 1 and 2 are
preserved; draft 1's weekly-comparison diagnostics were superseded by draft 2.

Final local handoff: `operating_case_2_independent_review.json` records the
independent Astra/high economic review; `operating_reviewed_1/` contains the
attached review, 36 calculated references, source context and replay-only request.
The real 182-fact reviewed packet passed an end-to-end mechanics replay and
checkpoint replay in a temporary directory (16 synthetic responses). It produced
conditional operating admission while acceptance and valuation remained blocked.
No synthetic report was retained as a research deliverable. The case context is
198,132 UTF-8 bytes; this is a payload-size measurement, **not** a token estimate.

The inherited 600-second overall replay budget is **not** the live plan. Proposed
single English development pilot: 1,500,000 total tokens, 5,400 seconds overall,
600 seconds per call, zero optional follow-ups, at most one reader repair, no
provider fallback. Before dispatch, obtain explicit authorization and create a
fresh live request with finalization reserves (1,200 seconds / 300,000 tokens,
within—not in addition to—the total caps). Do not mutate the reviewed replay
bundle. No live research calls have been made in this follow-up; subagent usage
is separate and aggregate subagent token telemetry is unavailable.

## Remaining deliverables and ownership

Latest update: the subsequently authorized live pilot failed in parent supervision
after six completed calls; it did not reach a final reader. Known usage is 558,608
tokens plus an unsettled call. The input and checkpoints remain unchanged, and no
live retry was attempted. [Pilot 1 diagnosis and recovery boundary](DEEP-RESEARCH-STAGE3-PILOT1.md)
supersedes the pre-dispatch authorization checkpoint above. Supervisor hardening
and exact same-case prefix recovery are implemented and tested offline. The
[recovery procedure](DEEP-RESEARCH-CASE-RECOVERY.md) separates offline preparation
from a new, destination-bound incremental authorization. No continuation has been
dispatched; the original unknown usage and failed source remain unchanged.

Engineering-owned (no user choice of WACC or assumptions needed):

1. The fiscal operating bridge, independent review attachment, numerical reader
   integration and real-packet offline readiness checks are complete. Synthetic
   model responses prove mechanics, not report quality.
2. A full cash-flow valuation still requires economic underwriting, opening-date
   roll-forward, capitalization/realization/tax and model-bound commitments/funding
   coverage. Missing disclosures stay unknown. These block valuation conclusions,
   not a useful conditional operating report, and are not user financial sign-off.
3. After an authorized development-validation pilot, compare its actual English
   reader against the supplied report. No new final report or superiority claim
   exists yet. Fix substantive reader defects before claiming the Stage 3 exit.

User checkpoints remain separate: authorize a **specific** bounded live plan before
dispatch, review the completed report for usefulness/readability, and authorize
merge/release when requested. No live budget, merge or production activation is
implied by this engineering checkpoint. The initial development pilot is not the
broader M6 release pilot.
