"""Offline boundary regressions for Stage 3 financial-case ingestion."""

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.case_context import (
    CaseSourcePassage,
    FinancialCaseReview,
    load_case_context,
    source_passages_sha256,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    ReviewFinding,
    SourceDocument,
)
from tradingagents.research.financial_case import (
    CommitmentSchedule,
    ConclusionAssessment,
    FinancialCase,
    FinancialConvention,
    ReconciliationSchedule,
    ScheduleLine,
    evidence_snapshot_sha256,
)
from tradingagents.research.storage import canonical_json, digest, parse_json

CUTOFF = "2026-09-18T12:00:00Z"
OPENING_DATE = "2026-09-18"


def _source(content: str | None = None) -> SourceDocument:
    if content is None:
        content = (
            "Issuer filing\n"
            "row receivables: receivables were 12 USD millions.\n"
            "row cash: cash was 10 USD millions.\n"
            "row debt: debt was 3 USD millions.\n"
            "row shares: diluted shares were 24 millions.\n"
        )
    return SourceDocument(
        id="filing",
        url="https://example.test/filing",
        title="Issuer filing",
        publisher="Issuer",
        retrieved_at="2026-09-17T10:00:00Z",
        published_at="2026-09-16T10:00:00Z",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        kind="filing",
    )


def _fact(
    fact_id: str,
    value: str,
    *,
    unit: str = "USD",
    currency: str | None = "USD",
) -> FinancialFact:
    return FinancialFact(
        id=fact_id,
        source_id="filing",
        metric=fact_id,
        value=value,
        scale="1000000",
        unit=unit,
        currency=currency,
        period_end=OPENING_DATE,
        basis="US GAAP",
        location=f"row {fact_id}",
    )


def _snapshot(content: str | None = None) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        ticker="NVDA",
        cutoff=CUTOFF,
        sources=(_source(content),),
        facts=(
            _fact("receivables", "12"),
            _fact("cash", "10"),
            _fact("debt", "3"),
            _fact("shares", "24", unit="shares", currency=None),
        ),
    )


def _line(fact: FinancialFact, classification: str) -> ScheduleLine:
    return ScheduleLine(
        id=f"{fact.id}-line",
        fact_id=fact.id,
        classification=classification,
        effect="add",
        normalized_value=fact.normalized_value,
        unit=fact.unit,
        currency=fact.currency,
        period_end=fact.period_end,
        convention_ids=(f"{fact.id}-policy",),
    )


def _case(snapshot: EvidenceSnapshot) -> FinancialCase:
    facts = {fact.id: fact for fact in snapshot.facts}
    lines = (
        _line(facts["receivables"], "operating_current_asset"),
        _line(facts["cash"], "available_cash"),
        _line(facts["debt"], "borrowed_debt"),
        _line(facts["shares"], "diluted_shares"),
    )
    schedules = (
        ReconciliationSchedule(
            id="working-capital-schedule",
            kind="operating_working_capital",
            status="complete",
            rationale="Selected operating working capital.",
            components=(lines[0],),
        ),
        ReconciliationSchedule(
            id="cash-schedule",
            kind="cash_and_securities",
            status="complete",
            rationale="Selected available cash.",
            components=(lines[1],),
        ),
        ReconciliationSchedule(
            id="debt-schedule",
            kind="debt_and_leases",
            status="complete",
            rationale="Selected borrowed debt.",
            components=(lines[2],),
        ),
        ReconciliationSchedule(
            id="share-schedule",
            kind="shares",
            status="complete",
            rationale="Selected point-in-time diluted shares.",
            components=(lines[3],),
            share_basis="point_in_time_diluted",
        ),
    )
    return FinancialCase(
        ticker="NVDA",
        cutoff=CUTOFF,
        timezone="UTC",
        snapshot_sha256=evidence_snapshot_sha256(snapshot),
        opening_date=OPENING_DATE,
        schedules=schedules,
        commitments=CommitmentSchedule(
            id="commitment-schedule",
            status="complete",
            rationale="No selected commitment rows.",
        ),
        conventions=tuple(
            FinancialConvention(
                id=f"{fact.id}-policy",
                kind="convention",
                description=f"Classification policy for {fact.id}.",
                affected_component_ids=(f"{fact.id}-line",),
            )
            for fact in snapshot.facts
        ),
        assessments=(
            ConclusionAssessment(
                id="equity-assessment",
                output="equity_bridge",
                status="not_assessed",
                rationale="Equity bridge remains unassessed.",
            ),
            ConclusionAssessment(
                id="funding-assessment",
                output="funding",
                status="not_assessed",
                rationale="Funding remains unassessed.",
            ),
        ),
    )


def _request(tmp_path, **updates) -> ResearchRequest:
    values = {
        "ticker": "NVDA",
        "cutoff": CUTOFF,
        "timezone": "UTC",
        "backend": "api",
        "output_dir": tmp_path / "output",
        "quality_revision": "evidence-led-bounded",
        "valuation_method": "fcff",
        "financial_case_path": tmp_path / "case.json",
    }
    values.update(updates)
    return ResearchRequest(**values)


def _review(
    case: FinancialCase,
    snapshot: EvidenceSnapshot,
    *,
    status="reviewed",
    supplemental: tuple[CaseSourcePassage, ...] = (),
):
    return FinancialCaseReview(
        status=status,
        reviewer_id="independent-reviewer",
        case_sha256=digest(case.model_dump(mode="json")),
        snapshot_sha256=evidence_snapshot_sha256(snapshot),
        source_passages_sha256=(source_passages_sha256(supplemental) if supplemental else None),
        findings=(
            ReviewFinding(
                code="OPEN-BLOCKER",
                severity="critical",
                message="Forecast linkage is absent.",
            ),
        ),
        limitations=("Review does not approve valuation output.",),
    )


def _content(
    case: FinancialCase,
    review: FinancialCaseReview | None = None,
    *,
    source_passages: tuple[CaseSourcePassage, ...] = (),
    **extra,
) -> bytes:
    value = {"case": case.model_dump(mode="json"), **extra}
    if review is not None:
        value["review"] = review.model_dump(mode="json")
    if source_passages:
        value["source_passages"] = [item.model_dump(mode="json") for item in source_passages]
    return canonical_json(value)


def _authored_passage(snapshot: EvidenceSnapshot) -> CaseSourcePassage:
    source = snapshot.sources[0]
    return CaseSourcePassage(
        source_id=source.id,
        source_sha256=source.content_sha256,
        start=0,
        end=len(source.content),
        text=source.content,
        fact_ids=tuple(fact.id for fact in snapshot.facts),
        location_hints=(
            "header: issuer filing; unit: USD millions and shares millions; "
            "observation date: 2026-09-18",
        ),
        selection_basis="authored_exact",
    )


def test_draft_ingestion_recomputes_case_and_blocks_all_model_outputs(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    context = load_case_context(_content(case), _request(tmp_path), snapshot)

    assert context.reviewed is False
    assert context.reconciliation.output_eligibility.operating_asset_value.status == "conditional"
    assert {
        component.status
        for component in (
            context.scope.operating_asset_value,
            context.scope.equity_per_share_value,
            context.scope.funding_assessment,
            context.scope.opening_date_alignment,
        )
    } == {"blocked"}
    assert "not bound to reviewed forecast assumptions" in context.limitations[0]
    assert any("unreviewed Stage 2 draft" in item for item in context.limitations)

    model_context = context.model_context()
    assert model_context["review_status"] == "draft_unreviewed"
    assert model_context["reviewed"] is False
    assert model_context["financial_case_review"] is None
    assert "forecasts" not in model_context
    assert "enterprise_value" not in model_context
    assert "equity_value" not in model_context
    assert set(context.artifacts) == {
        "financial_case.json",
        "financial_reconciliation.json",
        "case_context.json",
    }
    assert parse_json(context.artifacts["financial_case.json"]) == case.model_dump(mode="json")
    assert parse_json(context.artifacts["case_context.json"]) == model_context


def test_review_is_exactly_bound_but_cannot_clear_deterministic_blockers(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    review = _review(case, snapshot)
    context = load_case_context(_content(case, review), _request(tmp_path), snapshot)

    assert context.reviewed is True
    assert context.model_context()["review_status"] == "reviewed"
    assert context.model_context()["financial_case_review"] == review.model_dump(mode="json")
    assert "financial_case_review.json" in context.artifacts
    assert all(
        getattr(context.scope, name).status == "blocked"
        for name in (
            "operating_asset_value",
            "equity_per_share_value",
            "funding_assessment",
            "opening_date_alignment",
        )
    )
    assert any("OPEN-BLOCKER" in item for item in context.limitations)


def test_explicit_draft_review_never_masquerades_as_reviewed(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    context = load_case_context(
        _content(case, _review(case, snapshot, status="draft")),
        _request(tmp_path),
        snapshot,
    )

    assert context.reviewed is False
    assert context.model_context()["review_status"] == "draft_unreviewed"
    assert any("unreviewed Stage 2 draft" in item for item in context.limitations)


def test_selected_facts_keep_exact_bounded_source_text_and_provenance(tmp_path):
    content = "x" * 7_000 + "row cash: exact cash disclosure" + "y" * 7_000
    snapshot = _snapshot(content)
    context = load_case_context(_content(_case(snapshot)), _request(tmp_path), snapshot)

    material = context.source_material[0]
    assert material.content_sha256 == snapshot.sources[0].content_sha256
    assert sum(len(item.text) for item in material.passages) <= 6_000
    assert any("row cash: exact cash disclosure" in item.text for item in material.passages)
    for passage in material.passages:
        assert snapshot.sources[0].content[passage.start : passage.end] == passage.text
        assert passage.source_sha256 == snapshot.sources[0].content_sha256
        assert "verified" not in passage.selection_basis


def test_empty_eligible_source_is_disclosed_without_invented_passage_text(tmp_path):
    snapshot = _snapshot("")
    context = load_case_context(_content(_case(snapshot)), _request(tmp_path), snapshot)

    assert context.source_material[0].passages == ()
    assert any("No source text is available" in item for item in context.limitations)


def test_authored_exact_passages_are_review_bound_and_replace_auto_fallback(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    passages = (_authored_passage(snapshot),)
    review = _review(case, snapshot, supplemental=passages)
    context = load_case_context(
        _content(case, review, source_passages=passages), _request(tmp_path), snapshot
    )

    assert context.source_passages == passages
    assert context.source_material[0].passages == passages
    supplemental = context.model_context()["supplemental_source_material"]
    assert supplemental["classification"] == "exact_selected_text_not_semantic_verification"
    assert supplemental["source_passages_sha256"] == source_passages_sha256(passages)
    assert supplemental["passages"] == [passages[0].model_dump(mode="json")]


def test_supplemental_passages_require_same_source_selected_facts_and_bounded_text(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    passage = _authored_passage(snapshot)
    unknown_fact = passage.model_copy(update={"fact_ids": ("not-selected",)})
    with pytest.raises(ValueError, match="selected from the same source"):
        load_case_context(
            _content(case, source_passages=(unknown_fact,)), _request(tmp_path), snapshot
        )

    oversized_text = "x" * 100_001
    oversized_source = _snapshot(oversized_text)
    oversized = CaseSourcePassage(
        source_id="filing",
        source_sha256=oversized_source.sources[0].content_sha256,
        start=0,
        end=len(oversized_text),
        text=oversized_text,
        fact_ids=("cash",),
        location_hints=("bounded allowance test",),
        selection_basis="authored_exact",
    )
    with pytest.raises(ValueError, match="bounded text allowance"):
        load_case_context(
            _content(_case(oversized_source), source_passages=(oversized,)),
            _request(tmp_path),
            oversized_source,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "span",
        "date",
        "header",
        "body",
    ],
)
def test_tampered_supplemental_material_invalidates_source_or_review_hash(tmp_path, mutation):
    snapshot = _snapshot()
    case = _case(snapshot)
    original = _authored_passage(snapshot)
    review = _review(case, snapshot, supplemental=(original,))
    if mutation == "span":
        changed = original.model_copy(update={"start": 1})
        message = "span differs from exact text"
    elif mutation == "body":
        changed = original.model_copy(update={"text": original.text.replace("cash", "CASH", 1)})
        message = "differs from eligible frozen text"
    else:
        hint = original.location_hints[0]
        replacement = (
            hint.replace("2026-09-18", "2026-09-17")
            if mutation == "date"
            else hint.replace("issuer filing", "altered header")
        )
        changed = original.model_copy(update={"location_hints": (replacement,)})
        message = "supplemental source-passages hash mismatch"

    with pytest.raises((ValueError, ValidationError), match=message):
        load_case_context(
            _content(case, review, source_passages=(changed,)),
            _request(tmp_path),
            snapshot,
        )


def test_review_cannot_omit_or_reuse_supplemental_material_hash(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    passage = _authored_passage(snapshot)
    review_without_hash = _review(case, snapshot)
    with pytest.raises(ValueError, match="supplemental source-passages hash mismatch"):
        load_case_context(
            _content(case, review_without_hash, source_passages=(passage,)),
            _request(tmp_path),
            snapshot,
        )

    review_for_passage = _review(case, snapshot, supplemental=(passage,))
    with pytest.raises(ValueError, match="supplemental source-passages hash mismatch"):
        load_case_context(_content(case, review_for_passage), _request(tmp_path), snapshot)


@pytest.mark.parametrize("field", ["case_sha256", "snapshot_sha256"])
def test_review_rejects_any_exact_hash_mismatch(tmp_path, field):
    snapshot = _snapshot()
    case = _case(snapshot)
    review = _review(case, snapshot).model_copy(update={field: "0" * 64})

    with pytest.raises(ValueError, match="review hash mismatch"):
        load_case_context(_content(case, review), _request(tmp_path), snapshot)


@pytest.mark.parametrize(
    ("request_update", "message"),
    [
        ({"ticker": "AMD"}, "frozen snapshot identity"),
        (
            {"cutoff": datetime.fromisoformat("2026-09-17T12:00:00+00:00")},
            "frozen snapshot identity",
        ),
        ({"timezone": "America/Los_Angeles"}, "ticker, cutoff, or timezone"),
    ],
)
def test_request_case_snapshot_identity_mismatches_are_rejected(tmp_path, request_update, message):
    snapshot = _snapshot()
    case = _case(snapshot)
    request = _request(tmp_path).model_copy(update=request_update)

    with pytest.raises(ValueError, match=message):
        load_case_context(_content(case), request, snapshot)


def test_mutated_or_forged_copied_snapshots_are_rejected(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    changed = snapshot.model_copy(update={"gaps": ("new source followup",)})
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        load_case_context(_content(case), _request(tmp_path), changed)

    source = snapshot.sources[0]
    forged_source = source.model_copy(update={"content": "changed without a new hash"})
    forged = snapshot.model_copy(update={"sources": (forged_source,)})
    with pytest.raises(ValidationError, match="source content hash mismatch"):
        load_case_context(_content(case), _request(tmp_path), forged)


def test_forged_case_copy_is_revalidated_against_frozen_facts(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    first = case.schedules[0].components[0]
    forged_line = first.model_copy(update={"normalized_value": Decimal(1)})
    forged_schedule = case.schedules[0].model_copy(update={"components": (forged_line,)})
    forged_case = case.model_copy(update={"schedules": (forged_schedule, *case.schedules[1:])})

    with pytest.raises(ValueError, match="normalized fact fields differ"):
        load_case_context(_content(forged_case), _request(tmp_path), snapshot)


@pytest.mark.parametrize(
    "payload",
    [
        {"case": None, "financial_reconciliation": {}},
        {"case": None, "unknown": True},
    ],
)
def test_envelope_rejects_serialized_reconciliation_and_unknown_fields(tmp_path, payload):
    snapshot = _snapshot()
    payload["case"] = _case(snapshot).model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_case_context(canonical_json(payload), _request(tmp_path), snapshot)


def test_nested_case_and_review_unknown_fields_are_rejected(tmp_path):
    snapshot = _snapshot()
    case = _case(snapshot)
    case_data = case.model_dump(mode="json")
    case_data["serialized_reconciliation"] = {}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_case_context(canonical_json({"case": case_data}), _request(tmp_path), snapshot)

    review_data = _review(case, snapshot).model_dump(mode="json")
    review_data["approved"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_case_context(
            canonical_json({"case": case.model_dump(mode="json"), "review": review_data}),
            _request(tmp_path),
            snapshot,
        )


def test_malformed_duplicate_json_and_non_bytes_are_rejected(tmp_path):
    snapshot = _snapshot()
    request = _request(tmp_path)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_case_context(b'{"case":{},"case":{}}', request, snapshot)
    with pytest.raises(TypeError, match="must be bytes"):
        load_case_context(bytearray(b"{}"), request, snapshot)  # type: ignore[arg-type]


def test_request_must_be_explicit_bounded_fcff_opt_in(tmp_path):
    snapshot = _snapshot()
    content = _content(_case(snapshot))
    missing_path = _request(tmp_path).model_copy(update={"financial_case_path": None})
    with pytest.raises(ValueError, match="opted-in request path"):
        load_case_context(content, missing_path, snapshot)

    wrong_revision = _request(tmp_path).model_copy(update={"quality_revision": "evidence-led"})
    with pytest.raises(ValidationError, match="bounded evidence-led FCFF"):
        load_case_context(content, wrong_revision, snapshot)

    wrong_method = _request(tmp_path).model_copy(update={"valuation_method": "equity_fcfe"})
    with pytest.raises(ValidationError, match="bounded evidence-led FCFF"):
        load_case_context(content, wrong_method, snapshot)
