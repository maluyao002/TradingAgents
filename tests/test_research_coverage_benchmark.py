from copy import deepcopy
from hashlib import sha256

import pytest

from scripts.research_coverage_benchmark import benchmark_source
from tradingagents.research.coverage_benchmark import compare_coverage_packing
from tradingagents.research.storage import canonical_json, digest, read_json


def test_benchmark_preserves_input_and_distinguishes_measurement_from_estimate():
    issues = [{"issue_id": str(i), "text": f"Uncertainty {i}"} for i in range(49)]
    original = deepcopy(issues)
    result = compare_coverage_packing(issues, "Exact reader.\n")
    assert issues == original
    assert result["raw_issue_count"] == result["exact_group_count"] == 49
    assert result["policies"]["legacy-12"]["call_count"] == 5
    assert result["policies"]["packed-24"]["call_count"] == 3
    for policy in result["policies"].values():
        assert policy["raw_issue_count"] == 49
        assert policy["repeated_reader_bytes"] == policy["call_count"] * 14
    assert result["live_calls"] == 0 and not result["quality_accepted"]
    assert result["full_provider_prompt_bytes"] is None
    assert result["measured_tokens"] is result["measured_live_latency_seconds"] is None


def test_benchmark_empty_inventory_does_not_invent_work():
    result = compare_coverage_packing([], "")
    assert all(p["call_count"] == p["output_token_envelope"] == p["repeated_reader_bytes"] == 0
               for p in result["policies"].values())


def saved_source(tmp_path):
    reader = "Exact historical reader."
    reader_hash = sha256(reader.encode()).hexdigest()
    (tmp_path / "stages").mkdir()
    issues = [{"issue_id": str(i), "text": f"Uncertainty {i}"} for i in range(13)]
    verification = {"English": {
        "stage": "verify_report", "reader_sha256": reader_hash,
        "issue_lifecycle": {"reader_sha256": reader_hash, "issues": [
            {**item, "status": "open", "decision": None} for item in issues
        ]},
        "coverage_batches": [{"stage": "verify_report-coverage-0", "reader_sha256": reader_hash,
                              "issue_ids": [str(i) for i in range(12)]}],
    }}
    (tmp_path / "reader_verification.json").write_bytes(canonical_json(verification))
    for name, output in {
        "verify_report-reader-candidate": {"reader_text": reader},
        "verify_report-cost-plan": {"reader_sha256": reader_hash, "original_issue_count": 13,
                                    "current_pass": {"call_count": 2}},
    }.items():
        (tmp_path / "stages" / f"{name}.json").write_bytes(canonical_json({
            "output": output, "output_hash": digest(output),
        }))
    return tmp_path


def test_source_benchmark_reconstructs_only_workload_and_preserves_bytes(tmp_path):
    root = saved_source(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    result = benchmark_source(root)
    assert result["raw_issue_count"] == 13
    assert result["reconstruction"]["matched_completed_batch_assignments"] == 1
    assert result["reconstruction"]["historical_reviews_imported"] is False
    assert len(result["source_artifact_sha256"]) == 3
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("mutation", ["assignment", "reader", "duplicate", "compound"])
def test_source_benchmark_rejects_unbound_or_unsupported_inventory(tmp_path, mutation):
    root = saved_source(tmp_path)
    path = root / "reader_verification.json"
    payload = read_json(path)
    data = payload["English"]
    if mutation == "assignment":
        data["coverage_batches"][0]["issue_ids"].pop()
    elif mutation == "reader":
        data["reader_sha256"] = "f" * 64
    elif mutation == "duplicate":
        data["issue_lifecycle"]["issues"].append(data["issue_lifecycle"]["issues"][0])
    else:
        data["issue_lifecycle"]["issues"][0]["compound_obligation"] = {}
    path.write_bytes(canonical_json(payload))
    with pytest.raises(ValueError):
        benchmark_source(root)


def test_source_benchmark_rejects_altered_candidate(tmp_path):
    root = saved_source(tmp_path)
    path = root / "stages/verify_report-reader-candidate.json"
    data = read_json(path)
    data["output"]["reader_text"] += "changed"
    path.write_bytes(canonical_json(data))
    with pytest.raises(ValueError, match="hash"):
        benchmark_source(root)
