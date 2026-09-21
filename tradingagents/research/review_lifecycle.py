"""Evidence-bound issue dispositions for an exact reader, never financial clearance.

The full-context verifier makes semantic judgments. Code checks their identity,
scope and quoted witnesses; coverage-only calls cannot resolve factual issues.
Missing decisions retain the original issue. Historical records are never edited.
"""

import re
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


# Resolution witnesses are deliberately limited to records produced by
# ``evidence_catalog``. Reader text has its own exact-span field and source IDs
# have no evidentiary meaning without a catalog namespace and retained content.
EVIDENCE_REFERENCE_CONTRACT = (
    "fact:<fact-id>",
    "calculation:<calculation-id>",
    "passage:<source-id>:<start>:<end>",
    "review:operating_scenarios",
    "claim_change:<sha256>",
)
_CLAIM_CHANGE_CONTRACT = "reader_bound_claim_change_v1"


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
        if item["claims"] and not item["related_records"] and not item["missing_claim_ids"]:
            item["lifecycle_subject"] = "historical_claim_defect"
        elif item["related_records"] and not item["claims"] and not item["missing_claim_ids"]:
            item["lifecycle_subject"] = (
                "research_question"
                if any(record["kind"] == "questions" for record in item["related_records"])
                else "research_record"
            )
        elif item["claims"] or item["related_records"] or item["missing_claim_ids"]:
            item["lifecycle_subject"] = "mixed"
        else:
            item["lifecycle_subject"] = "general"
        protection_reasons = []
        if item["text"] in protected_texts:
            protection_reasons.append("engine_protected_text")
        if any(not origin["retirable"] for origin in item["origins"]):
            protection_reasons.append("nonretirable_origin")
        if any(f.category == "financial" for f in related):
            protection_reasons.append("financial_finding")
        if any(f.category == "security" for f in related):
            protection_reasons.append("security_finding")
        if item["lifecycle_subject"] == "mixed":
            protection_reasons.append("mixed_claim_and_research_record_requires_split")
        item["resolution_protection_reasons"] = tuple(protection_reasons)
        item["resolution_protected"] = bool(protection_reasons)
        item["claim_change_eligible"] = (
            item["lifecycle_subject"] == "historical_claim_defect"
            and not item["resolution_protected"]
        )
        result.append(item)
    return result


def _catalog_reference(reference):
    """Return the catalog namespace for an exact, admissible reference."""
    if reference == "review:operating_scenarios":
        return "review"
    for namespace in ("fact", "calculation"):
        prefix = namespace + ":"
        if reference.startswith(prefix):
            identifier = reference[len(prefix):]
            return namespace if identifier and identifier == identifier.strip() else None
    if reference.startswith("passage:"):
        try:
            source_id, start, end = reference.removeprefix("passage:").rsplit(":", 2)
        except ValueError:
            return None
        if (source_id and source_id == source_id.strip() and start.isdigit() and end.isdigit()
                and int(start) <= int(end)):
            return "passage"
        return None
    if re.fullmatch(r"claim_change:[a-f0-9]{64}", reference):
        return "claim_change"
    return None


def resolution_witness_contract(evidence, reader):
    """Describe the exact witness interface supplied to the semantic verifier."""
    invalid = sorted(reference for reference in evidence if _catalog_reference(reference) is None)
    if invalid:
        raise ValueError("resolution evidence contains non-catalog references")
    return {
        "catalog_key_formats": EVIDENCE_REFERENCE_CONTRACT,
        "catalog_keys": tuple(sorted(evidence)),
        "claim_change": {
            "allowed_issue_subject": "historical_claim_defect",
            "decision_status": "superseded",
            "does_not_resolve": "underlying_research_question",
            "ineligible_when": "original_claim_literal_present",
            "required_excerpt_literal": "reader_sha256",
        },
        "contract": "resolution_witness_v1",
        "evidence_excerpt_match": "exact_literal_substring_of_catalog_value",
        "forbidden_reference_forms": ("rendered_reader", "bare_source_id"),
        "protected_issue_subjects": {
            "mixed": "split_claim_defect_from_research_record_before_retirement",
        },
        "reader_excerpt_field": "reader_excerpts",
        "reader_excerpt_match": "exact_literal_substring_of_reader",
        "reader_sha256": sha256(reader.encode("utf-8")).hexdigest(),
    }


def claim_change_evidence(issues, reader):
    """Build deterministic absence records for known historical claims.

    These records establish only that every exact original claim literal is
    absent from specific reader bytes. They do not establish that the
    underlying research question is answered; that remains a semantic verifier
    decision supported by exact replacement or qualification spans.
    """
    reader_sha256 = sha256(reader.encode("utf-8")).hexdigest()
    identifiers = [item.get("issue_id") for item in issues]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate lifecycle issue IDs")
    records = {}
    for issue in issues:
        if not issue.get("claim_change_eligible") or issue.get("missing_claim_ids"):
            continue
        claims = []
        valid = True
        for claim in issue.get("claims", ()):
            identifier, text = claim.get("id"), claim.get("text")
            if (not isinstance(identifier, str) or not identifier.strip()
                    or not isinstance(text, str) or not text.strip()
                    or text in reader):
                valid = False
                break
            claims.append({
                "claim_id": identifier,
                "claim_sha256": digest(claim),
                "claim_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
                "stage": claim.get("stage"),
            })
        if not valid or not claims:
            continue
        claims.sort(key=lambda item: (
            item["claim_id"], item["stage"] or "", item["claim_sha256"]
        ))
        record = {
            "claim_fingerprints": claims,
            "contract": _CLAIM_CHANGE_CONTRACT,
            "issue_id": issue["issue_id"],
            "observation": "all_original_claim_literals_absent",
            "reader_sha256": reader_sha256,
        }
        records["claim_change:" + digest(record)] = canonical_json(record).decode("utf-8")
    return records


def evidence_catalog(snapshot, calculated_values, *, eligible_ids=None, case_context=None,
                     issues=None, reader=None):
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
    if (issues is None) != (reader is None):
        raise ValueError("issues and reader are both required for claim-change evidence")
    if issues is not None:
        catalog.update(claim_change_evidence(issues, reader))
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
    reader_hash = sha256(reader.encode("utf-8")).hexdigest()

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
            expected_claim_change = claim_change_evidence((issue,), reader)
            claim_change_required = issue.get("lifecycle_subject") == "historical_claim_defect"
            claim_change_valid = (
                not claim_change_required
                or (
                    decision.status == "superseded"
                    and len(expected_claim_change) == 1
                    and all(evidence.get(reference) == value
                            and any(w.reference == reference and reader_hash in w.excerpt
                                    for w in decision.witnesses)
                            for reference, value in expected_claim_change.items())
                )
            )
            valid = (
                review.reviewed_report and not review.contradicted_claim_ids
                and counts[identifier] == 1 and not issue.get("resolution_protected")
                and not issue.get("missing_claim_ids")
                and _exact_spans(decision.reader_excerpts, reader)
                and len(set(decision.reader_excerpts)) == len(decision.reader_excerpts)
                and bool(decision.witnesses)
                and len({(w.reference, w.excerpt) for w in decision.witnesses}) == len(decision.witnesses)
                and all(_catalog_reference(w.reference) is not None
                        and w.reference in evidence and w.excerpt.strip()
                        and w.excerpt in evidence[w.reference] for w in decision.witnesses)
                and all(_catalog_reference(w.reference) != "claim_change"
                        or w.reference in expected_claim_change for w in decision.witnesses)
                and claim_change_valid
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
        ledger.append({**deepcopy(issue), "status": state,
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
            and not ("research_uncertainty" in decision.conclusion_scopes
                     and len(decision.conclusion_scopes) != 1)
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
        [deepcopy(item) for item in issues if item["issue_id"] not in retired],
        {"reader_sha256": reader_hash,
         "issues": ledger, "scoped_findings": scoped,
         "original_review": review.model_dump(mode="json"),
         "retired_issue_ids": sorted(retired), "evidence_sha256": digest(evidence)},
    )


LIFECYCLE_POLICY = (
    "Review inherited issues against current evidence and the exact reader. Missing decisions "
    "remain open. Resolve or supersede a historical absence/claim warning ONLY when current "
    "evidence actually invalidates that precise warning and the reader is corrected: quote exact "
    "witness excerpts from resolution_evidence and exact reader passages. Evidence references "
    "must use an exact catalog key: fact:<fact-id>, calculation:<calculation-id>, "
    "passage:<source-id>:<start>:<end>, review:operating_scenarios, or "
    "claim_change:<sha256>. Bare source IDs and rendered_reader are never evidence references; "
    "reader text belongs only in reader_excerpts. Never infer resolution from the existence of an "
    "ID or unrelated fact. A reader-bound claim_change record proves only that all known historical "
    "claim literals are absent from those exact reader bytes. For such a removed or corrected "
    "historical claim use superseded, cite that exact record, quote its exact reader_sha256 literal, "
    "and quote the replacement qualified discussion; the verifier must still judge its semantics. "
    "The record does not answer or retire "
    "an underlying research question, and a generic caveat does not suffice. "
    "Do not resolve missing financial prerequisites merely because they are disclosed. "
    "Only engine-designated obsolete review-status metadata is retirable; current authored "
    "operating/reviewer caveats stay protected even after review. For retirable origins, "
    "include the required reviewed-package "
    "witness reference and quote its exact package hash; review of operating scenarios never "
    "clears the separate financial case or the operating-only boundary. "
    "For new findings distinguish a report_defect (unsupported, misleading, contradictory or "
    "missing material disclosure) from a disclosed_limitation (a correctly bounded inference "
    "or correctly withheld unavailable conclusion). Use finding_dispositions with exact reader "
    "spans and affected conclusion scopes. research_uncertainty applies only to properly labeled "
    "uncertainty, NEVER unsupported factual claims, mathematical errors or an asserted unavailable "
    "valuation, and it cannot be mixed with a deterministic conclusion scope. Security and "
    "numerical errors always remain report defects. Do not invent new "
    "clearance: deterministic financial scope and release eligibility cannot be overridden."
)
