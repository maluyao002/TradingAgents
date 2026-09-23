"""Assemble captured public evidence into a fresh, offline-only draft case.

Exact excerpts are authored inputs, not automated factual approvals. No old
review, checkpoint, quote freshness, or model usage allowance is carried forward.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cli.research import _resolve_paths
from tradingagents.research.case_context import FinancialCaseEnvelope, load_case_context
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest, SourceDocument
from tradingagents.research.evidence import validate_snapshot
from tradingagents.research.financial_case import FinancialCase, evidence_snapshot_sha256
from tradingagents.research.operating_scenarios import (
    ForecastSourceMaterial,
    OperatingScenarioPackage,
)
from tradingagents.research.sources import (
    FileSourceCache,
    SourceAccessError,
    normalize_public_https_url,
)
from tradingagents.research.storage import (
    atomic_write,
    canonical_json,
    digest,
    parse_json,
    read_bytes,
)

MAX_SOURCES = 8
MAX_PASSAGES = 24
MAX_PASSAGE_CHARS = 40_000
MAX_SOURCE_RAW_BYTES = 8 * 1024 * 1024
MAX_SOURCE_TEXT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_RAW_BYTES = 32 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 16 * 1024 * 1024
FRESHNESS_GAP = (
    "This is a targeted evidence extension, not a complete as-of refresh. Legacy "
    "financial facts, quotes, expectations and gaps retain their original dates; "
    "a later cutoff does not roll forward balances or establish current market inputs."
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _aware(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _document_identity(source):
    try:
        url = normalize_public_https_url(source.url)
    except SourceAccessError:
        # Historical metadata can use non-fetchable origins; preserve it rather
        # than relabeling or silently dropping the original source.
        url = source.url
    return url, source.content_sha256


def _capture(path, pinned):
    """Read and authenticate one v3 capture, including its exact original input."""
    path = path.resolve(strict=True)
    raw = read_bytes(path / "capture_manifest.json")
    source_raw = read_bytes(path / "source_manifest.json")
    manifest, source = parse_json(raw), parse_json(source_raw)
    if (manifest.get("schema_version") != 3
            or manifest.get("packet_type") != "public_source_capture"
            or manifest.get("admission", {}).get("status") != "not_admissible_to_frozen_case"
            or _sha(source_raw) != manifest["source_manifest"]["content_sha256"]
            or source["case_cutoff"] != manifest["source_case_cutoff"]):
        raise ValueError("capture provenance is invalid or requires investigation")
    cutoff = _aware(manifest["source_case_cutoff"])
    if _aware(manifest["captured_at"]) <= cutoff:
        raise ValueError("capture timestamp must follow its frozen cutoff")
    records = manifest["records"]
    inputs = source["sources"]
    if (len({r["id"] for r in records}) != len(records)
            or len({r["id"] for r in inputs}) != len(inputs)):
        raise ValueError("duplicate capture source identities")
    inputs = {r["id"]: r for r in inputs}
    for record in records:
        if (record["id"] not in inputs
                or normalize_public_https_url(record["requested_url"])
                != normalize_public_https_url(inputs[record["id"]]["source_url"])):
            raise ValueError("capture URL differs from its source manifest")
    pinned[path / "capture_manifest.json"] = raw
    pinned[path / "source_manifest.json"] = source_raw
    return {r["id"]: r for r in records}, cutoff


def _selected_sources(selection, selection_path, cutoff, pinned):
    if selection.get("schema_version") != 1:
        raise ValueError("unsupported selection schema")
    rows = selection.get("sources")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SOURCES:
        raise ValueError("select between one and eight sources")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate selected source identities")
    sources, passages, audit, captures = [], [], [], {}
    documents = set()
    total_raw = total_text = 0
    for row in rows:
        path = Path(row["capture_directory"])
        if not path.is_absolute():
            path = selection_path.parent / path
        path = path.resolve(strict=True)
        if path not in captures:
            captures[path] = _capture(path, pinned)
        records, old_cutoff = captures[path]
        record = records.get(row["id"])
        if record is None or record["status"] != "captured":
            raise ValueError("selected source was not successfully captured")
        # Bound bytes before cache deserialization or repeated full-text model
        # construction. FileSourceCache also bounds its own reads against races.
        blobs = path / "source-cache"
        for key in ("raw_sha256", "text_sha256"):
            value = record[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("invalid captured blob digest")
        raw_size = (blobs / "raw" / record["raw_sha256"]).stat().st_size
        text_size = (blobs / "text" / record["text_sha256"]).stat().st_size
        total_raw += raw_size
        total_text += text_size
        if (raw_size > MAX_SOURCE_RAW_BYTES or text_size > MAX_SOURCE_TEXT_BYTES
                or total_raw > MAX_TOTAL_RAW_BYTES or total_text > MAX_TOTAL_TEXT_BYTES):
            raise ValueError("captured documents exceed packet byte allowance")
        fetched = FileSourceCache(blobs, max_bytes=MAX_SOURCE_RAW_BYTES).get(record["requested_url"])
        if fetched is None or not fetched.text:
            raise ValueError("selected full-text cache is missing")
        if len(fetched.raw) != raw_size or len(fetched.text.encode("utf-8")) != text_size:
            raise ValueError("captured blob size changed during selection")
        if (fetched.status != 200 or record["http_status"] != 200
                or fetched.final_url != record["final_url"]
                or fetched.raw_sha256 != record["raw_sha256"]
                or fetched.text_sha256 != record["text_sha256"]
                or fetched.text_sha256 != row["text_sha256"]
                or fetched.retrieved_at != _aware(record["retrieved_at"])
                or fetched.redirects != tuple(record["redirects"])
                or fetched.media_type != record["media_type"]
                or len(fetched.text) != record["text_characters"]
                or not old_cutoff < fetched.retrieved_at <= cutoff
                or normalize_public_https_url(row["source_url"]) != fetched.final_url):
            raise ValueError("selected source differs from capture or is after the new cutoff")
        for url in (fetched.final_url, *fetched.redirects):
            normalize_public_https_url(url)
        identity = (normalize_public_https_url(fetched.final_url), fetched.text_sha256)
        if identity in documents:
            raise ValueError("duplicate captured document under multiple identities")
        documents.add(identity)
        # Pin the cache metadata and blobs actually used, not a subsequent reread.
        pinned[path / "source-cache" / "raw" / fetched.raw_sha256] = fetched.raw
        pinned[path / "source-cache" / "text" / fetched.text_sha256] = fetched.text.encode("utf-8")
        index = path / "source-cache" / "indexes" / f"{_sha(fetched.requested_url.encode())}.json"
        index_raw = read_bytes(index)
        metadata = parse_json(index_raw)
        if (metadata["raw_sha256"] != fetched.raw_sha256
                or metadata["text_sha256"] != fetched.text_sha256
                or _aware(metadata["retrieved_at"]) != fetched.retrieved_at
                or metadata["final_url"] != fetched.final_url):
            raise ValueError("cache index changed during selection")
        pinned[index] = index_raw
        for key in ("published_at", "publication_basis", "title", "publisher", "independence"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"selected source requires {key}")
        # Retain date-only publication evidence in the audit without fabricating a
        # midnight timestamp. Availability at observed retrieval is conservative.
        publication_day = datetime.fromisoformat(row["published_at"]).date()
        if publication_day > fetched.retrieved_at.date():
            raise ValueError("claimed publication date is after observed retrieval")
        sources.append(SourceDocument(
            id=row["id"], url=fetched.final_url, title=row["title"], publisher=row["publisher"],
            retrieved_at=fetched.retrieved_at, published_at=fetched.retrieved_at,
            content=fetched.text, content_sha256=fetched.text_sha256,
            kind=row["kind"], availability="full_text",
        ))
        if not row["passages"]:
            raise ValueError("every selected source requires an exact passage")
        for item in row["passages"]:
            start, end, text = item["start"], item["end"], item["exact_text"]
            if (type(start) is not int or type(end) is not int or start < 0
                    or end > len(fetched.text) or fetched.text[start:end] != text):
                raise ValueError("authored excerpt differs from captured text")
            for key in ("claim", "boundary", "topic"):
                if not isinstance(item[key], str) or not item[key].strip():
                    raise ValueError(f"authored excerpt requires {key}")
            passages.append(ForecastSourceMaterial(
                id=item["id"], source_id=row["id"], source_sha256=fetched.text_sha256,
                start=start, end=end, text=text,
                context=(f"Topic: {item['topic']}. Authored source claim: {item['claim']} "
                         f"Boundary: {item['boundary']} Independence: {row['independence']} "
                         f"Reported publication date: {row['published_at']}; basis: {row['publication_basis']}. "
                         "Exact text integrity is not economic endorsement or automatic issue closure."),
            ))
        audit.append({
            "id": row["id"], "capture_directory": str(path),
            "capture_manifest_sha256": _sha(pinned[path / "capture_manifest.json"]),
            "raw_sha256": fetched.raw_sha256, "text_sha256": fetched.text_sha256,
            "reported_publication_date": row["published_at"], "publication_basis": row["publication_basis"],
            "publication_timestamp_policy": "observed_retrieval_as_conservative_availability",
            "retrieved_at": fetched.retrieved_at, "independence": row["independence"],
        })
    if (len(passages) > MAX_PASSAGES or sum(len(p.text) for p in passages) > MAX_PASSAGE_CHARS
            or len({p.id for p in passages}) != len(passages)):
        raise ValueError("duplicate passages or bounded passage allowance exceeded")
    return sources, passages, audit


def _require_unchanged(pinned):
    for path, expected in pinned.items():
        if read_bytes(path, max_bytes=max(len(expected), 1)) != expected:
            raise ValueError(f"input changed during packet assembly: {path.name}")


def prepare(source_config: Path, selections: Path, output: Path, *, cutoff: datetime) -> dict:
    """Create a fresh replay-only case; all independent reviews must be renewed."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None or cutoff > datetime.now(timezone.utc):
        raise ValueError("new cutoff must be aware and not in the future")
    if output.is_symlink():
        raise ValueError("output must not be a symlink")
    source_config, selections = source_config.resolve(strict=True), selections.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError("output must be new")
    pinned = {source_config: read_bytes(source_config), selections: read_bytes(selections)}
    old_request = ResearchRequest.model_validate(_resolve_paths(parse_json(pinned[source_config]), source_config.parent))
    if old_request.evidence_path is None or old_request.financial_case_path is None:
        raise ValueError("source request requires evidence and financial case")
    for path in (old_request.evidence_path, old_request.financial_case_path):
        pinned[path] = read_bytes(path)
    old_snapshot = EvidenceSnapshot.model_validate_json(pinned[old_request.evidence_path])
    if validate_snapshot(old_snapshot, old_request) != old_snapshot:
        raise ValueError("original snapshot requires normalization")
    # Validate original case/package before considering a changed identity.
    original = load_case_context(pinned[old_request.financial_case_path], old_request, old_snapshot)
    old_envelope = FinancialCaseEnvelope.model_validate_json(pinned[old_request.financial_case_path])
    if old_envelope.cashflow_bridge is not None:
        raise ValueError("reassemble an existing cash-flow package explicitly; cannot silently drop it")
    if not original.operating_scenarios or not original.operating_scenarios.reviewed:
        raise ValueError("original conditional operating scenarios require a valid review")
    if cutoff <= old_snapshot.cutoff:
        raise ValueError("new cutoff must advance the frozen snapshot")
    sources, passages, source_audit = _selected_sources(parse_json(pinned[selections]), selections, cutoff, pinned)
    old_documents = {_document_identity(s) for s in old_snapshot.sources}
    if any(_document_identity(s) in old_documents for s in sources):
        raise ValueError("selected document already exists in the frozen snapshot")
    if any(output == path.parent or path.parent in output.parents for path in pinned):
        raise ValueError("output must be outside input directories")
    payload = old_snapshot.model_dump(mode="json")
    payload.update(cutoff=cutoff, sources=[*old_snapshot.sources, *sources], gaps=[*old_snapshot.gaps, FRESHNESS_GAP])
    snapshot = EvidenceSnapshot.model_validate(payload)
    case = FinancialCase.model_validate({**old_envelope.case.model_dump(mode="json"),
                                        "cutoff": cutoff, "snapshot_sha256": evidence_snapshot_sha256(snapshot)})
    operating = OperatingScenarioPackage.model_validate({
        **old_envelope.operating_scenarios.model_dump(mode="json"), "cutoff": cutoff,
        "case_sha256": digest(case), "evidence_sha256": evidence_snapshot_sha256(snapshot),
        "source_material": [*old_envelope.operating_scenarios.source_material, *passages], "review": None,
    })
    envelope = FinancialCaseEnvelope(case=case, review=None,
                                    source_passages=old_envelope.source_passages, operating_scenarios=operating)
    request = ResearchRequest.model_validate({
        **old_request.model_dump(mode="json"), "cutoff": cutoff, "backend": "replay",
        "report_language": "English", "internal_language": "English", "additional_report_languages": (),
        "evidence_path": output / "evidence.json", "financial_case_path": output / "case_input.json",
        "output_dir": output / "run", "prior_dossier_path": None, "dossier_dir": None,
    })
    if validate_snapshot(snapshot, request) != snapshot:
        raise ValueError("new evidence requires normalization")
    context = load_case_context(canonical_json(envelope), request, snapshot)
    readiness = {
        "schema_version": 1, "status": "eligible_targeted_extension_pending_independent_review",
        "model_calls": 0, "network_calls": 0, "report_generated": False, "live_authorized": False,
        "new_sources": len(sources), "new_passages": len(passages),
        "new_passage_characters": sum(len(p.text) for p in passages),
        "prior_cutoff": old_snapshot.cutoff, "cutoff": cutoff,
        "case_reviewed": context.reviewed, "operating_reviewed": context.operating_scenarios.reviewed,
        "inherited_reviews_cleared": True, "old_analysis_reuse_authorized": False,
        "historical_usage_reset": False, "freshness_limit": FRESHNESS_GAP,
        "budget_meaning": "Copied replay configuration only; not a live allowance or known remaining historical budget.",
        "next": "Independently review the exact new case/operating package and passages; "
                "then assemble/review the cash-flow bridge and measure actual payload delivery. "
                "A separately bounded fresh analysis is required; replaying old answers is not acceptance.",
        "source_audit": source_audit,
    }
    outputs = {**context.artifacts, "request.json": canonical_json(request),
               "evidence.json": canonical_json(snapshot), "case_input.json": canonical_json(envelope),
               "followup_source_material.json": canonical_json(passages),
               "authored_selections.json": pinned[selections], "readiness.json": canonical_json(readiness)}
    _require_unchanged(pinned)
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        atomic_write(output / name, raw)
    atomic_write(output / "manifest.json", canonical_json({
        "schema_version": 1, "artifact_hashes": {name: _sha(raw) for name, raw in outputs.items()},
        "input_hashes": {str(path): _sha(raw) for path, raw in pinned.items()},
        "scope": "Offline eligible targeted extension; no financial, review, report, or production acceptance.",
    }))
    return readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    result = prepare(args.source_config, args.selections, args.output, cutoff=_aware(args.cutoff))
    print(canonical_json({key: result[key] for key in ("status", "new_sources", "new_passages", "model_calls")}).decode())


if __name__ == "__main__":
    main()
