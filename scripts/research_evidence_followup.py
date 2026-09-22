"""Capture a bounded, post-cutoff public-source packet without model calls.

The capture is deliberately separate from any frozen case.  It reads URLs from
an existing evidence-follow-up manifest, delegates retrieval and raw/text cache
writing to ``PublicSourceFetcher`` and ``FileSourceCache``, and records every
attempt without printing credentials, source bodies, or request headers.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.research.sources import (
    FetchPolicy,
    FileSourceCache,
    PublicSourceFetcher,
    SourceAccessError,
)
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    parse_json,
    read_bytes,
    read_json,
)

MAX_URLS = 8
CAPTURE_POLICY = FetchPolicy(
    request_timeout_seconds=15.0,
    total_timeout_seconds=45.0,
    max_attempts=2,
)


def _new_destination(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError("capture destination must be new; retries use a separate directory")
    destination.mkdir(parents=True, exist_ok=False)


def _urls(manifest: Mapping[str, Any]) -> list[tuple[str, str]]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError("manifest sources must be a list")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    source_ids: set[str] = set()
    for item in sources:
        if not isinstance(item, Mapping):
            raise ValueError("manifest source must be an object")
        source_id, url = item.get("id"), item.get("source_url")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("manifest source id is required")
        if source_id in source_ids:
            raise ValueError(f"duplicate manifest source id: {source_id}")
        source_ids.add(source_id)
        if not isinstance(url, str) or not url:
            raise ValueError(f"{source_id}: source_url is required")
        if url in seen:
            continue
        seen.add(url)
        result.append((source_id, url))
    if not result:
        raise ValueError("manifest has no source URLs")
    if len(result) > MAX_URLS:
        raise ValueError(f"manifest exceeds the {MAX_URLS}-URL capture limit")
    return result


def _source_manifest_provenance(manifest_path: Path, destination: Path):
    """Bind the packet to its exact input file, allowing the approved sibling output."""
    source_path = manifest_path.resolve(strict=True)
    if not source_path.is_file():
        raise ValueError("manifest path must be a file")
    destination_path = destination.resolve()
    source_directory = source_path.parent
    approved_nested_destination = (
        destination_path.parent == source_directory
        and destination_path.name.startswith("evidence_capture_")
    )
    if destination_path.is_relative_to(source_directory) and not approved_nested_destination:
        raise ValueError(
            "capture destination inside the manifest directory must be an explicitly named evidence_capture_* sibling"
        )
    content = read_bytes(source_path)
    return content, {
        "filepath": str(source_path),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "nested_destination_exception": approved_nested_destination,
    }


def capture_sources(
    manifest: Mapping[str, Any],
    destination: Path,
    *,
    fetcher: PublicSourceFetcher,
    source_manifest_path: Path,
    captured_at: datetime | None = None,
) -> dict[str, object]:
    """Fetch at most eight manifest URLs and persist a non-admissible manifest."""
    content, source_manifest = _source_manifest_provenance(source_manifest_path, destination)
    if canonical_json(parse_json(content)) != canonical_json(manifest):
        raise ValueError("provided manifest differs from captured source bytes")
    cutoff = manifest.get("case_cutoff")
    if not isinstance(cutoff, str) or not cutoff:
        raise ValueError("manifest case_cutoff is required")
    selected_urls = _urls(manifest)
    captured_at = captured_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    _new_destination(destination)
    atomic_write(destination / "source_manifest.json", content)
    cache = FileSourceCache(destination / "source-cache")

    records: list[dict[str, object]] = []
    for source_id, url in selected_urls:
        try:
            fetched = fetcher.fetch(url, use_cache=False)
            cache.put(fetched)
        except SourceAccessError as exc:
            records.append(
                {
                    "id": source_id,
                    "requested_url": url,
                    "status": "blocked" if exc.code == "sec_identity_required" else "failed",
                    "failure_code": exc.code,
                    "failure_message": str(exc),
                }
            )
            continue
        except Exception:
            records.append(
                {
                    "id": source_id,
                    "requested_url": url,
                    "status": "failed",
                    "failure_code": "unexpected_fetch_error",
                    "failure_message": "public source acquisition failed unexpectedly",
                }
            )
            continue

        usable_text = isinstance(fetched.text, str) and bool(fetched.text.strip())
        records.append(
            {
                "id": source_id,
                "requested_url": fetched.requested_url,
                "final_url": fetched.final_url,
                "status": "captured" if usable_text else "failed",
                "failure_code": None if usable_text else "missing_full_text",
                "failure_message": None if usable_text else "fetcher returned no extracted full text",
                "http_status": fetched.status,
                "retrieved_at": fetched.retrieved_at.astimezone(timezone.utc).isoformat(),
                "media_type": fetched.media_type,
                "raw_sha256": fetched.raw_sha256,
                "text_sha256": fetched.text_sha256,
                "text_characters": len(fetched.text) if usable_text else 0,
                "redirects": list(fetched.redirects),
                "cache": "source-cache",
            }
        )

    captured = sum(record["status"] == "captured" for record in records)
    failures = len(records) - captured
    packet = {
        "schema_version": 3,
        "packet_type": "public_source_capture",
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "source_manifest": source_manifest,
        "source_case_cutoff": cutoff,
        "admission": {
            "status": "not_admissible_to_frozen_case",
            "reason": "capture retrieval occurs after the frozen case cutoff; create a new cutoff and engine packet before use",
        },
        "limits": {
            "max_urls": MAX_URLS,
            "max_attempts_per_url": CAPTURE_POLICY.max_attempts,
            "total_timeout_seconds_per_url": CAPTURE_POLICY.total_timeout_seconds,
        },
        "counts": {"attempted": len(records), "captured": captured, "failed_or_blocked": failures},
        "records": records,
    }
    if read_bytes(source_manifest_path) != content:
        packet["admission"] = {"status": "source_manifest_changed_during_capture",
                               "reason": "Captured source_manifest.json preserves the actual input; reassemble with reviewed inputs."}
    atomic_write(destination / "capture_manifest.json", canonical_json(packet))
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    if not isinstance(manifest, Mapping):
        parser.error("manifest must be an object")

    # Existing configured SEC identity is used if present; it is never logged.
    fetcher = PublicSourceFetcher(
        policy=CAPTURE_POLICY,
        sec_user_agent=os.environ.get("SEC_USER_AGENT"),
    )
    packet = capture_sources(
        manifest,
        args.output,
        fetcher=fetcher,
        source_manifest_path=args.manifest,
    )
    counts = packet["counts"]
    print(
        f"capture_manifest={args.output / 'capture_manifest.json'} "
        f"attempted={counts['attempted']} captured={counts['captured']} "
        f"failed_or_blocked={counts['failed_or_blocked']}"
    )


if __name__ == "__main__":
    main()
