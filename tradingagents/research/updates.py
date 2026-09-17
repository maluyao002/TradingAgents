"""Point-in-time prior eligibility and explicit research-change summaries."""

from .contracts import Dossier, EvidenceSnapshot, ResearchRequest
from .storage import digest, parse_json


def eligible_prior(content: bytes, request: ResearchRequest) -> Dossier:
    prior = Dossier.model_validate(parse_json(content))
    if prior.ticker != request.ticker:
        raise ValueError("prior dossier ticker mismatch")
    if prior.cutoff > request.cutoff or prior.created_at > request.cutoff:
        raise ValueError("prior dossier was not available at research cutoff")
    if prior.assessment.status != "accepted":
        raise ValueError("prior dossier is not accepted research")
    return prior


def describe_update(prior: Dossier | None, evidence: EvidenceSnapshot) -> dict:
    """Do not call equal evidence hashes equal company economics or fresh coverage."""
    if prior is None:
        return {"mode": "fresh", "prior_id": None}
    return {
        "mode": "update", "prior_id": prior.id,
        "prior_cutoff": prior.cutoff.isoformat(),
        "evidence_changed": prior.evidence_hash != digest(evidence),
        "prior_coverage_watermark": prior.coverage_watermark.isoformat()
        if prior.coverage_watermark else None,
        "coverage_advanced": False,
        "limitation": "Prior conclusions are historical hypotheses, not newly verified evidence. "
                      "Change attribution requires new evidence and assumption reconciliation.",
    }
