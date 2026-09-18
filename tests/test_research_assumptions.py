"""Bounded, offline FCFF assumption-package contracts and validation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.research.assumptions import (
    ASSUMPTION_PACKAGE_SCOPE,
    REQUIRED_FCFF_PATHS,
    AssumptionEntry,
    AssumptionPackage,
    AssumptionRange,
    assumption_blockers,
    validate_assumption_package,
)
from tradingagents.research.contracts import (
    EvidenceSnapshot,
    FinancialFact,
    ResearchRequest,
    SourceDocument,
    default_roles,
)

CUTOFF = "2020-07-15T12:00:00+00:00"
CREATED_AT = "2020-07-16T12:00:00+00:00"


def _source(**updates) -> SourceDocument:
    content = updates.pop("content", "NVDA historical filing tables")
    values = {
        "id": "nvda-filing",
        "url": "https://example.test/nvda-filing",
        "title": "NVDA filing",
        "publisher": "NVIDIA",
        "retrieved_at": "2020-07-14T12:00:00+00:00",
        "published_at": "2020-07-10T12:00:00+00:00",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "kind": "filing",
    }
    values.update(updates)
    return SourceDocument(**values)


def _fact(identifier: str, metric: str, value: str, **updates) -> FinancialFact:
    values = {
        "id": identifier,
        "source_id": "nvda-filing",
        "metric": metric,
        "value": value,
        "unit": "USD",
        "currency": "USD",
        "period_end": "2020-06-30",
        "period_type": "instant",
        "basis": "US GAAP",
        "location": "explicit filing table",
    }
    values.update(updates)
    return FinancialFact(**values)


def _snapshot(*, source: SourceDocument | None = None) -> EvidenceSnapshot:
    component = _fact(
        "revenue-component",
        "revenue",
        "90",
        period_start="2019-07-01",
        period_type="duration",
        period_end="2020-03-31",
    )
    revenue = _fact(
        "revenue-ttm",
        "revenue",
        "100",
        scale="1000000",
        period_start="2019-07-01",
        period_type="duration",
        inputs=("revenue-component",),
        formula="revenue-component + explicit fourth quarter",
        location="derived TTM schedule with retained ancestry",
    )
    working_capital = _fact("working-capital", "working_capital", "20", scale="1000000")
    net_debt = _fact("net-debt", "net_debt", "-5", scale="1000000")
    shares = _fact(
        "diluted-shares-q2",
        "weighted_average_diluted_shares",
        "10",
        scale="1000000",
        unit="shares",
        currency=None,
        period_start="2020-04-01",
        period_type="duration",
        location="latest-quarter weighted-average diluted-share proxy",
    )
    return EvidenceSnapshot(
        ticker="NVDA",
        cutoff=CUTOFF,
        sources=(source or _source(),),
        facts=(component, revenue, working_capital, net_debt, shares),
        gaps=("Point-in-time diluted capitalization was unavailable.",),
    )


def _request(**updates) -> ResearchRequest:
    values = {
        "ticker": "NVDA",
        "cutoff": CUTOFF,
        "timezone": "UTC",
        "backend": "api",
        "output_dir": "research-output",
        "quality_revision": "evidence-led-bounded",
        "valuation_method": "fcff",
        "share_count_basis": "latest_quarter_diluted_proxy",
        "models": {role: setting.model_dump() for role, setting in default_roles().items()},
    }
    values.update(updates)
    return ResearchRequest(**values)


def _numeric(
    identifier: str,
    path: str,
    category: str,
    status: str,
    *,
    value: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    fact_id: str | None = None,
    rationale: str = "Explicitly bounded input with identified provenance.",
) -> AssumptionEntry:
    return AssumptionEntry(
        id=identifier,
        model_path=path,
        category=category,
        status=status,
        unit="fraction" if path not in {"current_revenue", "current_working_capital", "net_debt", "current_diluted_shares"} else ("shares" if path == "current_diluted_shares" else "USD"),
        rationale=rationale,
        evidence_ids=evidence_ids,
        range=None if value is None else AssumptionRange(low=value, base=value, high=value),
        fact_id=fact_id,
    )


def _entries() -> tuple[AssumptionEntry, ...]:
    historical = (
        _numeric("revenue", "current_revenue", "historical_anchor", "draft", value="100000000", evidence_ids=("revenue-ttm", "revenue-component"), fact_id="revenue-ttm"),
        _numeric("wc", "current_working_capital", "historical_anchor", "draft", value="20000000", evidence_ids=("working-capital",), fact_id="working-capital"),
        _numeric("debt", "net_debt", "historical_anchor", "draft", value="-5000000", evidence_ids=("net-debt",), fact_id="net-debt"),
        _numeric("shares", "current_diluted_shares", "historical_anchor", "draft", value="10000000", evidence_ids=("diluted-shares-q2",), fact_id="diluted-shares-q2"),
    )
    missing_external = (
        _numeric("discount", "discount_rate", "external_input", "missing", rationale="No cutoff-eligible market input has been supplied."),
        _numeric("terminal", "terminal_growth", "external_input", "missing", rationale="No cutoff-eligible terminal-growth input has been supplied."),
    )
    missing_forecasts = tuple(
        _numeric(
            f"forecast-{index}",
            path,
            "analyst_assumption",
            "missing",
            evidence_ids=("revenue-ttm",),
            rationale="Historical calibration evidence exists, but no numerical forecast choice has been made.",
        )
        for index, path in enumerate(
            sorted(
                path
                for path in REQUIRED_FCFF_PATHS
                if path.startswith("periods.*.")
                and path
                not in {
                    "periods.*.operating_margin_basis",
                    "periods.*.external_funding_required",
                }
            )
        )
    )
    conventions = tuple(
        AssumptionEntry(
            id=f"convention-{index}",
            model_path=path,
            category="model_convention",
            status="draft",
            unit="convention",
            rationale="Explicit model convention; it is not an external fact.",
            value=value,
        )
        for index, (path, value) in enumerate(
            (
                ("as_of_date", "2020-07-15"),
                ("units.currency", "USD"),
                ("units.amount_scale", "1000000"),
                ("units.share_scale", "1000000"),
                ("periods.*.operating_margin_basis", "after_sbc"),
                ("periods.*.external_funding_required", "false"),
                ("forecast_schedule", "annual periods ending on cutoff anniversaries"),
            )
        )
    )
    return historical + missing_external + missing_forecasts + conventions


def _package(snapshot: EvidenceSnapshot, **updates) -> tuple[AssumptionPackage, bytes]:
    evidence_bytes = snapshot.model_dump_json().encode()
    values = {
        "ticker": "NVDA",
        "cutoff": CUTOFF,
        "created_at": CREATED_AT,
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "valuation_method": "fcff",
        "entries": _entries(),
        "limitations": (
            "Preparation only; missing inputs prevent valuation readiness.",
            "This package is not an accepted investment recommendation.",
        ),
    }
    values.update(updates)
    return AssumptionPackage(**values), evidence_bytes


def _replace_entry(package: AssumptionPackage, path: str, **updates) -> AssumptionPackage:
    entries = tuple(
        entry.model_copy(update=updates) if entry.model_path == path else entry
        for entry in package.entries
    )
    return package.model_copy(update={"entries": entries})


def test_contract_defaults_ranges_and_closed_fcff_paths() -> None:
    entry = AssumptionEntry(
        id="discount",
        model_path="discount_rate",
        category="external_input",
        status="missing",
        unit="fraction",
        rationale="No market evidence supplied.",
    )
    assert entry.evidence_ids == entry.limitations == ()
    assert entry.value is entry.range is entry.fact_id is None
    assert AssumptionPackage.model_fields["reviewer"].default is None
    assert AssumptionPackage.model_fields["reviewed_at"].default is None
    assert AssumptionPackage.model_fields["value_basis"].default == "normalized_base_units"

    for values in (("2", "1", "3"), ("NaN", "1", "3")):
        with pytest.raises(ValidationError):
            AssumptionRange(low=values[0], base=values[1], high=values[2])
    with pytest.raises(ValidationError, match="unknown FCFF model path"):
        changed = entry.model_copy(update={"model_path": "periods.0.revenue_growth"})
        AssumptionEntry.model_validate(changed.model_dump())
    with pytest.raises(ValidationError, match="fraction units"):
        AssumptionEntry(
            id="percent-growth",
            model_path="periods.*.revenue_growth",
            category="analyst_assumption",
            status="draft",
            unit="percent",
            rationale="Incorrectly labeled unit.",
            evidence_ids=("some-evidence",),
            range=AssumptionRange(low="0.1", base="0.1", high="0.1"),
        )
    with pytest.raises(ValidationError):
        AssumptionPackage(
            ticker="NVDA",
            cutoff=CUTOFF,
            created_at=CREATED_AT,
            evidence_sha256="0" * 64,
            valuation_method="equity_fcfe",
            entries=(entry,),
            limitations=("FCFF only.",),
        )
    snapshot = _snapshot()
    package, _ = _package(snapshot)
    assert package.value_basis == "normalized_base_units"
    assert package.model_dump(mode="json")["value_basis"] == "normalized_base_units"
    with pytest.raises(ValidationError):
        AssumptionPackage.model_validate(
            {**package.model_dump(mode="json"), "value_basis": "model_scaled_units"}
        )
    assert "not an accepted investment recommendation" in ASSUMPTION_PACKAGE_SCOPE


def test_incomplete_nvda_packet_is_valid_preparation_but_not_ready() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)

    assert validate_assumption_package(package, snapshot, _request(), evidence_bytes) == package
    blockers = assumption_blockers(package)
    assert len(package.entries) == 20
    assert len(blockers) == 20
    assert "missing required assumption: discount_rate" in blockers
    assert "draft required assumption: current_revenue" in blockers
    assert "missing required assumption: periods.*.revenue_growth" in blockers
    assert not any("limitation" in blocker.lower() for blocker in blockers)


def test_hash_parsed_snapshot_and_identity_are_all_bound() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    request = _request()

    with pytest.raises(ValueError, match="evidence_sha256"):
        validate_assumption_package(
            package.model_copy(update={"evidence_sha256": "0" * 64}),
            snapshot,
            request,
            evidence_bytes,
        )
    other = snapshot.model_copy(update={"gaps": (*snapshot.gaps, "different")})
    with pytest.raises(ValueError, match="parsed snapshot"):
        validate_assumption_package(package, other, request, evidence_bytes)
    with pytest.raises(ValueError, match="ticker"):
        validate_assumption_package(
            package.model_copy(update={"ticker": "AMD"}), snapshot, request, evidence_bytes
        )
    duplicate_key_bytes = evidence_bytes.replace(
        b'{"schema_version":1,', b'{"schema_version":1,"schema_version":1,', 1
    )
    duplicate_hash_package = package.model_copy(
        update={"evidence_sha256": hashlib.sha256(duplicate_key_bytes).hexdigest()}
    )
    with pytest.raises(ValueError, match="evidence_bytes"):
        validate_assumption_package(
            duplicate_hash_package, snapshot, request, duplicate_key_bytes
        )


def test_equity_request_and_late_mutable_source_fail_explicitly() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    equity_request = _request(valuation_method="equity_fcfe")
    with pytest.raises(ValueError, match="FCFF only"):
        validate_assumption_package(package, snapshot, equity_request, evidence_bytes)

    late = _source(retrieved_at="2020-07-16T12:00:00+00:00", kind="news")
    late_snapshot = _snapshot(source=late)
    late_package, late_bytes = _package(late_snapshot)
    with pytest.raises(ValueError, match="retrieved after"):
        validate_assumption_package(late_package, late_snapshot, _request(), late_bytes)


@pytest.mark.parametrize(
    ("path", "updates", "message"),
    [
        ("current_revenue", {"range": AssumptionRange(low="1", base="1", high="1")}, "normalized fact"),
        ("current_working_capital", {"unit": "shares"}, "monetary unit"),
        ("net_debt", {"fact_id": "working-capital", "evidence_ids": ("working-capital",)}, "metric"),
    ],
)
def test_historical_anchors_require_exact_fact_value_metric_and_unit(path, updates, message) -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    changed = _replace_entry(package, path, **updates)
    with pytest.raises(ValueError, match=message):
        validate_assumption_package(changed, snapshot, _request(), evidence_bytes)


def test_historical_anchors_require_common_date_basis_and_latest_share_proxy() -> None:
    snapshot = _snapshot()
    package, _ = _package(snapshot)

    facts = tuple(
        fact.model_copy(update={"period_end": datetime(2020, 6, 29).date()})
        if fact.id == "net-debt"
        else fact
        for fact in snapshot.facts
    )
    changed_snapshot = snapshot.model_copy(update={"facts": facts})
    changed_package, changed_bytes = _package(changed_snapshot)
    with pytest.raises(ValueError, match="common period end"):
        validate_assumption_package(changed_package, changed_snapshot, _request(), changed_bytes)

    newer_share = _fact(
        "diluted-shares-newer",
        "weighted_average_diluted_shares",
        "11",
        unit="shares",
        currency=None,
        period_start="2020-04-16",
        period_end="2020-07-14",
        period_type="duration",
    )
    newer_snapshot = snapshot.model_copy(update={"facts": (*snapshot.facts, newer_share)})
    newer_package, newer_bytes = _package(newer_snapshot)
    with pytest.raises(ValueError, match="latest eligible quarter"):
        validate_assumption_package(newer_package, newer_snapshot, _request(), newer_bytes)

    fabricated_facts = tuple(
        fact.model_copy(update={"basis": "whatever"}) for fact in snapshot.facts
    )
    fabricated_snapshot = snapshot.model_copy(update={"facts": fabricated_facts})
    fabricated_package, fabricated_bytes = _package(fabricated_snapshot)
    with pytest.raises(ValueError, match="scope or basis"):
        validate_assumption_package(
            fabricated_package, fabricated_snapshot, _request(), fabricated_bytes
        )


def test_derived_historical_anchor_retains_snapshot_ancestry() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    validated = validate_assumption_package(package, snapshot, _request(), evidence_bytes)
    revenue = next(fact for fact in snapshot.facts if fact.id == "revenue-ttm")

    assert validated.entries[0].category == "historical_anchor"
    assert revenue.inputs == ("revenue-component",)
    assert revenue.formula == "revenue-component + explicit fourth quarter"

    stripped = _replace_entry(
        package, "current_revenue", evidence_ids=("revenue-ttm",)
    )
    with pytest.raises(ValueError, match="ancestry is not referenced"):
        validate_assumption_package(stripped, snapshot, _request(), evidence_bytes)


def test_nonmissing_choices_need_evidence_and_all_evidence_must_be_eligible() -> None:
    with pytest.raises(ValidationError, match="require evidence_ids"):
        _numeric(
            "forecast",
            "periods.*.revenue_growth",
            "analyst_assumption",
            "draft",
            value="0.1",
        )

    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    changed = _replace_entry(
        package,
        "periods.*.revenue_growth",
        evidence_ids=("invented-evidence",),
    )
    with pytest.raises(ValueError, match="ineligible evidence IDs"):
        validate_assumption_package(changed, snapshot, _request(), evidence_bytes)

    convention = next(entry for entry in package.entries if entry.model_path == "as_of_date")
    assert convention.category == "model_convention" and convention.evidence_ids == ()


def test_numeric_bounds_and_reviewed_discount_spread_are_enforced() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    discount = AssumptionRange(low="0.08", base="0.09", high="0.10")
    terminal = AssumptionRange(low="0.03", base="0.04", high="0.08")
    changed = _replace_entry(
        package,
        "discount_rate",
        status="reviewed",
        range=discount,
        evidence_ids=("nvda-filing",),
    )
    changed = _replace_entry(
        changed,
        "terminal_growth",
        status="reviewed",
        range=terminal,
        evidence_ids=("nvda-filing",),
    ).model_copy(
        update={
            "reviewer": "Human reviewer",
            "reviewed_at": datetime(2020, 7, 17, tzinfo=timezone.utc),
        }
    )
    with pytest.raises(ValueError, match="entire terminal_growth"):
        validate_assumption_package(changed, snapshot, _request(), evidence_bytes)

    huge = _replace_entry(
        package,
        "periods.*.capex_pct_revenue",
        status="draft",
        range=AssumptionRange(low="11", base="11", high="11"),
    )
    with pytest.raises(ValueError, match="outside FCFF bounds"):
        validate_assumption_package(huge, snapshot, _request(), evidence_bytes)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("as_of_date", "not-a-date", "as_of_date"),
        ("units.amount_scale", "abc", "invalid scale"),
        ("periods.*.operating_margin_basis", "cash-adjusted", "operating_margin_basis"),
        ("periods.*.external_funding_required", "nonsense", "true or false"),
    ],
)
def test_reviewed_conventions_cannot_be_nonsense(path, value, message) -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    changed = _replace_entry(package, path, status="reviewed", value=value).model_copy(
        update={
            "reviewer": "Human reviewer",
            "reviewed_at": datetime(2020, 7, 17, tzinfo=timezone.utc),
        }
    )
    with pytest.raises(ValueError, match=message):
        validate_assumption_package(changed, snapshot, _request(), evidence_bytes)


def test_positive_model_scales_need_not_equal_source_fact_scale() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    package = _replace_entry(package, "units.amount_scale", value="1")
    package = _replace_entry(package, "units.share_scale", value="1")
    assert validate_assumption_package(package, snapshot, _request(), evidence_bytes) == package


def test_review_metadata_and_wall_clock_are_bounded() -> None:
    snapshot = _snapshot()
    package, evidence_bytes = _package(snapshot)
    reviewed = _replace_entry(package, "current_revenue", status="reviewed")
    with pytest.raises(ValueError, match="require package reviewer"):
        validate_assumption_package(reviewed, snapshot, _request(), evidence_bytes)

    backwards = reviewed.model_copy(
        update={
            "reviewer": "Reviewer",
            "reviewed_at": datetime(2020, 7, 15, tzinfo=timezone.utc),
        }
    )
    with pytest.raises(ValueError, match="precede created_at"):
        validate_assumption_package(backwards, snapshot, _request(), evidence_bytes)

    future = package.model_copy(
        update={"created_at": datetime(2999, 1, 1, tzinfo=timezone.utc)}
    )
    with pytest.raises(ValueError, match="created_at cannot be in the future"):
        validate_assumption_package(future, snapshot, _request(), evidence_bytes)


def test_package_uniqueness_required_paths_and_caveats() -> None:
    snapshot = _snapshot()
    package, _ = _package(snapshot)
    duplicate = package.entries[0].model_copy(update={"id": package.entries[1].id})
    with pytest.raises(ValidationError, match="IDs must be unique"):
        AssumptionPackage(**{
            **package.model_dump(exclude={"entries"}),
            "entries": (duplicate, *package.entries[1:]),
        })

    partial = package.model_copy(update={"entries": package.entries[:-1]})
    assert "missing required path: forecast_schedule" in assumption_blockers(partial)
    caveat_heavy = package.model_copy(
        update={"limitations": ("Material caveat remains and is disclosed.",)}
    )
    assert assumption_blockers(caveat_heavy) == assumption_blockers(package)

    reviewed_without_metadata = _replace_entry(
        package, "current_revenue", status="reviewed"
    )
    assert "missing review metadata: reviewer" in assumption_blockers(
        reviewed_without_metadata
    )
