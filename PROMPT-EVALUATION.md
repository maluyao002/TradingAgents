# Evidence-grounded prompt improvements

## Scope

This revision changes prompts, evidence handoffs and data preparation while
preserving model profiles, debate rounds, tool interfaces and existing report fields. It adds a prompt
revision to checkpoint signatures to avoid resuming an old-prompt run.
No paid model analysis was run for this evaluation.

## Proposal assessment

| Proposal | Implementation and expected benefit | Limitation |
| --- | --- | --- |
| Preserve evidence and caveats | All analysis roles distinguish facts, assumptions, calculations, sources, dates, and accounting bases. Managers receive validated evidence records and specialist analysis, with full-report fallback. | Citations and correctness still depend on supplied evidence and model compliance. |
| Improve financial comparisons | Fundamentals checks unusual items in both periods and trailing/forward EPS comparability. News requires matching 12-month observations for year-over-year inflation. | Numeric comparisons use provider facts and explicit period checks; no new filing-footnote retrieval is included. |
| Reduce premature conclusions | Specialists provide findings without transaction verdicts; decision agents distinguish view, confidence, and action. | Existing structured output fields remain unchanged. |
| Improve debate usefulness | Opening claims are bounded; subsequent rounds emphasize corrections, concessions, and unresolved questions. Roles test hypotheses rather than defend a required outcome. | Word budgets are instructions, not enforced limits; better decisions are not yet demonstrated. |
| Improve sentiment calibration | Ratios include denominators, unlabeled posts, coverage and independence; fixed sentiment trading thresholds are removed. | Small or biased samples remain small or biased. |
| Improve decision applicability | Missing ownership, allocation, horizon and risk context are explicit. Illustrative valuations, moving indicators, review triggers and execution stops are distinguished. | Normal CLI/graph runs still supply no portfolio context or investment horizon. Helper tests with supplied values do not establish public input support. |
| Separate external text from instructions | Sentiment evidence is in a user message, with policy in the system message. Reports are marked as reference data. | Delimiters and prompt instructions are not a complete prompt-injection defense. |

## Offline evaluation

Tests capture prompts sent through actual agent factories, including structured
and fallback sentiment paths. They check source placement, round boundaries,
decision context, source/caveat instructions, schema alignment, and checkpoint
revision handling. They establish instruction delivery and compatibility, not
financial accuracy or real-model adherence.

Initial offline validation: 813 tests passed, 65 subtests passed, and two tests
were skipped (optional Bedrock dependency and credential-gated DeepSeek live
test). Ruff and whitespace checks passed. Independent review verified the
handoff, fallback, historical-data, caching and alias-conflict fixes without
finding blocking regressions in those paths.

Independent review identified and corrected conflicting Hold definitions,
system-role sentiment source placement, an unavailable news-report dependency,
and technical-price-based sizing language. Supplied trader decision context is
also kept outside the system message. Portfolio context input plumbing remains
outside this revision.

## Cost improvements (priorities 3–5)

- Role-specific soft output targets apply to structured and ordinary-text paths.
  The trader has room for trigger dates, rationale, horizon and invalidation.
  Material qualifications take precedence over a word target; no report is
  truncated or sent for another rewrite just because it is long.
- Structured-output recovery distinguishes formatting failures from transport,
  authentication, quota and programming errors. Ordinary output instructions
  also preserve the report headings expected by downstream readers.
- Specialist analysis and the downstream handoff use one model-authored
  representation, rendered into readable Markdown by code. Extra prose outside
  the representation triggers full-report retention; a summary is never assumed
  to cover that prose just because its JSON is well formed.
- Provider facts retain stable IDs, units, periods, accounting-basis limitations,
  sources and calculation dependencies. Downstream context includes referenced
  facts and their dependencies, caveated facts, and all supplied preparation
  caveats and analyst conflicts. A source citation retains its metadata; it does
  not expand every fact from that source. Validated `required_evidence_ids`
  retain material deterministic comparisons even if the model omits their IDs.
  Complete source data and evidence records remain in `evidence.json` beside
  the saved reports. Models must still identify material evidence correctly;
  structural validation cannot establish semantic completeness of their analysis.
- Market preparation reuses the existing eleven-indicator snapshot. Historical
  investigation remains available, and tool results are not duplicated in the
  prepared-data message. Fundamentals are fetched before their analysis call,
  preserving annual and quarterly data and explicit provider failures.
- A run-scoped data cache reuses identical successful provider requests, including
  news shared by the sentiment and news analysts and Alpha Vantage statement
  payloads containing both annual and quarterly data. It does not reuse failures
  or carry responses into a later analysis run.
- Financial calculations keep provider, currency and reporting frequency separate.
  Missing values are not zero; non-positive comparison bases and capex sign
  conventions are handled explicitly. FRED retains vintage pinning and extends
  CPI/PCE comparison history to calculate a matching twelve-month change without
  relabeling the user's shorter requested window.

Existing portfolio-context input limitations remain. Fiscal period end dates
alone do not prove historical publication availability. Past-date fundamental
preparation therefore withholds statements and current overview values whose
publication or vintage timing cannot be verified, and reports that limitation
without fetching them. This reduces historical coverage until a source with
verifiable publication timing is integrated. Model settings, debate
rounds and execution behavior are unchanged; Flex processing and model changes
are not included in this revision.

## Follow-up to the AMD comparison

The user-generated second AMD run shortened report text by 68% and completed
44% faster in this single paired case. Exact-month CPI, EPS comparability,
debate corrections and decision applicability improved. However, the original
source-citation expansion retained all 318 fundamental facts, so shorter output
did not establish smaller inputs or lower cost. This prompted revision
`evidence-v3`:

- Source citations and selected facts have separate semantics. Mandatory
  financial comparisons retain their dependencies, while complete raw provider
  data remains saved. Invalid mandatory IDs still trigger safe full-report fallback.
- Deterministic summaries restore trailing-four-quarter repurchases and stock
  compensation, same-period basic/diluted share changes, year-to-date capital
  spending against the prior full year with unequal-duration caveats, and
  supplied operating-income adjustment bridges. Missing, mixed-sign or
  non-contiguous inputs are withheld rather than filled or annualized.
- Missing unusual-item disclosures now constrain the conclusion: reported
  improvement must not become verified recurring or underlying improvement.
  Exact arithmetic bridges are shown with distinct provider labels and do not
  imply undocumented GAAP/adjusted equivalence.
- Market snapshots retain dated five-trading-row changes for moving averages
  and MACD, the 10-day EMA/50-day SMA spread, and runtime calculation provenance.
  These endpoint changes do not establish a monotonic trend or crossover date.
- News receives a bounded macro baseline covering headline CPI, core PCE,
  unemployment, Fed funds, the ten-year yield and the ten-year/two-year spread.
  Strict parsing of the existing FRED formatter retains numeric endpoints,
  exact annual index changes and rate changes in percentage points, with
  required IDs and calculation dependencies. Unrecognized formats retain raw
  provenance and a caveat rather than fabricated numeric records.
  This restores relevant coverage without an extra model request to choose
  routine macro inputs. It adds provider retrievals where the earlier run
  omitted these series. Optional prediction-market searches remain driven by
  material unresolved questions.
- Saved `run_metadata.json` records allowlisted model settings, round counts,
  observed usage and its completeness, and CLI elapsed time. Missing metrics
  and billed cost remain unknown; no API prices are guessed. Tool callbacks
  exclude deterministic prefetch. A report saved through the API without
  supplied runtime usage records those metrics as unavailable.

An offline replay re-prepared the same seven saved AMD fundamental responses
and retained the original model-authored analysis, without a model call. It
produced 361 total facts, 40 required IDs and 70 selected facts after explicit
citations and caveated facts were included. Shared analyst context fell from
109,071 to 51,195 characters (53.1%); the other three analyst inputs were kept
as saved. This measures the handoff repair, not a complete new analysis with
the new macro/technical baseline, model compliance, token billing or returns.
The financial replay reproduced repurchases 1.182B, SBC 1.895B, basic shares
+0.55%, diluted shares +1.78%, H1 capex 1.197B versus prior-full-year 0.974B,
and the 2.086B minus 0.186B equals 1.900B operating-income bridge.

Follow-up offline validation: 837 tests passed, 65 subtests passed, and two
optional/live tests were skipped. Ruff and whitespace checks passed. No paid
model run or new market-data request was made while implementing these fixes.

## Matched output evaluation to run next

Use the same Balanced models, two debate rounds, analysis date, and frozen source
responses for old/new prompts. A fresh web fetch alone is not a controlled
comparison. Repeat runs to assess model variation. Keep reports and credentials
out of version control.

The previously reviewed AMD run supplies these regression questions:

1. Are inflation observations exactly twelve months apart before being labeled
   year-over-year? Are unmatched periods labeled accurately?
2. Does the analysis account for the prior-period inventory/export-control charge
   when that disclosure is supplied, or explicitly identify missing footnotes?
3. Does undefined trailing/forward EPS comparability remain a caveat downstream?
4. Does an assumed valuation multiple remain an illustration rather than become
   a validated entry price or target?
5. Are downside thresholds ordered correctly and moving levels given refresh
   rules? Are closing review triggers distinguished from executable stops?
6. Does missing portfolio context produce conditional guidance without invented
   holdings, approved allocations, or risk budgets?
7. Are small sentiment samples and unlabeled posts represented honestly?
8. Do later debate rounds correct or resolve claims instead of restating them?

Score unsupported material claims, lost caveats, citation traceability to actual
supplied sources, calculation errors, unjustified actionable levels, and
assumed portfolio facts. Also record words, input/output tokens, cost and elapsed
time. The baseline contained approximately 24,415 report words, with about 64%
in debates; these figures describe one run, not a performance benchmark.

A shorter report or a changed Buy/Hold/Sell rating alone is not evidence of
improvement. The paired AMD results are encouraging but do not establish
repeatable quality, cost or latency gains; those require matched repeated
model runs. Historical data completeness and execution controls
need code-level validation beyond prompts.

## Cash-flow reconciliation and numeric citation follow-up

The next revision, `evidence-v4`, addresses two coverage gaps found after the
compact handoff repair:

- Prepare continuing- and discontinued-operations operating cash flow when
  the provider supplies those rows. Retain the latest annual reconciliation,
  its exact inputs and a readable qualification alongside headline annual FCF.
  Missing components are unavailable, not zero; a difference is not silently
  forced to reconcile.
- Prepare a dated sum of four contiguous quarterly FCF records and their
  dependencies. Compare it to a unique same-provider, same-unit overview only
  with an explicit unknown-horizon qualification. An arithmetic difference
  alone is not proof of a provider error or a comparable growth rate.
- Carry material cash-flow qualifications in prepared caveats so they survive
  even if a specialist omits them from its narrative. Preserve the selected
  records through the existing compact handoff without expanding source IDs
  into every raw statement row.
- Require the latest quarter and a valid exact prior-year comparison for gross
  margin, operating margin and net income, including calculation dependencies.
  Comparisons cannot substitute a different provider, unit, or nearby date.
- Ask specialists to put exact fact IDs beside numeric claims and cite both
  periods. Merge explicit inline references into the selected index on initial
  construction and checkpoint resume; invalid references disable compaction.
  This indexes explicit references, not numerical guesses from prose. It does
  not prove every arbitrary numerical statement has semantically correct
  support; required comparison records provide a deterministic backstop for
  the specific omissions found in the audit.

Models, reasoning efforts and debate rounds are unchanged. Prompt revision
tracking prevents resuming an incompatible earlier prompt configuration.

Validation for this revision: 851 tests and 65 subtests passed; one optional
Bedrock test was skipped and one integration test was deliberately excluded.
Ruff and whitespace checks passed. Independent review confirmed same-series
comparison selection and source-only compact handoff/checkpoint retention.

A frozen-source AMD replay kept the saved specialist response unchanged and
re-prepared its seven financial responses without network access or model
calls. The restored packet contains 82 of 379 prepared financial facts,
including all 56 required IDs and their dependencies. It reproduces total OCF
7.709B = continuing 6.493B + discontinued 1.216B; annual FCF 6.735B = total OCF
7.709B less 0.974B capex; four-quarter FCF 8.403B versus overview 8.841499648B
with an unknown-horizon qualification. Both-period profitability records
survive even when absent from the saved model's reference list.

With the same current renderer on both sides, the shared analyst context is
62,868 characters before re-preparation and 70,170 after (+11.61%). This is a
bounded offline context comparison, not measured new-run token billing or
proof of model compliance. No paid analysis was run. A subsequent user-run
report is still needed to assess the generated narrative and actual cost.
