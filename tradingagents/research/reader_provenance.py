"""Code-owned authoring-to-rendered bindings; not a semantic verification pass."""

import re
from dataclasses import asdict
from hashlib import sha256
from html import escape, unescape
from urllib.parse import unquote

from .calculated_values import calculation_anchor_id, render_calculations
from .case_report import case_reader_delivery
from .reader import ReaderIssue, render_reader
from .rendering import render_references
from .storage import digest

RENDERED_READER_POLICY = (
    "This is a rendered-reader review, not an authoring task. Fact/calculation markers "
    "and standalone scenario_table/scenario_assumptions_table markers are authoring syntax, expanded by code "
    "before you receive the draft/reader. Their absence in rendered prose is expected, "
    "not evidence of manual numerical entry. Inspect rendering_provenance for exact "
    "authored markers, expansion text, calculation IDs and hashes. Links to the model "
    "appendix identify analyst calculations, not issuer-reported results or proof of "
    "assumptions. Check numerical correctness, causal support, source/assumption scope, "
    "and local citations independently. These bindings do not clear financial gates. "
    "Code-owned review-status disclosures describe validated case state, not issuer facts. "
    "Check the entire authored narrative for contradictions with those disclosures; their "
    "presence does not excuse claims that an unreviewed financial case or absent cash-flow "
    "model has been approved. Fiscal operating scenarios are not a calendar cash-flow model."
)
_MARKER = re.compile(r"\{\{(?:fact:[^{}]+|calc:[^{}]+|scenario_table|scenario_assumptions_table)\}\}")


def _reject_authored_calculation_links(value):
    """Reserve renderer-owned destinations across all author-controlled fields."""
    if isinstance(value, dict):
        for item in value.values():
            _reject_authored_calculation_links(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_authored_calculation_links(item)
    elif isinstance(value, str):
        decoded = unquote(unescape(re.sub(r"\\(.)", r"\1", value)))
        if re.search(r"model_appendix\.md(?:\?[^\s#]*)?#calculation-", decoded, re.IGNORECASE):
            raise ValueError("calculation provenance links must be generated from markers")


def reader_provenance(authored, prepared, facts, calculations, language, reader, *, cite=False,
                      request=None, snapshot=None, issues=(), case_context=None, bind_case_state=False,
                      bind_cashflow_inputs=False):
    """Recompute every expansion; reject edited prose/metadata or fake calc links."""
    issues = tuple(issues)
    delivery = case_reader_delivery(case_context) if case_context is not None else None
    _reject_authored_calculation_links(authored.model_dump(mode="json"))
    for issue in issues:
        _reject_authored_calculation_links(asdict(issue) if isinstance(issue, ReaderIssue) else issue)
    expected = authored.model_copy(update={"sections": tuple(
        section.model_copy(update={"text": render_calculations(
            render_references(section.text, facts, language), calculations, language, cite=cite,
            scenario_delivery=delivery,
        )}) for section in authored.sections
    )})
    if expected != prepared:
        raise ValueError("authored-to-rendered draft binding mismatch")
    records = []
    for index, section in enumerate(authored.sections, 1):
        blocks = []
        for block_index, block in enumerate(section.text.split("\n\n"), 1):
            bindings = []
            for match in _MARKER.finditer(block):
                marker = match.group()
                if marker in {"{{scenario_table}}", "{{scenario_assumptions_table}}"} and block.strip() != marker:
                    raise ValueError("scenario table marker must occupy its own paragraph")
                expansion = render_calculations(render_references(marker, facts, language),
                                                calculations, language, cite=cite, scenario_delivery=delivery)
                ids = ([marker[len("{{calc:"):-2]] if marker.startswith("{{calc:") else
                       [item.id for item in calculations if item.valuation_method == "operating_scenario"
                        and re.fullmatch(r"operating_scenario\..+\.fiscal_total\.(revenue|operating_income)",
                                         item.id)] if marker == "{{scenario_table}}" else [])
                bindings.append({"authored_marker": marker, "authored_offset": match.start(),
                                 "expanded_text": expansion, "calculation_ids": ids})
            if bindings:
                blocks.append({"paragraph_index": block_index, "authored_text": block,
                               "bindings": bindings})
        records.append({"section_index": index, "authored_section_sha256": digest(section),
                        "prepared_section_sha256": digest(prepared.sections[index - 1]),
                        "paragraphs": blocks})
    if request is None or snapshot is None:
        raise ValueError("reader binding requires exact rendering context")
    rerendered = render_reader(request, prepared, snapshot, issues, language, compact=cite,
                              case_context=case_context, bind_case_state=bind_case_state,
                              bind_cashflow_inputs=bind_cashflow_inputs)
    if rerendered.reader_text != reader:
        raise ValueError("prepared-to-reader binding mismatch")
    encoded_issues = []
    for issue in issues:
        if isinstance(issue, str):
            encoded_issues.append({"kind": "text", "value": issue})
        elif isinstance(issue, ReaderIssue):
            encoded_issues.append({"kind": "reader_issue", "value": asdict(issue)})
        else:
            raise ValueError("unsupported provenance rendering issue")
    rendering_inputs = {"issues": encoded_issues, "snapshot_sha256": digest(snapshot),
                        "facts_sha256": digest(facts), "language": language, "compact": cite,
                        "ticker": request.ticker, "cutoff": request.cutoff.isoformat()}
    if delivery is not None:
        rendering_inputs.update(case_context_sha256=digest(case_context.model_context()),
                                case_reader_delivery_sha256=digest(delivery))
    if bind_case_state:
        from .review_disclosures import DISCLOSURE_POLICY
        rendering_inputs.update(review_disclosure_policy=DISCLOSURE_POLICY,
            case_review_disclosure=rerendered.limitations_audit["case_review_disclosure"])
    if bind_cashflow_inputs:
        rendering_inputs["cashflow_assumptions_policy"] = "reviewed-inputs-table-v1"
    return {"schema_version": 1, "calculation_citations": cite, "authored_draft_sha256": digest(authored),
            "prepared_draft_sha256": digest(prepared),
            "reader_sha256": sha256(reader.encode()).hexdigest(),
            "calculation_catalog_sha256": digest(calculations),
            "rendering_inputs": rendering_inputs, "rendering_inputs_sha256": digest(rendering_inputs),
            "binding_kind": "deterministic_expansion_not_semantic_acceptance", "sections": records}


def calculation_appendix(calculations, language):
    """Stable local link targets, retaining classification and package bindings."""
    lines = ["\n## Calculation provenance\n",
             "Analyst/model calculations, not new reported facts or financial clearance. "
             "The hash-bound value catalog is calculated_values.json. Exact inputs are in "
             "operating_scenario_package.json or valuation_inputs.json as applicable; "
             "evidence.json supplies source ancestry.\n"]
    for item in calculations:
        lines.extend([f'<a id="{escape(calculation_anchor_id(item.id), quote=True)}"></a>',
                      f"### {item.id}\n",
                      render_calculations("{{calc:" + item.id + "}}", calculations, language),
                      f"\nClassification: {item.classification}",
                      f"\nInput/package SHA-256: `{item.model_input_sha256}`",
                      f"\nResult SHA-256: `{item.model_result_sha256}`",
                      "\nEvidence IDs (ancestry, not proof of assumptions): " + ", ".join(item.evidence_ids), ""])
    return "\n".join(lines).encode()


def case_model_appendix(calculations, language, *, has_operating_scenarios=False,
                        has_cashflow_bridge=False):
    """The complete code-owned case appendix, shared by export and preview QA."""
    result = (
        b"# Financial model appendix\n\n"
        b"No supported operating valuation, equity target or funding conclusion is exported. "
        b"Financial schedules are not yet bound to a complete reviewed cash-flow valuation. No unconstrained "
        b"valuation-model call was dispatched in this case-backed workflow.\n\n"
        b"See financial_case.json, financial_reconciliation.json and case_context.json "
        b"for source-bound schedules, conventions, review status and unresolved prerequisites. "
        b"If case validation failed, those validated artifacts are absent; case_input.json "
        b"is retained only as the unvalidated input.\n\n"
        b"Report completion, conditional analytical eligibility and acceptance prerequisites "
        b"are recorded separately in report_admission.json. Production activation is disabled.\n"
    )
    if has_operating_scenarios:
        result += (
            b"\n## Fiscal operating scenarios\n\n"
            b"See operating_scenario_package.json and operating_scenario_context.json for "
            b"the historical anchors, dated fiscal assumptions, source passages and computed "
            b"revenue/gross-profit/operating-income bridge. Only independently reviewed "
            b"packages expose calculated references in the reader; draft or stale reviews "
            b"withhold these numbers. Operating profit is not cash flow or funding clearance.\n"
        )
    if has_cashflow_bridge:
        result += (
            b"\n## Conditional fiscal cash-flow bridge\n\n"
            b"See cashflow_bridge_package.json, cashflow_bridge_context.json and "
            b"cashflow_bridge_result.json for reported anchors, explicit analyst tax, D&A, "
            b"capex and working-capital assumptions, and commitment-overlap treatments. "
            b"Only a separately reviewed package exposes reader calculation references. "
            b"Cash flows use stated fiscal dates; there is no implied interim roll-forward, "
            b"economic approval, valuation, equity bridge or funding clearance.\n"
        )
    if calculations:
        result += calculation_appendix(calculations, language)
    return result
