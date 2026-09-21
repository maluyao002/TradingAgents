"""Code-owned authoring-to-rendered bindings; not a semantic verification pass."""

import re
from hashlib import sha256
from urllib.parse import quote

from .calculated_values import render_calculations
from .rendering import render_references
from .storage import digest

RENDERED_READER_POLICY = (
    "This is a rendered-reader review, not an authoring task. Fact/calculation markers "
    "and the standalone scenario_table marker are authoring syntax, expanded by code "
    "before you receive the draft/reader. Their absence in rendered prose is expected, "
    "not evidence of manual numerical entry. Inspect rendering_provenance for exact "
    "authored markers, expansion text, calculation IDs and hashes. Links to the model "
    "appendix identify analyst calculations, not issuer-reported results or proof of "
    "assumptions. Check numerical correctness, causal support, source/assumption scope, "
    "and local citations independently. These bindings do not clear financial gates."
)
_MARKER = re.compile(r"\{\{(?:fact:[^{}]+|calc:[^{}]+|scenario_table)\}\}")


def reader_provenance(authored, prepared, facts, calculations, language, reader, *, cite=False):
    """Recompute every expansion; reject edited prose/metadata or fake calc links."""
    expected = authored.model_copy(update={"sections": tuple(
        section.model_copy(update={"text": render_calculations(
            render_references(section.text, facts, language), calculations, language, cite=cite
        )}) for section in authored.sections
    )})
    if expected != prepared:
        raise ValueError("authored-to-rendered draft binding mismatch")
    records = []
    for index, section in enumerate(authored.sections, 1):
        if "model_appendix.md#calculation-" in section.text:
            raise ValueError("calculation provenance links must be generated from markers")
        blocks = []
        for block_index, block in enumerate(section.text.split("\n\n"), 1):
            bindings = []
            for match in _MARKER.finditer(block):
                marker = match.group()
                if marker == "{{scenario_table}}" and block.strip() != marker:
                    raise ValueError("scenario table marker must occupy its own paragraph")
                expansion = render_calculations(render_references(marker, facts, language),
                                                calculations, language, cite=cite)
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
    return {"schema_version": 1, "calculation_citations": cite, "authored_draft_sha256": digest(authored),
            "prepared_draft_sha256": digest(prepared),
            "reader_sha256": sha256(reader.encode()).hexdigest(),
            "calculation_catalog_sha256": digest(calculations),
            "binding_kind": "deterministic_expansion_not_semantic_acceptance", "sections": records}


def calculation_appendix(calculations, language):
    """Stable local link targets, retaining classification and package bindings."""
    lines = ["\n## Calculation provenance\n",
             "Analyst/model calculations, not new reported facts or financial clearance. "
             "Exact inputs and source ancestry are in calculated_values.json.\n"]
    for item in calculations:
        lines.extend([f'<a id="calculation-{quote(item.id, safe="._-")}"></a>',
                      f"### {item.id}\n",
                      render_calculations("{{calc:" + item.id + "}}", calculations, language),
                      f"\nClassification: {item.classification}",
                      f"\nInput/package SHA-256: `{item.model_input_sha256}`",
                      f"\nResult SHA-256: `{item.model_result_sha256}`",
                      "\nEvidence IDs (ancestry, not proof of assumptions): " + ", ".join(item.evidence_ids), ""])
    return "\n".join(lines).encode()
