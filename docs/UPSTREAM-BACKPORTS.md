# Selected upstream correctness improvements

These changes adapt selected fixes from TauricResearch/TradingAgents
[`v0.5.1`](https://github.com/TauricResearch/TradingAgents/releases/tag/v0.5.1),
commit `35543d0248bf89fcb92b17a15858ad0c0e940687`, to this fork. They do not
represent adoption of the entire upstream release. The fork retains its module
layout, Codex backend, evidence preparation, and research acceptance contracts.

## Core correctness

- Upstream `96daaf1`: bind data vendor configuration to the active run. Each
  graph, CLI run, and fundamentals pilot uses its own configuration throughout
  preparation and execution; other graphs in the process cannot replace it.
- Upstream `d04693a` and `a58aa61`: dated tools take their
  maximum date from graph state. Model arguments cannot extend the run's data
  window. The Codex bridge exposes only model-supplied arguments; LangGraph
  supplies the protected analysis date during tool execution.
  Historical context uses the ticker and analysis date, omitting live company
  identity and classification that have no verified historical vintage. A recent news feed that
  does not cover a historical window is marked unavailable and fails required
  evidence acceptance. Yahoo and Alpha Vantage insider feeds lack filing dates,
  so this fork withholds their historical rows before fetching; a transaction
  date alone cannot establish that a trade was public at the analysis date.
- Upstream `8d30fee`: unreadable decisions are recorded as `REVIEW`, and prose
  discussing multiple ratings cannot silently become the first mentioned
  rating. This fork continues to reject conflicting explicit labels, sharing
  the same parser between its research gate and decision consumers, rather
  than adopting upstream's last-label preference. Historical log entries keep
  their recorded ratings.

Checkpoint identity is now `checkpoint-config-v3`. Runs created under the old
identity start fresh when execution semantics differ; existing checkpoint files
and historical reports are preserved.

## Validation

Use offline fixtures and mocked API/Codex adapters for regressions covering:

- Ambiguous, missing, localized, and formatted ratings across the research
  gate, returned signal, and newly written decision log.
- Interleaved graph configurations, nested contexts, failure cleanup, CLI
  streaming, fundamentals preparation, and graph settlement.
- Omitted or future model dates, reversed windows, and protected arguments
  injected through both normal LangGraph tools and the Codex bridge.

Run the repository CI checks before merging: the supported Python test matrix,
lint, a clean locked installation, and the container privacy/import checks.
