"""Opt-in case-reader structure; coverage is not a claim of semantic quality."""

import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal, get_args

from pydantic import model_validator

from .stages import ReportDraft, ReportSection

SectionPurpose = Literal[
    "executive_thesis", "central_disagreement", "business_financial_drivers",
    "management", "scenarios", "counter_case", "falsifiers", "material_gaps",
]
SECTION_PURPOSES = get_args(SectionPurpose)
_SCENARIO_DECIMAL_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)


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
    "only {{calc:ID}}, {{scenario_table}} and {{scenario_assumptions_table}} markers for "
    "calculation presentation; never handwrite "
    "a model-appendix provenance link, because the renderer generates its analyst-calculation links. "
    "Briefly classify the assumptions and conditional nature in prose, never cite an issuer as though "
    "it reported the analyst calculation, and cite underlying reported inputs separately where stated. "
    "Forecast source-material IDs name appendix passages only; for response evidence_ids "
    "and source_ids use their underlying source_id or an eligible frozen financial-fact ID. "
    "Use an explicit [source_id] or [fact_id] link in every paragraph or table row containing a "
    "supported factual statement, placing it only where it actually supports the statement. Do not "
    "add issuer-source links to calculation-only text, and do not repeat a "
    "material caveat once it is clearly stated in the material-gaps section. "
    "When case_reader_delivery is supplied, state its financial-case review status separately "
    "from the operating-scenario review status and lead with the consequence: an unreviewed "
    "financial draft does not become reviewed because a conditional operating package was reviewed. "
    "The renderer supplies a concise, validated Review status and model scope block; do not "
    "duplicate it or contradict it in authored prose. An actuals-plus-guidance operating-income "
    "scenario is not a reviewed fiscal-guidance-to-calendar cash-flow or economic-underwriting "
    "model bridge. Keep that distinction explicit when discussing the scope of scenarios. "
    "When scenario_presentation is supplied, put {{scenario_assumptions_table}} on its own line "
    "once in the scenarios section. It is a compact input/provenance and analyst-calculation table: "
    "do not recreate its rows in prose, do not call calculated rates issuer-reported facts, and do "
    "not add source links that purport to prove an analyst calculation. Keep material gaps short and "
    "consequence-first; put hash, workflow, and re-review mechanics in the audit unless they change "
    "the investment consequence. Where the validated case identifies cash restrictions or unresolved "
    "legal availability, distinguish unrestricted corporate cash from restricted or customer cash and "
    "state only the conclusion the supplied inputs support. Do not infer quarterly depreciation or tax "
    "amounts from six-month observations. State, when relevant, that frozen-source review is not online "
    "authentication and arithmetic review is not an assessment of economic likelihood. Do not assert an "
    "input is absent unless the validated context or a cited source establishes that absence."
    " If a separate cashflow_bridge_delivery is supplied, its review status is independent of "
    "both the operating package and the financial schedules. Use only supplied {{calc:ID}} "
    "references for reviewed conditional cash-flow figures; unreviewed bridge results stay in "
    "the audit. Fiscal cash flows use explicit period dates and need not be translated to calendar "
    "periods unless a particular model requires it. A conditional cash-flow bridge does not "
    "complete valuation, capitalization, liquidity or economic underwriting. Explain which "
    "tax, reinvestment or commitment assumption drives the result, not merely that it is uncertain."
)


def _decimal_text(value: object) -> str:
    """Serialize a finite, code-supplied decimal without inventing display precision."""

    with localcontext(_SCENARIO_DECIMAL_CONTEXT):
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            raise ValueError("scenario delivery requires finite numeric inputs")
        return format(decimal, "f")


def _source_ids(assumption: Mapping[str, object], material: Mapping[str, object]) -> list[str]:
    """Resolve only declared source-material identifiers, preserving their order."""

    result: list[str] = []
    for material_id in assumption.get("evidence_ids", ()):  # type: ignore[union-attr]
        source_id = material.get(str(material_id), {}).get("source_id")
        if isinstance(source_id, str) and source_id not in result:
            result.append(source_id)
    return result


def _unclassified_guidance_witnesses(
    metric: str, assumption: Mapping[str, object], material: Mapping[str, object]
) -> list[dict[str, str]]:
    """Retain complete source context, never infer a period/basis/range binding.

    A lexical match is only a retrieval lead. Keep headings and trailing
    qualifications intact; numerical and temporal interpretation belongs to
    substantive review, not a prefix regex or matching input value.
    """

    if assumption.get("classification") != "management_guidance_anchor":
        return []
    metric_pattern = {"revenue": r"\brevenue\b", "gross_margin": r"\bgross\s+margins?\b"}.get(metric)
    if metric_pattern is None:
        return []
    witnesses: list[dict[str, str]] = []
    for material_id in assumption.get("evidence_ids", ()):  # type: ignore[union-attr]
        source = material.get(str(material_id), {})
        text = source.get("text")
        source_id = source.get("source_id")
        if not isinstance(text, str) or not isinstance(source_id, str):
            continue
        if (re.search(metric_pattern, text, re.IGNORECASE)
                and re.search(r"plus\s+or\s+minus|±|\+/-", text, re.IGNORECASE)):
            witness = {"source_id": source_id, "exact_excerpt": text}
            if witness not in witnesses:
                witnesses.append(witness)
    return witnesses


def _scenario_presentation(operating: Mapping[str, object]) -> dict | None:
    """Build compact, display-safe scenario inputs from the reviewed context only.

    This intentionally derives period length and normalized weekly comparisons from
    explicit period dates and supplied inputs.  It never reaches into source text,
    fills absent assumptions, or turns the resulting arithmetic into a reported fact.
    """

    if operating.get("reviewed") is not True:
        return None
    scenarios = operating.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return None
    raw_material = operating.get("source_material", ())
    material = {
        str(item["id"]): item for item in raw_material
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(raw_material, list) else {}
    rows = []
    with localcontext(_SCENARIO_DECIMAL_CONTEXT):
        for scenario in scenarios:
            if not isinstance(scenario, Mapping):
                raise ValueError("reviewed scenario delivery must contain object scenarios")
            periods = scenario.get("periods")
            if not isinstance(periods, list) or not periods:
                raise ValueError("reviewed scenario delivery must contain dated periods")
            previous_revenue_per_week: Decimal | None = None
            previous_inputs: dict[str, Decimal] | None = None
            for period in periods:
                if not isinstance(period, Mapping):
                    raise ValueError("reviewed scenario delivery must contain object periods")
                start = period.get("period_start")
                end = period.get("period_end")
                if not isinstance(start, (str, date)) or not isinstance(end, (str, date)):
                    raise ValueError("reviewed scenario delivery requires explicit period dates")
            # ISO dates are emitted by the validated model context.  Importing date
            # here would add no safety; the lexical subtraction is not used.
                start_date = date.fromisoformat(str(start))
                end_date = date.fromisoformat(str(end))
                weeks = Decimal((end_date - start_date).days + 1) / Decimal(7)
                if weeks <= 0:
                    raise ValueError("reviewed scenario delivery requires positive period length")
                period_inputs = period.get("inputs")
                if not isinstance(period_inputs, Mapping):
                    raise ValueError("reviewed scenario delivery requires declared period inputs")
                inputs = []
                by_name: dict[str, Decimal] = {}
                for name, unit in (("revenue", "USD"), ("gross_margin", "fraction"), ("opex", "USD")):
                    assumption = period_inputs.get(name)
                    if not isinstance(assumption, Mapping):
                        raise ValueError(f"reviewed scenario delivery lacks {name} input")
                    value = Decimal(_decimal_text(assumption.get("value")))
                    by_name[name] = value
                    evidence_ids = [str(item) for item in assumption.get("evidence_ids", ())]
                    inputs.append({
                        "name": name,
                        "value": _decimal_text(value),
                        "unit": unit,
                        "classification": assumption.get("classification"),
                        "source_material_ids": evidence_ids,
                        "source_ids": _source_ids(assumption, material),
                        "unclassified_guidance_witnesses": _unclassified_guidance_witnesses(
                            name, assumption, material),
                    })
                revenue_per_week = by_name["revenue"] / weeks
                comparison = None
                revenue_change = None
                opex_change = None
                if previous_revenue_per_week is not None and previous_revenue_per_week != 0:
                    comparison = revenue_per_week / previous_revenue_per_week - Decimal(1)
                if previous_inputs is not None:
                    if previous_inputs["revenue"] != 0:
                        revenue_change = by_name["revenue"] / previous_inputs["revenue"] - Decimal(1)
                    if previous_inputs["opex"] != 0:
                        opex_change = by_name["opex"] / previous_inputs["opex"] - Decimal(1)
                previous_revenue_per_week = revenue_per_week
                previous_inputs = by_name
                rows.append({
                    "scenario_id": scenario.get("id"),
                    "scenario_label": scenario.get("label"),
                    "period_id": period.get("id"),
                    "fiscal_label": period.get("fiscal_label"),
                    "period_start": start_date.isoformat(),
                    "period_end": end_date.isoformat(),
                    "inputs": inputs,
                    "analyst_calculations": {
                        "weeks": _decimal_text(weeks),
                        "revenue_per_week": _decimal_text(revenue_per_week),
                        "revenue_per_week_change_vs_previous_period": (
                            _decimal_text(comparison) if comparison is not None else None
                        ),
                        "revenue_change_vs_previous_period": (
                            _decimal_text(revenue_change) if revenue_change is not None else None
                        ),
                        "opex_change_vs_previous_period": (
                            _decimal_text(opex_change) if opex_change is not None else None
                        ),
                    },
                })
    return {
        "classification": "reviewed_conditional_inputs_and_analyst_calculations_not_reported_facts",
        "date_convention": operating.get("date_convention"),
        "rows": rows,
    }


def case_reader_delivery(case_context_or_model_context: object) -> dict:
    """Return the bounded writer contract for a ``CaseContext`` or its JSON context.

    The return value is intentionally a new compact view rather than a mutation of
    ``CaseContext.model_context()``.  Engine callers can place it beside the full
    financial-case payload and reuse the exact same object for writer preparation
    and provenance rerendering.
    """

    model_context_method = getattr(case_context_or_model_context, "model_context", None)
    context = model_context_method() if callable(model_context_method) else case_context_or_model_context
    if not isinstance(context, Mapping):
        raise TypeError("case reader delivery requires CaseContext or its model_context mapping")
    financial_status = context.get("review_status")
    if financial_status not in {"draft_unreviewed", "reviewed"}:
        raise ValueError("case reader delivery requires a validated financial review status")
    operating = context.get("operating_scenarios")
    reviewed_operating = isinstance(operating, Mapping) and operating.get("reviewed") is True
    bridge = context.get("cashflow_bridge")
    return {
        "schema_version": 1,
        "contract_kind": "offline_case_reader_delivery",
        "financial_case_review": {
            "status": financial_status,
            "reader_consequence": (
                "Financial schedules are an unreviewed draft; do not present cash-flow, valuation, "
                "funding, or equity conclusions as reviewed."
                if financial_status == "draft_unreviewed" else
                "Financial schedules have a bound independent review, not valuation or production approval."
            ),
        },
        "operating_package_review": {
            "status": "reviewed_conditional_operating_package" if reviewed_operating else "absent_or_unreviewed",
            "reader_consequence": (
                "A separately reviewed conditional operating package may support the supplied operating "
                "inputs and calculations only; it does not review or clear the financial draft."
                if reviewed_operating else
                "No reviewed operating package is available for numerical scenario presentation."
            ),
        },
        "scenario_presentation": _scenario_presentation(operating) if reviewed_operating else None,
        **({"cashflow_bridge_delivery": {
            "review_status": bridge.get("review_status"),
            "classification": "conditional_cash_flow_not_valuation_or_financial_case_approval",
            "date_convention": bridge.get("date_convention"),
            "reader_calculation_policy": "Use supplied calc references only; draft outputs remain withheld.",
            "requirements": [
                "Explain the operating-income, tax, D&A, capex, working-capital and commitment bridge.",
                "Retain proxy limitations and distinguish historical anchors from assumed forecast values.",
                "Do not infer valuation, per-share value or funding adequacy from positive cash flow.",
            ],
        }} if isinstance(bridge, Mapping) else {}),
        "writer_requirements": [
            "State the financial-draft and operating-package review statuses separately.",
            "Use {{scenario_assumptions_table}} only when scenario_presentation is present.",
            "Label source-backed inputs/provenance separately from analyst calculations.",
            "Retain supported issuer guidance ranges in concise sourced prose, checking the "
            "complete unclassified_guidance_witnesses for the correct period, currency and "
            "accounting basis. They are retrieval leads, not code-certified ranges. Do not "
            "drop material ranges during compression or infer applicability from a matching number.",
            "Keep gaps consequence-first; retain operational audit mechanics outside reader prose.",
            "Where supported by the validated context, distinguish unrestricted corporate cash "
            "from restricted or customer cash; do not infer legal availability from a balance alone.",
            "Do not derive quarterly depreciation or tax amounts from six-month observations.",
            "Keep frozen-source review versus online authentication, and arithmetic versus "
            "economic-likelihood review, as short scope boundaries.",
            "Do not call any input missing unless the validated context or cited source establishes its absence.",
        ],
    }
