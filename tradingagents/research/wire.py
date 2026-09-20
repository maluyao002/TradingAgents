"""Strict structured-output wire contracts for the isolated Codex backend.

Domain contracts keep ergonomic defaults for replay and internal callers.  These
wire-only models make every generated field explicit and avoid open-ended maps,
as required by strict structured outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator
from pydantic_core import SchemaError, SchemaValidator, core_schema

from .case_report import CaseReportDraft, SectionPurpose
from .contracts import Identifier
from .investigation_review import InvestigationReview
from .report_review import ReaderVerification
from .review_lifecycle import LifecycleVerification
from .stages import AnalysisOutput, ReportDraft, ValuationProposal, VerificationOutput

WIRE_SCHEMA_VERSION = "research-wire-v3"
# Keep exact financial values as strings at the generation boundary. Pydantic's
# default Decimal schema contains a lookahead pattern that is not portable across
# structured-output regex engines. Decimal still validates finite values locally;
# the financial domain retains all range, unit and schedule checks.
WireDecimal = Annotated[Decimal, WithJsonSchema({
    "type": "string", "description": "Finite decimal encoded as an exact string, e.g. 0.125."
})]
_VALUATION_WIRE_INSTRUCTION = (
    " For this valuation response only, encode assumption_rationale and assumptions as arrays "
    "of {key, value} entries, with no duplicate keys. The model field must be null or match the "
    "typed FCFF model in the response schema, including every required field. "
    "Encode financial amounts, scales and rates as exact decimal strings, not JSON numbers."
)


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _WireContract(_WireModel):
    schema_version: Literal[1]


class WireResearchQuestion(_WireContract):
    id: Identifier
    question: str
    consequence: str
    resolvability: Literal["high", "medium", "low"]
    status: Literal["open", "resolved", "unresolved"]


class WireFinding(_WireContract):
    id: Identifier
    question_id: Identifier
    conclusion: str
    evidence_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    uncertainty: str
    economic_consequence: str
    invalidation: str


class WireExtractedClaim(_WireContract):
    id: Identifier
    text: str
    source_ids: tuple[str, ...]
    source_locations: tuple[str, ...]
    kind: Literal["reported", "interpretation", "assumption", "calculation"]
    verification: Literal["unverified", "supported", "contradicted"]
    material: bool


class WireAnalysisOutput(_WireContract):
    summary: str = Field(min_length=1)
    questions: tuple[WireResearchQuestion, ...]
    findings: tuple[WireFinding, ...]
    claims: tuple[WireExtractedClaim, ...]
    unresolved_gaps: tuple[str, ...]
    followup_questions: tuple[str, ...]


class WireAssumptionSupport(_WireContract):
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    kind: Literal["reported", "assumption"]


class WireValuationUnits(_WireModel):
    currency: str
    amount_scale: WireDecimal
    share_scale: WireDecimal


class WireForecastPeriod(_WireModel):
    label: str
    period_start: date
    period_end: date
    discount_years: WireDecimal
    revenue_growth: WireDecimal
    operating_margin: WireDecimal
    operating_margin_basis: Literal["after_sbc", "before_sbc"]
    tax_rate: WireDecimal
    depreciation_amortization_pct_revenue: WireDecimal
    capex_pct_revenue: WireDecimal
    working_capital_pct_revenue: WireDecimal
    sbc_pct_revenue: WireDecimal
    external_funding_required: bool


class WireFCFFModelInput(_WireModel):
    as_of_date: date
    current_revenue: WireDecimal
    current_working_capital: WireDecimal
    periods: tuple[WireForecastPeriod, ...] = Field(min_length=1, max_length=50)
    discount_rate: WireDecimal
    terminal_growth: WireDecimal
    net_debt: WireDecimal
    current_diluted_shares: WireDecimal
    units: WireValuationUnits
    funding_caveats: tuple[str, ...]


class WireStringEntry(_WireModel):
    key: str = Field(min_length=1)
    value: str


class WireAssumptionEntry(_WireModel):
    key: str = Field(min_length=1)
    value: WireAssumptionSupport


class WireValuationProposal(_WireContract):
    model: WireFCFFModelInput | None
    accounting_basis: Literal["US GAAP", "IFRS"] | None
    assumption_rationale: tuple[WireStringEntry, ...]
    assumptions: tuple[WireAssumptionEntry, ...]
    evidence_ids: tuple[str, ...]
    unsupported_inputs: tuple[str, ...]
    scope_limitations: tuple[str, ...]

    @field_validator("assumption_rationale", "assumptions")
    @classmethod
    def unique_entry_keys(cls, value):
        keys = [entry.key for entry in value]
        if len(keys) != len(set(keys)):
            raise ValueError("valuation wire entries require unique keys")
        return value


class WireEquityForecastPeriod(_WireModel):
    label: str
    period_start: date
    period_end: date
    discount_years: WireDecimal
    net_income_common: WireDecimal
    required_capital_retention: WireDecimal


class WireEquityDCFModelInput(_WireModel):
    as_of_date: date
    current_net_income: WireDecimal
    periods: tuple[WireEquityForecastPeriod, ...] = Field(min_length=1, max_length=50)
    cost_of_equity: WireDecimal
    terminal_growth: WireDecimal
    current_diluted_shares: WireDecimal
    units: WireValuationUnits


class WireEquityValuationProposal(WireValuationProposal):
    model: WireEquityDCFModelInput | None


class WireClosureDecision(_WireContract):
    task_id: str
    status: Literal["still_open", "resolved", "disposed"]
    evidence_ids: tuple[str, ...]
    verifier_judgment: Literal["resolved", "not_resolved"] | None
    verifier_provenance_id: str | None
    disposition: str


class WireInvestigationReview(_WireContract):
    decisions: tuple[WireClosureDecision, ...]


class WireReviewFinding(_WireContract):
    code: str
    severity: Literal["info", "warning", "critical"]
    message: str
    affected_ids: tuple[str, ...]
    category: Literal["data", "numerical", "research", "editorial", "security"]


class WireVerificationOutput(_WireContract):
    supported_claim_ids: tuple[str, ...]
    contradicted_claim_ids: tuple[str, ...]
    findings: tuple[WireReviewFinding, ...]
    reviewed_report: bool


class WireLimitationDisposition(_WireContract):
    issue_id: str
    decision: Literal["reader_covered", "audit_only_operational", "audit_only_immaterial", "unresolved"]
    rationale: str
    reader_excerpt: str
    reader_excerpts: tuple[str, ...]


class WireReaderVerification(WireVerificationOutput):
    limitation_dispositions: tuple[WireLimitationDisposition, ...]


class WireEvidenceWitness(_WireContract):
    reference: str
    excerpt: str


class WireIssueResolution(_WireContract):
    issue_id: str
    status: Literal["open", "resolved", "superseded"]
    rationale: str
    witnesses: tuple[WireEvidenceWitness, ...]
    reader_excerpts: tuple[str, ...]


class WireFindingDisposition(_WireContract):
    finding_code: str
    disposition: Literal["report_defect", "disclosed_limitation"]
    rationale: str
    reader_excerpts: tuple[str, ...]
    conclusion_scopes: tuple[Literal[
        "operating_asset_value", "equity_per_share_value", "funding_assessment",
        "opening_date_alignment", "research_uncertainty",
    ], ...]


class WireLifecycleVerification(WireVerificationOutput):
    issue_resolutions: tuple[WireIssueResolution, ...]
    finding_dispositions: tuple[WireFindingDisposition, ...]


class WireReportSection(_WireContract):
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]


class WireReportDraft(_WireContract):
    sections: tuple[WireReportSection, ...] = Field(min_length=1)
    limitations: tuple[str, ...]
    investment_view: Literal["favorable", "neutral", "cautious", "unrated"]


class WireCaseReportSection(WireReportSection):
    purpose: SectionPurpose


class WireCaseReportDraft(WireReportDraft):
    sections: tuple[WireCaseReportSection, ...] = Field(min_length=8, max_length=8)
    investment_view: Literal["unrated"]


def validate_strict_schema(schema: dict[str, Any]) -> None:
    """Reject schemas that cannot be sent as strict structured outputs."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("strict output schema root must be an object")

    def visit(value, path="$"):
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        if "default" in value:
            raise ValueError(f"strict output schema contains a default at {path}")
        if isinstance(value.get("pattern"), str):
            try:
                SchemaValidator(core_schema.str_schema(pattern=value["pattern"]))
            except SchemaError:
                raise ValueError(f"strict output schema has a nonportable pattern at {path}") from None
        if value.get("type") == "object" or "properties" in value:
            properties = value.get("properties")
            if not isinstance(properties, dict):
                raise ValueError(f"strict output schema has an open object at {path}")
            if value.get("additionalProperties") is not False:
                raise ValueError(f"strict output schema permits additional properties at {path}")
            required = value.get("required")
            if (not isinstance(required, list) or len(required) != len(set(required))
                    or set(required) != set(properties)):
                raise ValueError(f"strict output schema has optional object fields at {path}")
        for key, item in value.items():
            visit(item, f"{path}.{key}")

    visit(schema)


_ROLE_MODELS = dict.fromkeys(
    ("planner", "business", "accounting", "expectations", "management", "events", "challenger"),
    (AnalysisOutput, WireAnalysisOutput),
)
_ROLE_MODELS.update({
    "valuation": (ValuationProposal, WireValuationProposal),
    "verifier": (VerificationOutput, WireVerificationOutput),
    "editor": (ReportDraft, WireReportDraft),
})


@dataclass(frozen=True)
class WireCodec:
    domain_model: type[BaseModel]
    wire_model: type[BaseModel]
    output_schema: dict[str, Any]

    def decode(self, data: dict[str, Any]) -> dict[str, Any]:
        wire = self.wire_model.model_validate(data)
        decoded = wire.model_dump(mode="json")
        if self.domain_model is ValuationProposal:
            decoded["assumption_rationale"] = {
                entry["key"]: entry["value"] for entry in decoded["assumption_rationale"]}
            decoded["assumptions"] = {
                entry["key"]: entry["value"] for entry in decoded["assumptions"]}
        return self.domain_model.model_validate(decoded).model_dump(mode="json")


def codec_for(role: str, response_schema: dict[str, Any], *, valuation_method="fcff") -> WireCodec:
    """Resolve a known domain contract to its closed strict wire representation."""
    models = _ROLE_MODELS.get(role)
    if models is None:
        raise ValueError("research role has no strict wire schema")
    domain_model, wire_model = models
    if role == "valuation":
        if valuation_method not in {"fcff", "equity_fcfe"}:
            raise ValueError("unknown valuation method")
        if valuation_method == "equity_fcfe":
            wire_model = WireEquityValuationProposal
    if role == "verifier" and response_schema == InvestigationReview.model_json_schema():
        domain_model, wire_model = InvestigationReview, WireInvestigationReview
    if role == "verifier" and response_schema == ReaderVerification.model_json_schema():
        domain_model, wire_model = ReaderVerification, WireReaderVerification
    if role == "verifier" and response_schema == LifecycleVerification.model_json_schema():
        domain_model, wire_model = LifecycleVerification, WireLifecycleVerification
    if role == "editor" and response_schema == CaseReportDraft.model_json_schema():
        # Case-backed readers retain all eight purposes at the provider boundary.
        domain_model, wire_model = CaseReportDraft, WireCaseReportDraft
    if response_schema != domain_model.model_json_schema():
        raise ValueError("unknown research response schema")
    output_schema = wire_model.model_json_schema()
    validate_strict_schema(output_schema)
    return WireCodec(domain_model, wire_model, output_schema)


def system_instruction_suffix(role: str) -> str:
    return _VALUATION_WIRE_INSTRUCTION if role == "valuation" else ""
