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

The core backport introduced `checkpoint-config-v3`; the SEC statement routing
and preparation changes advance it to `checkpoint-config-v4`. Runs created under the old
identity start fresh when execution semantics differ; existing checkpoint files
and historical reports are preserved.

## SEC EDGAR statements

The SEC vendor adapts upstream `f881c4a`, `c78fa86`, and `a9cc3be` (as-filed
statements, capital-expenditure tag aliases, and fiscal-year selection).
Its structured payload retains the original values and units, period dates,
filing dates, accession numbers, and XBRL tags. Prepared facts consume these
values directly; rounded display tables are not calculation inputs. Derived
facts receive a publication date only when every dependency has one.
Graph, CLI, and preparation entry points require canonical `YYYY-MM-DD` dates
before any live lookup, so ambiguous date strings cannot bypass historical guards.

Set `SEC_EDGAR_USER_AGENT` to your name and real contact email, then opt in for
the three statement tools:

```python
from copy import deepcopy
from tradingagents.default_config import DEFAULT_CONFIG

config = deepcopy(DEFAULT_CONFIG)
config["tool_vendors"].update({
    "get_balance_sheet": "sec_edgar,yfinance",
    "get_cashflow": "sec_edgar,yfinance",
    "get_income_statement": "sec_edgar,yfinance",
})
```

Pass this configuration to `TradingAgentsGraph`, or to the fundamentals pilot's
`run_fundamentals(..., config=config)` entry point. The CLI can use the same
tool overrides in its default configuration. The SEC provider requires no API
key. Its cached public responses use the configured data cache directory.

For a past analysis date, statements can be fetched only when the configured
vendor chain includes SEC. The shipped Yahoo-only configuration does not opt in;
the existing `default` vendor sentinel includes all available vendors, including
SEC. Filings and amendments after that date are excluded. Yahoo and Alpha
Vantage statement fallbacks are withheld even when SEC is unavailable; their
fiscal period dates do not establish publication dates. Current-day calls keep
the configured vendor order. Historical company profiles remain withheld.

The SEC ticker-to-CIK map is current. It identifies the issuer selected by the
requested ticker today; it does not prove the ticker belonged to that issuer
on a historical date. This limitation is carried in prepared evidence. SEC
facts may omit company-specific tags or standalone quarterly cash-flow rows;
missing rows remain unavailable rather than being estimated from YTD totals.

This shared data path is available on the research branch after synchronization.
It serves the graph and fundamentals pilot; it is not an adapter into V2's
separate accession-bound evidence collector. A date-only SEC `filed` field
does not establish V2's precise filing acceptance timestamp.
Existing frozen Deep Research V2 source packets retain their original bytes,
provenance, and review requirements; new vendor availability does not refresh
or approve those packets.

## Validation

Use offline fixtures and mocked API/Codex adapters for regressions covering:

- Ambiguous, missing, localized, and formatted ratings across the research
  gate, returned signal, and newly written decision log.
- Interleaved graph configurations, nested contexts, failure cleanup, CLI
  streaming, fundamentals preparation, and graph settlement.
- Omitted or future model dates, reversed windows, and protected arguments
  injected through both normal LangGraph tools and the Codex bridge.
- SEC filing cutoffs and amendments, annual versus quarterly periods, exact
  values and units, tag aliases, typed provider failures, and cache reuse.
- Historical vendor fallback restrictions and prepared facts' publication
  dates, including derived values whose dependencies were filed separately.

Run the repository CI checks before merging: the supported Python test matrix,
lint, a clean locked installation, and the container privacy/import checks.
