from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest

from tradingagents.research.contracts import Assessment, Dossier
from tradingagents.research.dossiers import (
    DossierStore,
    DossierStoreError,
    ForecastRecord,
    ReportedActual,
    score_forecast,
)

UTC = timezone.utc
D = Decimal


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _dossier(
    identifier: str,
    cutoff: datetime,
    *,
    status: str = "accepted",
    ticker: str = "AMD",
    evidence: str | None = None,
) -> Dossier:
    return Dossier(
        id=identifier,
        ticker=ticker,
        cutoff=cutoff,
        created_at=cutoff.replace(hour=cutoff.hour + 1),
        evidence_hash=_hash(evidence or identifier),
        assessment=Assessment(
            status=status, investment_view="neutral" if status == "accepted" else "unrated"
        ),
        artifact_hashes={"research.json": _hash(f"artifact-{identifier}")},
    )


def _forecast(**overrides) -> ForecastRecord:
    data = {
        "id": "forecast-1",
        "dossier_id": "dossier-1",
        "ticker": "AMD",
        "metric": "revenue",
        "basis": "GAAP consolidated",
        "unit": "USD",
        "value": "100",
        "scale": "1000000",
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "evidence_cutoff": "2026-01-10T00:00:00Z",
        "forecast_at": "2026-01-10T02:00:00Z",
    }
    data.update(overrides)
    return ForecastRecord.model_validate(data)


def _actual(**overrides) -> ReportedActual:
    data = {
        "source_id": "filing-1",
        "ticker": "AMD",
        "metric": "revenue",
        "basis": "GAAP consolidated",
        "unit": "USD",
        "value": "110",
        "scale": "1000000",
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "published_at": "2027-01-20T00:00:00Z",
    }
    data.update(overrides)
    return ReportedActual.model_validate(data)


def test_accepted_save_is_immutable_hashed_and_promoted(tmp_path) -> None:
    store = DossierStore(tmp_path)
    dossier = _dossier("dossier-1", _dt(10))
    result = store.save_dossier(dossier, evidence_available_at=_dt(9))

    assert result.stored is True
    assert result.promoted is True
    assert result.accepted_pointer_id == dossier.id
    assert store.load_current_accepted("AMD") == dossier
    assert store.load_dossier("AMD", dossier.id) == dossier

    same = store.save_dossier(dossier, evidence_available_at=_dt(9))
    assert same.stored is False
    assert same.promoted is False
    assert same.dossier_hash == result.dossier_hash

    path = tmp_path / "tickers/AMD/dossiers/dossier-1.json"
    record = json.loads(path.read_text())
    assert record["dossier_hash"] == result.dossier_hash
    assert len(record["record_hash"]) == 64


def test_existing_dossier_id_cannot_be_overwritten_with_different_content(tmp_path) -> None:
    store = DossierStore(tmp_path)
    original = _dossier("dossier-1", _dt(10))
    store.save_dossier(original, evidence_available_at=_dt(9))
    changed = _dossier("dossier-1", _dt(10), evidence="different")

    with pytest.raises(DossierStoreError, match="different content"):
        store.save_dossier(changed, evidence_available_at=_dt(9))
    assert store.load_dossier("AMD", "dossier-1") == original


def test_degraded_and_older_accepted_records_do_not_downgrade_pointer(tmp_path) -> None:
    store = DossierStore(tmp_path)
    newest = _dossier("accepted-new", _dt(20))
    store.save_dossier(newest, evidence_available_at=_dt(19))

    degraded = _dossier("degraded", _dt(25), status="needs_review")
    degraded_result = store.save_dossier(degraded, evidence_available_at=_dt(24))
    older = _dossier("accepted-old", _dt(10))
    older_result = store.save_dossier(older, evidence_available_at=_dt(9))

    assert degraded_result.stored is True and degraded_result.promoted is False
    assert older_result.stored is True and older_result.promoted is False
    assert store.load_dossier("AMD", "degraded") == degraded
    assert store.load_current_accepted("AMD") == newest


def test_historical_load_uses_requested_cutoff_not_latest_pointer(tmp_path) -> None:
    store = DossierStore(tmp_path)
    older = _dossier("older", _dt(10))
    newer = _dossier("newer", _dt(20))
    store.save_dossier(older, evidence_available_at=_dt(9))
    store.save_dossier(newer, evidence_available_at=_dt(19))

    assert store.load_current_accepted("AMD") == newer
    assert store.load_eligible("AMD", _dt(15)) == older
    assert store.load_eligible("AMD", _dt(9)) is None
    assert store.load_eligible("AMD", _dt(10)) is None
    assert store.load_eligible("AMD", _dt(10, 1)) == older


def test_unknown_future_evidence_and_invalid_cutoffs_fail_closed(tmp_path) -> None:
    store = DossierStore(tmp_path)
    dossier = _dossier("dossier-1", _dt(10))
    with pytest.raises(DossierStoreError, match="unknown"):
        store.save_dossier(dossier, evidence_available_at=None)
    with pytest.raises(DossierStoreError, match="future evidence"):
        store.save_dossier(dossier, evidence_available_at=_dt(11))
    with pytest.raises(DossierStoreError, match="timezone-aware"):
        store.load_eligible("AMD", datetime(2026, 1, 10))


@pytest.mark.parametrize(
    "dossier",
    [
        _dossier("../escape", _dt(10)),
        _dossier("safe", _dt(10), ticker="../AMD"),
    ],
)
def test_path_traversal_names_are_rejected(tmp_path, dossier) -> None:
    store = DossierStore(tmp_path)
    with pytest.raises(DossierStoreError, match="path name"):
        store.save_dossier(dossier, evidence_available_at=_dt(9))
    assert not (tmp_path / "escape.json").exists()


def test_corruption_is_detected_in_record_and_pointer(tmp_path) -> None:
    store = DossierStore(tmp_path)
    dossier = _dossier("dossier-1", _dt(10))
    store.save_dossier(dossier, evidence_available_at=_dt(9))
    record_path = tmp_path / "tickers/AMD/dossiers/dossier-1.json"
    record = json.loads(record_path.read_text())
    record["dossier"]["evidence_hash"] = _hash("tampered")
    record_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
    with pytest.raises(DossierStoreError, match="hash mismatch"):
        store.load_dossier("AMD", "dossier-1")

    # Restore via a separate ticker, then corrupt only the pointer binding.
    intact = _dossier("intel-1", _dt(10), ticker="INTC")
    store.save_dossier(intact, evidence_available_at=_dt(9))
    pointer_path = tmp_path / "tickers/INTC/accepted.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["dossier_hash"] = "0" * 64
    pointer_path.write_text(json.dumps(pointer, sort_keys=True, separators=(",", ":")))
    with pytest.raises(DossierStoreError, match="pointer hash mismatch"):
        store.load_current_accepted("INTC")


def test_symlinked_collection_pointer_and_lock_are_rejected(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    root = tmp_path / "collection-case"
    (root / "tickers/AMD").mkdir(parents=True)
    (root / "tickers/AMD/dossiers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DossierStoreError, match="symlink"):
        DossierStore(root).save_dossier(_dossier("one", _dt(10)), evidence_available_at=_dt(9))
    assert not list(outside.iterdir())

    pointer_root = tmp_path / "pointer-case"
    pointer_store = DossierStore(pointer_root)
    pointer_store.save_dossier(_dossier("one", _dt(10)), evidence_available_at=_dt(9))
    pointer = pointer_root / "tickers/AMD/accepted.json"
    pointer.unlink()
    pointer.symlink_to(outside / "pointer.json")
    with pytest.raises(DossierStoreError, match="symlink"):
        pointer_store.save_dossier(_dossier("two", _dt(20)), evidence_available_at=_dt(19))

    lock_root = tmp_path / "lock-case"
    (lock_root / "tickers/AMD").mkdir(parents=True)
    (lock_root / "tickers/AMD/.dossier.lock").symlink_to(outside / "lock")
    with pytest.raises(DossierStoreError, match="lock"):
        DossierStore(lock_root).save_dossier(_dossier("one", _dt(10)), evidence_available_at=_dt(9))


def test_per_ticker_lock_rejects_concurrent_writer_but_not_other_ticker(tmp_path) -> None:
    first = DossierStore(tmp_path)
    second = DossierStore(tmp_path)
    with first.ticker_lock("AMD"):
        with pytest.raises(DossierStoreError, match="in use"):
            second.save_dossier(_dossier("amd-1", _dt(10)), evidence_available_at=_dt(9))
        intel = _dossier("intel-1", _dt(10), ticker="INTC")
        assert second.save_dossier(intel, evidence_available_at=_dt(9)).promoted


def test_forecast_vintage_is_immutable_and_bound_to_dossier(tmp_path) -> None:
    store = DossierStore(tmp_path)
    dossier = _dossier("dossier-1", _dt(10))
    store.save_dossier(dossier, evidence_available_at=_dt(9))
    forecast = _forecast()

    first = store.save_forecast(forecast)
    second = store.save_forecast(forecast)
    assert first.stored is True and second.stored is False
    assert store.load_forecast("AMD", "forecast-1") == forecast

    changed = forecast.model_copy(update={"value": D("101")})
    with pytest.raises(DossierStoreError, match="different content"):
        store.save_forecast(changed)

    with pytest.raises(DossierStoreError, match="predate dossier creation"):
        store.save_forecast(_forecast(id="backdated", forecast_at="2026-01-10T00:30:00Z"))

    with pytest.raises(DossierStoreError, match="evidence cutoff"):
        store.save_forecast(_forecast(id="wrong-cutoff", evidence_cutoff="2026-01-09T00:00:00Z"))

    degraded = _dossier("degraded", _dt(20), status="needs_review")
    store.save_dossier(degraded, evidence_available_at=_dt(19))
    with pytest.raises(DossierStoreError, match="accepted dossier"):
        store.save_forecast(
            _forecast(
                id="degraded-forecast",
                dossier_id="degraded",
                evidence_cutoff=_dt(20),
                forecast_at=_dt(20, 2),
            )
        )


def test_forecast_score_is_decimal_accounting_error_not_stock_pnl() -> None:
    forecast = _forecast()
    actual = _actual(source_id="sec:0000002488-27-000001")
    score = score_forecast(forecast, actual, evaluation_cutoff=datetime(2027, 1, 31, tzinfo=UTC))

    assert score.forecast_value == D("100000000")
    assert score.actual_value == D("110000000")
    assert score.signed_error == D("-10000000")
    assert score.absolute_error == D("10000000")
    with localcontext() as context:
        context.prec = 40
        expected_percentage = D("-10000000") / D("110000000")
        expected_absolute_percentage = abs(expected_percentage)
    assert score.percentage_error == expected_percentage
    assert score.absolute_percentage_error == expected_absolute_percentage
    assert "stock" not in score.model_dump_json().lower()
    assert forecast.value == D("100")  # scoring did not rewrite the forecast
    assert score.actual_source_id == "sec:0000002488-27-000001"


def test_forecast_scoring_is_independent_of_global_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 6
        low_context = score_forecast(
            _forecast(), _actual(), evaluation_cutoff=datetime(2027, 1, 31, tzinfo=UTC)
        )
    with localcontext() as context:
        context.prec = 50
        high_context = score_forecast(
            _forecast(), _actual(), evaluation_cutoff=datetime(2027, 1, 31, tzinfo=UTC)
        )
    assert low_context == high_context


@pytest.mark.parametrize(
    ("actual_change", "message"),
    [
        ({"basis": "non-GAAP adjusted"}, "reported basis mismatch"),
        ({"unit": "EUR"}, "unit mismatch"),
        ({"scale": "1"}, "scale mismatch"),
        ({"period_end": "2027-12-31"}, "period end mismatch"),
        ({"ticker": "INTC"}, "ticker mismatch"),
    ],
)
def test_forecast_score_rejects_unmatched_reported_basis(actual_change, message) -> None:
    with pytest.raises(DossierStoreError, match=message):
        score_forecast(
            _forecast(),
            _actual(**actual_change),
            evaluation_cutoff=datetime(2028, 1, 31, tzinfo=UTC),
        )


def test_forecast_score_blocks_preforecast_and_future_publications() -> None:
    forecast = _forecast()
    with pytest.raises(DossierStoreError, match="after the forecast"):
        score_forecast(
            forecast,
            _actual(published_at="2026-01-10T02:00:00Z"),
            evaluation_cutoff=datetime(2027, 1, 31, tzinfo=UTC),
        )
    with pytest.raises(DossierStoreError, match="evaluation cutoff"):
        score_forecast(
            forecast,
            _actual(),
            evaluation_cutoff=datetime(2027, 1, 19, tzinfo=UTC),
        )
