"""Targeted tests for bounded post-cutoff public-source capture."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from scripts.research_evidence_followup import CAPTURE_POLICY, capture_sources
from tradingagents.research.sources import FetchedSource, SourceAccessError
from tradingagents.research.storage import canonical_json, read_json

UTC = timezone.utc


class StubFetcher:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls: list[str] = []

    def fetch(self, url, *, use_cache):
        assert use_cache is False
        self.calls.append(url)
        result = self.outcomes[url]
        if isinstance(result, Exception):
            raise result
        return result


def _fetched(url: str, text: str = "complete official source text") -> FetchedSource:
    raw = f"<p>{text}</p>".encode()
    return FetchedSource(
        requested_url=url,
        final_url=url,
        retrieved_at=datetime(2026, 9, 22, 18, tzinfo=UTC),
        status=200,
        media_type="text/html",
        charset="utf-8",
        raw=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def _manifest(*sources):
    return {"case_cutoff": "2026-09-19T06:57:53.244995Z", "sources": list(sources)}


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "evidence_manifest.json"
    path.write_bytes(canonical_json(manifest))
    return path


def test_captures_full_text_metadata_and_preserves_post_cutoff_admission(tmp_path) -> None:
    first = "https://example.com/first"
    second = "https://example.com/second"
    fetcher = StubFetcher({first: _fetched(first), second: _fetched(second)})

    manifest = _manifest({"id": "one", "source_url": first}, {"id": "two", "source_url": second})
    manifest_path = _write_manifest(tmp_path, manifest)
    packet = capture_sources(
        manifest,
        tmp_path / "evidence_capture_test",
        fetcher=fetcher,
        source_manifest_path=manifest_path,
        captured_at=datetime(2026, 9, 22, 19, tzinfo=UTC),
    )

    assert fetcher.calls == [first, second]
    assert packet["counts"] == {"attempted": 2, "captured": 2, "failed_or_blocked": 0}
    assert packet["admission"]["status"] == "not_admissible_to_frozen_case"
    assert packet["source_manifest"] == {
        "filepath": str(manifest_path.resolve()),
        "content_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "nested_destination_exception": True,
    }
    record = packet["records"][0]
    assert record["raw_sha256"] == _fetched(first).raw_sha256
    assert record["text_sha256"] == _fetched(first).text_sha256
    assert record["retrieved_at"] == "2026-09-22T18:00:00+00:00"
    assert read_json(tmp_path / "evidence_capture_test" / "capture_manifest.json") == packet
    assert (tmp_path / "evidence_capture_test" / "source_manifest.json").read_bytes() == manifest_path.read_bytes()
    assert (tmp_path / "evidence_capture_test" / "source-cache/text" / record["text_sha256"]).is_file()


def test_records_sec_identity_block_without_leaking_credentials(tmp_path) -> None:
    url = "https://www.sec.gov/Archives/example.htm"
    fetcher = StubFetcher({url: SourceAccessError("sec_identity_required", "SEC requests require an identified user agent")})

    manifest = _manifest({"id": "sec", "source_url": url})
    packet = capture_sources(
        manifest,
        tmp_path / "evidence_capture_test",
        fetcher=fetcher,
        source_manifest_path=_write_manifest(tmp_path, manifest),
    )

    assert packet["counts"] == {"attempted": 1, "captured": 0, "failed_or_blocked": 1}
    assert packet["records"][0]["status"] == "blocked"
    assert packet["records"][0]["failure_code"] == "sec_identity_required"


def test_rejects_more_than_eight_urls_before_fetching(tmp_path) -> None:
    urls = [{"id": f"s{index}", "source_url": f"https://example.com/{index}"} for index in range(9)]
    fetcher = StubFetcher({})

    with pytest.raises(ValueError, match="8-URL"):
        manifest = _manifest(*urls)
        capture_sources(
            manifest,
            tmp_path / "evidence_capture_test",
            fetcher=fetcher,
            source_manifest_path=_write_manifest(tmp_path, manifest),
        )

    assert fetcher.calls == []


def test_retry_cannot_overwrite_even_an_all_failure_capture(tmp_path) -> None:
    url = "https://example.com/one"
    failed = StubFetcher({url: SourceAccessError("dns_unavailable", "DNS failed")})
    destination = tmp_path / "evidence_capture_test"
    manifest = _manifest({"id": "one", "source_url": url})
    manifest_path = _write_manifest(tmp_path, manifest)
    capture_sources(manifest, destination, fetcher=failed, source_manifest_path=manifest_path)

    succeeded = StubFetcher({url: _fetched(url)})
    before = (destination / "capture_manifest.json").read_bytes()
    with pytest.raises(ValueError, match="must be new"):
        capture_sources(manifest, destination, fetcher=succeeded, source_manifest_path=manifest_path)
    assert not succeeded.calls
    assert (destination / "capture_manifest.json").read_bytes() == before


def test_policy_matches_bounded_assignment() -> None:
    assert CAPTURE_POLICY.total_timeout_seconds == 45.0
    assert CAPTURE_POLICY.max_attempts == 2


def test_rejects_duplicate_source_ids_before_fetching(tmp_path) -> None:
    manifest = _manifest(
        {"id": "duplicate", "source_url": "https://example.com/one"},
        {"id": "duplicate", "source_url": "https://example.com/two"},
    )
    fetcher = StubFetcher({})

    with pytest.raises(ValueError, match="duplicate manifest source id"):
        capture_sources(
            manifest,
            tmp_path / "evidence_capture_test",
            fetcher=fetcher,
            source_manifest_path=_write_manifest(tmp_path, manifest),
        )

    assert fetcher.calls == []


def test_allows_only_explicit_evidence_capture_sibling_nested_output(tmp_path) -> None:
    source_directory = tmp_path / "evidence_followup"
    source_directory.mkdir()
    manifest = _manifest({"id": "one", "source_url": "https://example.com/one"})
    manifest_path = _write_manifest(source_directory, manifest)
    fetcher = StubFetcher({"https://example.com/one": _fetched("https://example.com/one")})

    packet = capture_sources(
        manifest,
        source_directory / "evidence_capture_3",
        fetcher=fetcher,
        source_manifest_path=manifest_path,
    )

    assert packet["source_manifest"]["nested_destination_exception"] is True


def test_different_manifest_cannot_be_bound_to_an_unrelated_input_file(tmp_path) -> None:
    manifest = _manifest({"id": "one", "source_url": "https://example.com/one"})
    manifest_path = _write_manifest(tmp_path, manifest)
    destination = tmp_path / "evidence_capture_test"
    manifest["sources"][0]["source_url"] = "https://example.com/altered"
    fetcher = StubFetcher({})
    with pytest.raises(ValueError, match="differs from captured"):
        capture_sources(manifest, destination, fetcher=fetcher, source_manifest_path=manifest_path)
    assert not destination.exists() and not fetcher.calls


def test_capture_retains_original_input_if_source_manifest_changes(tmp_path):
    url = "https://example.com/one"
    manifest = _manifest({"id": "one", "source_url": url})
    manifest_path = _write_manifest(tmp_path, manifest)
    original = manifest_path.read_bytes()

    class ChangingFetcher(StubFetcher):
        def fetch(self, url, *, use_cache):
            manifest_path.write_bytes(b'{"changed":true}')
            return super().fetch(url, use_cache=use_cache)

    destination = tmp_path / "evidence_capture_test"
    packet = capture_sources(manifest, destination, fetcher=ChangingFetcher({url: _fetched(url)}),
                             source_manifest_path=manifest_path)
    assert packet["admission"]["status"] == "source_manifest_changed_during_capture"
    assert (destination / "source_manifest.json").read_bytes() == original
