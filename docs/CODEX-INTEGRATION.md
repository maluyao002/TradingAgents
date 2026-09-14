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

## Sources

- [Official app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [Official authentication](https://learn.chatgpt.com/docs/auth)
