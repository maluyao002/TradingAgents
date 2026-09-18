# Deep Research V2: implementation and acceptance plan

Status: implementation advanced; milestone acceptance remains gated. Updated 2026-09-18.

See [implementation progress](DEEP-RESEARCH-PROGRESS.md) and the
[offline runner guide](DEEP-RESEARCH-USAGE.md) for what actually works today.
The milestone exit criteria below remain requirements, not completion claims.

Latest numerical checkpoint: three independently reviewed conditional NVDA scenarios
now compile through the deterministic engine into an English valuation development
memo with annual cash-flow bridges and mechanical sensitivities. A 600-second-capped
live diagnostic completed but returned `model=null`; report acceptance is not implied.
Exact market-row delivery, the complete equity/funding bridge, opening-date alignment,
and core-reader integration remain priorities. See the progress log for measured
usage, review caveats and the distinction between a calculable case and a final report.

The [system design](DEEP-RESEARCH-DESIGN.md) is authoritative for scope, contracts,
roles, source policy, budgets, and report behavior. This plan supersedes the earlier
weekly-centric roadmap: deep-report production is the core deliverable; trading,
tactical signals, position sizing, scheduling, and publication are external concerns.

## 1. Delivery strategy

Build an opt-in investment-research workflow alongside the legacy graph, sharing
only neutral backend, data, checkpoint, and telemetry utilities. Introduce a
standalone research CLI before any weekly adapter. Do not rename the legacy graph
and then require fake trade decisions or debate history from the new engine.

Use independently reviewable milestones. Evaluation infrastructure starts in M0;
each later milestone adds fixtures and measured outcomes. Preserve existing changes,
including the Chinese token-comparison TODO in [weekly reports](WEEKLY-REPORTS.md).
Do not modify current schedules, live configurations, published reports, or the
separate AMD task as part of core development.

Persist the stage contracts and architecture decisions before implementing the
orchestrator. Changes to accepted scope or data-purchase policy require a separate
decision, not an undocumented implementation shortcut.

## 2. Milestones and dependencies

### Approved execution sequence — English NVDA / Robinhood contrast

The user approved the following priority order after independent Astra/high review.
Use **NVDA and Robinhood (HOOD)** for the initial development/evaluation pair.
Robinhood replaces the proposed early TSM contrast; the broader company coverage
and release obligations below remain uncompleted. Current reader output is English.
Preserve existing Chinese compatibility and its token-comparison TODO, but defer
new Chinese generation, localization and language experiments for this campaign.

1. Freeze reference identities, evaluation questions and defect cases before tuning.
   The supplied English `NVDA_Equity_Research_R2a_2026-09-17.html` and
   `HOOD_Equity_Research_2026-09-18.html` are comparator artifacts, not factual gold.
   Separate report date, evidence cutoff and acquisition time; do not admit a
   future-dated report as primary evidence. Select a held-out window before using
   it, and do not claim human-reviewed labels until actual review occurs.
2. In parallel, bound reader cleanup: concise authored limitations in the reader,
   complete linked audit records, and final-artifact checks. No material caveat
   may disappear merely to satisfy a length target. Implement bounded review
   repairs with explicit dispositions and dependent-stage invalidation.
3. Build question-specific evidence delivery and investigation together. Distinguish
   locally undelivered, retrievable, conflicting, unavailable and judgment inputs.
   Valuation blockers must be able to request evidence. Test resolution of a
   missing passage/input, a changed conclusion and a genuinely unresolved gap.
4. Complete a narrow multi-period NVDA model through numerical reader tables and
   independently reproducible scenarios/sensitivities. Reported/derived anchors,
   forecast assumptions and model conventions remain distinct. Connect typed
   calculation references; a calculator alone is not a numerical report.
5. Challenge the same interfaces with source-backed HOOD: transaction activity and
   monetization, customer versus corporate balances, net interest, subscriptions,
   regulatory capital/funding and regulatory-event scenarios. Determine applicable
   equity-cash-flow/earnings methods rather than mechanically applying industrial
   FCFF or subtracting customer liabilities as corporate debt. These methods need
   explicit eligibility and independently checked reference calculations.
6. Expand coverage and run matched workflow and acquisition comparisons separately,
   then complete longitudinal updates and release gates. Answerable reference
   cases must produce substantive model-backed output; truthful diagnostics alone
   cannot demonstrate product competence. Live trials remain bounded and explicit;
   no paid subscriptions, publication or scheduling changes are included.

This sequence changes execution priorities, not the distinction between implemented
utilities, integrated capabilities and accepted milestones. Do not declare M0-M6
complete from the NVDA engineering pilot or reference-file registration alone.

### M0 — Core boundary, contracts, and baseline

- Add `tradingagents.research` request/result types and injected service interfaces.
  Separate research assessment from legacy transaction/rating schemas.
- Add versioned source, claim, question, expectations, model, review, and dossier
  contracts with fixture serialization and schema compatibility tests.
- Add the standalone CLI's configuration validation and dry run; no live inference
  or provider fetch occurs during dry run. Keep legacy config behavior unchanged.
- Define cutoff, source identity, versioned resume identity, stage persistence,
  publication-independent output contracts, and resource-accounting boundaries.
- Establish saved baseline reports, a frozen-evidence replay harness, and a strong
  single-agent comparator with the same evidence/model eligibility/budget ceilings.
- Define expert-review rubrics and seed known defects: billion/亿 confusion,
  cash-flow period mismatch, unsupported consensus claims, and source-citation
  mismatch. Do not use the Claude report as factual ground truth.

Exit: offline contract and boundary tests pass; existing regression tests pass;
legacy entrypoints behave unchanged; the core cannot depend on weekly/publication
or trading modules.

### M1 — Evidence, financial normalization, and expectations

Depends on M0.

- Implement cached SEC/IR discovery, source-location retention, safe HTTP access,
  publication-aware selection, fair-access throttling, and explicit fetch status.
- Add structured news/events and fix timestamp loss, limit/filter order, duplicate
  syndication, entity relevance, and unsupported historical-coverage claims.
- Normalize statements and supporting disclosures for the five pilot companies,
  covering eight quarters/five years where available, with explicit missingness.
- Add independent-source provenance and the expectations comparison contract.
  Populate guidance and accessible estimates; record unavailable consensus and
  revisions without inventing or silently purchasing them.
- Add source-quality diagnostics for must-capture events, document availability,
  period/basis coverage, conflicting values, and unresolved data gaps.

Exit: source/financial gold fixtures reconcile; every retained material fact has a
traceable source and unit/period; unavailable essential evidence fails visibly.

### M2 — Research responsibilities and adaptive investigation

Depends on M1.

- Implement planning, business/industry, accounting/cash quality, expectations, and
  management/capital-allocation work with structured findings and question IDs.
- Include guidance track record, incentives, acquisitions, reinvestment, dilution,
  and customer/competitor evidence according to materiality.
- Implement hypothesis alternatives and rank unresolved questions by investment
  consequence and resolvability. Add bounded targeted follow-up and early stopping.
- Add shared industry-assumption versions and explicit cross-company disagreement
  records; do not force unrelated company analyses into a common conclusion.
- Implement stage-level usage tracking, checkpoints, cancellation, and resource
  reservation; all retries count against the run allowance.

Exit: offline cases demonstrate a resolved gap, a genuinely unresolved gap, and an
initial thesis changed by contrary evidence. No mandatory rhetorical debate, trade
proposal, or portfolio context is required.

### M3 — Financial models and deterministic valuation

Depends on M1; integrates with M2. Model-library work can proceed alongside M2
once M0 contracts are fixed.

- Build company-specific financial schedules with coherent income, balance-sheet,
  and cash-flow relationships and explicit economic drivers.
- Implement fabless/product, foundry/manufacturing, recovery, and mixed-business
  templates for the pilot. Support justified horizons, DCF, applicable comparables,
  and sum-of-the-parts without mechanical method averaging.
- Add scenarios, one-driver reverse valuation, sensitivity analysis, and distinct
  current-value/12-month/three-year outputs with explicit horizon bridges.
- Test customer-demand, capacity, reinvestment, cash/funding, share/dilution, and
  cross-company consistency constraints. Capture both hard arithmetic failures
  and economically questionable assumptions for review.
- Require assumption provenance or explicit rationale. Unsupported terminal states,
  probabilities, and decisive missing inputs must not produce precise targets.

Exit: independent reference calculations pass, TSM currency/ADR and INTC recovery
cases work, and bad assumptions are surfaced rather than hidden by correct math.

### M4 — Independent review and reader-report production

Depends on M2 and M3.

- Implement a blinded first-pass challenger, then expose the lead model/thesis and
  reconcile material disagreements without forcing agreement.
- Implement separate claim verification: source entailment, dates, definitions,
  units, numerical interpretation, and unsupported causal language. Preserve review
  coverage and unresolved findings, not an unjustified blanket verified label.
- Add the editor/synthesizer (English-first in the current campaign, preserving
  existing Chinese compatibility), structured-value rendering, readable source
  footnotes, local audit report, and model/source appendices.
- Implement data/numerical/research/editorial assessment and report-level admission
  rules. Critical unresolved issues produce Needs review / Unrated with dependent
  targets withheld; useful supported sections remain available.
- Ensure the editor cannot erase limitations, invent assumptions, or alter figures.
  Source/model revisions invalidate affected report/review stages.

Exit: a complete deep report can be produced independently of any weekly runner,
trader, portfolio manager, publisher, or chat context; fixtures catch convincing
but unsupported prose and preserve material caveats.

### M5 — Dossiers, research updates, and forecast evaluation

Depends on M4.

- Add immutable dossier history, promotion rules, source watermarks, and dependency-
  aware reuse. Fresh coverage and caller-requested updates share the same engine.
- Add change attribution for evidence, expectations, assumptions, valuation, and
  confidence. Show a change summary plus full refreshed thesis on update requests.
- Test quiet updates, earnings/restatement updates, failed source acquisition,
  stale/incompatible priors, and historical cutoffs. No timer or scheduler is added.
- Store forecast vintages and compare subsequently released actuals on a comparable
  basis. Attribute forecast errors; do not automatically rewrite prompts or models
  from short-term price performance or a small sample of outcomes.

Exit: interrupted/degraded updates cannot replace validated history; an unchanged
input can reuse appropriate stages without representing stale findings as fresh.

### M6 — Comparative evaluation and core release

Depends on M0-M5; build and run offline evaluations continuously before this stage.

- Complete the five-company benchmark with two windows each: one earnings/material-
  event window and one quieter window. Human-review the event list and reference facts.
- Compare legacy, V2, and the single-agent baseline on matched evidence and compatible
  research instructions; separate workflow comparison from end-to-end data acquisition.
  Record actual usage as well as shared ceilings; equal ceilings are not equal spend.
- Conduct blind report review, numerical/claim audits, data-coverage tests, and
  operational recovery tests. Reader preference alone cannot establish acceptance.
- Run the language benchmark described below. Publish measured quality/cost tradeoffs,
  limitations, and provider gaps rather than assumed multi-agent superiority.
- Run user-initiated live pilots only after offline acceptance. No implementation
  task automatically consumes the full live benchmark budget.

Exit: core acceptance criteria pass and the user reviews the five-company sample.
Release as opt-in `investment_research_v2`; retain explicit legacy selection and
rollback. Default adoption requires a separate approved application configuration
change; a successful core release does not silently switch live weekly work.

### Separate follow-on: application adapters

Not required to accept the deep research engine. After core acceptance, an adapter
can connect the existing weekly runner or publisher to `ResearchRequest` and
`ResearchResult`. Reuse the reader artifact without additional LLM rewriting.

Version new manifests and explicitly record selected artifact kind/hash. Preserve
old manifests and their full-report behavior; never rewrite historical batches
in place. A compatibility `complete_report.md` alias may point to audit content
at this boundary only. Publication retry must not rerun research.

Scheduling, notifications, batch frequency, and any 13-week refresh policy belong
to the external application. Verify document import/readback and unattended batch
behavior before changing that application's live configuration. Existing AMD task
associations and unrelated automations remain unchanged.

## 3. Acceptance matrix

| Area | Required cases and outcomes |
| --- | --- |
| Core separation | Standalone offline/live entrypoint needs no portfolio, trading state, scheduler, publisher, or prior chat; imports enforce the boundary |
| Financial correctness | 亿/billion; currency/ADR; fiscal periods; YTD-derived quarters; amendments; SBC and dilution; debt/leases; splits; non-GAAP bridges; negative earnings; missing data never zero |
| Model integrity | Reference DCF/SOTP/return calculations; consistent statements; terminal sensitivity; matched comparables; no future-share denominator shortcut or double-counted buybacks |
| Economic reasoning | Capacity/demand/cash constraints; reinvestment consistency; incompatible shared assumptions; supply-chain double counting; risks connected to financial effects |
| Expectations | Guidance versus consensus versus own forecast; matching vintages/bases; unavailable dispersion; reverse DCF not mislabeled as actual investor beliefs |
| News and sources | Timestamp retention; local/UTC cutoff; duplicate origin; irrelevant mentions; stale/partial feeds; full text unavailable; successful acquisition distinguished from no events |
| Independent review | Blinded counter-case; actual thesis reversal; unresolved disagreement; genuine citation that does not support the claim; editorial removal of a caveat detected |
| Historical integrity | Future filings, later restatements, live-only estimates, and later dossiers excluded unless eligible snapshots exist |
| Operations/security | Interruption at every stage; budget reservation/exhaustion/overshoot; quota/auth failure; corrupt state; concurrent runs; hostile source instructions and unsafe URLs |
| Longitudinal learning | Quiet update; event-driven deep refresh; failed watermark advance prohibited; immutable forecast vintage; actuals/basis reconciliation |

Core release requires:

- No unresolved critical numerical, provenance, security, or look-ahead failures in
  reports labeled accepted. A reviewer cannot override a deterministic hard failure.
- Every material numerical claim resolves to a validated fact, an explicit assumption,
  or a reproducible calculation. Semantic support of decisive claims is reviewed.
- All designated must-capture events in the benchmark are found, at least 90% recall
  across the broader labeled material-event set, and at least 90% precision among
  items presented as material. Report coverage outside the fixture set as unproven.
- Expert review of assumptions, accounting, and the central thesis finds no unresolved
  critical defects. Blind review prefers V2 to legacy for reasoning and readability
  in at least eight of ten paired cases without worse factual reliability.
- Report V2 versus the strong single-agent comparator separately. Do not claim
  multi-agent superiority without evidence; revisit unnecessary orchestration if
  it provides no demonstrated advantage.
- All five companies either produce an accepted report or truthful recoverable
  diagnostics. Essential gaps cannot be hidden to pass the completion metric.
- Two consecutive user-initiated standalone pilot batches complete with recovery
  checks and user review of the sample. No schedule or cloud publication is required.

These are release thresholds, not statistical proof of broad market coverage or
future forecast accuracy. Avoid tailoring prompts solely to known benchmark answers;
retain an additional unseen evaluation window for post-release regression checks.

## 4. Language, cost, and provider evaluation

Keep the default English analysis with direct Chinese synthesis until matched
evidence supports a change. Compare three variants: end-to-end Chinese, English
analysis with direct Chinese synthesis, and an English complete report followed
by translation. Match cutoff, evidence, models, mandate, and output requirements.

Start with three trials per variant on frozen NVDA evidence; extend to other firms
before generalizing. Record stage/model input, output, reasoning, cached input,
total tokens, latency, retrieval yield, unresolved questions, and report quality.
Report translation and verification costs explicitly; do not count reasoning or
cached tokens twice. All live trials are explicit user-initiated work.

Prioritize data evaluation in this order: filings/footnotes/segments, transcript
Q&A and guidance history, consistent consensus/revisions, independent ecosystem
evidence, and broader news. Public feeds are judged by coverage, correctness,
freshness, and point-in-time usability rather than article count.

Maintain a provider-gap register identifying missing events/metrics, their thesis
impact, attempted public alternatives, and whether a paid source could resolve
them. A subsequent paid-data proposal must address demonstrated gaps, licensing,
historical coverage, access reliability, and cost. No subscription, vendor switch,
expert outreach, or private-data access is authorized by this plan.
