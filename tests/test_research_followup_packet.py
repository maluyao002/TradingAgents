"""Synthetic packet assembly regressions; no network, models, or local report dependency."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import research_followup_packet as packet
from scripts.research_evidence_followup import capture_sources
from tests.test_research_evidence_followup import StubFetcher, _fetched
from tests.test_research_operating_scenarios import _reviewed
from tradingagents.research.case_context import load_case_context
from tradingagents.research.contracts import EvidenceSnapshot, ResearchRequest
from tradingagents.research.storage import canonical_json, read_json

CUTOFF = datetime(2025, 9, 22, 20, tzinfo=timezone.utc)


def fixture(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    operating, case, snapshot = _reviewed()
    (src / "evidence.json").write_bytes(canonical_json(snapshot))
    (src / "case.json").write_bytes(canonical_json({"case": case, "operating_scenarios": operating}))
    request = ResearchRequest(ticker="NVDA", cutoff=snapshot.cutoff, backend="replay", timezone=case.timezone,
                              output_dir=src / "run", evidence_path=src / "evidence.json",
                              financial_case_path=src / "case.json", quality_revision="evidence-led-bounded",
                              report_language="English")
    config = src / "request.json"
    config.write_bytes(canonical_json(request))
    url = "https://example.com/customer"
    text = "Customer spending is not supplier orders. Financing remains uncertain."
    fetched = replace(_fetched(url, text), retrieved_at=datetime(2025, 9, 22, 18, tzinfo=timezone.utc))
    source_manifest = src / "sources.json"
    manifest = {"case_cutoff": snapshot.cutoff.isoformat(), "sources": [{"id": "customer", "source_url": url}]}
    source_manifest.write_bytes(canonical_json(manifest))
    capture = src / "evidence_capture_1"
    capture_sources(manifest, capture, fetcher=StubFetcher({url: fetched}),
                    source_manifest_path=source_manifest, captured_at=CUTOFF)
    selection = {"schema_version": 1, "sources": [{
        "capture_directory": str(capture), "id": "customer", "source_url": url,
        "text_sha256": fetched.text_sha256, "published_at": "2025-09-21",
        "publication_basis": "Visible official event date", "title": "Customer disclosure",
        "publisher": "Customer", "kind": "ir", "independence": "Separate publisher, financially linked",
        "passages": [{"id": "customer-spending", "start": 0, "end": len(text), "exact_text": text,
                      "claim": "The company reports spending.", "boundary": "Not supplier-specific orders.",
                      "topic": "Demand funding"}],
    }]}
    selections = src / "selections.json"
    selections.write_bytes(canonical_json(selection))
    return config, selections, capture, snapshot, operating


def test_packet_preserves_historical_records_clears_reviews_and_pins_inputs(tmp_path):
    config, selections, capture, original, operating = fixture(tmp_path)
    before = {p: p.read_bytes() for p in config.parent.rglob("*") if p.is_file()}
    output = tmp_path / "new"
    result = packet.prepare(config, selections, output, cutoff=CUTOFF)
    assert result["new_sources"] == result["new_passages"] == 1
    assert result["model_calls"] == result["network_calls"] == 0
    assert not result["live_authorized"] and not result["operating_reviewed"]
    snapshot = EvidenceSnapshot.model_validate(read_json(output / "evidence.json"))
    assert snapshot.sources[:-1] == original.sources and snapshot.facts == original.facts
    assert snapshot.events == original.events and snapshot.expectations == original.expectations
    assert all(gap in snapshot.gaps for gap in original.gaps)
    assert snapshot.sources[-1].published_at == snapshot.sources[-1].retrieved_at
    request = ResearchRequest.model_validate(read_json(output / "request.json"))
    assert request.backend == "replay" and request.prior_dossier_path is None
    context = load_case_context((output / "case_input.json").read_bytes(), request, snapshot)
    assert not context.reviewed and not context.operating_scenarios.reviewed
    assert not context.operating_scenarios.calculated_values
    envelope = read_json(output / "case_input.json")
    assert envelope["operating_scenarios"]["review"] is None
    assert envelope["operating_scenarios"]["scenarios"] == operating.model_dump(mode="json")["scenarios"]
    assert len(envelope["operating_scenarios"]["source_material"]) == len(operating.source_material) + 1
    for path, raw in before.items():
        assert path.read_bytes() == raw
    manifest = read_json(output / "manifest.json")
    for name, expected in manifest["artifact_hashes"].items():
        assert packet._sha((output / name).read_bytes()) == expected
    assert str(capture / "capture_manifest.json") in manifest["input_hashes"]


@pytest.mark.parametrize("mutation", ["text", "hash", "url", "duplicate_source", "duplicate_passage", "date", "blank_boundary", "span", "limit"])
def test_invalid_authored_selections_fail_before_output(tmp_path, mutation):
    config, selections, _, _, _ = fixture(tmp_path)
    content = read_json(selections)
    row = content["sources"][0]
    if mutation == "text":
        row["passages"][0]["exact_text"] = "Fabricated text"
    elif mutation == "hash":
        row["text_sha256"] = "0" * 64
    elif mutation == "url":
        row["source_url"] = "https://example.com/wrong"
    elif mutation == "duplicate_source":
        content["sources"].append(row)
    elif mutation == "duplicate_passage":
        row["passages"].append(row["passages"][0])
    elif mutation == "date":
        row["published_at"] = "2025-09-23"
    elif mutation == "blank_boundary":
        row["passages"][0]["boundary"] = " "
    elif mutation == "span":
        row["passages"][0]["start"] = -1
    else:
        row["passages"] *= 25
    selections.write_bytes(canonical_json(content))
    with pytest.raises(ValueError):
        packet.prepare(config, selections, tmp_path / "new", cutoff=CUTOFF)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("mutation", ["input_hash", "capture_url", "timestamp", "raw_blob", "failed", "version"])
def test_corrupt_or_ineligible_capture_rejected(tmp_path, mutation):
    config, selections, capture, _, _ = fixture(tmp_path)
    manifest = read_json(capture / "capture_manifest.json")
    record = manifest["records"][0]
    if mutation == "input_hash":
        manifest["source_manifest"]["content_sha256"] = "0" * 64
    elif mutation == "capture_url":
        record["requested_url"] = "https://example.com/wrong"
    elif mutation == "timestamp":
        record["retrieved_at"] = "2025-09-22T17:00:00+00:00"
    elif mutation == "failed":
        record["status"] = "failed"
    elif mutation == "version":
        manifest["schema_version"] = 2
    else:
        (capture / "source-cache" / "raw" / record["raw_sha256"]).write_bytes(b"corrupt")
    (capture / "capture_manifest.json").write_bytes(canonical_json(manifest))
    with pytest.raises((ValueError, RuntimeError)):
        packet.prepare(config, selections, tmp_path / "new", cutoff=CUTOFF)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("cutoff", [datetime(2025, 9, 22, 17, tzinfo=timezone.utc),
                                   datetime(2025, 9, 18, 12, tzinfo=timezone.utc),
                                   datetime(2025, 9, 22), datetime(2099, 1, 1, tzinfo=timezone.utc)])
def test_ineligible_cutoff_rejected(tmp_path, cutoff):
    config, selections, _, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        packet.prepare(config, selections, tmp_path / "new", cutoff=cutoff)
    assert not (tmp_path / "new").exists()


def test_input_mutation_during_assembly_fails_before_publication(tmp_path, monkeypatch):
    config, selections, _, _, _ = fixture(tmp_path)
    original = packet._selected_sources

    def mutate(*args):
        result = original(*args)
        selections.write_bytes(b"{}")
        return result

    monkeypatch.setattr(packet, "_selected_sources", mutate)
    with pytest.raises(ValueError, match="input changed"):
        packet.prepare(config, selections, tmp_path / "new", cutoff=CUTOFF)
    assert not (tmp_path / "new").exists()


def test_existing_destination_not_overwritten(tmp_path):
    config, selections, _, _, _ = fixture(tmp_path)
    output = tmp_path / "new"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("retain")
    with pytest.raises(ValueError, match="output must be new"):
        packet.prepare(config, selections, output, cutoff=CUTOFF)
    assert marker.read_text() == "retain"


def test_dangling_output_symlink_rejected(tmp_path):
    config, selections, _, _, _ = fixture(tmp_path)
    output = tmp_path / "link"
    target = tmp_path / "outside"
    output.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        packet.prepare(config, selections, output, cutoff=CUTOFF)
    assert not target.exists()


def test_same_document_cannot_be_counted_under_a_second_source_identity(tmp_path):
    config, selections, capture, snapshot, _ = fixture(tmp_path)
    selection = read_json(selections)
    second = deepcopy(selection["sources"][0])
    second["id"] = "alias"
    second["passages"][0]["id"] = "alias-passage"
    second_capture = capture.parent / "evidence_capture_2"
    second["capture_directory"] = str(second_capture)
    selection["sources"].append(second)
    manifest = {"case_cutoff": snapshot.cutoff.isoformat(),
                "sources": [{"id": "alias", "source_url": second["source_url"]}]}
    source_manifest = config.parent / "sources2.json"
    source_manifest.write_bytes(canonical_json(manifest))
    fetched = replace(_fetched(second["source_url"], second["passages"][0]["exact_text"]),
                      retrieved_at=datetime(2025, 9, 22, 18, tzinfo=timezone.utc))
    capture_sources(manifest, second_capture, fetcher=StubFetcher({second["source_url"]: fetched}),
                    source_manifest_path=source_manifest, captured_at=CUTOFF)
    selections.write_bytes(canonical_json(selection))
    with pytest.raises(ValueError, match="duplicate captured document"):
        packet.prepare(config, selections, tmp_path / "new", cutoff=CUTOFF)
    assert not (tmp_path / "new").exists()


def test_document_identity_normalizes_urls_but_preserves_nonfetchable_history():
    a = SimpleNamespace(url="https://EXAMPLE.com/source", content_sha256="a" * 64)
    b = SimpleNamespace(url="https://example.com/source", content_sha256="a" * 64)
    assert packet._document_identity(a) == packet._document_identity(b)
    legacy = SimpleNamespace(url="local:legacy-source", content_sha256="b" * 64)
    assert packet._document_identity(legacy) == (legacy.url, legacy.content_sha256)


@pytest.mark.parametrize("limit", ["MAX_SOURCE_RAW_BYTES", "MAX_SOURCE_TEXT_BYTES", "MAX_TOTAL_RAW_BYTES", "MAX_TOTAL_TEXT_BYTES"])
def test_source_and_packet_byte_limits_precede_cache_loading(tmp_path, monkeypatch, limit):
    config, selections, _, _, _ = fixture(tmp_path)
    monkeypatch.setattr(packet, limit, 1)

    def unexpected(*args, **kwargs):
        pytest.fail("cache must not load an oversized document")

    monkeypatch.setattr(packet.FileSourceCache, "get", unexpected)
    with pytest.raises(ValueError, match="byte allowance"):
        packet.prepare(config, selections, tmp_path / "new", cutoff=CUTOFF)
    assert not (tmp_path / "new").exists()
