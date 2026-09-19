"""Synthetic, portable checks for the offline Microsoft evidence adapter."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from scripts.research_nvda_demand_evidence import (
    MICROSOFT_FY26_Q4_URL,
    DemandEvidenceExtractionError,
    load_demand_evidence,
)
from tradingagents.research.sources import FetchedSource, FileSourceCache, extract_text

SYNTHETIC_TRANSCRIPT = """Microsoft FY26 Q4 earnings transcript — July 29, 2026
Capital expenditures were $41 billion including the impact from higher component pricing as noted in our guide.
Roughly two thirds of our capex was for short-lived assets, primarily CPUs and GPUs as customers increasingly build solutions that leverage both AI and non-AI infrastructure.
In Azure and other cloud services, revenue grew 43% year over year.
Customer demand continues to exceed available capacity.
We also continue to modernize our fleet with our own silicon innovation, alongside the latest from NVIDIA and AMD.
And so, if the demand environment changes, you just slow down what is, in fact, the largest component and the driver of COGS.
Now, before I move to outlook, effective at the start of FY27, we are extending the estimated useful lives of our datacenters and office buildings, from 15 to 25 years, reflecting our operating history and expected use of these assets.
The impact of this update is reflected in today's guidance.
This change affects only the timing of future depreciation and is expected to have a minimal benefit to FY27 operating income.
The greater impact is on capital expenditures as more of our future datacenter leases will shift from finance leases to operating leases as a result of this update.
Finance leases are included in capital expenditures while operating leases are not.
Outside of this useful life impact, our calendar year 2026 CapEx investment expectations remain unchanged.
However, the shift from finance to operating leases adjusts our expectation to approximately $175 billion.
"""


def _cache(tmp_path, text: str = SYNTHETIC_TRANSCRIPT) -> FileSourceCache:
    cache = FileSourceCache(tmp_path / "synthetic-cache")
    raw = "".join(f"<p>{line}</p>" for line in text.splitlines() if line).encode("utf-8")
    extracted = extract_text(raw, media_type="text/html", charset="utf-8")
    assert extracted is not None
    cache.put(
        FetchedSource(
            requested_url=MICROSOFT_FY26_Q4_URL,
            final_url=MICROSOFT_FY26_Q4_URL,
            retrieved_at=datetime(2026, 9, 18, 10, 30, tzinfo=timezone.utc),
            status=200,
            media_type="text/html",
            charset="utf-8",
            raw=raw,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            text=extracted,
            text_sha256=hashlib.sha256(extracted.encode()).hexdigest(),
        )
    )
    return cache


def test_loads_hash_bound_bounded_material_without_backdating_retrieval(tmp_path) -> None:
    source, materials, claims = load_demand_evidence(_cache(tmp_path))

    assert source.retrieved_at == datetime(2026, 9, 18, 10, 30, tzinfo=timezone.utc)
    assert source.published_at == source.retrieved_at
    assert len(materials) == 5
    assert all(source.content[item.start:item.end] == item.text for item in materials)
    assert all(item.source_sha256 == source.content_sha256 for item in materials)
    assert all(len(item.text) < 1_500 for item in materials)
    assert len(claims) == 3
    assert "not an estimate of NVIDIA revenue" in claims[0]["analyst_implication"]
    assert "not equivalent to NVIDIA-specific demand" in claims[1]["analyst_implication"]
    assert "accounting-basis check" in claims[2]["analyst_implication"]


@pytest.mark.parametrize(
    "removed, expected",
    [
        ("And so, if the demand environment changes, you just slow down what is, in fact, the largest component and the driver of COGS.\n", "procurement"),
        ("Finance leases are included in capital expenditures while operating leases are not.\n", "lease"),
    ],
)
def test_missing_counterevidence_fails_closed(tmp_path, removed: str, expected: str) -> None:
    with pytest.raises(DemandEvidenceExtractionError, match=expected):
        load_demand_evidence(_cache(tmp_path, SYNTHETIC_TRANSCRIPT.replace(removed, "")))


def test_cache_hash_tampering_is_rejected(tmp_path) -> None:
    cache = _cache(tmp_path)
    index = next((tmp_path / "synthetic-cache" / "indexes").iterdir())
    index.write_text(index.read_text().replace('"status":200', '"status":201'))
    with pytest.raises(DemandEvidenceExtractionError, match="not verified"):
        load_demand_evidence(cache)
