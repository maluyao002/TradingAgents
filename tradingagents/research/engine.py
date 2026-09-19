"""Recovery-safe research workflow with injected evidence and model services.

This opt-in foundation emits review-required reports. Production acceptance stays
disabled until company-specific schedules and source coverage pass M3/M6 gates.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from .admission import evaluate_admission
from .budget import BudgetExhausted, BudgetTracker
from .calculated_values import calculation_catalog, render_calculations
from .case_context import load_case_context
from .case_report import CASE_READER_REQUIREMENTS, CaseReportDraft
from .context import pack_evidence
from .contracts import (
    Assessment,
    Dossier,
    EvidenceSnapshot,
    ReportLanguage,
    ResearchRequest,
    ResearchResult,
    Usage,
)
from .dossiers import DossierStore
from .equity_valuation import EquityDCFModelInput, EquityForecastPeriod, equity_dcf_valuation
from .evidence import validate_snapshot
from .investigation import collect_tasks, make_ledger
from .investigation_review import InvestigationReview
from .reader import ReaderIssue, render_reader
from .rendering import render_references
from .report_review import ReaderVerification, check_dispositions, limitation_packet
from .result_scope import scope_calculation
from .review_batches import (
    CoverageBatchResult,
    block_reader_contradictions,
    combine_coverage,
    coverage_batches,
    finalization_allowance,
)
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
        if recovery is not None and request.financial_case_path:
            raise ValueError("historical-prefix recovery cannot import stages into a new financial case")
        if recovery is not None:
            if not isinstance(recovery, dict) or recovery.get("schema_version") != 1:
                raise ValueError("invalid explicit recovery context")
            restore = getattr(services.models, "restore_recovery_context", None)
            if previous.get("recovery") is not None:
                if restore is None:
                    raise ValueError("recovery service cannot restore provenance")
                restore(previous["recovery"])
                recovery = services.models.recovery_context
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
        role_indices = defaultdict(int)
        outputs, reviews = {}, []
        active_admission_findings = []
        gaps = ["V2 source coverage and company-specific model acceptance remain pending."]
        snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff)
        draft, drafts, valuation = None, {}, {"status": "unavailable"}
        evidence_led = request.quality_revision != "foundation"
        bounded_review = request.quality_revision == "evidence-led-bounded"
        verified_readers = {}
        reader_verifications = {}
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

        def aggregate_usage():
            if recovery is None:
                return tracker.usage
            # The known historical counters are useful for budget accounting, but
            # the source dispatch was never measured.  It remains incomplete for
            # every cumulative artifact and result produced by recovery.
            return tracker.usage.model_copy(update={"complete": False})

        def save_resources(dispatched=None):
            resource_data = {
                "usage": aggregate_usage().model_dump(mode="json"),
                "elapsed_seconds": tracker.elapsed_seconds,
                "dispatched": dispatch_unsettled if dispatched is None else dispatched,
                "by_stage": usage_by_stage,
            }
            if recovery is not None:
                resource_data.update(
                    budget_usage=tracker.usage.model_dump(mode="json"),
                    recovery=services.models.recovery_context,
                )
            store.save_stage("resources", {}, resource_data)

        def call(stage, role, data, schema, finalization=False, language=None,
                 coverage_only=False):
            nonlocal dispatch_unsettled
            role_index = role_indices[role]
            role_indices[role] += 1
            queries = tuple(dict.fromkeys((*investigation_queries, *_research_queries(
                role, data, outputs)))) if evidence_led else ()
            if coverage_only and (not bounded_review or role != "verifier"):
                raise ValueError("coverage-only calls require the bounded reader verifier")
            payload = {**instruction(role, schema), "evidence": (
                {"scope": "Reader limitation coverage only; factual verification is a separate mandatory stage."}
                if coverage_only else _prompt_evidence(snapshot, queries)),
                       "research": data, "cutoff": request.cutoff.isoformat(),
                       "mandate": request.mandate, "stage": stage, "role_call_index": role_index,
                       "valuation_months": request.valuation_months,
                       "return_months": request.return_months,
                       "language": language or request.internal_language}
            if coverage_only:
                payload["system"] += (
                    " This is a limitation-coverage-only call, not source verification. "
                    "Assess every supplied issue against the exact reader text. Leave factual "
                    "claim-ID decisions empty; a separate mandatory full-context call checks facts. "
                    "Keep each disposition rationale concise without omitting its reasoning.")
            if evidence_led:
                payload["quality_requirements"] = {
                    "revision": "evidence-led-bounded-1" if bounded_review else "evidence-led-1",
                    "retrieval": "Lexical matches are leads, not proof or closure of a question.",
                    "reader": "Write a thesis-led report with a causal financial bridge and "
                              "explicit disconfirming evidence. Consolidate genuinely related "
                              "material limitations into concise authored prose; retain all "
                              "financially consequential uncertainty. Operational logs belong "
                              "in the audit, not the reader. Do not hide missing valuation. "
                              "Use {{calc:ID}} for supplied code-calculated values, never "
                              "retype or recompute them. They are illustrative assumptions-based "
                              "calculations, not reported facts; display is rounded to two decimals.",
                    "verification": "When rendered_reader is supplied, review that exact "
                                    "export, not only the intermediate draft. Repair only with "
                                    "supplied evidence; no new facts or unsupported calculations.",
                }
            if case_context is not None and not coverage_only and stage != "independent_challenge":
                payload["financial_case"] = case_context.model_context()
                payload["case_reader_requirements"] = CASE_READER_REQUIREMENTS
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
            cached = store.load_stage(stage, payload)
            if cached is not None:
                return schema.model_validate(cached)
            # UTF-8 bytes are a conservative input bound, plus an output envelope.
            output_envelope = 6_000 if coverage_only else 16_000
            envelope = len(canonical_json(payload)) + output_envelope
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
                permit = tracker.reserve(envelope, finalization=finalization)
                dispatch_unsettled = True
                save_resources()
                timeout_seconds = permit.timeout_seconds
            else:
                # Imported replies and diagnostics are already materialized and
                # cannot dispatch here. Their known usage is already in the
                # explicit recovery seed before the first stage admission.
                timeout_seconds = tracker.admit(finalization=finalization)
            try:
                reply = services.models.complete(role, {**payload,
                    "timeout_seconds": timeout_seconds, "max_output_tokens": output_envelope}, request)
                reply = ModelReply.model_validate(reply)
                if origin == "current_live":
                    tracker.complete(permit, reply.usage)
                usage_by_stage.setdefault(stage, []).append({
                    "role": role, "model": request.models[role].model,
                    "effort": request.models[role].effort,
                    "usage_origin": origin,
                    **reply.usage.model_dump(mode="json")})
                save_resources(dispatched=False)
                dispatch_unsettled = False
            except BaseException:
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
            return output

        def prepare_draft(candidate, language):
            if any(set(section.evidence_ids) - _known_ids(snapshot)
                   for section in candidate.sections):
                raise ValueError("draft invented evidence identifiers")
            eligible_facts = tuple(fact for fact in snapshot.facts
                                   if fact.id in _known_ids(snapshot))
            return candidate.model_copy(update={"sections": tuple(
                section.model_copy(update={"text": render_calculations(render_references(
                    section.text, eligible_facts, language), calculated_values, language)
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

        def reader_inputs():
            # Mandatory model caveats do not depend on the editor remembering them.
            material = [ReaderIssue(
                message=message, provenance_id=f"valuation.limitations[{index}]",
                severity="critical", category="financial", code="valuation_scope",
            ) for index, message in enumerate(valuation.get("limitations", []))]
            if valuation.get("status") == "unavailable" and not material:
                material.append(ReaderIssue(
                    message="Valuation unavailable; this report supplies no supported price target.",
                    provenance_id="valuation.status", severity="critical", category="financial",
                ))
            return [*gaps, *material]

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

        def verify_reader(candidate, language, stage, extra=None):
            rendered = render_reader(request, candidate, snapshot, reader_inputs(), language)
            rendered_hash = hashlib.sha256(rendered.reader_text.encode("utf-8")).hexdigest()
            limitations = limitation_packet(required_limitations())
            review_data = {
                "draft": candidate.model_dump(mode="json"), "analyses": outputs,
                "valuation": valuation, "rendered_reader": rendered.reader_text,
                "valuation_inputs": proposal.model_dump(mode="json"),
                "calculated_values": [value.model_dump(mode="json") for value in calculated_values],
                "rendered_reader_sha256": rendered_hash, **(extra or {}),
                "limitation_review": limitations,
                "limitation_policy": "For EVERY limitation issue ID return one disposition. "
                    "Material financial/research caveats must be reader_covered with an exact "
                    "excerpt showing the caveat in the rendered reader. Audit-only is allowed "
                    "only for genuinely operational or immaterial details, with a specific "
                    "rationale. Never classify a financially material unknown as immaterial "
                    "to improve readability. Return unresolved when it is missing.",
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
                batches = coverage_batches(limitations)
                factual_data = {key: value for key, value in review_data.items()
                                if key not in {"limitation_review", "limitation_policy"}}
                factual_data["review_scope"] = (
                    "Verify the exact full report's factual, numerical and causal claims. "
                    "Do not return limitation dispositions here; complete per-issue material "
                    "caveat coverage is checked in separate mandatory batches against these same bytes.")
                main_review = call(stage, "verifier", factual_data, VerificationOutput,
                                   True, language=language)
                batch_results = []
                for index, items in enumerate(batches):
                    batch_stage = f"{stage}-coverage-{index}"
                    batch_review = call(batch_stage, "verifier", {
                        "rendered_reader": rendered.reader_text,
                        "rendered_reader_sha256": rendered_hash,
                        "limitation_review": list(items),
                        "limitation_policy": review_data["limitation_policy"],
                        "review_scope": "Only assess each supplied issue's materiality and "
                            "coverage in the exact rendered text. Do not adjudicate factual "
                            "claim IDs or imply source verification. Set reviewed_report only "
                            "if you performed this coverage review. Each distinct issue needs "
                            "its own justified disposition; shared prose does not automatically "
                            "cover every issue. Unknown materiality must remain unresolved.",
                    }, ReaderVerification, True, language=language, coverage_only=True)
                    batch_results.append(CoverageBatchResult(rendered_hash, items, batch_review))
                    reader_verifications[language]["coverage_batches"].append({
                        "stage": batch_stage, "reader_sha256": rendered_hash,
                        "issue_ids": [item["issue_id"] for item in items],
                        "review": batch_review.model_dump(mode="json"),
                    })
                verification = combine_coverage(main_review, batch_results, limitations,
                                                rendered.reader_text, rendered_hash)
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
                    "validated_limitation_ids": [item.issue_id for item in verification.limitation_dispositions]
                    if verification.reviewed_report and not any(
                        item.severity in {"warning", "critical"} for item in verification.findings) else [],
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
            if recovery is not None:
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
            plan = call("planner", "planner", prior_data, AnalysisOutput)
            if not 3 <= len(plan.questions) <= 5:
                raise ValueError("planner must supply 3-5 decisive questions")
            outputs["planner"] = plan.model_dump(mode="json")
            question_data = {"questions": [q.model_dump(mode="json") for q in plan.questions]}
            # The independent challenge never sees the lead thesis or model first.
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
                    context_bytes = (len(canonical_json(_prompt_evidence(snapshot)))
                                     + 2 * len(canonical_json(outputs)) + len(canonical_json(proposal)))
                    if case_context is not None:
                        context_bytes += (len(canonical_json(case_context.model_context()))
                                          + len(CASE_READER_REQUIREMENTS.encode("utf-8")))
                    allowance = finalization_allowance(
                        inventory, context_bytes=context_bytes,
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
                final_review, rendered = verify_reader(draft, request.report_language, "verify_report")
                if (not final_review.reviewed_report or any(
                        item.severity in {"warning", "critical"} for item in final_review.findings)):
                    reviews.extend(final_review.findings)
                    gaps.extend(item.message for item in final_review.findings
                                if item.severity in {"warning", "critical"})
                    # One repair only, admitted from the existing finalization reserve.
                    source_draft = call("repair_report", "editor", {
                        **editor_data, "draft_to_repair": source_draft.model_dump(mode="json"),
                        "repair_findings": final_review.model_dump(mode="json"),
                        "repair_policy": "Resolve or explicitly qualify findings using existing "
                                         "evidence only. Do not remove material limitations.",
                    }, draft_schema, True, language=request.report_language)
                    draft = prepare_draft(source_draft, request.report_language)
                    final_review, rendered = verify_reader(
                        draft, request.report_language, "verify_repaired_report")
            else:
                final_review = call("verify_report", "verifier",
                                    {"draft": draft.model_dump(mode="json"), "analyses": outputs,
                                     "valuation": valuation}, VerificationOutput, True)
                final_review = block_reader_contradictions(final_review)
            reviews.extend(final_review.findings)
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
                    translated = prepare_draft(translated, language)
                    translation_review_data = {
                        "source_verified_draft": draft.model_dump(mode="json"),
                        "source_draft_with_placeholders": source_draft.model_dump(mode="json"),
                        "translation_requirements": translation_requirements,
                    }
                    if evidence_led:
                        translated_review, translated_render = verify_reader(
                            translated, language, f"verify-report-{language.lower()}",
                            translation_review_data)
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
            failure_type = type(exc).__name__
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
                request, None, snapshot, [*reader_inputs(), *reviews], request.report_language)
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
                "elapsed_seconds": tracker.elapsed_seconds, "production_accepted": False}),
        }
        if evidence_led:
            # The reader bytes remain frozen at verification. The companion audit
            # must still include later warnings/failures (e.g. an optional translation).
            final_audit = render_reader(request, primary_draft, snapshot,
                                        reader_inputs(), request.report_language).limitations_audit
            verification = reader_verifications.get(request.report_language, {})
            dispositions = {item["issue_id"]: item for item in verification.get("review", {}).get(
                "limitation_dispositions", [])} if verification.get("exported") else {}
            for item in final_audit["unresolved_issues"]["consolidated_exact_text"]:
                disposition_id = "limitation-" + digest(item["original_text"])
                item["disposition_id"] = disposition_id
                disposition = dispositions.get(disposition_id)
                if disposition:
                    item["verified_disposition"] = disposition
                    if disposition["decision"] == "reader_covered":
                        item["displayed_in_reader"] = True
                        item["reader_display"] = "verified_editorial_representation"
            final_audit["unresolved_issues"]["unrepresented_issue_ids"] = [
                item["issue_id"] for item in final_audit["unresolved_issues"]["consolidated_exact_text"]
                if not item["displayed_in_reader"]]
            artifacts["reader_limitations.json"] = canonical_json({
                **final_audit,
                "exported_reader_sha256": hashlib.sha256(reader.encode("utf-8")).hexdigest(),
                "audit_includes_post_verification_issues": True,
                "review_history": [item.model_dump(mode="json") for item in reviews],
            })
            artifacts["reader_verification.json"] = canonical_json(reader_verifications)
            artifacts["calculated_values.json"] = canonical_json(calculated_values)
            artifacts["investigation.json"] = canonical_json({
                "ledger": investigation_ledger, "cycles": investigation_cycles,
                "source_acquisition": "injected_provider_only; local retrieval does not acquire new sources",
            })
        if admission is not None:
            artifacts["report_admission.json"] = canonical_json(admission)
            artifacts["case_input.json"] = frozen["financial_case_path"]
            artifacts["case_material_delivery.json"] = canonical_json({
                "stages": case_material_delivery,
                "exclusions": ["independent_challenge: blinded to lead case",
                               "coverage-only verifier: exact reader and issue coverage, not factual review"],
                "measurement": "serialized orchestration payload; not provider token telemetry",
            })
            if case_context is not None:
                artifacts.update(case_context.artifacts)
            artifacts["model_appendix.md"] = (
                b"# Financial model appendix\n\n"
                b"No supported operating valuation, equity target or funding conclusion is exported. "
                b"Financial schedules are not yet bound to reviewed forecasts. No unconstrained "
                b"valuation-model call was dispatched in this case-backed workflow.\n\n"
                b"See financial_case.json, financial_reconciliation.json and case_context.json "
                b"for source-bound schedules, conventions, review status and unresolved prerequisites. "
                b"If case validation failed, those validated artifacts are absent; case_input.json "
                b"is retained only as the unvalidated input.\n\n"
                b"Report completion, conditional analytical eligibility and acceptance prerequisites "
                b"are recorded separately in report_admission.json. Production activation is disabled.\n"
            )
        if recovery is not None:
            recovery_provenance = {
                **services.models.recovery_context,
                "aggregate_usage": aggregate_usage().model_dump(mode="json"),
                "known_token_lower_bound": aggregate_usage().total_tokens,
                "usage_total_unknown": True,
                "budget_usage": tracker.usage.model_dump(mode="json"),
                "elapsed_seconds": tracker.elapsed_seconds,
            }
            artifacts["recovery_provenance.json"] = canonical_json(recovery_provenance)
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
                "usage_total_unknown": True}
               if recovery is not None else {}),
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
