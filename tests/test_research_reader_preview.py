from hashlib import sha256

import pytest

from tests.test_research_case_engine import case_setup
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.storage import read_json


def test_offline_preview_is_separate_unverified_and_preserves_history(tmp_path):
    request, services = case_setup(tmp_path / "source")
    result = run_research(request, services)
    hashes = {name: sha256((request.output_dir / name).read_bytes()).hexdigest()
              for name in result.artifacts}
    destination = tmp_path / "preview"
    metrics = preview_saved_reader(request.output_dir, request, destination)
    assert metrics["live_calls"] == 0 and metrics["acceptance"] is False
    assert "NOT VERIFIED OR ACCEPTED" in (destination / "reader_preview.md").read_text()
    assert read_json(destination / "comparison.json")["source_hashes"]
    assert all(sha256((request.output_dir / name).read_bytes()).hexdigest() == value
               for name, value in hashes.items())
    with pytest.raises(ValueError, match="fresh destination"):
        preview_saved_reader(request.output_dir, request, destination)
    with pytest.raises(ValueError, match="fresh destination"):
        preview_saved_reader(request.output_dir, request, request.output_dir / "nested")
