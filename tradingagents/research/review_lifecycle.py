"""Evidence-bound issue dispositions for an exact reader, never financial clearance.

The full-context verifier makes semantic judgments. Code checks their identity,
scope and quoted witnesses; coverage-only calls cannot resolve factual issues.
Missing decisions retain the original issue. Historical records are never edited.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, ReviewFinding
from .stages import VerificationOutput
from .storage import canonical_json, digest


class EvidenceWitness(Contract):
    reference: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class IssueResolution(Contract):
    issue_id: str
    status: Literal["open", "resolved", "superseded"]
    rationale: str = Field(min_length=1)
    witnesses: tuple[EvidenceWitness, ...] = ()
    reader_excerpts: tuple[str, ...] = ()

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("resolution rationale must be nonblank")
        return value


ConclusionScope = Literal[
    "operating_asset_value", "equity_per_share_value", "funding_assessment",
    "opening_date_alignment", "research_uncertainty",
]


class FindingDisposition(Contract):
    finding_code: str
    disposition: Literal["report_defect", "disclosed_limitation"]
    rationale: str = Field(min_length=1)
    reader_excerpts: tuple[str, ...] = ()
    conclusion_scopes: tuple[ConclusionScope, ...] = ()

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("finding rationale must be nonblank")
        return value


class LifecycleVerification(VerificationOutput):
    issue_resolutions: tuple[IssueResolution, ...] = ()
    finding_dispositions: tuple[FindingDisposition, ...] = ()


def enrich_issues(issues, outputs, prior_findings, protected_texts=(), limitation_origins=None):
    """Keep stable original IDs/text, adding propositions instead of opaque labels."""
    claims, records = {}, {}
    for stage, output in outputs.items():
        for claim in output.get("claims", []):
            claims.setdefault(claim["id"], []).append({"stage": stage, **claim})
        for kind in ("findings", "questions"):
            for record in output.get(kind, []):
                records.setdefault(record["id"], []).append({"stage": stage, "kind": kind, **record})
    result = []
    for original in issues:
        item = dict(original)
        related = [f for f in prior_findings if f.message == item["text"]]
        ids = list(dict.fromkeys(identifier for f in related for identifier in f.affected_ids))
        unverified_claim = None
        if item["text"].startswith("Unverified claim: "):
            unverified_claim = item["text"].removeprefix("Unverified claim: ")
            ids.append(unverified_claim)
        item["claims"] = [claim for identifier in dict.fromkeys(ids)
                          for claim in claims.get(identifier, [])]
        item["related_records"] = [record for identifier in dict.fromkeys(ids)
                                   if identifier != unverified_claim
                                   for record in records.get(identifier, [])]
        item["missing_claim_ids"] = [identifier for identifier in dict.fromkeys(ids)
                                     if (identifier not in claims and identifier not in records)
                                     or (identifier == unverified_claim and identifier not in claims)]
        item["prior_findings"] = [f.model_dump(mode="json") for f in related]
        item["origins"] = deepcopy((limitation_origins or {}).get(item["text"], []))
        item["resolution_protected"] = (item["text"] in protected_texts
                                        or any(not origin["retirable"] for origin in item["origins"])
                                        or any(f.category == "security" for f in related))
        result.append(item)
    return result


def evidence_catalog(snapshot, calculated_values, *, eligible_ids=None, case_context=None):
    # Facts and calculations have validated provenance. The verifier also receives
    # the normal source-context payload; existence of a witness is not entailment.
    records = [("fact:" + f.id, f) for f in snapshot.facts
               if eligible_ids is None or f.id in eligible_ids]
    records.extend(("calculation:" + value.id, value) for value in calculated_values
                   if eligible_ids is None or set(value.evidence_ids) <= set(eligible_ids))
    catalog = {reference: canonical_json(record).decode("utf-8") for reference, record in records}
    if case_context is not None:
        for passage in case_context.source_passages:
            if eligible_ids is None or passage.source_id in eligible_ids:
                catalog[f"passage:{passage.source_id}:{passage.start}:{passage.end}"] = passage.text
        operating = case_context.operating_scenarios
        if operating is not None and operating.reviewed:
            catalog["review:operating_scenarios"] = canonical_json({
                key: operating.model_context[key] for key in (
                    "context_kind", "reviewed", "decision", "package_sha256", "case_sha256", "evidence_sha256"
                )
            }).decode("utf-8")
    return catalog


def _exact_spans(spans, reader):
    return bool(spans) and all(span.strip() and span in reader for span in spans)


def reconcile_review(review, issues, evidence, reader, scope):
    """Return active findings/issues and an immutable, reader-bound audit ledger.

    Only the full factual verifier may propose resolutions. Exact evidence and
    reader witnesses are mandatory; security findings are never downgraded.
    Deterministic model scope remains unchanged regardless of any disposition.
    """
    review = LifecycleVerification.model_validate_json(review.model_dump_json())
    issue_map = {item["issue_id"]: item for item in issues}
    if len(issue_map) != len(issues):
        raise ValueError("duplicate lifecycle issue IDs")
    counts = Counter(item.issue_id for item in review.issue_resolutions)
    decisions = {item.issue_id: item for item in review.issue_resolutions}
    failures, ledger, retired = [], [], set()

    def fail(message, identifier):
        failures.append(ReviewFinding(code="issue_lifecycle", severity="critical",
                                      category="research", message=message,
                                      affected_ids=(identifier,)))

    for identifier in decisions:
        if identifier not in issue_map or counts[identifier] != 1:
            fail("Unknown or duplicate issue resolution.", identifier)
    for identifier, issue in issue_map.items():
        decision = decisions.get(identifier)
        state = "open"
        if decision is not None and decision.status != "open":
            valid = (
                review.reviewed_report and not review.contradicted_claim_ids
                and counts[identifier] == 1 and not issue.get("resolution_protected")
                and not issue.get("missing_claim_ids")
                and _exact_spans(decision.reader_excerpts, reader)
                and bool(decision.witnesses)
                and len({(w.reference, w.excerpt) for w in decision.witnesses}) == len(decision.witnesses)
                and all(w.reference in evidence and w.excerpt.strip()
                        and w.excerpt in evidence[w.reference] for w in decision.witnesses)
                and all(
                    origin.get("required_witness_reference")
                    and origin.get("package_sha256")
                    and any(w.reference == origin["required_witness_reference"]
                            and origin["package_sha256"] in w.excerpt for w in decision.witnesses)
                    for origin in issue.get("origins", [])
                )
            )
            if valid:
                state = decision.status
                retired.add(identifier)
            else:
                fail("Issue resolution lacks valid evidence/reader witnesses or is protected.", identifier)
        ledger.append({**issue, "status": state,
                       "decision": decision.model_dump(mode="json") if decision else None})

    finding_counts = Counter(item.code for item in review.findings)
    disposition_counts = Counter(item.finding_code for item in review.finding_dispositions)
    disposition_map = {item.finding_code: item for item in review.finding_dispositions}
    for code in disposition_map:
        if finding_counts[code] != 1 or disposition_counts[code] != 1:
            fail("Unknown or duplicate finding scope disposition.", code)
    active, scoped = [], []
    for finding in review.findings:
        decision = disposition_map.get(finding.code)
        if decision is None or decision.disposition == "report_defect":
            active.append(finding)
            continue
        allowed = (
            review.reviewed_report and not review.contradicted_claim_ids
            and finding_counts[finding.code] == 1 and disposition_counts[finding.code] == 1
            and finding.category in {"data", "research"}
            and _exact_spans(decision.reader_excerpts, reader)
            and bool(decision.conclusion_scopes)
            and ("research_uncertainty" not in decision.conclusion_scopes
                 or finding.category == "research")
            and all(target == "research_uncertainty" or (
                scope is not None and getattr(scope, target).status in {"blocked", "not_assessed"}
            ) for target in decision.conclusion_scopes)
        )
        if not allowed:
            active.append(finding)
            fail("Finding cannot be scoped away without a verified disclosure and unavailable conclusion.",
                 finding.code)
        else:
            scoped.append({"finding": finding.model_dump(mode="json"),
                           "disposition": decision.model_dump(mode="json")})
    active.extend(failures)
    return (
        VerificationOutput(
            supported_claim_ids=review.supported_claim_ids,
            contradicted_claim_ids=review.contradicted_claim_ids,
            reviewed_report=review.reviewed_report, findings=tuple(active)),
        [item for item in issues if item["issue_id"] not in retired],
        {"reader_sha256": sha256(reader.encode("utf-8")).hexdigest(),
         "issues": ledger, "scoped_findings": scoped,
         "original_review": review.model_dump(mode="json"),
         "retired_issue_ids": sorted(retired), "evidence_sha256": digest(evidence)},
    )


LIFECYCLE_POLICY = (
    "Review inherited issues against current evidence and the exact reader. Missing decisions "
    "remain open. Resolve or supersede a historical absence/claim warning ONLY when current "
    "evidence actually invalidates that precise warning and the reader is corrected: quote exact "
    "witness excerpts from resolution_evidence and exact reader passages. Never infer resolution "
    "from the existence of an ID or unrelated fact. If a claim was removed, quote the replacement "
    "qualified discussion and cite evidence supporting it; a generic caveat does not suffice. "
    "Do not resolve missing financial prerequisites merely because they are disclosed. "
    "For retirable operating-package metadata origins, include the required reviewed-package "
    "witness reference and quote its exact package hash; review of operating scenarios never "
    "clears the separate financial case or the operating-only boundary. "
    "For new findings distinguish a report_defect (unsupported, misleading, contradictory or "
    "missing material disclosure) from a disclosed_limitation (a correctly bounded inference "
    "or correctly withheld unavailable conclusion). Use finding_dispositions with exact reader "
    "spans and affected conclusion scopes. research_uncertainty applies only to properly labeled "
    "uncertainty, NEVER unsupported factual claims, mathematical errors or an asserted unavailable "
    "valuation. Security and numerical errors always remain report defects. Do not invent new "
    "clearance: deterministic financial scope and release eligibility cannot be overridden."
)
