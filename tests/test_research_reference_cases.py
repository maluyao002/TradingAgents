import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingagents.research.reference_cases import (
    ReferenceCase,
    ReferenceCaseManifest,
    validate_reference_files,
)


def _case(**changes):
    values = {
        "id": "hood-editorial-2026-09-18",
        "ticker": "HOOD",
        "report_basename": "hood.html",
        "reference_sha256": hashlib.sha256(b"local report").hexdigest(),
        "report_date": "2026-09-18",
        "questions": ("What is supported?",),
        "required_claim_areas": ("valuation constraints",),
    }
    values.update(changes)
    return ReferenceCase(**values)


def _manifest(case):
    return ReferenceCaseManifest(
        reference_cases=(case,),
        measured_quality_baseline="pending_matched_model_runs",
        benchmark_status="reference_registration_only",
    )


def test_content_identity_is_byte_bound_and_tampering_fails(tmp_path):
    reference = tmp_path / "hood.html"
    reference.write_bytes(b"local report")
    case = _case()
    validated = validate_reference_files(_manifest(case), (reference,), analysis_cutoff=date(2026, 9, 17))
    assert validated[0].content_identity_verified
    reference.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="content hash mismatch"):
        validate_reference_files(_manifest(case), (reference,), analysis_cutoff=date(2026, 9, 17))


def test_future_reference_cannot_become_evidence_or_gold(tmp_path):
    reference = tmp_path / "hood.html"
    reference.write_bytes(b"local report")
    result = validate_reference_files(
        _manifest(_case()), (reference,), analysis_cutoff=date(2026, 9, 17),
    )[0]
    assert result.report_after_analysis_cutoff
    assert result.factual_evidence_eligible is False
    assert result.gold_label_eligible is False


def test_registration_refuses_human_review_or_quality_certification():
    with pytest.raises(ValidationError):
        _case(human_reviewed=True)
    with pytest.raises(ValidationError):
        _case(quality_certified=True)
    with pytest.raises(ValidationError):
        _case(source_backed_reference_facts="reviewed")


def test_tracked_manifest_contains_basenames_not_private_paths():
    manifest_path = Path(__file__).parents[1] / "benchmarks/research-v2/reference_cases.json"
    raw = manifest_path.read_text()
    manifest = ReferenceCaseManifest.model_validate_json(raw)
    assert "/Users/luyaoma" not in raw
    assert all(Path(case.report_basename).name == case.report_basename
               for case in manifest.reference_cases)


def test_cli_validation_is_offline_and_emits_no_private_paths(tmp_path):
    reference = tmp_path / "hood.html"
    reference.write_bytes(b"local report")
    case = _case()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest(case).model_dump(mode="json")))
    completed = subprocess.run(
        [sys.executable, "scripts/research_reference_cases.py", "validate",
         "--manifest", str(manifest), "--analysis-cutoff", "2026-09-17",
         "--reference", str(reference)],
        check=True, text=True, capture_output=True,
    )
    output = json.loads(completed.stdout)
    assert output["offline"] is True
    assert output["validation"][0]["report_basename"] == "hood.html"
    assert str(tmp_path) not in completed.stdout
