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
    "and guidance credibility without overstating small samples. Rank the most consequential "
    "gaps without omitting any material caveat. Specify at least two observable falsifiers "
    "and what evidence would change the conclusion; do not invent numerical trigger thresholds."
)
