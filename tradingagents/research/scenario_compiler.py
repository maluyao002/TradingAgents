"""Compile reviewed assumption packages into deterministic conditional FCFF cases.

This module only assembles and executes source-linked conditional models.  It does
not review assumptions, assign probabilities, produce an investment target, or
accept a forecast as economically valid.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .assumptions import (
    AssumptionEntry,
    AssumptionPackage,
    assumption_blockers,
    validate_assumption_package,
)
from .contracts import Contract, EvidenceSnapshot, ResearchRequest
from .engine import _calculate
from .stages import AssumptionSupport, ValuationProposal
from .storage import canonical_json, digest, parse_json
from .valuation import FCFFModelInput, ForecastPeriod, ValuationUnits

_OPENING_PATHS = (
    "current_revenue",
    "current_working_capital",
    "net_debt",
    "current_diluted_shares",
)
_PERIOD_PATHS = (
    "revenue_growth",
    "operating_margin",
    "tax_rate",
    "depreciation_amortization_pct_revenue",
    "capex_pct_revenue",
    "working_capital_pct_revenue",
    "sbc_pct_revenue",
)
_SCOPE = (
    "Source-linked conditional FCFF modeling only; not human review, fundamental "
    "forecast validation, an investment recommendation, probability, or price target."
)


class ConditionalScenario(Contract):
    """One explicit, bounded set of conditional FCFF assumptions."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:/-]+$")
    thesis: str
    periods: tuple[ForecastPeriod, ...] = Field(min_length=1, max_length=50)
    discount_rate: Decimal
    terminal_growth: Decimal
    limitations: tuple[str, ...] = ()

    @field_validator("thesis")
    @classmethod
    def nonblank_thesis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scenario thesis must be nonblank")
        return value

    @field_validator("limitations")
    @classmethod
    def valid_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("scenario limitations must be nonblank")
        if len(values) != len(set(values)):
            raise ValueError("scenario limitations must be unique")
        return values


def _jsonable(value: Any) -> Any:
    """Return the canonical artifact representation (dates/Decimals become strings)."""

    return parse_json(canonical_json(value))


def _entry_map(package: AssumptionPackage) -> dict[str, AssumptionEntry]:
    return {entry.model_path: entry for entry in package.entries}


def _positive_decimal(entry: AssumptionEntry) -> Decimal:
    if entry.value is None:
        raise ValueError(f"missing convention value: {entry.model_path}")
    try:
        value = Decimal(entry.value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid scale convention: {entry.model_path}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"invalid scale convention: {entry.model_path}")
    return value


def _range(entry: AssumptionEntry):
    if entry.range is None:
        raise ValueError(f"missing reviewed assumption range: {entry.model_path}")
    return entry.range


def _check_in_range(value: Decimal, entry: AssumptionEntry, case_id: str) -> None:
    bounded = _range(entry)
    if not bounded.low <= value <= bounded.high:
        raise ValueError(
            f"scenario {case_id} is outside the reviewed range: {entry.model_path}"
        )


def _fallback_convention_evidence(
    entries: dict[str, AssumptionEntry],
) -> tuple[str, ...]:
    identifiers: list[str] = []
    for path in _OPENING_PATHS:
        for identifier in entries[path].evidence_ids:
            if identifier not in identifiers:
                identifiers.append(identifier)
    if not identifiers:
        raise ValueError("modeling conventions require eligible historical evidence IDs")
    return tuple(identifiers)


def _support(
    entry: AssumptionEntry,
    *,
    kind: Literal["reported", "assumption"],
    fallback_evidence: tuple[str, ...] = (),
    convention: bool = False,
) -> AssumptionSupport:
    evidence_ids = entry.evidence_ids or fallback_evidence
    if not evidence_ids:
        raise ValueError(f"assumption support lacks evidence IDs: {entry.model_path}")
    rationale = entry.rationale
    if convention:
        rationale = f"Modeling convention: {rationale}"
    return AssumptionSupport(
        rationale=rationale,
        evidence_ids=evidence_ids,
        kind=kind,
    )


def _build_model(
    scenario: ConditionalScenario,
    entries: dict[str, AssumptionEntry],
    request: ResearchRequest,
) -> FCFFModelInput:
    amount_scale = _positive_decimal(entries["units.amount_scale"])
    share_scale = _positive_decimal(entries["units.share_scale"])
    for path, value in (
        ("discount_rate", scenario.discount_rate),
        ("terminal_growth", scenario.terminal_growth),
    ):
        _check_in_range(value, entries[path], scenario.id)

    expected_margin_basis = entries["periods.*.operating_margin_basis"].value
    expected_funding = entries["periods.*.external_funding_required"].value == "true"
    for period in scenario.periods:
        for field_name in _PERIOD_PATHS:
            _check_in_range(
                getattr(period, field_name), entries[f"periods.*.{field_name}"], scenario.id
            )
        if period.operating_margin_basis != expected_margin_basis:
            raise ValueError(
                f"scenario {scenario.id} operating_margin_basis does not match package convention"
            )
        if period.external_funding_required != expected_funding:
            raise ValueError(
                f"scenario {scenario.id} external_funding_required does not match package convention"
            )

    opening: dict[str, Decimal] = {}
    for path in _OPENING_PATHS:
        scale = share_scale if path == "current_diluted_shares" else amount_scale
        opening[path] = _range(entries[path]).base / scale

    currency = entries["units.currency"].value
    if currency is None:
        raise ValueError("missing convention value: units.currency")
    return FCFFModelInput(
        as_of_date=request.cutoff.astimezone(ZoneInfo(request.timezone)).date(),
        current_revenue=opening["current_revenue"],
        current_working_capital=opening["current_working_capital"],
        periods=scenario.periods,
        discount_rate=scenario.discount_rate,
        terminal_growth=scenario.terminal_growth,
        net_debt=opening["net_debt"],
        current_diluted_shares=opening["current_diluted_shares"],
        units=ValuationUnits(
            currency=currency,
            amount_scale=amount_scale,
            share_scale=share_scale,
        ),
        funding_caveats=scenario.limitations,
    )


def _proposal(
    model: FCFFModelInput,
    scenario: ConditionalScenario,
    package: AssumptionPackage,
    snapshot: EvidenceSnapshot,
    entries: dict[str, AssumptionEntry],
) -> ValuationProposal:
    convention_evidence = _fallback_convention_evidence(entries)
    supports: dict[str, AssumptionSupport] = {}
    for path in _OPENING_PATHS:
        supports[path] = _support(entries[path], kind="reported")
    for path in ("discount_rate", "terminal_growth"):
        supports[path] = _support(entries[path], kind="assumption")
    for path in ("units.currency", "units.amount_scale", "units.share_scale"):
        supports[path] = _support(
            entries[path],
            kind="assumption",
            fallback_evidence=convention_evidence,
            convention=True,
        )

    schedule = entries["forecast_schedule"]
    schedule_evidence = schedule.evidence_ids or convention_evidence
    for index, _period in enumerate(model.periods):
        for field_name in _PERIOD_PATHS:
            supports[f"periods.{index}.{field_name}"] = _support(
                entries[f"periods.*.{field_name}"], kind="assumption"
            )
        for field_name, wildcard in (
            ("operating_margin_basis", "periods.*.operating_margin_basis"),
            ("external_funding_required", "periods.*.external_funding_required"),
        ):
            supports[f"periods.{index}.{field_name}"] = _support(
                entries[wildcard],
                kind="assumption",
                fallback_evidence=convention_evidence,
                convention=True,
            )
        for field_name in ("period_start", "period_end"):
            supports[f"periods.{index}.{field_name}"] = AssumptionSupport(
                rationale=(
                    "Modeling convention: explicit future forecast date supplied by the "
                    f"conditional scenario and validated against forecast_schedule. {schedule.rationale}"
                ),
                evidence_ids=schedule_evidence,
                kind="assumption",
            )

    fact_by_id = {fact.id: fact for fact in snapshot.facts}
    bases = {
        fact_by_id[entries[path].fact_id].basis
        for path in _OPENING_PATHS
        if entries[path].fact_id in fact_by_id
    }
    if len(bases) != 1:
        raise ValueError("opening historical anchors lack one accounting basis")

    evidence_ids: list[str] = []
    for support in supports.values():
        for identifier in support.evidence_ids:
            if identifier not in evidence_ids:
                evidence_ids.append(identifier)
    scope_limitations = tuple(dict.fromkeys((*package.limitations, *scenario.limitations, _SCOPE)))
    return ValuationProposal(
        model=asdict(model),
        accounting_basis=next(iter(bases)),  # type: ignore[arg-type]
        assumption_rationale={path: support.rationale for path, support in supports.items()},
        assumptions=supports,
        evidence_ids=tuple(evidence_ids),
        scope_limitations=scope_limitations,
    )


def _decomposition(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "explicit_period_present_value",
        "terminal_value_at_horizon",
        "terminal_value_present_value",
        "enterprise_value",
        "net_debt",
        "equity_value",
        "value_per_current_diluted_share",
        "terminal_value_share_of_enterprise_value",
    )
    return {key: result[key] for key in keys}


REVIEWED_PACKET_FILES = frozenset({
    "request.json", "evidence.json", "assumptions.json", "scenarios.json",
    "economic_audit.json", "market_inputs.json", "calibration.json",
})


def apply_conditional_review(package, scenarios, review, manifest):
    """Bind an actual automated review record; never infer clearance from a hash string."""
    hashes = manifest.get("artifact_hashes") if isinstance(manifest, dict) else None
    if not isinstance(hashes, dict) or set(hashes) != REVIEWED_PACKET_FILES:
        raise ValueError("review requires the complete prepared packet inventory")
    if not isinstance(review, dict) or (
        review.get("decision") != "conditional_modeling_cleared"
        or review.get("reviewer_kind") != "automated_agent"
        or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
        or review.get("prerequisites") != []
        or "reviewed_at" not in review
        or review.get("manifest_sha256") != digest(manifest)
        or review.get("assumptions_sha256") != digest(package)
        or review.get("scenarios_sha256") != digest(scenarios)
        or hashes["assumptions.json"] != digest(package)
        or hashes["scenarios.json"] != digest(scenarios)
        or hashes["evidence.json"] != package.evidence_sha256
    ):
        raise ValueError("review is incomplete, conditional, or not bound to this packet")
    limitations = review.get("limitations")
    if (not isinstance(limitations, list) or not limitations
            or any(not isinstance(item, str) or not item.strip() for item in limitations)):
        raise ValueError("review clearance limitations must be explicit")
    return AssumptionPackage.model_validate({
        **package.model_dump(),
        "entries": [entry.model_copy(update={"status": "reviewed"}) for entry in package.entries],
        "reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
        "limitations": tuple(dict.fromkeys((*package.limitations, *limitations))),
    })


def compile_scenarios(
    package: AssumptionPackage,
    snapshot: EvidenceSnapshot,
    request: ResearchRequest,
    evidence_bytes: bytes,
    scenarios: tuple[ConditionalScenario, ...],
) -> dict[str, Any]:
    """Compile one to three reviewed conditional cases and calculate them offline."""

    if request.valuation_method != "fcff":
        raise ValueError("conditional scenario compilation supports FCFF only")
    if not isinstance(scenarios, tuple):
        raise ValueError("scenarios must be supplied as a tuple")
    if not 1 <= len(scenarios) <= 3:
        raise ValueError("scenarios must contain 1 to 3 cases")
    if not all(isinstance(scenario, ConditionalScenario) for scenario in scenarios):
        raise ValueError("scenarios must contain ConditionalScenario contracts")
    identifiers = [scenario.id for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("scenario IDs must be unique")

    package = validate_assumption_package(package, snapshot, request, evidence_bytes)
    blockers = assumption_blockers(package)
    if blockers:
        raise ValueError("assumption package is not compilation-ready: " + "; ".join(blockers))
    entries = _entry_map(package)

    cases: list[dict[str, Any]] = []
    for scenario in scenarios:
        model = _build_model(scenario, entries, request)
        proposal = _proposal(model, scenario, package, snapshot, entries)
        calculation = _calculate(proposal, request, snapshot)
        if calculation.get("status") != "illustrative":
            limitations = calculation.get("limitations", ())
            raise ValueError(
                f"scenario {scenario.id} was refused by the deterministic calculator: "
                + "; ".join(str(item) for item in limitations)
            )
        result = calculation["result"]
        typed_input = asdict(model)
        cases.append(
            {
                "id": scenario.id,
                "thesis": scenario.thesis,
                "limitations": list(scenario.limitations),
                "typed_input": _jsonable(typed_input),
                "proposal": _jsonable(proposal),
                "result": _jsonable(result),
                "hashes": {
                    "scenario_sha256": digest(scenario),
                    "typed_input_sha256": digest(typed_input),
                    "proposal_sha256": digest(proposal),
                    "result_sha256": digest(result),
                },
                "opening_input_bindings": _jsonable(
                    calculation.get("opening_input_bindings", {})
                ),
                "decomposition": _jsonable(_decomposition(result)),
            }
        )

    return {
        "status": "conditional_illustrative",
        "valuation_method": "fcff",
        "scope": _SCOPE,
        "evidence_sha256": package.evidence_sha256,
        "assumption_package_sha256": digest(package),
        "review": {
            "reviewer": package.reviewer,
            "reviewed_at": package.reviewed_at.isoformat() if package.reviewed_at else None,
        },
        "cases": cases,
    }
