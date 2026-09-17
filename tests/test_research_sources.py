"""Offline coverage for bounded, SSRF-safe public research acquisition."""

from __future__ import annotations

import hashlib
import json
import ssl
from collections import deque
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.research.sources import (
    DiscoveryStatus,
    FetchPolicy,
    FileSourceCache,
    MemorySourceCache,
    PinnedHTTPSClientTransport,
    PublicSourceFetcher,
    PublicSourceService,
    SecRateLimiter,
    SourceAccessError,
    TransportResponse,
    discover_ir_links,
    is_exact_sec_archive_filing_url,
    normalize_public_https_url,
    parse_sec_accession_list,
    parse_sec_submissions,
)

PUBLIC_A = "93.184.216.34"
PUBLIC_B = "8.8.8.8"
UTC = timezone.utc


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []
        self.wall = datetime(2026, 9, 17, 12, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        assert 0 <= seconds <= 30
        self.sleeps.append(seconds)
        self.value += seconds

    def now(self) -> datetime:
        return self.wall + timedelta(seconds=self.value)


class FakeResolver:
    def __init__(self, answers: dict[str, tuple[str, ...]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.answers[host]


class FakeTransport:
    def __init__(self, *responses: TransportResponse | Exception) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, target, request_target, headers, *, timeout_seconds, max_bytes):
        self.calls.append(
            {
                "target": target,
                "request_target": request_target,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
                "max_bytes": max_bytes,
            }
        )
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def response(
    status: int = 200,
    body: bytes = b"ok",
    **headers: str,
) -> TransportResponse:
    return TransportResponse(status, headers or {"content-type": "text/plain"}, body)


def fetcher(
    transport: FakeTransport,
    *,
    answers: dict[str, tuple[str, ...]] | None = None,
    clock: FakeClock | None = None,
    cache=None,
    policy: FetchPolicy | None = None,
    sec_user_agent: str | None = "Example Research contact@example.com",
    limiter: SecRateLimiter | None = None,
) -> tuple[PublicSourceFetcher, FakeResolver, FakeClock]:
    clock = clock or FakeClock()
    resolver = FakeResolver(answers or {"example.com": (PUBLIC_A,)})
    return (
        PublicSourceFetcher(
            resolver=resolver,
            transport=transport,
            clock=clock,
            cache=cache,
            policy=policy,
            sec_user_agent=sec_user_agent,
            sec_limiter=limiter,
        ),
        resolver,
        clock,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a",
        "https://user:password@example.com/a",
        "https://example.com:8443/a",
        "https://example.com/a#fragment",
        "https://example.com\\@127.0.0.1/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
    ],
)
def test_url_policy_rejects_non_https_credentials_ports_and_local_literals(url):
    with pytest.raises(SourceAccessError):
        normalize_public_https_url(url)


@pytest.mark.unit
def test_production_transport_rejects_insecure_tls_context():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="certificate verification"):
        PinnedHTTPSClientTransport(ssl_context=context)


@pytest.mark.unit
@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("10.0.0.1",),
        (PUBLIC_A, "192.168.1.4"),
        ("::ffff:127.0.0.1",),
    ],
)
def test_dns_policy_fails_closed_for_private_or_mixed_answers(addresses):
    client, _resolver, _clock = fetcher(
        FakeTransport(response()), answers={"example.com": addresses}
    )
    with pytest.raises(SourceAccessError, match="non-public") as failure:
        client.fetch("https://example.com/report")
    assert failure.value.code == "unsafe_destination"


@pytest.mark.unit
def test_validated_ip_is_pinned_with_original_host_and_no_transport_dns():
    transport = FakeTransport(response(body=b"hello"))
    client, resolver, _clock = fetcher(transport)

    fetched = client.fetch("https://EXAMPLE.com/report?q=1")

    assert resolver.calls == [("example.com", 443)]
    call = transport.calls[0]
    assert call["target"].ip_address == PUBLIC_A
    assert call["target"].host == "example.com"
    assert call["request_target"] == "/report?q=1"
    assert call["headers"]["Host"] == "example.com"
    assert fetched.raw == b"hello"


@pytest.mark.unit
def test_redirect_hop_is_normalized_reresolved_and_private_target_rejected():
    transport = FakeTransport(response(302, b"", location="https://private.test/next"))
    client, resolver, _clock = fetcher(
        transport,
        answers={"example.com": (PUBLIC_A,), "private.test": ("10.0.0.2",)},
    )

    with pytest.raises(SourceAccessError) as failure:
        client.fetch("https://example.com/start")

    assert failure.value.code == "unsafe_destination"
    assert resolver.calls == [("example.com", 443), ("private.test", 443)]
    assert len(transport.calls) == 1


@pytest.mark.unit
def test_redirect_to_public_host_pins_each_hop_and_retains_chain():
    transport = FakeTransport(
        response(301, b"", location="https://cdn.example.net/final"),
        response(body=b"done", **{"content-type": "text/plain"}),
    )
    client, _resolver, _clock = fetcher(
        transport,
        answers={"example.com": (PUBLIC_A,), "cdn.example.net": (PUBLIC_B,)},
    )

    fetched = client.fetch("https://example.com/start")

    assert [call["target"].ip_address for call in transport.calls] == [PUBLIC_A, PUBLIC_B]
    assert fetched.final_url == "https://cdn.example.net/final"
    assert fetched.redirects == ("https://cdn.example.net/final",)


@pytest.mark.unit
def test_sec_requires_identified_agent_and_does_not_expose_it_in_failure():
    transport = FakeTransport(response())
    client, _resolver, _clock = fetcher(
        transport,
        answers={"data.sec.gov": (PUBLIC_A,)},
        sec_user_agent=None,
    )
    with pytest.raises(SourceAccessError) as failure:
        client.fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert failure.value.code == "sec_identity_required"
    assert not transport.calls

    secret_marker = "Private Team contact@example.com"
    failing = FakeTransport(SourceAccessError("transport_unavailable", secret_marker))
    identified, _resolver, _clock = fetcher(
        failing,
        answers={"data.sec.gov": (PUBLIC_A,)},
        sec_user_agent=secret_marker,
        policy=FetchPolicy(max_attempts=1),
    )
    with pytest.raises(SourceAccessError) as redacted:
        identified.fetch("https://data.sec.gov/a")
    assert secret_marker not in str(redacted.value)


@pytest.mark.unit
def test_shared_sec_limiter_spaces_every_attempt_at_two_per_second():
    clock = FakeClock()
    limiter = SecRateLimiter(clock)
    first_transport = FakeTransport(response())
    second_transport = FakeTransport(response())
    first, _, _ = fetcher(
        first_transport,
        answers={"www.sec.gov": (PUBLIC_A,)},
        clock=clock,
        limiter=limiter,
    )
    second, _, _ = fetcher(
        second_transport,
        answers={"data.sec.gov": (PUBLIC_B,)},
        clock=clock,
        limiter=limiter,
    )

    first.fetch("https://www.sec.gov/a")
    second.fetch("https://data.sec.gov/b")

    assert clock.sleeps == [0.5]


@pytest.mark.unit
def test_limiter_wait_cannot_start_request_after_total_deadline():
    clock = FakeClock()
    limiter = SecRateLimiter(clock)
    limiter.acquire()  # Reserve the first half-second slot for another SEC request.
    transport = FakeTransport(response())
    client, _, _ = fetcher(
        transport,
        answers={"www.sec.gov": (PUBLIC_A,)},
        clock=clock,
        limiter=limiter,
        policy=FetchPolicy(request_timeout_seconds=0.2, total_timeout_seconds=0.25),
    )
    with pytest.raises(SourceAccessError) as failure:
        client.fetch("https://www.sec.gov/a")
    assert failure.value.code == "timeout"
    assert not transport.calls


@pytest.mark.unit
def test_retry_backoff_and_transport_bounds_are_capped():
    transport = FakeTransport(
        response(429, b"", **{"retry-after": "9999"}),
        response(503, b""),
        response(body=b"ready"),
    )
    policy = FetchPolicy(
        max_bytes=7,
        request_timeout_seconds=6,
        total_timeout_seconds=20,
        max_attempts=3,
        initial_backoff_seconds=1,
        max_backoff_seconds=3,
    )
    client, _resolver, clock = fetcher(transport, policy=policy)

    assert client.fetch("https://example.com/a").raw == b"ready"
    assert clock.sleeps == [3, 2]
    assert all(call["max_bytes"] == 7 for call in transport.calls)
    assert all(0 < call["timeout_seconds"] <= 6 for call in transport.calls)


@pytest.mark.unit
def test_injected_transport_cannot_bypass_body_limit():
    client, _resolver, _clock = fetcher(
        FakeTransport(response(body=b"12345")),
        policy=FetchPolicy(max_bytes=4),
    )
    with pytest.raises(SourceAccessError) as failure:
        client.fetch("https://example.com/a")
    assert failure.value.code == "response_too_large"


@pytest.mark.unit
def test_html_extraction_is_content_bound_and_excludes_active_text():
    raw = b"<html><body><h1>Quarterly Results</h1><script>ignore me</script><p>Revenue &amp; margin</p></body></html>"
    transport = FakeTransport(response(body=raw, **{"content-type": "text/html; charset=utf-8"}))
    client, _resolver, _clock = fetcher(transport)

    fetched = client.fetch("https://example.com/results")

    assert fetched.text == "Quarterly Results\nRevenue & margin"
    assert fetched.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert fetched.text_sha256 == hashlib.sha256(fetched.text.encode()).hexdigest()
    assert "ignore me" not in fetched.text


@pytest.mark.unit
def test_memory_and_file_cache_return_exact_content_without_transport(tmp_path):
    raw = b"filing bytes"
    for cache in (MemorySourceCache(), FileSourceCache(tmp_path / "cache")):
        transport = FakeTransport(response(body=raw))
        client, _resolver, _clock = fetcher(transport, cache=cache)
        first = client.fetch("https://example.com/filing")
        second = client.fetch("https://example.com/filing", use_cache=True)
        assert first.raw_sha256 == second.raw_sha256
        assert second.raw == raw
        assert second.from_cache is True
        assert len(transport.calls) == 1


@pytest.mark.unit
def test_file_cache_reextracts_raw_and_rejects_cross_bound_text_blob(tmp_path):
    cache_root = tmp_path / "cache"
    cache = FileSourceCache(cache_root)
    first, _, _ = fetcher(
        FakeTransport(response(body=b"<p>First filing</p>", **{"content-type": "text/html"})),
        cache=cache,
    )
    second, _, _ = fetcher(
        FakeTransport(response(body=b"<p>Second filing</p>", **{"content-type": "text/html"})),
        cache=cache,
    )
    first.fetch("https://example.com/first")
    second.fetch("https://example.com/second")

    indexes = [json.loads(path.read_text()) for path in (cache_root / "indexes").iterdir()]
    first_index = next(item for item in indexes if item["requested_url"].endswith("/first"))
    second_index = next(item for item in indexes if item["requested_url"].endswith("/second"))
    first_path = next(
        path
        for path in (cache_root / "indexes").iterdir()
        if json.loads(path.read_text())["requested_url"].endswith("/first")
    )
    first_index["text_sha256"] = second_index["text_sha256"]
    first_path.write_text(json.dumps(first_index, sort_keys=True, separators=(",", ":")))

    with pytest.raises(SourceAccessError, match="not bound") as failure:
        cache.get("https://example.com/first")
    assert failure.value.code == "cache_corrupt"


@pytest.mark.unit
def test_exact_sec_archive_url_requires_host_cik_accession_and_document_path():
    accession = "0000320193-26-000001"
    valid = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/first.htm"
    assert is_exact_sec_archive_filing_url(valid, cik="0000320193", accession=accession)
    assert not is_exact_sec_archive_filing_url(
        valid.replace("www.sec.gov", "example.com"), cik="0000320193", accession=accession
    )
    assert not is_exact_sec_archive_filing_url(
        valid.replace("320193/", "2488/"), cik="0000320193", accession=accession
    )
    assert not is_exact_sec_archive_filing_url(
        valid + "?download=1", cik="0000320193", accession=accession
    )


def sec_payload(*, accepted: str = "2026-09-16T12:00:00Z", form: str = "10-Q"):
    return {
        "cik": 320193,
        "name": "Issuer",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                "filingDate": ["2026-09-16", "2026-09-18"],
                "reportDate": ["2026-06-30", "2026-09-30"],
                "acceptanceDateTime": [accepted, "2026-09-18T12:00:00Z"],
                "form": [form, "10-Q"],
                "primaryDocument": ["first.htm", "future.htm"],
            },
            "files": [{"name": "CIK0000320193-submissions-001.json"}],
        },
    }


@pytest.mark.unit
def test_sec_submissions_parser_filters_by_acceptance_instant_and_exposes_continuations():
    cutoff = datetime(2026, 9, 17, 12, tzinfo=UTC)
    result = parse_sec_submissions(sec_payload(), cutoff=cutoff)

    assert result.status is DiscoveryStatus.AVAILABLE
    assert [filing.accession for filing in result.filings] == ["0000320193-26-000001"]
    assert result.filings[0].document_url.endswith(
        "/Archives/edgar/data/320193/000032019326000001/first.htm"
    )
    assert result.continuation_urls == (
        "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json",
    )


@pytest.mark.unit
def test_accession_parser_distinguishes_no_events_from_unavailable():
    cutoff = datetime(2026, 9, 17, 12, tzinfo=UTC)
    no_events = parse_sec_accession_list(sec_payload(form="S-1"), cik="320193", cutoff=cutoff)
    malformed = sec_payload()
    malformed["filings"]["recent"]["form"] = []
    unavailable = parse_sec_submissions(malformed, cutoff=cutoff)

    assert no_events.status is DiscoveryStatus.NO_EVENTS
    assert unavailable.status is DiscoveryStatus.UNAVAILABLE
    assert unavailable.reason == "malformed_sec_accession_list"


@pytest.mark.unit
def test_sec_parsers_require_an_instant_cutoff():
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_sec_submissions(sec_payload(), cutoff=datetime(2026, 9, 17, 12))


@pytest.mark.unit
def test_ir_discovery_distinguishes_no_events_unavailable_and_safe_links():
    unavailable = discover_ir_links(None, base_url="https://ir.example.com/")
    empty = discover_ir_links("<a href='/careers'>Careers</a>", base_url="https://ir.example.com/")
    found = discover_ir_links(
        "<a href='/reports/q2.pdf'>Quarterly Results</a>"
        "<a href='http://private.test/10-q'>10-Q</a>",
        base_url="https://ir.example.com/",
    )

    assert unavailable.status is DiscoveryStatus.UNAVAILABLE
    assert empty.status is DiscoveryStatus.NO_EVENTS
    assert found.status is DiscoveryStatus.AVAILABLE
    assert found.links[0].url == "https://ir.example.com/reports/q2.pdf"
    assert len(found.links) == 1


@pytest.mark.unit
def test_service_reports_fetch_failure_as_unavailable_not_no_events():
    transport = FakeTransport(SourceAccessError("transport_unavailable", "hidden detail"))
    client, _resolver, _clock = fetcher(
        transport,
        answers={"data.sec.gov": (PUBLIC_A,)},
        policy=FetchPolicy(max_attempts=1),
    )
    service = PublicSourceService(client)

    result = service.discover_sec_filings("320193", cutoff=datetime(2026, 9, 17, 12, tzinfo=UTC))

    assert result.status is DiscoveryStatus.UNAVAILABLE
    assert result.reason == "sec_acquisition_failed"
