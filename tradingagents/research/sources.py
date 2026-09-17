"""Bounded, point-in-time-aware public source acquisition primitives.

The production transport resolves no hostnames. ``PublicSourceFetcher`` resolves
and validates each HTTPS redirect hop exactly once, then supplies one validated
public IP address to ``PinnedHTTPSClientTransport``.  The transport connects to
that IP while retaining the original hostname for TLS SNI/certificate validation
and the HTTP Host header.  Injected transports must honor the same contract; a
transport which resolves ``target.host`` again defeats the SSRF guarantee.

This module intentionally performs no LLM work and has no dependency on legacy
data providers.  Discovery helpers distinguish a valid empty result from an
acquisition/parsing failure so callers never turn "unavailable" into "no event".
"""

from __future__ import annotations

import email.message
import hashlib
import http.client
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

_SEC_HOSTS = frozenset({"sec.gov", "www.sec.gov", "data.sec.gov"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_SUPPORTED_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "6-K", "6-K/A"}
)
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_SAFE_SEC_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SAFE_CIK = re.compile(r"^[0-9]{1,10}$")
_SEC_USER_AGENT = re.compile(r"^[\x20-\x7e]{8,256}$")
_IR_KEYWORDS = (
    "10-k",
    "10-q",
    "20-f",
    "6-k",
    "8-k",
    "annual report",
    "quarterly report",
    "earnings",
    "financial results",
    "results",
    "presentation",
    "transcript",
)


class SourceAccessError(RuntimeError):
    """Safe-to-display acquisition failure with no response body or credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def now(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DNSResolver(Protocol):
    def resolve(self, host: str, port: int) -> Sequence[str]: ...


class SystemDNSResolver:
    """Bounded system resolution; callers validate all answers before choosing.

    ``getaddrinfo`` has no portable per-call timeout.  A small bounded set of
    daemon workers prevents one OS resolver stall from blocking acquisition or
    creating an unbounded number of stuck threads.
    """

    def __init__(self, *, timeout_seconds: float = 5.0, max_inflight: int = 4) -> None:
        if not 0 < timeout_seconds <= 30 or not 1 <= max_inflight <= 32:
            raise ValueError("DNS bounds are outside the supported range")
        self.timeout_seconds = timeout_seconds
        self._slots = threading.BoundedSemaphore(max_inflight)

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        if not self._slots.acquire(timeout=self.timeout_seconds):
            raise SourceAccessError("dns_unavailable", "public source DNS resolution timed out")
        completed = threading.Event()
        result: queue.SimpleQueue[object] = queue.SimpleQueue()

        def worker() -> None:
            try:
                result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except Exception as exc:
                result.put(exc)
            finally:
                self._slots.release()
                completed.set()

        threading.Thread(target=worker, name="research-dns", daemon=True).start()
        if not completed.wait(self.timeout_seconds):
            raise SourceAccessError("dns_unavailable", "public source DNS resolution timed out")
        outcome = result.get()
        if isinstance(outcome, Exception):
            raise SourceAccessError(
                "dns_unavailable", "public source DNS resolution failed"
            ) from None
        answers = outcome
        result: list[str] = []
        for answer in answers:
            address = answer[4][0]
            if address not in result:
                result.append(address)
        return tuple(result)


@dataclass(frozen=True)
class ResolvedTarget:
    """One normalized HTTPS authority pinned to one already-validated address."""

    host: str
    port: int
    ip_address: str


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class SourceTransport(Protocol):
    """Transport contract: connect to target.ip_address and never resolve host."""

    def get(
        self,
        target: ResolvedTarget,
        request_target: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse: ...


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: ResolvedTarget, *, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=context)
        self._pinned_ip = target.ip_address

    def connect(self) -> None:
        # No proxy/tunnel support is exposed by this transport.  Connecting to the
        # validated IP directly prevents a second DNS lookup after policy checks.
        raw = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PinnedHTTPSClientTransport:
    """Production HTTPS transport with public-IP pinning and ordinary PKI checks."""

    def __init__(
        self,
        *,
        ssl_context: ssl.SSLContext | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        chunk_bytes: int = 64 * 1024,
    ) -> None:
        if chunk_bytes < 1:
            raise ValueError("chunk_bytes must be positive")
        self._context = ssl_context or ssl.create_default_context()
        if not self._context.check_hostname or self._context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("HTTPS transport requires hostname and certificate verification")
        self._monotonic = monotonic
        self._chunk_bytes = chunk_bytes

    def get(
        self,
        target: ResolvedTarget,
        request_target: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse:
        _require_public_ip(target.ip_address)
        if timeout_seconds <= 0 or max_bytes < 1:
            raise ValueError("transport bounds must be positive")
        deadline = self._monotonic() + timeout_seconds
        connection = _PinnedHTTPSConnection(target, timeout=timeout_seconds, context=self._context)
        try:
            connection.request("GET", request_target, headers=dict(headers))
            response = connection.getresponse()
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError:
                    declared = -1
                if declared > max_bytes:
                    raise SourceAccessError(
                        "response_too_large", "public source exceeded byte allowance"
                    )

            body = bytearray()
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise SourceAccessError(
                        "timeout", "public source request exceeded its deadline"
                    )
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(self._chunk_bytes, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise SourceAccessError(
                        "response_too_large", "public source exceeded byte allowance"
                    )
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            return TransportResponse(response.status, response_headers, bytes(body))
        except SourceAccessError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise SourceAccessError(
                "transport_unavailable", "public source transport failed"
            ) from exc
        finally:
            connection.close()


@dataclass(frozen=True)
class FetchPolicy:
    max_bytes: int = 8 * 1024 * 1024
    request_timeout_seconds: float = 15.0
    total_timeout_seconds: float = 45.0
    max_redirects: int = 5
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0
    max_dns_addresses: int = 16

    def __post_init__(self) -> None:
        if not 1 <= self.max_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_bytes is outside the supported range")
        if not 0 < self.request_timeout_seconds <= 60:
            raise ValueError("request timeout is outside the supported range")
        if not self.request_timeout_seconds <= self.total_timeout_seconds <= 300:
            raise ValueError("total timeout is outside the supported range")
        if not 0 <= self.max_redirects <= 10 or not 1 <= self.max_attempts <= 5:
            raise ValueError("redirect or attempt allowance is outside the supported range")
        if not 0 <= self.initial_backoff_seconds <= self.max_backoff_seconds <= 30:
            raise ValueError("backoff is outside the supported range")
        if not 1 <= self.max_dns_addresses <= 64:
            raise ValueError("DNS answer allowance is outside the supported range")


@dataclass(frozen=True)
class FetchedSource:
    requested_url: str
    final_url: str
    retrieved_at: datetime
    status: int
    media_type: str
    charset: str | None
    raw: bytes
    raw_sha256: str
    text: str | None
    text_sha256: str | None
    redirects: tuple[str, ...] = ()
    from_cache: bool = False


class SourceCache(Protocol):
    def get(self, normalized_url: str) -> FetchedSource | None: ...

    def put(self, source: FetchedSource) -> None: ...


class MemorySourceCache:
    def __init__(self) -> None:
        self._values: dict[str, FetchedSource] = {}
        self._lock = threading.Lock()

    def get(self, normalized_url: str) -> FetchedSource | None:
        with self._lock:
            value = self._values.get(normalized_url)
        return replace(value, from_cache=True) if value is not None else None

    def put(self, source: FetchedSource) -> None:
        with self._lock:
            self._values[source.requested_url] = replace(source, from_cache=False)


class FileSourceCache:
    """Content-addressed raw/text blobs with a small mutable URL index."""

    def __init__(self, directory: Path, *, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.directory = Path(directory)
        self.max_bytes = max_bytes

    def _index(self, url: str) -> Path:
        return self.directory / "indexes" / f"{_sha256(url.encode('utf-8'))}.json"

    def _blob(self, kind: str, digest_value: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{64}", digest_value) is None:
            raise SourceAccessError("cache_corrupt", "source cache contains an invalid digest")
        return self.directory / kind / digest_value

    def get(self, normalized_url: str) -> FetchedSource | None:
        index = self._index(normalized_url)
        if not index.exists():
            return None
        try:
            payload = _read_bounded(index, 64 * 1024)
            metadata = json.loads(payload.decode("utf-8"))
            if not isinstance(metadata, dict) or metadata.get("requested_url") != normalized_url:
                raise ValueError
            raw_hash = metadata["raw_sha256"]
            raw = _read_bounded(self._blob("raw", raw_hash), self.max_bytes)
            if _sha256(raw) != raw_hash:
                raise ValueError
            text_hash = metadata.get("text_sha256")
            text = None
            if text_hash is not None:
                text_bytes = _read_bounded(self._blob("text", text_hash), self.max_bytes * 4)
                if _sha256(text_bytes) != text_hash:
                    raise ValueError
                text = text_bytes.decode("utf-8")
            media_type = metadata["media_type"]
            charset = metadata.get("charset")
            if not isinstance(media_type, str) or not isinstance(charset, (str, type(None))):
                raise ValueError
            _validate_text_binding(raw, media_type, charset, text, text_hash)
            retrieved_at = datetime.fromisoformat(metadata["retrieved_at"])
            if retrieved_at.tzinfo is None:
                raise ValueError
            return FetchedSource(
                requested_url=normalized_url,
                final_url=metadata["final_url"],
                retrieved_at=retrieved_at,
                status=metadata["status"],
                media_type=media_type,
                charset=charset,
                raw=raw,
                raw_sha256=raw_hash,
                text=text,
                text_sha256=text_hash,
                redirects=tuple(metadata.get("redirects", ())),
                from_cache=True,
            )
        except (
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise SourceAccessError("cache_corrupt", "source cache record is invalid") from exc

    def put(self, source: FetchedSource) -> None:
        _validate_text_binding(
            source.raw,
            source.media_type,
            source.charset,
            source.text,
            source.text_sha256,
        )
        raw_path = self._blob("raw", source.raw_sha256)
        _write_content_blob(raw_path, source.raw, source.raw_sha256)
        if source.text is not None and source.text_sha256 is not None:
            encoded = source.text.encode("utf-8")
            _write_content_blob(self._blob("text", source.text_sha256), encoded, source.text_sha256)
        metadata = {
            "schema_version": 1,
            "requested_url": source.requested_url,
            "final_url": source.final_url,
            "retrieved_at": source.retrieved_at.isoformat(),
            "status": source.status,
            "media_type": source.media_type,
            "charset": source.charset,
            "raw_sha256": source.raw_sha256,
            "text_sha256": source.text_sha256,
            "redirects": list(source.redirects),
        }
        _atomic_write(self._index(source.requested_url), _canonical_json(metadata))


class SecRateLimiter:
    """Thread-safe fixed-spacing limiter; share one instance across fetchers."""

    def __init__(self, clock: Clock, *, requests_per_second: float = 2.0) -> None:
        if not 0 < requests_per_second <= 2.0:
            raise ValueError("SEC rate must be in (0, 2]")
        self._clock = clock
        self._interval = 1.0 / requests_per_second
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock.monotonic()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                self._clock.sleep(delay)
                now = self._clock.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._interval


_SYSTEM_CLOCK = SystemClock()
_SYSTEM_RESOLVER = SystemDNSResolver()
_SHARED_SEC_LIMITER = SecRateLimiter(_SYSTEM_CLOCK)


class PublicSourceFetcher:
    """Fetch public HTTPS resources with bounded, rebind-safe acquisition."""

    def __init__(
        self,
        *,
        resolver: DNSResolver | None = None,
        transport: SourceTransport | None = None,
        clock: Clock | None = None,
        cache: SourceCache | None = None,
        policy: FetchPolicy | None = None,
        sec_user_agent: str | None = None,
        sec_limiter: SecRateLimiter | None = None,
    ) -> None:
        custom_clock = clock is not None
        self.clock = clock or _SYSTEM_CLOCK
        self.resolver = resolver or _SYSTEM_RESOLVER
        self.transport = transport or PinnedHTTPSClientTransport()
        self.cache = cache
        self.policy = policy or FetchPolicy()
        self.sec_user_agent = _validated_sec_user_agent(sec_user_agent)
        self.sec_limiter = sec_limiter or (
            SecRateLimiter(self.clock) if custom_clock else _SHARED_SEC_LIMITER
        )

    def fetch(self, url: str, *, use_cache: bool = False) -> FetchedSource:
        requested = normalize_public_https_url(url)
        if use_cache and self.cache is not None:
            cached = self.cache.get(requested)
            if cached is not None:
                return cached

        deadline = self.clock.monotonic() + self.policy.total_timeout_seconds
        current = requested
        redirects: list[str] = []
        visited = {current}
        for _hop in range(self.policy.max_redirects + 1):
            target, request_target = self._resolve_target(current)
            response = self._request(target, request_target, deadline)
            if response.status in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not isinstance(location, str) or not location:
                    raise SourceAccessError("invalid_redirect", "redirect omitted its destination")
                destination = normalize_public_https_url(urljoin(current, location))
                if destination in visited:
                    raise SourceAccessError("redirect_loop", "public source redirect loop detected")
                visited.add(destination)
                redirects.append(destination)
                current = destination
                continue
            if not 200 <= response.status < 300:
                raise SourceAccessError(
                    "http_unavailable", "public source returned an unavailable status"
                )
            media_type, charset = _content_type(response.headers.get("content-type"))
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise SourceAccessError(
                    "unsupported_content_encoding", "compressed public responses are not accepted"
                )
            text = extract_text(response.body, media_type=media_type, charset=charset)
            raw_hash = _sha256(response.body)
            text_hash = _sha256(text.encode("utf-8")) if text is not None else None
            retrieved_at = self.clock.now()
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                raise SourceAccessError(
                    "invalid_clock", "source clock must return an aware timestamp"
                )
            source = FetchedSource(
                requested_url=requested,
                final_url=current,
                retrieved_at=retrieved_at,
                status=response.status,
                media_type=media_type,
                charset=charset,
                raw=response.body,
                raw_sha256=raw_hash,
                text=text,
                text_sha256=text_hash,
                redirects=tuple(redirects),
            )
            if self.cache is not None:
                self.cache.put(source)
            return source
        raise SourceAccessError("redirect_limit", "public source exceeded redirect allowance")

    def _resolve_target(self, url: str) -> tuple[ResolvedTarget, str]:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise SourceAccessError("invalid_url", "public source URL has no hostname")
        try:
            addresses = tuple(dict.fromkeys(self.resolver.resolve(host, 443)))
        except SourceAccessError:
            raise
        except Exception as exc:
            raise SourceAccessError(
                "dns_unavailable", "public source DNS resolution failed"
            ) from exc
        if not addresses or len(addresses) > self.policy.max_dns_addresses:
            raise SourceAccessError("dns_unavailable", "public source DNS answer is unusable")
        # Fail closed when a name returns a mix of public and private answers.
        # The selected address is then pinned into the transport; no later lookup occurs.
        for address in addresses:
            _require_public_ip(address)
        request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        return ResolvedTarget(host, 443, addresses[0]), request_target

    def _request(
        self, target: ResolvedTarget, request_target: str, deadline: float
    ) -> TransportResponse:
        host = target.host
        is_sec = _is_sec_host(host)
        if is_sec and self.sec_user_agent is None:
            raise SourceAccessError(
                "sec_identity_required", "SEC requests require an identified user agent"
            )
        try:
            parsed_host = ipaddress.ip_address(host)
        except ValueError:
            host_header = host
        else:
            host_header = f"[{host}]" if parsed_host.version == 6 else host
        if target.port != 443:
            host_header = f"{host_header}:{target.port}"
        headers = {
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Host": host_header,
            "User-Agent": self.sec_user_agent if is_sec else "TradingAgents-Research/0.4",
        }
        last_status: int | None = None
        for attempt in range(self.policy.max_attempts):
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                raise SourceAccessError("timeout", "public source exceeded its total deadline")
            if is_sec:
                self.sec_limiter.acquire()
                remaining = deadline - self.clock.monotonic()
                if remaining <= 0:
                    raise SourceAccessError("timeout", "public source exceeded its total deadline")
            timeout = min(self.policy.request_timeout_seconds, remaining)
            try:
                response = self.transport.get(
                    target,
                    request_target,
                    headers,
                    timeout_seconds=timeout,
                    max_bytes=self.policy.max_bytes,
                )
            except SourceAccessError as exc:
                if exc.code not in {"transport_unavailable", "timeout"}:
                    raise
                response = None
            except Exception:
                response = None

            if response is not None:
                if not isinstance(response.status, int) or not 100 <= response.status <= 599:
                    raise SourceAccessError(
                        "invalid_response", "public source returned invalid metadata"
                    )
                if not isinstance(response.body, bytes):
                    raise SourceAccessError(
                        "invalid_response", "public source returned invalid content"
                    )
                if len(response.body) > self.policy.max_bytes:
                    raise SourceAccessError(
                        "response_too_large", "public source exceeded byte allowance"
                    )
                response = TransportResponse(
                    response.status,
                    {str(key).lower(): str(value) for key, value in response.headers.items()},
                    response.body,
                )
            if response is not None and response.status not in _RETRYABLE_STATUSES:
                return response
            if response is not None:
                last_status = response.status
            if attempt + 1 >= self.policy.max_attempts:
                break
            delay = min(
                self.policy.initial_backoff_seconds * (2**attempt),
                self.policy.max_backoff_seconds,
            )
            if response is not None:
                delay = min(
                    max(delay, _retry_after_seconds(response.headers.get("retry-after"))),
                    self.policy.max_backoff_seconds,
                )
            remaining = deadline - self.clock.monotonic()
            if remaining <= delay:
                raise SourceAccessError("timeout", "public source exceeded its total deadline")
            if delay:
                self.clock.sleep(delay)
        if last_status is not None:
            raise SourceAccessError("http_unavailable", "public source remained unavailable")
        raise SourceAccessError("transport_unavailable", "public source transport failed") from None


class DiscoveryStatus(str, Enum):
    AVAILABLE = "available"
    NO_EVENTS = "no_events"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SecFiling:
    cik: str
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    accepted_at: datetime
    primary_document: str
    document_url: str


@dataclass(frozen=True)
class SecDiscoveryResult:
    status: DiscoveryStatus
    filings: tuple[SecFiling, ...] = ()
    continuation_urls: tuple[str, ...] = ()
    reason: str | None = None


def parse_sec_accession_list(
    payload: bytes | str | Mapping[str, object],
    *,
    cik: str,
    cutoff: datetime,
    forms: Sequence[str] = tuple(sorted(_SUPPORTED_FORMS)),
) -> SecDiscoveryResult:
    """Purely parse an SEC columnar accession list and apply acceptance cutoff."""

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    normalized_cik = _normalize_cik(cik)
    try:
        decoded = _decode_json_object(payload)
        recent = (
            decoded.get("filings", {}).get("recent")
            if isinstance(decoded.get("filings"), dict)
            else None
        )
        table = recent if isinstance(recent, dict) else decoded
        required = (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
        )
        columns = [table[name] for name in required]
        if any(not isinstance(column, list) for column in columns):
            raise ValueError
        lengths = {len(column) for column in columns}
        if len(lengths) != 1:
            raise ValueError
        allowed = {str(form).upper() for form in forms}
        filings: list[SecFiling] = []
        for row in zip(*columns, strict=True):
            accession, filing_raw, report_raw, accepted_raw, form_raw, document = row
            form = str(form_raw).upper()
            if form not in allowed:
                continue
            if not isinstance(accession, str) or _ACCESSION.fullmatch(accession) is None:
                raise ValueError
            if accession[:10] != normalized_cik:
                raise ValueError
            if not isinstance(document, str) or _SAFE_SEC_FILE.fullmatch(document) is None:
                raise ValueError
            accepted = _aware_datetime(accepted_raw)
            filing_date = _iso_date(filing_raw)
            report_date = _iso_date(report_raw, optional=True)
            if accepted is None or filing_date is None:
                raise ValueError
            if accepted > cutoff:
                continue
            compact_accession = accession.replace("-", "")
            filing_cik = normalized_cik.lstrip("0") or "0"
            document_url = (
                f"https://www.sec.gov/Archives/edgar/data/{filing_cik}/"
                f"{compact_accession}/{document}"
            )
            filings.append(
                SecFiling(
                    cik=normalized_cik,
                    accession=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=report_date,
                    accepted_at=accepted,
                    primary_document=document,
                    document_url=document_url,
                )
            )
        filings.sort(key=lambda item: (item.accepted_at, item.accession), reverse=True)
        status = DiscoveryStatus.AVAILABLE if filings else DiscoveryStatus.NO_EVENTS
        return SecDiscoveryResult(status=status, filings=tuple(filings))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return SecDiscoveryResult(
            status=DiscoveryStatus.UNAVAILABLE, reason="malformed_sec_accession_list"
        )


def parse_sec_submissions(
    payload: bytes | str | Mapping[str, object],
    *,
    cutoff: datetime,
    forms: Sequence[str] = tuple(sorted(_SUPPORTED_FORMS)),
) -> SecDiscoveryResult:
    """Purely parse the SEC company-submissions index and continuation references."""

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    try:
        decoded = _decode_json_object(payload)
        cik = _normalize_cik(str(decoded["cik"]))
        parsed = parse_sec_accession_list(decoded, cik=cik, cutoff=cutoff, forms=forms)
        if parsed.status is DiscoveryStatus.UNAVAILABLE:
            return parsed
        filings = decoded.get("filings")
        if not isinstance(filings, dict):
            raise ValueError
        raw_files = filings.get("files", [])
        if not isinstance(raw_files, list):
            raise ValueError
        continuations: list[str] = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise ValueError
            name = item.get("name")
            if not isinstance(name, str) or _SAFE_SEC_FILE.fullmatch(name) is None:
                raise ValueError
            continuations.append(f"https://data.sec.gov/submissions/{name}")
        status = (
            DiscoveryStatus.AVAILABLE
            if parsed.filings or continuations
            else DiscoveryStatus.NO_EVENTS
        )
        return SecDiscoveryResult(status, parsed.filings, tuple(continuations))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return SecDiscoveryResult(
            status=DiscoveryStatus.UNAVAILABLE, reason="malformed_sec_submissions"
        )


@dataclass(frozen=True)
class DiscoveredLink:
    url: str
    title: str


@dataclass(frozen=True)
class LinkDiscoveryResult:
    status: DiscoveryStatus
    links: tuple[DiscoveredLink, ...] = ()
    reason: str | None = None


class _IRLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, _collapse_text(" ".join(self._text))))
            self._href = None
            self._text = []


def discover_ir_links(html: str | None, *, base_url: str) -> LinkDiscoveryResult:
    """Pure basic IR-link discovery; each returned URL is revalidated when fetched."""

    if html is None:
        return LinkDiscoveryResult(DiscoveryStatus.UNAVAILABLE, reason="html_unavailable")
    try:
        base = normalize_public_https_url(base_url)
        parser = _IRLinkParser()
        parser.feed(html)
        links: list[DiscoveredLink] = []
        seen: set[str] = set()
        for href, title in parser.links:
            candidate_text = f"{title} {href}".lower()
            if not any(keyword in candidate_text for keyword in _IR_KEYWORDS):
                continue
            try:
                url = normalize_public_https_url(urljoin(base, href))
            except SourceAccessError:
                continue
            if url not in seen:
                seen.add(url)
                links.append(DiscoveredLink(url, title or Path(urlsplit(url).path).name))
        return LinkDiscoveryResult(
            DiscoveryStatus.AVAILABLE if links else DiscoveryStatus.NO_EVENTS,
            tuple(links),
        )
    except ValueError:
        return LinkDiscoveryResult(DiscoveryStatus.UNAVAILABLE, reason="malformed_ir_html")


class PublicSourceService:
    """Small baseline SEC/IR adapter over the safe fetcher."""

    def __init__(self, fetcher: PublicSourceFetcher) -> None:
        self.fetcher = fetcher

    def fetch_document(self, url: str, *, use_cache: bool = False) -> FetchedSource:
        return self.fetcher.fetch(url, use_cache=use_cache)

    def discover_sec_filings(
        self,
        cik: str,
        *,
        cutoff: datetime,
        forms: Sequence[str] = tuple(sorted(_SUPPORTED_FORMS)),
        use_cache: bool = False,
    ) -> SecDiscoveryResult:
        try:
            normalized_cik = _normalize_cik(cik)
            source = self.fetcher.fetch(
                f"https://data.sec.gov/submissions/CIK{normalized_cik}.json",
                use_cache=use_cache,
            )
        except (SourceAccessError, ValueError):
            return SecDiscoveryResult(DiscoveryStatus.UNAVAILABLE, reason="sec_acquisition_failed")
        return parse_sec_submissions(source.raw, cutoff=cutoff, forms=forms)

    def discover_ir(self, url: str, *, use_cache: bool = False) -> LinkDiscoveryResult:
        try:
            source = self.fetcher.fetch(url, use_cache=use_cache)
        except SourceAccessError:
            return LinkDiscoveryResult(DiscoveryStatus.UNAVAILABLE, reason="ir_acquisition_failed")
        if source.media_type != "text/html":
            return LinkDiscoveryResult(DiscoveryStatus.UNAVAILABLE, reason="ir_html_unavailable")
        return discover_ir_links(source.text, base_url=source.final_url)


class _VisibleTextParser(HTMLParser):
    _BLOCKS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "div",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "li",
            "main",
            "nav",
            "p",
            "section",
            "table",
            "td",
            "th",
            "tr",
        }
    )
    _HIDDEN = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in self._HIDDEN:
            self.hidden_depth += 1
        elif normalized in self._BLOCKS and not self.hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._HIDDEN and self.hidden_depth:
            self.hidden_depth -= 1
        elif normalized in self._BLOCKS and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def extract_text(raw: bytes, *, media_type: str, charset: str | None = None) -> str | None:
    """Deterministically extract plain text from bounded HTML/text/JSON bytes."""

    if media_type not in {"text/html", "text/plain", "application/json", "application/ld+json"}:
        return None
    encoding = charset or "utf-8"
    if len(encoding) > 40 or re.fullmatch(r"[A-Za-z0-9._-]+", encoding) is None:
        raise SourceAccessError("unsupported_charset", "public source declared an invalid charset")
    try:
        decoded = raw.decode(encoding, errors="replace")
    except LookupError as exc:
        raise SourceAccessError(
            "unsupported_charset", "public source declared an unknown charset"
        ) from exc
    if media_type != "text/html":
        return decoded
    parser = _VisibleTextParser()
    try:
        parser.feed(decoded)
        parser.close()
    except ValueError as exc:
        raise SourceAccessError("extraction_failed", "public HTML extraction failed") from exc
    return _collapse_text("".join(parser.parts))


def _validate_text_binding(
    raw: bytes,
    media_type: str,
    charset: str | None,
    text: str | None,
    text_hash: str | None,
) -> None:
    """Require cached extracted text to be exactly reproducible from raw bytes."""

    try:
        expected = extract_text(raw, media_type=media_type, charset=charset)
    except SourceAccessError as exc:
        raise SourceAccessError(
            "cache_corrupt", "source cache extraction metadata is invalid"
        ) from exc
    expected_hash = _sha256(expected.encode("utf-8")) if expected is not None else None
    if text != expected or text_hash != expected_hash:
        raise SourceAccessError(
            "cache_corrupt", "source cache text is not bound to its raw response"
        )


def normalize_public_https_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise SourceAccessError("invalid_url", "public source URL is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in url) or "\\" in url:
        raise SourceAccessError("invalid_url", "public source URL contains unsafe characters")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        if parsed.fragment:
            raise ValueError
        host = parsed.hostname
        if host is None or "%" in host:
            raise ValueError
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port or 443
        if port != 443:
            raise ValueError
        if len(host) > 253 or not host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if any(
                not label
                or len(label) > 63
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
                for label in host.split(".")
            ):
                raise ValueError from None
            netloc = host
        else:
            _require_public_ip(str(address))
            netloc = f"[{host}]" if address.version == 6 else host
        return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    except (UnicodeError, ValueError):
        raise SourceAccessError("invalid_url", "public source URL is invalid") from None


def is_exact_sec_archive_filing_url(url: str, *, cik: str, accession: str) -> bool:
    """Return whether URL is the exact public SEC archive directory for an accession."""

    try:
        normalized_cik = _normalize_cik(cik)
        if _ACCESSION.fullmatch(accession) is None or accession[:10] != normalized_cik:
            return False
        normalized = normalize_public_https_url(url)
        parsed = urlsplit(normalized)
        if parsed.hostname != "www.sec.gov" or parsed.query:
            return False
        filing_cik = normalized_cik.lstrip("0") or "0"
        compact_accession = accession.replace("-", "")
        prefix = f"/Archives/edgar/data/{filing_cik}/{compact_accession}/"
        if not parsed.path.startswith(prefix):
            return False
        document = parsed.path[len(prefix) :]
        return _SAFE_SEC_FILE.fullmatch(document) is not None
    except (SourceAccessError, TypeError, ValueError):
        return False


def _require_public_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise SourceAccessError("unsafe_destination", "DNS returned an invalid address") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global:
        raise SourceAccessError(
            "unsafe_destination", "public source resolved to a non-public address"
        )


def _is_sec_host(host: str) -> bool:
    return host in _SEC_HOSTS or host.endswith(".sec.gov")


def _validated_sec_user_agent(value: str | None) -> str | None:
    if value is None:
        return None
    if _SEC_USER_AGENT.fullmatch(value) is None or "@" not in value or " " not in value:
        raise ValueError("SEC user agent must identify an organization and contact email")
    return value


def _content_type(value: str | None) -> tuple[str, str | None]:
    if not value:
        return "application/octet-stream", None
    message = email.message.Message()
    message["content-type"] = value
    media_type = message.get_content_type().lower()
    charset = message.get_param("charset")
    return media_type, charset.strip('"').lower() if isinstance(charset, str) else None


def _retry_after_seconds(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value.strip())
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, parsed) if parsed < 1_000_000 else 0.0


def _normalize_cik(value: str) -> str:
    text = str(value).strip()
    if _SAFE_CIK.fullmatch(text) is None:
        raise ValueError("CIK must contain at most ten digits")
    return text.zfill(10)


def _decode_json_object(payload: bytes | str | Mapping[str, object]) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    decoded = json.loads(
        payload,
        object_pairs_hook=unique,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(decoded, dict):
        raise ValueError("SEC payload must be an object")
    return decoded


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _iso_date(value: object, *, optional: bool = False) -> date | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _collapse_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".source-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_content_blob(path: Path, content: bytes, expected_hash: str) -> None:
    if _sha256(content) != expected_hash:
        raise SourceAccessError("cache_corrupt", "source cache content hash mismatch")
    if path.exists():
        if _sha256(_read_bounded(path, len(content) + 1)) != expected_hash:
            raise SourceAccessError("cache_corrupt", "source cache blob is invalid")
        return
    _atomic_write(path, content)


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError as exc:
        raise SourceAccessError("cache_corrupt", "source cache could not be read") from exc
    if len(content) > max_bytes:
        raise SourceAccessError("cache_corrupt", "source cache entry exceeds its allowance")
    return content


__all__ = [
    "Clock",
    "DNSResolver",
    "DiscoveredLink",
    "DiscoveryStatus",
    "FetchPolicy",
    "FetchedSource",
    "FileSourceCache",
    "LinkDiscoveryResult",
    "MemorySourceCache",
    "PinnedHTTPSClientTransport",
    "PublicSourceFetcher",
    "PublicSourceService",
    "ResolvedTarget",
    "SecDiscoveryResult",
    "SecFiling",
    "SecRateLimiter",
    "SourceAccessError",
    "SourceCache",
    "SourceTransport",
    "SystemDNSResolver",
    "TransportResponse",
    "discover_ir_links",
    "extract_text",
    "is_exact_sec_archive_filing_url",
    "normalize_public_https_url",
    "parse_sec_accession_list",
    "parse_sec_submissions",
]
