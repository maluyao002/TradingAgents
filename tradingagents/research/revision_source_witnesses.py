"""Finding-bound retrieval leads for new revision contracts, never issue closure.

Historical v3 retrieval stays in review_lifecycle. Here a claim's saved locations
are untrusted locators: only exact slices of cutoff-eligible frozen source bytes
enter the catalog. The fresh verifier still decides support and materiality.
"""

import re
from hashlib import sha256

from .context import pack_evidence
from .contracts import EvidenceSnapshot
from .review_lifecycle import source_passage_witness_valid, source_passage_witnesses
from .storage import digest

SOURCE_WITNESS_POLICY = "finding_bound_issue_context_exact_source_passage_v1"
MAX_CATALOG_CHARACTERS = 24_000
MAX_FINDINGS = 32
MAX_LOCATION_CHARACTERS = 2_000
_TOKEN = re.compile(r"[^\W_]{3,}")
_RANGE = re.compile(r"(?<![\w.-])(\d{1,9})\s*[-–—]\s*(\d{1,9})(?!\d)")
_LOCATOR = re.compile(r"\b(?:characters?|chars?|excerpts?)\s*[:,]?\s*(?=\d)", re.IGNORECASE)
_LABEL_STOP = frozenset({
    "source", "report", "reader", "review", "current", "retained", "excerpt",
    "evidence", "unavailable", "disclosure", "financial", "company", "issuer",
    "material", "limitation", "unverified", "claim", "annual", "quarter",
    "revenue", "customer", "customers", "concentration", "funding", "risk",
    "the", "and", "for", "with", "has", "not", "from", "this", "that",
})


def _tokens(text):
    return set(_TOKEN.findall(text.casefold())) - _LABEL_STOP


def _finding_hash(finding):
    actual = digest({key: value for key, value in finding.items()
                     if key != "source_finding_sha256"})
    if finding.get("source_finding_sha256", actual) != actual:
        raise ValueError("source finding hash differs from its content")
    return actual


def _source_leads(source, text, locations=()):
    """Return bounded exact spans; locations do not attest the claim's truth."""
    spans = []
    for location in locations[:8]:
        if not isinstance(location, str):
            continue
        locator = _LOCATOR.search(location[:2048])
        if locator is None:
            continue
        for match in list(_RANGE.finditer(location[locator.end():2048]))[:4]:
            left, right = match.groups()
            start, end = int(left), int(right)
            if (left != str(start) or right != str(end)
                    or not 0 <= start < end <= len(source.content)
                    or end - start > MAX_LOCATION_CHARACTERS):
                continue
            # Context helps avoid a sliced table heading or missing qualifier.
            spans.append((max(0, start - 160), min(len(source.content), end + 160)))
    if spans:
        terms = _tokens(text)
        percentages = re.findall(r"\b\d+(?:\.\d+)?%", text)
        def relevance(span):
            value = source.content[span[0]:span[1]]
            score = len(terms.intersection(_tokens(value)))
            score += 4 * sum(number in value for number in percentages)
            return -score, span
        return sorted(set(spans), key=relevance)[:4]
    # One-source retrieval prevents a broad subject query from being dominated
    # by unrelated issuers. A missing match never proves source absence.
    snapshot = EvidenceSnapshot(ticker="RETRIEVAL", cutoff=source.published_at,
                                sources=(source,))
    packed = pack_evidence(snapshot, queries=(text[:512],),
                           max_source_chars=2600, max_chars_per_source=2600)
    return [(item["start"], item["end"])
            for entry in packed["sources"] for item in entry["excerpts"]
            if item["text"] == source.content[item["start"]:item["end"]]][:3]


def revision_source_passage_witnesses(snapshot, findings, issues=()):
    """Carry exact source leads for affected claims and historical absence issues.

    This catalog cannot retire an issue, validate a financial model, or replace
    fresh factual/coverage review. Limits are retrieval bounds, not support claims.
    Every derived reference remains independently bound to its terminal finding.
    """
    findings = list(findings)
    if len(findings) > MAX_FINDINGS:
        raise ValueError("revision source-witness finding limit exceeded")
    hashes = [_finding_hash(finding) for finding in findings]
    if len(set(hashes)) != len(hashes):
        raise ValueError("duplicate terminal findings in witness retrieval")
    eligible = {source.id: source for source in snapshot.sources
                if source.published_at is not None and source.published_at <= snapshot.cutoff
                and source.availability == "full_text"
                and sha256(source.content.encode()).hexdigest() == source.content_sha256}
    by_issue = {}
    for issue in issues:
        identifier = issue.get("issue_id", issue.get("id"))
        if identifier in by_issue:
            raise ValueError("duplicate issue identity in witness context")
        if identifier:
            by_issue[identifier] = issue
    labels = {key: _tokens(f"{source.id} {source.title}")
              for key, source in eligible.items()}
    # Label anchoring requires a distinctive token, not a generic financial word.
    unique = {key: values - set().union(*(other for other_key, other in labels.items()
                                        if other_key != key))
              for key, values in labels.items()}
    candidates = {key: [] for key in hashes}
    legacy = source_passage_witnesses(snapshot, findings)
    for reference, value in legacy.items():
        if (reference.split(":", 2)[1] in candidates
                and source_passage_witness_valid(reference, value, legacy, snapshot,
                                                reference.split(":", 2)[1])):
            candidates[reference.split(":", 2)[1]].append((reference, value))

    for finding, finding_hash in zip(findings, hashes, strict=True):
        affected = set(finding.get("affected_ids") or ())
        related = [finding] + [other for other in findings if other is not finding
                    and other.get("code") != "limitation_disposition"
                    and affected.intersection(other.get("affected_ids") or ())]
        contextual = [by_issue[key] for key in sorted(affected) if key in by_issue]
        leads = []
        for issue in contextual:
            for claim in [*issue.get("claims", ()), *issue.get("related_records", ())][:16]:
                source_ids = list(dict.fromkeys(claim.get("source_ids") or ()))
                for source_id in source_ids[:8]:
                    if source_id in eligible:
                        # A bare location cannot identify which of several sources
                        # it belongs to; ambiguous offsets are not transferred.
                        locations = tuple(location for location in claim.get("source_locations", ())
                                          if isinstance(location, str)
                                          and (len(source_ids) == 1 or source_id in location))
                        leads.append((source_id, str(claim.get("text", "")), locations))
        text = " ".join(str(item.get("message", item.get("text", "")))
                        for item in [*related, *contextual])[:4096]
        for source_id in sorted(eligible):
            anchors = unique[source_id].intersection(_tokens(text))
            distinctive = len(anchors) >= 2 or any(
                re.search(rf"\b{re.escape(term)}\b", text)
                for term in re.findall(r"\b[A-Z][A-Za-z]{2,}\b", text)
                if term.casefold() in anchors)
            if not leads and not candidates[finding_hash] and (source_id in affected or distinctive):
                source = eligible[source_id]
                leads.append((source_id, f"{source.title} {text}", ()))
        groups = []
        for source_id, query, locations in leads[:32]:
            source = eligible[source_id]
            group = []
            for start, end in _source_leads(source, query, locations):
                if not locations:
                    # A title/header hit can end just before the actual disclosure.
                    end = min(len(source.content), end + 500)
                if not 0 <= start < end <= len(source.content):
                    continue
                reference = (f"source_passage:{finding_hash}:{source.id}:"
                             f"{source.content_sha256}:{start}:{end}")
                pair = (reference, source.content[start:end])
                if pair not in group:
                    group.append(pair)
            groups.append(group)
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index < len(group) and group[index] not in candidates[finding_hash]:
                    candidates[finding_hash].append(group[index])

    # Fair allocation means one combined finding cannot starve related critical
    # findings of their own hash-bound references. No passage is truncated to fit.
    catalog, remaining = {}, MAX_CATALOG_CHARACTERS
    for index in range(max((len(items) for items in candidates.values()), default=0)):
        for items in candidates.values():
            if index < len(items):
                reference, value = items[index]
                if reference not in catalog and len(value) <= remaining:
                    catalog[reference] = value
                    remaining -= len(value)
    return catalog
