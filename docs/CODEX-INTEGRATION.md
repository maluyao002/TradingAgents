# Codex subscription integration

This development uses the official local Codex app-server protocol. The existing
API implementation remains the baseline. The integration is not enabled by the
stage 1 compatibility probe.

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
below remain pending. Stage 2 is merged; stage 3 is in development; stages 4–5 have not started.

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
interruption after malformed responses. No live research run has been performed.
The GitHub hooks finding is also addressed: startup disables lifecycle and plugin
hooks, and inference requires verified disabled hook features and explicitly empty
effective hook configuration, including configuration inherited from system layers.

At startup, choose **API** for the existing research workflow or **Codex subscription**
for the setup preview. You can skip this new picker with `tradingagents --backend api`
or `tradingagents --backend codex`. `python -m cli.main` accepts the same options.
`TRADINGAGENTS_BACKEND` supplies a default backend when no flag is given; a flag wins.
The API provider, profile, key, checkpoint, and model environment settings keep their
existing behavior. Programmatic `TradingAgentsGraph` callers continue to use the API.

The Codex preview checks ChatGPT sign-in and reads the live model catalog. Quick,
Balanced, and Deep profiles are available only when every research role's exact
model and effort are advertised. Unsupported profiles are disabled and show the
affected roles. Custom checks one advertised model/effort pair. This is a capability
check, not proof of model entitlement, remaining quota, or research quality. The
preview does not run inference, fetch market data, save model settings, or fall back
to an API provider. API checkpoint flags are rejected on this path before any deletion.

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

Implemented in [PR #5](https://github.com/maluyao002/TradingAgents/pull/5),
awaiting user approval. Branch: `feat/codex-stage-3`, based on merged stage 2. This pilot
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
stage. Live matched comparison remains pending the user's run. A fresh independent
read-only review found one portability issue: locale-dependent artifact encoding.
All artifact reads/writes now use explicit UTF-8, verified by a Unicode round-trip
test with UTF-8 mode disabled and the C locale. No other actionable findings were reported.

## Sources

- [Official app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [Official authentication](https://learn.chatgpt.com/docs/auth)
