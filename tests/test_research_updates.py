import pytest

from tradingagents.research.contracts import Dossier, EvidenceSnapshot, ResearchRequest
from tradingagents.research.storage import canonical_json, digest
from tradingagents.research.updates import describe_update, eligible_prior


def test_prior_must_actually_exist_by_requested_cutoff(tmp_path):
    request = ResearchRequest(ticker="TEST", cutoff="2025-01-03T00:00:00Z", backend="api",
                              output_dir=tmp_path)
    evidence = EvidenceSnapshot(ticker="TEST", cutoff="2025-01-01T00:00:00Z")
    prior = Dossier(id="prior", ticker="TEST", cutoff=evidence.cutoff,
                    created_at="2025-01-02T00:00:00Z", evidence_hash=digest(evidence),
                    assessment={"status": "accepted", "investment_view": "neutral"})
    assert eligible_prior(canonical_json(prior), request) == prior
    assert not describe_update(prior, evidence)["evidence_changed"]
    assert not describe_update(prior, evidence)["coverage_advanced"]
    for changes in ({"ticker": "OTHER"}, {"created_at": "2025-01-04T00:00:00Z"},
                    {"assessment": {"status": "needs_review"}}):
        with pytest.raises(ValueError):
            eligible_prior(canonical_json({**prior.model_dump(mode="json"), **changes}), request)
