# Codex subscription integration

The research workflow supports the official local Codex app-server and the
existing API providers as explicit startup choices. Stages 1–4 are merged into
`feat/codex-integration`. Stage 5 has AMD full-pipeline comparisons and offline
regressions; non-AMD acceptance remains pending. This is a supervised research
beta, not an unattended trading or broker execution service.

## Stage tracker

| Stage | Scope | Acceptance |
| --- | --- | --- |
| 1 | Isolated protocol compatibility probe | Handshake, model discovery, ephemeral thread lifecycle, transport failures; live inference remains user-run |
| 2 | Adapter and startup choice | Explicit Codex/API choice, authentication guidance, capability validation, API regression suite |
| 3 | One fundamentals analyst | Identical prepared evidence, reconciliations and citation validation; matched user-run comparison |
| 4 | Full pipeline | Tool requests, role isolation, debates, managers, progress and checkpoint recovery |
| 5 | Evaluation | Repeated matched runs with quality, latency and usage accounting; no unmeasured equivalence claims |

Each stage receives a separate PR into `feat/codex-integration` and a new reviewer
with no conversation history. Main is not the target of these stage PRs.

## Boundaries

- Subscription and API are explicit choices; no automatic paid fallback.
- Default API callers do not require Codex or start an app-server process.
- TradingAgents owns roles, evidence, conversation history and tool execution.
- Codex sessions use an isolated runtime home and ephemeral threads. Existing
  development authentication and sessions are not imported or altered.
- A separate runtime requires a separate sign-in through the official Codex flow;
  credentials must not be copied into this repository or report artifacts.
- No live model analysis is run by the implementation workflow. The user initiates
  live checks and comparisons. Offline tests cannot establish research quality.

## Stage 1: compatibility probe

Merged into `feat/codex-integration` in [PR #3](https://github.com/maluyao002/TradingAgents/pull/3)
after independent review and resolution of all four GitHub review threads.
Validation: 35 focused tests, 897 full offline tests and 65 subtests passed; Ruff
passed. Review corrections cover detached descendant cleanup, explicit POSIX
support, strict authentication/isolation response validation, path-free filesystem
failures, and detection of all workspace entry types. The Unix socket regression
requires local IPC permission when tests run in a restricted sandbox. Live gates
below describe the historical stage-1 checklist. Subsequent stages and user-run
AMD evaluations exercised inference and the complete research pipeline; the current
release checklist is at the end of this document.

The initial transport supports macOS/Linux only. It explicitly rejects Windows
before launching a subprocess; the existing API backend remains cross-platform.
Process groups and nonblocking POSIX pipe writes provide bounded cleanup and
write deadlines, including descendants that redirect their standard streams.

The installed CLI used for schema inspection was `codex-cli 0.153.4`. Its generated
protocol schema exposes `thread/start.ephemeral`, `baseInstructions`,
`developerInstructions`, `allowProviderModelFallback`, and per-turn `outputSchema`
and `effort`. This establishes field availability, not server or model adherence.

The probe performs a handshake, reads only the authentication mode, lists model
IDs and efforts, and optionally starts/unsubscribes an empty ephemeral thread.
It never starts an inference turn. Results must not contain email, tokens, raw
server errors, or personal filesystem paths.

Actual local metadata-only smoke on CLI 0.153.4 succeeded: initialize;
account/read reported no sign-in in the dedicated empty runtime; model/list
returned six catalog entries; thread/start returned the requested Terra model,
OpenAI provider, ephemeral=true, and zero instruction sources. The created thread
was absent from thread/list and unsubscribe succeeded. No turn/start was sent.
An unauthenticated catalog is not proof of account entitlement or model access.

Remaining gates before model execution: verify effective tool/configuration
isolation, authentication with the dedicated runtime, structured output,
cancellation during inference, model/effort adherence, and absence of research
sessions in the normal desktop sidebar. A successful offline probe alone is not
permission to enable the complete backend.

## Stage 2: adapter and startup choice

Merged into `feat/codex-integration` in [PR #4](https://github.com/maluyao002/TradingAgents/pull/4).
Validation: 986 full offline tests and 65 subtests passed; Ruff passed. Independent
review and focused re-review are complete with no remaining findings. Review fixes
require explicit effective configuration fields and retain known turn IDs for
interruption after malformed responses. No live research run was performed during stage 2; later stages include user-run
AMD research evaluations.
The GitHub hooks finding is also addressed: startup disables lifecycle and plugin
hooks, and inference requires verified disabled hook features and explicitly empty
effective hook configuration, including configuration inherited from system layers.

At startup, choose **API** for the existing research workflow or **Codex subscription**
for the full research workflow (stage 4). You can skip this picker with `tradingagents --backend api`
or `tradingagents --backend codex`. `python -m cli.main` accepts the same options.
`TRADINGAGENTS_BACKEND` supplies a default backend when no flag is given; a flag wins.
The API provider, profile, key, checkpoint, and model environment settings keep their
existing behavior. Programmatic `TradingAgentsGraph` callers continue to use the API unless they explicitly select the Codex backend and supply an open adapter.

The Codex preview checks ChatGPT sign-in and reads the live model catalog. Quick,
Balanced, and Deep profiles are available only when every research role's exact
model and effort are advertised. Unsupported profiles are disabled and show the
affected roles. Custom checks one advertised model/effort pair. This is a capability
check, not proof of model entitlement, remaining quota, or research quality. The
preview does not run inference, fetch market data, save model settings, or fall back
to an API provider. The early preview rejected checkpoint flags; the current full workflow supports
backend-isolated checkpoint recovery as described below.

### One-time sign-in (user-run)

Use the official stable Codex CLI **0.153.4 or newer** on macOS/Linux and a dedicated runtime outside this
repository. The default is `~/.tradingagents/codex`. Do not copy authentication files
from the normal Codex home or put credentials in this repository.

This integration uses the isolation controls verified on CLI 0.153.4. Older, prerelease, or
unrecognized CLI versions are rejected before app-server startup with an upgrade
message, rather than dropping isolation flags that older strict-config parsers
cannot recognize. Newer versions must still pass the capability and isolation checks.

```sh
export TRADINGAGENTS_CODEX_HOME="$HOME/.tradingagents/codex"
mkdir -p -m 700 "$TRADINGAGENTS_CODEX_HOME"
CODEX_HOME="$TRADINGAGENTS_CODEX_HOME" codex login -c 'cli_auth_credentials_store="file"' -c 'forced_login_method="chatgpt"'
tradingagents --backend codex
```

The Codex CLI requires this runtime directory to exist before sign-in; the `mkdir`
step creates it with access restricted to your user. Your current project directory
does not affect the runtime location.

Complete the official browser sign-in with ChatGPT. API-key sign-in is not accepted
by this backend. Keep the same `TRADINGAGENTS_CODEX_HOME` for later setup checks;
the application deliberately ignores your normal `CODEX_HOME`. The CLI only provides
guidance when sign-in is missing; it does not read, copy, or display credentials.

Stage 2's adapter is standalone. The stage 3 pilot below connects one fundamentals
analyst; tool execution and the remaining graph are stage 4. The existing API
workflow remains available for full research reports.

The adapter's text interface takes explicit role instructions, evidence/history,
model, and effort for each call. Each call uses a new ephemeral thread, so the
caller supplies any history it needs and roles do not share server-side context.
Before inference it checks effective configuration, MCP availability, and tool
feature settings; unverifiable isolation fails closed. Unexpected tool requests,
model rerouting, unsuccessful turns, and timeouts fail the call. The adapter does
not retry or fall back to the API. Structured output, TradingAgents tool dispatch,
and graph callbacks are outside this stage. Fake-server tests establish client
behavior; real model adherence, sidebar behavior, and research quality still need
the later user-run checks.

Feature discovery is not a complete list of configuration keys. Unadvertised
connector, memory, and skill controls must be explicitly disabled in the effective
configuration; any advertised conflicting state is rejected. Shell access must be
disabled independently of the catalog's execution-backend preference. Skill and
instruction discovery settings are also checked explicitly. The corrected
`mcpServerStatus/list` request and isolation checks passed a metadata-only smoke
check on CLI 0.153.4 with a temporary empty runtime, no authentication reads, and
zero inference turns. This does not establish model execution or research quality.

## Stage 3: fundamentals-only pilot

Merged in [PR #5](https://github.com/maluyao002/TradingAgents/pull/5). This pilot
reuses the existing fundamentals analyst, prepared financial calculations, prompt,
and evidence-packet validation. It does not change the API research graph.

Run interactively with either backend:

```sh
tradingagents --backend codex --fundamentals
tradingagents --backend api --fundamentals
```

The pilot shows the exact fundamentals model and effort for Quick, Balanced, or
Deep. Balanced and Deep currently both use Sol/high for this role; the profiles'
other roles and debate rounds are not part of a single-analyst run. Codex validates
the selected pair against its catalog; the API pilot explicitly uses OpenAI with
the same pair. API model entitlement is still checked by the provider. Neither
backend automatically falls back to the other. The pilot API call disables automatic
retries, and the Codex inference deadline is five minutes.

For a matched comparison, run the first backend and replay its saved evidence
through the other. Substitute the current analysis date and the actual result path:

```sh
python -m cli.fundamentals --backend codex --ticker AMD --date YYYY-MM-DD --profile balanced
python -m cli.fundamentals --backend api --ticker AMD --date YYYY-MM-DD --profile balanced --evidence reports/fundamentals_RUN/result.json
```

Each run writes `fundamentals_report.md` and `result.json` to a unique directory
under ignored `reports/`. The result includes the prepared evidence snapshot,
validated evidence packet, backend/model/effort, a SHA-256 evidence fingerprint, and elapsed time. Replay uses
only the source snapshot, never the previous analyst's answer, and rejects a
ticker/date mismatch. It does not refetch providers. Keep bundles local: they
contain research/source data and are not intended for a public PR.

With no replay bundle, TradingAgents fetches data through the existing configured
providers. A historical date may deliberately produce unavailable fundamentals
because those providers lack point-in-time publication timestamps. This limitation
is retained, not replaced with current data. Credentials use the existing provider
setup; Codex sign-in does not grant access to paid data feeds.

The user initiates all live comparisons. Review both reports for continuing versus
discontinued OCF, four-quarter versus overview FCF horizons, working-capital bridges,
required financial comparisons, numeric citations, and preserved missing-data
caveats. Validation status and citation coverage are structural checks, not proof
of factual correctness or equivalent research quality. No live model comparison
has been performed during implementation. Full pipeline/progress/checkpoint work
remains stage 4, and repeated quality/latency/usage evaluation remains stage 5.

Validation uses focused offline checks: 19 new core pilot cases, nine pilot CLI
cases, and the affected existing CLI, prepared-data, reconciliation, citation, and
prompt tests. No full-suite rerun or real research inference was needed for this
stage. Subsequent user-run AMD comparisons are reflected in the release checklist. A fresh independent
read-only review found one portability issue: locale-dependent artifact encoding.
All artifact reads/writes now use explicit UTF-8, verified by a Unicode round-trip
test with UTF-8 mode disabled and the C locale. No other actionable findings were reported.

A user-run pilot exposed a documented `warning` notification associated with the
active thread. An empty-thread metadata check reproduced this event without
inference. The adapter now accepts well-formed advisory warnings without echoing
private warning text; unrelated-thread warnings, malformed payloads, unknown
active-turn events, and tool activity remain rejected. All 59 adapter tests pass,
including five focused warning/unknown-event regression cases.

A follow-up tiny, user-authorized Codex diagnostic identified
`thread/settings/updated` during turn startup. The adapter now validates its
model, effort, provider, workspace, approval settings, and read-only sandbox before
continuing. A repeat diagnostic using Sol/low received a final text response and
completed cleanup; no market data or API backend was used. This verifies the live
protocol path, not AMD research quality or the Balanced/high workload. Unexpected
known protocol events now include their public method name in diagnostics, never
their payload. Unknown names stay redacted.

## Stage 4: full research pipeline

The Codex startup choice now runs the same analyst → research debate → trader →
risk debate → portfolio manager graph as the API. The fundamentals-only pilot
remains available with `--fundamentals`. Quick/Balanced/Deep preserve their exact
per-agent models and reasoning efforts and 1/2/3 rounds; explicit round environment
overrides retain their existing precedence. Unsupported profiles are disabled;
there is no model downgrade or API fallback. The full Codex flow currently offers
these three profiles, while custom API settings retain their existing behavior.

```sh
tradingagents --backend codex --checkpoint
tradingagents --backend api
```

Codex sign-in covers model access only. Existing market-data provider credentials
are still needed. The workflow produces research reports and proposed decisions;
it does not submit orders. Full live research tests remain user-run.

### Tools, roles, and structured decisions

Each LangChain model invocation opens a fresh ephemeral Codex thread. TradingAgents
passes that role's explicit instructions and message history; the Codex runtime does
not retain conversations across nodes. Tool requests use app-server's `outputSchema`
structured response, converted into validated LangChain tool calls. Only tools bound
by the current analyst and arguments matching their schemas are accepted. The existing
local ToolNode executes them; Codex's shell, browser, MCP, and workspace tools remain
disabled. Experimental native dynamic-tool execution is not enabled.

Research Manager, Trader, Portfolio Manager, and Sentiment Analyst keep their typed
output schemas and local validation. Model calls are serialized within the shared
runtime. Invalid protocol responses, unsupported calls, timeouts, and cancellations
fail without switching backends. The same graph emits node/tool progress into the
existing dashboard. Token usage is captured when reported by the runtime; missing
usage and billed costs remain unknown. Final report metadata records the selected backend and role settings,
without runtime paths, credentials, or Codex thread IDs.

### Recovery

`--checkpoint` saves TradingAgents graph state after completed nodes. Run again with
the same ticker/date and effective research settings to resume. Recovery creates fresh Codex
threads from the saved application state; it does not depend on Codex session history.
An interrupted model call may be repeated when its graph node is retried.

Codex checkpoints live in the `codex/checkpoints` subdirectory of the configured data
cache; API checkpoints keep their existing location. Both now use a versioned
digest covering model/provider/effort/generation settings, language, vendor/tool
routing, news windows, graph settings, prompt version, and date-specific calendar
boundaries. Codex fingerprints also include the bridge version. Changing any of
these starts a fresh run. Older fingerprints intentionally do not resume.
Credentials, runtime paths, callbacks and display-only profile labels are excluded. On the Codex path, `--clear-checkpoints` clears only Codex
checkpoints. The dedicated app-server context closes on success, error, or cancellation.

For programmatic use, supply an explicit profile and own the adapter lifetime:

```python
from tradingagents.codex.adapter import CodexAdapter
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.model_profiles import apply_model_profile

config = apply_model_profile(DEFAULT_CONFIG, "balanced")
config["llm_backend"] = "codex"
with CodexAdapter(home="~/.tradingagents/codex", timeout=300) as adapter:
    graph = TradingAgentsGraph(config=config, codex_adapter=adapter)
    # This invokes the subscription models and configured data providers.
    state, signal = graph.propagate("AMD", "YYYY-MM-DD")
```

Validation: focused adapter/bridge and end-to-end graph tests passed, along with
API/CLI profile, pilot, checkpoint, and reporting regressions. A fresh independent
review identified structured-output coercion and an unstructured-retry path; both
are fixed. Codex now strictly validates the full typed response and emits a redacted
error on failure, without the API helper's free-text recovery. A regression verifies
that rejected private values do not enter warning logs. The reviewer also found an
unused import, which was removed. Ruff and whitespace checks passed.

Stage 5 has user-run AMD comparisons with per-role latency and usage accounting.
A non-AMD full-pipeline run remains the next acceptance check. Offline integration
tests establish routing, validation and recovery, not model quality or profitability.

## Sources

- [Official app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [Official authentication](https://learn.chatgpt.com/docs/auth)

### Report exports and market-data availability

Reports now default to one `complete_report.md`, with the portfolio decision first and every analyst and debate section retained. Evidence and run metadata remain in JSON sidecars. Choose “Save separate agent Markdown files too?” when saving to also export individual sections; programmatic callers can set `config["report_split_files"] = True`. Exporting either format makes no model calls. Save into a fresh or empty directory; nonempty destinations are rejected before writing so older files cannot be mixed into a new run.

FRED uses `FRED_API_KEY` when set. On macOS, an existing Keychain item can instead be selected with `FRED_KEYCHAIN_SERVICE` or `FRED_KEYCHAIN_LABEL`, and optionally `FRED_KEYCHAIN_ACCOUNT` (default `api-key`). Service takes precedence over label if both are set. No item names are guessed. Unavailable keys leave macro data explicitly unavailable. Keychain lookup results are cached for the process lifetime; restart after changing or unlocking credentials.

Recent daily prices carry a provisional warning through prepared facts and downstream evidence. Without exchange-session metadata, the conservative date boundary includes any day that could still be current in UTC-12; it does not certify an official closing auction. Observation time is snapshot preparation time and does not guarantee a fresh provider response.

### Token usage for backend comparisons

Both CLI backends save observed input, output, cached-input, reasoning-output and total tokens in `run_metadata.json`, including a per-model breakdown. Reasoning is a subset of output, and cached input is a subset of input; neither is added again to the total. API reporting continues to use provider response usage.

Codex uses the official [`thread/tokenUsage/updated`](https://learn.chatgpt.com/docs/app-server) event for the active thread and turn. Each model invocation creates a fresh ephemeral thread, so the latest cumulative `tokenUsage.total` snapshot belongs to that invocation. Repeated updates replace the prior snapshot; they are not summed. The adapter passes text and usage together under the existing call lock. The text-only `complete()` interface remains available; `complete_with_usage()` returns both. No extra inference or account-wide polling is needed.

Absent or malformed telemetry is recorded as unknown (`null`), not zero. Aggregate values are sums of observed usage, which can be partial: inspect `usage_completeness` and `calls_missing_usage` before comparing runs. Completeness tracks calls with valid input/output counts; optional cached, reasoning and total fields can still be unavailable for API providers. Counts are runtime-reported usage, not an invoice or a measurement of Codex subscription allowance. The initial implementation is verified with offline protocol fixtures; your next live run will confirm what this runtime reports.

### Per-role timing and repeated-input diagnostics

Both CLI backends now save `usage.per_role` and `usage.calls` in `run_metadata.json` automatically. Each completed call has its role, model, success/failure, observed token counts and elapsed seconds. Shared API clients still attribute calls to separate graph roles. Missing usage remains null. The existing live dashboard is unchanged, and instrumentation makes no model or data-provider requests.

- Role elapsed time sums model-call durations, including failures. It excludes data fetching outside those calls; it is not whole-agent wall time. `timing.model_active_seconds` measures the union of completed intervals, avoiding double-counting concurrent calls. `timing.llm_elapsed_seconds` is their sum.
- `timing.outside_model_calls_seconds` is the remainder of the measured CLI analysis interval when all calls finished. It includes preparation, tools, graph/UI work and checkpointing. It does not isolate backend overhead. Graph setup and Codex process initialization/authentication are timed separately before that analysis interval; model selection time and final process shutdown are not measured here.
- Codex successful calls additionally record `runtime` timings for bridge lock waiting, adapter work, validation, thread creation, turn start, waiting for completion and cleanup. Bridge duration contains adapter duration, which contains its phases: do not add these nested totals. Turn-wait duration includes server/network time and possible internal retries as well as inference; pure model compute is not exposed. Failed callbacks retain their total elapsed time, without invented phase measurements.
- `prompt_characters` counts visible message content. `repeated_message_characters` detects exact message type/content repetitions within one run, including across roles. `analyst_evidence_characters` and `repeated_analyst_evidence_characters` measure exact `<analyst_evidence>` blocks, even when surrounding debate/history changes. These overlapping character metrics are not additive and are not tokenizer measurements, cache hits, or guaranteed savings.
- Codex wire counts describe the instructions, serialized prompt and output schema supplied by the bridge. They cannot reveal hidden server framing. Only numeric metrics and allowlisted role/model labels are persisted; source text, prompts and their transient fingerprints are not exported.

Telemetry is scoped to the current invocation. Resumed checkpoints do not reconstruct timings or token counts from the previous process; saved older reports cannot acquire real timing measurements retroactively.

### What a matched full-pipeline replay would compare

Start with AMD and freeze a complete input bundle: ticker/date, raw market/fundamental/macro/company-news/global-news/social snapshots, additional permitted tool responses, portfolio assumptions and prior-decision memory. Run the complete agent graph once through API and once through Codex with the same Balanced choices, reasoning efforts, rounds and prompt-policy version. Regenerate every analyst and decision response; do not replay the previous model conclusions. Missing data requests must be explicitly unavailable or fail replay rather than silently fetching live data. Record the same input bundle identity for both runs.

This isolates backend behavior from changing market/news/social inputs. Equal initial evidence does not imply identical downstream prompts: different model outputs and tool choices are part of the behavior being evaluated. A separate role-by-role replay would be needed to hold every individual model prompt identical. For latency/caching evaluation, repeat in alternating backend order and distinguish fresh versus reused data caches; server-side prompt-cache state cannot necessarily be controlled.

The existing fundamentals-only replay and offline fake-model acceptance tests are not a full-pipeline replay implementation. After an AMD comparison, use a different-sector ticker to check generalization while keeping each backend pair matched.

### Analysis calendar and citation handoffs

Both backends use the same date-only analysis calendar as the CLI: the machine's
local timezone. Evidence prompts retain original retrieval timestamps and show
their local equivalents; `run_metadata.json` records the selected day's start
and exclusive end with UTC offsets. The two boundaries are localized separately
so daylight-saving changes are represented correctly. This describes a calendar
day, not a market-close decision cutoff. Evidence validation converts offset-aware
publication timestamps into that same local calendar before comparing dates;
date-only and naive timestamps retain their stated dates. Provider retrieval
windows are unchanged. Unknown publication times, historical-vintage limits,
and provisional market-bar status remain independent limitations; a next-day
UTC retrieval label alone does not establish future evidence.

The research-manager handoff must cite each material claim and the operands of
comparisons, including both periods and cash versus debt. If an immediate plan
omits a citation, the trader identifies that handoff gap rather than asserting
that evidence is absent from the complete research bundle. These are generation
contracts, not a guarantee that every future model response will comply.


## Release hardening and acceptance

### Reproducible installation and CI

The checked-in `uv.lock` pins the application and development dependency resolution.
Use `uv sync --locked --extra dev` for development or `uv sync --locked --no-dev`
for a runtime checkout. CI uses uv 0.9.28 and tests Python 3.10–3.13; the clean-install
job imports the non-editable package away from its checkout. Dependency updates
should deliberately update the lock and pass these checks. A lockfile does not pin
the operating system, Python patch release, or external Codex CLI.

The Docker build accepts only packaging files and application sources, excludes
local secrets and generated artifacts even inside source folders, and copies only
the installed environment to the runtime image. CI inserts private-file canaries
and checks they are absent in that image. The supplied container supports the
API backend; it does not bundle the Codex CLI. Use the local macOS/Linux installation
for Codex. Inject API credentials at runtime with the existing Compose env file.

### Accepted versus degraded research

Both full-pipeline entry points run a deterministic gate after analysis. It checks
selected analyst reports and source-backed handoffs, required news/social coverage,
usable market/fundamental facts, completed research and risk debates, manager/trader
outputs, and an explicit unambiguous final rating. Invalid or unavailable required
evidence makes the result **degraded**. Diagnostic reports remain available.
This gate adds no model or provider requests and does not change profiles or rounds.

`propagate()` returns `REVIEW` for degraded runs and omits their decision from
reflection memory. The CLI displays the status, and `complete_report.md` plus
`run_metadata.json.research_quality` expose the same acceptance result. Consumers
must require `accepted == true` and use the gated `signal`; do not extract an order
from raw diagnostic decision prose. Calling the low-level LangGraph directly or
`process_signal()` only bypasses this finalization and is not an acceptance check.

An accepted result means structural research checks passed. It does not establish
factual accuracy, point-in-time data completeness, suitability, profitability, or
permission to trade. Known data caveats still need human review. The project has
no scheduler, broker order submission, paper-trading ledger, or live approval flow.

### Next manual acceptance

Run one non-AMD ticker through the full Codex workflow with Balanced unchanged:

```sh
tradingagents --backend codex --checkpoint
```

Select the ticker/date and Balanced, then save the report. Review the acceptance
status/reasons, required evidence and citations, Trader and Portfolio Manager
consistency, and per-role timing/usage. A degraded result is diagnostic evidence
for investigation, not a successful acceptance run. This is a real subscription
and data-provider run; offline tests and clean-install checks do not invoke it.
The existing AMD API runs remain comparison evidence; another paid API run is
not required for this next acceptance step. Cross-ticker quality acceptance stays
pending until that report is reviewed.
