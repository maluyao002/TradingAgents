"""Evidence-bound issue dispositions for an exact reader, never financial clearance.

The full-context verifier makes semantic judgments. Code checks their identity,
scope and quoted witnesses; coverage-only calls cannot resolve factual issues.
Missing decisions retain the original issue. Historical records are never edited.
"""

import json
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


_COMPOUND_OBLIGATION_CONTRACT = "exact_compound_obligation_split_v1"
_COMPOUND_COVERAGE_INSTRUCTION = (
    "Assess the code-owned components, not the compound original as one proposition. "
    "Every reader_required component needs its own exact literal reader span. A component "
    "marked audit_only_satisfied is established only by its attached exact catalog binding "
    "and must not be restated as absent. An audit_only_procedural component remains in the "
    "immutable audit but need not be rendered. The original issue remains open audit "
    "provenance and cannot be resolved or superseded as a whole."
)

_SCENARIO_AND_VALUATION_GAP = (
    "Scenario and valuation review: the payload contains no scenario schedules, "
    "code-calculated scenario bindings, independent review outputs, verified market quote "
    "or consensus vintage. The retained market passages also omit the numerical inputs "
    "required to reconstruct the described discount-rate approach."
)
_FISCAL_SCENARIO_AND_EQUITY_GAP = (
    "Can the actual fiscal-year scenario schedules, calculation bindings and independent "
    "reviews be supplied together with a reconciled cutoff-date equity bridge?"
)
_OPERATING_REVIEW_BOUNDARY = (
    "This review used the supplied frozen sources offline and verifies their internal "
    "fidelity, not independent online authentication or completeness of subsequent "
    "information. Exact arithmetic does not validate the economic likelihood of the "
    "assumptions. Changed package, case or evidence content requires fresh review and "
    "matching hashes."
)

_RECONCILIATION_LINEAGE = (
    "This is a new typed source-statement reconciliation in the new package. It does "
    "not create a missing historical attribution artifact, revise the old packet or "
    "transfer an old review. Changed package, case, evidence or operating identities "
    "require a new dependent review."
)


def _component(name, text, *, status, reader_treatment, scope):
    return {
        "name": name,
        "text": text,
        "status": status,
        "reader_treatment": reader_treatment,
        "scope": scope,
    }


_COMPOUND_PROFILES = {
    _RECONCILIATION_LINEAGE: {
        "profile": "reconciliation_review_lineage_only",
        "requires_operating_evidence": False,
        "revision_only": True,
        "components": (
            _component(
                "immutable_history_and_dependent_review",
                _RECONCILIATION_LINEAGE,
                status="open_procedural",
                reader_treatment="audit_only_procedural",
                scope="review_reexecution_control",
            ),
        ),
    },
    _SCENARIO_AND_VALUATION_GAP: {
        "profile": "conditional_operating_vs_market_valuation_inputs",
        "requires_operating_evidence": True,
        "components": (
            _component(
                "reviewed_conditional_operating_package",
                "A reviewed conditional operating package supplies scenario schedules, "
                "code-calculated bindings, and a hash-bound independent operating review.",
                status="satisfied_current_evidence",
                reader_treatment="audit_only_satisfied",
                scope="conditional_operating_scenarios_only",
            ),
            _component(
                "market_and_discount_rate_inputs",
                "Whether a verified cutoff-date market quote, eligible matching-period "
                "consensus, and sufficient numerical inputs to reconstruct the described "
                "discount-rate approach are established remains a separate valuation "
                "question; the operating review alone does not supply them.",
                status="open",
                reader_treatment="reader_required",
                scope="market_inputs_and_valuation",
            ),
            _component(
                "operating_review_scope_boundary",
                "The independent review covers conditional operating schedules and arithmetic, "
                "not actual fiscal-year results, financial-case schedules, or valuation.",
                status="current_boundary",
                reader_treatment="reader_required",
                scope="operating_not_actual_or_valuation_review",
            ),
        ),
    },
    _FISCAL_SCENARIO_AND_EQUITY_GAP: {
        "profile": "conditional_operating_vs_cutoff_equity_bridge",
        "requires_operating_evidence": True,
        "components": (
            _component(
                "reviewed_conditional_operating_package",
                "A reviewed conditional operating package supplies fiscal-year conditional "
                "operating schedules, calculation bindings, and a hash-bound independent "
                "operating review.",
                status="satisfied_current_evidence",
                reader_treatment="audit_only_satisfied",
                scope="conditional_operating_scenarios_only",
            ),
            _component(
                "cutoff_equity_bridge",
                "Whether a reconciled cutoff-date equity bridge is established remains a "
                "separate valuation question; the operating review alone does not supply or "
                "review it.",
                status="open",
                reader_treatment="reader_required",
                scope="equity_per_share_value",
            ),
            _component(
                "operating_review_scope_boundary",
                "The independent review covers conditional operating schedules and arithmetic, "
                "not actual fiscal-year results, financial-case schedules, or valuation.",
                status="current_boundary",
                reader_treatment="reader_required",
                scope="operating_not_actual_or_valuation_review",
            ),
        ),
    },
    _OPERATING_REVIEW_BOUNDARY: {
        "profile": "operating_review_material_vs_procedural_boundary",
        "requires_operating_evidence": False,
        "components": (
            _component(
                "frozen_offline_source_boundary",
                "The review verifies the supplied frozen sources' internal fidelity, not "
                "independent online authentication or completeness of subsequent information.",
                status="current_boundary",
                reader_treatment="reader_required",
                scope="frozen_offline_source_review",
            ),
            _component(
                "arithmetic_not_likelihood_boundary",
                "Exact arithmetic does not validate the economic likelihood of the assumptions.",
                status="current_boundary",
                reader_treatment="reader_required",
                scope="economic_likelihood_not_reviewed",
            ),
            _component(
                "changed_content_re_review_rule",
                "Changed package, case or evidence content requires fresh review and matching "
                "hashes.",
                status="open_procedural",
                reader_treatment="audit_only_procedural",
                scope="review_reexecution_control",
            ),
        ),
    },
}


def _operating_package_binding(evidence):
    """Return an exact catalog binding only for reviewed, package-bound schedules."""
    if not isinstance(evidence, dict):
        return None
    reference = "review:operating_scenarios"
    serialized = evidence.get(reference)
    if not isinstance(serialized, str):
        return None
    try:
        review = json.loads(serialized)
    except (TypeError, ValueError):
        return None
    package_sha256 = review.get("package_sha256") if isinstance(review, dict) else None
    if not (
        isinstance(review, dict)
        and review.get("context_kind") == "reviewed_conditional_operating_scenarios"
        and review.get("reviewed") is True
        and review.get("decision") == "conditional_operating_scenarios"
        and isinstance(package_sha256, str)
        and re.fullmatch(r"[a-f0-9]{64}", package_sha256)
        and all(
            isinstance(review.get(key), str)
            and re.fullmatch(r"[a-f0-9]{64}", review[key])
            for key in ("case_sha256", "evidence_sha256")
        )
    ):
        return None

    required_outputs = {
        ("q3", "revenue"), ("q3", "operating_income"),
        ("q4", "revenue"), ("q4", "operating_income"),
        ("fiscal_total", "revenue"), ("fiscal_total", "operating_income"),
    }
    scenario_outputs = {}
    valid_records = {}
    prefix = "calculation:operating_scenario."
    for calculation_reference, value in evidence.items():
        if not calculation_reference.startswith(prefix) or not isinstance(value, str):
            continue
        identifier = calculation_reference.removeprefix("calculation:")
        try:
            scenario_prefix, period, metric = identifier.rsplit(".", 2)
            record = json.loads(value)
        except (TypeError, ValueError):
            continue
        scenario = scenario_prefix.removeprefix("operating_scenario.")
        output = (period, metric)
        if not scenario or output not in required_outputs or not isinstance(record, dict):
            continue
        expected_classification = (
            "operating_scenario_assumption_not_reported_fact"
            if period in {"q3", "q4"} and metric == "revenue"
            else "conditional_operating_scenario_calculation_not_reported_fact"
        )
        if not (
            record.get("id") == identifier
            and record.get("model_input_sha256") == package_sha256
            and record.get("valuation_method") == "operating_scenario"
            and record.get("classification") == expected_classification
            and isinstance(record.get("evidence_ids"), list)
            and bool(record["evidence_ids"])
            and isinstance(record.get("value"), str)
            and bool(record["value"].strip())
        ):
            continue
        scenario_outputs.setdefault(scenario, set()).add(output)
        valid_records[calculation_reference] = value

    complete_scenarios = sorted(
        scenario for scenario, outputs in scenario_outputs.items()
        if outputs == required_outputs
    )
    if not complete_scenarios:
        return None
    selected = sorted(
        reference for reference in valid_records
        if reference.removeprefix(prefix).rsplit(".", 2)[0] in complete_scenarios
    )
    bound_evidence = {reference: serialized}
    bound_evidence.update({item: valid_records[item] for item in selected})
    return {
        "binding_id": "operating_package",
        "catalog_sha256": digest(bound_evidence),
        "package_sha256": package_sha256,
        "witnesses": [
            {"reference": reference, "excerpt": serialized},
            *(
                {"reference": item,
                 "excerpt": f'\"id\":\"{item.removeprefix("calculation:")}\"'}
                for item in selected
            ),
        ],
    }


def _compound_payload(issue, profile, evidence_binding):
    components = []
    for component in profile["components"]:
        item = deepcopy(component)
        item["component_id"] = f'{issue["issue_id"]}#component:{item["name"]}'
        if item["status"] == "satisfied_current_evidence":
            item["evidence_binding_id"] = evidence_binding["binding_id"]
        components.append(item)
    payload = {
        "contract": _COMPOUND_OBLIGATION_CONTRACT,
        "profile": profile["profile"],
        "original": {
            "issue_id": issue["issue_id"],
            "text": issue["text"],
            "sha256": digest({"issue_id": issue["issue_id"], "text": issue["text"]}),
        },
        "components": components,
        "evidence_bindings": ([deepcopy(evidence_binding)] if evidence_binding else []),
        "coverage_instruction": _COMPOUND_COVERAGE_INSTRUCTION,
    }
    payload["contract_sha256"] = digest(payload)
    return payload


def compound_obligation_contract_valid(issue):
    """Validate the static, code-owned split without making evidence claims."""
    compound = issue.get("compound_obligation")
    if not isinstance(compound, dict):
        return False
    profile = _COMPOUND_PROFILES.get(issue.get("text"))
    if profile is None or compound.get("profile") != profile["profile"]:
        return False
    original = compound.get("original")
    if original != {
        "issue_id": issue.get("issue_id"),
        "text": issue.get("text"),
        "sha256": digest({"issue_id": issue.get("issue_id"), "text": issue.get("text")}),
    }:
        return False
    if compound.get("contract") != _COMPOUND_OBLIGATION_CONTRACT:
        return False
    if compound.get("coverage_instruction") != _COMPOUND_COVERAGE_INSTRUCTION:
        return False
    expected = []
    bindings = compound.get("evidence_bindings")
    if not isinstance(bindings, list):
        return False
    binding_ids = {
        binding.get("binding_id") for binding in bindings if isinstance(binding, dict)
    }
    for component in profile["components"]:
        item = deepcopy(component)
        item["component_id"] = f'{issue.get("issue_id")}#component:{item["name"]}'
        if item["status"] == "satisfied_current_evidence":
            item["evidence_binding_id"] = "operating_package"
            if "operating_package" not in binding_ids:
                return False
        expected.append(item)
    if compound.get("components") != expected:
        return False
    supplied_hash = compound.get("contract_sha256")
    unhashed = {key: value for key, value in compound.items() if key != "contract_sha256"}
    return supplied_hash == digest(unhashed)


def compound_obligation_evidence_valid(issue, evidence):
    """Recompute rather than trust a stored current-evidence binding."""
    if not compound_obligation_contract_valid(issue):
        return False
    profile = _COMPOUND_PROFILES[issue["text"]]
    expected = (
        [_operating_package_binding(evidence)]
        if profile["requires_operating_evidence"] else []
    )
    return None not in expected and issue["compound_obligation"]["evidence_bindings"] == expected


def split_compound_obligations(issues, evidence, *, reader_revision=False):
    """Attach conservative atomic coverage components to exact known legacy issues.

    Original issue IDs and text are never replaced. Similar or unmatched prose is
    untouched. A stale operating component is marked satisfied only when the
    current catalog contains a reviewed conditional-operating record and complete,
    package-bound period/fiscal calculations. Open valuation components protect
    the parent from whole-issue retirement.
    """
    operating_binding = _operating_package_binding(evidence)
    result = []
    for source in issues:
        item = deepcopy(source)
        profile = _COMPOUND_PROFILES.get(item.get("text"))
        if profile is None or (profile.get("revision_only") and not reader_revision):
            result.append(item)
            continue
        if profile.get("revision_only") and any(
            finding.get("category") == "security" or (
                finding.get("category") == "numerical" and finding.get("severity") == "critical")
            for finding in item.get("prior_findings", ())
        ):
            result.append(item)
            continue
        if profile["requires_operating_evidence"] and operating_binding is None:
            result.append(item)
            continue
        binding = operating_binding if profile["requires_operating_evidence"] else None
        item["compound_obligation"] = _compound_payload(item, profile, binding)
        reasons = tuple(item.get("resolution_protection_reasons", ()))
        reason = "compound_obligation_has_current_or_open_components"
        item["resolution_protection_reasons"] = (*reasons, *(() if reason in reasons else (reason,)))
        item["resolution_protected"] = True
        item["claim_change_eligible"] = False
        item["reader_coverage_required"] = any(
            component["reader_treatment"] == "reader_required"
            for component in profile["components"]
        )
        result.append(item)
    return result


def compound_coverage_issues(issues):
    """Expand valid parent contracts into independently reviewable atomic issues.

    Code-satisfied stale components remain in the parent audit contract and are
    not sent back to the coverage model. Every current reader or procedural
    component receives a deterministic child ID. Unmatched and malformed parents
    remain intact and therefore fail closed under the ordinary issue contract.
    """
    result = []
    for source in issues:
        parent = deepcopy(source)
        if "compound_obligation" not in parent:
            result.append(parent)
            continue
        if not compound_obligation_contract_valid(parent):
            result.append(parent)
            continue
        compound = parent["compound_obligation"]
        for component in compound["components"]:
            if component["reader_treatment"] == "audit_only_satisfied":
                continue
            child = {
                "issue_id": component["component_id"],
                "text": component["text"],
                "compound_parent": deepcopy(compound["original"]),
                "compound_contract_sha256": compound["contract_sha256"],
                "parent_context_sha256": digest({
                    key: value for key, value in parent.items()
                    if key != "compound_obligation"
                }),
                "coverage_component": deepcopy(component),
                "prior_findings": deepcopy(parent.get("prior_findings", [])),
                "origins": deepcopy(parent.get("origins", [])),
                "reader_coverage_required": (
                    component["reader_treatment"] == "reader_required"
                ),
                "resolution_protected": True,
                "claim_change_eligible": False,
            }
            result.append(child)
    return result


def compound_coverage_component_valid(issue):
    """Recognize only an exact child emitted by ``compound_coverage_issues``."""
    parent = issue.get("compound_parent")
    component = issue.get("coverage_component")
    if not isinstance(parent, dict) or not isinstance(component, dict):
        return False
    profile = _COMPOUND_PROFILES.get(parent.get("text"))
    if profile is None or parent.get("sha256") != digest({
        "issue_id": parent.get("issue_id"), "text": parent.get("text")
    }):
        return False
    expected = next(
        (deepcopy(item) for item in profile["components"]
         if item["name"] == component.get("name")),
        None,
    )
    if expected is None or expected["reader_treatment"] == "audit_only_satisfied":
        return False
    expected["component_id"] = (
        f'{parent.get("issue_id")}#component:{expected["name"]}'
    )
    return (
        component == expected
        and issue.get("issue_id") == expected["component_id"]
        and issue.get("text") == expected["text"]
        and isinstance(issue.get("compound_contract_sha256"), str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", issue["compound_contract_sha256"]))
        and isinstance(issue.get("parent_context_sha256"), str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", issue["parent_context_sha256"]))
    )


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
        has_compound = "compound_obligation" in issue
        compound_valid = (
            not has_compound or compound_obligation_evidence_valid(issue, evidence)
        )
        if has_compound and not compound_valid:
            fail("Compound obligation contract or evidence binding is invalid.", identifier)
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
                and not has_compound
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
    "For a code-owned compound_obligation, assess its atomic coverage components; never "
    "resolve or supersede the parent. An audit_only_satisfied operating component does not "
    "clear or assert the answer to any current market, equity, financial-case, actual-results "
    "or valuation question. "
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
