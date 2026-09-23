"""Dated operating-income-to-cash-flow bridges with explicit analyst assumptions.

The boundary is deliberately narrower than valuation.  It combines reported,
frozen historical anchors with a reviewed conditional operating scenario and
explicit tax, D&A, capex, working-capital, and commitment-overlap assumptions.
It never infers equity value, funding adequacy, or economic approval.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_serializer, model_validator

from .calculated_values import CalculatedValue
from .contracts import Contract, EvidenceSnapshot, FinancialFact, Identifier, ReviewFinding
from .financial_case import FinancialCase, evidence_snapshot_sha256, reconcile_financial_case
from .operating_scenarios import OperatingScenarioResult
from .sources import is_exact_sec_archive_filing_url
from .storage import canonical_json, digest

_MAX_AMOUNT = Decimal("1e50")
_CALCULATION_CLASSIFICATION = "conditional_analyst_calculation_not_economic_approval"
_SCOPE_LIMITATION = (
    "This bridge is a conditional operating cash-flow calculation, not an accepted forecast, "
    "valuation, equity bridge, funding assessment, investment recommendation, or economic approval."
)
_UNREVIEWED_LIMITATION = (
    "The cash-flow assumptions and commitment-overlap judgments have not received an independent "
    "hash-bound review; numerical outputs remain explicitly conditional analyst calculations."
)


class WorkingCapitalAnchorComponent(Contract):
    """One reported beginning/end balance used in a deliberately bounded OWC proxy."""

    id: Identifier
    metric: str = Field(min_length=1)
    current_fact_id: Identifier
    prior_fact_id: Identifier
    effect: Literal["asset", "liability"]
    rationale: str = Field(min_length=1)

    @field_validator("rationale")
    @classmethod
    def nonblank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("working-capital rationale cannot be blank")
        return value


class ReportedCashFlowAnchor(Contract):
    """Selectors for reported duration facts and balance-sheet movements."""

    fiscal_label: str = Field(min_length=1, max_length=80)
    period_start: date
    period_end: date
    operating_income_fact_id: Identifier
    income_before_tax_fact_id: Identifier
    income_tax_expense_fact_id: Identifier
    depreciation_amortization_fact_id: Identifier
    capex_cashflow_fact_id: Identifier
    operating_cash_flow_fact_id: Identifier
    working_capital_components: tuple[WorkingCapitalAnchorComponent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_anchor(self):
        if self.period_start > self.period_end:
            raise ValueError("reported cash-flow anchor ends before it starts")
        identifiers = [item.id for item in self.working_capital_components]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("working-capital component identifiers must be unique")
        fact_ids = [
            fact_id
            for item in self.working_capital_components
            for fact_id in (item.current_fact_id, item.prior_fact_id)
        ]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("working-capital facts cannot be reused across components")
        return self


_RECONCILIATION_METRICS = {
    "net_income": "net_income",
    "stock_compensation": "stock_based_compensation",
    "deferred_tax": "deferred_tax",
    "equity_gains": "equity_gains",
    "other_adjustment": "other_adjustment",
    "receivables_movement": "receivables_movement",
    "inventory_movement": "inventory_movement",
    "prepaid_other_assets_movement": "prepaid_other_assets_movement",
    "payables_movement": "payables_movement",
    "accrued_current_liabilities_movement": "accrued_current_liabilities_movement",
    "other_long_term_liabilities_movement": "other_long_term_liabilities_movement",
}
_NONCASH_ROLES = (
    "stock_compensation", "deferred_tax", "equity_gains", "other_adjustment"
)
_MOVEMENT_ROLES = (
    "receivables_movement", "inventory_movement", "prepaid_other_assets_movement",
    "payables_movement", "accrued_current_liabilities_movement",
    "other_long_term_liabilities_movement",
)


class HistoricalCashFlowRowSelector(Contract):
    """One signed, reported source-statement row selected for exact reconciliation."""

    role: Literal[
        "net_income", "stock_compensation", "deferred_tax", "equity_gains",
        "other_adjustment", "receivables_movement", "inventory_movement",
        "prepaid_other_assets_movement", "payables_movement",
        "accrued_current_liabilities_movement", "other_long_term_liabilities_movement",
    ]
    fact_id: Identifier
    sign: Literal["positive", "negative", "zero"]


class HistoricalCashFlowReconciliationSelector(Contract):
    """Complete H1 CFO rows, plus issuer FCF's distinct asset-principal row."""

    anchor_source_id: Identifier
    cashflow_statement_source_id: Identifier
    rows: tuple[HistoricalCashFlowRowSelector, ...] = Field(min_length=11, max_length=11)
    asset_principal_cashflow_fact_id: Identifier
    issuer_free_cash_flow_fact_id: Identifier

    @model_validator(mode="after")
    def complete_unique_roles(self):
        roles = [row.role for row in self.rows]
        fact_ids = [row.fact_id for row in self.rows]
        if set(roles) != set(_RECONCILIATION_METRICS) or len(roles) != len(set(roles)):
            raise ValueError("historical reconciliation requires each source row role exactly once")
        all_ids = [*fact_ids, self.asset_principal_cashflow_fact_id,
                   self.issuer_free_cash_flow_fact_id]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("historical reconciliation facts cannot be reused across roles")
        return self


class AnalystCashFlowAssumption(Contract):
    """One explicit numeric judgment; evidence is an anchor, not factual support for the value."""

    value: Decimal
    unit: Literal["fraction", "USD"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    classification: Literal["conditional_analyst_assumption"] = "conditional_analyst_assumption"

    @model_validator(mode="after")
    def finite_supported_value(self):
        if not self.value.is_finite() or self.value.copy_abs() > _MAX_AMOUNT:
            raise ValueError("cash-flow assumption is outside the bounded numeric domain")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("cash-flow assumption evidence IDs must be unique")
        if not self.rationale.strip():
            raise ValueError("cash-flow assumption rationale cannot be blank")
        return self


class PeriodCashFlowAssumptions(Contract):
    """Cash-flow assumptions for one exact operating-scenario fiscal period."""

    period_id: Identifier
    period_start: date
    period_end: date
    tax_rate: AnalystCashFlowAssumption
    depreciation_amortization: AnalystCashFlowAssumption
    capex: AnalystCashFlowAssumption
    change_in_operating_working_capital: AnalystCashFlowAssumption

    @model_validator(mode="after")
    def coherent_period(self):
        if self.period_start > self.period_end:
            raise ValueError("cash-flow assumption period ends before it starts")
        if self.tax_rate.unit != "fraction" or not Decimal(0) <= self.tax_rate.value <= Decimal(1):
            raise ValueError("tax_rate must be a fraction between zero and one")
        for name in ("depreciation_amortization", "capex"):
            assumption = getattr(self, name)
            if assumption.unit != "USD" or assumption.value < 0:
                raise ValueError(f"{name} must be nonnegative USD")
        if self.change_in_operating_working_capital.unit != "USD":
            raise ValueError("change in operating working capital must be USD")
        return self


class CashFlowScenarioAssumptions(Contract):
    id: Identifier
    periods: tuple[PeriodCashFlowAssumptions, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_contiguous_periods(self):
        identifiers = [period.period_id for period in self.periods]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("cash-flow period identifiers must be unique")
        for previous, current in zip(self.periods, self.periods[1:], strict=False):
            if current.period_start != previous.period_end + timedelta(days=1):
                raise ValueError("cash-flow assumption periods must be contiguous")
        return self


class CommitmentOverlapAssumption(Contract):
    """Analyst treatment of one disclosed commitment within the bridge horizon."""

    commitment_id: Identifier
    treatment: Literal[
        "assumed_already_reflected",
        "incremental_deduction",
        "excluded_non_operating",
        "zero_disclosed",
        "unresolved_not_deducted",
    ]
    overlaps: tuple[Literal["operating_income", "capex", "working_capital"], ...] = ()
    period_id: Identifier | None = None
    incremental_cash_outflow: Decimal = Decimal(0)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def treatment_is_coherent(self):
        if not self.incremental_cash_outflow.is_finite() or self.incremental_cash_outflow < 0:
            raise ValueError("incremental commitment outflow must be finite and nonnegative")
        if len(self.overlaps) != len(set(self.overlaps)):
            raise ValueError("commitment overlap dimensions must be unique")
        if self.treatment == "assumed_already_reflected":
            if not self.overlaps or self.period_id is not None or self.incremental_cash_outflow:
                raise ValueError("already-reflected commitments require overlap and no deduction")
        elif self.treatment == "incremental_deduction":
            if self.overlaps or self.period_id is None or self.incremental_cash_outflow <= 0:
                raise ValueError("incremental commitments require a period, amount, and no overlap")
        elif self.overlaps or self.period_id is not None or self.incremental_cash_outflow:
            raise ValueError("non-deducted commitment treatments cannot carry overlap or cash outflow")
        if not self.rationale.strip():
            raise ValueError("commitment treatment rationale cannot be blank")
        return self


class CashFlowBridgeReview(Contract):
    """Optional independent review record; review is not economic approval."""

    reviewer_id: Identifier
    reviewed_at: AwareDatetime
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operating_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["conditional_cash_flow_bridge"]
    findings: tuple[ReviewFinding, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator("limitations")
    @classmethod
    def nonblank_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("cash-flow review limitations cannot be blank")
        return values


class CashFlowBridgePackage(Contract):
    """Case-bound cash-flow assumptions layered on reviewed operating scenarios."""

    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,19}$")
    cutoff: AwareDatetime
    author_id: Identifier
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operating_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    date_convention: Literal["explicit_fiscal_period_dates_no_calendar_translation"] = (
        "explicit_fiscal_period_dates_no_calendar_translation"
    )
    historical_anchor: ReportedCashFlowAnchor
    historical_reconciliation: HistoricalCashFlowReconciliationSelector | None = None
    scenarios: tuple[CashFlowScenarioAssumptions, ...] = Field(min_length=1, max_length=3)
    commitment_horizon: str = Field(min_length=1)
    commitment_assumptions: tuple[CommitmentOverlapAssumption, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    review: CashFlowBridgeReview | None = None

    @model_serializer(mode="wrap")
    def omit_absent_reconciliation(self, handler):
        # Preserve both semantic and byte-level artifact identity for packages
        # created before this optional extension, including saved reader previews.
        data = handler(self)
        if self.historical_reconciliation is None:
            data.pop("historical_reconciliation", None)
        return data

    @model_validator(mode="after")
    def unique_nonblank_package(self):
        scenario_ids = [scenario.id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("cash-flow scenario identifiers must be unique")
        commitment_ids = [item.commitment_id for item in self.commitment_assumptions]
        if len(commitment_ids) != len(set(commitment_ids)):
            raise ValueError("commitment assumptions must be unique")
        if any(not limitation.strip() for limitation in self.limitations):
            raise ValueError("cash-flow limitations cannot be blank")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("cash-flow limitations must be unique")
        return self


@dataclass(frozen=True)
class CashFlowBridgeResult:
    reviewed: bool
    limitations: tuple[str, ...]
    model_context: dict
    artifacts: dict[str, bytes]
    calculated_values: tuple[CalculatedValue, ...]


def cashflow_bridge_package_sha256(package: CashFlowBridgePackage) -> str:
    """Hash every package field except the optional independent review."""

    if not isinstance(package, CashFlowBridgePackage):
        raise TypeError("package must be a CashFlowBridgePackage")
    excluded = {"review"}
    if package.historical_reconciliation is None:
        excluded.add("historical_reconciliation")
    return digest(package.model_dump(mode="json", exclude=excluded))


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(
        Context(prec=max(80, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 1))
    ):
        result = left * right
    if not result.is_finite() or result.copy_abs() > _MAX_AMOUNT:
        raise ValueError("cash-flow multiplication exceeds the bounded domain")
    return result


def _exact_sum(*values: Decimal) -> Decimal:
    minimum_exponent = min(value.as_tuple().exponent for value in values)
    digits = max(
        len(value.as_tuple().digits) + value.as_tuple().exponent - minimum_exponent
        for value in values
    )
    with localcontext(Context(prec=max(80, digits + len(str(len(values))) + 1))):
        result = sum(values, Decimal(0))
    if not result.is_finite() or result.copy_abs() > _MAX_AMOUNT:
        raise ValueError("cash-flow addition exceeds the bounded domain")
    return result


def _normalized_fact_value(fact: FinancialFact) -> Decimal:
    """Apply a fact's scale without inheriting the caller's Decimal context."""

    return _exact_product(fact.value, fact.scale)


def _eligible_fact_map(snapshot: EvidenceSnapshot) -> dict[str, FinancialFact]:
    eligible_sources = {
        source.id
        for source in snapshot.sources
        if source.availability == "full_text"
        and source.published_at is not None
        and source.published_at <= snapshot.cutoff
        and (
            source.retrieved_at <= snapshot.cutoff
            or (
                snapshot.instrument is not None
                and source.kind == "filing"
                and source.accession is not None
                and is_exact_sec_archive_filing_url(
                    source.url, cik=snapshot.instrument.cik, accession=source.accession
                )
            )
        )
        and hashlib.sha256(source.content.encode("utf-8")).hexdigest() == source.content_sha256
    }
    eligible: dict[str, FinancialFact] = {}
    pending = list(snapshot.facts)
    while pending:
        ready = [
            fact
            for fact in pending
            if fact.source_id in eligible_sources and set(fact.inputs) <= set(eligible)
        ]
        if not ready:
            break
        eligible.update((fact.id, fact) for fact in ready)
        pending = [fact for fact in pending if fact.id not in eligible]
    return eligible


def _fact(
    facts: dict[str, FinancialFact],
    identifier: str,
    metric: str,
    anchor: ReportedCashFlowAnchor,
) -> FinancialFact:
    fact = facts.get(identifier)
    if fact is None:
        raise ValueError(f"cash-flow anchor fact is absent or ineligible: {identifier}")
    if (
        fact.metric != metric
        or fact.period_type != "duration"
        or fact.period_start != anchor.period_start
        or fact.period_end != anchor.period_end
        or fact.basis != "US GAAP"
        or fact.unit != "USD"
        or fact.currency != "USD"
        or fact.segment is not None
    ):
        raise ValueError(f"cash-flow anchor fact selector mismatch: {identifier}")
    return fact


def _historical_result(anchor: ReportedCashFlowAnchor, facts: dict[str, FinancialFact]) -> dict:
    operating_income = _fact(facts, anchor.operating_income_fact_id, "operating_income", anchor)
    pretax = _fact(facts, anchor.income_before_tax_fact_id, "income_before_income_tax", anchor)
    tax = _fact(facts, anchor.income_tax_expense_fact_id, "income_tax_expense", anchor)
    depreciation = _fact(
        facts, anchor.depreciation_amortization_fact_id, "depreciation_amortization", anchor
    )
    capex = _fact(facts, anchor.capex_cashflow_fact_id, "capex_cashflow", anchor)
    operating_cash_flow = _fact(
        facts, anchor.operating_cash_flow_fact_id, "operating_cash_flow", anchor
    )
    values = {
        name: _normalized_fact_value(fact)
        for name, fact in (
            ("operating_income", operating_income),
            ("income_before_tax", pretax),
            ("income_tax_expense", tax),
            ("depreciation_amortization", depreciation),
            ("capex_cashflow", capex),
            ("operating_cash_flow", operating_cash_flow),
        )
    }
    if values["income_before_tax"] <= 0 or values["income_tax_expense"] < 0:
        raise ValueError("reported effective tax-rate anchors must be nonnegative with positive pretax")
    if values["capex_cashflow"] > 0:
        raise ValueError("reported capex cash-flow anchor must use the negative-outflow convention")
    with localcontext(Context(prec=80)):
        effective_tax_rate = values["income_tax_expense"] / values["income_before_tax"]
    if effective_tax_rate > 1:
        raise ValueError("reported effective tax rate exceeds one")

    current, prior = Decimal(0), Decimal(0)
    component_rows = []
    for component in anchor.working_capital_components:
        current_fact = facts.get(component.current_fact_id)
        prior_fact = facts.get(component.prior_fact_id)
        if current_fact is None or prior_fact is None:
            raise ValueError(f"working-capital fact is absent or ineligible: {component.id}")
        if (
            current_fact.metric != component.metric
            or prior_fact.metric != component.metric
            or current_fact.period_type != "instant"
            or prior_fact.period_type != "instant"
            or current_fact.period_end != anchor.period_end
            or prior_fact.period_end != anchor.period_start - timedelta(days=1)
            or current_fact.unit != "USD"
            or prior_fact.unit != "USD"
            or current_fact.currency != "USD"
            or prior_fact.currency != "USD"
            or current_fact.basis != "US GAAP"
            or prior_fact.basis != "US GAAP"
            or current_fact.segment is not None
            or prior_fact.segment is not None
        ):
            raise ValueError(f"working-capital anchor mismatch: {component.id}")
        sign = Decimal(1) if component.effect == "asset" else Decimal(-1)
        current_reported_value = _normalized_fact_value(current_fact)
        prior_reported_value = _normalized_fact_value(prior_fact)
        current_value = _exact_product(current_reported_value, sign)
        prior_value = _exact_product(prior_reported_value, sign)
        current = _exact_sum(current, current_value)
        prior = _exact_sum(prior, prior_value)
        component_rows.append(
            {
                **component.model_dump(mode="json"),
                "current_value": current_reported_value,
                "prior_value": prior_reported_value,
                "signed_change": _exact_sum(current_value, prior_value.copy_negate()),
            }
        )

    change_in_working_capital = _exact_sum(current, prior.copy_negate())
    operating_tax_proxy = _exact_product(values["operating_income"], effective_tax_rate)
    nopat_proxy = _exact_sum(values["operating_income"], operating_tax_proxy.copy_negate())
    capex_magnitude = values["capex_cashflow"].copy_negate()
    bridge_cash_flow = _exact_sum(
        nopat_proxy,
        values["depreciation_amortization"],
        capex_magnitude.copy_negate(),
        change_in_working_capital.copy_negate(),
    )
    reported_free_cash_flow = _exact_sum(
        values["operating_cash_flow"], capex_magnitude.copy_negate()
    )
    return {
        "fiscal_label": anchor.fiscal_label,
        "period_start": anchor.period_start,
        "period_end": anchor.period_end,
        "accounting_basis": "US GAAP",
        "currency": "USD",
        "classification": "reported_anchors_with_explicit_operating_tax_and_owc_proxy",
        "operating_income": values["operating_income"],
        "reported_consolidated_effective_tax_rate": effective_tax_rate,
        "operating_tax_proxy": operating_tax_proxy,
        "nopat_proxy": nopat_proxy,
        "reported_depreciation_amortization": values["depreciation_amortization"],
        "reported_capex_magnitude": capex_magnitude,
        "known_row_working_capital_opening": prior,
        "known_row_working_capital_closing": current,
        "change_in_known_row_working_capital": change_in_working_capital,
        "working_capital_components": component_rows,
        "bridge_cash_flow": bridge_cash_flow,
        "reported_operating_cash_flow": values["operating_cash_flow"],
        "reported_free_cash_flow": reported_free_cash_flow,
        "bridge_minus_reported_free_cash_flow": _exact_sum(
            bridge_cash_flow, reported_free_cash_flow.copy_negate()
        ),
        "fact_ids": (
            anchor.operating_income_fact_id,
            anchor.income_before_tax_fact_id,
            anchor.income_tax_expense_fact_id,
            anchor.depreciation_amortization_fact_id,
            anchor.capex_cashflow_fact_id,
            anchor.operating_cash_flow_fact_id,
        ),
    }


def _historical_reconciliation(
    selector: HistoricalCashFlowReconciliationSelector,
    anchor: ReportedCashFlowAnchor,
    historical: dict,
    facts: dict[str, FinancialFact],
) -> dict:
    """Attribute the unchanged proxy residual to exact reported CFO source rows."""

    anchor_ids = (
        anchor.operating_income_fact_id, anchor.income_before_tax_fact_id,
        anchor.income_tax_expense_fact_id, anchor.depreciation_amortization_fact_id,
        anchor.capex_cashflow_fact_id, anchor.operating_cash_flow_fact_id,
    )
    if any(row.fact_id in anchor_ids for row in selector.rows):
        raise ValueError("historical reconciliation row reuses a different anchor role")
    anchor_cfo = facts[anchor.operating_cash_flow_fact_id]
    anchor_capex = facts[anchor.capex_cashflow_fact_id]
    anchor_da = facts[anchor.depreciation_amortization_fact_id]
    if (
        anchor_cfo.source_id != selector.anchor_source_id
        or anchor_capex.source_id != selector.anchor_source_id
        or anchor_da.source_id != selector.cashflow_statement_source_id
    ):
        raise ValueError("historical reconciliation anchor source mismatch")

    row_by_role = {row.role: row for row in selector.rows}
    source_rows = {}
    for role, metric in _RECONCILIATION_METRICS.items():
        selection = row_by_role[role]
        fact = _fact(facts, selection.fact_id, metric, anchor)
        expected_source = (
            selector.anchor_source_id
            if role in {"net_income", "stock_compensation"}
            else selector.cashflow_statement_source_id
        )
        if fact.source_id != expected_source or fact.inputs or fact.formula:
            raise ValueError(f"historical reconciliation source mismatch: {role}")
        value = _normalized_fact_value(fact)
        actual_sign = "positive" if value > 0 else "negative" if value < 0 else "zero"
        if actual_sign != selection.sign:
            raise ValueError(f"historical reconciliation sign mismatch: {role}")
        source_rows[role] = {
            "fact_id": fact.id,
            "source_id": fact.source_id,
            "source_location": fact.location,
            "metric": fact.metric,
            "source_value": fact.value,
            "scale": fact.scale,
            "signed_value": value,
            "sign": actual_sign,
        }

    principal = _fact(
        facts, selector.asset_principal_cashflow_fact_id,
        "asset_principal_cashflow", anchor,
    )
    issuer_fcf = facts.get(selector.issuer_free_cash_flow_fact_id)
    if issuer_fcf is None:
        raise ValueError("historical reconciliation issuer FCF fact is absent or ineligible")
    if (
        issuer_fcf.metric != "issuer_free_cash_flow"
        or issuer_fcf.period_type != "duration"
        or issuer_fcf.period_start != anchor.period_start
        or issuer_fcf.period_end != anchor.period_end
        or issuer_fcf.basis != "issuer non-GAAP FCF"
        or issuer_fcf.unit != "USD"
        or issuer_fcf.currency != "USD"
        or issuer_fcf.segment is not None
    ):
        raise ValueError("historical reconciliation issuer FCF selector mismatch")
    if (
        principal.source_id != selector.anchor_source_id
        or issuer_fcf.source_id != selector.anchor_source_id
        or principal.inputs or principal.formula or issuer_fcf.inputs or issuer_fcf.formula
    ):
        raise ValueError("historical reconciliation FCF source mismatch")
    principal_value = _normalized_fact_value(principal)
    issuer_value = _normalized_fact_value(issuer_fcf)
    if principal_value > 0:
        raise ValueError("asset principal cash flow must use the negative-outflow convention")
    comparator = Decimal(historical["reported_free_cash_flow"])
    if _exact_sum(comparator, principal_value) != issuer_value:
        raise ValueError("issuer FCF differs from CFO less capex and asset principal")

    noncash = _exact_sum(
        *(source_rows[role]["signed_value"] for role in _NONCASH_ROLES)
    )
    movements = _exact_sum(
        *(source_rows[role]["signed_value"] for role in _MOVEMENT_ROLES)
    )
    reconstructed_cfo = _exact_sum(
        source_rows["net_income"]["signed_value"],
        Decimal(historical["reported_depreciation_amortization"]),
        noncash, movements,
    )
    if reconstructed_cfo != Decimal(historical["reported_operating_cash_flow"]):
        raise ValueError("historical reconciliation CFO subtotal mismatch")

    components = {
        "tax_proxy_less_net_income": {
            "value": _exact_sum(
                Decimal(historical["nopat_proxy"]),
                source_rows["net_income"]["signed_value"].copy_negate(),
            ),
            "fact_ids": (
                anchor.operating_income_fact_id, anchor.income_before_tax_fact_id,
                anchor.income_tax_expense_fact_id, row_by_role["net_income"].fact_id,
            ),
        },
        "minus_omitted_noncash_adjustments": {
            "value": noncash.copy_negate(),
            "fact_ids": tuple(row_by_role[role].fact_id for role in _NONCASH_ROLES),
        },
        "balance_sheet_proxy_less_reported_movements": {
            "value": _exact_sum(
                Decimal(historical["change_in_known_row_working_capital"]).copy_negate(),
                movements.copy_negate(),
            ),
            "fact_ids": tuple(dict.fromkeys((
                *(fact_id for component in anchor.working_capital_components
                  for fact_id in (component.current_fact_id, component.prior_fact_id)),
                *(row_by_role[role].fact_id for role in _MOVEMENT_ROLES),
            ))),
        },
    }
    residual = _exact_sum(*(component["value"] for component in components.values()))
    if residual != Decimal(historical["bridge_minus_reported_free_cash_flow"]):
        raise ValueError("historical reconciliation residual does not match proxy bridge")
    return {
        "status": "mechanically_attributed_not_economic_approval",
        "currency": "USD",
        "period_start": anchor.period_start,
        "period_end": anchor.period_end,
        "anchor_source_id": selector.anchor_source_id,
        "cashflow_statement_source_id": selector.cashflow_statement_source_id,
        "source_rows": source_rows,
        "depreciation_amortization_fact_id": anchor.depreciation_amortization_fact_id,
        "depreciation_amortization_source_id": anchor_da.source_id,
        "depreciation_amortization_source_location": anchor_da.location,
        "depreciation_amortization_value": historical["reported_depreciation_amortization"],
        "reported_noncash_adjustments": noncash,
        "reported_cashflow_movements": movements,
        "reconstructed_operating_cash_flow": reconstructed_cfo,
        "reported_operating_cash_flow_fact_id": anchor.operating_cash_flow_fact_id,
        "reported_operating_cash_flow_source_id": anchor_cfo.source_id,
        "ocf_minus_capex": comparator,
        "issuer_free_cash_flow": issuer_value,
        "issuer_free_cash_flow_fact_id": issuer_fcf.id,
        "issuer_free_cash_flow_source_location": issuer_fcf.location,
        "asset_principal_cashflow": principal_value,
        "asset_principal_cashflow_fact_id": principal.id,
        "asset_principal_source_location": principal.location,
        "issuer_fcf_less_ocf_minus_capex": _exact_sum(
            issuer_value, comparator.copy_negate()
        ),
        "residual_components": components,
        "bridge_minus_ocf_minus_capex": residual,
        "limitation": (
            "Source-statement arithmetic only; mixed working-capital movements and "
            "operating-tax economics remain unresolved, and this is not economic approval."
        ),
    }


def _review_status(package: CashFlowBridgePackage) -> tuple[bool, tuple[str, ...]]:
    review = package.review
    if review is None:
        return False, (_UNREVIEWED_LIMITATION,)
    errors = []
    if review.reviewer_id == package.author_id:
        errors.append("Cash-flow bridge author and independent reviewer must differ.")
    if review.package_sha256 != cashflow_bridge_package_sha256(package):
        errors.append("Cash-flow bridge review package hash is stale or mismatched.")
    if review.case_sha256 != package.case_sha256:
        errors.append("Cash-flow bridge review case hash is mismatched.")
    if review.evidence_sha256 != package.evidence_sha256:
        errors.append("Cash-flow bridge review evidence hash is mismatched.")
    if review.operating_package_sha256 != package.operating_package_sha256:
        errors.append("Cash-flow bridge review operating-package hash is mismatched.")
    if review.reviewed_at < package.cutoff:
        errors.append("Cash-flow bridge review cannot predate the evidence cutoff.")
    if any(finding.severity in {"warning", "critical"} for finding in review.findings):
        errors.append("Cash-flow bridge review contains unresolved warning or critical findings.")
    return not errors, tuple(errors)


def _calculated_values(
    package: CashFlowBridgePackage,
    historical: dict,
    scenarios: list[dict],
    result_hash: str,
    operating_result: OperatingScenarioResult,
    incremental_evidence_by_period: dict[str, list[str]],
) -> tuple[CalculatedValue, ...]:
    """Create the controlled-rendering catalog for a reviewed bridge only."""

    input_hash = cashflow_bridge_package_sha256(package)
    historical_evidence = tuple(
        dict.fromkeys(
            (
                *historical["fact_ids"],
                *(
                    fact_id
                    for row in historical["working_capital_components"]
                    for fact_id in (row["current_fact_id"], row["prior_fact_id"])
                ),
            )
        )
    )
    values: list[CalculatedValue] = []

    def add(identifier, value, evidence_ids, classification):
        values.append(
            CalculatedValue(
                id=identifier,
                value=value,
                unit="USD",
                currency="USD",
                classification=classification,
                valuation_method="cashflow_bridge",
                share_count_basis="not_applicable",
                model_input_sha256=input_hash,
                model_result_sha256=result_hash,
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            )
        )

    for field in (
        "bridge_cash_flow",
        "reported_free_cash_flow",
        "bridge_minus_reported_free_cash_flow",
    ):
        add(
            f"cashflow_bridge.historical.{field}",
            historical[field],
            historical_evidence,
            "reported_anchors_with_conditional_tax_and_working_capital_proxy_not_reported_fact",
        )
    reconciliation = historical.get("reconciliation")
    if reconciliation is not None:
        source_rows = reconciliation["source_rows"]
        all_reconciliation_ids = tuple(dict.fromkeys((
            *historical_evidence,
            *(row["fact_id"] for row in source_rows.values()),
            reconciliation["asset_principal_cashflow_fact_id"],
            reconciliation["issuer_free_cash_flow_fact_id"],
        )))
        for field in (
            "reconstructed_operating_cash_flow", "ocf_minus_capex",
            "issuer_free_cash_flow", "issuer_fcf_less_ocf_minus_capex",
            "bridge_minus_ocf_minus_capex",
        ):
            add(
                f"cashflow_bridge.historical.reconciliation.{field}",
                reconciliation[field], all_reconciliation_ids,
                "mechanical_historical_reconciliation_not_economic_approval",
            )
        for name, component in reconciliation["residual_components"].items():
            add(
                f"cashflow_bridge.historical.reconciliation.{name}",
                component["value"], component["fact_ids"],
                "mechanical_proxy_residual_component_not_economic_approval",
            )
    package_scenarios = {scenario.id: scenario for scenario in package.scenarios}
    operating_values = {value.id: value for value in operating_result.calculated_values}
    for scenario in scenarios:
        assumptions_by_period = {
            period.period_id: period for period in package_scenarios[scenario["id"]].periods
        }
        scenario_evidence: list[str] = []
        for period in scenario["periods"]:
            assumptions = assumptions_by_period[period["period_id"]]
            assumption_evidence = tuple(
                dict.fromkeys(
                    item
                    for assumption in (
                        assumptions.tax_rate,
                        assumptions.depreciation_amortization,
                        assumptions.capex,
                        assumptions.change_in_operating_working_capital,
                    )
                    for item in assumption.evidence_ids
                )
            )
            operating_id = (
                f"operating_scenario.{scenario['id']}.{period['period_id']}.operating_income"
            )
            if operating_id not in operating_values:
                raise ValueError("cash-flow bridge requires operating-income calculation ancestry")
            evidence_ids = tuple(
                dict.fromkeys((
                    *operating_values[operating_id].evidence_ids,
                    *assumption_evidence,
                    *incremental_evidence_by_period[period["period_id"]],
                ))
            )
            scenario_evidence.extend(evidence_ids)
            prefix = f"cashflow_bridge.{scenario['id']}.{period['period_id']}"
            for field in (
                "operating_tax",
                "nopat",
                "fcff_before_incremental_commitments",
                "incremental_commitment_deduction",
                "conditional_cash_flow",
            ):
                add(prefix + f".{field}", period[field], evidence_ids, _CALCULATION_CLASSIFICATION)
            for field in (
                "depreciation_amortization",
                "capex",
                "change_in_operating_working_capital",
            ):
                add(
                    prefix + f".{field}",
                    period[field]["value"],
                    evidence_ids,
                    "conditional_analyst_assumption_not_reported_fact",
                )
        combined_evidence = tuple(dict.fromkeys((*historical_evidence, *scenario_evidence)))
        add(
            f"cashflow_bridge.{scenario['id']}.future_period_cash_flow",
            scenario["future_period_cash_flow"],
            combined_evidence,
            _CALCULATION_CLASSIFICATION,
        )
        add(
            f"cashflow_bridge.{scenario['id']}.fiscal_total.conditional_cash_flow",
            scenario["fiscal_total"]["conditional_cash_flow"],
            combined_evidence,
            _CALCULATION_CLASSIFICATION,
        )
    return tuple(values)


def evaluate_cashflow_bridge(
    package: CashFlowBridgePackage,
    case: FinancialCase,
    snapshot: EvidenceSnapshot,
    operating_result: OperatingScenarioResult,
) -> CashFlowBridgeResult:
    """Validate bindings and compute a dated, conditional cash-flow bridge."""

    if not isinstance(package, CashFlowBridgePackage):
        raise TypeError("package must be a CashFlowBridgePackage")
    if not isinstance(case, FinancialCase) or not isinstance(snapshot, EvidenceSnapshot):
        raise TypeError("case and snapshot must use their validated contract types")
    if not isinstance(operating_result, OperatingScenarioResult):
        raise TypeError("operating_result must be an OperatingScenarioResult")
    package = CashFlowBridgePackage.model_validate_json(package.model_dump_json(warnings="error"))
    case = FinancialCase.model_validate_json(case.model_dump_json(warnings="error"))
    snapshot = EvidenceSnapshot.model_validate_json(snapshot.model_dump_json(warnings="error"))

    case_hash = digest(case.model_dump(mode="json"))
    evidence_hash = evidence_snapshot_sha256(snapshot)
    operating_context = operating_result.model_context
    if package.ticker != case.ticker or package.ticker != snapshot.ticker:
        raise ValueError("cash-flow bridge ticker differs from case or snapshot")
    if package.cutoff != case.cutoff or package.cutoff != snapshot.cutoff:
        raise ValueError("cash-flow bridge cutoff differs from case or snapshot")
    if package.case_sha256 != case_hash or package.evidence_sha256 != evidence_hash:
        raise ValueError("cash-flow bridge case or evidence hash is mismatched")
    if case.snapshot_sha256 != evidence_hash:
        raise ValueError("financial case does not bind the frozen evidence snapshot")
    reconciliation = reconcile_financial_case(case, snapshot)
    if reconciliation.case_sha256 != case_hash:
        raise ValueError("financial-case reconciliation identity mismatch")
    if not operating_result.reviewed or operating_context.get("reviewed") is not True:
        raise ValueError("cash-flow bridge requires reviewed conditional operating scenarios")
    if (
        operating_context.get("case_sha256") != case_hash
        or operating_context.get("evidence_sha256") != evidence_hash
        or operating_context.get("package_sha256") != package.operating_package_sha256
    ):
        raise ValueError("cash-flow bridge operating-package binding is mismatched")

    facts = _eligible_fact_map(snapshot)
    historical = _historical_result(package.historical_anchor, facts)
    if package.historical_reconciliation is not None:
        historical["reconciliation"] = _historical_reconciliation(
            package.historical_reconciliation, package.historical_anchor, historical, facts
        )
    if package.historical_anchor.period_end != case.opening_date:
        raise ValueError("reported cash-flow anchor must end on the observed case opening date")

    eligible_ids = set(facts)
    operating_by_id = {item["id"]: item for item in operating_context["scenarios"]}
    package_by_id = {item.id: item for item in package.scenarios}
    if set(package_by_id) != set(operating_by_id):
        raise ValueError("cash-flow scenarios must exactly cover reviewed operating scenarios")
    period_dates = {
        period.period_id: (period.period_start, period.period_end)
        for period in package.scenarios[0].periods
    }
    # Commitment treatments are shared across scenarios, so their period keys
    # must identify the same dates everywhere (not merely exist in the union).
    for scenario in package.scenarios[1:]:
        if {
            period.period_id: (period.period_start, period.period_end)
            for period in scenario.periods
        } != period_dates:
            raise ValueError("cash-flow scenarios must share identical period IDs and dates")

    commitments = {item.id: item for item in case.commitments.items}
    in_horizon = {
        item.id for item in case.commitments.items if item.disclosed_timing == package.commitment_horizon
    }
    treatments = {item.commitment_id: item for item in package.commitment_assumptions}
    if set(treatments) != in_horizon:
        raise ValueError("commitment assumptions must exactly cover the disclosed bridge horizon")
    commitment_rows = []
    incremental_by_period = {period_id: Decimal(0) for period_id in period_dates}
    incremental_evidence_by_period: dict[str, list[str]] = {
        period_id: [] for period_id in period_dates
    }
    for identifier in sorted(in_horizon):
        source = commitments[identifier]
        treatment = treatments[identifier]
        # Both deductions and treatment totals below are USD amounts. Never
        # silently aggregate another currency or a nonmonetary quantity.
        if source.unit != "USD" or source.currency != "USD":
            raise ValueError("cash-flow commitments require USD units and currency")
        if treatment.treatment == "zero_disclosed" and source.normalized_value != 0:
            raise ValueError("zero-disclosed treatment requires a reported zero amount")
        if treatment.treatment != "zero_disclosed" and source.normalized_value == 0:
            raise ValueError("reported zero commitments must use zero-disclosed treatment")
        if treatment.treatment == "incremental_deduction":
            if source.timing != "known":
                raise ValueError("unknown-timing commitments cannot be deducted incrementally")
            if (
                source.amount_basis != "contractual_cash"
                or source.treatment != "deduct_incrementally"
                or source.overlap != "none"
            ):
                raise ValueError(
                    "incremental commitment requires contractual cash and a compatible "
                    "non-overlapping source treatment"
                )
            if treatment.period_id not in incremental_by_period:
                raise ValueError("incremental commitment references an unknown cash-flow period")
            period_start, period_end = period_dates[treatment.period_id]
            if period_end < source.due_start or period_start > source.due_end:
                raise ValueError(
                    "incremental commitment period does not overlap its disclosed due window"
                )
            if treatment.incremental_cash_outflow > source.normalized_value:
                raise ValueError("incremental commitment deduction exceeds the disclosed amount")
            incremental_by_period[treatment.period_id] = _exact_sum(
                incremental_by_period[treatment.period_id], treatment.incremental_cash_outflow
            )
            incremental_evidence_by_period[treatment.period_id].append(source.fact_id)
        commitment_rows.append(
            {
                **treatment.model_dump(mode="json"),
                "disclosed_timing": source.disclosed_timing,
                "reported_amount": source.normalized_value,
                "amount_basis": source.amount_basis,
                "source_case_timing": source.timing,
                "source_case_overlap": source.overlap,
                "source_case_treatment": source.treatment,
                "fact_id": source.fact_id,
            }
        )

    rendered_scenarios = []
    for scenario in package.scenarios:
        operating = operating_by_id[scenario.id]
        operating_periods = {item["id"]: item for item in operating["periods"]}
        if {period.period_id for period in scenario.periods} != set(operating_periods):
            raise ValueError("cash-flow periods must exactly cover each operating scenario")
        rendered_periods = []
        expected_start = package.historical_anchor.period_end + timedelta(days=1)
        for assumptions in scenario.periods:
            source = operating_periods[assumptions.period_id]
            source_start = (
                date.fromisoformat(source["period_start"])
                if isinstance(source["period_start"], str)
                else source["period_start"]
            )
            source_end = (
                date.fromisoformat(source["period_end"])
                if isinstance(source["period_end"], str)
                else source["period_end"]
            )
            if (
                assumptions.period_start != source_start
                or assumptions.period_end != source_end
                or assumptions.period_start != expected_start
            ):
                raise ValueError("cash-flow dates differ from reviewed fiscal operating periods")
            expected_start = assumptions.period_end + timedelta(days=1)
            for item in (
                assumptions.tax_rate,
                assumptions.depreciation_amortization,
                assumptions.capex,
                assumptions.change_in_operating_working_capital,
            ):
                if set(item.evidence_ids) - eligible_ids:
                    raise ValueError("cash-flow assumption references absent or ineligible evidence")
            revenue = Decimal(source["inputs"]["revenue"]["value"])
            operating_income = Decimal(source["calculated"]["operating_income"])
            operating_tax = _exact_product(operating_income, assumptions.tax_rate.value)
            nopat = _exact_sum(operating_income, operating_tax.copy_negate())
            pre_commitment = _exact_sum(
                nopat,
                assumptions.depreciation_amortization.value,
                assumptions.capex.value.copy_negate(),
                assumptions.change_in_operating_working_capital.value.copy_negate(),
            )
            incremental = incremental_by_period[assumptions.period_id]
            rendered_periods.append(
                {
                    "period_id": assumptions.period_id,
                    "fiscal_label": source["fiscal_label"],
                    "period_start": assumptions.period_start,
                    "period_end": assumptions.period_end,
                    "currency": "USD",
                    "classification": _CALCULATION_CLASSIFICATION,
                    "revenue": revenue,
                    "operating_income": operating_income,
                    "tax_rate": assumptions.tax_rate.model_dump(mode="json"),
                    "operating_tax": operating_tax,
                    "nopat": nopat,
                    "depreciation_amortization": assumptions.depreciation_amortization.model_dump(
                        mode="json"
                    ),
                    "capex": assumptions.capex.model_dump(mode="json"),
                    "change_in_operating_working_capital": (
                        assumptions.change_in_operating_working_capital.model_dump(mode="json")
                    ),
                    "fcff_before_incremental_commitments": pre_commitment,
                    "incremental_commitment_deduction": incremental,
                    "conditional_cash_flow": _exact_sum(
                        pre_commitment, incremental.copy_negate()
                    ),
                }
            )
        future_total = _exact_sum(
            *(Decimal(item["conditional_cash_flow"]) for item in rendered_periods)
        )
        fiscal_total = _exact_sum(Decimal(historical["bridge_cash_flow"]), future_total)
        rendered_scenarios.append(
            {
                "id": scenario.id,
                "label": operating["label"],
                "periods": rendered_periods,
                "future_period_cash_flow": future_total,
                "fiscal_total": {
                    "fiscal_label": operating["fiscal_total"]["fiscal_label"],
                    "period_start": package.historical_anchor.period_start,
                    "period_end": rendered_periods[-1]["period_end"],
                    "currency": "USD",
                    "classification": _CALCULATION_CLASSIFICATION,
                    "reported_anchor_cash_flow": historical["bridge_cash_flow"],
                    "future_conditional_cash_flow": future_total,
                    "conditional_cash_flow": fiscal_total,
                },
            }
        )

    reviewed, review_errors = _review_status(package)
    review = package.review
    limitations = tuple(
        dict.fromkeys(
            (
                *package.limitations,
                *review_errors,
                *(review.limitations if reviewed and review is not None else ()),
                _SCOPE_LIMITATION,
            )
        )
    )
    commitment_totals = {
        treatment: _exact_sum(
            *(
                commitments[row.commitment_id].normalized_value
                for row in package.commitment_assumptions
                if row.treatment == treatment
            ),
            Decimal(0),
        )
        for treatment in (
            "assumed_already_reflected",
            "incremental_deduction",
            "excluded_non_operating",
            "zero_disclosed",
            "unresolved_not_deducted",
        )
    }
    package_hash = cashflow_bridge_package_sha256(package)
    commitment_context = {
        "horizon": package.commitment_horizon,
        "coverage": "all_disclosed_case_items_in_horizon",
        "items": commitment_rows,
        "reported_amounts_by_treatment": commitment_totals,
        "outside_horizon_item_ids": [
            item.id
            for item in case.commitments.items
            if item.disclosed_timing != package.commitment_horizon
        ],
        "no_automatic_gross_commitment_deduction": True,
    }
    output_scope = {
        "conditional_cash_flow_bridge": {
            "status": "conditional" if reviewed else "audit_only",
            "reasons": [
                (
                    "A current independent review permits controlled rendering of the "
                    "conditional bridge calculations."
                    if reviewed
                    else "Numerical bridge artifacts await independent hash-bound review."
                ),
                "Review and arithmetic do not imply economic approval.",
            ],
        },
        "operating_asset_value": {
            "status": "blocked",
            "reasons": ["No discount rate, terminal cash flow, or valuation is supplied."],
        },
        "equity_per_share_value": {
            "status": "blocked",
            "reasons": ["The financial-case equity bridge and diluted capitalization remain incomplete."],
        },
        "funding_assessment": {
            "status": "blocked",
            "reasons": [
                "Commitment overlap is assessed only for bridge double counting, not liquidity or funding adequacy."
            ],
        },
    }
    result_payload = {
        "schema_version": 1,
        "classification": _CALCULATION_CLASSIFICATION,
        "package_sha256": package_hash,
        "historical_anchor": historical,
        "scenarios": rendered_scenarios,
        "reviewed": reviewed,
        "review_status": (
            "independently_reviewed_conditional_not_approval"
            if reviewed
            else "not_independently_reviewed_review_artifact_only"
        ),
        "commitment_overlap": commitment_context,
        "output_scope": output_scope,
        "limitations": limitations,
    }
    result_hash = digest(result_payload)
    calculated_values = (
        _calculated_values(
            package, historical, rendered_scenarios, result_hash,
            operating_result, incremental_evidence_by_period,
        )
        if reviewed
        else ()
    )
    common_context = {
        "schema_version": 1,
        "reviewed": reviewed,
        "package_sha256": package_hash,
        "case_sha256": case_hash,
        "evidence_sha256": evidence_hash,
        "operating_package_sha256": package.operating_package_sha256,
        "model_result_sha256": result_hash,
        "date_convention": package.date_convention,
        "temporal_basis": {
            "reported_anchor_end": package.historical_anchor.period_end,
            "evidence_cutoff": package.cutoff,
            "cash_flows_use_actual_fiscal_period_dates": True,
            "calendar_translation_performed": False,
            "cutoff_is_not_a_rolled_historical_anchor": True,
        },
        "review": review.model_dump(mode="json") if review is not None else None,
        "output_scope": output_scope,
        "limitations": limitations,
    }
    if reviewed:
        context = {
            **common_context,
            "context_kind": "reviewed_conditional_cash_flow_bridge",
            "review_status": "independently_reviewed_conditional_not_approval",
            "decision": "conditional_cash_flow_bridge",
            "classification": _CALCULATION_CLASSIFICATION,
            "historical_anchor": historical,
            "scenarios": rendered_scenarios,
            "commitment_overlap": commitment_context,
            "calculated_value_ids": [item.id for item in calculated_values],
        }
    else:
        context = {
            **common_context,
            "context_kind": "cash_flow_bridge_audit_only",
            "review_status": "not_independently_reviewed_review_artifact_only",
            "decision": None,
            "audit": {
                "classification": "unreviewed_cash_flow_inputs_not_for_numeric_presentation",
                "scenario_ids": [scenario.id for scenario in package.scenarios],
                "periods": [
                    {
                        "scenario_id": scenario.id,
                        "period_id": period.period_id,
                        "period_start": period.period_start,
                        "period_end": period.period_end,
                    }
                    for scenario in package.scenarios
                    for period in scenario.periods
                ],
                "commitment_horizon": package.commitment_horizon,
                "commitment_ids": [
                    item.commitment_id for item in package.commitment_assumptions
                ],
                "review_errors": review_errors,
                "numeric_review_artifact": "cashflow_bridge_result.json",
            },
        }
    artifacts = {
        "cashflow_bridge_package.json": canonical_json(package),
        "cashflow_bridge_context.json": canonical_json(context),
        "cashflow_bridge_result.json": canonical_json(result_payload),
    }
    if calculated_values:
        artifacts["cashflow_bridge_calculated_values.json"] = canonical_json(calculated_values)
    return CashFlowBridgeResult(
        reviewed=reviewed,
        limitations=limitations,
        model_context=context,
        artifacts=artifacts,
        calculated_values=calculated_values,
    )
