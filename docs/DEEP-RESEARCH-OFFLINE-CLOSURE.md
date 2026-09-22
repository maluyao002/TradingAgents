# Offline finalization closure — September 21 validation follow-up

Scope: the recommended sequence in
`reports/NVDA_VALIDATION_20260921/VALIDATION_REPORT.md`, steps 1–4. Integration
target is `codex/deep-research-v2`, not main. New live calls, historical artifact
edits, financial acceptance and production activation are outside this change.

## Why this change

The last NVDA run stopped during coverage batch 13 of 16, after 81.86 minutes.
Its 22 completed calls reported 1,395,072 tokens; the failed call's usage and
specific cause remain unknown. A clean initial factual pass is not completed
coverage or reader acceptance. This change cannot retroactively recover counters
or establish why that historical `CodexInferenceError` occurred.

## Deliverables and boundaries

1. **Failure observability.** Adapter failures carry finite, allowlisted reason
   codes. The engine records the failed stage and model-call durations without
   raw error/provider text. A dispatched failure still poisons complete usage
   and cannot be retried as free work. Unknown errors remain unknown.
2. **Writer/coverage contract.** Financial-draft status is separate from
   conditional operating-package review. New scenario presentation is derived
   from validated frozen inputs, with input provenance distinguished from
   analyst arithmetic. Old table expansion stays byte-compatible. Exact excerpts
   remain case-sensitive; paraphrases are never silently promoted to witnesses.
   Three exact legacy compound templates receive scoped, evidence-bound treatment
   that leaves current financial questions open and retains the original audit
   record. Similar/unmatched prose is not automatically split. Each current
   component needs its own checked disposition before its original parent ID
   can pass coverage; the parent is never retired as a whole.
3. **Time-aware finalization.** Persist completed/failed call durations alongside
   existing usage. Project remaining sequential coverage and the possible
   repair/re-review path. Recheck before each coverage batch and before buying
   repair or an uncached repaired factual review. Stop a path whose observed
   known subtotal already exceeds remaining wall time, preserving its candidate.
4. **Review and integration.** Targeted offline regressions, independent code
   review, normal hosted checks and a separate PR. No model calls substitute for
   regression tests. The implementation log records final verification results.

### Timing policy

Only completed and accepted `current_live` stages with matching role, model and
effort provide samples. Replay, imported historical replies, reuse, failed calls
and replies rejected by stage validation do not. Use the
maximum of the latest five compatible samples plus 25% headroom, with a one-second
floor and ten seconds of path completion overhead. Coverage requires two samples;
writer and full factual review require one. These are engineering heuristics,
not calibrated confidence intervals or a completion guarantee.

Cold-start estimates remain unknown, not zero. Timeout sums are separately
reported as worst-case bounds, not expected latency. A known subtotal can establish
that a path does not fit even if some calls remain unknown. Hypothetical repair
is visible in the initial plan but does not block an otherwise feasible clean
pass; once repair is required, its full projected path is checked before dispatch.
Per-call timeout and token admission remain mandatory.

### Concurrency decision

Keep finalization sequential in this change. The current model service has a
single adapter, and deadline handling depends on main-thread signal behavior.
Threading calls without isolated workers would risk cancellation, checkpoint
ordering and unknown-spend accounting. Bounded concurrent workers require a
separate design with atomic aggregate reservations, cancellation settlement and
deterministic merge tests. No throughput benefit is claimed here. Existing exact
coverage reuse and lossless context packing remain enabled; neither removes a
material obligation or replaces full-context factual verification.

### Compatibility and preserved gates

The engine revision advances to preview-7. A new prompt or lifecycle contract
cannot silently reuse a preview-6 attestation. Historical preview-6 rendering
remains supported with its original provenance contract. New case-backed
presentation binds the delivery contract to validated frozen case/evidence bytes;
preview reconstruction must reproduce it, not trust model-written numbers.
Guidance-range extraction is deliberately narrow: exact source clauses must
match the input value and safe accounting basis; ambiguous clauses remain exact
unclassified witnesses for substantive review. Scenario arithmetic uses an
explicit Decimal context. These bindings verify presentation provenance, not
the likelihood or economic adequacy of assumptions.

Recovery resource readers accept the additive diagnostics through a shared
strict shape validator; they do not loosen usage reconciliation or authorization.

Financial-case review, valuation eligibility, equity bridge, funding, independent
ecosystem evidence, HOOD generality, human reader acceptance and release approval
remain separate. An arithmetic check or safe finalization stop closes none of
those gates.

## Next authorized-work boundary

After review/integration, propose a new narrowly bounded validation plan. Prefer
compatible reusable work, but the September 21 checkpoint has incomplete usage
and unsettled dispatch and is not eligible for exact-candidate continuation under
the current recovery rules. Do not clear its flags, assume zero failed-call usage,
reuse an expired allowance or silently fall back to an expensive full rerun.
Any new live path requires explicit new allowance and the applicable acknowledgement
of unknown historical usage. Then inspect the actual exported reader before
moving to targeted financial/independent-evidence closure and HOOD.
