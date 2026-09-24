"""Lossless verification-only nesting and pre-dispatch provider-byte admission."""

import json
from copy import deepcopy

import pytest

from tests.test_research_verification_repair import _verification
from tradingagents.research.engine import run_research
from tradingagents.research.prompt_context import (
    INDEXED_CONTEXT_POLICY,
    PROMPT_CONTEXT_ENCODING_VERSION,
    TABLE_CONTEXT_POLICY,
    _shared_packet,
    compact_prompt_context,
    expand_prompt_context,
    model_prompt,
)
from tradingagents.research.reader_revision import (
    GENERIC_REVISION_POLICY,
    GENERIC_REVISION_POLICY_V4,
    VERIFICATION_REPAIR_POLICY,
)
from tradingagents.research.services import ResearchServices
from tradingagents.research.storage import canonical_json, read_json


def _nested_payload():
    parents = [{"scope": str(i), "common": "Exact evidence with Unicode 中文. " * 3200,
                "detail": str(i) * 1100} for i in range(12)]
    return {"original": parents, "repeat": deepcopy(parents),
            "individual": [deepcopy(p) for p in reversed(parents)]}


def test_nested_sharing_is_lossless_smaller_and_opt_in():
    payload = _nested_payload()
    before = deepcopy(payload)
    legacy = compact_prompt_context(payload)
    nested = compact_prompt_context(payload, recursive_shared=True)
    assert len(canonical_json(legacy)) > 1_048_576
    assert len(canonical_json(nested)) < 1_048_576
    assert expand_prompt_context(nested) == before == payload
    assert model_prompt(payload) == canonical_json(legacy)
    flagged = {**payload, "verification_repair_policy": VERIFICATION_REPAIR_POLICY}
    assert expand_prompt_context(json.loads(model_prompt(flagged))) == flagged
    assert len(model_prompt(flagged)) < len(canonical_json(legacy))


def test_numbered_revision_small_shared_context_is_lossless_and_scoped():
    payload = {f"item_{i}": {"detail": "Evidence 中文 " * 30, "id": i}
               for i in range(18)}
    legacy = model_prompt(payload)
    frozen = model_prompt({**payload, "verification_repair_policy": VERIFICATION_REPAIR_POLICY})
    generic_data = {**payload, "reader_revision_policy": GENERIC_REVISION_POLICY}
    generic = model_prompt(generic_data)
    assert expand_prompt_context(json.loads(generic)) == generic_data
    assert model_prompt(payload) == legacy
    assert model_prompt({**payload, "verification_repair_policy": VERIFICATION_REPAIR_POLICY}) == frozen
    assert len(generic) < len(frozen)


def test_cost_aware_sharing_avoids_short_reference_overhead():
    payload = {str(i): {"text": str(i % 10) * 180, "unique": i}
               for i in range(20)}
    naive = _shared_packet(payload, recursive=True, min_shared_bytes=128)
    economical = _shared_packet(payload, recursive=True, min_shared_bytes=128,
                                cost_aware=True)
    assert len(canonical_json(economical)) < len(canonical_json(naive))
    assert economical["payload"] == payload and not economical["shared_context"]


def test_numbered_revision_nested_tables_hash_original_values():
    rows = [{"description": "Repeated exact evidence " * 30,
             "unusually_long_column_name": str(index), "amount": index}
            for index in range(30)]
    payload = {"reader_revision_policy": GENERIC_REVISION_POLICY,
               "left": rows, "right": deepcopy(rows),
               "different": [{**row, "amount": row["amount"] + 100} for row in rows]}
    assert expand_prompt_context(json.loads(model_prompt(payload))) == payload
    encoded = _shared_packet(payload, recursive=True, min_shared_bytes=256,
                             tables_first=True)
    packet = {"context_encoding": PROMPT_CONTEXT_ENCODING_VERSION,
              "context_policy": TABLE_CONTEXT_POLICY, **encoded}
    assert expand_prompt_context(packet) == payload
    assert '"research_context_table"' in canonical_json(packet).decode()


def _indexed_payload():
    return {"reader_revision_policy": GENERIC_REVISION_POLICY_V4,
            "rows": [{"unique": i, "source_hash": str(i % 7) * 64,
                      "text": "Exact evidence 中文 " * (8 + i % 5)} for i in range(250)]}


def test_v4_indexed_references_reduce_bytes_without_changing_earlier_policies():
    payload = _indexed_payload()
    plain = compact_prompt_context(payload, recursive_shared=True, aggressive_shared=True)
    packed = json.loads(model_prompt(payload))
    assert packed["context_policy"] == INDEXED_CONTEXT_POLICY
    assert len(canonical_json(packed)) < len(canonical_json(plain))
    assert canonical_json(expand_prompt_context(packed)) == canonical_json(payload)
    assert all(len(entry[0]) == 64 for entry in packed["shared_context"])
    for policy in (GENERIC_REVISION_POLICY, VERIFICATION_REPAIR_POLICY):
        legacy = {**payload, "reader_revision_policy": policy}
        expected = compact_prompt_context(legacy,
            recursive_shared=policy == GENERIC_REVISION_POLICY,
            aggressive_shared=policy == GENERIC_REVISION_POLICY)
        assert model_prompt(legacy) == canonical_json(expected)


@pytest.mark.parametrize("mutation", ["wrong_hash", "duplicate", "unused", "cycle",
                                     "bool_ref", "negative_ref", "too_large_ref", "wrong_policy"])
def test_indexed_decoder_rejects_invalid_catalog_and_references(mutation):
    packed = json.loads(model_prompt(_indexed_payload()))
    assert packed["context_policy"] == INDEXED_CONTEXT_POLICY
    if mutation == "wrong_hash":
        packed["shared_context"][0][0] = "0" * 64
    elif mutation == "duplicate":
        packed["shared_context"].append(deepcopy(packed["shared_context"][0]))
    elif mutation == "unused":
        from tradingagents.research.storage import digest
        packed["shared_context"].append([digest("unused"), "unused"])
    elif mutation == "cycle":
        packed["payload"] = {"research_context_ref": 0}
        packed["shared_context"][0][1] = {"research_context_ref": 0}
    elif mutation in {"bool_ref", "negative_ref", "too_large_ref"}:
        index = {"bool_ref": True, "negative_ref": -1, "too_large_ref": 10**20}[mutation]
        packed["payload"] = {"research_context_ref": index}
    else:
        packed["context_policy"] = "Unrecognized indexed policy"
    with pytest.raises(ValueError):
        expand_prompt_context(packed)


def test_indexed_source_owned_markers_and_root_are_not_interpreted():
    for payload in (
        {"research_context_ref": 0, "original": "source-owned"},
        {"context_encoding": PROMPT_CONTEXT_ENCODING_VERSION,
         "context_policy": INDEXED_CONTEXT_POLICY, "shared_context": [["source", "data"]]},
    ):
        for policy in (GENERIC_REVISION_POLICY, GENERIC_REVISION_POLICY_V4):
            original = {**payload, "reader_revision_policy": policy}
            assert expand_prompt_context(json.loads(model_prompt(original))) == original


@pytest.mark.parametrize("marker", ["research_context_ref", "research_context_table"])
def test_nested_sharing_never_interprets_source_owned_tags(marker):
    payload = {**_nested_payload(), "source_owned": {marker: "untrusted"}}
    packed = compact_prompt_context(payload, recursive_shared=True)
    assert packed == payload


def test_nested_sharing_preserves_literal_root_escaping():
    payload = {"context_encoding": "exact-shared-context-v2", **_nested_payload()}
    packed = compact_prompt_context(payload, recursive_shared=True)
    assert packed["literal_payload"] is True
    assert expand_prompt_context(packed) == payload


def test_nested_sharing_rejects_cycles_and_tampered_catalog():
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="cycle"):
        compact_prompt_context(cycle, recursive_shared=True)
    packed = compact_prompt_context(_nested_payload(), recursive_shared=True)
    key = next(iter(packed["shared_context"]))
    packed["shared_context"][key] = "changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        expand_prompt_context(packed)


def test_prompt_size_admission_stops_before_dispatch_with_complete_usage(tmp_path):
    _, request, evidence, service, provider = _verification(tmp_path)
    service.max_prompt_utf8_bytes = 10
    result = run_research(request, ResearchServices(evidence, service))
    assert result.stop_reason == "verification_prompt_size_limit"
    assert not provider.calls
    assert result.usage == service.plan.plan.source_usage and result.usage.complete
    resources = read_json(request.output_dir / "stages/resources.json")["output"]
    assert not resources["dispatched"] and resources["budget_usage"]["complete"]
    admission = read_json(request.output_dir / "stages/reverification-admission.json")["output"]
    assert not admission["prompt_admission"]["fits"]
    assert admission["prompt_admission"]["calls"][0]["stage"] == "verify_frozen_report"
    assert not service.candidate_recovery_context["current_calls"]
