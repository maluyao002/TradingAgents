# Offline repair after the September 20 NVDA validation

## Scope and baseline

The bounded English NVDA validation on `c397176` consumed 2,106,837 measured
tokens in 62.53 minutes. It stopped before repaired coverage: 893,980 conservative
reserve tokens were required and 893,163 remained. Its repaired factual review
also contained a critical rendering-provenance finding and citation warnings.
The run did not export or accept a report. The private local validation report is
`reports/NVDA_VALIDATION_20260920/VALIDATION_REPORT.md`.

This follow-up implements that report's offline steps on
`codex/research-validation-interfaces`, targeting `codex/deep-research-v2`, not main.
It does not collect new financial data, perform live research, revise historical
reports, activate production or clear Stage 2/3 economic acceptance.

## Delivered interfaces

### Authoring and exact-reader verification

The rendered verifier receives a different contract from the author. Template
markers are expected in authored drafts and expected to be absent after rendering.
Each initial/repaired/translated candidate has a deterministic provenance record:
authored and prepared hashes, exact marker-bearing paragraphs and expansions,
calculation IDs, catalog hash and final reader hash. Exact saved rendering inputs
permit recomputation of both the prepared draft and the final reader, rejecting
changed text or metadata. A provenance record does not override a verifier
finding or establish source entailment or economic likelihood.

Bounded case readers use numeric links to stable calculation entries in the model
appendix. Those entries retain classification, package/result hashes and source
ancestry. Issuer footnotes remain separate: an issuer is not represented as having
reported an analyst assumption or calculated fiscal total. Handwritten calculation
provenance links are rejected across draft titles, bodies, limitations and issue
text, including equivalent encoded/escaped destinations; authors must use the
calculation/table markers. Raw appendix anchors and encoded links remain matched.
Version-6 offline previews require provenance, recompute the complete binding,
verify the full calculation catalog and appendix, and copy the verified appendix
while remaining explicitly unverified. Missing provenance cannot downgrade a new
run into the legacy preview path.

### Evidence-backed warning lifecycle

The verifier gets an explicit catalog-key and literal-excerpt contract. Bare
source IDs, `rendered_reader`, nonliteral ellipsized witnesses and empty witnesses
cannot retire a warning. Reader excerpts have their own field and current hash.

For a known historical claim defect, code can supply a hash-bound claim-change
record establishing only that the original literal is absent from this reader.
The verifier must choose `superseded`, cite the current-reader hash and quote the
specific replacement/qualification. It must still judge semantic correction;
absence of a literal is not proof that an equivalent unsupported claim disappeared.
The underlying research question is not thereby resolved. Mixed claim/question,
financial-designated, security and protected-origin issues cannot use this shortcut.
Questions may still be resolved through ordinary exact supporting evidence.

### Reader versus audit

Authoring instructions require a short ranked investment-relevant boundary and
keep provider/reviewer procedure and historical warning records in the audit.
Material financial uncertainty stays in the reader and under coverage checks.
Consecutive duplicate source-footnote clusters are collapsed, preserving distinct
sources and citations separated by claim text. Paragraph metadata identifies
calculation links separately from issuer sources. These mechanics do not prove
that a new model-authored reader will be concise or semantically well cited.

### Context and complete repair admission

The model boundary stores large byte-identical JSON context once, referenced by
hash. It does not summarize, truncate, merge similar facts, change provenance or
promote source text into trusted instructions. A strict offline decoder verifies
round-trip identity; colliding reserved source keys disable packing. Small or
unprofitable transformations retain the original representation.

The actual adapter and admission planning share the complete model boundary:
trusted instructions and role suffix, packed prompt and strict provider wire schema.
Both include the same output-allowance instruction; domain schemas are not used as
a substitute for the actual dispatch schema when counting bytes.
Conservative input-byte reserves and full output envelopes remain in force;
heuristic token estimates are not substituted for them. Before a repair dispatch,
the engine checks its exact payload plus the estimated factual re-review and
coverage path. Insufficient capacity stops as `repair_path_budget_insufficient`
before paying for the repair. Future reader growth is explicitly estimated and
post-repair exact coverage admission still runs. A cached repair does not bypass
admission of an uncached factual re-review plus its entire current coverage path;
an insufficient continuation stops as `reverification_path_budget_insufficient`
before paying for that re-review. Exact cached replies are not charged again.
No provider-enforced hard cap or
guarantee of finalization is claimed, and unknown usage still prevents retries.

## Verification and limitations

Targeted offline regressions cover initial and repaired bindings, forged links,
appendix/preview integrity, exact witness namespaces, removal versus question
resolution, protected mixed issues, lossless context packing and corruption,
adapter/planner equivalence, whole repair-path refusal, recovery and financial
gate preservation. Hosted CI retains the repository's normal full checks.

The initial integrated scope passed 310 tests. After independent and hosted review
fixes, 232 affected tests passed; the final title/issue-link follow-up passed 44
directly affected tests. These overlapping counts are separate runs, not additive.
Ruff and whitespace checks pass. Independent reviews cover lifecycle protections,
complete model-boundary accounting, preview recomputation and partial-cache
admission. All verification described here is offline.

An offline replay of saved NVDA outputs measured lossless prompt-byte reductions
of roughly 7–12% in several large analysis/factual/editor calls. This is not a
live-token or quality benchmark. Old review replies are deliberately not accepted
as reviews of the new rendered bytes; replay ends at the first unavailable saved
response. The original evidence, case and run files remain unchanged.

Engine identity advances to `research-v2-preview-6`. Existing version-5 candidate
continuations must not silently reuse reviews under changed prompts/rendering.
Current-contract continuation remains covered by offline regressions; the saved
September 20 candidate is not automatically eligible for it. The next live check
must explicitly establish a compatible plan or use a fresh bounded run.

## Delivery and next checkpoint

Independent code review, targeted verification and a PR precede integration.
After merge, the next required user input is a **separate live-validation budget**.
No unused balance from the earlier campaign is silently renewed. A live pass must
still demonstrate exact-reader factual/coverage completion and useful prose before
Stage 3 reader acceptance. Financial-case closure, independent ecosystem evidence,
HOOD generality and eventual user/release acceptance remain separate work.
