from copy import deepcopy
from dataclasses import asdict, fields
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.test_research_equity_valuation import _model as equity_model
from tests.test_research_valuation import _model
from tradingagents.research.case_report import SECTION_PURPOSES, CaseReportDraft
from tradingagents.research.investigation_review import InvestigationReview
from tradingagents.research.stages import (
    AnalysisOutput,
    ReportDraft,
    ValuationProposal,
    VerificationOutput,
)
from tradingagents.research.valuation import FCFFModelInput, ForecastPeriod, ValuationUnits
from tradingagents.research.wire import (
    WireFCFFModelInput,
    WireForecastPeriod,
    WireValuationUnits,
    codec_for,
    validate_strict_schema,
)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_equity_wire_is_closed_and_round_trips_without_fcff_fields():
    codec = codec_for("valuation", ValuationProposal.model_json_schema(), valuation_method="equity_fcfe")
    validate_strict_schema(codec.output_schema)
    payload = _valuation_wire_payload()
    payload["model"] = asdict(equity_model())
    decoded = codec.decode(payload)
    assert "cost_of_equity" in decoded["model"]
    assert "net_debt" not in decoded["model"]
    assert "discount_rate" not in decoded["model"]
    with pytest.raises(ValueError):
        codec_for("valuation", ValuationProposal.model_json_schema()).decode(payload)


def test_investigation_wire_is_closed_and_does_not_replace_legacy_verification():
    codec = codec_for("verifier", InvestigationReview.model_json_schema())
    validate_strict_schema(codec.output_schema)
    assert codec.decode({"schema_version": 1, "decisions": []}) == {"schema_version": 1, "decisions": []}
    legacy = codec_for("verifier", VerificationOutput.model_json_schema())
    assert legacy.domain_model is VerificationOutput


@pytest.mark.parametrize(("role", "domain"), [
    ("planner", AnalysisOutput),
    ("business", AnalysisOutput),
    ("valuation", ValuationProposal),
    ("verifier", VerificationOutput),
    ("editor", ReportDraft),
    ("editor", CaseReportDraft),
])
def test_known_stage_wire_schemas_are_closed_required_and_default_free(role, domain):
    schema = codec_for(role, domain.model_json_schema()).output_schema
    validate_strict_schema(schema)
    assert all("default" not in node for node in _walk(schema))
    for node in _walk(schema):
        if node.get("type") == "object" or "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"value": {"type": "string"}},
     "required": [], "additionalProperties": False},
    {"type": "object", "properties": {"value": {"type": "string"}},
     "required": ["value"]},
    {"type": "object", "additionalProperties": {"type": "string"}},
    {"type": "object", "properties": {"value": {"type": "string", "default": "x"}},
     "required": ["value"], "additionalProperties": False},
])
def test_strict_schema_validation_rejects_optional_open_maps_and_defaults(schema):
    with pytest.raises(ValueError, match="strict output schema"):
        validate_strict_schema(schema)


def test_explicit_closed_schema_validates_but_is_not_an_known_stage_contract():
    schema = {"type": "object", "properties": {"value": {"type": "string"}},
              "required": ["value"], "additionalProperties": False}
    validate_strict_schema(schema)
    with pytest.raises(ValueError, match="unknown research response schema"):
        codec_for("planner", schema)


def _case_wire_payload():
    return {
        "schema_version": 1,
        "investment_view": "unrated",
        "limitations": ["Synthetic conditional operating report, not valuation."],
        "sections": [
            {"schema_version": 1, "title": purpose, "purpose": purpose,
             "text": "Synthetic discussion.", "evidence_ids": []}
            for purpose in SECTION_PURPOSES
        ],
    }


def test_case_editor_wire_preserves_purposes_and_legacy_contract():
    codec = codec_for("editor", CaseReportDraft.model_json_schema())
    payload = _case_wire_payload()
    assert codec.domain_model is CaseReportDraft
    assert codec.decode(payload) == payload
    assert codec_for("editor", ReportDraft.model_json_schema()).domain_model is ReportDraft
    with pytest.raises(ValueError, match="unknown research response schema"):
        codec_for("business", CaseReportDraft.model_json_schema())


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "unknown", "rated", "extra"])
def test_case_editor_wire_rejects_invalid_structure(invalid):
    payload = _case_wire_payload()
    if invalid == "missing":
        payload["sections"].pop()
    elif invalid == "duplicate":
        payload["sections"][-1]["purpose"] = payload["sections"][0]["purpose"]
    elif invalid == "unknown":
        payload["sections"][-1]["purpose"] = "unrecognized"
    elif invalid == "rated":
        payload["investment_view"] = "favorable"
    else:
        payload["sections"][0]["unrecognized"] = True
    with pytest.raises(ValidationError):
        codec_for("editor", CaseReportDraft.model_json_schema()).decode(payload)


def _valuation_wire_payload():
    return {
        "schema_version": 1,
        "model": asdict(_model()),
        "accounting_basis": "US GAAP",
        "assumption_rationale": [{"key": "discount_rate", "value": "Synthetic rationale"}],
        "assumptions": [{"key": "discount_rate", "value": {
            "schema_version": 1, "rationale": "Synthetic rationale",
            "evidence_ids": ["filing"], "kind": "assumption"}}],
        "evidence_ids": ["filing"],
        "unsupported_inputs": [],
        "scope_limitations": [],
    }


def test_valuation_wire_uses_typed_model_and_decodes_unique_entries_to_domain_maps():
    codec = codec_for("valuation", ValuationProposal.model_json_schema())
    decoded = codec.decode(_valuation_wire_payload())
    assert decoded["model"]["units"]["currency"] == "USD"
    assert decoded["assumption_rationale"] == {"discount_rate": "Synthetic rationale"}
    assert decoded["assumptions"]["discount_rate"]["evidence_ids"] == ["filing"]
    proposal = ValuationProposal.model_validate(decoded)
    assert TypeAdapter(FCFFModelInput).validate_python(proposal.model) == _model()


@pytest.mark.parametrize(("wire", "domain"), [
    (WireFCFFModelInput, FCFFModelInput),
    (WireForecastPeriod, ForecastPeriod),
    (WireValuationUnits, ValuationUnits),
])
def test_typed_valuation_wire_fields_cannot_drift_from_dataclasses(wire, domain):
    assert set(wire.model_fields) == {field.name for field in fields(domain)}


@pytest.mark.parametrize("field", ["assumption_rationale", "assumptions"])
def test_valuation_wire_rejects_duplicate_map_keys(field):
    codec = codec_for("valuation", ValuationProposal.model_json_schema())
    payload = _valuation_wire_payload()
    payload[field].append(deepcopy(payload[field][0]))
    with pytest.raises(ValidationError, match="unique keys"):
        codec.decode(payload)


def test_domain_contract_defaults_remain_available_for_replay():
    output = AnalysisOutput(summary="Synthetic")
    assert output.questions == output.findings == output.claims == ()


def test_valuation_decimal_wire_schema_has_no_generated_pattern_or_numeric_union():
    schema = codec_for("valuation", ValuationProposal.model_json_schema()).output_schema
    for model in ("WireFCFFModelInput", "WireForecastPeriod", "WireValuationUnits"):
        properties = schema["$defs"][model]["properties"]
        for value in properties.values():
            if "Finite decimal" in value.get("description", ""):
                assert value["type"] == "string"
                assert "pattern" not in value and "anyOf" not in value


def test_exact_financial_decimal_string_round_trips_without_float_loss():
    codec = codec_for("valuation", ValuationProposal.model_json_schema())
    payload = _valuation_wire_payload()
    exact = "12345678901234567890.12345678901234567890"
    payload["model"]["current_revenue"] = exact
    assert Decimal(codec.decode(payload)["model"]["current_revenue"]) == Decimal(exact)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "not-a-number", "--", True])
def test_string_wire_keeps_local_decimal_validation(invalid):
    payload = _valuation_wire_payload()
    payload["model"]["current_revenue"] = invalid
    with pytest.raises(ValidationError):
        codec_for("valuation", ValuationProposal.model_json_schema()).decode(payload)


def test_strict_schema_rejects_nonportable_decimal_lookahead_before_dispatch():
    schema = {"type": "object", "properties": {"value": {
        "type": "string", "pattern": r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"}},
        "required": ["value"], "additionalProperties": False}
    with pytest.raises(ValueError, match="nonportable pattern"):
        validate_strict_schema(schema)
