"""Explicit, bounded SEC baseline collection; never an assertion of full coverage.

Transport is injected. A supplied instrument identity is required, so issuer,
currency and ADR details cannot be silently guessed from a ticker string.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .contracts import EvidenceSnapshot, ResearchRequest, SourceDocument
from .evidence import normalize_company_facts
from .sources import (
    DiscoveryStatus,
    PublicSourceService,
    SourceAccessError,
    is_exact_sec_archive_filing_url,
    normalize_public_https_url,
)
from .storage import parse_json


class PublicEvidenceCollector:
    def __init__(self, sources: PublicSourceService, *, max_filings: int = 16):
        if type(max_filings) is not int or not 1 <= max_filings <= 32:
            raise ValueError("max_filings must be between 1 and 32")
        self.sources = sources
        self.max_filings = max_filings

    def collect(self, request: ResearchRequest) -> EvidenceSnapshot:
        if request.instrument is None:
            raise ValueError("public collection requires explicit instrument identity")
        instrument = request.instrument
        gaps = [
            "SEC baseline only: transcripts, independent ecosystem evidence and news coverage are incomplete.",
            "Consensus, estimate revisions and dispersion are unavailable; do not infer them from guidance.",
            "Companyfacts does not replace filing footnotes, segment schedules or accounting reconciliation.",
            "Historical extraction uses filing accession dates; completeness of historical API contents is unproven.",
        ]
        documents = []
        by_accession = {}
        discovered = self.sources.discover_sec_filings(instrument.cik, cutoff=request.cutoff)
        if discovered.status == DiscoveryStatus.UNAVAILABLE:
            gaps.append(
                "critical: SEC filing discovery unavailable; this is not evidence of no events."
            )
        elif discovered.status == DiscoveryStatus.NO_EVENTS:
            gaps.append("critical: no eligible filings found in the returned SEC index.")
        if discovered.continuation_urls:
            gaps.append(
                "Older SEC index pages remain uncollected; five-year/eight-quarter coverage is unproven."
            )
        if len(discovered.filings) > self.max_filings:
            gaps.append("Filing acquisition capped; omitted filings remain coverage gaps.")
        for filing in discovered.filings[: self.max_filings]:
            if filing.cik != instrument.cik or filing.accepted_at > request.cutoff:
                raise ValueError("SEC discovery returned an ineligible issuer or filing")
            try:
                fetched = self.sources.fetch_document(filing.document_url, use_cache=True)
            except SourceAccessError:
                gaps.append(f"critical: filing acquisition unavailable: {filing.accession}")
                continue
            if not is_exact_sec_archive_filing_url(
                fetched.final_url,
                cik=instrument.cik,
                accession=filing.accession,
            ):
                gaps.append(f"critical: filing archive provenance invalid: {filing.accession}")
                continue
            if not fetched.text or fetched.text_sha256 is None:
                gaps.append(f"critical: filing full text unavailable: {filing.accession}")
                continue
            identifier = f"sec:{filing.accession}"
            document = SourceDocument(
                id=identifier,
                url=fetched.final_url,
                title=f"{request.ticker} {filing.form} {filing.filing_date}",
                publisher="SEC",
                retrieved_at=fetched.retrieved_at,
                published_at=filing.accepted_at,
                content=fetched.text,
                content_sha256=fetched.text_sha256,
                accession=filing.accession,
                origin_id=filing.accession,
                kind="filing",
            )
            documents.append(document)
            by_accession[filing.accession] = identifier
        facts = ()
        if documents:
            try:
                companyfacts_url = (
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{instrument.cik}.json"
                )
                fetched = self.sources.fetch_document(companyfacts_url)
                if normalize_public_https_url(fetched.final_url) != companyfacts_url:
                    raise ValueError("companyfacts final URL provenance mismatch")
                payload = parse_json(fetched.raw)
                if (
                    not isinstance(payload, dict)
                    or str(payload.get("cik", "")).zfill(10) != instrument.cik
                ):
                    raise ValueError("companyfacts issuer mismatch")
                facts = normalize_company_facts(
                    payload,
                    request.ticker,
                    request.cutoff,
                    sources={source.id: source for source in documents},
                    accession_source_ids=by_accession,
                )
                local_date = request.cutoff.astimezone(ZoneInfo(request.timezone)).date()
                facts = tuple(fact for fact in facts if fact.period_end <= local_date)
                # Preserve the exact API text for audit, but do not backdate its publication.
                if (
                    fetched.retrieved_at <= request.cutoff
                    and fetched.text is not None
                    and fetched.text_sha256
                ):
                    documents.append(
                        SourceDocument(
                            id="sec:companyfacts",
                            url=fetched.final_url,
                            title="SEC companyfacts acquisition snapshot (not independently dated evidence)",
                            publisher="SEC",
                            retrieved_at=fetched.retrieved_at,
                            content=fetched.text,
                            content_sha256=fetched.text_sha256,
                            kind="other",
                        )
                    )
                elif fetched.retrieved_at > request.cutoff:
                    gaps.append(
                        "critical: companyfacts audit snapshot was retrieved after the cutoff and omitted."
                    )
            except (SourceAccessError, ValueError, UnicodeError):
                gaps.append(
                    "critical: companyfacts unavailable or invalid; no replacement values invented."
                )
        if not facts:
            gaps.append("critical: no eligible normalized financial facts.")
        return EvidenceSnapshot(
            ticker=request.ticker,
            cutoff=request.cutoff,
            sources=tuple(documents),
            facts=facts,
            gaps=tuple(gaps),
            instrument=instrument,
        )
