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

from .budget import BudgetExhausted, BudgetTracker
from .contracts import (
    Assessment,
    Dossier,
    EvidenceSnapshot,
    ResearchRequest,
    ResearchResult,
    Usage,
)
from .dossiers import DossierStore
from .evidence import validate_snapshot
from .rendering import render_references
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


def _known_ids(snapshot):
    eligible = {source.id for source in snapshot.sources
                if source.published_at is not None and source.availability == "full_text"}
    return eligible | {item.id for item in (*snapshot.facts, *snapshot.events)
                       if item.source_id in eligible} | {
                           item.id for item in snapshot.expectations
                           if not set(item.source_ids) - eligible}


def _prompt_evidence(snapshot):
    """Keep undated/current API payloads in the audit, never in historical reasoning."""
    known = _known_ids(snapshot)
    payload = snapshot.model_dump(mode="json")
    for key in ("sources", "facts", "events", "expectations"):
        payload[key] = [item for item in payload[key] if item["id"] in known]
    return payload


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
    for data, schema in [(raw, FCFFModelInput), (raw.get("units", {}), ValuationUnits),
                         *((period, ForecastPeriod) for period in raw.get("periods", []))]:
        if not isinstance(data, dict) or set(data) - {field.name for field in fields(schema)}:
            raise ValueError("unsupported valuation input fields")
    model = TypeAdapter(FCFFModelInput).validate_python(raw)
    if model.as_of_date != request.cutoff.astimezone(ZoneInfo(request.timezone)).date():
        raise ValueError("valuation date differs from research cutoff")
    required = {"current_revenue", "current_working_capital", "net_debt",
                "current_diluted_shares", "discount_rate", "terminal_growth",
                "units.currency", "units.amount_scale", "units.share_scale"}
    for index, period in enumerate(model.periods):
        required.update(f"periods.{index}.{field.name}" for field in fields(period)
                        if field.name not in {"label", "period_start", "period_end", "discount_years"})
    missing = required - proposal.assumptions.keys()
    if missing or not proposal.evidence_ids:
        return {"status": "unavailable", "limitations": [
            "Missing evidence-linked assumption coverage: " + ", ".join(sorted(missing or {"evidence_ids"}))]}
    known = _known_ids(snapshot)
    if any(set(support.evidence_ids) - known for support in proposal.assumptions.values()):
        raise ValueError("valuation assumptions invented evidence identifiers")
    facts = {fact.id: fact for fact in snapshot.facts if fact.id in known}
    opening_metrics = {"current_revenue": "revenue", "current_working_capital": "working_capital",
                       "net_debt": "net_debt", "current_diluted_shares": "diluted_shares"}
    for name, metric in opening_metrics.items():
        support = proposal.assumptions[name]
        scale = model.units.share_scale if name == "current_diluted_shares" else model.units.amount_scale
        compatible = [facts[sid] for sid in support.evidence_ids if sid in facts
                      and facts[sid].metric == metric
                      and (facts[sid].unit == "shares" if name == "current_diluted_shares"
                           else facts[sid].currency == model.units.currency)]
        if support.kind != "reported" or not any(
                fact.normalized_value == getattr(model, name) * scale for fact in compatible):
            return {"status": "unavailable", "limitations": [f"Opening input not bound to a matching fact: {name}"]}
    result = dcf_valuation(model)
    return {"status": "illustrative", "result": asdict(result),
            "limitations": [*result.limitations, *proposal.scope_limitations,
                            "Sector schedules and source-linked assumptions need independent review."]}


def _report(request, draft, snapshot, gaps):
    chinese = request.report_language == "Chinese"
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
    text.extend(f"- {gap}" for gap in dict.fromkeys(gaps))
    text.append("\n## 来源 / Sources\n")
    text.extend(f"{number[sid]}. {source.title} — {source.url}"
                for sid, source in sources.items())
    return "\n".join(text) + "\n"


def run_research(request: ResearchRequest, services: ResearchServices) -> ResearchResult:
    """Run or resume research. Never implicitly instantiate providers or publishers."""
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
                required = {"reader_report.md", "audit_report.md", "evidence.json", "research.json",
                            "valuation_inputs.json", "valuation_results.json", "quality.json",
                            "run_metadata.json"}
                if request.dossier_dir:
                    required.add("dossier.json")
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
        tracker = BudgetTracker(request.budget,
                                previous_usage=Usage.model_validate(previous.get("usage", {})),
                                elapsed_seconds=previous.get("elapsed_seconds", 0))
        if previous.get("dispatched"):
            tracker.record(Usage(complete=False))
        usage_by_stage = dict(previous.get("by_stage", {}))
        role_indices = defaultdict(int)
        outputs, reviews = {}, []
        gaps = ["V2 source coverage and company-specific model acceptance remain pending."]
        snapshot = EvidenceSnapshot(ticker=request.ticker, cutoff=request.cutoff)
        draft, valuation = None, {"status": "unavailable"}
        proposal = ValuationProposal(unsupported_inputs=("valuation has not completed",))
        stop_reason = "completed_needs_review"
        failure_type = None

        def save_resources(dispatched=False):
            store.save_stage("resources", {}, {"usage": tracker.usage.model_dump(mode="json"),
                                               "elapsed_seconds": tracker.elapsed_seconds,
                                               "dispatched": dispatched,
                                               "by_stage": usage_by_stage})

        def call(stage, role, data, schema, finalization=False):
            role_index = role_indices[role]
            role_indices[role] += 1
            payload = {**instruction(role, schema), "evidence": _prompt_evidence(snapshot),
                       "research": data, "cutoff": request.cutoff.isoformat(),
                       "mandate": request.mandate, "stage": stage, "role_call_index": role_index,
                       "valuation_months": request.valuation_months,
                       "return_months": request.return_months,
                       "language": request.report_language if role == "editor"
                       else request.internal_language}
            if role == "valuation":
                payload["financial_model_schema"] = TypeAdapter(FCFFModelInput).json_schema()
            cached = store.load_stage(stage, payload)
            if cached is not None:
                return schema.model_validate(cached)
            # UTF-8 bytes are a conservative input bound, plus an output envelope.
            envelope = len(canonical_json(payload)) + 16_000
            permit = tracker.reserve(envelope, finalization=finalization)
            save_resources(dispatched=True)
            try:
                reply = services.models.complete(role, {**payload,
                    "timeout_seconds": permit.timeout_seconds, "max_output_tokens": 16_000}, request)
                reply = ModelReply.model_validate(reply)
            except BaseException:
                tracker.cancel(permit, dispatched=True)
                save_resources()
                raise
            tracker.complete(permit, reply.usage)
            usage_by_stage.setdefault(stage, []).append({
                "role": role, "model": request.models[role].model,
                "effort": request.models[role].effort, **reply.usage.model_dump(mode="json")})
            save_resources()
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
            gaps.extend(snapshot.gaps)
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
            proposal = call("valuation", "valuation", outputs, ValuationProposal)
            valuation = _calculate(proposal, request, snapshot)
            unresolved = [item for output in outputs.values()
                          for item in output.get("followup_questions", [])]
            followup = getattr(services.evidence, "followup", None)
            for cycle in range(request.budget.followup_cycles):
                if not unresolved or followup is None:
                    break
                tracker.admit()
                followup_input = {"evidence_hash": digest(snapshot),
                                  "questions": list(dict.fromkeys(unresolved))}
                saved_followup = store.load_stage(f"followup-{cycle}", followup_input)
                if saved_followup is not None:
                    updated = EvidenceSnapshot.model_validate(saved_followup)
                else:
                    updated = followup(request, snapshot, tuple(dict.fromkeys(unresolved)))
                updated = validate_snapshot(updated, request)
                if updated.instrument != snapshot.instrument:
                    raise ValueError("follow-up changed instrument identity")
                for kind in ("sources", "facts", "events", "expectations"):
                    retained = {item.id: item for item in getattr(updated, kind)}
                    if any(retained.get(item.id) != item for item in getattr(snapshot, kind)):
                        raise ValueError("follow-up must preserve immutable prior evidence")
                store.save_stage(f"followup-{cycle}", followup_input,
                                 updated.model_dump(mode="json"))
                if digest(updated) == digest(snapshot):
                    break
                snapshot = EvidenceSnapshot.model_validate_json(updated.model_dump_json())
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
                proposal = call(f"valuation-{cycle}", "valuation", outputs, ValuationProposal)
                valuation = _calculate(proposal, request, snapshot)
                unresolved = list(dict.fromkeys([
                    *revision.followup_questions,
                    *(question for role in ("business", "accounting", "expectations", "management")
                      for question in outputs[role].get("followup_questions", []))]))
            gaps.extend(unresolved)
            challenge = call("reconcile_challenge", "challenger",
                             {"analyses": outputs, "valuation": valuation}, AnalysisOutput)
            outputs["reconciled_challenge"] = challenge.model_dump(mode="json")
            gaps.extend(challenge.unresolved_gaps)
            review = call("verify_claims", "verifier", outputs, VerificationOutput, True)
            reviews.extend(review.findings)
            claim_ids = {claim["id"] for output in outputs.values()
                         for claim in output.get("claims", [])}
            if (set(review.supported_claim_ids) | set(review.contradicted_claim_ids)) - claim_ids:
                raise ValueError("verifier invented claim identifiers")
            if set(review.supported_claim_ids) & set(review.contradicted_claim_ids):
                raise ValueError("verifier supplied conflicting claim decisions")
            missing = claim_ids - set(review.supported_claim_ids)
            gaps.extend(f"Unverified claim: {identifier}" for identifier in sorted(missing))
            draft = call("editor", "editor", {"analyses": outputs, "limitations": gaps,
                                                "valuation": valuation}, ReportDraft, True)
            if any(set(section.evidence_ids) - _known_ids(snapshot) for section in draft.sections):
                raise ValueError("draft invented evidence identifiers")
            eligible_facts = tuple(fact for fact in snapshot.facts if fact.id in _known_ids(snapshot))
            draft = draft.model_copy(update={"sections": tuple(
                section.model_copy(update={"text": render_references(
                    section.text, eligible_facts, request.report_language)}) for section in draft.sections)})
            final_review = call("verify_report", "verifier",
                                {"draft": draft.model_dump(mode="json"), "analyses": outputs,
                                 "valuation": valuation}, VerificationOutput, True)
            reviews.extend(final_review.findings)
            if not final_review.reviewed_report or any(
                item.severity == "critical" for item in final_review.findings
            ):
                draft = None
                stop_reason = "verification_failed"
                gaps.append("Reader draft withheld because final verification did not pass.")
        except Exception as exc:
            # Do not emit provider errors or arbitrary validation inputs into reports.
            stop_reason = str(exc) if isinstance(exc, BudgetExhausted) else "stage_failed"
            failure_type = type(exc).__name__
            gaps.append(f"Research stopped: {stop_reason}; inspect saved valid stages.")
            draft = None
        finally:
            save_resources()

        gaps.extend(item.message for item in reviews if item.severity in {"critical", "warning"})
        if draft:
            gaps.extend(draft.limitations)
        assessment = Assessment(status="needs_review", findings=tuple(reviews))
        reader = _report(request, draft, snapshot, gaps)
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
                "usage_measurement": "saved_response_counters" if getattr(services.models, "kind", None)
                == "replay" else "provider_reported",
                "report_language": request.report_language, "usage": tracker.usage,
                "usage_by_stage": usage_by_stage,
                "total_tokens": tracker.usage.total_tokens, "stop_reason": stop_reason,
                "failure_type": failure_type,
                "elapsed_seconds": tracker.elapsed_seconds, "production_accepted": False}),
        }
        artifacts["run_metadata.json"] = canonical_json({
            **parse_json(artifacts["run_metadata.json"]), "update": describe_update(prior, snapshot)})
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
                                assessment=assessment, usage=tracker.usage,
                                unresolved_gaps=tuple(dict.fromkeys(gaps)), stop_reason=stop_reason)
        if result.stop_reason == "completed_needs_review":
            store.save_stage("completed-result", {}, result.model_dump(mode="json"))
        atomic_write(store.directory / "result.json", canonical_json(result))
        return result
