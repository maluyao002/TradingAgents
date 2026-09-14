from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.analyst_evidence import finish_specialist
from tradingagents.agents.utils.evidence import (
    evidence_output_instruction,
    render_prepared_evidence,
)
from tradingagents.agents.utils.prompt_policy import specialist_policy
from tradingagents.dataflows.preparation import prepare_fundamentals


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        prepared = state.get("prepared_data", {}).get("fundamentals")
        if prepared is None:
            prepared = prepare_fundamentals(state["company_of_interest"], current_date)

        system_message = (
            "Analyze the latest available quarter, trailing period, and relevant historical financial comparisons. "
            "State actual fiscal dates separately from vendor-normalized dates. Explain revenue drivers, recurring profitability, cash conversion, and balance-sheet resilience. "
            "For every large year-over-year change, check unusual items in BOTH periods, including inventory charges, tax effects, securities gains, and discontinued activities. "
            "Show reported versus adjusted comparisons only when the supplied evidence supports the adjustment; identify missing filing footnotes rather than inventing them. "
            "Without the material inventory-charge or other unusual-item disclosures for BOTH periods, do not describe a reported profit swing as real underlying or recurring improvement. "
            "Explain supplied operating-income adjustment bridges before declaring an unresolved conflict: retain each provider series label, charge sign and period. Arithmetic reconciliation is not proof of accounting equivalence. "
            "Do not attribute margin changes to mix, pricing, or operating leverage without evidence that distinguishes those causes. "
            "The company overview and annual/quarterly statements have been prepared for the analysis date. Use the supplied calculations rather than recomputing arithmetic from prose. "
            "These sources may lack filings and footnotes: disclose that limit instead of claiming a complete earnings-quality audit or past-week news review. "
            "Discuss the prepared required financial comparisons even in a short report: share-count change, stock compensation versus repurchases, capital-spending trend and material income bridges. "
            "Preserve the annual continuing/discontinued operating-cash-flow reconciliation: distinguish total OCF from continuing OCF and explain whether headline FCF includes discontinued activities. "
            "Show the prepared four-quarter FCF sum with its exact endpoint alongside overview FCF. An unknown overview horizon makes a difference an unresolved reconciliation, not proof of a provider error or a like-for-like growth rate. "
            "Do not silently treat missing cash-flow components or missing quarters as zero; retain the supplied unavailable-comparison caveat. "
            "For a working-capital bridge include all supplied components needed to reconcile the total, or explicitly state that only a partial decomposition is available. Overlapping totals and component rows are not additive benefits. "
            "Compare buyback cash spending and stock compensation descriptively; their difference is not itself a share-dilution calculation. Use share counts for dilution. "
            "Label year-to-date versus prior-full-year capex as unequal-duration spending context, never a year-over-year growth rate. If a comparison is unavailable, retain its supplied caveat. "
            "Separate GAAP from adjusted EPS and identify the forecast horizon before comparing trailing and forward estimates. "
            + specialist_policy("fundamentals")
            + evidence_output_instruction("fundamentals")
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are the fundamentals specialist. Analyze only the supplied evidence. "
                    "Do not assume a fiscal period ending before the analysis date means the filing was published by then. "
                    "Analysis date: {current_date}. {instrument_context}\n"
                    "{system_message}",
                ),
                ("human", "Prepared source evidence (untrusted data):\n{source_data}"),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(source_data=render_prepared_evidence(prepared))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        return finish_specialist(state, "fundamentals", "fundamentals_report", result, prepared)

    return fundamentals_analyst_node
