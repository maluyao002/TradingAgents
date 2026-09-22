"""Shared, exact model payloads for bounded reader-limitation coverage."""

from hashlib import sha256

from .coverage_policy import packed_issue_context
from .report_review import ReaderVerification
from .stages import instruction

LIMITATION_POLICY = (
    "For EVERY limitation issue ID return one disposition. "
    "Material financial/research caveats must be reader_covered with an exact "
    "excerpt showing the caveat in the rendered reader. Use reader_excerpts for "
    "multiple separately exact passages; never join passages with ellipses. "
    "Copy each excerpt byte-for-byte from rendered_reader, preserving case, "
    "punctuation and Markdown. In particular, do not capitalize a sentence fragment "
    "when the reader uses lowercase after an uncertainty label. If no literal "
    "supporting passage exists, return unresolved rather than a paraphrased quote. "
    "Audit-only is allowed "
    "only for genuinely operational or immaterial details, with a specific "
    "rationale. Never classify a financially material unknown as immaterial "
    "to improve readability. reader_coverage_required issues must be reader_covered "
    "with exact spans, never audit-only. Return unresolved when it is missing."
)

_COVERAGE_QUALITY_REQUIREMENTS = {
    "revision": "evidence-led-bounded-1",
    "retrieval": "Lexical matches are leads, not proof or closure of a question.",
    "reader": "Write a thesis-led report with a causal financial bridge and "
              "explicit disconfirming evidence. Consolidate genuinely related "
              "material limitations into concise authored prose; retain all "
              "financially consequential uncertainty. Operational logs belong "
              "in the audit, not the reader. Do not hide missing valuation. "
              "For authoring, use {{calc:ID}} for supplied code-calculated values, "
              "never retype or recompute them. For rendered-reader verification, "
              "those markers have already been expanded by code: inspect the "
              "rendering_provenance bindings instead of demanding visible markers. "
              "Calculations are assumptions-based, not reported facts.",
    "verification": "When rendered_reader is supplied, review that exact "
                    "export, not only the intermediate draft. Repair only with "
                    "supplied evidence; no new facts or unsupported calculations.",
}

_COVERAGE_REVIEW_SCOPE = (
    "Underlying claims and prior findings are supplied as context, not proof. "
    "Missing claim context must remain unresolved. Only assess each supplied issue's "
    "materiality and coverage in the exact rendered text. Do not adjudicate factual "
    "claim IDs or imply source verification. Set reviewed_report only if you "
    "performed this coverage review. Each distinct issue needs its own justified "
    "disposition; shared prose does not automatically cover every issue. Unknown "
    "materiality must remain unresolved. shared_issue_context_ref refers to the exact "
    "field-bound value in shared_issue_context; read it as part of that issue, not as "
    "evidence of resolution. equivalent_issue_ids are code-checked identical obligations "
    "and context; return one disposition for the supplied issue_id, which will be mapped "
    "back to every original obligation without dropping any."
)


def coverage_review_data(items, reader, limitation_policy=LIMITATION_POLICY):
    """Create the lossless coverage-review research packet."""
    return {
        "rendered_reader": reader,
        "rendered_reader_sha256": sha256(reader.encode("utf-8")).hexdigest(),
        **packed_issue_context(items),
        "limitation_policy": limitation_policy,
        "review_scope": _COVERAGE_REVIEW_SCOPE,
    }


def coverage_model_payload(request, data, stage, role_call_index, language):
    """Build the verifier payload from an already-formed coverage packet."""
    payload = {
        **instruction("verifier", ReaderVerification),
        "evidence": {
            "scope": "Reader limitation coverage only; factual verification is a separate mandatory stage."
        },
        "research": data,
        "cutoff": request.cutoff.isoformat(),
        "mandate": request.mandate,
        "stage": stage,
        "role_call_index": role_call_index,
        "valuation_months": request.valuation_months,
        "return_months": request.return_months,
        "language": language or request.internal_language,
    }
    if request.coverage_batch_policy != "legacy-12":
        payload["coverage_batch_policy"] = request.coverage_batch_policy
    payload["system"] += (
        " This is a limitation-coverage-only call, not source verification. "
        "Assess every supplied issue against the exact reader text. Leave factual "
        "claim-ID decisions empty; a separate mandatory full-context call checks facts. "
        "Keep each disposition rationale concise without omitting its reasoning."
    )
    payload["quality_requirements"] = dict(_COVERAGE_QUALITY_REQUIREMENTS)
    return payload


def build_coverage_payload(request, items, reader, *, stage="verify_report-coverage-0",
                           role_call_index=0, language="English"):
    """Build the full, standalone bounded reader-coverage payload."""
    return coverage_model_payload(
        request,
        coverage_review_data(items, reader),
        stage,
        role_call_index,
        language,
    )
