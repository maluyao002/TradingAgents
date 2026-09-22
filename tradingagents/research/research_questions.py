"""Decision-led work planning, deliberately separate from issue resolution.

Grouping questions cannot retire warnings, prove equivalence, or shrink verifier
coverage. A question may depend on several issues, and an issue on several
questions. Unassigned issues remain visible with their complete source context.
"""

from copy import deepcopy
from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, Identifier
from .storage import digest

QUESTION_LED_REQUIREMENTS = {
    "policy": "decision-led-research-v1",
    "judgment": [
        "Lead each material section with a supported judgment, not an inventory of unknowns.",
        "Separate observed evidence, analyst inference, confidence and the strongest alternative explanation.",
        "Explain the causal link from business driver to margin, reinvestment and cash conversion.",
        "State what observable evidence would overturn the judgment; do not invent numeric triggers.",
    ],
    "unknowns": {
        "missing_evidence": "Name the exact missing passage/data and affected conclusion; search public primary sources first.",
        "unfinished_analysis": "Name the calculation or integration required; missing analysis is not unavailable evidence.",
        "potentially_undisclosed": "Record sources searched and dates before declaring a public-data boundary; use labeled proxies only.",
        "future_uncertainty": "Use explicit conditional assumptions and sensitivities, not a demand for unknowable future facts.",
        "review_workflow": "Keep audit mechanics outside the reader except for their investment consequence.",
    },
    "scope": "Keep operating cash flow, operating value, equity/per-share value and funding adequacy separate. "
             "Use only supplied eligible calculations; a conditional illustration is not acceptance or a price target.",
    "reader": "Rank a short set of decision-relevant questions. Consolidate repetition without omitting material "
              "qualifications. A work-package grouping is not a lifecycle disposition or coverage waiver.",
    "verification": "Check judgment, alternative, confidence, causal support and falsifier against the exact reader. "
                    "Instruction compliance or a factual pass alone does not certify financial completeness.",
}


class DecisionQuestion(Contract):
    id: Identifier
    priority: int = Field(ge=1, le=5)
    question: str = Field(min_length=1)
    kinds: tuple[Literal["missing_evidence", "unfinished_analysis", "potentially_undisclosed",
                         "future_uncertainty", "review_workflow"], ...] = Field(min_length=1)
    owner: str = Field(min_length=1)
    affected_conclusions: tuple[str, ...] = Field(min_length=1)
    required_work: tuple[str, ...] = Field(min_length=1)
    completion_criteria: tuple[str, ...] = Field(min_length=1)
    bounded_search: str = Field(min_length=1)
    fallback: str = Field(min_length=1)
    issue_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("issue_ids", "kinds")
    @classmethod
    def unique_items(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("question members must be unique")
        return values

    @field_validator("question", "owner", "bounded_search", "fallback")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("question fields cannot be blank")
        return value

    @field_validator("affected_conclusions", "required_work", "completion_criteria")
    @classmethod
    def nonblank_entries(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("question entries cannot be blank")
        return values


def question_register(issues, questions, *, analyst, source_identity):
    """Bind an explicit analyst-authored plan to a lossless inventory, fail closed.

    No lexical classification, automatic closure, priority-based filtering, or
    claim that this plan establishes source truth is performed here.
    """
    if not analyst.strip() or not source_identity.strip():
        raise ValueError("analyst and source identity are required")
    inventory = deepcopy(list(issues))
    ids = [item["issue_id"] for item in inventory]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate inventory issue ID")
    definitions = [DecisionQuestion.model_validate(item) for item in questions]
    if len({item.id for item in definitions}) != len(definitions):
        raise ValueError("duplicate decision question ID")
    assigned = {key for item in definitions for key in item.issue_ids}
    if assigned - set(ids):
        raise ValueError("question references an issue outside the bound inventory")
    return {
        "schema_version": 1,
        "policy": "decision-question-register-v1",
        "meaning": "analyst_work_plan_not_issue_resolution_or_report_acceptance",
        "analyst": analyst,
        "source_identity": source_identity,
        "inventory_sha256": digest(inventory),
        "inventory": inventory,
        "questions": [item.model_dump(mode="json") for item in sorted(definitions, key=lambda x: (x.priority, x.id))],
        "unassigned_issue_ids": [key for key in ids if key not in assigned],
        "original_issue_count": len(inventory),
        "assigned_issue_count": len(assigned),
        "question_count": len(definitions),
        "resolved_by_this_plan": 0,
    }


def render_question_register(register):
    """Human work index; full issue context remains in the sibling JSON audit."""
    lines = ["# Decision-led research work register", "",
             f"{register['original_issue_count']} retained issue records; {register['question_count']} "
             f"work packages; {len(register['unassigned_issue_ids'])} unassigned records.", "",
             "This is a work plan, not evidence of resolution, semantic equivalence or acceptance.", ""]
    for item in register["questions"]:
        lines.extend([f"## P{item['priority']} — {item['question']}", "",
                      f"Owner: {item['owner']}. Types: {', '.join(item['kinds'])}.", "",
                      "Affected conclusions: " + "; ".join(item["affected_conclusions"]) + ".", "",
                      "Required work:", "", *[f"- {text}" for text in item["required_work"]], "",
                      "Done when:", "", *[f"- {text}" for text in item["completion_criteria"]], "",
                      "Search boundary: " + item["bounded_search"], "",
                      "If not disclosed: " + item["fallback"], "",
                      f"Audit linkage: {item['id']}, {len(item['issue_ids'])} issue records.", ""])
    if register["unassigned_issue_ids"]:
        lines.extend(["## Unassigned — triage remains required", "",
                      *[f"- {key}" for key in register["unassigned_issue_ids"]], ""])
    return "\n".join(lines)
