# Deep Research V2 implementation log

Branch: `codex/deep-research-v2`, based on `ebc3383` (the current weekly-report
checkout). Live schedules, provider settings, historical reports, and user tasks
are unchanged. Local commits are used as reviewable checkpoints; no remote push
or pull request is implicit.

## English NVDA / Robinhood campaign — September 17, 2026

The approved sequence now uses Robinhood as the early contrast and English-only
new outputs. Existing Chinese compatibility and its comparison TODO are preserved.
The new behavior is opt-in `quality_revision: "evidence-led"`; historical
`foundation` request identities and recovery payloads remain compatible.

| Approved step | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| 1: Freeze evaluation cases | Both supplied English HTMLs registered by basename/hash; decisive questions, required areas and defect-case inventory saved | Human-reviewed primary-source labels, held-out windows and matched model runs; comparator files are not gold |
| 2: Reader and repair | Separate lossless audit, footnotes, typed per-limitation dispositions, exact rendered-text/hash verification, one budgeted repair, warning/critical withholding after repair | Real-report readability and semantic verification evaluation; literal excerpts do not prove entailment |
| 3: Targeted investigation | Bounded question-specific full-text passages; valuation blockers included; cycle/provenance ledger; explicit evidence-linked closure; tests for revised analysis and still-open gaps | Autonomous external discovery/follow-up provider, semantic classification of unstructured gaps and selective dependent-role reruns |
| 4: NVDA numerical slice | 52 original facts preserved plus 29 strict source-bound anchors; common-period TTM/WC/net-debt/share reconciliation; typed model/result references through exact verified synthetic reader | Live source-calibrated report, company economic review, scenarios/sensitivities and horizon bridges in the reader; no accepted target |
| 5: HOOD contrast | Independently tested common-equity cash-flow DCF, source binding, attribution/scale/timing checks, matching strict wire codec, no customer-debt/FCFF mixing | Downloaded primary-source packet and normalized broker schedules; direct IR and SEC source checks returned HTTP 403 |
| 6: Broader evaluation/release | Existing evaluation contracts retained; engineering regressions expanded | Matched workflow versus acquisition experiments, broader companies/windows, human/blind review, longitudinal and release gates |

The source-bound NVDA enrichment is a narrow development adapter, not eight-quarter/
five-year coverage. It produces 81 total facts with unchanged source bytes and
retained original fact objects. Working capital is an explicitly qualified
aggregate-row proxy; cash-only net debt excludes securities/leases and does not
certify cash availability. An optional quarterly weighted-average diluted-share
proxy remains a duration fact, must be the latest eligible quarter and is visibly
labeled in per-share outputs. Default point-in-time binding is not relaxed.

Independent review caught and fixed material-caveat disappearance, missing terminal
review dispositions, audit/verification ID disagreement, segmented common-income
binding, and a stale-quarter share proxy. The numeric catalogue now includes method,
denominator basis, input/result hashes, opening inputs, rates, and discount timing,
separate from reported facts. Materiality and forecasts still need independent
financial review. A successful illustrative calculation is not production acceptance.

Latest coordinator full non-live suite: **1,806 passed, 2 skipped, 18 existing
warnings, 65 subtests passed**. Separate final attribution/materiality/recovery tests
passed 18 cases. Changed-file Ruff and `git diff --check` passed. A broader research
test-file Ruff invocation also surfaced two pre-existing import-order warnings in
untouched diagnostic/stage-instruction tests; they were not silently rewritten.
The actual original recovery source and diagnostic still validate with source
identity `8a9c4e24312e85ef44242f5df98605f307a939b7043baaefe6e033846a5ad40a`.

Commits in this campaign:

- `698aa1b`: English-first NVDA/Robinhood design and sequence.
- `ef91aa0`: immutable comparator reference registration.
- `70fc87e`: bounded broker equity-cash-flow calculator and independent math tests.
- `1586128`: local targeted passages, pure reader/audit rendering, investigation ledger.
- `519c6f5`: frozen NVDA anchor normalization without rewriting source evidence.
- `34ad11b`: opt-in engine integration, strict provider schemas and exact-report gates.

Sol/high workers owned reader, retrieval, investigation and financial components;
Terra/medium owned reference registration. Independent Sol/high review led to the
fixes above. Worker token totals are unavailable and are not included in report
runtime usage. No scheduling, trading, publishing, default application change,
remote push or new Chinese run occurred.

The bounded English-only live NVDA run is under
`reports/RESEARCH_EN_20260917/nvda_run_1`, using the new 81-fact snapshot and a
1.5-million-token/90-minute ceiling, one investigation cycle and a finalization
reserve. A first launch was denied by automatic privacy review. Read-only provenance
checks established that the payload consists only of public NVIDIA IR documents,
derived facts and the public-company research mandate—not reference HTMLs, user
holdings, credentials or chat history. The same launch was then approved; no
restriction was bypassed. Completion and measured usage are pending.

Robinhood needs saved Q2 2026 10-Q / FY2025 10-K / earnings-release files or another
authorized primary-source delivery path. An asynchronous request was sent to the
user while unaffected work continued. The September 18 comparison report remains
an editorial comparator, never substitute primary evidence for the September 17 case.

## Baseline (2026-09-17)

- Luna/medium read-only scout: 1,325 existing tests passed, two failed, one skipped,
  one deselected using the offline command below. The two failures are existing
  Unix-socket creation probe cases in the restricted sandbox (`TransportClosed`
  rather than expected `ProbeError`), not new research behavior. Do not hide them.
- Optional Bedrock dependency is unavailable; its existing test skips.
- Process-lifecycle suites are excluded from the initial fast baseline, not from
  eventual lifecycle validation. They spawn fake local processes, not live models.

```sh
.venv/bin/pytest -q tests --ignore=tests/test_codex_transport.py \
  --ignore=tests/test_weekly_process_cleanup.py -m 'not integration'
```

## Historical foundation checkpoint: tested offline preview

| Area | Implemented and exercised offline | Still required for milestone acceptance |
| --- | --- | --- |
| M0 | Typed/versioned contracts, independent package, dry-run/replay CLI, frozen input hashes, atomic checkpoints, budget reservations, scoring rubric | Matched legacy/V2/strong-single-agent experiment runners and reviewed reference corpus |
| M1 | Pinned-public-IP HTTPS, SEC/IR discovery, bounded fetching/cache, accession-aware financial normalization, news date/relevance/dedup rules, quarter derivation, explicit SEC baseline collector | Full eight-quarter/five-year reconciliation for five companies, transcripts/guidance history, broader material-event coverage and independent-source evidence |
| M2 | Planner and four specialists, blinded challenger, bounded follow-ups and thesis-revision fixtures, durable stage usage, safe resume, bounded provenance-preserving excerpts, explicit Codex adapter and POSIX hard-deadline supervisor | Live recovery validation, evaluated retrieval/context quality, cross-company assumption reconciliation and realistic coverage fixtures |
| M3 | FCFF/DCF, SOTP, comparables, sensitivity, reverse valuation, horizon bridges, dated discounting, integrated one-period statements and sector driver mechanics | Source-calibrated multi-period company models, full ADR/FX/corporate-action integration, independent financial review and economic constraints on realistic cases |
| M4 | Structured reports, Chinese financial-value rendering, separate claim/final-review hooks, limitations, hashed reader/audit exports | Measured semantic entailment quality, complete editorial numerical admission, report preference tests and production acceptance rules |
| M5 | Immutable dossiers, accepted-history protection, eligible priors, change metadata, real forecast vintage timing, comparable-actual scoring | Full evidence/expectation/assumption/value change attribution, dependency-aware cross-run reuse and calibrated forecast integration |
| M6 | Offline score contracts, synthetic defect fixtures, regression/recovery tests | Human-reviewed gold labels, matched comparison/language trials and explicitly authorized live pilots |

No whole milestone is declared accepted just because its mechanical tests pass.
The standalone replay path works. The live Codex entrypoint and supervisor are
implemented and tested with fake providers/workers, but have not been live-validated.
Live execution requires a separate `--allow-live` flag, an explicit existing isolated
runtime, and frozen evidence or SEC identification plus instrument identity.
Every current engine report is `Needs review / Unrated`; production acceptance is
deliberately disabled. See [usage and limitations](DEEP-RESEARCH-USAGE.md).

## Delegation

- Sol/high: independent design review.
- Luna/medium: baseline and neutral-utility scouting.
- Terra/medium: bounded CLI/contracts tests, then evidence normalization fixtures.
- Sol/high: deterministic valuation implementation and accounting edge-case tests.
- Coordinator: shared contracts, boundaries, integration, review, tests and commits.

Each worker owns disjoint paths and does not commit or initiate live research.
Delegation telemetry is not an efficiency benchmark; aggregate subagent token cost
is unavailable here.

## Review resolutions

Independent Sol/high review identified real contract gaps before orchestration:
token admission did not reserve concurrent calls; source hashes and publication
cutoffs were not authoritative; ADR identity was underspecified; derived-fact
cycles were possible; accepted artifact manifests could be empty; replay hashing
could reread changed files; storage lacked an injected protocol.

The follow-up hardening adds atomic reservations, source-text hash checks,
cutoff/reference validation, instrument identity, raw-times-scale semantics and
duration metadata, DAG validation, accepted artifact requirements, bounded read-
once input helpers, and a storage interface. Semantic verification and historical
source availability still require M1/M4 implementation and external acceptance.

Subsequent independent review led to tested fixes for:

- Weak completed-result manifests and mismatched ticker/cutoff on resume.
- Follow-up evidence bypassing baseline checks or replacing immutable records.
- Empty/blank reader sections incorrectly appearing completed.
- Unsupported valuations admitted by arbitrary rationale text.
- Discount exponents unrelated to forecast dates and quarterly terminal flows
  incorrectly capitalized as annual perpetuities.
- Edited news/IR content retrieved after a historical cutoff, cache raw/text
  mismatch, and cross-host redirects falsely receiving SEC provenance.
- Context-dependent Decimal calculations and levered/unlevered tax-shield errors.
- Backdated forecast vintages and later-created dossiers leaking into historical work.

## Verification

At the integrated offline checkpoint:

```sh
.venv/bin/pytest -q tests/test_research_*.py
# 297 research tests (all included in the passing full suite below)
.venv/bin/ruff check tradingagents/research cli/research.py tests/test_research_*.py
# All checks passed
git diff --check
# Clean
.venv/bin/pytest -q tests -m 'not integration'
# 1,603 passed, 1 skipped, 1 deselected; 65 subtests passed
```

The complete offline suite ran with permission to create local test sockets and
fake child processes. The two baseline sandbox failures passed in that environment;
the subprocess/socket subset separately passed 55 tests. The remaining skip is the
pre-existing absent optional Bedrock dependency; the deselected test is live
integration. Existing model-catalog warnings remain unchanged. No live research,
data purchases, publication, or schedule changes occurred.

The new supervisor also deliberately fails before launch when process inspection is
denied by a sandbox. Its process tests therefore require the same approved local-
process test environment. This is not bypassed or silently converted to success.

Additional reviewed runtime fixes preserve unsettled dispatch across interruptions
after provider return, prevent alarms from interrupting threaded transport cleanup,
subtract verified checkpoint elapsed time on resume, validate artifact hashes in
the parent, and attempt cleanup even when a later process inspection fails. Source
excerpt limits and financial bounds have explicit regression coverage. Official
OpenAI documentation was used to verify the reused adapter's structured-output and
usage boundary; the implementation does not invent a hard output-token cap.

## Commit checkpoints

- `a2b9135`: agreed design, delivery plan and baseline.
- `4116214`: M0 contracts, dry-run CLI, recovery primitives.
- `06b2c02`: evidence identity, budget reservations, replay integrity.
- `ca3ac32`: bounded public SEC/IR source access.
- `8726b6c`: deterministic valuation and horizon calculations.
- `c073ecb`: financial/news normalization and explicit SEC baseline collection.
- `3b4464f`: dossier history, prior eligibility and honest forecast vintages.
- `874e90f`: mutable-lookahead and source-provenance review fixes.
- `5c71004`: integrated statement mechanics and dated discount timing.
- `2849838`: recoverable offline orchestration and reviewed reader exports.
- `139f013`: usage documentation and explicit remaining acceptance gates.
- `1f07a7a`: bounded source context, quantitative admission and durable usage settlement.
- `968a3f3`: explicit live Codex worker, per-call deadline and parent supervision.

Later integration/documentation commits are available in the branch log. All commits
are local; no push, merge, deletion of old branches, or change to other user work
is included in this implementation task.

## Next decisions and work

### September 17 bilingual NVDA pilot

The user authorized one live NVDA pilot with English and Chinese final reports.
Commit `01f06e2` adds paired finalization using one shared research pass, a faithful
translation, separate verification, one budget/usage ledger and hashed outputs.
It also adds explicitly curated pilot acquisition/table helpers; these are not
automatic production discovery or calibrated financial models.

Five public NVIDIA sources were acquired and frozen at 2026-09-17 15:21:06 UTC,
with 52 source-table-bound financial facts. The paired-export regression subset
passed 69 tests; the research suite passed 309 tests; the full offline suite passed
1,615 tests and 65 subtests (2 skips, 18 existing warnings).

The first live attempt stopped at the planner provider boundary after 4.64 seconds
with `CodexInferenceError`, before a research stage completed. No substantive final
reports were produced. The provider returned no token counters; usage is unknown,
not zero. Its artifacts/checkpoint remain under `reports/NVDA_V2_20260917/run` and
must not be reset to conceal unsettled dispatch. The comparison in the parent
folder is explicitly interim. A separate bounded diagnostic retry was requested
from the user, not automatically launched.

Inspection found Pydantic/domain schemas do not directly meet the strict provider
wire requirements (all object fields required, no open dictionaries). The initial
error alone does not prove this was its cause. The schema adapter now uses closed,
required-field wire contracts and typed valuation input; unique key/value arrays
round-trip into the unchanged domain maps. Unknown schemas fail before adapter
startup; decoding failures preserve returned usage. Wire identity is versioned and
field-drift guards cover the financial dataclasses. Safe schema-error classification
does not expose upstream prose or authorize retries. Final offline validation:
**1,639 passed, 2 skipped, 18 existing warnings, 65 subtests passed**. The live
schema repair was then exercised in the explicitly authorized one-call diagnostic
retry described below, but did not produce a completed research response.

The diagnostic retry passed all model preflights and isolation settings, then
streamed output until **287.74 seconds**, when the adapter raised
`Codex turn exceeded the notification safety limit`. The exact observed trigger
was the local `_MAX_TURN_EVENTS = 10_000` cap, which counts text-delta notifications
alongside lifecycle/control events. It was not a reported subscription quota error
or the 300-second deadline. The desktop account had 57% weekly allowance remaining
at the check; this is not asserted to identify the separate runtime account.

Both attempts still lack returned token counters and remain usage-incomplete.
No further diagnostic call was launched. The original five-second error has no
retained precise upstream payload, so the schema defect is not retroactively
declared its proven cause. Final bilingual reports remain ungenerated.

The scoped diagnostic helper retains redacted protocol metadata and permits one
inference turn under parent supervision. It also preflights configured models.
After reviewing the diagnostic, token-delta log writes were removed to avoid
per-token disk overhead in future diagnostics; this change was not exercised by
a second live call. A completed validated planner reply, if any, is explicitly
separate from diagnostic logs. Next: redesign the stream/control-event bounds
while retaining deadline, payload-size and tool-isolation protections, then
validate before returning to bilingual generation. At that point the production
cap was unchanged.

The next user-authorized repair separates the 10,000 control-event limit from
250,000 validated nonempty agent-message deltas, bounded additionally by 4,000,000
cumulative streamed characters. Empty chunks, advisory messages and retired or
unrelated traffic still consume the control budget. Deltas require a started turn
and matching uncompleted agent-message item; completed item types cannot change.
The original absolute deadline, completed-output cap, transport message-byte
limit and isolation checks remain in force. Stream fragments are not retained.
Regression coverage exercises more than 10,000 legitimate deltas, all count/size
boundaries, Unicode, empty/malformed/out-of-order streams, usage retention and
interrupt/unsubscribe/invalidation on cap failure. One further diagnostic is
authorized under unchanged 300-second call / 360-second parent bounds, using the
same frozen evidence and planner configuration in a separate output directory.
Independent Terra/medium review found no remaining issue after checking an initial
false-positive empty-delta finding against the code and added regressions. Final
offline validation: **1,676 passed, 2 skipped, 18 existing warnings, 65 subtests**.

The authorized second diagnostic succeeded after **258.30 seconds** with the same
Sol/high planner, frozen evidence and 300-second call / 360-second parent bounds.
It received **10,010 agent-message delta events**, demonstrating why a shared
10,000-event control/stream cap was insufficient. The separate validated planner
reply and redacted diagnostic are under `reports/NVDA_V2_20260917/diagnostic_retry_2`.
Returned usage is complete: **23,154 input + 14,118 output = 37,272 total tokens**,
including 4,076 reasoning-output tokens (a subset of output, not added again),
with zero cached-input tokens reported. Exactly one planner inference was made;
no full-engine or bilingual finalization calls followed. The first two failed
attempts still have unknown usage and unchanged artifacts. This is successful live
planner/wire/stream validation, not full-pipeline or research-quality acceptance.
Next: an authorized fresh full-engine run, keeping the failed run's unsettled
checkpoint intact; this diagnostic artifact is not an engine resume checkpoint.

Preflight for the user-authorized full bilingual run found that the successful
diagnostic was **wire/schema-valid, not engine-semantic-valid**: its findings cite
generated `C1`-style claim IDs instead of eligible evidence IDs. It also mixes
Chinese into the English intermediate analysis. No additional inference was made
to discover this. The original diagnostic is preserved, not relabeled or patched.
Prompts now explicitly distinguish evidence references from generated record IDs,
namespace new IDs by stage (avoiding independent-stage collisions), preserve
question links and use only the current payload language. The planner is scoped
as a concise plan rather than duplicating a bilingual final report. Diagnostics
now also enforce evidence eligibility, planner question count, finding/question
links and unique claim IDs while preserving returned usage on semantic failure.
Full generation will use `request_final_1.json` / `run_final_1`, with unchanged
frozen evidence, model settings and budget. Prior failures remain untouched.

The prompt/diagnostic repair passed **1,682 tests, 2 skips, 18 existing warnings
and 65 subtests** and was committed as `0e9b1b0`. Independent Terra/medium review
found no remaining prompt-contract blocker. The authorized full run then completed
and checkpointed planner, independent challenge, business, accounting, expectations
and management. It stopped at the valuation provider boundary after **1,148.27
seconds**, with `CodexStructuredOutputError` (provider schema rejection), not the
streaming-event guard or a timeout. No English or Chinese final was generated.

Known spend for those six completed calls is **142,599 input + 54,468 output =
197,067 tokens**, including 17,164 reasoning-output tokens within output. The
valuation call returned no counters; aggregate usage is incomplete, not zero for
that call. The unsettled dispatch flag and all valid stages are preserved under
`reports/NVDA_V2_20260917/run_final_1`. No automatic retry/resume was launched.

Offline inspection confirms the valuation schema differs from the successfully
used analysis schema: it includes typed dates and Decimal number/string unions
with Pydantic-generated lookahead patterns. Those are compatibility candidates,
not a proven exact cause: the retained safe error class does not identify the
rejected keyword/path. Next is a scoped valuation-schema diagnostic and repair,
plus explicit recovery that retains the six valid stages and the failed call's
unknown usage. Do not clear `dispatched`, invent zero usage, or silently rerun the
entire research pass. Live valuation validation and continuation require direction.

The user subsequently authorized valuation-schema repair, bounded validation and
explicit recovery of the six-stage prefix. Wire v2 represents financial Decimals
as exact strings, avoiding Pydantic's generated lookahead regex while keeping
finite-value/domain validation and date validation. The old rejection did not
retain a precise keyword path, so regex compatibility remains a hypothesis until
the repaired schema is exercised. Nonportable regex patterns now fail offline.
The diagnostic supports a single valuation call using the original six analyses;
it checks source identity/artifact/stage hashes before provider startup and binds
a successful reply to its exact payload, source hashes and wire version. It does
not edit source checkpoints or settle prior unknown usage. Initial focused tests:
52 passed; real source-prefix verification passed without live calls.

The repaired schema diagnostic then succeeded in **59.31 seconds**, returning
**61,532 input + 3,091 output = 64,623 tokens**, including 2,033 reasoning-output
tokens within output. It returned a valid `model=null` proposal with explicit
missing inputs rather than an unsupported target; this validates the wire path,
not a populated valuation model. The artifact/provenance are retained under
`valuation_diagnostic_1`. The six source stages plus the diagnostic have **261,690
known tokens**; the original rejected valuation call remains unknown. Before the
probe, 1,699 offline tests and 65 subtests passed (2 skips, 18 existing warnings).

Explicit recovery is implemented with source/payload/provenance validation and a
new output directory. It imports the six stages and validated valuation reply,
seeds all **261,690 known tokens and 1,207.58 elapsed seconds** before the first
admission, and never changes the original incomplete-usage record. Imported calls
are not dispatched or charged again; any new unmeasured call stops continuation.
The accounting regression tests cover early stops and diagnostic replay, along
with tampering, symlinks/FIFOs, explicit authorization and bilingual outputs.
Integration validation: **1,712 passed, 2 skipped, 18 existing warnings and 65
subtests**. The next live stage is challenge reconciliation, followed by claim
verification, English editing/verification and Chinese translation/verification.

The authorized recovery completed successfully under `run_recovered_1`, producing
both English and Chinese research previews with `completed_needs_review`. All six
new calls returned complete counters; aggregate usage remains incomplete because
of the original valuation rejection. Known spend is **648,933 input + 100,189
output = 749,122 tokens**: 197,067 historical + 64,623 diagnostic + 487,432 new.
Reasoning output (35,133) is included in output, not added again. Earlier diagnostic
attempts and coding/reviewer agents are outside this ledger. Chinese translation
and verification add 185,102 tokens (92,213 + 92,889); this is not a controlled
Chinese-versus-English research comparison. Retained elapsed time is 2,320.45s.

Final checks passed: 11 artifact hashes, 17 unchanged source-run hashes, eight
sections per language, and identical ordering of all 46 fact insertions and
section evidence IDs. Both verifiers returned no critical findings; the combined
assessment has eight warnings and eight informational findings. Independent
Terra/medium review found a more coherent question-led narrative than legacy,
while Claude remains more complete as a valuation/scenario memo. Different input
scopes prevent a controlled quality claim. No price target or production acceptance
is supported by this issuer-only, truncated evidence packet.

Next quality work exposed by the actual rendered artifacts:

- Separate concise decision-relevant reader limitations from the full audit log:
  each current reader appends 175 bullets, overwhelming the main narrative.
- Fully localize the Chinese reader wrapper and audit presentation; its narrative
  is translated but raw gaps/status boilerplate still include English.
- Add a compact synthesis and ranked evidence/monitoring agenda, improve pinpoint
  citation scope, and remove stale placeholder/process prose from the reader.
- Supply complete relevant filing context, independent evidence and reconciled
  valuation inputs before attempting an accepted valuation-bearing report.

The saved pilot artifacts remain unchanged. The detailed three-way comparison and
usage breakdown are in `reports/NVDA_V2_20260917/comparison.md` (local run output).

Baseline review also illustrates why the Claude report is not a gold label and
why reviewers need checks: an initial criticism of its 18.6% EPS premium mistakenly
used Q2 instead of H1; H1 EPS $4.85/$4.09 supports 18.6%. That criticism was withdrawn.
Human source/model review and matched evaluations remain outstanding.

Next extend validation beyond this narrow pilot, and use verified reference
evidence to calibrate company models, coverage and comparison runners.
These are additional acceptance/implementation tasks, not silently completed features.
Before additional live pilots, obtain explicit authorization and agree the reference window; the existing NVDA
report and Claude comparison are candidate baselines, not gold factual labels.
Human source/model review and matched evaluations remain mandatory before acceptance.
The separate Chinese token-comparison TODO remains in `WEEKLY-REPORTS.md`.

Known operational boundaries: Codex output-token limits are advisory (actual spend
and overshoot are recorded); total wall supervision has bounded cleanup overhead;
resume cannot recover elapsed time that was never checkpointed before a hard crash;
very rapid unobserved double-fork descendants need stronger OS containment. These
limitations must be evaluated before unattended production use.
