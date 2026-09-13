# TradingAgents model profile specifications

**Default profile: Balanced.**

These profiles are implemented in the local TradingAgents checkout. With the OpenAI provider selected, the CLI offers Quick, Balanced (default), Deep, and Custom. Named profiles configure each agent’s model and reasoning effort and both debate limits. Custom retains the original model and depth controls; other providers use that original flow.

## Agent assignments

Each cell specifies the model and its reasoning effort. Profiles cover all four optional analysts; the analyst selection controls which ones run.

| Agent | Quick | Balanced — default | Deep |
|---|---|---|---|
| Market / technical analyst | Luna · Low | Terra · Medium | Terra · High |
| Fundamentals analyst | Terra · Medium | Sol · High | Sol · High |
| News / macro analyst | Luna · Low | Terra · Medium | Terra · High |
| Sentiment analyst | Luna · Low | Terra · Medium | Terra · High |
| Bull researcher | Luna · Low | Terra · Medium | Sol · Medium |
| Bear researcher | Luna · Low | Terra · Medium | Sol · Medium |
| Research manager | Terra · Medium | Sol · High | Astra · High |
| Trader | Terra · Medium | Sol · Medium | Sol · High |
| Aggressive risk analyst | Luna · Low | Terra · Medium | Sol · Medium |
| Conservative risk analyst | Luna · Low | Terra · Medium | Sol · Medium |
| Neutral risk analyst | Luna · Low | Terra · Medium | Sol · Medium |
| Portfolio manager | Terra · Medium | Astra · High | Astra · High |

## Research depth

Quick, Balanced, and Deep use 1, 2, and 3 rounds respectively for both the investment debate and risk discussion. Explicit environment round overrides take precedence and are shown in the CLI summary.

| Setting | Quick | Balanced — default | Deep |
|---|---|---|---|
| Custom-mode research-depth equivalent | Shallow | 2 rounds (no menu equivalent) | Medium |
| `max_debate_rounds` | 1 | 2 | 3 |
| `max_risk_discuss_rounds` | 1 | 2 | 3 |
| Intended use | Fast initial screening | Routine substantive analysis | Difficult or conflicting cases needing extensive review |

Reasoning effort controls thinking within an individual model call. Debate depth controls repeated contributions across agents. One round still includes the opposing investment views and the three risk perspectives; it does not mean one API call. Tool use can add further calls.

Quick retains Terra for financial interpretation and the three decision-making roles, while using Luna for bounded summaries and initial arguments. It prioritizes speed and lower cost, with less scrutiny of edge cases.

Balanced concentrates stronger models on fundamentals, synthesis, trading-plan construction, and the final decision. Both sides of the investment debate and all three risk perspectives use matching capability levels.

Deep uses Terra / High for technical, news, and sentiment analysis, where interpreting supplied evidence is relatively bounded. Sol / High handles fundamentals and trading-plan construction; Sol / Medium develops investment and risk arguments. Astra / High is reserved for the research manager and portfolio manager, which reconcile competing arguments and make integrated decisions. No role uses XHigh. These are workload-based starting hypotheses, not proven adequacy thresholds; compare quality, latency, and cost against Balanced on the same evidence.

## Exact model identifiers

| Display name | API model identifier |
|---|---|
| Astra | `gpt-6-astra` |
| Sol | `gpt-5.6-sol` |
| Terra | `gpt-5.6-terra` |
| Luna | `gpt-5.6-luna` |

The implementation uses explicit model identifiers, rather than the `gpt-5.6` alias for Sol. Effort values used by these profiles are lowercase: `low`, `medium`, `high`.

## Supporting calls and evaluation

- Final signal extraction is already deterministic in this checkout and makes no LLM call. Luna / Low is retained as the compatibility quick-client assignment; it does not add an extraction call. Unparseable decisions return REVIEW.
- Reflection, when invoked by the existing outcome-memory workflow: Terra / Medium for Quick and Balanced; Sol / High for Deep. This is outside the 12 core roles.
- Compare the profiles using the same ticker, as-of date, available evidence, and portfolio assumptions. Record wall-clock duration, token cost, tool failures, unsupported claims, and decision consistency. A single analysis does not establish which model assignment is best.
- Stronger reasoning cannot repair missing or stale data. All profiles should preserve source dates and missing-data flags; numerical risk limits and order checks belong in deterministic code.
- These profiles configure analysis only and do not add broker execution.

Model capability references: [OpenAI model comparison](https://developers.openai.com/api/docs/models/compare), [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna). These assignments are engineering recommendations, not measured trading-performance rankings.

## Running a profile

Run `tradingagents` or `python -m cli.main` from your activated environment as before. Choose OpenAI, then choose Quick, Balanced, or Deep at **OpenAI Agent Profile**. Balanced is preselected unless existing model or reasoning overrides select Custom. The profile replaces the separate model, effort, and research-depth questions. The effective profile summary appears before analysis starts.

Custom keeps the original quick/deep model controls and Shallow/Medium/Deep depth menu (1/3/5 rounds). These custom depth labels are separate from the named profiles (1/2/3 rounds). Other providers retain their existing manual selection flow.

Programmatic callers can opt into the same profiles:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.model_profiles import apply_model_profile
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = apply_model_profile(DEFAULT_CONFIG, "balanced")
graph = TradingAgentsGraph(config=config)
# Call graph.propagate(...) only when ready to run an analysis.
```

`apply_model_profile` returns an independent configuration. Programmatic callers can change its round limits afterward. Existing callers that do not apply a profile retain the original quick/deep routing and defaults.
