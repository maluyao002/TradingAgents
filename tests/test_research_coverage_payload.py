"""Exact offline parity between standalone and engine coverage payloads."""

import pytest

from tests.test_research_bounded_finalization import BoundedFixture
from tests.test_research_engine import setup
from tradingagents.research.coverage_payload import build_coverage_payload
from tradingagents.research.engine import run_research
from tradingagents.research.services import ResearchServices


@pytest.mark.parametrize("policy", ["legacy-12", "packed-24"])
def test_helper_matches_engine_captured_coverage_payload_for_each_policy(tmp_path, policy):
    request, services = setup(tmp_path / policy)
    request = request.model_copy(update={
        "quality_revision": "evidence-led-bounded",
        "report_language": "English",
        "coverage_batch_policy": policy,
    })
    models = BoundedFixture(count=1)
    result = run_research(request, ResearchServices(services.evidence, models))

    assert result.stop_reason == "completed_needs_review"
    captured = next(payload for _, payload in models.calls
                    if payload["stage"] == "verify_report-coverage-0")
    rebuilt = build_coverage_payload(
        request,
        captured["research"]["limitation_review"],
        captured["research"]["rendered_reader"],
        stage=captured["stage"],
        role_call_index=captured["role_call_index"],
        language=captured["language"],
    )

    assert rebuilt == {key: value for key, value in captured.items()
                       if key not in {"max_output_tokens", "timeout_seconds"}}
    assert captured["timeout_seconds"] == request.budget.call_timeout_seconds
    assert captured["max_output_tokens"] == (6_000 if policy == "legacy-12" else 12_000)
