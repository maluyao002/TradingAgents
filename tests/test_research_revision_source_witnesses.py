"""Source witnesses are bounded retrieval leads, not inherited attestations."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.review_lifecycle import source_passage_witness_valid
from tradingagents.research.revision_source_witnesses import (
    MAX_CATALOG_CHARACTERS,
    MAX_FINDINGS,
    revision_source_passage_witnesses,
)
from tradingagents.research.storage import digest

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def source(identifier="issuer_filing", text="Neutral preface. Customer concentration 17%. End."):
    return SourceDocument(id=identifier, url=f"https://example.test/{identifier}",
        title="Issuer filing", publisher="Issuer", published_at=NOW, retrieved_at=NOW,
        content=text, content_sha256=sha256(text.encode()).hexdigest())


def setup_claim(doc, location="characters 16–43"):
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=NOW, sources=(doc,))
    finding = {"code": "material-omissions", "message": "Material concentration missing.",
               "affected_ids": ["issue-a"]}
    issue = {"issue_id": "issue-a", "text": "Historical claim issue", "claims": [{
        "text": "A customer accounts for 17% of revenue.", "source_ids": [doc.id],
        "source_locations": [location],
    }]}
    return snapshot, finding, issue


def test_opaque_issue_recovers_exact_source_using_bound_claim_context():
    snapshot, finding, issue = setup_claim(source())
    linked = {"code": "limitation_disposition", "message": "Unresolved issue-a",
              "affected_ids": ["issue-a"]}
    unrelated = {"code": "limitation_disposition", "message": "Unresolved issue-b",
                 "affected_ids": ["issue-b"]}
    catalog = revision_source_passage_witnesses(snapshot, [finding, linked, unrelated], [issue])
    assert catalog
    assert {ref.split(":")[1] for ref in catalog} == {digest(finding), digest(linked)}
    for ref, value in catalog.items():
        assert "concentration 17%" in value
        assert source_passage_witness_valid(ref, value, catalog, snapshot, ref.split(":")[1])
    assert issue["text"] == "Historical claim issue"  # no lifecycle mutation


@pytest.mark.parametrize("update", [
    {"published_at": None}, {"published_at": NOW + timedelta(days=1)},
    {"availability": "unavailable"}, {"content_sha256": "0" * 64},
])
def test_ineligible_or_hash_mismatched_sources_never_enter_catalog(update):
    snapshot, finding, issue = setup_claim(source())
    snapshot = snapshot.model_copy(update={"sources": (snapshot.sources[0].model_copy(update=update),)})
    assert not revision_source_passage_witnesses(snapshot, [finding], [issue])


@pytest.mark.parametrize("location", [
    "characters 999999–1000000", "characters -10–20", "characters 0001–0043",
    "characters 40–10", "characters 0–99999999999999999999999999",
])
def test_bad_locations_do_not_become_references(location):
    snapshot, finding, issue = setup_claim(source(), location)
    catalog = revision_source_passage_witnesses(snapshot, [finding], [issue])
    for ref, value in catalog.items():
        assert source_passage_witness_valid(ref, value, catalog, snapshot, digest(finding))


def test_ambiguous_multisource_locations_are_not_reassigned():
    first = source("first", "x" * 1500 + "Customer concentration 17%." + "x" * 1500)
    second = source("second", "y" * 1500 + "Customer concentration 31%." + "y" * 1500)
    snapshot, finding, issue = setup_claim(first, "characters 1500–1526")
    snapshot = snapshot.model_copy(update={"sources": (first, second)})
    issue["claims"][0]["source_ids"] = [first.id, second.id]
    catalog = revision_source_passage_witnesses(snapshot, [finding], [issue])
    assert not any(ref.endswith(":1340:1686") for ref in catalog)


@pytest.mark.parametrize("location", ["FY2026-2027 results", "2026-2027 report",
                                     "Retained excerpt FY2026-2027 results"])
def test_year_ranges_are_not_character_locators(location):
    text = "Unrelated boilerplate. " * 180 + "Customer concentration 17%. " * 15
    snapshot, finding, issue = setup_claim(source(text=text), location)
    expected = {**issue, "claims": [{**issue["claims"][0], "source_locations": []}]}
    catalog = revision_source_passage_witnesses(snapshot, [finding], [issue])
    assert catalog == revision_source_passage_witnesses(snapshot, [finding], [expected])
    assert not any(ref.endswith(":1866:2187") for ref in catalog)


def test_short_distinctive_source_acronym_recovers_current_provenance():
    doc = source("xyz_procurement", "Navigation. " * 80 +
                 "XYZ laboratory deployed the named supercomputer. " * 30)
    doc = doc.model_copy(update={"title": "XYZ laboratory deployed supercomputer"})
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=NOW, sources=(doc,))
    finding = {"code": "provenance_gap", "message": "XYZ has no retained excerpt.",
               "affected_ids": ["issue-a"]}
    catalog = revision_source_passage_witnesses(snapshot, [finding])
    assert any("laboratory deployed" in val for val in catalog.values())
    assert "no retained excerpt" not in " ".join(catalog.values())


def test_catalog_bounded_and_finding_hashes_checked():
    snapshot, finding, issue = setup_claim(source(text="Customer concentration 17%. " * 300))
    findings = [{**finding, "message": f"Missing customer concentration {i}."} for i in range(32)]
    catalog = revision_source_passage_witnesses(snapshot, findings, [issue])
    assert sum(map(len, catalog.values())) <= MAX_CATALOG_CHARACTERS
    with pytest.raises(ValueError, match="hash differs"):
        revision_source_passage_witnesses(snapshot, [{**finding, "source_finding_sha256": "0" * 64}])
    with pytest.raises(ValueError, match="finding limit"):
        revision_source_passage_witnesses(snapshot, findings + findings[:MAX_FINDINGS])
    with pytest.raises(ValueError, match="duplicate terminal"):
        revision_source_passage_witnesses(snapshot, [finding, finding])
    with pytest.raises(ValueError, match="duplicate issue"):
        revision_source_passage_witnesses(snapshot, [finding], [issue, issue])


def test_equivalent_id_shapes_and_supplied_finding_hash_are_byte_identical():
    snapshot, finding, issue = setup_claim(source())
    expected = revision_source_passage_witnesses(snapshot, [finding], [issue])
    alternate = {**issue, "id": issue["issue_id"]}
    del alternate["issue_id"]
    bound = {**finding, "source_finding_sha256": digest(finding)}
    assert revision_source_passage_witnesses(snapshot, [bound], [alternate]) == expected
