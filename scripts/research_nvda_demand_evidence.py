"""Bounded Microsoft demand/counterevidence for an NVDA case.

The default path is offline: it reads one already-verified Microsoft transcript
from ``FileSourceCache`` and extracts a few short, hash-bound passages.  The
source is an independent customer/partner perspective, not an unbiased source,
and this adapter does not translate Microsoft's capital spending into NVIDIA
revenue or claim broad customer coverage.

``--fetch`` is intentionally opt-in.  It uses the project's bounded public
fetcher and writes only to a new cache directory; normal case construction must
use a frozen cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Final

from tradingagents.research.contracts import SourceDocument
from tradingagents.research.reviewed_inputs import ExactSourceMaterial
from tradingagents.research.sources import FetchedSource, FileSourceCache, PublicSourceFetcher

MICROSOFT_FY26_Q4_URL: Final = (
    "https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q4"
)
MICROSOFT_FY26_Q4_SOURCE_ID: Final = "msft-fy26-q4-earnings"
MICROSOFT_FY26_Q4_EVENT_DATE: Final = "2026-07-29"


class DemandEvidenceExtractionError(ValueError):
    """Raised when the one required cached transcript cannot support the packet."""


# These are deliberately anchored to the reviewed official wording and order, not
# generic topic keywords.  The captured text remains the exact contiguous cache
# slice, including source punctuation and whitespace.  A changed transcript needs
# a reviewed selector update rather than a looser substitute.
_SELECTORS: Final = (
    (
        "msft-capex-short-lived-assets",
        re.compile(
            r"Capital expenditures were \$41 billion including the impact from higher "
            r"component pricing as noted in our guide\.\s*"
            r"Roughly two thirds of our capex was for short-lived assets, "
            r"primarily CPUs and GPUs as customers increasingly build solutions that "
            r"leverage both AI and non-AI infrastructure\."
        ),
        "Microsoft FY26 Q4 capital spending and short-lived CPU/GPU mix.",
        "USD", "2026-06-30",
    ),
    (
        "msft-azure-demand-capacity",
        re.compile(
            r"In Azure and other cloud services, revenue grew 43%[^.]{0,320}\."
            r"(?:\s+[^.]{1,360}\.){0,3}\s*"
            r"Customer demand continues to exceed available capacity\."
        ),
        "Microsoft's stated Azure demand-versus-capacity condition.",
        "text", "2026-07-29",
    ),
    (
        "msft-multi-vendor-accelerators",
        re.compile(
            r"We also continue to modernize our fleet with our own silicon innovation, "
            r"alongside the latest from NVIDIA and AMD\."
        ),
        "Microsoft's stated own-silicon and third-party accelerator positioning.",
        "text", "2026-07-29",
    ),
    (
        "msft-procurement-demand-downside",
        re.compile(
            r"And so, if the demand environment changes, you just slow down what is, "
            r"in fact, the largest component and the driver of COGS\."
        ),
        "Microsoft's stated ability to adjust procurement if demand changes.",
        "text", "2026-07-29",
    ),
    (
        "msft-fy27-lease-life-classification",
        re.compile(
            r"Now, before I move to outlook, effective at the start of FY27, we are "
            r"extending the estimated useful lives of our datacenters and office buildings, "
            r"from 15 to 25 years, reflecting our operating history and expected use of "
            r"these assets\.\s*The impact of this update is reflected in today's guidance\.\s*"
            r"This change affects only the timing of future depreciation and is expected to "
            r"have a minimal benefit to FY27 operating income\.\s*The greater impact is on "
            r"capital expenditures as more of our future datacenter leases will shift from "
            r"finance leases to operating leases as a result of this update\.\s*Finance leases "
            r"are included in capital expenditures while operating leases are not\.\s*Outside "
            r"of this useful life impact, our calendar year 2026 CapEx investment expectations "
            r"remain unchanged\.\s*However, the shift from finance to operating leases adjusts "
            r"our expectation to approximately \$175 billion\."
        ),
        "Microsoft's stated FY27 useful-life change and finance-versus-operating lease capex presentation.",
        "text", "2026-07-29",
    ),
)


def _verified_document(source: FetchedSource) -> SourceDocument:
    """Convert one cache record only after validating its raw/text bindings."""
    if (
        source.status != 200
        or source.media_type != "text/html"
        or not source.text
        or not source.text_sha256
        or hashlib.sha256(source.raw).hexdigest() != source.raw_sha256
        or hashlib.sha256(source.text.encode("utf-8")).hexdigest() != source.text_sha256
    ):
        raise DemandEvidenceExtractionError("Microsoft transcript cache entry is not verified full-text HTML")
    # The cache has no publisher timestamp.  Treating observed retrieval as publication
    # is conservative and, crucially, never rewrites the actual retrieval timestamp.
    return SourceDocument(
        id=MICROSOFT_FY26_Q4_SOURCE_ID,
        url=source.final_url,
        title="Microsoft FY26 Q4 earnings transcript",
        publisher="Microsoft",
        retrieved_at=source.retrieved_at,
        published_at=source.retrieved_at,
        content=source.text,
        content_sha256=source.text_sha256,
        kind="ir",
        availability="full_text",
    )


def _material(
    source: SourceDocument,
    *,
    identifier: str,
    selector: re.Pattern[str],
    context: str,
    unit: str,
    observation_date: str,
) -> ExactSourceMaterial:
    event_date = re.escape(MICROSOFT_FY26_Q4_EVENT_DATE.replace("-", " "))
    if not re.search(r"(?i)(?:July\s+29,?\s+2026|" + event_date + r")", source.content):
        raise DemandEvidenceExtractionError("Microsoft transcript date selector is missing or changed")
    matches = list(selector.finditer(source.content))
    if len(matches) != 1:
        raise DemandEvidenceExtractionError(
            f"{identifier}: required named transcript passage is missing or ambiguous"
        )
    match = matches[0]
    return ExactSourceMaterial(
        id=identifier,
        source_id=source.id,
        source_sha256=source.content_sha256,
        start=match.start(),
        end=match.end(),
        text=match.group(0),
        context=context,
        unit=unit,
        observation_date=observation_date,
    )


def load_demand_evidence(
    cache: FileSourceCache,
) -> tuple[SourceDocument, tuple[ExactSourceMaterial, ...], tuple[dict, ...]]:
    """Load the complete, bounded Microsoft evidence set from a frozen cache.

    All selectors are required, including the procurement and reporting-basis
    counterevidence.  This prevents a caller from silently retaining only the
    demand-positive excerpts.
    """
    try:
        fetched = cache.get(MICROSOFT_FY26_Q4_URL)
    except Exception as exc:
        raise DemandEvidenceExtractionError("Microsoft transcript cache cannot be validated") from exc
    if fetched is None:
        raise DemandEvidenceExtractionError("Microsoft transcript cache entry is missing")
    source = _verified_document(fetched)
    materials = tuple(
        _material(
            source,
            identifier=identifier,
            selector=selector,
            context=context,
            unit=unit,
            observation_date=observation_date,
        )
        for identifier, selector, context, unit, observation_date in _SELECTORS
    )
    claims = (
        {
            "id": "msft-customer-demand-signal",
            "kind": "reported_with_analyst_implication",
            "source_ids": (source.id,),
            "source_material_ids": ("msft-capex-short-lived-assets", "msft-azure-demand-capacity"),
            "claim": "Microsoft reported $41 billion of FY26 Q4 capital expenditures including a CPU/GPU-heavy short-lived component, while stating Azure demand exceeded capacity.",
            "analyst_implication": "This is one customer-side demand and infrastructure signal; it can support scenario attention to accelerator availability, not an estimate of NVIDIA revenue.",
            "falsifier": "Later Microsoft disclosures showing sustained excess capacity, lower infrastructure deployment, or reduced accelerator procurement would weaken this signal.",
            "limitation": "Microsoft is a commercial participant and this single source is neither unbiased nor broad independent customer coverage.",
        },
        {
            "id": "msft-supplier-and-procurement-counterevidence",
            "kind": "reported_with_analyst_implication",
            "source_ids": (source.id,),
            "source_material_ids": ("msft-multi-vendor-accelerators", "msft-procurement-demand-downside"),
            "claim": "Microsoft described own-silicon innovation alongside NVIDIA and AMD, plus an ability to slow its largest COGS driver if demand changes.",
            "analyst_implication": "Customer infrastructure spending is not equivalent to NVIDIA-specific demand; supplier mix and procurement timing can change.",
            "falsifier": "Evidence that Microsoft committed exclusively to NVIDIA capacity without adjustment rights would contradict this counterweight.",
            "limitation": "The passage does not quantify vendor allocations, purchase orders, or NVIDIA revenue.",
        },
        {
            "id": "msft-reported-capex-accounting-counterevidence",
            "kind": "reported_with_analyst_implication",
            "source_ids": (source.id,),
            "source_material_ids": ("msft-fy27-lease-life-classification",),
            "claim": "Microsoft described an FY27 useful-life change that shifts some future datacenter leases from finance to operating leases; it states finance leases are included in capex and operating leases are not.",
            "analyst_implication": "Reported capex comparisons require an accounting-basis check before being used as a deployment proxy.",
            "falsifier": "A reconciled disclosure showing no useful-life or finance-lease presentation effect on the compared measure would remove this caution.",
            "limitation": "This is a reporting-basis caveat, not evidence that physical deployment rose or fell by the same amount.",
        },
    )
    return source, materials, claims


def _fetch_new_cache(destination: Path) -> FileSourceCache:
    if destination.exists():
        raise DemandEvidenceExtractionError("--destination-cache must be a new path")
    cache = FileSourceCache(destination)
    PublicSourceFetcher(cache=cache).fetch(MICROSOFT_FY26_Q4_URL, use_cache=False)
    return cache


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, nargs="?", help="existing frozen FileSourceCache directory")
    parser.add_argument("--fetch", action="store_true", help="fetch once into a new cache directory")
    parser.add_argument("--destination-cache", type=Path, help="new cache directory required with --fetch")
    args = parser.parse_args()
    if args.fetch:
        if args.cache is not None or args.destination_cache is None:
            parser.error("--fetch requires --destination-cache and no existing cache argument")
        cache = _fetch_new_cache(args.destination_cache)
    else:
        if args.cache is None or args.destination_cache is not None:
            parser.error("supply one existing cache directory, or use --fetch --destination-cache")
        cache = FileSourceCache(args.cache)
    try:
        source, materials, claims = load_demand_evidence(cache)
    except (DemandEvidenceExtractionError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"source": source.model_dump(mode="json"), "source_material": [item.model_dump(mode="json") for item in materials], "claims": claims}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
