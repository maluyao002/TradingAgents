"""V6-only, finding-bound exact source leads with validated material locators.

These passages are delivery candidates, never an attestation that a reader claim
is supported. Historical v3-v5 selectors and model payloads remain untouched.
"""

from hashlib import sha256

from .review_lifecycle import source_passage_witness_valid
from .revision_source_witnesses import (
    MAX_CATALOG_CHARACTERS,
    MAX_FINDINGS,
    _source_leads,
    revision_source_passage_witnesses,
)
from .storage import digest

SOURCE_WITNESS_POLICY = "finding_bound_direct_source_and_validated_locator_v1"
MAX_SOURCE_MATERIAL_RECORDS = 128
MAX_LOCATOR_CHARACTERS = 4096
MAX_QUERY_LEADS_PER_SOURCE = 3


def _finding_hash(finding):
    if not isinstance(finding, dict):
        raise ValueError("source finding must be a record")
    actual = digest({key: value for key, value in finding.items()
                     if key != "source_finding_sha256"})
    if finding.get("source_finding_sha256", actual) != actual:
        raise ValueError("source finding hash differs from its content")
    return actual


def _eligible_sources(snapshot):
    return {source.id: source for source in snapshot.sources
            if source.published_at is not None and source.published_at <= snapshot.cutoff
            and source.availability == "full_text"
            and sha256(source.content.encode("utf-8")).hexdigest() == source.content_sha256}


def _reference(finding_hash, source, start, end):
    return (f"source_passage:{finding_hash}:{source.id}:"
            f"{source.content_sha256}:{start}:{end}")


def _material_pair(record, source, finding_hash):
    """Revalidate a case-reviewed locator against immutable source bytes."""
    if not isinstance(record, dict):
        raise ValueError("source material locator must be a record")
    start, end = record.get("start"), record.get("end")
    if (record.get("source_id") != source.id
            or record.get("source_sha256") != source.content_sha256
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source.content)
            or type(record.get("text")) is not str
            or not record["text"].strip()
            or source.content[start:end] != record["text"]):
        raise ValueError("source material locator differs from frozen eligible source")
    if end - start > MAX_LOCATOR_CHARACTERS:
        return None  # A valid oversized passage cannot evade the bounded catalog.
    return _reference(finding_hash, source, start, end), record["text"]


def revision_source_passage_witnesses_v6(snapshot, findings, issues=(), source_material=()):
    """Select exact passages for each terminal finding's explicitly affected source.

    Unlike the historical selector, a preexisting but irrelevant legacy hit
    cannot suppress current finding-prose retrieval. Exact validated material
    locators on that same affected source are offered first. Allocation remains
    round-robin by finding and never truncates a passage or broadens witness
    authority beyond its original finding hash.
    """
    findings = list(findings)
    if len(findings) > MAX_FINDINGS:
        raise ValueError("revision source-witness finding limit exceeded")
    hashes = [_finding_hash(finding) for finding in findings]
    if len(hashes) != len(set(hashes)):
        raise ValueError("duplicate terminal findings in witness retrieval")
    if not isinstance(source_material, (list, tuple)) or len(source_material) > MAX_SOURCE_MATERIAL_RECORDS:
        raise ValueError("source material locator inventory is unbounded")
    eligible = _eligible_sources(snapshot)
    old = revision_source_passage_witnesses(snapshot, findings, issues)
    old_by_finding = {finding_hash: [] for finding_hash in hashes}
    for reference, value in old.items():
        parts = reference.split(":", 2)
        if (len(parts) != 3 or parts[1] not in old_by_finding
                or not source_passage_witness_valid(reference, value, old, snapshot, parts[1])):
            raise ValueError("historical source-witness catalog is invalid")
        old_by_finding[parts[1]].append((reference, value))

    candidates = {}
    for finding, finding_hash in zip(findings, hashes, strict=True):
        affected = {item for item in finding.get("affected_ids", ()) if item in eligible}
        material, prose = [], []
        for source_id in sorted(affected):
            source = eligible[source_id]
            for record in source_material:
                if isinstance(record, dict) and record.get("source_id") == source_id:
                    pair = _material_pair(record, source, finding_hash)
                    if pair is not None and pair not in material:
                        material.append(pair)
            query = str(finding.get("message", ""))[:4096]
            if query.strip():
                for start, end in _source_leads(source, query)[:MAX_QUERY_LEADS_PER_SOURCE]:
                    # Query hits are bounded leads, not source-material attestations.
                    end = min(len(source.content), end + 500)
                    if not 0 <= start < end <= len(source.content):
                        continue
                    pair = (_reference(finding_hash, source, start, end),
                            source.content[start:end])
                    if pair not in material and pair not in prose:
                        prose.append(pair)
        ordered = []
        for pair in (*material, *prose, *old_by_finding[finding_hash]):
            if pair not in ordered:
                ordered.append(pair)
        candidates[finding_hash] = ordered

    catalog, remaining = {}, MAX_CATALOG_CHARACTERS
    for index in range(max((len(items) for items in candidates.values()), default=0)):
        for finding_hash in hashes:
            items = candidates[finding_hash]
            if index >= len(items):
                continue
            reference, value = items[index]
            if reference not in catalog and len(value) <= remaining:
                catalog[reference] = value
                remaining -= len(value)
    if any(not source_passage_witness_valid(reference, value, catalog, snapshot,
                                           reference.split(":", 2)[1])
           for reference, value in catalog.items()):
        raise ValueError("v6 source witness differs from its exact frozen source")
    return catalog
