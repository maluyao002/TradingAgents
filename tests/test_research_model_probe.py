"""Offline tests for the one-call valuation model probe; no provider calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import research_model_probe as probe
from tests.test_research_valuation import _model
from tradingagents.research.contracts import (
    Assessment,
    Budget,
    EvidenceSnapshot,
    ResearchRequest,
    ResearchResult,
    SourceDocument,
    Usage,
)
from tradingagents.research.services import ModelReply
from tradingagents.research.stages import ValuationProposal
from tradingagents.research.storage import canonical_json, read_json


def _source(cutoff: str) -> SourceDocument:
    content = (
        "Reported revenue, working capital, debt, cash, diluted shares, margins, taxes, "
        "capital expenditures, stock compensation, common income, regulatory capital, "
        "management outlook, demand, expenses, risk, and reinvestment."
    )
    return SourceDocument(
        id="filing",
        url="https://example.test/filing",
        title="Synthetic filing",
        publisher="Synthetic issuer",
        retrieved_at=cutoff,
        published_at=cutoff,
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        kind="filing",
    )


def _write_snapshot(
    tmp_path: Path,
    *,
    cutoff: str = "2026-09-17T12:00:00+00:00",
    snapshot: EvidenceSnapshot | None = None,
) -> tuple[Path, EvidenceSnapshot]:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    value = snapshot or EvidenceSnapshot(
        ticker="TEST",
        cutoff=cutoff,
        sources=(_source(cutoff),),
        gaps=("Synthetic fixture only.",),
    )
    path = inputs / "evidence.json"
    path.write_bytes(canonical_json(value))
    return path, value


def _request(
    tmp_path: Path,
    evidence_path: Path,
    *,
    cutoff: str = "2026-09-17T12:00:00+00:00",
    budget: Budget | None = None,
    valuation_method: str = "fcff",
    share_count_basis: str = "point_in_time_diluted",
) -> ResearchRequest:
    return ResearchRequest(
        ticker="TEST",
        cutoff=cutoff,
        backend="codex",
        output_dir=tmp_path / "probe-output",
        evidence_path=evidence_path,
        mandate="Diagnose whether defensible forecast assumptions can be authored.",
        quality_revision="evidence-led" if valuation_method == "equity_fcfe" else "foundation",
        valuation_method=valuation_method,
        share_count_basis=share_count_basis,
        budget=budget or Budget(),
    )


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "isolated-home"
    home.mkdir()
    return home


def _null_proposal(reason: str = "Operating basis remains unsupported") -> dict:
    return ValuationProposal(
        model=None,
        unsupported_inputs=(reason,),
        scope_limitations=("One-call diagnostic only.",),
    ).model_dump(mode="json")


def _install_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data: dict | None = None,
    usage: Usage | None = None,
    error: Exception | None = None,
    exit_error: Exception | None = None,
    on_complete=None,
):
    calls = []

    class Service:
        identity = "synthetic-isolated-service"

        def __init__(self, home):
            self.home = home

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if exit_error is not None:
                raise exit_error
            return False

        def complete(self, role, payload, request):
            calls.append((role, payload, request))
            if on_complete is not None:
                on_complete()
            if error is not None:
                raise error
            return ModelReply(
                data=data if data is not None else _null_proposal(),
                usage=usage if usage is not None else Usage(input_tokens=120, output_tokens=30),
            )

    monkeypatch.setattr(probe, "CodexModelService", Service)
    return calls


@pytest.mark.parametrize("requested,expected", [(120, 120), (300, 300), (600, 600), (900, 600)])
def test_payload_call_deadline_is_explicit_and_capped(tmp_path: Path, requested: int, expected: int):
    evidence_path, snapshot = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path, budget=Budget(call_timeout_seconds=requested))
    payload, _ = probe.build_payload(request, snapshot)
    assert payload["timeout_seconds"] == expected


def test_one_fcff_call_has_targeted_context_authoring_policy_and_exact_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _snapshot = _write_snapshot(tmp_path)
    original = evidence_path.read_bytes()
    request = _request(tmp_path, evidence_path)
    calls = _install_service(monkeypatch)

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    role, payload, called_request = calls[0]
    assert role == "valuation" and called_request == request
    assert payload["timeout_seconds"] == 300
    assert payload["max_output_tokens"] == 16_000
    assert payload["prior_stage_context"].startswith("none")
    assert "research" not in payload
    policy = "".join(payload["analyst_authoring_policy"])
    assert "Historical opening anchors must be source-bound" in policy
    assert "reported or source-derived historical fact IDs" in policy
    assert "Source-derived anchors must retain their formula and input ancestry" in policy
    assert "AssumptionSupport.kind='reported' denotes historical fact binding" in policy
    assert "does not relabel a derived amount as directly issuer-reported" in policy
    assert "Forecast dates, period labels, units, scale choices" in policy
    assert "source need not literally contain the future forecast value" in policy
    assert "Never fabricate reported data, consensus, probabilities" in policy
    assert "Do not force a model" in policy
    assert policy in payload["system"]
    assert payload["evidence"]["retrieval_metadata"]["mode"] == "bounded_keyword_passages"
    assert payload["context_queries"][0] == request.mandate
    schema_text = json.dumps(payload["financial_model_schema"])
    assert "current_revenue" in schema_text
    assert "current_net_income" not in schema_text

    assert result.stop_reason == "model_probe_completed"
    assert result.assessment.status == "needs_review"
    assert result.assessment.investment_view == "unrated"
    assert evidence_path.read_bytes() == original
    assert read_json(request.output_dir / "proposal.json") == _null_proposal()
    calculation = read_json(request.output_dir / "calculation_result.json")
    assert calculation == {
        "status": "unavailable",
        "limitations": ["Operating basis remains unsupported"],
    }
    usage = read_json(request.output_dir / "usage.json")
    assert usage["actual_usage"]["input_tokens"] == 120
    assert usage["usage_total_unknown"] is False

    provenance = read_json(request.output_dir / "provenance.json")
    assert provenance["payload_sha256"] == hashlib.sha256(canonical_json(payload)).hexdigest()
    assert provenance["evidence_sha256"] == hashlib.sha256(original).hexdigest()
    assert provenance["evidence_sha256_after"] == provenance["evidence_sha256"]
    assert provenance["model_binding"] == {
        "role": "valuation",
        "model": request.models["valuation"].model,
        "effort": request.models["valuation"].effort,
        "service_identity": "synthetic-isolated-service",
    }
    assert provenance["result_binding"]["proposal_sha256"] == hashlib.sha256(
        (request.output_dir / "proposal.json").read_bytes()
    ).hexdigest()
    probe_record = read_json(request.output_dir / "probe.json")
    assert probe_record["call_count"] == 1
    assert probe_record["retry_count"] == 0
    assert probe_record["status"] == "completed"
    assert "not completed research" in probe_record["diagnostic_label"]
    preflight = probe_record["preflight"]
    components = preflight["estimated_input_components_utf8_bytes"]
    assert preflight["estimated_input_utf8_bytes"] == sum(components.values())
    assert preflight["estimated_admission_envelope"] == (
        preflight["estimated_input_utf8_bytes"] + 16_000
    )
    assert preflight["hard_output_cap"] is False
    assert preflight["admission_is_not_guarantee"] is True
    assert "provider protocol overhead" in preflight["estimate_basis"]
    assert "conservative_total_token_upper_bound" not in preflight
    assert "input_byte_token_upper_bound" not in preflight


def test_equity_schema_and_latest_quarter_share_proxy_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(
        tmp_path,
        evidence_path,
        valuation_method="equity_fcfe",
        share_count_basis="latest_quarter_diluted_proxy",
    )
    calls = _install_service(monkeypatch)

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert result.stop_reason == "model_probe_completed"
    payload = calls[0][1]
    schema_text = json.dumps(payload["financial_model_schema"])
    assert "current_net_income" in schema_text
    assert "required_capital_retention" in schema_text
    assert "current_revenue" not in schema_text
    assert payload["valuation_method"] == "equity_fcfe"
    assert payload["share_count_basis"] == "latest_quarter_diluted_proxy"
    assert any("weighted average latest quarter" in query for query in payload["context_queries"])


def _illustrative_fixture(tmp_path: Path) -> tuple[Path, ResearchRequest, dict]:
    cutoff = "2024-12-31T12:00:00+00:00"
    source = _source(cutoff)
    opening = {
        "current_revenue": ("revenue", 100),
        "current_working_capital": ("working_capital", 10),
        "net_debt": ("net_debt", 20),
        "current_diluted_shares": ("diluted_shares", 10),
    }
    fact_values = []
    for identifier, (metric, value) in opening.items():
        fact = {
            "id": identifier,
            "source_id": source.id,
            "metric": metric,
            "value": value,
            "unit": "shares" if "shares" in identifier else "USD",
            "currency": None if "shares" in identifier else "USD",
            "period_end": "2024-12-31",
            "basis": "US GAAP",
            "location": "synthetic fixture",
        }
        if identifier == "current_revenue":
            fact.update(period_start="2024-01-01", period_type="duration")
        fact_values.append(fact)
    snapshot = EvidenceSnapshot(
        ticker="TEST",
        cutoff=cutoff,
        sources=(source,),
        facts=tuple(fact_values),
    )
    evidence_path, _ = _write_snapshot(tmp_path, snapshot=snapshot)
    request = _request(tmp_path, evidence_path, cutoff=cutoff).model_copy(
        update={"quality_revision": "evidence-led"}
    )
    model = _model()
    required = {
        *opening,
        "discount_rate",
        "terminal_growth",
        "units.currency",
        "units.amount_scale",
        "units.share_scale",
    }
    for index, period in enumerate(model.periods):
        required.update(
            f"periods.{index}.{field.name}"
            for field in fields(period)
            if field.name not in {"label", "discount_years"}
        )
    assumptions = {
        name: {
            "kind": "reported" if name in opening else "assumption",
            "rationale": "Exact anchor" if name in opening else "Synthetic forecast choice",
            "evidence_ids": [name if name in opening else source.id],
        }
        for name in required
    }
    proposal_data = ValuationProposal(
        model=asdict(model),
        evidence_ids=(source.id,),
        assumptions=assumptions,
        accounting_basis="US GAAP",
        scope_limitations=("Synthetic model for deterministic validation only.",),
    ).model_dump(mode="json")
    return evidence_path, request, proposal_data


def test_valid_proposal_runs_deterministic_calculation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _evidence_path, request, proposal_data = _illustrative_fixture(tmp_path)
    calls = _install_service(monkeypatch, data=proposal_data)

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_completed"
    calculation = read_json(request.output_dir / "calculation_result.json")
    assert calculation["status"] == "illustrative"
    assert calculation["valuation_method"] == "fcff"
    assert calculation["opening_input_bindings"]["current_revenue"]["fact_id"] == (
        "current_revenue"
    )


def test_invalid_output_still_persists_proposal_calculation_and_actual_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    invalid = {"_invalid_model_response": True}
    calls = _install_service(
        monkeypatch,
        data=invalid,
        usage=Usage(input_tokens=12, output_tokens=3, cached_input_tokens=2),
    )

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_invalid_output"
    assert result.usage.total_tokens == 15
    assert read_json(request.output_dir / "proposal.json") == invalid
    assert read_json(request.output_dir / "calculation_result.json") == {
        "status": "not_calculated",
        "reason_code": "proposal_validation_failed",
    }
    usage = read_json(request.output_dir / "usage.json")
    assert usage["actual_usage"]["input_tokens"] == 12
    assert usage["actual_usage"]["output_tokens"] == 3
    assert usage["known_token_lower_bound"] == 15
    assert read_json(request.output_dir / "provenance.json")["result_binding"]


def test_unknown_usage_remains_unknown_and_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    calls = _install_service(
        monkeypatch,
        data=_null_proposal(),
        usage=Usage(complete=False),
    )

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert not result.usage.complete
    assert result.stop_reason == "model_probe_usage_unknown"
    usage = read_json(request.output_dir / "usage.json")
    assert usage["usage_total_unknown"] is True
    assert usage["known_token_lower_bound"] == 0
    assert usage["observed_budget_overshoot"] is None
    record = read_json(request.output_dir / "probe.json")
    assert record["retry_count"] == 0
    assert record["status"] == "completed_with_usage_unknown"


def test_provider_error_is_redacted_and_unsettled_usage_remains_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    secret = "raw-upstream-secret-must-not-be-persisted"
    calls = _install_service(monkeypatch, error=RuntimeError(secret))

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_call_failed"
    assert not result.usage.complete
    persisted = "".join(path.read_text() for path in request.output_dir.iterdir())
    assert secret not in persisted
    assert "RuntimeError" not in persisted
    assert read_json(request.output_dir / "usage.json")["usage_total_unknown"] is True


def test_cleanup_failure_after_known_reply_preserves_complete_usage_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    secret = "cleanup-upstream-secret-must-not-be-persisted"
    calls = _install_service(
        monkeypatch,
        data=_null_proposal(),
        usage=Usage(input_tokens=321, output_tokens=45, cached_input_tokens=20),
        exit_error=RuntimeError(secret),
    )

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_call_failed"
    assert result.usage.complete is True
    assert result.usage.total_tokens == 366
    usage = read_json(request.output_dir / "usage.json")
    assert usage["actual_usage"]["input_tokens"] == 321
    assert usage["actual_usage"]["output_tokens"] == 45
    assert usage["known_token_lower_bound"] == 366
    assert usage["usage_total_unknown"] is False
    record = read_json(request.output_dir / "probe.json")
    assert record["status"] == "failed"
    assert record["failure_code"] == "model_call_failed"
    assert record["call_count"] == 1
    assert record["retry_count"] == 0
    assert read_json(request.output_dir / "proposal.json") == _null_proposal()
    persisted = "".join(path.read_text() for path in request.output_dir.iterdir())
    assert secret not in persisted
    assert "RuntimeError" not in persisted


def test_conservative_token_preflight_blocks_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    budget = Budget(
        wall_seconds=600,
        reserve_seconds=10,
        total_tokens=1_000,
        reserve_tokens=100,
        call_timeout_seconds=300,
    )
    request = _request(tmp_path, evidence_path, budget=budget)
    calls = _install_service(monkeypatch)

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert calls == []
    assert result.stop_reason == "model_probe_preflight_failed"
    assert result.usage == Usage()
    record = read_json(request.output_dir / "probe.json")
    assert record["call_count"] == 0
    assert record["preflight"]["admitted"] is False
    assert record["preflight"]["estimated_admission_envelope"] > 1_000
    assert record["preflight"]["hard_output_cap"] is False
    assert record["preflight"]["admission_is_not_guarantee"] is True


def test_observed_output_overshoot_is_reported_without_hiding_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    calls = _install_service(
        monkeypatch,
        usage=Usage(input_tokens=100, output_tokens=20_000),
    )

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_budget_overshoot"
    usage = read_json(request.output_dir / "usage.json")
    assert usage["actual_usage"]["output_tokens"] == 20_000
    assert usage["observed_budget_overshoot"] is True
    record = read_json(request.output_dir / "probe.json")
    assert record["status"] == "completed_with_budget_overshoot"
    assert read_json(request.output_dir / "calculation_result.json")["status"] == "unavailable"


def test_source_hash_is_rechecked_after_the_only_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)

    def mutate_source():
        evidence_path.write_bytes(evidence_path.read_bytes() + b" ")

    calls = _install_service(monkeypatch, on_complete=mutate_source)

    result = probe.ModelProbeWorker(_home(tmp_path))(request)

    assert len(calls) == 1
    assert result.stop_reason == "model_probe_source_changed"
    provenance = read_json(request.output_dir / "provenance.json")
    assert provenance["evidence_sha256"] != provenance["evidence_sha256_after"]
    assert read_json(request.output_dir / "probe.json")["failure_code"] == (
        "frozen_evidence_changed"
    )


def test_unsafe_or_existing_output_is_rejected_before_any_write_or_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    calls = _install_service(monkeypatch)
    unsafe = evidence_path.parent / "probe-output"
    request = _request(tmp_path, evidence_path).model_copy(update={"output_dir": unsafe})

    with pytest.raises(probe.ModelProbeError, match="inside frozen evidence"):
        probe.ModelProbeWorker(_home(tmp_path))(request)
    assert calls == []
    assert not unsafe.exists()

    existing = tmp_path / "existing-output"
    existing.mkdir()
    request = request.model_copy(update={"output_dir": existing})
    with pytest.raises(probe.ModelProbeError, match="must not already exist"):
        probe.ModelProbeWorker(tmp_path / "isolated-home")(request)
    assert list(existing.iterdir()) == []


def _cli_probe_result(
    output: Path, request: ResearchRequest, mutation: str | None = None
) -> ResearchResult:
    output.mkdir()
    artifacts = {}
    hashes = {}
    if mutation != "empty":
        for index, name in enumerate(probe.PROBE_ARTIFACT_NAMES):
            path = output / name
            path.write_bytes(canonical_json({"artifact": name, "index": index}))
            artifacts[name] = str(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if mutation == "missing":
        artifacts.pop("provenance.json")
        hashes.pop("provenance.json")
    elif mutation == "missing_file":
        (output / "usage.json").unlink()
    elif mutation == "mismatched_hash":
        hashes["proposal.json"] = "0" * 64
    elif mutation == "mismatched_path":
        outside = output.parent / "outside-probe.json"
        outside.write_bytes((output / "probe.json").read_bytes())
        artifacts["probe.json"] = str(outside)
        hashes["probe.json"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    return ResearchResult(
        ticker=request.ticker,
        cutoff=request.cutoff,
        artifacts=artifacts,
        artifact_hashes=hashes,
        assessment=Assessment(status="needs_review"),
        usage=Usage(input_tokens=10, output_tokens=2),
        stop_reason="model_probe_completed",
    )


@pytest.mark.parametrize("call_seconds,supervisor_seconds", [(300, 360), (600, 600), (900, 600)])
def test_cli_requires_live_opt_in_and_caps_supervision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    call_seconds: int, supervisor_seconds: int,
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path, budget=Budget(call_timeout_seconds=call_seconds))
    home = _home(tmp_path)
    config = tmp_path / "request.json"
    config.write_bytes(canonical_json(request))
    output = tmp_path / "cli-probe"

    assert probe.main(
        ["--config", str(config), "--output", str(output), "--codex-home", str(home)]
    ) == 2
    assert not output.exists()

    captured = {}

    def fake_supervisor(worker, called_request, *, timeout_seconds):
        captured.update(
            worker=worker,
            request=called_request,
            timeout_seconds=timeout_seconds,
        )
        result = _cli_probe_result(output, called_request)
        return SimpleNamespace(status="completed", code="completed", result=result)

    monkeypatch.setattr(probe, "run_supervised", fake_supervisor)
    assert probe.main(
        [
            "--config",
            str(config),
            "--output",
            str(output),
            "--codex-home",
            str(home),
            "--allow-live",
        ]
    ) == 0
    assert captured["timeout_seconds"] == supervisor_seconds
    assert captured["request"].output_dir == output.resolve()
    assert isinstance(captured["worker"], probe.ModelProbeWorker)
    assert output.is_dir()
    assert {path.name for path in output.iterdir()} == set(probe.PROBE_ARTIFACT_NAMES)
    assert "model_probe_completed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mutation",
    ["empty", "missing", "missing_file", "mismatched_hash", "mismatched_path"],
)
def test_cli_rejects_completed_result_without_exact_hash_bound_five_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    evidence_path, _ = _write_snapshot(tmp_path)
    request = _request(tmp_path, evidence_path)
    home = _home(tmp_path)
    config = tmp_path / "request.json"
    config.write_bytes(canonical_json(request))
    output = tmp_path / "cli-probe"

    def fake_supervisor(worker, called_request, *, timeout_seconds):
        assert timeout_seconds == 360
        result = _cli_probe_result(output, called_request, mutation)
        return SimpleNamespace(status="completed", code="completed", result=result)

    monkeypatch.setattr(probe, "run_supervised", fake_supervisor)
    assert probe.main(
        [
            "--config",
            str(config),
            "--output",
            str(output),
            "--codex-home",
            str(home),
            "--allow-live",
        ]
    ) == 1
