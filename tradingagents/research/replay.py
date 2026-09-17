"""Explicit offline services, suitable for frozen fixtures and saved responses."""

from collections import defaultdict
from copy import deepcopy

from .contracts import EvidenceSnapshot, ResearchRequest
from .services import ModelReply
from .storage import digest


class SnapshotEvidenceService:
    def __init__(self, snapshot: EvidenceSnapshot):
        self.snapshot = snapshot

    def collect(self, request: ResearchRequest):
        if (request.ticker != self.snapshot.ticker or request.cutoff != self.snapshot.cutoff):
            raise ValueError("snapshot identity/cutoff does not match request")
        return self.snapshot


class ReplayModelService:
    """Responses are test/replay inputs, not a live research capability."""

    kind = "replay"

    def __init__(self, responses: dict):
        self.responses = deepcopy(responses)
        self.identity = digest(self.responses)
        self.calls = []
        self.counts = defaultdict(int)

    def complete(self, role, payload, request):
        self.calls.append((role, deepcopy(payload)))
        index = payload.get("role_call_index", self.counts[role])
        choices = self.responses.get(role, [])
        if index >= len(choices):
            raise ValueError(f"no saved response for role {role}")
        self.counts[role] += 1
        return ModelReply.model_validate(choices[index])
