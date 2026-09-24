"""V6 source leads must be exact, finding-bound, and independently bounded."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from tradingagents.research.contracts import EvidenceSnapshot, SourceDocument
from tradingagents.research.review_lifecycle import source_passage_witness_valid
from tradingagents.research.revision_source_witnesses import (
    MAX_CATALOG_CHARACTERS,
    revision_source_passage_witnesses,
)
from tradingagents.research.revision_source_witnesses_v6 import (
    SOURCE_WITNESS_POLICY,
    revision_source_passage_witnesses_v6,
)
from tradingagents.research.storage import digest

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _source(identifier, text, *, published_at=NOW):
    return SourceDocument(
        id=identifier, url=f"https://example.test/{identifier}", title=f"{identifier} report",
        publisher="Issuer", published_at=published_at, retrieved_at=NOW,
        content=text, content_sha256=sha256(text.encode()).hexdigest(),
    )


def _material(source, start, end, identifier="metric_table"):
    return {"schema_version": 1, "id": identifier, "source_id": source.id,
            "source_sha256": source.content_sha256, "start": start, "end": end,
            "text": source.content[start:end], "context": "Exact reviewed locator, not an attestation."}


def _fixture():
    text = ("Meeting logistics and voting information. " * 70
            + "Performance-dependent target pay was 84 percent.\n"
            + "The metric mix included revenue, operating income, and customer milestones.\n"
            + "A pre-specified adjustment governed one product-line outcome.\n")
    source = _source("issuer_proxy", text)
    start = text.index("Performance-dependent")
    second = text.index("The metric mix")
    third = text.index("A pre-specified")
    material = [_material(source, start, second, "target_pay"),
                _material(source, second, third, "metric_mix"),
                _material(source, third, len(text), "adjustment")]
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=NOW, sources=(source,))
    finding = {"code": "proxy_inventory", "severity": "warning", "category": "research",
               "message": "The reader overstates the supplied performance-pay inventory and outcome.",
               "affected_ids": [source.id]}
    return snapshot, finding, material


def test_policy_and_current_finding_direct_material_beats_nonempty_legacy():
    snapshot, finding, material = _fixture()
    old = revision_source_passage_witnesses(snapshot, [finding])
    assert old  # This must not suppress the direct v6 candidates.
    new = revision_source_passage_witnesses_v6(snapshot, [finding], source_material=material)
    assert SOURCE_WITNESS_POLICY == "finding_bound_direct_source_and_validated_locator_v1"
    assert all(new.get(reference) == value for reference, value in old.items())
    expected = {
        (f"source_passage:{digest(finding)}:{snapshot.sources[0].id}:"
         f"{snapshot.sources[0].content_sha256}:{item['start']}:{item['end']}"): item["text"]
        for item in material
    }
    assert all(new.get(reference) == value for reference, value in expected.items())
    assert any(reference not in old for reference in new)
    assert sum(map(len, new.values())) <= MAX_CATALOG_CHARACTERS
    for reference, value in new.items():
        assert source_passage_witness_valid(reference, value, new, snapshot, digest(finding))


def test_nonempty_legacy_does_not_suppress_current_finding_prose_query(monkeypatch):
    snapshot, finding, material = _fixture()
    old = revision_source_passage_witnesses(snapshot, [finding])
    assert old
    calls = []

    def prose_lead(source, query):
        calls.append((source.id, query))
        return [(0, 20)]

    monkeypatch.setattr(
        "tradingagents.research.revision_source_witnesses_v6._source_leads", prose_lead)
    catalog = revision_source_passage_witnesses_v6(
        snapshot, [finding], source_material=material)
    assert calls == [(snapshot.sources[0].id, finding["message"])]
    assert any(reference.endswith(":0:520") for reference in catalog)


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "0" * 64),
    ("text", "normalized or altered text"),
    ("start", -1),
    ("end", 999999),
    ("start", True),
])
def test_changed_explicit_material_locator_rejected(field, value):
    snapshot, finding, material = _fixture()
    changed = deepcopy(material)
    changed[0][field] = value
    with pytest.raises(ValueError, match="locator differs"):
        revision_source_passage_witnesses_v6(snapshot, [finding], source_material=changed)


@pytest.mark.parametrize("change", ["future", "undated", "unavailable", "bad_source_hash"])
def test_ineligible_source_never_receives_material_or_query_leads(change):
    snapshot, finding, material = _fixture()
    source = snapshot.sources[0]
    updates = {
        "future": {"published_at": NOW + timedelta(days=1)},
        "undated": {"published_at": None},
        "unavailable": {"availability": "unavailable"},
        "bad_source_hash": {"content_sha256": "0" * 64},
    }
    snapshot = snapshot.model_copy(update={"sources": (source.model_copy(update=updates[change]),)})
    assert not revision_source_passage_witnesses_v6(
        snapshot, [finding], source_material=material)


def test_material_on_unrelated_source_is_not_imported_or_cross_bound():
    snapshot, finding, material = _fixture()
    other = _source("other_issuer", "Unrelated performance-dependent pay was 99 percent.")
    snapshot = snapshot.model_copy(update={"sources": (*snapshot.sources, other)})
    unrelated = _material(other, 0, len(other.content), "unrelated")
    catalog = revision_source_passage_witnesses_v6(
        snapshot, [finding], source_material=[*material, unrelated])
    assert all(f":{other.id}:" not in reference for reference in catalog)
    second = {**finding, "code": "other_finding", "affected_ids": [other.id]}
    catalog = revision_source_passage_witnesses_v6(
        snapshot, [finding, second], source_material=[*material, unrelated])
    assert any(f":{other.id}:" in reference and reference.split(":")[1] == digest(second)
               for reference in catalog)
    assert all(not source_passage_witness_valid(reference, value, catalog, snapshot, digest(second))
               for reference, value in catalog.items() if reference.split(":")[1] == digest(finding))


def test_oversized_locator_and_many_findings_stay_bounded_and_fair():
    text = "Performance metrics and exact goals. " * 160
    source = _source("issuer_proxy", text)
    snapshot = EvidenceSnapshot(ticker="ISS", cutoff=NOW, sources=(source,))
    finding = {"code": "metric_gap", "message": "Performance metrics are incompletely described.",
               "affected_ids": [source.id]}
    oversized = _material(source, 0, len(text), "oversized")
    findings = [{**finding, "code": f"metric_gap_{index}"} for index in range(6)]
    catalog = revision_source_passage_witnesses_v6(
        snapshot, findings, source_material=[oversized])
    assert sum(map(len, catalog.values())) <= MAX_CATALOG_CHARACTERS
    assert {reference.split(":")[1] for reference in catalog} == {
        digest(item) for item in findings}
    assert all(value != oversized["text"] for value in catalog.values())


def test_legacy_catalog_unchanged_and_finding_hash_tamper_rejected():
    snapshot, finding, material = _fixture()
    before = revision_source_passage_witnesses(snapshot, [finding])
    revision_source_passage_witnesses_v6(snapshot, [finding], source_material=material)
    assert revision_source_passage_witnesses(snapshot, [finding]) == before
    with pytest.raises(ValueError, match="hash differs"):
        revision_source_passage_witnesses_v6(
            snapshot, [{**finding, "source_finding_sha256": "0" * 64}],
            source_material=material)
