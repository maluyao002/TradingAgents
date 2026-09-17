"""Explicit, picklable live worker; never used by dry-run or offline replay."""

from dataclasses import dataclass
from pathlib import Path

from .collection import PublicEvidenceCollector
from .contracts import ResearchRequest
from .engine import run_research
from .models import CodexModelService
from .services import ResearchServices
from .sources import FileSourceCache, PublicSourceFetcher, PublicSourceService


@dataclass(frozen=True)
class PublicEvidenceService:
    sec_user_agent: str | None

    def collect(self, request: ResearchRequest):
        if not self.sec_user_agent or request.instrument is None:
            raise ValueError("public acquisition needs SEC identity and explicit instrument")
        # Called inside the engine's locked run, not before destination validation.
        fetcher = PublicSourceFetcher(
            sec_user_agent=self.sec_user_agent,
            cache=FileSourceCache(request.output_dir / "source-cache"))
        return PublicEvidenceCollector(PublicSourceService(fetcher)).collect(request)


@dataclass(frozen=True)
class CodexResearchWorker:
    home: Path
    sec_user_agent: str | None = None

    def __call__(self, request: ResearchRequest):
        if request.backend != "codex":
            raise ValueError("live worker requires explicit Codex backend")
        if request.evidence_path is None and (request.instrument is None or not self.sec_user_agent):
            raise ValueError("live acquisition requires instrument identity and SEC identification")
        with CodexModelService(self.home) as models:
            return run_research(request, ResearchServices(PublicEvidenceService(self.sec_user_agent), models))
