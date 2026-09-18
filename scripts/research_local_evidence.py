"""Import an explicit packet of already-downloaded public documents.

This is an offline, manifest-bounded importer.  It does not fetch URLs, discover
documents, or claim full-web coverage.  ``original_url`` records public origin
metadata only; ``local_file`` supplies the exact bytes that are archived.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.sources import (
    SourceAccessError,
    extract_text,
    normalize_public_https_url,
)
from tradingagents.research.storage import atomic_write, canonical_json, read_bytes, read_json

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SOURCES = 64
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 128 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 256 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MIN_FULL_TEXT_CHARACTERS = 500
PDF_TIMEOUT_SECONDS = 90
PDF_EXTRACTION_GAP = (
    "PDF text extraction uses pypdf without OCR; images, charts, and scanned pages may not be "
    "represented. Source availability='full_text' means the complete extractor output was "
    "retained, not that visual layout content is complete."
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,19}$")
_KINDS = frozenset({"filing", "ir", "news", "regulatory", "market", "other"})
_ACQUISITION_METHODS = frozenset({"local", "browser"})
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "ticker", "gaps", "sources"})
_SOURCE_FIELDS = frozenset(
    {
        "id",
        "local_file",
        "original_url",
        "title",
        "publisher",
        "published_at",
        "retrieved_at",
        "kind",
        "acquisition",
    }
)

_PDF_PROGRAM = r"""
import io
import json
import sys

import pypdf

raw = sys.stdin.buffer.read()
reader = pypdf.PdfReader(io.BytesIO(raw), strict=True)
if reader.is_encrypted:
    raise ValueError("encrypted PDFs are not supported")
if not reader.pages:
    raise ValueError("PDF has no pages")
if len(reader.pages) > MAX_PAGES:
    raise ValueError("PDF exceeds page allowance")
parts = []
empty_text_page_ids = []
for number, page in enumerate(reader.pages, 1):
    page_text = page.extract_text()
    if page_text is None:
        page_text = ""
    if not page_text.strip():
        empty_text_page_ids.append(number)
    parts.append("[PDF page %d]\n%s" % (number, page_text))
text = "\n\n".join(parts)
encoded = text.encode("utf-8")
if len(encoded) > MAX_TEXT_BYTES:
    raise ValueError("PDF extracted text exceeds byte allowance")
header = json.dumps(
    {"name": "pypdf.PdfReader.page.extract_text", "version": pypdf.__version__,
     "pages": len(reader.pages), "page_markers": "one-based [PDF page N]",
     "empty_text_page_count": len(empty_text_page_ids),
     "empty_text_page_ids": empty_text_page_ids},
    sort_keys=True,
    separators=(",", ":"),
)
sys.stdout.buffer.write(header.encode("utf-8") + b"\n" + encoded)
"""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_fields(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise ValueError(f"{label} fields are invalid: {'; '.join(details)}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _timestamp(value: Any, label: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _source_path(manifest_path: Path, value: Any, source_id: str) -> Path:
    supplied = Path(_nonempty_string(value, f"source {source_id} local_file")).expanduser()
    path = supplied if supplied.is_absolute() else manifest_path.parent / supplied
    if path.is_symlink():
        raise ValueError(f"source {source_id} local_file cannot be a symlink")
    if not path.is_file():
        raise ValueError(f"source {source_id} local_file is not a regular file")
    return path.resolve()


def _normalize_url(value: Any, source_id: str) -> str:
    try:
        return normalize_public_https_url(value)
    except SourceAccessError as exc:
        raise ValueError(f"source {source_id} original_url is not a public HTTPS URL") from exc


def _validate_manifest(manifest_path: Path) -> tuple[str, tuple[str, ...], list[dict[str, Any]]]:
    manifest = _require_mapping(
        read_json(manifest_path, max_bytes=MAX_MANIFEST_BYTES), "manifest"
    )
    _require_fields(manifest, _TOP_LEVEL_FIELDS, "manifest")
    if manifest["schema_version"] != 1:
        raise ValueError("manifest schema_version must be 1")
    ticker = _nonempty_string(manifest["ticker"], "manifest ticker")
    if _TICKER.fullmatch(ticker) is None:
        raise ValueError("manifest ticker is invalid")
    gaps_value = manifest["gaps"]
    if not isinstance(gaps_value, list) or not all(
        isinstance(item, str) and item.strip() for item in gaps_value
    ):
        raise ValueError("manifest gaps must be a list of non-empty strings")
    sources_value = manifest["sources"]
    if not isinstance(sources_value, list) or not 1 <= len(sources_value) <= MAX_SOURCES:
        raise ValueError(f"manifest sources must contain between 1 and {MAX_SOURCES} items")

    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_archive_ids: set[str] = set()
    for index, untrusted in enumerate(sources_value):
        item = _require_mapping(untrusted, f"source {index}")
        _require_fields(item, _SOURCE_FIELDS, f"source {index}")
        source_id = _nonempty_string(item["id"], f"source {index} id")
        if _SAFE_ID.fullmatch(source_id) is None:
            raise ValueError(f"source {index} id is not safe for an archive filename")
        archive_id = source_id.casefold()
        if source_id in seen_ids or archive_id in seen_archive_ids:
            raise ValueError(f"duplicate source id: {source_id}")
        seen_ids.add(source_id)
        seen_archive_ids.add(archive_id)
        kind = item["kind"]
        if kind not in _KINDS:
            raise ValueError(f"source {source_id} kind is unsupported")
        acquisition = item["acquisition"]
        if acquisition not in _ACQUISITION_METHODS:
            raise ValueError(f"source {source_id} acquisition must be local or browser")
        published_at = _timestamp(
            item["published_at"], f"source {source_id} published_at", nullable=True
        )
        retrieved_at = _timestamp(item["retrieved_at"], f"source {source_id} retrieved_at")
        assert retrieved_at is not None
        if published_at is not None and published_at > retrieved_at:
            raise ValueError(f"source {source_id} was published after it was retrieved")
        prepared.append(
            {
                "id": source_id,
                "local_file": _source_path(manifest_path, item["local_file"], source_id),
                "original_url": _normalize_url(item["original_url"], source_id),
                "title": _nonempty_string(item["title"], f"source {source_id} title"),
                "publisher": _nonempty_string(
                    item["publisher"], f"source {source_id} publisher"
                ),
                "published_at": published_at,
                "retrieved_at": retrieved_at,
                "kind": kind,
                "acquisition": acquisition,
            }
        )
    return ticker, tuple(gaps_value), sorted(prepared, key=lambda value: value["id"])


def _file_type(path: Path, raw: bytes, source_id: str) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError(f"source {source_id} has a .pdf name but no PDF signature")
        return "application/pdf", ".pdf"
    if suffix in {".html", ".htm"}:
        try:
            sample = raw[:64 * 1024].decode("utf-8", errors="strict").lower()
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"source {source_id} HTML must be valid UTF-8") from exc
        if not any(marker in sample for marker in ("<!doctype html", "<html", "<body")):
            raise ValueError(f"source {source_id} does not contain a recognizable HTML document")
        return "text/html", ".html"
    if suffix in {".txt", ".text"}:
        if b"\x00" in raw:
            raise ValueError(f"source {source_id} text contains NUL bytes")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"source {source_id} text must be valid UTF-8") from exc
        return "text/plain", ".txt"
    raise ValueError(f"source {source_id} must be a .pdf, .html, .htm, .txt, or .text file")


def _validate_pdf_python(pdf_python: Path) -> Path:
    executable = Path(pdf_python).expanduser()
    if executable.is_symlink():
        executable = executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("pdf_python must be an executable file")
    return executable.resolve()


def _extract_pdf(raw: bytes, pdf_python: Path) -> tuple[str, dict[str, Any]]:
    program = f"MAX_PAGES={MAX_PDF_PAGES}\nMAX_TEXT_BYTES={MAX_TEXT_BYTES}\n" + _PDF_PROGRAM
    try:
        result = subprocess.run(
            [str(pdf_python), "-c", program],
            input=raw,
            capture_output=True,
            check=True,
            timeout=PDF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF extraction exceeded its time allowance") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        message = detail[-1][:500] if detail else "pypdf failed"
        raise ValueError(f"PDF extraction failed: {message}") from exc
    header, separator, body = result.stdout.partition(b"\n")
    if not separator:
        raise ValueError("PDF extractor returned no metadata")
    try:
        metadata = json.loads(header.decode("utf-8"))
        text = body.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF extractor returned invalid output") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("pages"), int):
        raise ValueError("PDF extractor returned invalid metadata")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("PDF extracted text exceeds byte allowance")
    return text, metadata


def _extract(raw: bytes, media_type: str, pdf_python: Path | None) -> tuple[str, Any]:
    if media_type == "application/pdf":
        if pdf_python is None:
            raise ValueError("pdf_python is required when the manifest contains a PDF")
        return _extract_pdf(raw, _validate_pdf_python(pdf_python))
    try:
        text = extract_text(raw, media_type=media_type, charset="utf-8")
    except SourceAccessError as exc:
        raise ValueError("local document text extraction failed") from exc
    assert text is not None
    extractor = {
        "name": (
            "tradingagents.research.sources.extract_text"
            if media_type == "text/html"
            else "strict UTF-8 decode"
        ),
        "version": 1,
        "page_markers": None,
    }
    return text, extractor


def _require_full_text(text: str, source_id: str) -> bytes:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError(f"source {source_id} extracted text exceeds byte allowance")
    body = re.sub(r"^\[PDF page \d+\]\n", "", text, flags=re.MULTILINE)
    if sum(not character.isspace() for character in body) < MIN_FULL_TEXT_CHARACTERS:
        raise ValueError(f"source {source_id} has no usable full text")
    return encoded


def _destination_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _link_new(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise ValueError(f"publication target already exists: {destination}") from exc


def _write_new_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise ValueError(f"publication target already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _write_completion_marker(output: Path, evidence_bytes: bytes) -> None:
    temporary = output / ".evidence.json.incomplete"
    _write_new_file(temporary, evidence_bytes)
    try:
        _link_new(temporary, output / "evidence.json")
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
            # evidence.json, when present, is already a complete hard link.  A
            # stale hidden cleanup file does not change completion semantics.


def _publish_staged_packet(staging: Path, output: Path, evidence_bytes: bytes) -> None:
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("destination already exists; refusing to overwrite it") from exc
    for directory_name in ("raw", "text"):
        source_directory = staging / directory_name
        destination_directory = output / directory_name
        destination_directory.mkdir(mode=0o700)
        for source in sorted(source_directory.iterdir(), key=lambda path: path.name):
            _link_new(source, destination_directory / source.name)
    _link_new(staging / "acquisition.json", output / "acquisition.json")

    # Once staging is gone, no pre-completion publication work remains.  A failure
    # above intentionally leaves an inspectable partial destination without the
    # evidence.json completion marker; callers must choose a fresh destination.
    shutil.rmtree(staging)
    _write_completion_marker(output, evidence_bytes)


def _import_time(clock: Callable[[], datetime] | None) -> datetime:
    imported_at = datetime.now(timezone.utc) if clock is None else clock()
    if not isinstance(imported_at, datetime) or imported_at.tzinfo is None:
        raise ValueError("import clock must return an aware datetime")
    if imported_at.utcoffset() is None:
        raise ValueError("import clock must return an aware datetime")
    return imported_at.astimezone(timezone.utc)


def import_local_evidence(
    manifest_path: Path,
    output: Path,
    *,
    pdf_python: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> EvidenceSnapshot:
    """Freeze one explicit offline packet into a new evidence destination."""

    manifest_path = Path(manifest_path).expanduser().resolve()
    output = Path(output).expanduser().absolute()
    if _destination_exists(output):
        raise ValueError("destination already exists; use a new destination")
    ticker, gaps, entries = _validate_manifest(manifest_path)
    imported_at = _import_time(clock)
    for entry in entries:
        if entry["retrieved_at"] > imported_at:
            raise ValueError(f"source {entry['id']} retrieved_at is after the import time")
    cutoff = max(entry["retrieved_at"] for entry in entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.import-", dir=output.parent))
    documents: list[SourceDocument] = []
    provenance: list[dict[str, Any]] = []
    retained_gaps = list(gaps)
    has_pdf = False
    total_text_bytes = 0
    try:
        for entry in entries:
            source_id = entry["id"]
            raw = read_bytes(entry["local_file"], max_bytes=MAX_SOURCE_BYTES)
            if not raw:
                raise ValueError(f"source {source_id} is empty")
            media_type, archive_suffix = _file_type(entry["local_file"], raw, source_id)
            text, extractor = _extract(raw, media_type, pdf_python)
            if media_type == "application/pdf":
                has_pdf = True
                empty_pages = extractor["empty_text_page_ids"]
                if empty_pages:
                    page_ids = ", ".join(str(page_id) for page_id in empty_pages)
                    retained_gaps.append(
                        f"Source {source_id} has no extracted text on PDF page(s) {page_ids}; "
                        "they may be blank, image-only, or scanned and require visual or OCR review."
                    )
            text_bytes = _require_full_text(text, source_id)
            total_text_bytes += len(text_bytes)
            if total_text_bytes > MAX_TOTAL_TEXT_BYTES:
                raise ValueError("packet extracted text exceeds total byte allowance")
            raw_hash = _sha256(raw)
            text_hash = _sha256(text_bytes)
            raw_relative = Path("raw") / f"{source_id}{archive_suffix}"
            text_relative = Path("text") / f"{source_id}.txt"
            raw_path = staging / raw_relative
            text_path = staging / text_relative
            atomic_write(raw_path, raw)
            atomic_write(text_path, text_bytes)
            raw_path.chmod(0o444)
            text_path.chmod(0o444)
            documents.append(
                SourceDocument(
                    id=source_id,
                    url=entry["original_url"],
                    title=entry["title"],
                    publisher=entry["publisher"],
                    retrieved_at=entry["retrieved_at"],
                    published_at=entry["published_at"],
                    content=text,
                    content_sha256=text_hash,
                    kind=entry["kind"],
                    availability="full_text",
                )
            )
            provenance.append(
                {
                    "id": source_id,
                    "acquisition": entry["acquisition"],
                    "http_fetched_by_importer": False,
                    "local_file": str(entry["local_file"]),
                    "original_url": entry["original_url"],
                    "media_type": media_type,
                    "raw_archive": raw_relative.as_posix(),
                    "raw_bytes": len(raw),
                    "raw_sha256": raw_hash,
                    "text_archive": text_relative.as_posix(),
                    "text_bytes": len(text_bytes),
                    "text_sha256": text_hash,
                    "extractor": extractor,
                    "published_at": (
                        entry["published_at"].isoformat()
                        if entry["published_at"] is not None
                        else None
                    ),
                    "retrieved_at": entry["retrieved_at"].isoformat(),
                }
            )

        if has_pdf and PDF_EXTRACTION_GAP not in retained_gaps:
            retained_gaps.append(PDF_EXTRACTION_GAP)
        snapshot = EvidenceSnapshot(
            ticker=ticker,
            cutoff=cutoff,
            sources=tuple(documents),
            gaps=tuple(retained_gaps),
        )
        acquisition = {
            "schema_version": 1,
            "scope": "explicit_manifest_offline_public_documents_only",
            "coverage": "No HTTP fetch or full-web coverage is claimed by this importer.",
            "frozen_cutoff": cutoff.isoformat(),
            "sources": provenance,
        }
        evidence_bytes = canonical_json(snapshot)
        evidence_path = staging / "evidence.json"
        acquisition_path = staging / "acquisition.json"
        atomic_write(evidence_path, evidence_bytes)
        atomic_write(acquisition_path, canonical_json(acquisition))
        evidence_path.chmod(0o444)
        acquisition_path.chmod(0o444)
        _publish_staged_packet(staging, output, evidence_bytes)
        return snapshot
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pdf-python", type=Path)
    args = parser.parse_args()
    try:
        snapshot = import_local_evidence(
            args.manifest,
            args.output,
            pdf_python=args.pdf_python,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"imported {len(snapshot.sources)} sources", flush=True)
    print(f"frozen_cutoff {snapshot.cutoff.isoformat()}", flush=True)


if __name__ == "__main__":
    main()
