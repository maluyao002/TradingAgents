"""M0 contract boundaries for the standalone research workflow."""

from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tradingagents.research.contracts import (
    Assessment,
    Budget,
    EvidenceSnapshot,
    Expectation,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
    Usage,
    default_roles,
)


def request_data(**overrides):
    data = {
        "ticker": "AMD",
        "cutoff": "2026-09-17T12:00:00+00:00",
        "backend": "api",
        "output_dir": "research-output",
        "models": {role: setting.model_dump() for role, setting in default_roles().items()},
    }
    data.update(overrides)
    return data


def source_data(**overrides):
    data = {
        "id": "source-1",
        "url": "https://example.test/filing",
        "title": "Filing",
        "publisher": "Issuer",
        "retrieved_at": "2026-09-16T12:00:00+00:00",
        "published_at": "2026-09-15T12:00:00+00:00",
        "content": "reported revenue",
        "content_sha256": sha256(b"reported revenue").hexdigest(),
    }
    data.update(overrides)
    return data


def fact_data(**overrides):
    data = {
        "id": "fact-1",
        "source_id": "source-1",
        "metric": "revenue",
        "value": "100",
        "unit": "USD",
        "period_end": "2026-06-30",
        "basis": "GAAP",
        "location": "page 1",
    }
    data.update(overrides)
    return data


def test_request_round_trip_is_versioned_and_requires_aware_cutoff():
    request = ResearchRequest.model_validate(request_data())
    assert ResearchRequest.model_validate_json(request.model_dump_json()) == request

    for invalid in ({"schema_version": 2}, {"unexpected": True}, {"cutoff": "2026-09-17T12:00:00"}):
        with pytest.raises(ValidationError):
            ResearchRequest.model_validate(request_data(**invalid))


def test_default_research_roles_follow_balanced_profile():
    roles = default_roles()
    assert {role: (setting.model, setting.effort) for role, setting in roles.items()} == {
        "planner": ("gpt-6-sol", "high"),
        "business": ("gpt-6-sol", "high"),
        "accounting": ("gpt-6-sol", "high"),
        "expectations": ("gpt-6-sol", "high"),
        "management": ("gpt-6-sol", "high"),
        "valuation": ("gpt-6-sol", "high"),
        "events": ("gpt-5.6-terra", "medium"),
        "challenger": ("gpt-6-astra", "high"),
        "verifier": ("gpt-6-sol", "high"),
        "editor": ("gpt-6-astra", "high"),
    }


def test_request_requires_exact_roles_and_replay_evidence():
    missing_role = deepcopy(request_data()["models"])
    missing_role.pop("editor")
    with pytest.raises(ValidationError, match="explicit assignments"):
        ResearchRequest.model_validate(request_data(models=missing_role))
    with pytest.raises(ValidationError, match="replay requires"):
        ResearchRequest.model_validate(request_data(backend="replay"))
    assert (
        ResearchRequest.model_validate(
            request_data(backend="replay", evidence_path="evidence.json")
        ).backend
        == "replay"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"wall_seconds": 100, "reserve_seconds": 100},
        {"total_tokens": 100, "reserve_tokens": 100},
        {"wall_seconds": 100, "call_timeout_seconds": 101},
        {"wall_seconds": 1.0},
    ],
)
def test_budget_rejects_invalid_allowances(values):
    with pytest.raises(ValidationError):
        Budget(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"input_tokens": -1},
        {"input_tokens": 3, "cached_input_tokens": 4},
        {"output_tokens": 3, "reasoning_output_tokens": 4},
        {"input_tokens": 1.0},
    ],
)
def test_usage_rejects_invalid_accounting(values):
    with pytest.raises(ValidationError):
        Usage(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"value": "NaN"},
        {"scale": "0"},
        {"period_start": "2026-07-01"},
        {"inputs": ["fact-0"]},
        {"formula": "a - b"},
    ],
)
def test_financial_fact_rejects_invalid_accounting_values(values):
    with pytest.raises(ValidationError):
        FinancialFact.model_validate(fact_data(**values))


def test_evidence_requires_resolvable_references_and_expectation_support():
    source = SourceDocument.model_validate(source_data())
    fact = FinancialFact.model_validate(fact_data())
    expectation = Expectation.model_validate(
        {
            "id": "expectation-1",
            "metric": "revenue",
            "kind": "guidance",
            "period_end": "2026-12-31",
            "basis": "GAAP",
            "unit": "USD",
            "value": "120",
            "source_ids": ["source-1"],
            "as_of": "2026-09-16T12:00:00+00:00",
        }
    )
    assert EvidenceSnapshot(
        ticker="AMD",
        cutoff="2026-09-17T12:00:00+00:00",
        sources=[source],
        facts=[fact],
        expectations=[expectation],
    )

    with pytest.raises(ValidationError, match="unknown source reference"):
        EvidenceSnapshot(ticker="AMD", cutoff="2026-09-17T12:00:00+00:00", facts=[fact])
    with pytest.raises(ValidationError, match="external expectations require evidence"):
        Expectation.model_validate(
            {
                "id": "expectation-2",
                "metric": "revenue",
                "kind": "guidance",
                "period_end": "2026-12-31",
                "basis": "GAAP",
                "unit": "USD",
                "value": "120",
                "as_of": "2026-09-16T12:00:00+00:00",
            }
        )
    with pytest.raises(ValidationError, match="one contributor is not consensus"):
        Expectation.model_validate(
            {
                "id": "expectation-3",
                "metric": "revenue",
                "kind": "consensus",
                "period_end": "2026-12-31",
                "basis": "GAAP",
                "unit": "USD",
                "value": "120",
                "source_ids": ["source-1"],
                "contributors": 1,
                "as_of": "2026-09-16T12:00:00+00:00",
            }
        )


def test_assessment_fails_closed_when_not_accepted():
    with pytest.raises(ValidationError, match="unaccepted research"):
        Assessment(status="needs_review", investment_view="favorable")


def test_source_hash_cutoff_unknown_availability_and_operand_cycle():
    with pytest.raises(ValidationError, match="hash mismatch"):
        SourceDocument.model_validate(source_data(content="changed"))
    future = SourceDocument.model_validate(source_data(published_at="2027-01-01T00:00:00Z"))
    with pytest.raises(ValidationError, match="after evidence cutoff"):
        EvidenceSnapshot(ticker="AMD", cutoff="2026-09-17T00:00:00Z", sources=[future])
    unknown = SourceDocument.model_validate(source_data(published_at=None))
    fact = FinancialFact.model_validate(fact_data())
    with pytest.raises(ValidationError, match="known source publication"):
        EvidenceSnapshot(ticker="AMD", cutoff="2026-09-17T00:00:00Z", sources=[unknown], facts=[fact])
    source = SourceDocument.model_validate(source_data())
    a = FinancialFact.model_validate(fact_data(id="a", inputs=["b"], formula="b"))
    b = FinancialFact.model_validate(fact_data(id="b", inputs=["a"], formula="a"))
    with pytest.raises(ValidationError, match="cycle"):
        EvidenceSnapshot(ticker="AMD", cutoff="2026-09-17T00:00:00Z", sources=[source], facts=[a, b])
