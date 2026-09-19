# Deep Research V2: system design

Status: agreed target design; implementation and acceptance remain partial. Updated 2026-09-18.

This is the canonical design for the next research engine. The companion
[implementation plan](DEEP-RESEARCH-PLAN.md) defines delivery and acceptance.
Existing runtime behavior remains described by the current integration and
weekly-report documentation. This document describes the target, not a completion
claim; see the [implementation log](DEEP-RESEARCH-PROGRESS.md) for verified scope.
Start with the [current status and roadmap](DEEP-RESEARCH-STATUS.md) for priorities,
deliverables and engineering-versus-user responsibilities.

## 1. Objective and boundaries

Produce a deep company research report whose central argument, financial model,
evidence, and uncertainty withstand independent scrutiny. The product is a
research report, not a transaction proposal or a transcript of agent debate.

The report must explain the business, the central investment disagreement,
available market expectations, our differentiated assessment, its financial
consequences, and the evidence that would change the conclusion. A differentiated
assessment may agree with consensus; disagreement is never manufactured.

### Core engine owns

- Research mandate, company identity, explicit evidence cutoff, and research plan.
- Public-source acquisition, normalized financial history, and event coverage.
- Business, accounting, expectations, management, and industry analysis.
- Financial forecasts, reproducible valuation, scenarios, and sensitivity analysis.
- Independent challenge, claim verification, and research-quality assessment.
- An English reader report for the current campaign, a complete audit record, and
  versioned company dossiers; existing Chinese output remains optional.
- Stage recovery, bounded resource use, and research telemetry.

### Outside the core engine

- Trading agents, transaction proposals, tactical signals, entry/exit rules,
  technical-indicator strategies, stops, position sizing, and portfolio constraints.
- Weekly scheduling, batch/watchlist orchestration, notifications, and task links.
- Google Drive publication, authentication for publication, and document upload.
- Broker access, order execution, paid-data purchases, and autonomous outreach.

Prices and corporate actions remain research inputs for valuation and historical
context; excluding tactical signals does not exclude market data. Business and
valuation risk analysis remains essential, but is not portfolio-risk management.

The dependency direction is applications/adapters -> research engine -> source,
model, and persistence services. The engine must not import the weekly runner,
publication code, trading agents, or portfolio decision schemas. It runs without
a chat, scheduler, Google account, portfolio, or broker connection.

The existing trading-oriented graph remains available as `legacy`. A separate
`investment_research_v2` workflow reuses neutral infrastructure, not fake debate
histories or fabricated trade decisions to satisfy legacy validation.

## 2. Agreed defaults and changes from the earlier proposal

- Current development/evaluation pair: NVDA and Robinhood (HOOD), as explicitly
  selected by the user. Original expansion coverage includes AMD, INTC, AVGO and
  TSM. Reusable interfaces do not imply validated support for all sectors.
- Mandate: long-term company research; distinguish current intrinsic value,
  12-month valuation, and three-year shareholder-return scenarios.
- Internal research and current reader reports: English. Defer Chinese generation
  and language experiments while preserving compatibility with existing requests.
- Public sources first, retaining existing configured providers as secondary
  sources. No new paid service or desktop connector is a core dependency.
- Per-company allowance: 90 minutes and 1.5 million total tokens, with early exit.
- Report supported findings even when incomplete, labeled Needs review / Unrated;
  withhold targets or conclusions whose prerequisites fail.
- A ten-year DCF is a supported model, not a universal requirement. Forecast
  horizon, business drivers, and valuation method must fit the company.
- The opening is an executive summary for first coverage and a change summary
  for an update. Neither mode implies a weekly schedule.
- A future weekly adapter may request update-plus-full-thesis reports, but weekly
  execution and its rollout are not acceptance dependencies of the core engine.

## 3. Evidence, financial data, and expectations

### Source acquisition and provenance

Use SEC submissions/XBRL and company investor-relations disclosures for filing
discovery and reported financial information. Retrieve filing documents, exhibits,
earnings releases, presentations, and publicly accessible transcripts/Q&A for
details that standardized entity-level facts do not capture. Support 10-K, 10-Q,
8-K, 20-F, 6-K, and amendments for the pilot companies.

Collect five fiscal years and eight quarters where available. Preserve original
figures and source locations alongside normalized values: entity, metric, currency,
scale, period start/end, duration, accounting basis, segment/dimension, accession,
publication/acceptance time, retrieval time, and content hash. Missing periods are
not zero. Cash-flow quarter derivations retain their operands and formula.

Explicitly handle restatements, fiscal-calendar changes, debt versus leases,
unusual items, GAAP/non-GAAP bridges, SBC, shares and splits, and TSM's reporting
currency, ADR conversion, and accounting basis. Retain provider disagreements;
do not silently blend unlike definitions or count duplicate data as corroboration.

Use yfinance for prices and secondary cross-checks. A normalized statement alone
is not a substitute for the filing's accounting definitions and footnotes.

Source records distinguish a company statement from independent support for that
statement. For material claims, seek customers, competitors, suppliers, official
regulators, and credible industry/technical sources. Track common source origins
so syndicated stories and repeated management claims do not become independent
confirmations. Public information cannot fully replace expert interviews or channel
checks; record those coverage limitations rather than inventing them.

### News and event coverage

Use structured events, retaining publisher, URL, publication time, event time,
entities, text availability, and original source. Fix the existing loss of parsed
timestamps and final result limiting before date filtering. A recent-list endpoint
must disclose its limited historical/window coverage.

The baseline combines filings, IR announcements, relevant official policy/macro
releases, and existing news discovery. Investigate customer, supplier, competitor,
and policy developments only when connected to a research question. Deduplicate
syndication and classify announcements, assertions, independent reporting,
commentary, and rumors. Full-text access failures remain visible; headlines and
snippets do not justify claims requiring unavailable article contents.

Store acquisition success, temporal coverage, and unresolved coverage gaps
separately from event counts. No results is not proof that no event occurred.

### Expectations comparison

Create an explicit comparison of management guidance, available analyst consensus,
our forecast, and price-implied scenarios for the important drivers. Capture
forecast period, basis, source/vintage, dispersion, contributor count, and revisions
when available; leave unavailable fields unknown. Do not reconstruct historical
consensus from today's estimates or call one analyst's estimate a consensus.

The report explains where and why our assumptions differ and what evidence could
resolve the difference. Reverse DCF establishes conditional price-consistent
assumptions, not the unique beliefs of market participants. Missing consensus is
disclosed, not replaced by an invented market narrative; it does not alone block
otherwise supported fundamental research.

### Acquisition and point-in-time safeguards

Cache immutable source snapshots and honor public access restrictions. Use an
identified SEC client with a shared two-request-per-second limiter, bounded
requests, timeouts, and backoff. Store the configured contact identity outside
report artifacts. Do not bypass paywalls or purchase access automatically.

Only fetch public HTTP(S) destinations; reject private/local network targets and
unsafe redirects. Retrieved documents are untrusted data, never instructions or
executable code. Keep credentials and private operational details out of artifacts.

Apply an explicit timezone-aware cutoff. Publication/acceptance, retrieval, and
observation dates have distinct meanings. Later amendments or revised datasets
must not enter historical evidence merely because the underlying period is old.
Unknown availability is flagged and cannot establish a historical claim. Live
profiles and consensus require an eligible retained snapshot for historical use.

## 4. Research workflow and responsibilities

Responsibilities are not a requirement for one permanent agent per topic.
Parallelize independent investigations; shared assumptions and dependent model
updates must be reconciled before synthesis.

| Responsibility | Output | Relation to legacy roles |
| --- | --- | --- |
| Research planner | Three to five decisive questions, coverage plan, material gaps | Evolves research-manager responsibility |
| Business/industry analyst | Revenue drivers, competition, customer economics, external evidence | Expands fundamentals/news responsibilities |
| Accounting/cash-quality analyst | Reconciled history, earnings quality, financing and dilution | Separates accounting work from generic fundamentals |
| Expectations analysis | Guidance/consensus/own-model comparison and disagreement | Required capability shared by planner and analysts |
| Management/capital-allocation review | Historical decisions, incentives, governance, guidance record | Required business/accounting coverage, not a generic biography |
| Financial-model/valuation agent | Justified structured assumptions and interpretation | New dedicated modeling responsibility |
| Independent challenger | Alternative case, counterevidence, disputed assumptions | Replaces mandatory opinion-based debate rounds in V2 |
| Claim verifier | Source support and numerical/semantic review findings | Separate verification pass with explicit coverage |
| Editor/synthesizer | Coherent English report for the current campaign, optional Chinese compatibility, validated tables and references | New reader-facing synthesis responsibility |

Use the existing research-manager/fundamentals model assignments for planning and
specialist work, news assignment for event classification, and portfolio-manager
model assignment for challenge and synthesis. This reuses model configuration,
not the portfolio agent or its schema. Claim verification uses the fundamentals
assignment. Every assignment remains explicit, configurable, and preflighted.

### Execution sequence

1. Validate the request; load an eligible prior dossier; acquire baseline evidence.
2. Identify decisive questions and the leading competing explanations. Rank gaps
   by possible effect on the thesis/valuation and likelihood that research can
   resolve them. Do not invent numerical value-of-information estimates.
3. Investigate business, accounting, expectations, and management. Record evidence,
   counterevidence, uncertainty, economic consequences, and falsification criteria.
4. Build company-specific forecasts and run deterministic model/constraint checks.
5. Give the challenger evidence and questions without the lead narrative or final
   investment view. Freeze its independent assessment before showing the lead
   model and conclusion for reconciliation. Record and test disagreements.
6. Conduct targeted retrieval and affected-analysis/model revisions for material
   gaps; allow at most three follow-up cycles and stop when additional work is
   unproductive or the resource allowance is reached.
7. Verify material claims and model consistency; synthesize the reader report;
   validate the rendered report against evidence and computed values. Repairs
   cannot bypass checks or continue outside the remaining budget.
8. Persist artifacts and assessment atomically; promote an eligible dossier version.

Management review covers guidance versus contemporaneous actuals, incentives,
governance conflicts, acquisitions, R&D/capex outcomes, and repurchases versus
dilution where material. Distinguish demonstrable evidence from judgment. Do not
equate earnings beats with value creation or selectively sample favorable periods.

## 5. Company-specific modeling and valuation

Agents specify assumptions; tested code performs calculations. Never execute
model-generated scripts as the valuation engine. The structured model contains
historical anchors, drivers, forecast periods, assumptions with provenance or
rationale, formulas, outputs, and limitations.

Connect income, balance-sheet, and cash-flow projections at the material schedule
level. Reconcile working capital, capex/depreciation, cash needs, financing, and
shares. Do not force unsupported granular forecasts; unavailable decisive schedules
limit the model's conclusions.

Support the following pilot business templates:

- Brokerage/financial platforms (HOOD contrast): transaction volumes and take
  rates, customer balances versus corporate assets, net-interest economics,
  subscription income, regulatory capital and funding. Select an appropriate
  equity-cash-flow/earnings valuation basis; do not treat customer custody assets
  as corporate cash or mechanically reuse industrial FCFF/net-debt conventions.

- Fabless/product businesses: segment/product mix, volume/pricing where available,
  customer demand, supply constraints, and R&D/reinvestment.
- Foundries/manufacturing: capacity, utilization, yields where supported, node mix,
  capex, depreciation, and funding requirements.
- Recovery/turnaround: segment performance, cash burn, financing needs, and explicit
  conditions for normalization. Negative earnings do not receive a P/E multiple.
- Mixed businesses: separate material economic segments and use sum-of-the-parts
  where appropriate, reconciling corporate costs, net debt, and other claims once.

The model builder chooses and justifies the forecast horizon based on the business
cycle and a supportable normalized state. DCF is the default candidate, not an
obligation when inputs cannot support it; comparable valuation and sum-of-the-parts
are supported alternatives/cross-checks with explicit applicability. Never
mechanically average methods with unrelated horizons or accounting bases.

Calculate bear/base/bull scenarios and sensitivities. Do not assign default scenario
probabilities; use justified probabilities only when supported and otherwise show
unweighted scenarios. Reverse valuation changes one named driver at a time while
holding others fixed and states that many other solutions can fit the same price.

Preserve SBC as an economic cost and reconcile dilution without double counting.
Do not divide today's FCFF equity value by a future repurchase-reduced share count.
Handle financing, distributable cash, repurchases, and dividends consistently.
Keep current value, 12-month valuation, and three-year returns distinct; a future
value needs an explicit bridge, not a relabeled present DCF.

### Economic and shared-industry constraints

Version a shared set of source-backed industry assumptions: demand, customer
spending, capacity, supply limitations, technology transitions, and regulatory
conditions as relevant. Record compatible versus disputed assumptions across
company dossiers rather than force consensus.

Test that forecast volumes fit capacity, demand fits customer economics, margins
fit competitive assumptions, and growth fits reinvestment/funding. Do not count
replacement demand twice or sum different supply-chain layers as independent end
demand. Flag contradictions even when every individual input has a citation.

Every material risk must connect to a driver, financial consequence, or a clearly
stated unquantifiable limitation. Show terminal-value dependence and sensitivity
instead of hiding uncertainty behind detailed annual forecasts.

## 6. Verification, reporting, and longitudinal learning

Separate data quality, numerical integrity, research adequacy, and editorial quality.
Legacy structural checks continue unchanged for legacy runs; V2 has its own contract.

Deterministic checks cover units, dates, identity, formula inputs, reconciliations,
reference resolution, and output consistency. Semantic verification checks whether
the cited passage supports the claim's scope, period, magnitude, and causal wording.
A real citation is necessary but not sufficient. LLM verification is not factual
certification and cannot override deterministic failures. Record review coverage
and unverified items rather than claim comprehensive verification by default.

Reader outputs distinguish investment view (`favorable`, `neutral`, `cautious`,
`unrated`), evidence confidence, and research status. These are research assessments,
not trading instructions. Neutral requires balanced evidence; insufficient decisive
evidence yields Unrated / Needs review. Unsupported prices are omitted. Optional
social sentiment is not an acceptance prerequisite for deep fundamental research.

Report order:

1. Executive conclusion; changes since previous coverage when applicable.
2. Central disagreement and expectations comparison.
3. Business economics, industry, competition, and customer/supplier evidence.
4. Financial quality, management, governance, and capital allocation.
5. Forecasts, valuation, scenarios, and prospective returns.
6. Counter-thesis, risks, catalysts, and falsifying evidence.
7. Material unknowns, methodology, sources, and model appendix.

For the current English campaign, use a concise executive opening and a cohesive
narrative; do not apply Chinese character counts as English length limits. If
Chinese output is explicitly requested later, the earlier soft targets are
600-1,000 Chinese characters for the opening and 6,000-10,000 for the full narrative,
excluding tables/references. Material analysis takes precedence over length.
Generate key tables and numerical references from validated
structured values. Use readable footnotes linked to precise sources; keep machine
IDs and full mappings in the audit record. The editor cannot invent evidence,
modify assumptions, remove material caveats, or turn an assumption into a fact.

Maintain immutable company dossiers with thesis/counter-thesis, financial history,
models, expectations, open questions, source lineage, and change attribution.
Update mode refreshes source discovery and market inputs and recomputes affected
research. The caller requests refreshes; no timer or weekly scheduling exists in
the engine. Missing or incompatible prior dossiers trigger disclosed fresh coverage.

Earnings, guidance, restatements, financing, acquisitions, regulation, or contrary
evidence can require a deep refresh during a requested run. Failed acquisition does
not advance a coverage watermark. Use a seven-day overlap from the previous
successful cutoff for event discovery; deduplicate rather than discard unchanged
events without checking their provenance.

Degraded attempts remain inspectable but do not overwrite the validated dossier.
An old model may be referenced with its old as-of date; it must never appear as a
newly verified conclusion after an unsuccessful update. Source-backed observations
and candidate models are stored separately from promotion eligibility.

Record forecast vintages and subsequently compare revenue, margins, and cash flow
with released actuals, respecting basis changes. Attribute errors to data, modeling,
assumptions, or unexpected events. Use calibration tests only for explicit dated
probabilistic forecasts. Stock-price performance and reader preference are not
substitutes for analytical correctness.

## 7. Public interfaces and recovery

Create a neutral `tradingagents.research` package with typed request/result contracts.
Expose `run_research(request, services)` and a standalone `cli.research` entrypoint.
The request contains company identity, cutoff/timezone, mandate/horizons, languages,
model/source settings, budgets, output destination, and optional prior dossier and
frozen evidence snapshot. The result contains artifact paths/hashes, assessment,
unresolved gaps, usage, and an optional promoted dossier ID. No trading or weekly
fields are required. Inject source/model/storage services for offline replay/tests.

Persist independently versioned contracts for source documents, events, provider
facts, extracted claims, expectations, questions/findings, model assumptions/results,
review findings, assessment, and dossiers. Keep LLM-extracted claims separate from
existing provider-supplied `EvidenceRecord` facts, including extraction and source
verification status. All material conclusions resolve to evidence, explicit
assumptions, or reproducible calculations.

Artifacts: `reader_report.md`, `audit_report.md`, `evidence.json`, `research.json`,
`valuation_inputs.json`, `valuation_results.json`, `quality.json`, and
`run_metadata.json`. Dossier history lives in its own configured store. Metadata
records core/prompt/model/calculation/schema versions, source snapshots, cutoff,
languages, dossier lineage, review coverage, stage timings, tokens, and stop reason.

Checkpoints bind all behavior-affecting inputs, including source and dossier
versions; incompatible resumes must not reuse stale stage results. Save successful
stage outputs before rendering. Re-rendering or publication retry never restarts
research. Locks, atomic writes, and recoverable partial artifacts are required.

Retain the 300-second call timeout and existing transient-retry policy. Reserve the
last 20 minutes and approximately 300,000 tokens for synthesis and verification.
Apply admission checks before new calls and include retries in usage. Cached input
is not subtracted from total tokens; reasoning output is not added twice. Record
unavoidable in-flight token overshoot honestly; enforce the wall-time deadline and
do not silently increase either allowance. No new call starts after exhaustion.
Export deterministic diagnostics if insufficient allowance remains for a report.

Authentication/quota failures stop new inference; company-specific source failures
produce explicit gaps. Backend selection remains explicit, with no automatic paid
API fallback. Implementation and offline tests do not initiate live research runs.

Adapters may select V2 and consume the result. Legacy config/manifests retain legacy
behavior; never mutate historical batches, docs, or checkpoints in place. The
`complete_report.md` compatibility alias belongs to an adapter, not the core output
contract. New adapter manifests identify artifact kind/hash and require version-aware
readers. Core implementation does not switch live weekly configuration or schedules.

## 8. Design references and limits

These references informed the design; vendor capability descriptions are not
independent quality benchmarks or a recommendation to purchase access.

- [CFA research-report guidelines](https://www.cfainstitute.org/sites/default/files/-/media/documents/support/research-challenge/challenge/rc-written-report-guidelines.pdf): business, industry, valuation, financial analysis, and risk coverage.
- [CFA company forecasting](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/company-analysis-forecasting): drivers, company-specific horizons, and coherent forecasts.
- [CFA capital allocation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/capital-investments-and-capital-allocation): management decisions and value creation.
- [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [developer guidance](https://www.sec.gov/about/developer-resources): filing/data access and fair-use controls.
- [AlphaSense research](https://www.alpha-sense.com/deep-research-for-users/): broader research collections and source-location citations.
- [FactSet estimates](https://insight.factset.com/resources/factset-consensus-estimates-datafeed): consensus, guidance, revisions, and individual estimates; avoid relying on dated coverage counts.
- [Anthropic research architecture](https://www.anthropic.com/engineering/multi-agent-research-system) and [Dexter](https://github.com/virattt/dexter): adaptive investigation, bounded coordination, and research evaluation.
