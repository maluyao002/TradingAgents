"""Exact-source, opt-in reader disclosures for a numbered continuation.

This validates provenance and syntax only. The ordinary factual and atomic
coverage reviews must still decide whether each proposition is supported.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .contracts import EvidenceSnapshot
from .storage import digest

DISCLOSURE_POLICY = "frozen-source-controlled-disclosure-v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_ENTRIES = 12
_MAX_TEXT = 2200


def validate_disclosure_packet(
    packet: dict[str, Any], snapshot: EvidenceSnapshot, *,
    candidate_stage: str, candidate_sha256: str, terminal_review_sha256: str,
    evidence_sha256: str, case_context_sha256: str,
    issue_ids: set[str], finding_affected_ids: dict[str, set[str]],
) -> dict[str, Any]:
    """Return a canonical, source-checked packet or fail before dispatch."""
    required = {"schema_version", "source_candidate_stage", "source_candidate_sha256",
                "source_terminal_review_sha256", "evidence_sha256", "case_context_sha256",
                "entries"}
    if not isinstance(packet, dict) or set(packet) != required or packet["schema_version"] != 1:
        raise ValueError("controlled disclosure packet shape is invalid")
    if (packet["source_candidate_stage"] != candidate_stage
            or packet["source_candidate_sha256"] != candidate_sha256
            or packet["source_terminal_review_sha256"] != terminal_review_sha256
            or packet["evidence_sha256"] != evidence_sha256
            or packet["case_context_sha256"] != case_context_sha256):
        raise ValueError("controlled disclosure packet is bound to another source")
    entries = packet["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= _MAX_ENTRIES:
        raise ValueError("controlled disclosure entries are missing or excessive")
    sources = {source.id: source for source in snapshot.sources}
    seen_issues: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "issue_ids", "terminal_finding_sha256s", "text", "source_passages"
        }:
            raise ValueError("controlled disclosure entry shape is invalid")
        ids = entry["issue_ids"]
        hashes = entry["terminal_finding_sha256s"]
        prose = entry["text"]
        passages = entry["source_passages"]
        if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or any(value not in issue_ids or value in seen_issues for value in ids)
                or not isinstance(hashes, list) or not hashes
                or len(hashes) != len(set(hashes))
                or any(value not in finding_affected_ids for value in hashes)
                or any(not set(ids).intersection(finding_affected_ids[value])
                       for value in hashes)
                or any(not any(identifier in finding_affected_ids[value]
                                   for value in hashes) for identifier in ids)):
            raise ValueError("controlled disclosure issue or finding binding is invalid")
        seen_issues.update(ids)
        if (not isinstance(prose, str) or not prose.strip() or len(prose) > _MAX_TEXT
                or "\n" in prose or "\r" in prose or "[^" in prose or "{{" in prose
                or "<a " in prose or "#" in prose):
            raise ValueError("controlled disclosure prose must be one plain paragraph")
        if not isinstance(passages, list) or not passages or len(passages) > 6:
            raise ValueError("controlled disclosure requires bounded source passages")
        for passage in passages:
            if not isinstance(passage, dict) or set(passage) != {
                "source_id", "document_sha256", "start", "end", "exact_text"
            }:
                raise ValueError("controlled disclosure passage shape is invalid")
            source = sources.get(passage["source_id"])
            start, end = passage["start"], passage["end"]
            if (source is None or source.availability != "full_text"
                    or source.published_at is None
                    or source.published_at > snapshot.cutoff
                    or not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or not 0 <= start < end <= len(source.content)
                    or not isinstance(passage["exact_text"], str)
                    or not passage["exact_text"].strip()
                    or source.content_sha256 != passage["document_sha256"]
                    or hashlib.sha256(source.content.encode("utf-8")).hexdigest()
                    != passage["document_sha256"]
                    or source.content[start:end] != passage["exact_text"]):
                raise ValueError("controlled disclosure passage differs from frozen full text")
    if not _HASH.fullmatch(digest(packet)):
        raise ValueError("controlled disclosure digest is invalid")
    return packet


def prior_disclosure_lineage(provenance: dict[str, Any] | None,
                             prior_contracts: tuple[dict[str, Any], ...],
                             disclosure_contract_sha256s: str | frozenset[str]
                             ) -> tuple[dict[str, Any], ...]:
    """Carry exact prior disclosure packets through a hash-bound lineage."""
    contract_hashes = ({disclosure_contract_sha256s}
                       if isinstance(disclosure_contract_sha256s, str)
                       else disclosure_contract_sha256s)
    records = [item for item in prior_contracts
               if item["contract_sha256"] in contract_hashes]
    if not records:
        return ()
    if not isinstance(provenance, dict):
        raise ValueError("controlled disclosure lineage has no source provenance")
    earlier = provenance.get("prior_controlled_disclosures")
    direct_disclosure = provenance.get("revision_contract_sha256") in contract_hashes
    if not isinstance(earlier, list) or len(earlier) != len(records) - int(direct_disclosure):
        raise ValueError("controlled disclosure lineage is incomplete")
    if digest(earlier) != provenance.get("prior_controlled_disclosures_sha256"):
        raise ValueError("controlled disclosure lineage hash differs")
    fields = {"generation", "contract_sha256", "provenance_sha256", "packet", "packet_sha256"}
    if direct_disclosure:
        own = provenance.get("controlled_disclosure")
        if not isinstance(own, dict) or digest(own) != provenance.get("controlled_disclosure_sha256"):
            raise ValueError("controlled disclosure source packet differs")
        current = {
            "generation": records[-1]["generation"],
            "contract_sha256": provenance["revision_contract_sha256"],
            "provenance_sha256": records[-1]["provenance_sha256"],
            "packet": own,
            "packet_sha256": digest(own),
        }
        result = (*earlier, current)
    else:
        result = tuple(earlier)
    for record, entry in zip(records, result, strict=True):
        if (not isinstance(entry, dict) or set(entry) != fields
                or entry["generation"] != record["generation"]
                or entry["contract_sha256"] != record["contract_sha256"]
                or entry["provenance_sha256"] != record["provenance_sha256"]
                or not isinstance(entry["packet"], dict)
                or digest(entry["packet"]) != entry["packet_sha256"]):
            raise ValueError("controlled disclosure lineage ownership differs")
    return result
