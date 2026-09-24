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
