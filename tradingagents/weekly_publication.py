"""Prepare and track private Google Docs publication for weekly batches.

This module deliberately has no Google client or credentials.  The caller uploads
the generated HTML through the connected Drive tooling and records the observed
IDs here.  Keeping those concerns separate makes an interrupted upload safe to
resume without rerunning analysis or blindly creating another document.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from markdown_it import MarkdownIt

_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


def markdown_to_html(markdown: str) -> str:
    """Render report Markdown as safe, import-friendly HTML without altering text."""
    rendered = _MARKDOWN.render(markdown.replace("\r\n", "\n").replace("\r", "\n"))
    # Reports intentionally use ``<br>`` inside generated table cells. Keep only
    # that inert tag usable; arbitrary raw HTML remains escaped by MarkdownIt.
    rendered = re.sub(r"&lt;br\s*/?&gt;", "<br>", rendered, flags=re.IGNORECASE)
    return "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>" + rendered + "</body></html>"


def prepare_publication(manifest_path: str | Path, summary_file: str | Path | None = None) -> dict[str, Any]:
    """Stage full reports and a deterministic digest, then update publication only."""
    manifest_path = Path(manifest_path).resolve()
    with _batch_lock(manifest_path.parent):
        manifest = _read_manifest(manifest_path)
        _validate_manifest(manifest)
        publication = manifest.setdefault("publication", {"folder_id": None, "digest": {}, "companies": {}})
        publication.setdefault("folder_id", None)
        publication.setdefault("companies", {})
        publication.setdefault("digest", {})
        company_entries: list[dict[str, Any]] = []
        for ticker, company in sorted(manifest["companies"].items()):
            entry = _prepare_company(
                manifest_path.parent, manifest["batch_id"], ticker, company,
                publication["companies"].get(ticker, {}),
            )
            publication["companies"][ticker] = entry
            company_entries.append(entry)
        digest = _prepare_digest(manifest_path.parent, manifest, company_entries, publication["digest"], summary_file)
        publication["digest"] = digest
        manifest["updated_at"] = _now()
        _atomic_write(manifest_path, manifest)
        return {"batch_id": manifest["batch_id"], "companies": [_public_entry(entry) for entry in company_entries], "digest": _public_entry(digest)}


def record_folder(manifest_path: str | Path, folder_id: str) -> None:
    _require_id(folder_id, "folder ID")
    _update_publication(manifest_path, lambda publication: publication.__setitem__("folder_id", folder_id))


def record_document(manifest_path: str | Path, name: str, document_id: str, url: str, *, digest: bool = False) -> None:
    """Record an observed Drive result. It remains unverified until readback."""
    _require_id(document_id, "document ID")
    if _document_id_from_url(url) != document_id:
        raise ValueError("URL must be an observed Google Docs URL for the supplied document ID")

    def update(publication: dict[str, Any]) -> None:
        target = publication.setdefault("digest", {}) if digest else publication.setdefault("companies", {}).get(name)
        if not isinstance(target, dict) or not target.get("content_sha256"):
            raise ValueError("prepare publication before recording a document")
        target.update({"document_id": document_id, "url": url, "uploaded_content_sha256": target["content_sha256"], "upload_status": "uploaded", "verified": False})

    _update_publication(manifest_path, update)


def mark_verified(manifest_path: str | Path, name: str, *, digest: bool = False) -> None:
    """Mark a document verified after the connector has read it back."""
    def update(publication: dict[str, Any]) -> None:
        target = publication.setdefault("digest", {}) if digest else publication.setdefault("companies", {}).get(name)
        if not isinstance(target, dict) or not target.get("document_id") or not target.get("url"):
            raise ValueError("cannot verify a document that has not been recorded")
        if target.get("uploaded_content_sha256") != target.get("content_sha256"):
            raise ValueError("cannot verify a document whose prepared content changed")
        if _document_id_from_url(target["url"]) != target["document_id"]:
            raise ValueError("recorded document URL is invalid")
        target["verified"] = True
        target["upload_status"] = "verified"

    _update_publication(manifest_path, update)


def publication_resume_plan(manifest_path: str | Path) -> list[dict[str, Any]]:
    """Return only documents still requiring upload/readback, never create them."""
    manifest = _read_manifest(Path(manifest_path))
    publication = manifest.get("publication", {})
    folder_id = publication.get("folder_id")
    plans = []
    for ticker, entry in sorted(publication.get("companies", {}).items()):
        if not entry.get("verified") and entry.get("upload_status") in {"prepared", "uploaded", "needs_update", "reconcile"}:
            plans.append({"kind": "company", "ticker": ticker, "target_folder_id": folder_id, **_public_entry(entry)})
    digest = publication.get("digest", {})
    if digest and not digest.get("verified") and digest.get("upload_status") in {"prepared", "uploaded", "needs_update", "reconcile"}:
        plans.append({"kind": "digest", "target_folder_id": folder_id, **_public_entry(digest)})
    return plans


def _prepare_company(
    batch_root: Path, batch_id: str, ticker: str, company: dict[str, Any], prior: dict[str, Any],
) -> dict[str, Any]:
    status = company.get("status")
    if status not in {"completed", "degraded"}:
        return {
            "ticker": ticker,
            "title": f"{batch_id} — {ticker} Weekly Watchlist Report",
            "status": status or "pending",
            "analysis_date": company.get("analysis_date"),
            "quality": _quality(company),
            "error": company.get("error"),
            "upload_status": "not_ready",
            "verified": False,
        }
    report_dir = Path(company.get("report_dir", ""))
    if not report_dir.is_absolute():
        raise ValueError(f"{ticker}: report_dir must be absolute")
    source = report_dir / "complete_report.md"
    if not source.is_file():
        raise FileNotFoundError(f"{ticker}: missing complete_report.md in report_dir")
    text = source.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = f"{batch_id}:{ticker}:{content_hash}"
    staging = batch_root / "publication" / "companies" / f"{ticker}-{content_hash[:12]}.html"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(markdown_to_html(text), encoding="utf-8")
    entry = {"ticker": ticker, "title": f"{batch_id} — {ticker} Weekly Watchlist Report", "status": status, "analysis_date": company.get("analysis_date"), "source_file": str(source), "content_sha256": content_hash, "publication_key": key, "html_file": str(staging), "quality": _quality(company), "executive_excerpt": _portfolio_excerpt(text) if _quality(company)["accepted"] else None, "upload_status": "prepared", "verified": False}
    if _same_content(prior, entry):
        entry.update({key: prior[key] for key in ("document_id", "url", "upload_status", "verified") if key in prior})
        if "uploaded_content_sha256" in prior:
            entry["uploaded_content_sha256"] = prior["uploaded_content_sha256"]
    elif prior.get("document_id") and prior.get("url"):
        entry.update({key: prior[key] for key in ("document_id", "url", "uploaded_content_sha256") if key in prior})
        entry.update({"verified": False, "upload_status": "needs_update"})
    return entry


def _prepare_digest(batch_root: Path, manifest: dict[str, Any], entries: list[dict[str, Any]], prior: dict[str, Any], summary_file: str | Path | None) -> dict[str, Any]:
    sources = {entry["ticker"]: [entry.get("content_sha256"), entry.get("status"), entry.get("analysis_date")]
               for entry in entries}
    summary = _summary_markdown(summary_file) if summary_file is not None else (
        prior.get("summary_markdown", "") if prior.get("summary_sources") == sources else ""
    )
    markdown = _digest_markdown(manifest, entries, summary)
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    staging = batch_root / "publication" / f"weekly-digest-{content_hash[:12]}.html"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(markdown_to_html(markdown), encoding="utf-8")
    entry = {"title": f"Weekly Watchlist Digest — {manifest['batch_id']}", "source_file": None, "content_sha256": content_hash, "publication_key": f"{manifest['batch_id']}:digest:{content_hash}", "html_file": str(staging), "markdown": markdown, "upload_status": "prepared", "verified": False}
    entry.update(summary_markdown=summary, summary_sources=sources)
    if _same_content(prior, entry):
        entry.update({key: prior[key] for key in ("document_id", "url", "upload_status", "verified") if key in prior})
        if "uploaded_content_sha256" in prior:
            entry["uploaded_content_sha256"] = prior["uploaded_content_sha256"]
    elif prior.get("document_id") and prior.get("url"):
        entry.update({key: prior[key] for key in ("document_id", "url", "uploaded_content_sha256") if key in prior})
        entry.update({"verified": False, "upload_status": "needs_update"})
    return entry


def _digest_markdown(manifest: dict[str, Any], entries: list[dict[str, Any]], summary: str) -> str:
    rows = ["| Ticker | Analysis date | Status | Quality | Signal | Report |", "|---|---|---|---|---|---|"]
    notes = [f"# Weekly Watchlist Digest — {manifest['batch_id']}", "", "This digest links to the complete reports. It does not replace them.", ""]
    for entry in entries:
        ticker, status = entry["ticker"], entry.get("status", "pending")
        accepted = entry.get("quality", {}).get("accepted") is True
        quality = "Accepted" if accepted else "Needs review"
        signal = entry.get("quality", {}).get("signal") if accepted else ("REVIEW" if status in {"completed", "degraded"} else "—")
        link = f"[Open full report]({entry['url']})" if entry.get("verified") and entry.get("url") else "Pending verification"
        rows.append(f"| {ticker} | {entry.get('analysis_date') or '—'} | {status} | {quality} | {signal or '—'} | {link} |")
        if status in {"failed", "blocked", "pending", "running"}:
            notes.append(f"- {ticker}: {status}. {_error_message(entry.get('error')) or 'No report is available yet.'}")
        elif not accepted:
            reason = "; ".join(_reason_text(value) for value in entry.get("quality", {}).get("reasons", [])) or "quality gate did not accept this report"
            notes.append(f"- {ticker}: Needs review — {reason}. Read the full report before using it.")
        elif not summary and entry.get("executive_excerpt"):
            notes.append(f"## {ticker} executive-summary excerpt\n\n{entry['executive_excerpt']}")
    return "\n".join(notes + (["", "## Weekly overview", "", summary] if summary else []) + ["", *rows, ""])


def _quality(company: dict[str, Any]) -> dict[str, Any]:
    quality = company.get("quality") if isinstance(company.get("quality"), dict) else {}
    return {"accepted": quality.get("accepted") is True, "signal": quality.get("signal"), "reasons": quality.get("reasons") if isinstance(quality.get("reasons"), list) else []}


def _portfolio_excerpt(text: str) -> str | None:
    match = re.search(r"^## Portfolio Manager Decision\s*$\n?(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    section = match.group(1).strip()
    if not section:
        return None
    # The report begins with a subheading and rating before its actual summary.
    summary = re.search(r"^\*\*Executive Summary\*\*\s*:\s*(.+?)(?=\n\s*\n|\Z)",
                        section, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return summary.group(1).strip() if summary else None


def _reason_text(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("\n", " ")
    if isinstance(value, dict) and isinstance(value.get("code"), str):
        code = value["code"].replace("_", " ")
        role = value.get("role")
        return f"{role}: {code}" if isinstance(role, str) else code
    return "quality check failed"


def _summary_markdown(summary_file: str | Path | None) -> str:
    if summary_file is None:
        return ""
    path = Path(summary_file)
    if path.suffix.lower() != ".md" or not path.is_file():
        raise ValueError("summary file must be an existing Markdown file")
    return path.read_text(encoding="utf-8").strip()


def _error_message(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        return value["message"]
    return value if isinstance(value, str) else None


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    allowed = ("ticker", "title", "status", "analysis_date", "source_file", "content_sha256", "publication_key", "html_file", "quality", "error", "document_id", "url", "uploaded_content_sha256", "upload_status", "verified")
    return {key: entry[key] for key in allowed if key in entry}


def _same_content(prior: dict[str, Any], entry: dict[str, Any]) -> bool:
    return prior.get("content_sha256") == entry["content_sha256"] and prior.get("title") == entry["title"]


def _update_publication(manifest_path: str | Path, callback) -> None:
    path = Path(manifest_path).resolve()
    with _batch_lock(path.parent):
        manifest = _read_manifest(path)
        publication = manifest.setdefault("publication", {"folder_id": None, "digest": {}, "companies": {}})
        callback(publication)
        manifest["updated_at"] = _now()
        _atomic_write(path, manifest)


def _batch_lock(batch_root: Path):
    # Import only at call time so this module remains independently importable.
    from tradingagents.weekly import batch_lock
    return batch_lock(batch_root)


def _read_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    # The runner owns the JSON writer; it provides the batch-wide atomic contract.
    from tradingagents.weekly import write_json
    write_json(path, value)


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("batch_id"), str):
        raise ValueError("expected weekly manifest schema_version 1 and batch_id")
    if not isinstance(manifest.get("companies"), dict):
        raise ValueError("manifest companies must be a mapping")


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"invalid Google Drive {label}")


def _document_id_from_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc not in {"docs.google.com", "drive.google.com"}:
        return None
    if parsed.netloc == "docs.google.com":
        match = re.fullmatch(r"/document/d/([A-Za-z0-9_-]{10,200})(?:/.*)?", parsed.path)
        return match.group(1) if match else None
    ids = parse_qs(parsed.query).get("id", [])
    return ids[0] if parsed.path in {"/open", "/document/d"} and len(ids) == 1 and _ID.fullmatch(ids[0]) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
