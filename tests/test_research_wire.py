from copy import deepcopy
from dataclasses import asdict, fields

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.test_research_valuation import _model
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


@pytest.mark.parametrize(("role", "domain"), [
    ("planner", AnalysisOutput),
    ("business", AnalysisOutput),
    ("valuation", ValuationProposal),
    ("verifier", VerificationOutput),
    ("editor", ReportDraft),
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
