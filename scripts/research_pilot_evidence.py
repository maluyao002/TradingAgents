"""Explicit curated-source pilot acquisition, not automatic production coverage.

No inference, subscriptions or SEC client identity. URLs and publication dates
must be reviewed in the manifest. Freeze the cutoff after actual acquisition.
PDF extraction uses an explicitly supplied bundled Python with pypdf installed.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.sources import FileSourceCache, PublicSourceFetcher
from tradingagents.research.storage import atomic_write, canonical_json, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pdf-python", required=True, type=Path)
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    if (args.output / "evidence.json").exists():
        parser.error("frozen evidence already exists; use a new destination")
    fetcher = PublicSourceFetcher(cache=FileSourceCache(args.output / "source-cache"))
    documents, provenance = [], []
    for item in manifest["sources"]:
        fetched = fetcher.fetch(item["url"], use_cache=True)
        content = fetched.text
        extractor = "research.sources.extract_text"
        if fetched.media_type == "application/pdf":
            extraction = subprocess.run(
                [str(args.pdf_python), "-c", "import io,sys; from pypdf import PdfReader; "
                 "r=PdfReader(io.BytesIO(sys.stdin.buffer.read())); "
                 "print('\\n\\n'.join('[PDF page %d]\\n%s' % (i+1,p.extract_text()) "
                 "for i,p in enumerate(r.pages)))"], input=fetched.raw,
                capture_output=True, check=True, timeout=90)
            content = extraction.stdout.decode("utf-8")
            extractor = "pypdf with one-based PDF page markers"
            atomic_write(args.output / (item["id"] + ".pdf"), fetched.raw)
        if not content or len(content) < 500:
            raise ValueError("source has no usable full text: " + item["id"])
        document = SourceDocument(
            id=item["id"], url=fetched.final_url, title=item["title"],
            publisher=item["publisher"], retrieved_at=fetched.retrieved_at,
            published_at=item["published_at"], content=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(), kind="ir")
        documents.append(document)
        atomic_write(args.output / (item["id"] + ".txt"), content.encode())
        provenance.append({"id": item["id"], "requested_url": item["url"],
                           "final_url": fetched.final_url, "raw_sha256": fetched.raw_sha256,
                           "text_sha256": document.content_sha256, "extractor": extractor,
                           "retrieved_at": fetched.retrieved_at})
        print(item["id"], len(content), "characters", flush=True)
    snapshot = EvidenceSnapshot(ticker=manifest["ticker"], cutoff=datetime.now(timezone.utc),
        sources=tuple(documents), gaps=tuple(manifest["gaps"]))
    atomic_write(args.output / "evidence.json", canonical_json(snapshot))
    atomic_write(args.output / "acquisition.json", canonical_json(provenance))
    print("frozen_cutoff", snapshot.cutoff.isoformat(), flush=True)


if __name__ == "__main__":
    main()
