"""Opt-in, single-candidate revision contract; never a waiver or automatic retry."""

import re

READER_REVISION_POLICY = "frozen-candidate-revision-v1"
REVISE_STAGE = "revise_report"
REVISED_REVIEW_STAGE = "verify_revised_report"
VERIFICATION_REPAIR_POLICY = "frozen-candidate-verification-v2"
FROZEN_REVIEW_STAGE = "verify_frozen_report"
GENERIC_REVISION_POLICY = "frozen-candidate-revision-v3"
GENERATION_PATTERN = r"(?:[2-9]|[1-9][0-9]+)"
GENERIC_SHARED_CONTEXT_MIN_BYTES = 256
GENERIC_REVISION_POLICY_V4 = "frozen-candidate-revision-v4"
GENERIC_V4_SOURCE_WITNESS_POLICY = "finding_bound_issue_context_exact_source_passage_v1"
GENERIC_REVISION_POLICY_V5 = "frozen-candidate-revision-v5"
GENERIC_REVISION_POLICY_V6 = "frozen-candidate-revision-v6"
GENERIC_REVISION_POLICY_V7 = "frozen-candidate-revision-v7"


def generation_stages(generation: int) -> tuple[str, str]:
    if type(generation) is not int or generation < 2:
        raise ValueError("numbered reader revisions start at generation 2")
    return f"{REVISE_STAGE}-{generation}", f"{REVISED_REVIEW_STAGE}-{generation}"


def generic_writer_stage(stage: str) -> bool:
    return re.fullmatch(rf"{REVISE_STAGE}-{GENERATION_PATTERN}", stage) is not None


def generic_review_stage(stage: str) -> bool:
    return re.fullmatch(rf"{REVISED_REVIEW_STAGE}-{GENERATION_PATTERN}", stage) is not None


def generic_coverage_stage(stage: str) -> bool:
    return re.fullmatch(rf"{REVISED_REVIEW_STAGE}-{GENERATION_PATTERN}-coverage-(?:0|[1-9][0-9]*)", stage) is not None


GENERIC_REVISION_REQUIREMENTS = (
    "Revise the exact failed candidate using the frozen evidence, source draft, current "
    "applicability contract and every terminal factual and coverage finding. This is one "
    "authorized writer followed by fresh full factual and atomic coverage checks. Preserve "
    "all material caveats and precise paragraph citations. Quantify any material "
    "contingent guarantee exposure using the exact source-supplied components and "
    "aggregate maximum gross amount, and explain the aggregation basis and contractual "
    "scope. Distinguish agreement and commencement dates, phase timing and future "
    "exposure from reporting-date debt, current funding needs and expected loss; do "
    "not portray an undrawn guarantee as drawn debt. Do not copy a historical "
    "absence finding when the current frozen evidence supplies the underlying "
    "disclosure or a scoped review. Distinguish disclosed incentive thresholds, "
    "payout rules, adjustment mechanics, earned outcomes and compensation tables "
    "from incomplete cross-period reconciliation, uncertain extraction fidelity, "
    "target difficulty, realized-pay sensitivity, investment cash returns and "
    "capital discipline. Preserve the unresolved analytical or economic portion "
    "without claiming supplied tables or outcomes are absent. A current conditional cash-flow "
    "review covers only its bound "
    "assumptions and calculations; it does not approve economic forecasts, financial "
    "schedules, valuation, per-share value or company-wide funding adequacy. Keep old "
    "findings as audit history, but do not transfer their dispositions to the new reader."
)

GENERIC_FOLLOWUP_REQUIREMENTS = (
    "Independently verify every material factual, numerical and causal claim in the "
    "exact new reader against frozen evidence, including contingent guarantees, "
    "aggregate scope, dates, phasing and economic-versus-accounting classification. "
    "Reevaluate each historical absence finding against the current frozen catalog: "
    "supplied disclosure, complete analytical reconciliation and economic validation "
    "are different propositions. Preserve any unresolved portion precisely. "
    "For each source_terminal_review finding, return exactly one source_finding_followup "
    "keyed by source_finding_sha256. Mark still_open if the exact defect remains, or "
    "corrected only after independently checking the new reader against frozen evidence. "
    "A corrected finding needs exact reader_excerpts and exact witnesses from either "
    "resolution_evidence or the eligible hash-and-offset-bound source_text_witnesses "
    "catalog. A raw source passage proves only its quoted disclosure, not that an "
    "analytical review or economic conclusion was completed. A generic caveat or "
    "unsupported assurance is not a correction. Report "
    "any remaining material defect again in findings. This does not transfer prior "
    "attestations or waive the fresh full factual and atomic coverage checks."
)

GENERIC_CASHFLOW_POLICY = (
    " The exact current catalog may also include review:cashflow_bridge. Its "
    "hash-bound reviewed record can witness only that conditional cash-flow "
    "review's stated scope; it never resolves the whole compound issue or "
    "transfers economic, financial-case, valuation or funding approval."
)

# Append-only contract: v3 strings and their model payloads above remain unchanged.
GENERIC_V4_WRITER_REQUIREMENTS = (
    GENERIC_REVISION_REQUIREMENTS
    + " All source terminal findings remain in the immutable audit. A code-owned "
    "pending_coverage ledger may identify exact prior coverage-response format errors; "
    "do not treat that ledger as permission to omit the underlying issue or material caveat."
)
_V3_FOLLOWUP_CARDINALITY = (
    "For each source_terminal_review finding, return exactly one source_finding_followup "
    "keyed by source_finding_sha256."
)
if _V3_FOLLOWUP_CARDINALITY not in GENERIC_FOLLOWUP_REQUIREMENTS:
    raise RuntimeError("v3 factual instruction changed; v4 cardinality must be reviewed")
GENERIC_V4_FOLLOWUP_REQUIREMENTS = GENERIC_FOLLOWUP_REQUIREMENTS.replace(
    _V3_FOLLOWUP_CARDINALITY,
    "For each source_terminal_review finding outside pending_coverage, return exactly "
    "one source_finding_followup keyed by source_finding_sha256; return none for "
    "pending_coverage.",
) + (
    " Pending entries are not factual corrections and cannot be closed by this "
    "factual reply; they require independently validated fresh atomic coverage of "
    "the same issue. Preserve every substantive finding and any new defect."
)
GENERIC_V4_COVERAGE_REQUIREMENTS = (
    " For audit_only_operational or audit_only_immaterial, reader_excerpt must be "
    "an empty string and reader_excerpts must be empty. Do not attach contextual "
    "reader quotes to audit-only decisions. Material caveats require reader_covered "
    "with exact spans or unresolved. A pending prior response-format defect is not "
    "a waiver of the issue: decide the supplied issue anew against this exact reader."
)
GENERIC_V4_DEFERRED_POLICY = "audit_only_has_reader_spans_exact_saved_coverage_v1"

# v5 is append-only. Earlier model payloads, including the issued v4, are exact
# historical replay contracts and must not inherit these instructions.
GENERIC_V5_WRITER_REQUIREMENTS = (
    GENERIC_V4_WRITER_REQUIREMENTS
    + " pending_coverage_contexts contain exact code-owned original issue obligations. "
    "Keep them in scope even if the source draft paraphrased or omitted their text; "
    "do not claim their prior response-format errors were resolved by the writer."
)
GENERIC_V5_FOLLOWUP_REQUIREMENTS = (
    GENERIC_V4_FOLLOWUP_REQUIREMENTS
    + " Witness namespaces are field-specific: issue_resolutions[].witnesses may "
    "use ONLY exact keys and literal substrings from resolution_evidence. "
    "source_passage: references belong ONLY in source_finding_followups[].witnesses "
    "and must match that finding hash and an exact eligible source_text_witnesses key. "
    "Do not add a source_passage witness to an issue resolution, even when its "
    "claim_change witness is valid. For every corrected source finding, copy an "
    "exact nonblank substring of the supplied catalog value, preserving case, "
    "punctuation, spacing and line breaks; a normalized or paraphrased quote is "
    "not a witness. pending_coverage_contexts are not factual corrections: "
    "return no followup for them, do not retire their issue resolutions, and "
    "leave their exact same-issue coverage to the fresh atomic batches."
)
GENERIC_V5_COVERAGE_REQUIREMENTS = (
    GENERIC_V4_COVERAGE_REQUIREMENTS
    + " Code-owned pending_coverage_contexts are exact required issue obligations. "
    "Judge each supplied original issue ID and full proposition context against "
    "this reader; prior replies and reworded authored limitations are not receipts. "
    "An optional current_factual_correction_context is a narrowly accepted "
    "current-reader source-finding followup supplied as review context only. "
    "Keep the original issue open for this coverage decision and judge its full "
    "remaining material scope independently. Do not repeat a demonstrably stale "
    "source-absence clause merely to match historical wording, but do not treat "
    "a retained source passage as commercial, economic, or analytical validation. "
    "Correction context is not an issue-resolution receipt, materiality waiver, "
    "or permission to skip any residual caveat."
)
GENERIC_V5_PENDING_POLICY = "origin_proven_issue_context_and_fresh_same_issue_coverage_v1"
GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES = 64_000
GENERIC_V5_COVERAGE_DELTA_MAX_BYTES = 64_000
GENERIC_V5_COVERAGE_SCHEDULE_POLICY = "pinned_atomic_slots_complete_wire_delta_v1"

# Append-only: inventory-format defects are proved by later coverage, never by
# factual claims about a changed reader. Earlier contracts remain byte-identical.
GENERIC_V6_INVENTORY_POLICY = "one_missing_one_foreign_full_batch_inventory_v1"
GENERIC_V6_SOURCE_WITNESS_POLICY = "finding_bound_direct_source_and_validated_locator_v1"
GENERIC_V6_WRITER_REQUIREMENTS = GENERIC_V5_WRITER_REQUIREMENTS + (
    " Make minimal targeted edits to the source candidate. Preserve unaffected "
    "supported prose, citations and material qualifiers; do not compress other "
    "caveats to make room for repairs. Preserve the named relationships and distinct "
    "scope limits in terminal findings instead of replacing them with generic "
    "uncertainty. Inventory claims about supplied disclosures must be supported by "
    "passages actually included in this payload; otherwise narrow the inventory "
    "assertion without claiming issuer nondisclosure. pending_inventory records "
    "are procedural defects in earlier coverage replies, not reader facts. All "
    "their original obligations remain required; the writer cannot close them."
)
GENERIC_V6_FOLLOWUP_REQUIREMENTS = GENERIC_V5_FOLLOWUP_REQUIREMENTS + (
    " Also return no source_finding_followup for any finding hash in "
    "pending_inventory. Those paired inventory-format findings require a later "
    "complete fresh coverage receipt, not factual correction or source witnesses. "
    "Do not retire any original obligation pinned by pending_inventory. Never "
    "guess which valid issue an unknown historical ID was intended to identify."
)
GENERIC_V6_COVERAGE_REQUIREMENTS = GENERIC_V5_COVERAGE_REQUIREMENTS + (
    " Copy each assigned issue ID exactly and return exactly one disposition per "
    "assigned ID. Do not shorten, combine, substitute or infer issue identities. "
    "A complete ID inventory is necessary but does not establish material coverage; "
    "apply all ordinary semantic and exact-reader-span requirements independently."
)

# v7 adds an opt-in, fixed disclosure packet to reader construction. It does
# not retire an issue or transfer any historical factual/coverage attestation.
GENERIC_V7_WRITER_REQUIREMENTS = GENERIC_V6_WRITER_REQUIREMENTS + (
    " A separately source-bound controlled_disclosure packet will be rendered "
    "as fixed paragraphs after your authored sections. Do not paraphrase those "
    "paragraphs or treat their presence as an issue receipt. Keep the rest of "
    "the reader coherent with them and retain every remaining qualification. "
    "When narrowing a source inventory claim, revise any adjacent outcome or "
    "rules assurance that relies on the same undelivered passages; repair the "
    "whole affected paragraph rather than leaving an implied overclaim."
)
GENERIC_V7_FOLLOWUP_REQUIREMENTS = GENERIC_V6_FOLLOWUP_REQUIREMENTS + (
    " Independently check the fixed controlled disclosure paragraphs against "
    "their source passages, all other frozen evidence, and the full reader. "
    "A source-bound packet is not a factual attestation."
)
GENERIC_V7_COVERAGE_REQUIREMENTS = GENERIC_V6_COVERAGE_REQUIREMENTS + (
    " The fixed controlled disclosure paragraphs are part of the exact reader "
    "under review. Judge all original obligations against the complete rendered "
    "reader; packet inclusion alone is not coverage."
)

VERIFICATION_REPAIR_REQUIREMENTS = (
    "This is a new attestation of the unchanged candidate, not permission to edit "
    "the reader or waive a finding. Reevaluate materiality and evidence independently. "
    "For audit_only_operational or audit_only_immaterial decisions, reader_excerpt "
    "must be an empty string and reader_excerpts must be empty. Do not attach contextual reader "
    "quotes to audit-only decisions. If a material caveat requires reader prose, use "
    "reader_covered with exact spans or unresolved; never force audit-only to satisfy "
    "the schema. Code-owned satisfied components are limited to their exact bound "
    "review scope; no financial, valuation, funding or user acceptance transfers."
)

REVISION_REQUIREMENTS = (
    "Revise the exact failed candidate using the frozen evidence and every remaining "
    "finding. This is one bounded revision, not permission to omit checks or invent facts. "
    "Distinguish repurchase authorization, evidence of execution, disclosed amounts and "
    "net dilution; do not convert missing amounts into absence of any execution. "
    "Keep the separate draft financial-case working-capital schedule distinct from "
    "reviewed narrow cash bridges and legacy proxies, including unresolved mixed rows. "
    "Preserve commitment dates/horizons, later obligations, guarantee default triggers "
    "and defined contractual coverage when supplied; contingency alone is not a complete "
    "description. Disclose material source-selection/truncation, retrieval completeness "
    "and table-extraction risks concisely. Distinguish supplied incentive mechanics and "
    "earned outcomes from unresolved target difficulty, realized pay sensitivity and "
    "investment returns. Never label supplied evidence absent merely because economic "
    "underwriting is incomplete. Procedural components explicitly classified by the "
    "code-owned applicability contract stay in the immutable audit; material economic "
    "and source-quality limits remain in the reader. Retain precise paragraph citations "
    "and calculation bindings, improve scenario interpretation and observable follow-up "
    "tests, and avoid implementation jargon and repeated caveats. Do not assert valuation, "
    "per-share value or company-wide funding approval. The new reader requires its own "
    "full factual and per-issue coverage verification; old attestations do not transfer."
)
