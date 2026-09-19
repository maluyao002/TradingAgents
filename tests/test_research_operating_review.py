"""Offline review attachment preserves immutable inputs and scope boundaries."""

from hashlib import sha256

import pytest

from scripts.research_operating_review import attach_review
from tests.test_research_case_scenarios import operating_setup
from tradingagents.research.storage import canonical_json, read_json


def bundle(tmp_path):
    request, snapshot, package = operating_setup(tmp_path)
    source = tmp_path / "draft"
    source.mkdir()
    envelope = read_json(request.financial_case_path)
    review = envelope["operating_scenarios"]["review"]
    envelope["operating_scenarios"]["review"] = None
    draft_package = envelope["operating_scenarios"]
    artifacts = {
        "case_input.json": envelope, "operating_scenario_package.json": draft_package,
        "financial_case.json": envelope["case"], "evidence.json": snapshot,
        "request.json": request,
        "operating_scenario_diagnostics.json": {"weeks": "synthetic period comparison"},
        "closure_ledger.json": {"equity": "blocked"},
        "provenance.json": {"derived_case_change": "synthetic observed opening"},
    }
    hashes = {}
    for name, value in artifacts.items():
        raw = canonical_json(value)
        (source / name).write_bytes(raw)
        hashes[name] = sha256(raw).hexdigest()
    (source / "manifest.json").write_bytes(canonical_json({"artifact_hashes": hashes}))
    review_path = tmp_path / "review.json"
    review_path.write_bytes(canonical_json(review))
    return source, review_path, tmp_path / "reviewed"


def test_review_attachment_is_offline_fresh_and_keeps_valuation_blocked(tmp_path):
    source, review, output = bundle(tmp_path)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    readiness = attach_review(source, review, output)
    assert readiness["operating_scenarios_reviewed"]
    assert not readiness["live_authorized"] and not readiness["report_generated"]
    assert not readiness["case_reviewed"]
    assert read_json(output / "request.json")["backend"] == "replay"
    assert read_json(output / "request.json")["internal_language"] == "English"
    assert "deep-research reader" in read_json(output / "request.json")["mandate"]
    assert len(read_json(output / "operating_scenario_calculated_values.json")) == 12
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
    manifest = read_json(output / "manifest.json")["artifact_hashes"]
    for name in ("operating_scenario_diagnostics.json", "closure_ledger.json", "provenance.json"):
        retained_name = "source_provenance.json" if name == "provenance.json" else name
        assert (output / retained_name).read_bytes() == original[name]
        assert manifest[retained_name] == sha256(original[name]).hexdigest()
    with pytest.raises(ValueError, match="new directory"):
        attach_review(source, review, output)


@pytest.mark.parametrize("failure", ["stale", "self_review", "warning", "tampered"])
def test_invalid_review_or_tampering_never_creates_output(tmp_path, failure):
    source, review, output = bundle(tmp_path)
    record = read_json(review)
    if failure == "stale":
        record["package_sha256"] = "0" * 64
    elif failure == "self_review":
        record["reviewer_id"] = "fixture-author"
    elif failure == "warning":
        record["findings"] = [{"code": "unresolved", "severity": "warning", "message": "Not cleared."}]
    else:
        (source / "evidence.json").write_bytes(b"{}")
    review.write_bytes(canonical_json(record))
    with pytest.raises(ValueError):
        attach_review(source, review, output)
    assert not output.exists()
