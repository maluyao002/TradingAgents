"""Read-only historical coverage-packing benchmark; no model calls or acceptance."""

import argparse
import hashlib
import json
from pathlib import Path

from tradingagents.research.coverage_benchmark import compare_coverage_packing
from tradingagents.research.coverage_policy import coverage_batches_for_policy
from tradingagents.research.storage import digest, parse_json, read_bytes


def benchmark_source(source, language="English"):
    source = Path(source)
    contents = {}

    def load(name, *, checkpoint=False):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("benchmark requires regular source files")
        contents[name] = read_bytes(path)
        data = parse_json(contents[name])
        if checkpoint:
            if digest(data["output"]) != data["output_hash"]:
                raise ValueError("source checkpoint output hash mismatch")
            return data["output"]
        return data

    verification = load("reader_verification.json")[language]
    stage = verification["stage"]
    if stage not in {"verify_report", "verify_repaired_report"}:
        raise ValueError("unsupported reader verification stage")
    candidate = load(f"stages/{stage}-reader-candidate.json", checkpoint=True)
    plan = load(f"stages/{stage}-cost-plan.json", checkpoint=True)
    if plan.get("coverage_batch_policy", "legacy-12") != "legacy-12":
        raise ValueError("historical comparator requires a legacy source plan")
    ledger = verification["issue_lifecycle"]
    if any(row["status"] not in {"open", "resolved", "superseded"} for row in ledger["issues"]):
        raise ValueError("unknown source issue status")
    if len({row["issue_id"] for row in ledger["issues"]}) != len(ledger["issues"]):
        raise ValueError("duplicate source issue identifiers")
    # reconcile_review adds only status and decision to each immutable input
    # issue. Removing them from open ledger rows reconstructs its coverage input.
    issues = tuple({key: value for key, value in row.items() if key not in {"status", "decision"}}
                   for row in ledger["issues"] if row["status"] == "open")
    if any("compound_obligation" in issue for issue in issues):
        raise ValueError("compound source requires a separate atomic-issue reconstruction")
    result = compare_coverage_packing(issues, candidate["reader_text"])
    reader_hash = result["reader_sha256"]
    if any(value != reader_hash for value in (
            ledger["reader_sha256"], verification["reader_sha256"], plan["reader_sha256"])):
        raise ValueError("source reader bindings disagree")
    legacy_batches = coverage_batches_for_policy(issues, "legacy-12")
    if (plan["original_issue_count"] != len(issues)
            or plan["current_pass"]["call_count"] != len(legacy_batches)):
        raise ValueError("source coverage plan does not match reconstructed issues")
    completed = verification["coverage_batches"]
    if len(completed) > len(legacy_batches):
        raise ValueError("source completed coverage exceeds plan")
    for index, saved in enumerate(completed):
        if (saved["stage"] != f"{stage}-coverage-{index}"
                or saved["reader_sha256"] != reader_hash
                or saved["issue_ids"] != [item["issue_id"] for item in legacy_batches[index]]):
            raise ValueError("source coverage assignments do not match reconstructed issues")
    if any(read_bytes(source / name) != content for name, content in contents.items()):
        raise ValueError("source artifacts changed during benchmark")
    result["source_artifact_sha256"] = {
        name: hashlib.sha256(content).hexdigest() for name, content in contents.items()
    }
    result["reconstruction"] = {
        "method": "open_lifecycle_rows_without_status_or_decision",
        "matched_completed_batch_assignments": len(completed),
        "historical_reviews_imported": False,
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--language", choices=("English", "Chinese"), default="English")
    args = parser.parse_args(argv)
    print(json.dumps(benchmark_source(args.source_run, args.language), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
