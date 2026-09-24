"""Recovery-safe research workflow with injected evidence and model services.

This opt-in foundation emits review-required reports. Production acceptance stays
disabled until company-specific schedules and source coverage pass M3/M6 gates.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from tradingagents.codex.adapter import (
    codex_failure_diagnostic,
    codex_failure_reason,
    safe_failure_type,
)

from .admission import evaluate_admission
from .budget import BudgetExhausted, BudgetTracker
from .calculated_values import calculation_catalog, render_calculations
from .case_context import load_case_context
from .case_report import CASE_READER_REQUIREMENTS, CaseReportDraft, case_reader_delivery
from .context import pack_evidence
from .contracts import (
    Assessment,
    Dossier,
    EvidenceSnapshot,
    ReportLanguage,
    ResearchRequest,
    ResearchResult,
    ReviewFinding,
    Usage,
)
from .coverage_payload import LIMITATION_POLICY, coverage_model_payload, coverage_review_data
from .coverage_policy import (
    coverage_batches_for_policy,
    coverage_output_envelope,
)
from .dossiers import DossierStore
from .equity_valuation import EquityDCFModelInput, EquityForecastPeriod, equity_dcf_valuation
from .evidence import validate_snapshot
from .finalization_timing import finalization_time_plan
from .investigation import collect_tasks, make_ledger
from .investigation_review import InvestigationReview
from .prompt_context import model_input_bytes, model_prompt
from .reader import ReaderIssue, render_reader
from .reader_provenance import RENDERED_READER_POLICY, case_model_appendix, reader_provenance
from .reader_revision import (
    FROZEN_REVIEW_STAGE,
    GENERATION_PATTERN,
    GENERIC_CASHFLOW_POLICY,
    GENERIC_REVISION_POLICY,
    GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES,
    GENERIC_V5_COVERAGE_DELTA_MAX_BYTES,
    READER_REVISION_POLICY,
    REVISE_STAGE,
    REVISED_REVIEW_STAGE,
    REVISION_REQUIREMENTS,
    VERIFICATION_REPAIR_POLICY,
    VERIFICATION_REPAIR_REQUIREMENTS,
    generation_stages,
    generic_coverage_stage,
    generic_review_stage,
    generic_writer_stage,
)
from .rendering import render_references
from .report_review import (
    ReaderVerification,
    check_dispositions,
    fan_in_compound_dispositions,
    limitation_packet,
    nonmandatory_review_finding_texts,
    requires_reader_coverage,
    validated_disposition_ids,
)
from .research_questions import QUESTION_LED_REQUIREMENTS
from .result_scope import scope_calculation
from .review_batches import (
    CoverageBatchResult,
    FinalizationCallPlan,
    block_reader_contradictions,
    combine_coverage,
    fanout_group_dispositions,
    finalization_allowance,
    finalization_workload,
    group_equivalent_issues,
)
from .review_lifecycle import (
    LIFECYCLE_POLICY,
    LifecycleVerification,
    RevisionLifecycleVerification,
    compound_coverage_issues,
    enrich_issues,
    evidence_catalog,
    reconcile_review,
    resolution_witness_contract,
    source_passage_witness_valid,
    split_compound_obligations,
)
from .revision_contracts import V4_CONTRACT, V5_CONTRACT, revision_contract
from .revision_correction_context import (
    CORRECTION_CONTEXT_FIELD,
    CORRECTION_CONTEXT_POLICY,
    MAX_CORRECTION_CONTEXT_BYTES,
    attach_current_factual_corrections,
)
from .revision_coverage_schedule import (
    coverage_delta_admission,
    decorated_packet_bytes,
    pinned_coverage_slots,
    project_pinned_coverage,
    reserve_global_coverage_delta,
)
from .revision_deferred import deferred_coverage_eligibility, resolve_deferred_coverage
from .revision_pending import pending_entries, project_pending_issues
from .revision_witness_selection import revision_witness_catalog
from .services import ModelReply, ResearchServices
from .stages import AnalysisOutput, ReportDraft, ValuationProposal, VerificationOutput, instruction
from .storage import (
    ENGINE_VERSION,
    CheckpointStore,
    atomic_write,
    canonical_json,
    digest,
    load_request_inputs,
    parse_json,
    read_bytes,
    read_json,
    request_identity,
)
from .updates import describe_update, eligible_prior
from .valuation import FCFFModelInput, ForecastPeriod, ValuationUnits, dcf_valuation
from .wire import WIRE_SCHEMA_VERSION

_REPORT_FILENAMES = {"English": "reader_report_en.md", "Chinese": "reader_report_zh.md"}
_ROLE_QUERIES = {
    "planner": "revenue customers segments competitive risks financial condition outlook",
    "business": "customer concentration competition demand capacity pricing segment revenue",
    "accounting": "income cash flows balance sheets working capital shares stock compensation unusual gains",
    "expectations": "guidance outlook revenue margins forecasts expectations",
    "management": "capital allocation repurchases incentives acquisitions governance",
    "valuation": "annual revenue net income diluted shares cash debt capital expenditures working capital",
    "challenger": "risks competition customer concentration regulatory uncertainty",
}


def _research_queries(role, data, outputs):
    questions = data.get("questions", outputs.get("planner", {}).get("questions", []))
    return tuple(dict.fromkeys([
        _ROLE_QUERIES.get(role, "financial results risks assumptions limitations"),
        *(question["question"] for question in questions),
        *(question for output in outputs.values()
          for question in output.get("followup_questions", [])),
    ]))


def _required_artifacts(request: ResearchRequest):
    required = {"reader_report.md", "audit_report.md", "evidence.json", "research.json",
                "valuation_inputs.json", "valuation_results.json", "quality.json",
                "run_metadata.json"}
    if request.additional_report_languages:
        required.update(_REPORT_FILENAMES[language] for language in (
            request.report_language, *request.additional_report_languages))
    if request.dossier_dir:
        required.add("dossier.json")
    if request.financial_case_path:
        required.update({"case_input.json", "financial_case.json", "financial_reconciliation.json",
                         "case_context.json", "case_material_delivery.json", "model_appendix.md",
                         "report_admission.json"})
    if request.quality_revision != "foundation":
        required.update({"reader_limitations.json", "reader_verification.json", "calculated_values.json",
                         "investigation.json"})
    return required


def _known_ids(snapshot):
    eligible = {source.id for source in snapshot.sources
                if source.published_at is not None and source.availability == "full_text"}
    fact_ids = set()
    pending = [fact for fact in snapshot.facts if fact.source_id in eligible]
    while pending:
        ready = {fact.id for fact in pending if set(fact.inputs) <= fact_ids}
        if not ready:
            break
        fact_ids.update(ready)
        pending = [fact for fact in pending if fact.id not in fact_ids]
    return eligible | fact_ids | {item.id for item in snapshot.events
                                  if item.source_id in eligible} | {
                           item.id for item in snapshot.expectations
                           if not set(item.source_ids) - eligible}


def _prompt_evidence(snapshot, queries=()):
    """Keep undated/current API payloads in the audit, never in historical reasoning."""
    known = _known_ids(snapshot)
    payload = snapshot.model_dump(mode="json")
    for key in ("sources", "facts", "events", "expectations"):
        original_count = len(payload[key])
        payload[key] = [item for item in payload[key] if item["id"] in known]
        if len(payload[key]) != original_count:
            payload["gaps"].append(f"{original_count - len(payload[key])} {key} omitted from model context: ineligible evidence or dependencies.")
    bounded_queries = []
    remaining = 4096
    for query in queries:
        if len(bounded_queries) >= 16 or remaining <= 0:
            break
        excerpt = query[:min(512, remaining)]
        if excerpt.strip():
            bounded_queries.append(excerpt)
            remaining -= len(excerpt)
    result = pack_evidence(EvidenceSnapshot.model_validate(payload), queries=tuple(bounded_queries))
    if tuple(bounded_queries) != tuple(queries):
        result["context_gaps"].append(
            "Question delivery was bounded to 16 queries, 512 characters each and 4096 total; "
            "omitted/truncated questions remain open, not evidence of absence.")
    return result


def _validate_analysis(output: AnalysisOutput, snapshot: EvidenceSnapshot):
    known = _known_ids(snapshot)
    references = {sid for claim in output.claims for sid in claim.source_ids}
    references |= {sid for finding in output.findings
                   for sid in (*finding.evidence_ids, *finding.counterevidence_ids)}
    if references - known:
        raise ValueError("analysis invented evidence identifiers")
    return output


def _calculate(proposal: ValuationProposal, request: ResearchRequest, snapshot):
    if proposal.model is None or proposal.unsupported_inputs:
        return {"status": "unavailable", "limitations": proposal.unsupported_inputs}
    if set(proposal.evidence_ids) - _known_ids(snapshot):
        raise ValueError("valuation invented evidence identifiers")
    raw = proposal.model
    equity = request.valuation_method == "equity_fcfe"
    model_schema = EquityDCFModelInput if equity else FCFFModelInput
    period_schema = EquityForecastPeriod if equity else ForecastPeriod
    for data, schema in [(raw, model_schema), (raw.get("units", {}), ValuationUnits),
                         *((period, period_schema) for period in raw.get("periods", []))]:
        if not isinstance(data, dict) or set(data) - {field.name for field in fields(schema)}:
            raise ValueError("unsupported valuation input fields")
    model = TypeAdapter(model_schema).validate_python(raw)
    if model.as_of_date != request.cutoff.astimezone(ZoneInfo(request.timezone)).date():
        raise ValueError("valuation date differs from research cutoff")
    required = {"current_diluted_shares", "terminal_growth",
                "units.currency", "units.amount_scale", "units.share_scale"}
    required.update({"current_net_income", "cost_of_equity"} if equity else {
        "current_revenue", "current_working_capital", "net_debt", "discount_rate"})
    for index, period in enumerate(model.periods):
        required.update(f"periods.{index}.{field.name}" for field in fields(period)
                        if field.name not in {"label", "discount_years"})
    missing = required - proposal.assumptions.keys()
    if missing or not proposal.evidence_ids:
        return {"status": "unavailable", "limitations": [
            "Missing evidence-linked assumption coverage: " + ", ".join(sorted(missing or {"evidence_ids"}))]}
    known = _known_ids(snapshot)
    if any(set(support.evidence_ids) - known for support in proposal.assumptions.values()):
        raise ValueError("valuation assumptions invented evidence identifiers")
    facts = {fact.id: fact for fact in snapshot.facts if fact.id in known}
    if proposal.accounting_basis is None:
        return {"status": "unavailable", "limitations": ["Opening accounting basis is unspecified."]}
    if snapshot.instrument and model.units.currency != snapshot.instrument.reporting_currency:
        return {"status": "unavailable", "limitations": ["Model currency differs from reporting currency."]}
    opening_dates = set()
    opening_bindings = {}
    opening_metrics = ({"current_net_income": "net_income_common",
                        "current_diluted_shares": "diluted_shares"} if equity else {
                        "current_revenue": "revenue", "current_working_capital": "working_capital",
                        "net_debt": "net_debt", "current_diluted_shares": "diluted_shares"})
    share_proxy = request.share_count_basis == "latest_quarter_diluted_proxy"
    if share_proxy:
        opening_metrics["current_diluted_shares"] = "weighted_average_diluted_shares"
    for name, metric in opening_metrics.items():
        support = proposal.assumptions[name]
        scale = model.units.share_scale if name == "current_diluted_shares" else model.units.amount_scale
        compatible = [facts[sid] for sid in support.evidence_ids if sid in facts
                      and facts[sid].metric == metric
                      and facts[sid].basis == proposal.accounting_basis
                      and facts[sid].segment is None
                      and 0 <= (model.as_of_date - facts[sid].period_end).days <= 120
                      and ((facts[sid].period_type == "duration" and facts[sid].period_start is not None
                           and 360 <= (facts[sid].period_end - facts[sid].period_start).days + 1 <= 371
                           if name in {"current_revenue", "current_net_income"}
                           else facts[sid].period_type == "instant")
                           if not (name == "current_diluted_shares" and share_proxy) else (
                               facts[sid].period_type == "duration" and facts[sid].period_start is not None
                               and 75 <= (facts[sid].period_end - facts[sid].period_start).days + 1 <= 105))
                      and (facts[sid].unit == "shares" if name == "current_diluted_shares"
                           else facts[sid].currency == facts[sid].unit == model.units.currency)
                      and facts[sid].normalized_value == getattr(model, name) * scale]
        if support.kind != "reported" or len(compatible) != 1:
            return {"status": "unavailable", "limitations": [f"Opening input not bound to a matching fact: {name}"]}
        if name == "current_diluted_shares" and share_proxy:
            newer = [fact for fact in facts.values()
                     if fact.metric == "weighted_average_diluted_shares"
                     and fact.segment is None and fact.basis == proposal.accounting_basis
                     and fact.period_type == "duration" and fact.period_start is not None
                     and 75 <= (fact.period_end - fact.period_start).days + 1 <= 105
                     and fact.unit == "shares" and compatible[0].period_end < fact.period_end <= model.as_of_date]
            if newer:
                return {"status": "unavailable", "limitations": [
                    "Share-count proxy is not the latest eligible quarter; opening anchors require reconciliation."]}
        opening_dates.add(compatible[0].period_end)
        fact = compatible[0]
        opening_bindings[name] = {
            "fact_id": fact.id, "classification": "derived" if fact.inputs else "reported",
            "inputs": fact.inputs, "formula": fact.formula, "location": fact.location,
            "period_start": fact.period_start, "period_end": fact.period_end,
            "period_type": fact.period_type,
        }
    if len(opening_dates) != 1:
        return {"status": "unavailable", "limitations": ["Opening facts require a reconciled common period end."]}
    result = equity_dcf_valuation(model) if equity else dcf_valuation(model)
    return {"status": "illustrative", "result": asdict(result),
            **({"opening_input_bindings": opening_bindings,
                "valuation_method": request.valuation_method}
               if request.quality_revision != "foundation" else {}),
            **({"share_count_basis": request.share_count_basis} if share_proxy else {}),
            "limitations": [*result.limitations, *proposal.scope_limitations,
                            *(["Per-share values use the latest-quarter weighted-average diluted-share "
                               "proxy, not point-in-time diluted capitalization; future issuance, "
                               "buybacks and option dilution require separate schedules."] if share_proxy else []),
                            "Sector schedules and source-linked assumptions need independent review."]}


def _report(request, draft, snapshot, gaps, language: ReportLanguage | None = None):
    language = language or request.report_language
    chinese = language == "Chinese"
    title = "深度研究报告" if chinese else "Deep research report"
    status = "需要复核 / 未评级" if chinese else "Needs review / Unrated"
    text = [f"# {request.ticker} — {title}", f"\n{status}",
            f"\nAs of: {request.cutoff.isoformat()}",
            "\nResearch foundation preview; not an accepted investment assessment."]
    sources = {source.id: source for source in snapshot.sources}
    number = {source.id: index + 1 for index, source in enumerate(snapshot.sources)}
    if draft:
        for section in draft.sections:
            if set(section.evidence_ids) - _known_ids(snapshot):
                raise ValueError("draft invented evidence identifiers")
            body = section.text
            for sid in sorted(number, key=len, reverse=True):
                body = body.replace(f"[{sid}]", f"[{number[sid]}]")
            text.append(f"\n## {section.title}\n\n{body}")
    text.append("\n## 重要限制 / Material limitations\n")
    limitations = (*gaps, *(draft.limitations if draft else ()))
    text.extend(f"- {gap}" for gap in dict.fromkeys(limitations))
    text.append("\n## 来源 / Sources\n")
    text.extend(f"{number[sid]}. {source.title} — {source.url}"
                for sid, source in sources.items())
    return "\n".join(text) + "\n"


def run_research(request: ResearchRequest, services: ResearchServices) -> ResearchResult:
    """Run or resume research. Never implicitly instantiate providers or publishers."""
    # model_copy/model_construct are not validation boundaries.
    request = ResearchRequest.model_validate_json(request.model_dump_json(warnings="error"))
    frozen = load_request_inputs(request)
    prior = eligible_prior(frozen["prior_dossier_path"], request) if "prior_dossier_path" in frozen else None
    identity = request_identity(request, frozen)
    replay_identity = getattr(services.models, "identity", None)
    identity = digest({"request": identity, "model_service": replay_identity})
    factory = services.storage or CheckpointStore
    store = factory(request.output_dir, identity)
    with store.lock():
        # A completed immutable replay is read back, not rewritten with a new
        # elapsed time (which would invalidate dossier artifact hashes).
        result_path = store.directory / "result.json"
        if result_path.is_symlink():
            raise ValueError("result cannot be a symlink")
        if result_path.exists():
            saved_result = ResearchResult.model_validate(read_json(result_path))
            if saved_result.stop_reason == "completed_needs_review":
                required = _required_artifacts(request)
                if (saved_result.ticker != request.ticker or saved_result.cutoff != request.cutoff
                        or not required <= saved_result.artifacts.keys()
                        or store.load_stage("completed-result", {}) != saved_result.model_dump(mode="json")):
                    raise ValueError("completed result identity or manifest mismatch")
                for name, expected_hash in saved_result.artifact_hashes.items():
                    if Path(name).name != name:
                        raise ValueError("invalid artifact name")
                    path = store.directory / name
                    if (path.is_symlink() or saved_result.artifacts[name] != str(path)
                            or hashlib.sha256(read_bytes(path)).hexdigest() != expected_hash):
                        raise ValueError("completed research artifact mismatch")
                return saved_result
        previous = store.load_stage("resources", {}) or {}
        recovery = getattr(services.models, "recovery_context", None)
        case_recovery = getattr(services.models, "case_recovery_context", None)
        candidate_recovery = getattr(services.models, "candidate_recovery_context", None)
        if recovery is not None and request.financial_case_path:
            raise ValueError("historical-prefix recovery cannot import stages into a new financial case")
        if case_recovery is not None:
            # This is a separately validated same-case prefix, never permission
            # to pass a legacy recovery dictionary through the case boundary.
            from .case_recovery import CaseRecoveryModelService

            if recovery is not None or not isinstance(services.models, CaseRecoveryModelService):
                raise ValueError("case recovery requires the validated case-recovery service")
            services.models.validate_request(request, frozen)
            recovery = case_recovery
        if candidate_recovery is not None:
            from .finalization_recovery import FinalizationRecoveryModelService

            if recovery is not None or not isinstance(services.models, FinalizationRecoveryModelService):
                raise ValueError("candidate recovery requires its validated continuation service")
            services.models.validate_request(request, frozen)
            recovery = candidate_recovery

        def current_recovery_context():
            if candidate_recovery is not None:
                return services.models.candidate_recovery_context
            return (services.models.case_recovery_context if case_recovery is not None
                    else services.models.recovery_context)

        if recovery is not None:
            if not isinstance(recovery, dict) or recovery.get("schema_version") != 1:
                raise ValueError("invalid explicit recovery context")
            restore = getattr(services.models, "restore_recovery_context", None)
            if previous.get("recovery") is not None:
                if restore is None:
                    raise ValueError("recovery service cannot restore provenance")
                restore(previous["recovery"])
                recovery = current_recovery_context()
            previous_usage = Usage.model_validate(
                previous.get(
                    "budget_usage",
                    recovery["initial_budget_usage"],
                )
            )
            previous_elapsed = previous.get(
                "elapsed_seconds", recovery["previous_elapsed_seconds"]
            )
        else:
            if "budget_usage" in previous or "recovery" in previous:
                raise ValueError("explicit recovery checkpoint requires its recovery service")
            previous_usage = Usage.model_validate(previous.get("usage", {}))
            previous_elapsed = previous.get("elapsed_seconds", 0)
        tracker = BudgetTracker(
            request.budget,
            previous_usage=previous_usage,
            elapsed_seconds=previous_elapsed,
        )
        if previous.get("dispatched"):
            tracker.record(Usage(complete=False))
        dispatch_unsettled = bool(previous.get("dispatched"))
        usage_by_stage = dict(previous.get("by_stage", {}))
        call_timings = list(previous.get("call_timings", []))
        active_stage = None
        role_indices = defaultdict(int)
        outputs, reviews = {}, []
        active_admission_findings = []
        pre_editor_findings = []
        retired_reader_texts = set()
        gaps = ["V2 source coverage and company-specific model acceptance remain pending."]
        snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff)
        draft, drafts, valuation = None, {}, {"status": "unavailable"}
        evidence_led = request.quality_revision != "foundation"
        bounded_review = request.quality_revision == "evidence-led-bounded"
        coverage_envelope = coverage_output_envelope(request.coverage_batch_policy)

        def coverage_batches(issues):
            return coverage_batches_for_policy(issues, request.coverage_batch_policy)

        def numbered_contract(stage):
            if candidate_recovery is None or not candidate_recovery.get("revision_generation"):
                return None
            match = re.fullmatch(
                rf"(?:revise_report|verify_revised_report)-({GENERATION_PATTERN})(?:-coverage-(?:0|[1-9][0-9]*))?",
                stage)
            if match is None:
                return None
            number = int(match[1])
            if number == candidate_recovery["revision_generation"]:
                return revision_contract(candidate_recovery["reader_revision_policy"],
                                         candidate_recovery["revision_contract_sha256"])
            records = [item for item in candidate_recovery.get("prior_revision_contracts", ())
                       if item["generation"] == number]
            if len(records) != 1:
                raise ValueError("numbered revision lacks unambiguous historical contract")
            return revision_contract(records[0]["policy"], records[0]["contract_sha256"])

        verified_readers = {}
        reader_verifications = {}
        model_checkpoints = {}
        coverage_reuse = {}
        factual_payloads = {}
        finalization_candidate = {}
        candidate_review_stage = None
        finalization_plans = {}
        calculated_values = ()
        investigation_queries = ()
        investigation_tasks = {}
        investigation_cycles = []
        investigation_ledger = None
        proposal = ValuationProposal(unsupported_inputs=("valuation has not completed",))
        case_context = None
        case_material_delivery = {}
        draft_schema = CaseReportDraft if request.financial_case_path else ReportDraft
        stop_reason = "completed_needs_review"
        failure_type = None
        failure_reason = None
        failure_diagnostic = None
        failed_stage = None

        def aggregate_usage():
            if recovery is None:
                return tracker.usage
            if case_recovery is not None or candidate_recovery is not None:
                # A new authorized budget covers only continuation calls. Keep
                # the source's known spend in cumulative reporting, and never
                # represent its unsettled call as measured or free.
                historical = Usage.model_validate(recovery["previous_usage"])
                return Usage(
                    input_tokens=historical.input_tokens + tracker.usage.input_tokens,
                    output_tokens=historical.output_tokens + tracker.usage.output_tokens,
                    cached_input_tokens=historical.cached_input_tokens + tracker.usage.cached_input_tokens,
                    reasoning_output_tokens=historical.reasoning_output_tokens + tracker.usage.reasoning_output_tokens,
                    complete=(historical.complete and tracker.usage.complete
                              if candidate_recovery is not None else False),
                )
            # The known historical counters are useful for budget accounting, but
            # the source dispatch was never measured.  It remains incomplete for
            # every cumulative artifact and result produced by recovery.
            return tracker.usage.model_copy(update={"complete": False})

        def save_resources(dispatched=None):
            resource_data = {
                "usage": aggregate_usage().model_dump(mode="json"),
                "elapsed_seconds": tracker.elapsed_seconds,
                "dispatched": dispatch_unsettled if dispatched is None else dispatched,
                "active_stage": active_stage if (dispatch_unsettled if dispatched is None else dispatched) else None,
                "failure_reason": failure_reason, "failure_diagnostic": failure_diagnostic,
                "failed_stage": failed_stage,
                "by_stage": usage_by_stage,
                "call_timings": call_timings,
            }
            if recovery is not None:
                resource_data.update(
                    budget_usage=tracker.usage.model_dump(mode="json"),
                    recovery=current_recovery_context(),
                )
            store.save_stage("resources", {}, resource_data)
            save_finalization_checkpoint()

        def save_finalization_checkpoint():
            # Only current-contract, frozen case runs can be resumed from a
            # factual-reviewed candidate. Never upgrade legacy recovery artifacts.
            if (not request.financial_case_path or not bounded_review or not candidate_review_stage
                    or case_recovery is not None or (recovery is not None and candidate_recovery is None)):
                return
            usage = aggregate_usage()
            if dispatch_unsettled:
                usage = usage.model_copy(update={"complete": False})
            atomic_write(store.directory / "finalization_checkpoint.json", canonical_json({
                "schema_version": 1, "engine_version": ENGINE_VERSION,
                "wire_schema_version": WIRE_SCHEMA_VERSION,
                "request": request.model_dump(mode="json"),
                "model_service_identity": getattr(services.models, "source_model_identity", replay_identity),
                "run_identity": identity,
                "frozen_input_hashes": {name: hashlib.sha256(content).hexdigest()
                                        for name, content in frozen.items()},
                "stages": model_checkpoints, "usage": usage.model_dump(mode="json"),
                "dispatched": dispatch_unsettled,
                "candidate": finalization_candidate, "candidate_review_stage": candidate_review_stage,
            }))

        def model_payload(stage, role, data, schema, language=None, coverage_only=False,
                          *, record_delivery=False, role_call_index=None):
            role_index = role_indices[role] if role_call_index is None else role_call_index
            queries = tuple(dict.fromkeys((*investigation_queries, *_research_queries(
                role, data, outputs)))) if evidence_led else ()
            if coverage_only and (not bounded_review or role != "verifier"):
                raise ValueError("coverage-only calls require the bounded reader verifier")
            if coverage_only:
                payload = coverage_model_payload(request, data, stage, role_index, language)
                if stage.startswith(REVISED_REVIEW_STAGE + "-coverage-"):
                    payload["reader_revision_policy"] = READER_REVISION_POLICY
                if stage.startswith(FROZEN_REVIEW_STAGE + "-coverage-"):
                    payload["verification_repair_policy"] = VERIFICATION_REPAIR_POLICY
                    payload["system"] += " " + VERIFICATION_REPAIR_REQUIREMENTS
                if generic_coverage_stage(stage):
                    contract = numbered_contract(stage)
                    payload["reader_revision_policy"] = contract.policy
                    payload["revision_contract_sha256"] = contract.sha256
                    if contract.coverage_requirements:
                        payload["system"] += contract.coverage_requirements
                return payload
            payload = {**instruction(role, schema), "evidence": (
                _prompt_evidence(snapshot, queries)),
                       "research": data, "cutoff": request.cutoff.isoformat(),
                       "mandate": request.mandate, "stage": stage, "role_call_index": role_index,
                       "valuation_months": request.valuation_months,
                       "return_months": request.return_months,
                       "language": language or request.internal_language}
            if evidence_led:
                payload["quality_requirements"] = {
                    "revision": "evidence-led-bounded-1" if bounded_review else "evidence-led-1",
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
            if stage in {REVISE_STAGE, REVISED_REVIEW_STAGE}:
                payload["reader_revision_policy"] = READER_REVISION_POLICY
            if stage == FROZEN_REVIEW_STAGE:
                payload["verification_repair_policy"] = VERIFICATION_REPAIR_POLICY
                payload["system"] += " " + VERIFICATION_REPAIR_REQUIREMENTS
            if generic_writer_stage(stage) or generic_review_stage(stage):
                contract = numbered_contract(stage)
                payload["reader_revision_policy"] = contract.policy
                payload["revision_contract_sha256"] = contract.sha256
                payload["system"] += " " + (
                    contract.writer_requirements if generic_writer_stage(stage)
                    else contract.factual_requirements)
            if case_context is not None and not coverage_only and stage not in {"planner", "independent_challenge"}:
                payload["financial_case"] = case_context.model_context()
                payload["case_reader_delivery"] = case_reader_delivery(case_context)
                payload["decision_led_research"] = deepcopy(QUESTION_LED_REQUIREMENTS)
                if role == "editor":
                    writer_issues = split_compound_obligations(
                        limitation_packet(data.get("limitations", ())),
                        evidence_catalog(snapshot, calculated_values,
                                         eligible_ids=_known_ids(snapshot), case_context=case_context),
                        reader_revision=stage == REVISE_STAGE or generic_writer_stage(stage),
                        verification_repair=generic_writer_stage(stage),
                    )
                    payload["compound_obligation_guidance"] = [
                        item for item in writer_issues if "compound_obligation" in item
                    ]
                payload["case_reader_requirements"] = (
                    RENDERED_READER_POLICY if "rendered_reader" in data else CASE_READER_REQUIREMENTS)
                if record_delivery:
                    case_material_delivery[stage] = {
                        "case_context_sha256": digest(payload["financial_case"]),
                        "payload_sha256": digest(payload),
                        "delivery": "full_case_context_in_serialized_payload",
                    }
            if role == "valuation":
                schema_type = EquityDCFModelInput if request.valuation_method == "equity_fcfe" else FCFFModelInput
                payload["financial_model_schema"] = TypeAdapter(schema_type).json_schema()
                if evidence_led:
                    payload["share_count_basis"] = request.share_count_basis
                    payload["share_count_requirement"] = (
                        "Opening current_diluted_shares must bind to an eligible fact. "
                        "Only when latest_quarter_diluted_proxy is explicitly selected may it use "
                        "a weighted_average_diluted_shares quarterly duration fact at the common "
                        "opening period end. This is a disclosed proxy, never an instant fact.")
                if request.valuation_method == "equity_fcfe":
                    payload["system"] = payload["system"].replace(
                        "company-specific FCFF input", "company-specific equity-cash-flow input")
                    payload["valuation_method"] = "equity_fcfe"
                    payload["equity_model_requirements"] = (
                        "Use net income attributable to common, not consolidated income including "
                        "noncontrolling interests. Reconcile unusual gains and retained regulatory "
                        "capital explicitly. Do not treat customer cash or receivables as corporate "
                        "net debt, add back SBC, or divide present equity value by future shares. "
                        "Current net income is an annual opening anchor. Forward assumptions need "
                        "source-linked rationale; no automatic fixed capital-retention percentage.")
            return payload

        def call(stage, role, data, schema, finalization=False, language=None,
                 coverage_only=False):
            nonlocal dispatch_unsettled, active_stage
            active_stage = stage
            payload = model_payload(stage, role, data, schema, language, coverage_only,
                                    record_delivery=True)
            role_indices[role] += 1
            reuse_key = digest({"wire": WIRE_SCHEMA_VERSION, "payload": {
                key: value for key, value in payload.items() if key not in {"stage", "role_call_index"}
            }}) if coverage_only else None

            def remember_coverage(output):
                if reuse_key is None:
                    return
                checked = check_dispositions(output, data["limitation_review"], data["rendered_reader"])
                if (checked.reviewed_report and not checked.contradicted_claim_ids
                        and not checked.supported_claim_ids
                        and not any(f.severity in {"warning", "critical"} for f in checked.findings)):
                    coverage_reuse[reuse_key] = (stage, checked.model_dump(mode="json"))

            cached = store.load_stage(stage, payload)
            if cached is not None:
                stage_usage = usage_by_stage.get(stage, [])
                model_checkpoints[stage] = {
                    "role": role, "inputs_hash": digest(payload), "output_hash": digest(cached),
                    "output": cached,
                    "usage": Usage.model_validate({key: stage_usage[-1][key] for key in (
                        "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "complete"
                    )}).model_dump(mode="json") if stage_usage else Usage(complete=False).model_dump(mode="json"),
                }
                output = schema.model_validate(cached)
                remember_coverage(output)
                return output
            if (reuse_key is not None and reuse_key in coverage_reuse
                    and not (candidate_recovery and candidate_recovery.get("revision_generation"))
                    and not (candidate_recovery and (
                        candidate_recovery.get("reader_revision_policy")
                        or candidate_recovery.get("verification_repair_policy"))
                             and services.models.has_saved_reply(stage, payload))):
                reused_stage, data = coverage_reuse[reuse_key]
                output = schema.model_validate(data)
                usage = Usage()
                usage_by_stage.setdefault(stage, []).append({
                    "role": role, "model": request.models[role].model,
                    "effort": request.models[role].effort, "usage_origin": "validated_reuse",
                    "reused_from_stage": reused_stage, **usage.model_dump(mode="json"),
                })
                store.save_stage(stage, payload, output.model_dump(mode="json"))
                model_checkpoints[stage] = {
                    "role": role, "inputs_hash": digest(payload), "output_hash": digest(output),
                    "output": output.model_dump(mode="json"), "usage": usage.model_dump(mode="json"),
                }
                save_resources()
                return output
            # UTF-8 bytes are a conservative input bound, plus an output envelope.
            output_envelope = coverage_envelope if coverage_only else 16_000
            envelope = model_input_bytes(payload, role=role, output_token_envelope=output_envelope,
                                         valuation_method=request.valuation_method) + output_envelope
            origin = "current_live"
            origin_resolver = getattr(services.models, "call_origin", None)
            if recovery is not None and origin_resolver is None:
                raise ValueError("explicit recovery service must classify every model call")
            if origin_resolver is not None:
                if recovery is None:
                    raise ValueError("model-call origin overrides require explicit recovery")
                origin = origin_resolver(role, payload, request)
            if origin not in {"current_live", "imported_historical", "validated_diagnostic"}:
                raise ValueError("invalid model-call usage origin")
            if origin == "validated_diagnostic" and any(
                item.get("usage_origin") == "validated_diagnostic"
                for item in usage_by_stage.get(stage, [])
                if isinstance(item, dict)
            ):
                raise BudgetExhausted("validated_diagnostic_already_consumed")
            permit = None
            if origin == "current_live":
                if (payload.get("verification_repair_policy") == VERIFICATION_REPAIR_POLICY
                        or payload.get("reader_revision_policy") in {
                            GENERIC_REVISION_POLICY, V4_CONTRACT.policy, V5_CONTRACT.policy}):
                    prompt_limit = getattr(services.models, "max_prompt_utf8_bytes", None)
                    prompt_bytes = len(model_prompt(payload))
                    if prompt_limit is not None and prompt_bytes > prompt_limit:
                        store.save_stage("verification-prompt-admission", {}, {
                            "stage": stage, "prompt_bytes": prompt_bytes,
                            "limit_bytes": prompt_limit, "fits": False,
                        })
                        raise BudgetExhausted("revision_prompt_size_limit" if
                            payload.get("reader_revision_policy") in {
                                GENERIC_REVISION_POLICY, V4_CONTRACT.policy, V5_CONTRACT.policy}
                            else "verification_prompt_size_limit")
                permit = tracker.reserve(envelope, finalization=finalization)
                dispatch_unsettled = True
                save_resources()
                timeout_seconds = permit.timeout_seconds
            else:
                # Imported replies and diagnostics are already materialized and
                # cannot dispatch here. Their known usage is already in the
                # explicit recovery seed before the first stage admission.
                timeout_seconds = tracker.admit(finalization=finalization)
            call_started = tracker.elapsed_seconds
            timing = {"stage": stage, "role": role, "model": request.models[role].model,
                      "effort": request.models[role].effort, "usage_origin": origin,
                      "service_kind": getattr(services.models, "kind", "injected"),
                      "completed": False, "stage_accepted": False}
            try:
                reply = services.models.complete(role, {**payload,
                    "timeout_seconds": timeout_seconds, "max_output_tokens": output_envelope}, request)
                reply = ModelReply.model_validate(reply)
                if origin == "current_live":
                    tracker.complete(permit, reply.usage)
                timing.update(duration_seconds=max(0, tracker.elapsed_seconds - call_started),
                              completed=reply.usage.complete)
                call_timings.append(timing)
                usage_by_stage.setdefault(stage, []).append({
                    "role": role, "model": request.models[role].model,
                    "effort": request.models[role].effort,
                    "usage_origin": origin,
                    **reply.usage.model_dump(mode="json")})
                save_resources(dispatched=False)
                dispatch_unsettled = False
            except BaseException:
                if timing not in call_timings:
                    timing["duration_seconds"] = max(0, tracker.elapsed_seconds - call_started)
                    call_timings.append(timing)
                if origin == "current_live":
                    tracker.record(Usage(complete=False))
                save_resources()
                raise
            if not reply.usage.complete:
                raise BudgetExhausted("usage_incomplete")
            if tracker.usage.total_tokens > request.budget.total_tokens:
                raise BudgetExhausted("token_budget_exceeded")
            if tracker.elapsed_seconds >= request.budget.wall_seconds:
                raise BudgetExhausted("wall_deadline_exceeded")
            output = schema.model_validate(reply.data)
            if isinstance(output, AnalysisOutput):
                _validate_analysis(output, snapshot)
                if len({claim.id for claim in output.claims}) != len(output.claims):
                    raise ValueError("duplicate claim identifiers within a stage")
                known_questions = {question["id"] for item in outputs.values()
                                   for question in item.get("questions", [])}
                known_questions.update(question.id for question in output.questions)
                if any(finding.question_id not in known_questions for finding in output.findings):
                    raise ValueError("finding references an unknown research question")
                earlier_claims = {claim["id"]: claim for name, item in outputs.items()
                                  if name != role for claim in item.get("claims", [])}
                for claim in output.claims:
                    if claim.id in earlier_claims and earlier_claims[claim.id] != claim.model_dump(mode="json"):
                        raise ValueError("ambiguous claim identifier across stages")
            store.save_stage(stage, payload, output.model_dump(mode="json"))
            model_checkpoints[stage] = {
                "role": role, "inputs_hash": digest(payload), "output_hash": digest(output),
                "output": output.model_dump(mode="json"), "usage": reply.usage.model_dump(mode="json"),
            }
            remember_coverage(output)
            save_finalization_checkpoint()
            timing["stage_accepted"] = True
            save_resources()
            return output

        def time_plan(workload):
            return finalization_time_plan(
                workload["calls"], call_timings, request.models,
                remaining_seconds=request.budget.wall_seconds - tracker.elapsed_seconds,
            )

        def prepare_draft(candidate, language):
            if any(set(section.evidence_ids) - _known_ids(snapshot)
                   for section in candidate.sections):
                raise ValueError("draft invented evidence identifiers")
            eligible_facts = tuple(fact for fact in snapshot.facts
                                   if fact.id in _known_ids(snapshot))
            return candidate.model_copy(update={"sections": tuple(
                section.model_copy(update={"text": render_calculations(render_references(
                    section.text, eligible_facts, language), calculated_values, language,
                    cite=bounded_review and case_context is not None,
                    scenario_delivery=case_reader_delivery(case_context) if case_context is not None else None)
                    if evidence_led else render_references(section.text, eligible_facts, language)})
                for section in candidate.sections)})

        def validate_translation(source, translated):
            if (translated.investment_view != source.investment_view
                    or len(translated.sections) != len(source.sections)
                    or len(translated.limitations) != len(source.limitations)
                    or any(translated_section.evidence_ids != source_section.evidence_ids
                           for source_section, translated_section
                           in zip(source.sections, translated.sections, strict=True))):
                raise ValueError("translated draft changed report structure or evidence links")
            if request.financial_case_path and [s.purpose for s in source.sections] != [
                    s.purpose for s in translated.sections]:
                raise ValueError("translated draft changed case section purposes")

        def case_valuation():
            # A schedule review cannot authorize independently generated forecasts.
            # Until reviewed scenarios bind to these schedules, there is no live
            # valuation call and no raw numerical result to leak to later roles.
            candidate = ValuationProposal(unsupported_inputs=case_context.limitations)
            scoped = scope_calculation({"status": "unavailable",
                                        "limitations": list(case_context.limitations)}, case_context.scope)
            return candidate, scoped

        def reader_inputs(*, include_retired=False):
            # Mandatory model caveats do not depend on the editor remembering them.
            material = [ReaderIssue(
                message=message, provenance_id=f"valuation.limitations[{index}]",
                severity="critical", category="financial", code="valuation_scope",
            ) for index, message in enumerate(valuation.get("limitations", []))
                if include_retired or message not in retired_reader_texts]
            if valuation.get("status") == "unavailable" and not material:
                material.append(ReaderIssue(
                    message="Valuation unavailable; this report supplies no supported price target.",
                    provenance_id="valuation.status", severity="critical", category="financial",
                ))
            current_gaps = gaps if include_retired else [text for text in gaps if text not in retired_reader_texts]
            return [*current_gaps, *material]

        def record_investigations(cycle):
            for task in collect_tasks(outputs, proposal, valuation):
                origins = tuple(origin.model_copy(update={
                    "occurrence_id": f"{cycle}:{origin.occurrence_id}",
                    "origin_path": f"{cycle}.{origin.origin_path}",
                    "provenance_id": f"{cycle}:{origin.provenance_id}",
                }) for origin in task.origins)
                previous_task = investigation_tasks.get(task.id)
                if previous_task:
                    origins = (*previous_task.origins, *origins)
                investigation_tasks[task.id] = task.model_copy(update={"origins": origins})
            # Valuation blockers must not be crowded out by long analyst gap lists.
            ordered = sorted(investigation_tasks.values(), key=lambda task: (
                0 if any(origin.source_kind == "valuation_proposal" for origin in task.origins) else 1,
                task.id,
            ))
            return make_ledger(ordered, snapshot)

        def required_limitations():
            # Retrieval's followup cap is NOT a cap on mandatory reader coverage.
            ledger_texts = (
                [entry.text for entry in investigation_ledger.entries if entry.status != "resolved"]
                if bounded_review and investigation_ledger is not None else []
            )
            return list(dict.fromkeys((*gaps, *ledger_texts)))

        def verify_reader(candidate, language, stage, extra=None, *, authored=None):
            nonlocal finalization_candidate, candidate_review_stage, active_stage
            active_stage = stage
            rendered = render_reader(request, candidate, snapshot, reader_inputs(), language,
                                     compact=bounded_review and case_context is not None,
                                     bind_case_state=bool(request.financial_case_path), case_context=case_context,
                                     bind_cashflow_inputs=ENGINE_VERSION == "research-v2-preview-12")
            rendered_hash = hashlib.sha256(rendered.reader_text.encode("utf-8")).hexdigest()
            if stage == FROZEN_REVIEW_STAGE and (
                    not candidate_recovery
                    or (candidate_recovery.get("verification_repair_policy") != VERIFICATION_REPAIR_POLICY
                        and not candidate_recovery.get("revision_generation"))
                    or (candidate_recovery.get("verification_repair_policy") == VERIFICATION_REPAIR_POLICY
                        and rendered_hash != candidate_recovery["candidate"]["reader_sha256"])):
                raise ValueError("verification repair changed the authorized reader bytes")
            limitations = limitation_packet([*required_limitations(), *candidate.limitations])
            if bounded_review:
                origins = case_context.limitation_origins if case_context else {}
                limitations = enrich_issues(
                    limitations, outputs, pre_editor_findings,
                    protected_texts=[text for text in valuation.get("limitations", [])
                                     if not origins.get(text)
                                     or any(not origin["retirable"] for origin in origins[text])],
                    limitation_origins=origins,
                )
                if rendered.limitations_audit.get("reader_compaction", {}).get("active"):
                    case_review_exemptions = nonmandatory_review_finding_texts(
                        case_context.review.findings
                        if case_context is not None and case_context.review is not None else ()
                    )
                    operating_review = (
                        case_context.operating_scenarios.model_context.get("review")
                        if case_context is not None and case_context.operating_scenarios is not None
                        else None
                    )
                    operating_review_exemptions = nonmandatory_review_finding_texts(
                        operating_review.get("findings", ()) if operating_review else ()
                    )
                    financial_prerequisites = tuple(
                        text for text in valuation.get("limitations", ())
                        if not origins.get(text) or any(
                            not origin["retirable"]
                            and not (
                                origin["origin_id"].startswith("operating.review.info.")
                                and text in operating_review_exemptions
                            )
                            and not (
                                origin["origin_id"].startswith("case.review.finding.")
                                and text in case_review_exemptions
                            )
                            for origin in origins[text]
                        )
                    )
                    limitations = [{
                        **item,
                        "reader_coverage_required": requires_reader_coverage(
                            item, financial_prerequisite_texts=financial_prerequisites
                        ),
                    } for item in limitations]
            stage_contract = numbered_contract(stage) if generic_review_stage(stage) else None
            pending_contexts = tuple((extra or {}).get("pending_coverage_contexts", ()))
            if stage_contract == V5_CONTRACT:
                if pending_entries(pending_contexts) != tuple((extra or {}).get("pending_coverage", ())):
                    raise ValueError("v5 factual pending context differs from its eligibility")
                limitations = project_pending_issues(limitations, pending_contexts)
            review_data = {
                "draft": candidate.model_dump(mode="json"), "analyses": outputs,
                "valuation": valuation, "rendered_reader": rendered.reader_text,
                "valuation_inputs": proposal.model_dump(mode="json"),
                "calculated_values": [value.model_dump(mode="json") for value in calculated_values],
                "rendered_reader_sha256": rendered_hash, **(extra or {}),
                "limitation_review": limitations,
                "limitation_policy": LIMITATION_POLICY,
            }
            if bounded_review:
                # Save the authored bytes before review; this is NOT a verified export.
                store.save_stage(f"{stage}-reader-candidate", {"reader_sha256": rendered_hash}, {
                    "reader_sha256": rendered_hash, "verification_status": "unverified",
                    "reader_text": rendered.reader_text,
                })
                reader_verifications[language] = {
                    "reader_sha256": rendered_hash, "stage": stage,
                    "review": ReaderVerification().model_dump(mode="json"),
                    "exported": False, "coverage_batches": [],
                }
                factual_data = {key: value for key, value in review_data.items()
                                if key not in {"limitation_review", "limitation_policy"}}
                resolution_evidence = evidence_catalog(
                    snapshot, calculated_values, eligible_ids=_known_ids(snapshot), case_context=case_context,
                    issues=limitations, reader=rendered.reader_text,
                    verification_repair=stage == FROZEN_REVIEW_STAGE or generic_review_stage(stage),
                )
                limitations = split_compound_obligations(
                    limitations, resolution_evidence,
                    reader_revision=stage in {REVISED_REVIEW_STAGE, FROZEN_REVIEW_STAGE}
                        or generic_review_stage(stage),
                    verification_repair=stage == FROZEN_REVIEW_STAGE or generic_review_stage(stage))
                factual_data.update(
                    inherited_issues=limitations, resolution_evidence=resolution_evidence,
                    resolution_witness_contract=resolution_witness_contract(
                        resolution_evidence, rendered.reader_text),
                    issue_resolution_policy=(LIFECYCLE_POLICY + GENERIC_CASHFLOW_POLICY
                        if generic_review_stage(stage) else LIFECYCLE_POLICY),
                    conclusion_scope=case_context.scope.model_dump(mode="json") if case_context else None,
                    paragraph_citations=rendered.limitations_audit.get("paragraph_citations", []),
                    citation_policy="Assess citation scope at each factual paragraph or table. "
                        "A section evidence list is not paragraph support. Flag unsupported material "
                        "claims and missing or misleading paragraph citations for repair; do not "
                        "invent sources for an uncited inference or infer support from an ID alone. "
                        "Code-generated model_appendix calculation links are local calculation "
                        "provenance, not issuer evidence; inspect rendering_provenance and the "
                        "calculation catalog. Do not demand an issuer citation for an explicitly "
                        "analyst-selected assumption, but verify its classification and rationale.",
                )
                if authored is None:
                    raise ValueError("bounded reader review requires the exact authored draft")
                provenance = reader_provenance(
                    authored, candidate, tuple(f for f in snapshot.facts if f.id in _known_ids(snapshot)),
                    calculated_values, language, rendered.reader_text,
                    cite=case_context is not None, request=request, snapshot=snapshot, issues=reader_inputs(),
                    case_context=case_context, bind_case_state=bool(request.financial_case_path),
                    bind_cashflow_inputs=ENGINE_VERSION == "research-v2-preview-12",
                )
                bound_calculations = {
                    section["section_index"]: {
                        identifier for paragraph in section["paragraphs"]
                        for binding in paragraph["bindings"] for identifier in binding["calculation_ids"]
                    } for section in provenance["sections"]
                }
                for citation in rendered.limitations_audit.get("paragraph_citations", []):
                    if set(citation.get("calculation_ids", ())) - bound_calculations[citation["section_index"]]:
                        raise ValueError("reader calculation citation lacks authored provenance")
                factual_data["rendering_provenance"] = {
                    key: value for key, value in provenance.items() if key != "rendering_inputs"
                }
                factual_data["rendered_reader_policy"] = RENDERED_READER_POLICY
                store.save_stage(f"{stage}-rendering-provenance",
                                 {"reader_sha256": rendered_hash}, provenance)
                factual_data["review_scope"] = (
                    "Verify the exact full report's factual, numerical and causal claims. "
                    "Do not return limitation dispositions here; complete per-issue material "
                    "caveat coverage is checked in separate mandatory batches against these same bytes.")

                def coverage_data(items):
                    return coverage_review_data(items, rendered.reader_text,
                                                review_data["limitation_policy"])

                factual_schema = (RevisionLifecycleVerification if generic_review_stage(stage)
                                  else LifecycleVerification)
                factual_payloads[stage] = model_payload(
                    stage, "verifier", factual_data, factual_schema, language)
                v5_slots = ()
                v5_baselines = ()
                if stage_contract == V5_CONTRACT:
                    v5_slots = pinned_coverage_slots(coverage_batches(
                        compound_coverage_issues(limitations)))
                    baselines = []
                    for index, items in enumerate(v5_slots):
                        batch_stage = f"{stage}-coverage-{index}"
                        payload = model_payload(batch_stage, "verifier", coverage_data(items),
                            ReaderVerification, language, True,
                            role_call_index=role_indices["verifier"] + 1 + index)
                        baselines.append({"source_slot_index": index,
                            "issue_ids": [item["issue_id"] for item in items],
                            "payload": payload, "payload_sha256": digest(payload),
                            "input_bytes": model_input_bytes(payload, role="verifier",
                                output_token_envelope=coverage_envelope,
                                valuation_method=request.valuation_method),
                            "prompt_bytes": len(model_prompt(payload))})
                    v5_baselines = tuple(baselines)
                if stage in {"verify_repaired_report", REVISED_REVIEW_STAGE, FROZEN_REVIEW_STAGE} or generic_review_stage(stage):
                    factual_payload = model_payload(stage, "verifier", factual_data, factual_schema, language)
                    factual_cached = (store.load_stage(stage, factual_payload) is not None
                        or getattr(services.models, "has_saved_reply", lambda *_: False)(stage, factual_payload))
                    # A saved repair does not make the factual recheck free.
                    # Before a paid recheck, reserve its full exact candidate
                    # coverage path, without assuming any future issue retirement.
                    if not factual_cached:
                        precheck_calls = [FinalizationCallPlan(stage, "repaired_factual_review", factual_payload,
                            16_000, request.budget.call_timeout_seconds, role="verifier",
                            valuation_method=request.valuation_method)]
                        base_batches = (v5_slots if stage_contract == V5_CONTRACT else
                                        coverage_batches(compound_coverage_issues(limitations)))
                        for index, items in enumerate(base_batches):
                            batch_stage = f"{stage}-coverage-{index}"
                            payload = (v5_baselines[index]["payload"] if stage_contract == V5_CONTRACT
                                       else model_payload(batch_stage, "verifier", coverage_data(items),
                                           ReaderVerification, language, True,
                                           role_call_index=role_indices["verifier"] + 1 + index))
                            cached = (stage_contract != V5_CONTRACT and
                                (store.load_stage(batch_stage, payload) is not None
                                or getattr(services.models, "has_saved_reply", lambda *_: False)(batch_stage, payload))
                                )
                            precheck_calls.append(FinalizationCallPlan(batch_stage, "repaired_coverage", payload,
                                coverage_envelope, request.budget.call_timeout_seconds, cache_hit=cached,
                                role="verifier", valuation_method=request.valuation_method))
                        workload = finalization_workload(precheck_calls)
                        if stage_contract == V5_CONTRACT:
                            workload = reserve_global_coverage_delta(workload,
                                has_slots=bool(v5_slots))
                        remaining = request.budget.total_tokens - tracker.usage.total_tokens
                        precheck = {"reader_sha256": rendered_hash, "remaining_tokens": remaining,
                            "remaining_path": workload, "assumes_no_future_issue_retirement": True,
                            "wall_time": time_plan(workload),
                            "fits_reserve": workload["conservative_reserve_tokens"] < remaining}
                        if stage == FROZEN_REVIEW_STAGE or generic_review_stage(stage):
                            prompt_limit = getattr(services.models, "max_prompt_utf8_bytes", None)
                            precheck["prompt_admission"] = {
                                "limit_bytes": prompt_limit,
                                "calls": [{"stage": item.call_id,
                                           "prompt_bytes": len(model_prompt(item.payload)),
                                           **({"possible_decorated_prompt_bytes":
                                               len(model_prompt(item.payload)) +
                                               GENERIC_V5_COVERAGE_DELTA_MAX_BYTES}
                                              if stage_contract == V5_CONTRACT and
                                              item.call_id != stage else {})}
                                          for item in precheck_calls if not item.cache_hit],
                            }
                            precheck["prompt_admission"]["fits"] = prompt_limit is None or all(
                                item.get("possible_decorated_prompt_bytes",
                                         item["prompt_bytes"]) <= prompt_limit
                                for item in precheck["prompt_admission"]["calls"])
                        finalization_plans["reverification_admission"] = precheck
                        store.save_stage("reverification-admission", {}, precheck)
                        if not precheck.get("prompt_admission", {}).get("fits", True):
                            raise BudgetExhausted("verification_prompt_size_limit")
                        if not precheck["fits_reserve"]:
                            raise BudgetExhausted("reverification_path_budget_insufficient")
                        if precheck["wall_time"]["stop_before_dispatch"]:
                            raise BudgetExhausted("reverification_path_time_insufficient")
                factual_review = call(stage, "verifier", factual_data, factual_schema,
                                      True, language=language)
                raw_factual_review = (factual_review.model_dump(mode="json")
                                      if stage_contract == V5_CONTRACT else None)
                unresolved_source_findings = []
                accepted_source_corrections = []
                source_followup_audit = None
                if generic_review_stage(stage):
                    source_review = factual_data["source_terminal_review"]
                    findings = {item["source_finding_sha256"]: item
                                for item in source_review["findings"]}
                    pending_coverage = (tuple(factual_data.get("pending_coverage", ()))
                                        if stage_contract.deferred_coverage else ())
                    if stage_contract == V5_CONTRACT and any(
                        item.issue_id in {entry["issue_id"] for entry in pending_coverage}
                        and item.status != "open" for item in factual_review.issue_resolutions
                    ):
                        raise ValueError("pending coverage issue cannot be factually retired")
                    pending_hashes = {item["source_finding_sha256"] for item in pending_coverage}
                    if (len(pending_hashes) != len(pending_coverage)
                            or not pending_hashes <= set(findings)
                            or any(item["source_finding"] != {key: value for key, value in findings[
                                item["source_finding_sha256"]].items()
                                if key != "source_finding_sha256"}
                                for item in pending_coverage)):
                        raise ValueError("numbered revision pending coverage ledger differs from source")
                    followups = {item.source_finding_sha256: item
                                 for item in factual_review.source_finding_followups}
                    if (len(followups) != len(factual_review.source_finding_followups)
                            or set(followups) != set(findings) - pending_hashes):
                        raise ValueError("numbered revision omitted a terminal source finding")
                    source_followup_audit = [item.model_dump(mode="json")
                                             for item in factual_review.source_finding_followups]
                    for finding_hash, finding in findings.items():
                        if finding_hash in pending_hashes:
                            continue
                        followup = followups[finding_hash]
                        if followup.disposition == "still_open":
                            unresolved_source_findings.append(ReviewFinding.model_validate({
                                key: value for key, value in finding.items()
                                if key != "source_finding_sha256"}))
                            continue
                        if (rendered_hash == source_review["reader_sha256"]
                                or not followup.reader_excerpts or not followup.witnesses
                                or len(set(followup.reader_excerpts)) != len(followup.reader_excerpts)
                                or len({(item.reference, item.excerpt) for item in followup.witnesses})
                                != len(followup.witnesses)
                                or any(not excerpt.strip() or excerpt not in rendered.reader_text
                                       for excerpt in followup.reader_excerpts)
                                or any(not witness.excerpt.strip()
                                       or not (
                                           witness.reference in resolution_evidence
                                           and witness.excerpt in resolution_evidence[witness.reference]
                                           or source_passage_witness_valid(
                                               witness.reference, witness.excerpt,
                                               factual_data["source_text_witnesses"], snapshot,
                                               finding_hash)
                                       ) for witness in followup.witnesses)):
                            unresolved_source_findings.append(ReviewFinding.model_validate({
                                key: value for key, value in finding.items()
                                if key != "source_finding_sha256"}))
                        else:
                            accepted_source_corrections.append((finding, followup.model_dump(mode="json")))
                    factual_review = LifecycleVerification.model_validate(
                        factual_review.model_dump(mode="json", exclude={"source_finding_followups"}))
                if factual_review.reviewed_report and language == request.report_language:
                    finalization_candidate = {"stage": stage, "reader_sha256": rendered_hash,
                                              "reader_text": rendered.reader_text}
                    candidate_review_stage = stage
                    save_finalization_checkpoint()
                main_review, limitations, lifecycle = reconcile_review(
                    factual_review, limitations, resolution_evidence, rendered.reader_text,
                    case_context.scope if case_context else None,
                )
                if source_followup_audit is not None:
                    lifecycle["source_terminal_review"] = source_review
                    lifecycle["source_terminal_review_sha256"] = factual_data[
                        "source_terminal_review_sha256"]
                    lifecycle["source_finding_followups"] = source_followup_audit
                    if pending_coverage or stage_contract == V5_CONTRACT:
                        lifecycle["pending_coverage"] = list(pending_coverage)
                        lifecycle["pending_coverage_sha256"] = digest(pending_coverage)
                    if stage_contract == V5_CONTRACT:
                        lifecycle["pending_coverage_contexts"] = list(pending_contexts)
                        lifecycle["pending_coverage_contexts_sha256"] = digest(pending_contexts)
                if unresolved_source_findings:
                    main_review = main_review.model_copy(update={"findings": (
                        *main_review.findings, *unresolved_source_findings)})
                if rendered.limitations_audit.get("reader_compaction", {}).get("active"):
                    cited_sections = {item["section_index"] for item in rendered.limitations_audit.get(
                        "paragraph_citations", []) if item["source_ids"] or item.get("calculation_ids")}
                    section_only = {item["section_index"] for item in rendered.limitations_audit.get(
                        "section_citations", []) if item["source_ids"]} - cited_sections
                    if section_only:
                        main_review = main_review.model_copy(update={"findings": (
                            *main_review.findings, ReviewFinding(
                                code="paragraph_citations_missing", severity="warning", category="editorial",
                                message="Sourced sections need explicit paragraph or table citations; "
                                        "a section-only source inventory is not paragraph support.",
                                affected_ids=tuple(f"section:{index}" for index in sorted(section_only))),
                        )})
                reader_verifications[language]["issue_lifecycle"] = lifecycle
                still_rendered = [item["issue_id"] for item in lifecycle["issues"]
                                  if item["status"] in {"resolved", "superseded"}
                                  and item["text"] in rendered.reader_text]
                if still_rendered:
                    main_review = main_review.model_copy(update={"findings": (
                        *main_review.findings, ReviewFinding(
                            code="retired_issue_still_rendered", severity="critical", category="editorial",
                            message="Retired historical warnings remain verbatim in the reader; remove "
                                    "them from the next candidate, preserving their audit records.",
                            affected_ids=tuple(still_rendered)),
                    )})
                # Only a subsequent candidate may omit retired warnings. This
                # candidate's bytes and attestation are never rewritten in place.
                retired_reader_texts.update(item["text"] for item in lifecycle["issues"]
                                           if item["status"] in {"resolved", "superseded"})
                if stage_contract == V5_CONTRACT:
                    if (MAX_CORRECTION_CONTEXT_BYTES != GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES
                            or CORRECTION_CONTEXT_POLICY !=
                            "current_single_issue_accepted_factual_context_v1"):
                        raise ValueError("v5 correction context differs from the bound contract")
                    limitations = attach_current_factual_corrections(
                        limitations,
                        accepted_finding_hashes={finding["source_finding_sha256"]
                                                 for finding, _ in accepted_source_corrections},
                        factual_review=raw_factual_review,
                        source_terminal_review=source_review,
                        source_terminal_review_sha256=factual_data[
                            "source_terminal_review_sha256"],
                        reader_text=rendered.reader_text,
                        factual_stage=stage,
                        factual_payload=factual_payloads[stage],
                        checkpoint=model_checkpoints[stage],
                        resolution_evidence=resolution_evidence,
                        source_text_witnesses=factual_data["source_text_witnesses"],
                        snapshot=snapshot,
                    )
                    correction_contexts = [record for issue in limitations
                                           for record in issue.get(CORRECTION_CONTEXT_FIELD, ())]
                    lifecycle["current_factual_correction_contexts"] = correction_contexts
                    lifecycle["current_factual_correction_contexts_sha256"] = digest(
                        correction_contexts)
                parent_limitations = limitations
                limitations = compound_coverage_issues(parent_limitations)
                v5_projected = (project_pinned_coverage(v5_slots, limitations)
                                if stage_contract == V5_CONTRACT else ())
                batches = (tuple(items for _, items in v5_projected)
                           if stage_contract == V5_CONTRACT else coverage_batches(limitations))
                batch_results = []

                planned_calls = []
                v5_delta_rows = []
                reader_bytes = len(rendered.reader_text.encode("utf-8"))
                for index, items in enumerate(batches):
                    batch_stage = f"{stage}-coverage-{index}"
                    if stage_contract == V5_CONTRACT:
                        decorated_packet_bytes(items)
                    payload = model_payload(batch_stage, "verifier", coverage_data(items), ReaderVerification,
                                            language, True, role_call_index=role_indices["verifier"] + index)
                    if stage_contract == V5_CONTRACT:
                        source_index = v5_projected[index][0]
                        baseline = v5_baselines[source_index]
                        v5_delta_rows.append({
                            "source_slot_index": source_index, "batch_index": index,
                            "stage": batch_stage,
                            "issue_ids": [item["issue_id"] for item in items],
                            "base_payload_sha256": baseline["payload_sha256"],
                            "actual_payload_sha256": digest(payload),
                            "base_input_bytes": baseline["input_bytes"],
                            "actual_input_bytes": model_input_bytes(payload, role="verifier",
                                output_token_envelope=coverage_envelope,
                                valuation_method=request.valuation_method),
                            "base_prompt_bytes": baseline["prompt_bytes"],
                            "actual_prompt_bytes": len(model_prompt(payload)),
                        })
                    key = digest({"wire": WIRE_SCHEMA_VERSION, "payload": {
                        name: value for name, value in payload.items() if name not in {"stage", "role_call_index"}
                    }})
                    cached = ((stage_contract != V5_CONTRACT and key in coverage_reuse)
                              or store.load_stage(batch_stage, payload) is not None
                              or getattr(services.models, "has_saved_reply", lambda *_: False)(batch_stage, payload))
                    planned_calls.append(FinalizationCallPlan(
                        batch_stage, "current_coverage", payload, coverage_envelope,
                        request.budget.call_timeout_seconds, cache_hit=cached, reader_bytes=reader_bytes,
                        role="verifier", valuation_method=request.valuation_method))
                if stage_contract == V5_CONTRACT:
                    schedule = coverage_delta_admission(v5_delta_rows)
                    lifecycle["pinned_coverage_schedule"] = schedule
                    lifecycle["pinned_coverage_schedule_sha256"] = digest(schedule)
                    prompt_limit = getattr(services.models, "max_prompt_utf8_bytes", None)
                    if prompt_limit is not None and any(
                        row["actual_prompt_bytes"] > prompt_limit
                        for row, call_plan in zip(v5_delta_rows, planned_calls, strict=True)
                        if not call_plan.cache_hit
                    ):
                        raise BudgetExhausted("verification_prompt_size_limit")
                current_workload = finalization_workload(planned_calls)
                # Future output sizes are explicit assumptions, not fabricated
                # exact prompts or a promise of a provider-enforced spend cap.
                if stage == "verify_report":
                    repair_data = {**editor_data, "draft_to_repair": source_draft.model_dump(mode="json"),
                                   "issue_lifecycle": lifecycle, "repair_findings": main_review.model_dump(mode="json")}
                    repair_payload = model_payload("repair_report", "editor", repair_data, draft_schema, language)
                    finding_growth = len(batches) * coverage_envelope * 4
                    planned_calls.append(FinalizationCallPlan(
                        "repair_report", "possible_repair", b" " * (model_input_bytes(
                            repair_payload, role="editor", output_token_envelope=16_000,
                            valuation_method=request.valuation_method) + finding_growth),
                        16_000, request.budget.call_timeout_seconds))
                    factual_payload = model_payload("verify_repaired_report", "verifier", factual_data,
                                                    LifecycleVerification, language)
                    planned_calls.append(FinalizationCallPlan(
                        "verify_repaired_report", "possible_repair_review",
                        b" " * (model_input_bytes(factual_payload, role="verifier", output_token_envelope=16_000,
                                                 valuation_method=request.valuation_method) + reader_bytes),
                        16_000, request.budget.call_timeout_seconds, reader_bytes=2 * reader_bytes))
                    for index, items in enumerate(batches):
                        payload = model_payload(f"verify_repaired_report-coverage-{index}", "verifier",
                                                coverage_data(items), ReaderVerification, language, True)
                        planned_calls.append(FinalizationCallPlan(
                            f"verify_repaired_report-coverage-{index}", "possible_repair_coverage",
                            b" " * (model_input_bytes(payload, role="verifier", output_token_envelope=coverage_envelope,
                                                     valuation_method=request.valuation_method) + reader_bytes), coverage_envelope,
                            request.budget.call_timeout_seconds, reader_bytes=2 * reader_bytes))
                remaining_tokens = request.budget.total_tokens - tracker.usage.total_tokens
                plan = {
                    "coverage_batch_policy": request.coverage_batch_policy,
                    "reader_sha256": rendered_hash, "original_issue_count": len(parent_limitations),
                    "atomic_issue_count": len(limitations),
                    "grouped_issue_count": sum(len(group_equivalent_issues(items)) for items in batches),
                    "factual_review_completed": True,
                    "remaining_workload": finalization_workload(planned_calls),
                    "current_pass": current_workload, "remaining_tokens": remaining_tokens,
                    "current_pass_fits_reserve": current_workload["conservative_reserve_tokens"] < remaining_tokens,
                    "assumptions": {"repair_reader_growth_factor": 2,
                                    "coverage_reply_bytes_per_output_token": 4,
                                    "repair_sizes_are_estimates": True,
                                    "per_call_admission_remains_mandatory": True},
                }
                finalization_plans[stage] = plan
                plan["current_pass_wall_time"] = time_plan(current_workload)
                plan["remaining_path_wall_time"] = time_plan(plan["remaining_workload"])
                plan["coverage_time_checks"] = []
                store.save_stage(f"{stage}-cost-plan", {"reader_sha256": rendered_hash}, plan)
                if not plan["current_pass_fits_reserve"]:
                    raise BudgetExhausted("finalization_pass_budget_insufficient")
                for index, items in enumerate(batches):
                    batch_stage = f"{stage}-coverage-{index}"
                    # Reproject only work still needed against the same reader.
                    # Possible repair is disclosed in the plan, but is not a
                    # mandatory cost until the completed review requests it.
                    active_stage = batch_stage
                    wall = time_plan({"calls": current_workload["calls"][index:]})
                    plan["coverage_time_checks"].append({"before_stage": batch_stage, **wall})
                    store.save_stage(f"{stage}-cost-plan", {"reader_sha256": rendered_hash}, plan)
                    if wall["stop_before_dispatch"]:
                        raise BudgetExhausted("finalization_pass_time_insufficient")
                    batch_review = call(batch_stage, "verifier", coverage_data(items), ReaderVerification,
                                        True, language=language, coverage_only=True)
                    groups = group_equivalent_issues(items)
                    if any(len(group.issue_ids) > 1 for group in groups):
                        batch_review = fanout_group_dispositions(batch_review, groups)
                    batch_results.append(CoverageBatchResult(rendered_hash, items, batch_review))
                    reader_verifications[language]["coverage_batches"].append({
                        "stage": batch_stage, "reader_sha256": rendered_hash,
                        "issue_ids": [item["issue_id"] for item in items],
                        "equivalent_groups": {group.group_id: list(group.issue_ids) for group in groups},
                        "review": batch_review.model_dump(mode="json"),
                    })
                verification = combine_coverage(main_review, batch_results, limitations,
                                                rendered.reader_text, rendered_hash)
                verification = fan_in_compound_dispositions(
                    verification, parent_limitations, rendered.reader_text)
                limitations = parent_limitations
                if generic_review_stage(stage) and stage_contract.deferred_coverage:
                    receipts, pending_failures = resolve_deferred_coverage(
                        pending_coverage,
                        source_stage=source_review["stage"],
                        generation=int(stage.rsplit("-", 1)[1]),
                        contract_sha256=stage_contract.sha256,
                        reader_sha256=rendered_hash, reader_text=rendered.reader_text,
                        issues=limitations, batches=batches,
                        batch_audit=reader_verifications[language]["coverage_batches"],
                        model_checkpoints=model_checkpoints, policy=stage_contract.policy)
                    lifecycle["deferred_coverage_receipts"] = list(receipts)
                    lifecycle["deferred_coverage_receipts_sha256"] = digest(receipts)
                    lifecycle["pending_coverage_unresolved"] = [
                        item["source_finding_sha256"] for item in pending_coverage
                        if item["source_finding_sha256"] not in {
                            receipt["source_finding_sha256"] for receipt in receipts}]
                    if pending_failures:
                        verification = verification.model_copy(update={"findings": (
                            *verification.findings, *pending_failures)})
            else:
                verification = call(stage, "verifier", review_data, ReaderVerification,
                                    True, language=language)
                verification = block_reader_contradictions(
                    check_dispositions(verification, limitations, rendered.reader_text))
            reader_verifications[language] = {
                **reader_verifications.get(language, {}),
                "reader_sha256": rendered_hash, "stage": stage,
                "review": verification.model_dump(mode="json"),
                "exported": False,
            }
            if request.financial_case_path:
                reader_verifications[language].update({
                    "required_limitation_ids": [item["issue_id"] for item in limitations],
                    "validated_limitation_ids": list(validated_disposition_ids(
                        verification, limitations, rendered.reader_text)),
                })
            return verification, rendered

        try:
            cached_evidence = store.load_stage("evidence", {})
            if cached_evidence is not None:
                snapshot = EvidenceSnapshot.model_validate(cached_evidence)
            elif "evidence_path" in frozen:
                snapshot = EvidenceSnapshot.model_validate(parse_json(frozen["evidence_path"]))
            else:
                snapshot = services.evidence.collect(request)
            snapshot = validate_snapshot(snapshot, request)
            store.save_stage("evidence", {}, snapshot.model_dump(mode="json"))
            if "financial_case_path" in frozen:
                case_context = load_case_context(frozen["financial_case_path"], request, snapshot)
                gaps.extend(case_context.limitations)
            gaps.extend(snapshot.gaps)
            if recovery is not None and candidate_recovery is None:
                gaps.append(
                    "Explicit recovery reused six validated historical stages; the source run "
                    "contains an unmeasured dispatched call, so cumulative usage remains incomplete."
                )
            if getattr(services.models, "kind", None) == "codex" and not any(
                    source.id in _known_ids(snapshot) and source.content.strip() for source in snapshot.sources):
                gaps.append("No eligible source text; live model calls withheld.")
                raise ValueError("no eligible live evidence")
            prior_data = {"prior_hypotheses": prior.model_dump(mode="json"),
                          "update": describe_update(prior, snapshot)} if prior else {}
            # Case runs blind the first challenge before even planner questions
            # or prior-dossier hypotheses can influence its payload/retrieval.
            challenger = (call("independent_challenge", "challenger", {}, AnalysisOutput)
                          if case_context is not None else None)
            plan = call("planner", "planner", prior_data, AnalysisOutput)
            if not 3 <= len(plan.questions) <= 5:
                raise ValueError("planner must supply 3-5 decisive questions")
            outputs["planner"] = plan.model_dump(mode="json")
            question_data = {"questions": [q.model_dump(mode="json") for q in plan.questions]}
            # The independent challenge never sees the lead thesis or model first.
            if challenger is None:
                challenger = call("independent_challenge", "challenger", question_data, AnalysisOutput)
            outputs["challenger"] = challenger.model_dump(mode="json")
            for role in ("business", "accounting", "expectations", "management"):
                analysis = call(role, role, question_data, AnalysisOutput)
                outputs[role] = analysis.model_dump(mode="json")
                gaps.extend(analysis.unresolved_gaps)
            if case_context is not None:
                proposal, valuation = case_valuation()
            else:
                proposal = call("valuation", "valuation", outputs, ValuationProposal)
                valuation = _calculate(proposal, request, snapshot)
            unresolved = [item for output in outputs.values()
                          for item in output.get("followup_questions", [])]
            if evidence_led:
                investigation_ledger = record_investigations("initial")
                unresolved = [item.question for item in investigation_ledger.followup_questions]
            followup = getattr(services.evidence, "followup", None)
            delivered_packets = set()
            for cycle in range(request.budget.followup_cycles):
                if not unresolved or (followup is None and not evidence_led):
                    break
                if bounded_review:
                    inventory = limitation_packet(required_limitations())
                    prospective_values = calculation_catalog(proposal, valuation)
                    if case_context is not None and case_context.operating_scenarios is not None:
                        prospective_values = (*prospective_values, *case_context.operating_scenarios.calculated_values)
                    if case_context is not None and case_context.cashflow_bridge is not None:
                        prospective_values = (*prospective_values, *case_context.cashflow_bridge.calculated_values)
                    prospective_data = {
                        "analyses": outputs, "limitations": required_limitations(), "valuation": valuation,
                        "valuation_inputs": proposal.model_dump(mode="json"),
                        "calculated_values": [item.model_dump(mode="json") for item in prospective_values],
                        "claim_verification": VerificationOutput().model_dump(mode="json"),
                    }
                    prospective_evidence = evidence_catalog(
                        snapshot, prospective_values, eligible_ids=_known_ids(snapshot), case_context=case_context)
                    # Build through the dispatch constructor so new policies, schemas,
                    # case delivery and compound guidance cannot disappear from reserves.
                    # Reader/provenance and future output growth remain planning allowances;
                    # actual later calls still undergo exact-boundary admission.
                    prospective_payloads = {
                        "editor": ("editor", model_payload("editor", "editor", prospective_data, draft_schema)),
                        "factual": ("verifier", model_payload("verify_report", "verifier", {
                            **prospective_data, "rendered_reader": "", "inherited_issues": inventory,
                            "resolution_evidence": prospective_evidence, "issue_resolution_policy": LIFECYCLE_POLICY,
                            "resolution_witness_contract": resolution_witness_contract(prospective_evidence, ""),
                            "conclusion_scope": case_context.scope.model_dump(mode="json") if case_context else None,
                        }, LifecycleVerification)),
                    }
                    prospective_sizes = {name: model_input_bytes(payload, role=role, output_token_envelope=16_000,
                                                                 valuation_method=request.valuation_method)
                                         for name, (role, payload) in prospective_payloads.items()}
                    # Generous, explicit heuristics, not a mathematical token/byte
                    # maximum or a guarantee against an advisory provider overrun.
                    future_output_calls, advisory_output_tokens, bytes_per_token = 8, 16_000, 16
                    reader_bytes_allowance = advisory_output_tokens * bytes_per_token
                    growth_allowance = future_output_calls * reader_bytes_allowance
                    context_bytes = max(prospective_sizes.values()) + growth_allowance
                    allowance = finalization_allowance(
                        inventory, context_bytes=context_bytes,
                        reader_bytes=reader_bytes_allowance,
                        coverage_batch_policy=request.coverage_batch_policy,
                        call_timeout_seconds=request.budget.call_timeout_seconds,
                        language_count=1 + len(request.additional_report_languages),
                    )
                    next_cycle_call_seconds = 6 * request.budget.call_timeout_seconds
                    next_cycle_token_envelope = 6 * (context_bytes + 16_000)
                    remaining_seconds = request.budget.wall_seconds - tracker.elapsed_seconds
                    remaining_tokens = request.budget.total_tokens - tracker.usage.total_tokens
                    skip = (
                        remaining_seconds - next_cycle_call_seconds
                        < max(request.budget.reserve_seconds, allowance["planned_call_seconds"])
                        or remaining_tokens - next_cycle_token_envelope
                        < max(request.budget.reserve_tokens, allowance["estimated_tokens"])
                    )
                    store.save_stage(f"finalization-plan-{cycle}", {
                        "issue_ids": [item["issue_id"] for item in inventory],
                    }, {**allowance, "optional_cycle_skipped": skip,
                        "prospective_context_bytes": prospective_sizes,
                        "future_output_growth_bytes_planning_allowance": growth_allowance,
                        "future_output_growth_assumptions": {
                            "calls": future_output_calls, "advisory_output_tokens_per_call": advisory_output_tokens,
                            "bytes_per_token_heuristic": bytes_per_token,
                        },
                        "provider_output_cap_is_hard": False,
                        "per_call_admission_remains_mandatory": True,
                        "context_bytes_planning_assumption": context_bytes,
                        "optional_cycle_token_envelope": next_cycle_token_envelope,
                        "optional_cycle_call_seconds": next_cycle_call_seconds,
                        "reason": "Preserve mandatory drafting, factual review, per-issue coverage and one repair capacity."})
                    if skip:
                        investigation_cycles.append({
                            "cycle": cycle, "mode": "not_dispatched_finalization_headroom",
                            "external_acquisition_performed": False,
                            "all_unreviewed_tasks_remain_open": True,
                        })
                        break
                tracker.admit()
                local_only = followup is None
                followup_input = {"evidence_hash": digest(snapshot),
                                  "questions": list(dict.fromkeys(unresolved))}
                if local_only:
                    packet = _prompt_evidence(snapshot, tuple(unresolved))
                    packet_hash = digest(packet.get("sources", []))
                    matched = any(query.get("match_label") == "keyword_match"
                                  for query in packet.get("retrieval_metadata", {}).get("queries", []))
                    if not matched or packet_hash in delivered_packets:
                        break
                    delivered_packets.add(packet_hash)
                    followup_input["local_context_sha256"] = packet_hash
                    store.save_stage(f"retrieval-{cycle}", followup_input, packet)
                saved_followup = store.load_stage(f"followup-{cycle}", followup_input)
                if saved_followup is not None:
                    updated = EvidenceSnapshot.model_validate(saved_followup)
                else:
                    updated = snapshot if local_only else followup(
                        request, snapshot, tuple(dict.fromkeys(unresolved)))
                updated = validate_snapshot(updated, request)
                if updated.instrument != snapshot.instrument:
                    raise ValueError("follow-up changed instrument identity")
                for kind in ("sources", "facts", "events", "expectations"):
                    retained = {item.id: item for item in getattr(updated, kind)}
                    if any(retained.get(item.id) != item for item in getattr(snapshot, kind)):
                        raise ValueError("follow-up must preserve immutable prior evidence")
                store.save_stage(f"followup-{cycle}", followup_input,
                                 updated.model_dump(mode="json"))
                if digest(updated) == digest(snapshot) and not local_only:
                    break
                snapshot = EvidenceSnapshot.model_validate_json(updated.model_dump_json())
                if case_context is not None:
                    # Changed evidence requires a new case/review and a new run;
                    # do not continue on an obsolete financial reconciliation.
                    case_context = load_case_context(frozen["financial_case_path"], request, snapshot)
                if evidence_led:
                    investigation_queries = tuple(dict.fromkeys(unresolved))
                    investigation_cycles.append({
                        "cycle": cycle, "mode": "local_full_text_retrieval" if local_only else "provider_followup",
                        "questions": investigation_queries, "evidence_hash": digest(snapshot),
                        "external_acquisition_performed": not local_only,
                    })
                revision = call(f"revision-{cycle}", "planner", outputs, AnalysisOutput)
                outputs[f"revision-{cycle}"] = revision.model_dump(mode="json")
                if revision.questions:
                    if not 3 <= len(revision.questions) <= 5:
                        raise ValueError("revised plan must supply 3-5 decisive questions")
                    question_data = {"questions": [q.model_dump(mode="json") for q in revision.questions]}
                for role in ("business", "accounting", "expectations", "management"):
                    analysis = call(f"{role}-{cycle}", role, question_data, AnalysisOutput)
                    outputs[role] = analysis.model_dump(mode="json")
                    gaps.extend(analysis.unresolved_gaps)
                if case_context is not None:
                    proposal, valuation = case_valuation()
                else:
                    proposal = call(f"valuation-{cycle}", "valuation", outputs, ValuationProposal)
                    valuation = _calculate(proposal, request, snapshot)
                unresolved = list(dict.fromkeys([
                    *revision.followup_questions,
                    *(question for role in ("business", "accounting", "expectations", "management")
                      for question in outputs[role].get("followup_questions", []))]))
                if evidence_led:
                    # Disappearance from a revised answer is not a closure judgment.
                    investigation_ledger = record_investigations(f"followup-{cycle}")
                    unresolved = [item.question for item in investigation_ledger.followup_questions]
            if bounded_review and investigation_cycles:
                # No producer currently supplies explicit, evidence-linked closure
                # candidates. Do not ask a model to exhaustively adjudicate every
                # raw gap or turn changed wording into proof of resolution.
                investigation_cycles[-1]["closure_review"] = {
                    "status": "not_dispatched_no_explicit_resolution_candidates",
                    "all_unreviewed_tasks_remain_open": True,
                    "mandatory_reader_coverage_is_separate": True,
                }
            if evidence_led and investigation_cycles and not bounded_review:
                closure_review = call("verify_investigations", "verifier", {
                    "investigations": investigation_ledger.model_dump(mode="json"),
                    "analyses": outputs, "valuation": valuation,
                    "closure_policy": "Explicitly judge resolution versus still_open or disposed. "
                                      "A keyword hit or revised omission cannot close a task. "
                                      "Resolved tasks need eligible evidence IDs and a reasoned "
                                      "disposition. Forecast judgments are never reported facts.",
                }, InvestigationReview, True)
                decisions = tuple(decision.model_copy(update={
                    "verifier_provenance_id": ("stages/verify_investigations.json"
                                               if decision.verifier_judgment else None),
                }) for decision in closure_review.decisions)
                investigation_ledger = make_ledger(investigation_tasks.values(), snapshot, decisions)
            gaps.extend(unresolved)
            gaps.extend(valuation.get("limitations", []))
            gaps.extend(_prompt_evidence(snapshot).get("context_gaps", []))
            challenge = call("reconcile_challenge", "challenger",
                             {"analyses": outputs, "valuation": valuation,
                              **({"valuation_inputs": proposal.model_dump(mode="json")}
                                 if evidence_led else {})}, AnalysisOutput)
            outputs["reconciled_challenge"] = challenge.model_dump(mode="json")
            gaps.extend(challenge.unresolved_gaps)
            if bounded_review:
                investigation_ledger = record_investigations("final_challenge")
                existing_gap_texts = set(gaps)
                gaps.extend(text for text in required_limitations() if text not in existing_gap_texts)
            review = call("verify_claims", "verifier", outputs, VerificationOutput, True)
            reviews.extend(review.findings)
            pre_editor_findings.extend(review.findings)
            active_admission_findings.extend(review.findings)
            claim_ids = {claim["id"] for output in outputs.values()
                         for claim in output.get("claims", [])}
            if (set(review.supported_claim_ids) | set(review.contradicted_claim_ids)) - claim_ids:
                raise ValueError("verifier invented claim identifiers")
            if set(review.supported_claim_ids) & set(review.contradicted_claim_ids):
                raise ValueError("verifier supplied conflicting claim decisions")
            missing = claim_ids - set(review.supported_claim_ids)
            gaps.extend(f"Unverified claim: {identifier}" for identifier in sorted(missing))
            if evidence_led:
                gaps.extend(item.message for item in review.findings
                            if item.severity in {"warning", "critical"})
            editor_data = {"analyses": outputs, "limitations": gaps, "valuation": valuation}
            if evidence_led:
                calculated_values = calculation_catalog(proposal, valuation)
                if case_context is not None and case_context.operating_scenarios is not None:
                    calculated_values = (*calculated_values, *case_context.operating_scenarios.calculated_values)
                if case_context is not None and case_context.cashflow_bridge is not None:
                    calculated_values = (*calculated_values, *case_context.cashflow_bridge.calculated_values)
                editor_data["claim_verification"] = review.model_dump(mode="json")
                editor_data["valuation_inputs"] = proposal.model_dump(mode="json")
                editor_data["calculated_values"] = [value.model_dump(mode="json") for value in calculated_values]
                editor_data["investigations"] = ({
                    "open_task_count": sum(entry.status == "still_open" for entry in investigation_ledger.entries),
                    "full_provenance_audit": "investigation.json",
                    "coverage_policy": "Every unresolved task text is supplied in limitations; "
                        "none is implicitly resolved, immaterial or audit-only.",
                } if bounded_review else investigation_ledger.model_dump(mode="json"))
            source_draft = call("editor", "editor", editor_data, draft_schema, True,
                                language=request.report_language)
            draft = prepare_draft(source_draft, request.report_language)
            if evidence_led:
                final_review, rendered = verify_reader(draft, request.report_language, "verify_report",
                                                      authored=source_draft)
                if (not final_review.reviewed_report or any(
                        item.severity in {"warning", "critical"} for item in final_review.findings)):
                    reviews.extend(final_review.findings)
                    # Reader defects are repair instructions and audit history,
                    # not new eternal financial limitations to echo in the reader.
                    # One repair only, admitted from the existing finalization reserve.
                    repair_data = {
                        **editor_data,
                        "limitations": [text for text in gaps if text not in retired_reader_texts],
                        "issue_lifecycle": reader_verifications[request.report_language].get("issue_lifecycle"),
                        "draft_to_repair": source_draft.model_dump(mode="json"),
                        "repair_findings": final_review.model_dump(mode="json"),
                        "repair_policy": "Resolve or explicitly qualify findings using existing "
                                         "evidence only. Do not remove material limitations. "
                                         "Remove historical warnings explicitly retired with "
                                         "evidence in issue_lifecycle; retain their audit history.",
                    }
                    if bounded_review:
                        # Do not buy a repair when the known remaining review path
                        # already cannot fit. Future candidate growth stays an
                        # explicit estimate; exact post-repair admission still runs.
                        active_stage = "repair_report"
                        repair_payload = model_payload("repair_report", "editor", repair_data,
                                                       draft_schema, request.report_language)
                        repair_cached = (
                            store.load_stage("repair_report", repair_payload) is not None
                            or getattr(services.models, "has_saved_reply", lambda *_: False)(
                                "repair_report", repair_payload)
                        )
                        estimated_calls = finalization_plans["verify_report"]["remaining_workload"]["calls"]
                        repair_path = finalization_workload([
                            FinalizationCallPlan(
                                item["call_id"], item["phase"],
                                repair_payload if item["phase"] == "possible_repair" else
                                b" " * item["serialized_input_bytes"],
                                item["output_token_envelope"], item["timeout_seconds"],
                                cache_hit=(repair_cached if item["phase"] == "possible_repair"
                                           else item["cache_hit"]), reader_bytes=item["reader_bytes"],
                                role="editor" if item["phase"] == "possible_repair" else "verifier",
                                valuation_method=request.valuation_method,
                            ) for item in estimated_calls if item["phase"] != "current_coverage"
                        ])
                        remaining = request.budget.total_tokens - tracker.usage.total_tokens
                        repair_plan = {"remaining_tokens": remaining, "repair_path": repair_path,
                                       "fits_reserve": repair_path["conservative_reserve_tokens"] < remaining,
                                       "wall_time": time_plan(repair_path),
                                       "repair_cached": repair_cached,
                                       "future_candidate_sizes_are_estimates": True}
                        finalization_plans["repair_admission"] = repair_plan
                        store.save_stage("repair-admission", {}, repair_plan)
                        if not repair_cached and not repair_plan["fits_reserve"]:
                            raise BudgetExhausted("repair_path_budget_insufficient")
                        if not repair_cached and repair_plan["wall_time"]["stop_before_dispatch"]:
                            raise BudgetExhausted("repair_path_time_insufficient")
                    source_draft = call("repair_report", "editor", repair_data,
                                        draft_schema, True, language=request.report_language)
                    draft = prepare_draft(source_draft, request.report_language)
                    final_review, rendered = verify_reader(
                        draft, request.report_language, "verify_repaired_report", authored=source_draft)
                if candidate_recovery and (candidate_recovery.get("reader_revision_policy")
                                           or candidate_recovery.get("verification_repair_policy")):
                    replay_revision = bool(candidate_recovery.get("verification_repair_policy")
                        or candidate_recovery.get("revision_generation"))
                    if ((not replay_revision and candidate_recovery["reader_revision_policy"] != READER_REVISION_POLICY)
                            or reader_verifications[request.report_language]["stage"] != "verify_repaired_report"
                            or (not replay_revision and reader_verifications[request.report_language]["reader_sha256"]
                                != candidate_recovery["candidate"]["reader_sha256"])):
                        raise ValueError("revision source reconstruction differs from the authorized candidate")
                    # Keep all historical findings, but no old retirement or coverage
                    # decision can attest a new candidate, even if its text is identical.
                    reviews.extend(final_review.findings)
                    retired_reader_texts.clear()
                    coverage_reuse.clear()
                    revision_data = {
                        **editor_data, "limitations": list(gaps),
                        "draft_to_repair": source_draft.model_dump(mode="json"),
                        "repair_findings": final_review.model_dump(mode="json"),
                        "issue_lifecycle": reader_verifications[request.report_language].get("issue_lifecycle"),
                        "repair_policy": REVISION_REQUIREMENTS,
                        "reader_revision_policy": READER_REVISION_POLICY,
                    }
                    active_stage = REVISE_STAGE
                    revision_payload = model_payload(REVISE_STAGE, "editor", revision_data,
                                                     draft_schema, request.report_language)
                    revision_cached = (store.load_stage(REVISE_STAGE, revision_payload) is not None
                        or getattr(services.models, "has_saved_reply", lambda *_: False)(REVISE_STAGE, revision_payload))
                    reader_bytes = len(rendered.reader_text.encode("utf-8"))
                    # Budget a writer plus full factual/coverage pass, with explicit
                    # growth assumptions. Exact post-writer admission remains mandatory.
                    estimated_calls = [FinalizationCallPlan(
                        REVISE_STAGE, "revision", revision_payload, 16_000,
                        request.budget.call_timeout_seconds, cache_hit=revision_cached, role="editor",
                        valuation_method=request.valuation_method), FinalizationCallPlan(
                        REVISED_REVIEW_STAGE, "revision_factual",
                        b" " * (model_input_bytes(factual_payloads["verify_repaired_report"],
                                                  role="verifier", output_token_envelope=16_000,
                                                  valuation_method=request.valuation_method)
                                 + reader_bytes + len(REVISION_REQUIREMENTS.encode())),
                        16_000, request.budget.call_timeout_seconds, role="verifier",
                        valuation_method=request.valuation_method)]
                    # The old current_pass excludes retired issues. Revision
                    # reopens them, so estimate from the complete pre-resolution
                    # issue packet under the new applicability policy instead.
                    previous_factual = factual_payloads["verify_repaired_report"]["research"]
                    reopened = split_compound_obligations(
                        previous_factual["inherited_issues"], previous_factual["resolution_evidence"],
                        reader_revision=True)
                    atomic_reopened = compound_coverage_issues(reopened)
                    for index, items in enumerate(coverage_batches(atomic_reopened)):
                        stage = f"{REVISED_REVIEW_STAGE}-coverage-{index}"
                        payload = model_payload(stage, "verifier", coverage_review_data(
                            items, rendered.reader_text), ReaderVerification, request.report_language,
                            True, role_call_index=role_indices["verifier"] + 1 + index)
                        estimated_calls.append(FinalizationCallPlan(
                            stage, "revision_coverage",
                            b" " * (model_input_bytes(payload, role="verifier",
                                                      output_token_envelope=coverage_envelope,
                                                      valuation_method=request.valuation_method)
                                     + reader_bytes + 4096),
                            coverage_envelope, request.budget.call_timeout_seconds,
                            role="verifier", valuation_method=request.valuation_method))
                    workload = finalization_workload(estimated_calls)
                    remaining = request.budget.total_tokens - tracker.usage.total_tokens
                    revision_plan = {"policy": READER_REVISION_POLICY, "remaining_tokens": remaining,
                                     "remaining_path": workload, "wall_time": time_plan(workload),
                                     "fits_reserve": workload["conservative_reserve_tokens"] < remaining,
                                     "estimate_basis": "pre_reconciliation_obligations",
                                     "reopened_issue_count": len(reopened),
                                     "atomic_reopened_issue_count": len(atomic_reopened),
                                     "future_candidate_sizes_are_estimates": True}
                    finalization_plans["revision_admission"] = revision_plan
                    store.save_stage("revision-admission", {}, revision_plan)
                    if not revision_cached and not revision_plan["fits_reserve"]:
                        raise BudgetExhausted("revision_path_budget_insufficient")
                    if not revision_cached and revision_plan["wall_time"]["stop_before_dispatch"]:
                        raise BudgetExhausted("revision_path_time_insufficient")
                    source_draft = call(REVISE_STAGE, "editor", revision_data, draft_schema,
                                        True, language=request.report_language)
                    draft = prepare_draft(source_draft, request.report_language)
                    final_review, rendered = verify_reader(
                        draft, request.report_language, REVISED_REVIEW_STAGE, authored=source_draft)
                    if replay_revision:
                        if (reader_verifications[request.report_language]["reader_sha256"]
                                != candidate_recovery["candidate"]["reader_sha256"]
                                and not candidate_recovery.get("revision_generation")):
                            raise ValueError("verification source reconstruction differs from authorized candidate")
                        reviews.extend(final_review.findings)
                        retired_reader_texts.clear()
                        coverage_reuse.clear()
                        final_review, rendered = verify_reader(
                            draft, request.report_language, FROZEN_REVIEW_STAGE, authored=source_draft)
                    if candidate_recovery.get("revision_generation"):
                        target_generation = candidate_recovery["revision_generation"]
                        for generation in range(2, target_generation + 1):
                            writer_stage, review_stage = generation_stages(generation)
                            contract = numbered_contract(writer_stage)
                            source_stage = reader_verifications[request.report_language]["stage"]
                            source_hash = reader_verifications[request.report_language]["reader_sha256"]
                            source_writer = (REVISE_STAGE if generation == 2 else
                                             generation_stages(generation - 1)[0])
                            if generation == target_generation and (
                                source_stage != candidate_recovery["candidate_review_stage"]
                                or source_hash != candidate_recovery["candidate"]["reader_sha256"]
                                or source_writer != candidate_recovery["source_writer_stage"]
                                or digest(final_review.model_dump(mode="json"))
                                != candidate_recovery["source_terminal_review_sha256"]
                            ):
                                raise ValueError("generic revision source candidate or writer differs")
                            if generation == target_generation and contract.deferred_coverage:
                                if digest(reader_verifications[request.report_language][
                                    "issue_lifecycle"]) != candidate_recovery["source_issue_lifecycle_sha256"]:
                                    raise ValueError("numbered revision source issue lifecycle differs")
                                for record in candidate_recovery["prior_revision_contracts"]:
                                    for name, proof in record["stage_proofs"].items():
                                        saved = model_checkpoints.get(name)
                                        if saved is None or {key: saved[key] for key in (
                                            "role", "inputs_hash", "output_hash")} != proof:
                                            raise ValueError("numbered revision historical stage ownership differs")
                            reviews.extend(final_review.findings)
                            retired_reader_texts.clear()
                            coverage_reuse.clear()
                            previous_factual = factual_payloads[source_stage]["research"]
                            pending_contexts = ()
                            if contract == V5_CONTRACT:
                                if generation == target_generation:
                                    pending_contexts = tuple(candidate_recovery[
                                        "pending_coverage_contexts"])
                                else:
                                    records = [item for item in candidate_recovery.get(
                                        "prior_pending_contexts", ()) if item["generation"] == generation]
                                    if len(records) != 1 or digest(records[0]["contexts"]) != records[0][
                                        "contexts_sha256"]:
                                        raise ValueError("historical v5 pending context is missing")
                                    pending_contexts = tuple(records[0]["contexts"])
                            reopened = split_compound_obligations(
                                previous_factual["inherited_issues"],
                                previous_factual["resolution_evidence"],
                                reader_revision=True, verification_repair=True)
                            if contract == V5_CONTRACT:
                                reopened = project_pending_issues(reopened, pending_contexts)
                            atomic_reopened = compound_coverage_issues(reopened)
                            source_findings = {
                                digest(finding.model_dump(mode="json")): finding.model_dump(mode="json")
                                for finding in final_review.findings
                            }
                            source_terminal_review = {"stage": source_stage,
                                "reader_sha256": source_hash,
                                "findings": [{"source_finding_sha256": key, **value}
                                             for key, value in source_findings.items()]}
                            source_issues = reader_verifications[request.report_language][
                                "issue_lifecycle"]["issues"]
                            source_text_witnesses = revision_witness_catalog(
                                contract, snapshot, source_terminal_review["findings"], source_issues)
                            pending_coverage = (pending_entries(pending_contexts)
                                if contract == V5_CONTRACT else deferred_coverage_eligibility(
                                    reader_verifications[request.report_language], model_checkpoints,
                                    rendered.reader_text) if contract.deferred_coverage else ())
                            if contract == V5_CONTRACT:
                                terminal_hashes = set(source_findings)
                                if (len({item["issue_id"] for item in pending_coverage})
                                        != len(pending_coverage)
                                        or any(item["source_finding_sha256"] not in terminal_hashes
                                               for item in pending_coverage)):
                                    raise ValueError("v5 pending context is not in source terminal findings")
                            if (generation == target_generation and contract.deferred_coverage
                                    and digest(pending_coverage) != candidate_recovery[
                                        "deferred_coverage_eligibility_sha256"]):
                                raise ValueError("numbered revision deferred coverage eligibility differs")
                            if (generation == target_generation
                                    and digest(source_text_witnesses)
                                    != candidate_recovery["source_witness_catalog_sha256"]):
                                raise ValueError("generic revision source witness catalog differs")
                            revision_data = {
                                **editor_data, "limitations": list(gaps),
                                "draft_to_repair": source_draft.model_dump(mode="json"),
                                "repair_findings": final_review.model_dump(mode="json"),
                                "source_terminal_review_sha256": digest(
                                    final_review.model_dump(mode="json")),
                                "current_applicability": reopened,
                                "source_candidate": {"stage": source_stage,
                                    "reader_sha256": source_hash, "reader_text": rendered.reader_text},
                                "source_writer_stage": source_writer,
                                "source_text_witnesses": source_text_witnesses,
                                "repair_policy": contract.writer_requirements,
                                "reader_revision_policy": contract.policy,
                                **({"pending_coverage": list(pending_coverage)}
                                   if contract.deferred_coverage else {}),
                                **({"pending_coverage_contexts": list(pending_contexts)}
                                   if contract == V5_CONTRACT else {}),
                            }
                            active_stage = writer_stage
                            revision_payload = model_payload(writer_stage, "editor", revision_data,
                                draft_schema, request.report_language)
                            revision_cached = (store.load_stage(writer_stage, revision_payload) is not None
                                or getattr(services.models, "has_saved_reply", lambda *_: False)(
                                    writer_stage, revision_payload))
                            reader_bytes = len(rendered.reader_text.encode("utf-8"))
                            finding_growth = len(final_review.findings) * 4096
                            correction_growth = (GENERIC_V5_CORRECTION_CONTEXT_MAX_BYTES
                                                 if contract == V5_CONTRACT else 0)
                            future_growth = 2 * reader_bytes + finding_growth + 8192
                            estimated_calls = [FinalizationCallPlan(
                                writer_stage, "revision", revision_payload, 16_000,
                                request.budget.call_timeout_seconds, cache_hit=revision_cached,
                                role="editor", valuation_method=request.valuation_method),
                                FinalizationCallPlan(review_stage, "revision_factual",
                                    b" " * (model_input_bytes(factual_payloads[source_stage],
                                        role="verifier", output_token_envelope=16_000,
                                        valuation_method=request.valuation_method) + future_growth),
                                    16_000, request.budget.call_timeout_seconds, role="verifier",
                                    valuation_method=request.valuation_method)]
                            for index, items in enumerate(coverage_batches(atomic_reopened)):
                                batch_stage = f"{review_stage}-coverage-{index}"
                                payload = model_payload(batch_stage, "verifier", coverage_review_data(
                                    items, rendered.reader_text), ReaderVerification,
                                    request.report_language, True,
                                    role_call_index=role_indices["verifier"] + 1 + index)
                                estimated_calls.append(FinalizationCallPlan(
                                    batch_stage, "revision_coverage",
                                    b" " * (model_input_bytes(payload, role="verifier",
                                        output_token_envelope=coverage_envelope,
                                        valuation_method=request.valuation_method) + future_growth),
                                    coverage_envelope, request.budget.call_timeout_seconds,
                                    role="verifier", valuation_method=request.valuation_method))
                            workload = finalization_workload(estimated_calls)
                            if contract == V5_CONTRACT:
                                workload = reserve_global_coverage_delta(workload,
                                    has_slots=bool(atomic_reopened))
                            remaining = request.budget.total_tokens - tracker.usage.total_tokens
                            prompt_limit = getattr(services.models, "max_prompt_utf8_bytes", None)
                            writer_prompt_bytes = len(model_prompt(revision_payload))
                            # The new reader is not known until the paid writer returns.
                            # Admit the unchanged source reader under the *new* factual
                            # contract now, with a bounded growth margin. The actual
                            # post-writer factual and coverage prompts are still checked
                            # exactly before their own dispatches.
                            baseline_factual = {
                                **previous_factual,
                                "inherited_issues": reopened,
                                "source_terminal_review": source_terminal_review,
                                "source_terminal_review_sha256": digest(
                                    final_review.model_dump(mode="json")),
                                "source_text_witnesses": source_text_witnesses,
                                "issue_resolution_policy": (LIFECYCLE_POLICY
                                    + GENERIC_CASHFLOW_POLICY),
                                **({"pending_coverage": list(pending_coverage)}
                                   if contract.deferred_coverage else {}),
                                **({"pending_coverage_contexts": list(pending_contexts)}
                                   if contract == V5_CONTRACT else {}),
                            }
                            baseline_payload = model_payload(
                                review_stage, "verifier", baseline_factual,
                                RevisionLifecycleVerification, request.report_language)
                            baseline_factual_bytes = len(model_prompt(baseline_payload))
                            factual_headroom = min(32_768, max(16_384, reader_bytes // 2))
                            revision_plan = {
                                "policy": contract.policy, "generation": generation,
                                "source_candidate_stage": source_stage,
                                "source_reader_sha256": source_hash,
                                "source_writer_stage": source_writer,
                                "remaining_tokens": remaining, "remaining_path": workload,
                                "wall_time": time_plan(workload),
                                "fits_reserve": workload["conservative_reserve_tokens"] < remaining,
                                "writer_prompt_admission": {"prompt_bytes": writer_prompt_bytes,
                                    "limit_bytes": prompt_limit,
                                    "fits": prompt_limit is None or writer_prompt_bytes <= prompt_limit},
                                "baseline_factual_prompt_admission": {
                                    "source_reader_prompt_bytes": baseline_factual_bytes,
                                    "required_growth_headroom_bytes": factual_headroom,
                                    "limit_bytes": prompt_limit,
                                    "fits": prompt_limit is None or
                                        baseline_factual_bytes + factual_headroom <= prompt_limit,
                                    "future_candidate_is_estimated": True,
                                },
                                "reopened_issue_count": len(reopened),
                                "atomic_reopened_issue_count": len(atomic_reopened),
                                "future_prompt_growth_bytes": future_growth,
                                **({"accepted_correction_context_growth_bound_bytes":
                                    correction_growth} if contract == V5_CONTRACT else {}),
                                "future_candidate_sizes_are_estimates": True,
                            }
                            finalization_plans["revision_admission"] = revision_plan
                            store.save_stage("revision-admission", {}, revision_plan)
                            if not revision_cached:
                                if not revision_plan["writer_prompt_admission"]["fits"]:
                                    raise BudgetExhausted("revision_prompt_size_limit")
                                if not revision_plan["baseline_factual_prompt_admission"]["fits"]:
                                    raise BudgetExhausted(
                                        "revision_future_factual_prompt_headroom_insufficient")
                                if not revision_plan["fits_reserve"]:
                                    raise BudgetExhausted("revision_path_budget_insufficient")
                                if revision_plan["wall_time"]["stop_before_dispatch"]:
                                    raise BudgetExhausted("revision_path_time_insufficient")
                            source_draft = call(writer_stage, "editor", revision_data, draft_schema,
                                                True, language=request.report_language)
                            draft = prepare_draft(source_draft, request.report_language)
                            final_review, rendered = verify_reader(
                                draft, request.report_language, review_stage,
                                extra={"source_terminal_review": source_terminal_review,
                                       "source_terminal_review_sha256": digest(
                                           final_review.model_dump(mode="json")),
                                       "source_text_witnesses": source_text_witnesses,
                                       **({"pending_coverage": list(pending_coverage)}
                                          if contract.deferred_coverage else {}),
                                       **({"pending_coverage_contexts": list(pending_contexts)}
                                          if contract == V5_CONTRACT else {})},
                                authored=source_draft)
            else:
                final_review = call("verify_report", "verifier",
                                    {"draft": draft.model_dump(mode="json"), "analyses": outputs,
                                     "valuation": valuation}, VerificationOutput, True)
                final_review = block_reader_contradictions(final_review)
            reviews.extend(final_review.findings)
            if bounded_review:
                lifecycle = reader_verifications.get(request.report_language, {}).get("issue_lifecycle", {})
                retired = set(lifecycle.get("retired_issue_ids", []))
                active_admission_findings[:] = [finding for finding in pre_editor_findings
                    if "limitation-" + digest(finding.message) not in retired]
            active_admission_findings.extend(final_review.findings)
            if not final_review.reviewed_report or any(
                item.severity in ({"warning", "critical"} if evidence_led else {"critical"})
                for item in final_review.findings
            ):
                draft = None
                stop_reason = "verification_failed"
                gaps.append("Reader draft withheld because final verification did not pass.")
            else:
                drafts[request.report_language] = draft
                if evidence_led:
                    verified_readers[request.report_language] = rendered
                    reader_verifications[request.report_language]["exported"] = True
                translation_requirements = {
                    "mode": "faithful_translation",
                    "preserve": ["claims", "numbers", "limitations", "investment_view",
                                 "section_evidence_ids"],
                    "structure": ["same_section_count_and_order",
                                  "same_limitation_count_and_order",
                                  "same_evidence_ids_order_per_section"],
                }
                for language in request.additional_report_languages:
                    translation_data = {
                        **editor_data,
                        "source_verified_draft": draft.model_dump(mode="json"),
                        "source_draft_with_placeholders": source_draft.model_dump(mode="json"),
                        "translation_requirements": translation_requirements,
                    }
                    translated = call(f"editor-{language.lower()}", "editor", translation_data,
                                      draft_schema, True, language=language)
                    validate_translation(draft, translated)
                    translated_authored = translated
                    translated = prepare_draft(translated, language)
                    translation_review_data = {
                        "source_verified_draft": draft.model_dump(mode="json"),
                        "source_draft_with_placeholders": source_draft.model_dump(mode="json"),
                        "translation_requirements": translation_requirements,
                    }
                    if evidence_led:
                        translated_review, translated_render = verify_reader(
                            translated, language, f"verify-report-{language.lower()}",
                            translation_review_data, authored=translated_authored)
                    else:
                        translated_review = call(
                        f"verify-report-{language.lower()}", "verifier",
                        {"draft": translated.model_dump(mode="json"),
                         "source_verified_draft": draft.model_dump(mode="json"),
                         "source_draft_with_placeholders": source_draft.model_dump(mode="json"),
                         "translation_requirements": translation_requirements,
                         "analyses": outputs, "valuation": valuation},
                            VerificationOutput, True)
                    translated_review = block_reader_contradictions(translated_review)
                    reviews.extend(translated_review.findings)
                    active_admission_findings.extend(translated_review.findings)
                    if not translated_review.reviewed_report or any(
                            item.severity in ({"warning", "critical"} if evidence_led else {"critical"})
                            for item in translated_review.findings):
                        stop_reason = "verification_failed"
                        gaps.append(f"{language} reader draft withheld because final verification "
                                    "did not pass.")
                        break
                    drafts[language] = translated
                    if evidence_led:
                        verified_readers[language] = translated_render
                        reader_verifications[language]["exported"] = True
        except Exception as exc:
            # Do not emit provider errors or arbitrary validation inputs into reports.
            stop_reason = str(exc) if isinstance(exc, BudgetExhausted) else "stage_failed"
            failure_type = safe_failure_type(exc)
            failure_reason = codex_failure_reason(exc)
            failure_diagnostic = codex_failure_diagnostic(exc)
            failed_stage = active_stage
            gaps.append(f"Research stopped: {stop_reason}; inspect saved valid stages.")
        finally:
            save_resources()

        gaps.extend(item.message for item in reviews if item.severity in {"critical", "warning"})
        gaps.extend(valuation.get("limitations", []))
        assessment = Assessment(status="needs_review", findings=tuple(reviews))
        primary_draft = drafts.get(request.report_language)
        reader = _report(request, primary_draft, snapshot, gaps, request.report_language)
        rendered = None
        if evidence_led:
            rendered = verified_readers.get(request.report_language) or render_reader(
                request, None, snapshot, [*reader_inputs(), *reviews], request.report_language,
                compact=bounded_review and case_context is not None,
                bind_case_state=bool(request.financial_case_path), case_context=case_context,
                bind_cashflow_inputs=ENGINE_VERSION == "research-v2-preview-12")
            reader = rendered.reader_text
        admission = None
        if request.financial_case_path:
            verification_attestation = reader_verifications.get(request.report_language, {})
            admission = evaluate_admission(
                stop_reason=stop_reason, reader_exported=primary_draft is not None,
                reader_sha256=hashlib.sha256(reader.encode("utf-8")).hexdigest(),
                verification={key: verification_attestation[key] for key in (
                    "reader_sha256", "exported", "review", "required_limitation_ids", "validated_limitation_ids"
                ) if key in verification_attestation},
                usage=aggregate_usage(), findings=tuple(active_admission_findings),
                scope=case_context.scope if case_context is not None else None,
                case_reviewed=case_context.reviewed if case_context is not None else False,
                operating_scenarios_reviewed=bool(case_context is not None
                    and case_context.operating_scenarios is not None
                    and case_context.operating_scenarios.reviewed),
                cashflow_bridge_reviewed=bool(case_context is not None
                    and case_context.cashflow_bridge is not None
                    and case_context.cashflow_bridge.reviewed),
            )
            assessment = Assessment(status=admission.assessment_status, findings=tuple(reviews))
            if admission.report_completion != "complete" and stop_reason == "completed_needs_review":
                stop_reason = "admission_failed"
                gaps.append("Report admission failed; exported reader is not an admitted completed report.")
        artifacts = {
            "reader_report.md": reader.encode("utf-8"),
            "audit_report.md": ("# Research audit\n\n```json\n"
                                 + canonical_json(outputs).decode("utf-8") + "\n```\n").encode(),
            "evidence.json": canonical_json(snapshot),
            "research.json": canonical_json(outputs),
            "valuation_inputs.json": canonical_json(proposal),
            "valuation_results.json": canonical_json(valuation),
            "quality.json": canonical_json(assessment),
            "run_metadata.json": canonical_json({"schema_version": 1, "engine": ENGINE_VERSION,
                "identity": identity, "ticker": request.ticker, "cutoff": request.cutoff,
                "backend": request.backend, "internal_language": request.internal_language,
                "coverage_batch_policy": request.coverage_batch_policy,
                "model_service_kind": getattr(services.models, "kind", "injected"),
                "usage_measurement": (
                    "mixed_imported_historical_diagnostic_and_current"
                    if recovery is not None
                    else "saved_response_counters"
                    if getattr(services.models, "kind", None) == "replay"
                    else "provider_reported"
                ),
                "hard_output_token_cap": getattr(services.models, "supports_hard_output_cap", False),
                "report_language": request.report_language, "usage": aggregate_usage(),
                "usage_by_stage": usage_by_stage,
                "total_tokens": aggregate_usage().total_tokens, "stop_reason": stop_reason,
                "failure_type": failure_type,
                "failure_reason": failure_reason, "failure_diagnostic": failure_diagnostic,
                "failed_stage": failed_stage,
                "call_timings": call_timings,
                "elapsed_seconds": tracker.elapsed_seconds, "production_accepted": False}),
        }
        if evidence_led:
            # The reader bytes remain frozen at verification. The companion audit
            # must still include later warnings/failures (e.g. an optional translation).
            final_audit = render_reader(request, primary_draft, snapshot,
                                        reader_inputs(include_retired=True), request.report_language,
                                        compact=bounded_review and case_context is not None,
                                        bind_case_state=bool(request.financial_case_path),
                                        bind_cashflow_inputs=ENGINE_VERSION == "research-v2-preview-12",
                                        case_context=case_context).limitations_audit
            verification = reader_verifications.get(request.report_language, {})
            dispositions = {item["issue_id"]: item for item in verification.get("review", {}).get(
                "limitation_dispositions", [])} if verification.get("exported") else {}
            lifecycle = verification.get("issue_lifecycle", {})
            retired_issues = {item["issue_id"]: item for item in lifecycle.get("issues", [])
                              if item["status"] in {"resolved", "superseded"}} if (
                verification.get("exported")
                and lifecycle.get("reader_sha256") == hashlib.sha256(reader.encode("utf-8")).hexdigest()
            ) else {}
            audit_items = [*final_audit["unresolved_issues"]["consolidated_exact_text"],
                           *final_audit["draft_limitations"]["consolidated_exact_text"]]
            for item in audit_items:
                disposition_id = "limitation-" + digest(item["original_text"])
                item["disposition_id"] = disposition_id
                retired_issue = retired_issues.get(disposition_id)
                if retired_issue:
                    # The history-inclusive rendering above is not the exported
                    # reader. Keep retired records, but do not claim they appear
                    # in it or remain unresolved coverage obligations.
                    item["lifecycle_status"] = retired_issue["status"]
                    item["displayed_in_reader"] = False
                    item["reader_display"] = "audit_only_retired"
                    continue
                disposition = dispositions.get(disposition_id)
                if disposition:
                    item["verified_disposition"] = disposition
                    if disposition["decision"] == "reader_covered":
                        item["displayed_in_reader"] = True
                        item["reader_display"] = "verified_editorial_representation"
                    elif item.get("reader_display") == "authored_material_gaps_pending_verification":
                        item["reader_display"] = disposition["decision"]
            final_audit["unresolved_issues"]["unrepresented_issue_ids"] = [
                item["issue_id"] for item in final_audit["unresolved_issues"]["consolidated_exact_text"]
                if not item["displayed_in_reader"] and item["reader_display"] != "audit_only_retired"]
            artifacts["reader_limitations.json"] = canonical_json({
                **final_audit,
                "exported_reader_sha256": hashlib.sha256(reader.encode("utf-8")).hexdigest(),
                "audit_includes_post_verification_issues": True,
                "review_history": [item.model_dump(mode="json") for item in reviews],
            })
            artifacts["reader_verification.json"] = canonical_json(reader_verifications)
            artifacts["calculated_values.json"] = canonical_json(calculated_values)
            if bounded_review:
                artifacts["finalization_plan.json"] = canonical_json(finalization_plans)
            artifacts["investigation.json"] = canonical_json({
                "ledger": investigation_ledger, "cycles": investigation_cycles,
                "source_acquisition": "injected_provider_only; local retrieval does not acquire new sources",
            })
        if admission is not None:
            artifacts["report_admission.json"] = canonical_json(admission)
            artifacts["case_input.json"] = frozen["financial_case_path"]
            artifacts["case_material_delivery.json"] = canonical_json({
                "stages": case_material_delivery,
                "exclusions": ["planner and independent_challenge: evidence-only, blinded to lead case",
                               "coverage-only verifier: exact reader and issue coverage, not factual review"],
                "measurement": "serialized orchestration payload; not provider token telemetry",
            })
            if case_context is not None:
                artifacts.update(case_context.artifacts)
            artifacts["model_appendix.md"] = case_model_appendix(
                calculated_values, request.report_language,
                has_operating_scenarios=case_context is not None and case_context.operating_scenarios is not None,
                has_cashflow_bridge=case_context is not None and case_context.cashflow_bridge is not None,
            )
        if recovery is not None:
            recovery_provenance = {
                **current_recovery_context(),
                "aggregate_usage": aggregate_usage().model_dump(mode="json"),
                "known_token_lower_bound": aggregate_usage().total_tokens,
                "usage_total_unknown": not aggregate_usage().complete,
                "budget_usage": tracker.usage.model_dump(mode="json"),
                "elapsed_seconds": tracker.elapsed_seconds,
            }
            artifacts["recovery_provenance.json"] = canonical_json(recovery_provenance)
        if candidate_review_stage and (store.directory / "finalization_checkpoint.json").exists():
            artifacts["finalization_checkpoint.json"] = read_bytes(store.directory / "finalization_checkpoint.json")
        if request.additional_report_languages:
            for language, completed_draft in drafts.items():
                text = (verified_readers[language].reader_text if evidence_led else _report(
                    request, completed_draft, snapshot, gaps, language))
                artifacts[_REPORT_FILENAMES[language]] = text.encode("utf-8")
        artifacts["run_metadata.json"] = canonical_json({
            **parse_json(artifacts["run_metadata.json"]),
            "quality_revision": request.quality_revision,
            **({"additional_report_languages": request.additional_report_languages}
               if request.additional_report_languages else {}),
            **({"known_token_lower_bound": aggregate_usage().total_tokens,
                "usage_total_unknown": not aggregate_usage().complete}
               if recovery is not None else {}),
            **({"budget_scope": "new_continuation_calls_only",
                "incremental_usage": tracker.usage.model_dump(mode="json"),
                "elapsed_seconds_scope": "continuation_only",
                "source_elapsed_seconds_scope": "last_checkpoint_not_total_wall"}
               if case_recovery is not None or candidate_recovery is not None else {}),
            "update": describe_update(prior, snapshot)})
        if request.dossier_dir is not None:
            created = datetime.now(timezone.utc)
            if created < request.cutoff:
                raise ValueError("cannot persist a dossier before its research cutoff")
            dossier = Dossier(
                id=f"research-{identity[:24]}-{digest(artifacts['run_metadata.json'].hex())[:12]}",
                ticker=request.ticker, cutoff=request.cutoff, created_at=created,
                parent_id=prior.id if prior else None, evidence_hash=digest(snapshot),
                assessment=assessment,
                findings=tuple(finding for output in outputs.values()
                               for finding in output.get("findings", [])),
                claims=tuple(claim for output in outputs.values()
                             for claim in output.get("claims", [])),
                open_questions=tuple(outputs.get("planner", {}).get("questions", [])),
                artifact_hashes={name: hashlib.sha256(content).hexdigest()
                                 for name, content in artifacts.items()},
                coverage_watermark=prior.coverage_watermark if prior else None)
            # A preview is appended for diagnosis; it never promotes accepted history
            # or advances the previous coverage watermark.
            DossierStore(request.dossier_dir).save_dossier(
                dossier, evidence_available_at=request.cutoff)
            artifacts["dossier.json"] = canonical_json(dossier)
        for name, content in artifacts.items():
            atomic_write(store.directory / name, content)
        result = ResearchResult(ticker=request.ticker, cutoff=request.cutoff,
                                artifacts={name: str(store.directory / name) for name in artifacts},
                                artifact_hashes={name: hashlib.sha256(data).hexdigest()
                                                 for name, data in artifacts.items()},
                                assessment=assessment, usage=aggregate_usage(),
                                unresolved_gaps=tuple(dict.fromkeys(gaps)), stop_reason=stop_reason)
        if result.stop_reason == "completed_needs_review":
            store.save_stage("completed-result", {}, result.model_dump(mode="json"))
        atomic_write(store.directory / "result.json", canonical_json(result))
        return result
