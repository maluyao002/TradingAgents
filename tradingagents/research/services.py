"""Injected boundaries: core research does not instantiate live clients implicitly."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import Contract, EvidenceSnapshot, ResearchRequest, Usage


class ModelReply(Contract):
    data: dict[str, Any]
    usage: Usage


class EvidenceService(Protocol):
    def collect(self, request: ResearchRequest) -> EvidenceSnapshot: ...


class ModelService(Protocol):
    def complete(self, role: str, payload: dict, request: ResearchRequest) -> ModelReply: ...


class ResearchStorage(Protocol):
    directory: Path

    def lock(self) -> AbstractContextManager: ...
    def save_stage(self, stage: str, inputs, output) -> None: ...
    def load_stage(self, stage: str, inputs): ...


class StorageFactory(Protocol):
    def __call__(self, directory: Path, identity: str) -> ResearchStorage: ...


@dataclass(frozen=True)
class ResearchServices:
    evidence: EvidenceService
    models: ModelService
    storage: StorageFactory | None = None
