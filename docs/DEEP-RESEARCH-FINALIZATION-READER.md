# Offline finalization efficiency and reader presentation

September 19, 2026. Development branch: `codex/research-finalization-reader`;
integration target: `codex/deep-research-v2`, never main. This delivery addresses
steps 2 and 3 of the NVDA comparison follow-up. It does not authorize live calls,
accept the historical NVDA draft, close financial gaps or close Stage 3.

## Finalization efficiency

- Exact equivalent obligations can share a review only when their complete
  context is identical. Every original ID is retained and receives a disposition
  after fanout. Different claims, origins or protection flags prevent grouping.
  This is not fuzzy or semantic deduplication.
- Repeated claim/finding/origin context can be represented by field-bound hash
  references when this makes the complete coverage payload smaller. Validation
  still uses the full original issues; context references are not evidence of
  factual resolution.
- `finalization_plan.json` records the current reader hash, actual serialized
  coverage payloads, output envelopes, cache hits, remaining allowance and a
  possible repair/reverification path. It distinguishes a four-bytes-per-token
  planning heuristic from the conservative one-byte-per-token admission reserve.
  Repair size and reader growth are explicit estimates; summed call timeouts are
  advisory, not expected elapsed time. Serialized orchestration payload size is
  not measured provider token usage. No provider-enforced spend cap is claimed.
- The complete remaining coverage pass must fit its conservative token reserve
  before its first new dispatch. The factual-reviewed candidate is saved first,
  so a budget stop does not force another paid draft/factual review.
- Clean coverage can be reused only for an identical reader, issue context,
  instructions and response contract. A changed reader or failed disposition
  requires fresh review. Reuse has zero **new** tokens; original spend stays in
  the ledger and the reused stage is identified.

## Candidate continuation

The engine writes `finalization_checkpoint.json` with the reader bytes/hash,
factual-review stage, model replies and payload/output hashes, input hashes,
provider identity and cumulative usage. Continuation reconstructs the state by
replaying exact saved calls under the current engine, including editor, factual
review and coverage work—not just the original six analysis stages. Only missing
calls may reach the explicitly authorized provider.

The first supported scope is a current-contract English financial-case run using
FCFF case routing, frozen inputs, `followup_cycles=0`, no extra report language and
complete, settled usage. Unknown/unsettled spend is rejected, never made zero.
The destination must be separate from the immutable source, with no dossier
publication. Changed inputs, settings, contract versions, reader identity,
provider identity or authorization fail closed. This is not a migration of the
old NVDA campaign or its expired budget.

Restored continuation state must retain the exact candidate-import proof and a
consistent first-live-call boundary. Unknown call usage cannot be followed by
another paid call. Offline plan creation uses a no-overwrite, symlink-resistant
write so concurrent file creation cannot replace historical data.

Prepare a reviewable plan offline:

```sh
python -m cli.research_finalize prepare --source-dir SOURCE_RUN \
  --config NEW_REQUEST_JSON --output PLAN_JSON
```

Execution requires a separate authorization file bound to the plan hash, new
request identity and incremental budget, plus `--allow-live` and an existing
isolated runtime home. The command uses the existing process supervisor. No such
live execution is part of this delivery.

## Reader presentation

Compact mode is an opt-in **candidate format**, not an acceptance decision. It
uses the case draft's authored, ranked material-gaps section instead of repeating
exhaustive limitations and audit IDs. All original limitations remain in audit
and in mandatory coverage review, including additional editor-authored caveats.
Security/numerical blockers and deterministic financial prerequisites remain
visible; lifecycle non-retirability alone does not force operational notes into
reader prose. The full factual and coverage review
runs against the exact compact bytes before export; no formatting is applied
after verification.

Paragraph citation mappings distinguish explicit support from section-level
source inventories. Legacy section-only references are retained with explicit
labeling, never reassigned to paragraphs as if entailment were established. A
compact sourced section with no explicit paragraph/table citation now requires
repair even when the factual verifier otherwise returns a clean review. Sources
unused by the reader remain in the audit. Editorial
instructions require citations near factual claims, concise thesis-led sections,
limited repetition and a short ranked boundary without suppressing material
uncertainty. Those instructions still require a live model-backed acceptance check.

An explicit `{{scenario_table}}` marker renders reviewed operating totals from
the calculation catalog rather than asking the editor to retype arithmetic.
Declared fractions render as percentages; quantities that are not declared
fractions are not silently rescaled. Operating scenarios remain distinct from
cash flow, equity value and funding clearance.

## Offline evidence and remaining delivery

Synthetic regressions cover exact-context grouping, lossless context expansion,
cost accounting, hash-bound reuse, budget stops before partial coverage, candidate
continuation without repeated model work, usage preservation, authorization and
tamper rejection, reader/audit separation, citations, percentages and scenarios.
These fixtures validate mechanics, not investment research conclusions.

`reader_preview.preview_saved_reader` creates a separate, conspicuously unverified
presentation comparison from saved NVDA text. Source files are hash-recorded and
left unchanged. It does not import old verification decisions or turn a shorter
reader into an admitted report.

The saved NVDA comparison is at
`reports/RESEARCH_FINALIZATION_OFFLINE_20260919/nvda_reader_preview_2/`.
The candidate shrinks from **49,460 to 26,857 UTF-8 bytes (45.7%)**, excluding the
additional unverified-preview banner. Its original prose has **zero explicit
paragraph citations**; section-level source links remain visible and honestly
labeled. The new citation gate would require a fresh repair rather than admit
that old draft. This is evidence of presentation compaction, not proof of better
factual quality or completion of reader acceptance.

Selected integrated validation: **363 tests passed**, plus Ruff and whitespace
checks. The scope covers the new helpers and CLI, engine continuation and budget
stops, legacy/evidence-led readers, case/scenario workflows, lifecycle, admission,
wire/model boundaries, storage and existing recovery paths—not the full local
suite. Independent review found and prompted fixes for inconsistent restored
continuation state, a concurrent plan-write overwrite/symlink race, and overly
broad mandatory reader visibility. Adversarial regressions cover each; the
continuation fixes passed an independent 28-test re-review. After the visibility
fix, the directly affected engine/recovery/reader/lifecycle scope passed 157 tests.

After review/integration: obtain a new bounded live budget, generate and inspect
the actual NVDA English reader, and obtain user acceptance. Financial-case closure,
independent ecosystem evidence and HOOD generality remain separate work.
