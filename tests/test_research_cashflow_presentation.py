"""Reviewed assumptions are mandatory reader content, not writer discretion."""

from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

from tests.test_research_cashflow_engine import CashFixture, setup
from tradingagents.research.case_context import load_case_context
from tradingagents.research.cashflow_presentation import cashflow_assumptions
from tradingagents.research.engine import run_research
from tradingagents.research.reader_preview import preview_saved_reader
from tradingagents.research.replay import SnapshotEvidenceService
from tradingagents.research.review_disclosures import review_disclosure
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import read_json


def test_inputs_are_code_owned_and_bound_to_actual_reader(tmp_path):
    request, snapshot = setup(tmp_path)
    models = CashFixture()
    result = run_research(request, ResearchServices(SnapshotEvidenceService(snapshot), models))
    assert result.stop_reason == "completed_needs_review"
    reader = (request.output_dir / "reader_report.md").read_text()
    assert reader.count("### Conditional cash-flow assumptions") == 1
    assert "20.00%" in reader and "10.00" in reader
    assert "not issuer guidance or approved economic forecasts" in reader
    factual = next(payload for _, payload in models.calls if payload["stage"] == "verify_report")
    assert factual["research"]["rendered_reader"] == reader
    provenance = read_json(request.output_dir / "stages/verify_report-rendering-provenance.json")["output"]
    disclosure = provenance["rendering_inputs"]["case_review_disclosure"]
    table = disclosure["cashflow_assumptions_text"]
    assert table in reader
    assert sha256(table.encode()).hexdigest() == disclosure["cashflow_assumptions_sha256"]
    preview_saved_reader(request.output_dir, request, tmp_path / "preview")


def test_draft_inputs_withheld_and_legacy_disclosure_unchanged(tmp_path):
    request, snapshot = setup(tmp_path, reviewed=False)
    context = load_case_context(request.financial_case_path.read_bytes(), request, snapshot)
    legacy = review_disclosure(request, snapshot, context, "English")
    assert "cashflow_assumptions_text" not in legacy
    current = review_disclosure(request, snapshot, context, "English", include_cashflow_inputs=True)
    assert current["cashflow_assumptions_text"] == ""


def test_chinese_preserves_scope_and_dates(tmp_path):
    request, snapshot = setup(tmp_path)
    context = load_case_context(request.financial_case_path.read_bytes(), request, snapshot)
    disclosure = review_disclosure(request, snapshot, context, "Chinese", include_cashflow_inputs=True)
    assert "条件性现金流假设" in disclosure["cashflow_assumptions_text"]
    assert "20.00%" in disclosure["cashflow_assumptions_text"]


def test_table_labels_cannot_embed_markdown_images_or_links(tmp_path):
    request, snapshot = setup(tmp_path)
    context = load_case_context(request.financial_case_path.read_bytes(), request, snapshot)
    model = deepcopy(context.cashflow_bridge.model_context)
    model["scenarios"][0]["label"] = "![tracking](https://example.invalid/collect)"
    model["scenarios"][0]["periods"][0]["fiscal_label"] = "[link](https://example.invalid) | extra"
    table = cashflow_assumptions(SimpleNamespace(reviewed=True, model_context=model), "English")
    assert "![tracking](" not in table and "[link](" not in table
    assert r"\!\[tracking\]\(" in table and "&#124; extra" in table
