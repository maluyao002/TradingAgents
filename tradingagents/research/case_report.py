"""Opt-in case-reader structure; coverage is not a claim of semantic quality."""

from typing import Literal, get_args

from pydantic import model_validator

from .stages import ReportDraft, ReportSection

SectionPurpose = Literal[
    "executive_thesis", "central_disagreement", "business_financial_drivers",
    "management", "scenarios", "counter_case", "falsifiers", "material_gaps",
]
SECTION_PURPOSES = get_args(SectionPurpose)


class CaseReportSection(ReportSection):
    purpose: SectionPurpose


class CaseReportDraft(ReportDraft):
    sections: tuple[CaseReportSection, ...]

    @model_validator(mode="after")
    def complete_structure(self):
        purposes = [section.purpose for section in self.sections]
        if set(purposes) != set(SECTION_PURPOSES) or len(purposes) != len(SECTION_PURPOSES):
            raise ValueError("case reader requires exactly one section for each analytical purpose")
        if self.investment_view != "unrated":
            raise ValueError("case reader remains unrated while production acceptance is disabled")
        return self


CASE_READER_REQUIREMENTS = (
    "Deliver each required analytical purpose exactly once; headings may be human-readable. "
    "Make the executive thesis and central disagreement explicit, connect business drivers "
    "to cash economics, evaluate management, and set out scenarios, counter-case, observable "
    "falsifiers and financially material gaps. Scenario narratives may be qualitative when "
    "the supplied case does not support numbers; never manufacture a forecast, target or "
    "implied return. Describe missing evidence honestly instead of filling a section with "
    "unsupported conclusions. Remain unrated. Financial-case review is not clearance for "
    "valuation. Section coverage is checked by code, but factual and causal substance must "
    "still pass the independent verifier against the exact rendered report."
    " Prioritize the one disagreement most capable of changing the thesis rather than "
    "presenting equally weighted essays. State each major causal judgment as evidence, "
    "inference, uncertainty and disconfirmation. Evaluate management incentives, allocation "
    "and guidance credibility without overstating small samples. Make the material-gaps section "
    "a short, ranked list of investment-relevant unknowns and caveats; do not omit any material "
    "caveat, but keep provider logs, reviewer workflow, historical-warning discussion and other "
    "operational audit detail in the audit. Specify at least two observable falsifiers "
    "and what evidence would change the conclusion; do not invent numerical trigger thresholds."
    " When reviewed operating scenarios are supplied, use their {{calc:ID}} references for "
    "scenario numbers and explain the fiscal actual-plus-assumption bridge. For reviewed "
    "operating-scenario fiscal totals, put {{scenario_table}} on its own line to render one "
    "compact, source-backed table; do not recreate the same rows in prose. These are "
    "conditional operating calculations, never cash flow, funding adequacy or equity value. Use "
    "only {{calc:ID}} and {{scenario_table}} markers for calculation presentation; never handwrite "
    "a model-appendix provenance link, because the renderer generates its analyst-calculation links. "
    "Briefly classify the assumptions and conditional nature in prose, never cite an issuer as though "
    "it reported the analyst calculation, and cite underlying reported inputs separately where stated. "
    "Forecast source-material IDs name appendix passages only; for response evidence_ids "
    "and source_ids use their underlying source_id or an eligible frozen financial-fact ID. "
    "Use an explicit [source_id] or [fact_id] link in every paragraph or table row containing a "
    "supported factual statement, placing it only where it actually supports the statement. Do not "
    "add issuer-source links to calculation-only text, and do not repeat a "
    "material caveat once it is clearly stated in the material-gaps section."
)
