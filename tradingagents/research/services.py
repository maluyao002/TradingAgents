"""Injected boundaries: core research does not instantiate live clients implicitly."""

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import Contract, EvidenceSnapshot, ResearchRequest, Usage


class ModelReply(Contract):
    data: dict[str, Any]
    usage: Usage


class EvidenceService(Protocol):
    def collect(self, request: ResearchRequest) -> EvidenceSnapshot: ...


class ModelService(Protocol):
    def complete(self, role: str, payload: dict, request: ResearchRequest) -> ModelReply: ...


@dataclass(frozen=True)
class ResearchServices:
    evidence: EvidenceService
    models: ModelService
