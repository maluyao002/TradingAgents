"""Offline local public-document importer tests; no network or private files."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.research_local_evidence as local_evidence
from scripts.research_local_evidence import import_local_evidence
from tradingagents.research.contracts import EvidenceSnapshot
from tradingagents.research.storage import read_json

LONG_TEXT = "Public company financial results and operating discussion. " * 20
FIXED_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    return FIXED_NOW


@pytest.mark.parametrize("value", ["2026-08-01T13:00:00Z", "2026-08-01T13:00:00+00:00",
                                 "2026-08-01T09:00:00-04:00"])
def test_timestamp_normalizes_utc_designator_for_python310(value, monkeypatch):
    class Python310Datetime:
        @staticmethod
        def fromisoformat(text):
            if text.endswith("Z"):
                raise ValueError("Python 3.10 does not accept the UTC designator")
            return datetime.fromisoformat(text)

    monkeypatch.setattr(local_evidence, "datetime", Python310Datetime)
    assert local_evidence._timestamp(value, "test") == datetime(2026, 8, 1, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["2026-08-01T13:00:00", "2026-08-01T13:00:00ZZ", "not-a-date"])
def test_timestamp_still_rejects_naive_or_malformed_inputs(value):
    with pytest.raises(ValueError):
        local_evidence._timestamp(value, "test")


def source(local_file: Path, **overrides):
    value = {
        "id": "release-q2",
        "local_file": str(local_file),
        "original_url": "https://investor.example.com/results/q2",
        "title": "Second quarter results",
        "publisher": "Example Corporation",
        "published_at": "2026-08-01T13:00:00Z",
        "retrieved_at": "2026-08-02T09:30:00-04:00",
        "kind": "ir",
        "acquisition": "browser",
    }
    value.update(overrides)
    return value


def write_manifest(path: Path, sources, **overrides) -> Path:
    value = {
        "schema_version": 1,
        "ticker": "EXM",
        "gaps": ["Manifest packet only; no full-web coverage."],
        "sources": sources,
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_import_archives_exact_bytes_hashes_and_deterministic_sources(tmp_path):
    text_path = tmp_path / "release.txt"
    text_bytes = LONG_TEXT.encode("utf-8")
    text_path.write_bytes(text_bytes)
    html_path = tmp_path / "filing.html"
    html_bytes = (
        "<!doctype html><html><body><h1>Annual filing</h1><p>"
        + LONG_TEXT
        + "</p><script>not evidence</script></body></html>"
    ).encode("utf-8")
    html_path.write_bytes(html_bytes)
    sources = [
        source(text_path, id="z-release"),
        source(
            html_path,
            id="a-filing",
            original_url="https://example.com/filing",
            kind="filing",
            acquisition="local",
            published_at="2026-07-31T12:00:00+00:00",
            retrieved_at="2026-08-01T12:00:00+00:00",
        ),
    ]
    manifest = write_manifest(tmp_path / "manifest.json", sources)

    first = tmp_path / "first"
    second = tmp_path / "second"
    snapshot = import_local_evidence(manifest, first, clock=fixed_clock)
    import_local_evidence(manifest, second, clock=fixed_clock)

    assert isinstance(snapshot, EvidenceSnapshot)
    assert [item.id for item in snapshot.sources] == ["a-filing", "z-release"]
    assert snapshot.cutoff.isoformat() == "2026-08-02T13:30:00+00:00"
    assert (first / "raw/z-release.txt").read_bytes() == text_bytes
    assert (first / "raw/a-filing.html").read_bytes() == html_bytes
    assert (first / "text/z-release.txt").read_bytes() == text_bytes
    assert (first / "evidence.json").read_bytes() == (second / "evidence.json").read_bytes()
    assert (first / "acquisition.json").read_bytes() == (
        second / "acquisition.json"
    ).read_bytes()

    provenance = read_json(first / "acquisition.json")
    assert provenance["scope"] == "explicit_manifest_offline_public_documents_only"
    assert "No HTTP fetch" in provenance["coverage"]
    by_id = {item["id"]: item for item in provenance["sources"]}
    assert by_id["z-release"]["http_fetched_by_importer"] is False
    assert by_id["z-release"]["acquisition"] == "browser"
    assert by_id["z-release"]["raw_sha256"] == hashlib.sha256(text_bytes).hexdigest()
    assert by_id["z-release"]["text_sha256"] == hashlib.sha256(text_bytes).hexdigest()
    assert by_id["a-filing"]["extractor"]["name"].endswith("extract_text")
    assert not (first / "raw/z-release.txt").stat().st_mode & 0o222


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"original_url": "http://example.com/report"}, "public HTTPS URL"),
        ({"retrieved_at": "2026-08-02T13:30:00"}, "UTC offset"),
        ({"acquisition": "http"}, "local or browser"),
        ({"id": "../escape"}, "safe for an archive filename"),
    ],
)
def test_malformed_source_metadata_is_rejected(tmp_path, change, message):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(tmp_path / "manifest.json", [source(document, **change)])

    with pytest.raises(ValueError, match=message):
        import_local_evidence(manifest, tmp_path / "output", clock=fixed_clock)
    assert not (tmp_path / "output").exists()


def test_duplicate_ids_are_rejected_before_output(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text(LONG_TEXT, encoding="utf-8")
    second.write_text(LONG_TEXT + "second", encoding="utf-8")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [source(first, id="same"), source(second, id="same")],
    )

    with pytest.raises(ValueError, match="duplicate source id"):
        import_local_evidence(manifest, tmp_path / "output", clock=fixed_clock)
    assert not (tmp_path / "output").exists()


def test_case_only_duplicate_ids_cannot_collide_in_archive(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text(LONG_TEXT, encoding="utf-8")
    second.write_text(LONG_TEXT + "second", encoding="utf-8")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [source(first, id="Release"), source(second, id="release")],
    )

    with pytest.raises(ValueError, match="duplicate source id"):
        import_local_evidence(manifest, tmp_path / "output", clock=fixed_clock)


def test_existing_destination_is_never_overwritten(tmp_path):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(tmp_path / "manifest.json", [source(document)])
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="destination already exists"):
        import_local_evidence(manifest, output, clock=fixed_clock)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert document.read_text(encoding="utf-8") == LONG_TEXT


def test_concurrently_created_empty_destination_is_not_replaced(tmp_path, monkeypatch):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(tmp_path / "manifest.json", [source(document)])
    output = tmp_path / "raced-output"
    output.mkdir()
    monkeypatch.setattr(local_evidence, "_destination_exists", lambda _path: False)

    with pytest.raises(ValueError, match="destination already exists"):
        import_local_evidence(manifest, output, clock=fixed_clock)
    assert list(output.iterdir()) == []


def test_publication_failure_preserves_partial_without_completion_marker(
    tmp_path, monkeypatch
):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(tmp_path / "manifest.json", [source(document)])
    output = tmp_path / "partial-output"

    def fail_completion(_path, _content):
        raise OSError("simulated completion-marker failure")

    monkeypatch.setattr(local_evidence, "_write_new_file", fail_completion)
    with pytest.raises(OSError, match="completion-marker failure"):
        import_local_evidence(manifest, output, clock=fixed_clock)

    assert output.is_dir()
    assert (output / "raw/release-q2.txt").is_file()
    assert (output / "text/release-q2.txt").is_file()
    assert (output / "acquisition.json").is_file()
    assert not (output / "evidence.json").exists()


def test_published_after_retrieval_is_rejected(tmp_path):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [
            source(
                document,
                published_at="2026-08-03T00:00:00Z",
                retrieved_at="2026-08-02T00:00:00Z",
            )
        ],
    )

    with pytest.raises(ValueError, match="published after"):
        import_local_evidence(manifest, tmp_path / "output", clock=fixed_clock)


def test_future_retrieval_is_rejected_with_injected_utc_clock(tmp_path):
    document = tmp_path / "document.txt"
    document.write_text(LONG_TEXT, encoding="utf-8")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [source(document, retrieved_at="2026-09-20T12:00:01Z")],
    )

    with pytest.raises(ValueError, match="retrieved_at is after the import time"):
        import_local_evidence(manifest, tmp_path / "output", clock=fixed_clock)
    assert not (tmp_path / "output").exists()


def test_file_type_and_full_text_are_validated(tmp_path):
    fake_pdf = tmp_path / "document.pdf"
    fake_pdf.write_text(LONG_TEXT, encoding="utf-8")
    fake_manifest = write_manifest(tmp_path / "fake.json", [source(fake_pdf)])
    with pytest.raises(ValueError, match="no PDF signature"):
        import_local_evidence(fake_manifest, tmp_path / "fake-output", clock=fixed_clock)

    short_text = tmp_path / "short.txt"
    short_text.write_text("only a snippet", encoding="utf-8")
    short_manifest = write_manifest(tmp_path / "short.json", [source(short_text)])
    with pytest.raises(ValueError, match="no usable full text"):
        import_local_evidence(short_manifest, tmp_path / "short-output", clock=fixed_clock)


def _pdf_runtime() -> Path | None:
    candidates = [os.environ.get("RESEARCH_PDF_PYTHON"), sys.executable]
    for candidate in candidates:
        if not candidate:
            continue
        probe = subprocess.run(
            [candidate, "-c", "import pypdf, reportlab"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return Path(candidate)
    return None


def test_pdf_extraction_has_page_markers_when_dependencies_are_available(tmp_path):
    pdf_python = _pdf_runtime()
    if pdf_python is None:
        pytest.skip("pypdf/reportlab runtime is unavailable")
    pdf = tmp_path / "packet.pdf"
    program = (
        "from reportlab.pdfgen.canvas import Canvas\n"
        "import sys\n"
        "c=Canvas(sys.argv[1])\n"
        f"c.drawString(72,720,{('First page ' + LONG_TEXT)!r})\n"
        "c.showPage()\n"
        "c.showPage()\n"
        f"c.drawString(72,720,{('Second page ' + LONG_TEXT)!r})\n"
        "c.save()\n"
    )
    subprocess.run([str(pdf_python), "-c", program, str(pdf)], check=True)
    manifest = write_manifest(tmp_path / "manifest.json", [source(pdf)])

    output = tmp_path / "output"
    snapshot = import_local_evidence(
        manifest, output, pdf_python=pdf_python, clock=fixed_clock
    )

    assert "[PDF page 1]" in snapshot.sources[0].content
    assert "[PDF page 2]" in snapshot.sources[0].content
    assert "[PDF page 3]" in snapshot.sources[0].content
    assert any("without OCR" in gap for gap in snapshot.gaps)
    assert any("page(s) 2" in gap for gap in snapshot.gaps)
    provenance = read_json(output / "acquisition.json")["sources"][0]
    assert provenance["extractor"]["name"] == "pypdf.PdfReader.page.extract_text"
    assert provenance["extractor"]["pages"] == 3
    assert provenance["extractor"]["empty_text_page_count"] == 1
    assert provenance["extractor"]["empty_text_page_ids"] == [2]
    assert provenance["raw_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    EvidenceSnapshot.model_validate(read_json(output / "evidence.json"))
