"""Provider-facing explicit closure review, separate from legacy stage schemas."""

from .contracts import Contract
from .investigation import ClosureDecision


class InvestigationReview(Contract):
    decisions: tuple[ClosureDecision, ...] = ()
