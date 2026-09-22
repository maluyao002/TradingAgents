# Bound review disclosures and retrospective PR review — September 22, 2026

## Scope and review record

PRs [#20](https://github.com/maluyao002/TradingAgents/pull/20) and
[#21](https://github.com/maluyao002/TradingAgents/pull/21) had independent Sol/high
agent reviews and passing hosted CI before integration. Neither has a formal
GitHub approval review. These are different forms of evidence; prior review did
not mean the code was defect-free.

The requested fresh review found five actionable defects:

| Origin | Finding | Repair |
| --- | --- | --- |
| #20 | Execution could select a different Codex home/provider context than the prepared plan | Bind the resolved dedicated runtime home and provider before attempt consumption and worker startup |
| #20 | Failure saving pre-dispatch intent could reference uninitialized/stale timing and falsely mark usage unknown | Track actual invocation separately, initialize timing, cancel undispatched reservations, retain unknown usage after actual dispatch |
| #20 | Runtime hashing omitted imported first-party modules outside the research package | Bind Python sources across `cli`, `scripts` and `tradingagents` |
| #21 | A positive synthetic control could pass using an irrelevant but exact quote | Require the fixed positive-control witness in the scorer, outside the model payload |
| #21 | Control selection assumed the target occurred in the first eligible batch | Find the unique protected target across the inventory, then select its matched batch without dropping or reordering issues |

The scorer requirement is specific to a synthetic evaluation with a known inserted
sentence. It is **not** a production semantic whitelist or a way to close report
issues by matching words. Historical plans remain inspectable; the changed runtime
does not authorize executing them again.

Read-only rescoring of the four saved target-containing responses under the fixed
scorer preserves the original observation: both operating-only negatives fail,
and both explicit financial-draft positives pass. The positives quote the actual
financial-draft sentence, so the scoring bug does not overturn those observations.

## Reader contract

The renderer revalidates the frozen case, snapshot, financial review and separate
operating package before adding a concise **Review status and model scope** block.
Financial schedules and operating scenarios have distinct review states. Neither
constitutes approval of a valuation model or investment conclusion.

The block distinguishes fiscal revenue/operating-income sensitivities from a
reviewed fiscal-guidance-to-calendar-cash-flow/underwriting bridge. The latter is
still unavailable in the case-backed engine; valuation, per-share and funding
conclusions remain withheld. This clarifies disclosure, not financial closure.

The audit and rendering provenance bind the exact text, validated case state,
snapshot and package hashes. Missing/stale case state prevents an authored reader
from rendering as valid. A failed-run diagnostic may instead state that review
status cannot be established. Contradictory authored prose is retained for the
mandatory factual review; the generated block never overrides that review.

Engine identity advances to `research-v2-preview-8`. Historical previews use their
original versioned rendering behavior and do not acquire the new text or reusable
attestations. Historical reports and unknown usage remain untouched.

## Verification and remaining work

Offline checks cover independent financial/operating statuses, absent and changed
state, stale snapshots, exact provenance, contradictory prose blocking export,
reader preview, finalization continuation and provider-boundary compatibility.
A real saved preview-6 NVDA candidate was also rendered into a fresh temporary
preview with zero live calls; its historical binding remained valid.

## Small actual-writer check

The diagnostic rehearses the current engine offline with eight saved analysis
and claim-review responses as **fixtures**, not validated recovery checkpoints.
It stops at the current editor boundary, then dispatches a new Astra/high writer.
That reply is schema-validated and passed through the current engine's rendering,
case disclosure and provenance logic. A new Sol/high factual review receives that
exact reader. Old reader reviews, coverage dispositions and attestations are never
imported. The historical run's incomplete usage is retained separately; zero usage
in an offline rehearsal describes only fixture execution, not the original calls.

Bounds: at most two provider calls, 900 seconds overall (880 worker, 890 supervisor,
cleanup headroom), 600 seconds per call, and a shared **2,000,000-token best-effort**
admission/post-call allowance. The unchanged OpenAI destination uses the isolated
`/Users/luyaoma/.tradingagents/codex` runtime. No retries, fallback, evidence
acquisition, historical edits or automatic full research run are authorized.

The conservative input-byte reservation is not an expected token bill. Initial
planning includes an explicitly estimated second payload and its output reserve;
the actual second payload is persisted and admitted again after rendering. Unknown
usage or an exceeded bound stops dispatch. A fresh single-use capsule binds source
artifacts, code, provider/home, model settings and the exact initial payload; the
second payload is generated only by the bound current-engine capture algorithm.

Independent review found and fixed a rehearsal time-of-check/time-of-use issue:
the engine now reads private copies of frozen evidence/case bytes, not mutable
original paths. The plan also records each fixture's historical checkpoint identity
and input hash alongside the current payload hash. Mismatches are disclosed, not
silently treated as valid recovery: the historical prompts belong to an older
contract, and this experiment deliberately holds their output text fixed. The
reviewer accepted that distinction; no historical claim or reader attestation is
promoted to current acceptance. A second review also moved historical v1 control
rejection ahead of the single-use attempt marker, preserving read-only handling.

The saved `reader_candidate.md` is diagnostic-only. A completed factual response
is not coverage verification, financial-model acceptance or an accepted report.
Inspect the response and actual reader rather than treating process completion as
a quality pass. The experiment tests writer/rendering integration, not the quality
of a freshly regenerated analysis prefix or the reliability of semantic coverage.

Next: finish independent review and integration, then execute the bounded check.
Keep legacy coverage as default. Financial-case closure,
independent ecosystem evidence, HOOD generality, reader acceptance and release
approval remain separate deliverables.
