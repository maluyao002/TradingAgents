import hashlib
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tests.test_research_engine import replies
from tradingagents.research.collection import PublicEvidenceCollector
from tradingagents.research.contracts import ResearchRequest
from tradingagents.research.engine import run_research
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.replay import ReplayModelService, SnapshotEvidenceService
from tradingagents.research.services import ResearchServices
from tradingagents.research.sources import DiscoveryStatus, SecDiscoveryResult, SecFiling


def request(tmp_path):
    return ResearchRequest(
        ticker="TEST",
        cutoff="2026-01-02T00:00:00Z",
        backend="api",
        output_dir=tmp_path,
        instrument={
            "issuer": "Synthetic issuer",
            "cik": "0000000001",
            "exchange": "TEST",
            "share_class": "common",
            "quote_currency": "USD",
            "reporting_currency": "USD",
            "identity_source": "synthetic fixture",
        },
    )


class Sources:
    def __init__(
        self,
        status=DiscoveryStatus.AVAILABLE,
        cik=1,
        *,
        filing_final_url=None,
        companyfacts_final_url=None,
    ):
        self.status, self.cik, self.calls = status, cik, []
        self.filing_final_url = filing_final_url
        self.companyfacts_final_url = companyfacts_final_url

    def discover_sec_filings(self, cik, cutoff):
        filing = SecFiling(
            cik,
            "0000000001-25-000001",
            "10-K",
            date(2025, 2, 1),
            date(2024, 12, 31),
            datetime(2025, 2, 1, tzinfo=timezone.utc),
            "test.htm",
            ("https://www.sec.gov/Archives/edgar/data/1/000000000125000001/test.htm"),
        )
        return SecDiscoveryResult(
            self.status, (filing,) if self.status == DiscoveryStatus.AVAILABLE else ()
        )

    def fetch_document(self, url, **kwargs):
        self.calls.append(url)
        text = (
            "Synthetic annual filing"
            if url.endswith(".htm")
            else json.dumps(
                {
                    "cik": self.cik,
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "accn": "0000000001-25-000001",
                                            "start": "2024-01-01",
                                            "end": "2024-12-31",
                                            "val": 100,
                                            "filed": "2025-02-01",
                                        }
                                    ]
                                }
                            }
                        }
                    },
                }
            )
        )
        final_url = (
            self.filing_final_url
            if url.endswith(".htm") and self.filing_final_url is not None
            else self.companyfacts_final_url
            if not url.endswith(".htm") and self.companyfacts_final_url is not None
            else url
        )
        return SimpleNamespace(
            final_url=final_url,
            text=text,
            raw=text.encode(),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            retrieved_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )


def test_public_collection_is_explicit_bounded_and_point_in_time(tmp_path):
    sources = Sources()
    result = PublicEvidenceCollector(sources).collect(request(tmp_path))
    assert len(sources.calls) == 2
    assert result.facts[0].value == 100
    assert len(result.sources) == 1
    assert result.sources[0].accession == "0000000001-25-000001"
    assert any("audit snapshot was retrieved after" in gap for gap in result.gaps)
    assert any("Consensus" in gap for gap in result.gaps)
    assert validate_snapshot(result, request(tmp_path)) == result


def test_historical_collection_validates_through_engine_before_checkpoint(tmp_path):
    research_request = request(tmp_path).model_copy(update={"output_dir": tmp_path / "engine"})
    snapshot = PublicEvidenceCollector(Sources()).collect(research_request)
    services = ResearchServices(SnapshotEvidenceService(snapshot), ReplayModelService(replies()))

    result = run_research(research_request, services)

    assert result.stop_reason == "completed_needs_review"
    assert not any(source.id == "sec:companyfacts" for source in snapshot.sources)
    assert any("audit snapshot was retrieved after" in gap for gap in snapshot.gaps)


def test_wrong_issuer_companyfacts_never_becomes_research(tmp_path):
    result = PublicEvidenceCollector(Sources(cik=2)).collect(request(tmp_path))
    assert not result.facts
    assert any("companyfacts unavailable" in gap for gap in result.gaps)


def test_non_sec_filing_redirect_is_not_labeled_as_sec_evidence(tmp_path):
    result = PublicEvidenceCollector(
        Sources(filing_final_url="https://example.com/Archives/fake.htm")
    ).collect(request(tmp_path))
    assert not result.sources and not result.facts
    assert any("archive provenance invalid" in gap for gap in result.gaps)


def test_companyfacts_redirect_must_remain_on_exact_sec_api_url(tmp_path):
    result = PublicEvidenceCollector(
        Sources(companyfacts_final_url="https://example.com/companyfacts.json")
    ).collect(request(tmp_path))
    assert len(result.sources) == 1
    assert not result.facts
    assert any("companyfacts unavailable" in gap for gap in result.gaps)


def test_unavailable_discovery_is_not_no_events(tmp_path):
    sources = Sources(status=DiscoveryStatus.UNAVAILABLE)
    result = PublicEvidenceCollector(sources).collect(request(tmp_path))
    assert not sources.calls
    assert any("discovery unavailable" in gap for gap in result.gaps)


def test_collection_requires_explicit_instrument(tmp_path):
    with pytest.raises(ValueError, match="identity"):
        PublicEvidenceCollector(Sources()).collect(
            request(tmp_path).model_copy(update={"instrument": None})
        )
