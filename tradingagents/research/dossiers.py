"""Immutable local dossier history and deterministic forecast outcome scoring.

This is an M5 storage sidecar foundation.  It does not integrate with the research
engine, choose forecasts, revise models, or interpret forecast errors as stock P&L.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .contracts import Dossier, Identifier
from .storage import atomic_write, canonical_json, parse_json, read_bytes

DOSSIER_SCHEMA_VERSION = 1
FORECAST_SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 16 * 1024 * 1024
_SAFE_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,19}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class DossierStoreError(ValueError):
    """Raised when an immutable record or store invariant is violated."""


def _safe_ticker(ticker: str) -> str:
    if not isinstance(ticker, str) or not _SAFE_TICKER.fullmatch(ticker):
        raise DossierStoreError("invalid ticker path name")
    if ticker in {".", ".."}:
        raise DossierStoreError("invalid ticker path name")
    return ticker


def _safe_name(value: str, kind: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise DossierStoreError(f"invalid {kind} path name")
    return value


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DossierStoreError(f"{name} must be timezone-aware")
    return value


def _finite(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DossierStoreError(f"{name} must be a finite Decimal")
    return value


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DossierStoreError(f"invalid {name} SHA-256")


def _record_hash(body: dict) -> str:
    return _sha256_bytes(canonical_json(body))


class ForecastRecord(BaseModel):
    """One immutable forecast vintage in raw source units."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False
    )

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    dossier_id: str = Field(min_length=1, max_length=128)
    ticker: str
    metric: str = Field(min_length=1, max_length=128)
    basis: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=64)
    value: Decimal
    scale: Decimal = Field(gt=0)
    period_start: date | None = None
    period_end: date
    evidence_cutoff: AwareDatetime
    forecast_at: AwareDatetime

    @model_validator(mode="after")
    def valid_record(self):
        _safe_name(self.id, "forecast ID")
        _safe_name(self.dossier_id, "dossier ID")
        _safe_ticker(self.ticker)
        _finite(self.value, "forecast value")
        _finite(self.scale, "forecast scale")
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("forecast period ends before it starts")
        return self

    @property
    def normalized_value(self) -> Decimal:
        return self.value * self.scale


class ReportedActual(BaseModel):
    """A published actual offered for comparison with one forecast vintage."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False
    )

    schema_version: Literal[1] = 1
    source_id: Identifier
    ticker: str
    metric: str = Field(min_length=1, max_length=128)
    basis: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=64)
    value: Decimal
    scale: Decimal = Field(gt=0)
    period_start: date | None = None
    period_end: date
    published_at: AwareDatetime

    @model_validator(mode="after")
    def valid_record(self):
        _safe_ticker(self.ticker)
        _finite(self.value, "actual value")
        _finite(self.scale, "actual scale")
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("actual period ends before it starts")
        return self

    @property
    def normalized_value(self) -> Decimal:
        return self.value * self.scale


class ForecastScore(BaseModel):
    """Deterministic accounting forecast error; never a market-return measure."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False
    )

    schema_version: Literal[1] = 1
    forecast_id: str
    actual_source_id: str
    ticker: str
    metric: str
    basis: str
    unit: str
    period_start: date | None
    period_end: date
    forecast_value: Decimal
    actual_value: Decimal
    signed_error: Decimal
    absolute_error: Decimal
    percentage_error: Decimal | None
    absolute_percentage_error: Decimal | None
    evaluated_at: AwareDatetime
    convention: Literal["forecast_minus_actual_actual_denominator"] = (
        "forecast_minus_actual_actual_denominator"
    )


class DossierSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dossier_id: str
    dossier_hash: str
    stored: bool
    promoted: bool
    accepted_pointer_id: str | None


class ForecastSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast_id: str
    forecast_hash: str
    stored: bool


class DossierStore:
    """Append-only filesystem store with a non-authoritative accepted pointer."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _ensure_directory(self, path: Path) -> None:
        if path.is_symlink():
            raise DossierStoreError("dossier store paths cannot be symlinks")
        path.mkdir(parents=False, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise DossierStoreError("invalid dossier store directory")

    def _ticker_directory(self, ticker: str, *, create: bool) -> Path:
        ticker = _safe_ticker(ticker)
        if self.root.is_symlink():
            raise DossierStoreError("dossier store root cannot be a symlink")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise DossierStoreError("invalid dossier store root")
        directory = self.root / "tickers"
        ticker_directory = directory / ticker
        if create:
            self._ensure_directory(directory)
            self._ensure_directory(ticker_directory)
        for candidate in (directory, ticker_directory):
            if candidate.is_symlink():
                raise DossierStoreError("dossier store paths cannot be symlinks")
            if candidate.exists() and not candidate.is_dir():
                raise DossierStoreError("invalid dossier store directory")
        return ticker_directory

    def _collection_directory(self, ticker: str, collection: str, *, create: bool) -> Path:
        ticker_directory = self._ticker_directory(ticker, create=create)
        directory = ticker_directory / collection
        if create:
            self._ensure_directory(directory)
        if directory.is_symlink():
            raise DossierStoreError("dossier collection cannot be a symlink")
        if directory.exists() and not directory.is_dir():
            raise DossierStoreError("invalid dossier collection")
        return directory

    @contextmanager
    def ticker_lock(self, ticker: str) -> Iterator[Path]:
        """Acquire a non-blocking, process-safe lock scoped to one ticker."""

        ticker_directory = self._ticker_directory(ticker, create=True)
        lock_path = ticker_directory / ".dossier.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise DossierStoreError("invalid dossier lock path") from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DossierStoreError("ticker dossier store is in use") from exc
            yield lock_path
        finally:
            os.close(descriptor)

    def _dossier_path(self, ticker: str, dossier_id: str, *, create: bool) -> Path:
        dossier_id = _safe_name(dossier_id, "dossier ID")
        return self._collection_directory(ticker, "dossiers", create=create) / f"{dossier_id}.json"

    def _forecast_path(self, ticker: str, forecast_id: str, *, create: bool) -> Path:
        forecast_id = _safe_name(forecast_id, "forecast ID")
        return (
            self._collection_directory(ticker, "forecasts", create=create) / f"{forecast_id}.json"
        )

    def _pointer_path(self, ticker: str, *, create: bool) -> Path:
        return self._ticker_directory(ticker, create=create) / "accepted.json"

    @staticmethod
    def _read_canonical(path: Path) -> dict:
        if path.is_symlink():
            raise DossierStoreError("immutable record cannot be a symlink")
        try:
            content = read_bytes(path, max_bytes=MAX_RECORD_BYTES)
            value = parse_json(content)
        except (OSError, UnicodeError, ValueError) as exc:
            raise DossierStoreError("invalid immutable record JSON") from exc
        if not isinstance(value, dict) or content != canonical_json(value):
            raise DossierStoreError("immutable record is not canonical")
        return value

    @staticmethod
    def _validate_dossier(dossier: Dossier, evidence_available_at: datetime | None) -> None:
        _safe_name(dossier.id, "dossier ID")
        _safe_ticker(dossier.ticker)
        _aware(dossier.cutoff, "dossier cutoff")
        _aware(dossier.created_at, "dossier created_at")
        if dossier.created_at < dossier.cutoff:
            raise DossierStoreError("dossier cannot be created before its cutoff")
        if evidence_available_at is None:
            raise DossierStoreError("evidence availability is unknown")
        _aware(evidence_available_at, "evidence_available_at")
        if evidence_available_at > dossier.cutoff:
            raise DossierStoreError("future evidence cannot enter a historical dossier")
        _validate_hash(dossier.evidence_hash, "evidence")
        for name, value in dossier.artifact_hashes.items():
            _safe_name(name, "artifact")
            _validate_hash(value, f"artifact {name}")

    @staticmethod
    def _dossier_record(dossier: Dossier, evidence_available_at: datetime) -> dict:
        payload = dossier.model_dump(mode="json")
        body = {
            "schema_version": DOSSIER_SCHEMA_VERSION,
            "dossier": payload,
            "dossier_hash": _record_hash(payload),
            "evidence_available_at": evidence_available_at.isoformat(),
        }
        return {**body, "record_hash": _record_hash(body)}

    def _load_dossier_path(self, path: Path) -> tuple[Dossier, str, datetime]:
        record = self._read_canonical(path)
        required = {
            "schema_version",
            "dossier",
            "dossier_hash",
            "evidence_available_at",
            "record_hash",
        }
        if set(record) != required or record.get("schema_version") != DOSSIER_SCHEMA_VERSION:
            raise DossierStoreError("invalid dossier record shape")
        body = {key: value for key, value in record.items() if key != "record_hash"}
        if record["record_hash"] != _record_hash(body):
            raise DossierStoreError("dossier record hash mismatch")
        if not isinstance(record["dossier"], dict):
            raise DossierStoreError("invalid dossier payload")
        if record["dossier_hash"] != _record_hash(record["dossier"]):
            raise DossierStoreError("dossier content hash mismatch")
        try:
            dossier = Dossier.model_validate(record["dossier"])
            evidence_available_at = datetime.fromisoformat(record["evidence_available_at"])
        except (TypeError, ValueError) as exc:
            raise DossierStoreError("invalid dossier payload") from exc
        self._validate_dossier(dossier, evidence_available_at)
        expected_name = f"{_safe_name(dossier.id, 'dossier ID')}.json"
        if path.name != expected_name:
            raise DossierStoreError("dossier ID does not match its file name")
        return dossier, record["dossier_hash"], evidence_available_at

    def _load_pointer(self, ticker: str) -> dict | None:
        path = self._pointer_path(ticker, create=False)
        if path.is_symlink():
            raise DossierStoreError("accepted pointer cannot be a symlink")
        if not path.exists():
            return None
        pointer = self._read_canonical(path)
        required = {"schema_version", "ticker", "dossier_id", "cutoff", "dossier_hash"}
        if set(pointer) != required or pointer.get("schema_version") != DOSSIER_SCHEMA_VERSION:
            raise DossierStoreError("invalid accepted dossier pointer")
        if pointer.get("ticker") != ticker:
            raise DossierStoreError("accepted pointer ticker mismatch")
        _safe_name(pointer.get("dossier_id"), "dossier ID")
        _validate_hash(pointer.get("dossier_hash"), "pointer dossier")
        try:
            cutoff = datetime.fromisoformat(pointer["cutoff"])
        except (TypeError, ValueError) as exc:
            raise DossierStoreError("invalid accepted pointer cutoff") from exc
        _aware(cutoff, "accepted pointer cutoff")
        return pointer

    def save_dossier(
        self, dossier: Dossier, *, evidence_available_at: datetime | None
    ) -> DossierSaveResult:
        """Append a dossier and promote only a strictly newer accepted cutoff."""

        self._validate_dossier(dossier, evidence_available_at)
        assert evidence_available_at is not None  # narrowed by validation
        record = self._dossier_record(dossier, evidence_available_at)
        content = canonical_json(record)
        dossier_hash = record["dossier_hash"]
        stored = False
        promoted = False
        with self.ticker_lock(dossier.ticker):
            path = self._dossier_path(dossier.ticker, dossier.id, create=True)
            if path.is_symlink():
                raise DossierStoreError("immutable dossier cannot be a symlink")
            if path.exists():
                existing_dossier, existing_hash, existing_availability = self._load_dossier_path(
                    path
                )
                if (
                    existing_hash != dossier_hash
                    or existing_dossier != dossier
                    or existing_availability != evidence_available_at
                ):
                    raise DossierStoreError("dossier ID already exists with different content")
            else:
                atomic_write(path, content)
                stored = True

            pointer = self._load_pointer(dossier.ticker)
            accepted_pointer_id = pointer["dossier_id"] if pointer else None
            if dossier.assessment.status == "accepted":
                current_cutoff = datetime.fromisoformat(pointer["cutoff"]) if pointer else None
                if pointer is None or dossier.cutoff > current_cutoff:
                    pointer_value = {
                        "schema_version": DOSSIER_SCHEMA_VERSION,
                        "ticker": dossier.ticker,
                        "dossier_id": dossier.id,
                        "cutoff": dossier.cutoff.isoformat(),
                        "dossier_hash": dossier_hash,
                    }
                    pointer_path = self._pointer_path(dossier.ticker, create=True)
                    if pointer_path.is_symlink():
                        raise DossierStoreError("accepted pointer cannot be a symlink")
                    atomic_write(pointer_path, canonical_json(pointer_value))
                    promoted = True
                    accepted_pointer_id = dossier.id
            return DossierSaveResult(
                dossier_id=dossier.id,
                dossier_hash=dossier_hash,
                stored=stored,
                promoted=promoted,
                accepted_pointer_id=accepted_pointer_id,
            )

    def load_dossier(self, ticker: str, dossier_id: str) -> Dossier:
        path = self._dossier_path(ticker, dossier_id, create=False)
        if not path.exists():
            raise DossierStoreError("dossier does not exist")
        dossier, _, _ = self._load_dossier_path(path)
        if dossier.ticker != ticker:
            raise DossierStoreError("dossier ticker mismatch")
        return dossier

    def load_current_accepted(self, ticker: str) -> Dossier | None:
        ticker = _safe_ticker(ticker)
        pointer = self._load_pointer(ticker)
        if pointer is None:
            return None
        dossier = self.load_dossier(ticker, pointer["dossier_id"])
        if dossier.assessment.status != "accepted":
            raise DossierStoreError("accepted pointer targets a degraded dossier")
        if dossier.cutoff.isoformat() != pointer["cutoff"]:
            raise DossierStoreError("accepted pointer cutoff mismatch")
        payload_hash = _record_hash(dossier.model_dump(mode="json"))
        if payload_hash != pointer["dossier_hash"]:
            raise DossierStoreError("accepted pointer hash mismatch")
        return dossier

    def load_eligible(self, ticker: str, cutoff: datetime) -> Dossier | None:
        """Load the newest validated accepted dossier no later than ``cutoff``."""

        ticker = _safe_ticker(ticker)
        cutoff = _aware(cutoff, "eligibility cutoff")
        directory = self._collection_directory(ticker, "dossiers", create=False)
        if not directory.exists():
            return None
        eligible: list[Dossier] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file():
                raise DossierStoreError("invalid entry in immutable dossier collection")
            dossier, _, _ = self._load_dossier_path(path)
            if dossier.ticker != ticker:
                raise DossierStoreError("dossier ticker mismatch")
            if (
                dossier.assessment.status == "accepted"
                and dossier.cutoff <= cutoff
                and dossier.created_at <= cutoff
            ):
                eligible.append(dossier)
        if not eligible:
            return None
        return max(eligible, key=lambda item: (item.cutoff, item.created_at, item.id))

    @staticmethod
    def _forecast_record(forecast: ForecastRecord) -> dict:
        payload = forecast.model_dump(mode="json")
        body = {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "forecast": payload,
            "forecast_hash": _record_hash(payload),
        }
        return {**body, "record_hash": _record_hash(body)}

    def _load_forecast_path(self, path: Path) -> tuple[ForecastRecord, str]:
        record = self._read_canonical(path)
        required = {"schema_version", "forecast", "forecast_hash", "record_hash"}
        if set(record) != required or record.get("schema_version") != FORECAST_SCHEMA_VERSION:
            raise DossierStoreError("invalid forecast record shape")
        body = {key: value for key, value in record.items() if key != "record_hash"}
        if record["record_hash"] != _record_hash(body):
            raise DossierStoreError("forecast record hash mismatch")
        if not isinstance(record["forecast"], dict):
            raise DossierStoreError("invalid forecast payload")
        if record["forecast_hash"] != _record_hash(record["forecast"]):
            raise DossierStoreError("forecast content hash mismatch")
        try:
            forecast = ForecastRecord.model_validate(record["forecast"])
        except (TypeError, ValueError) as exc:
            raise DossierStoreError("invalid forecast payload") from exc
        if path.name != f"{forecast.id}.json":
            raise DossierStoreError("forecast ID does not match its file name")
        return forecast, record["forecast_hash"]

    def save_forecast(self, forecast: ForecastRecord) -> ForecastSaveResult:
        """Append an immutable forecast vintage without changing any model."""

        record = self._forecast_record(forecast)
        content = canonical_json(record)
        stored = False
        with self.ticker_lock(forecast.ticker):
            dossier = self.load_dossier(forecast.ticker, forecast.dossier_id)
            if dossier.assessment.status != "accepted":
                raise DossierStoreError("forecast vintage requires an accepted dossier")
            if dossier.cutoff != forecast.evidence_cutoff:
                raise DossierStoreError("forecast evidence cutoff must match its dossier cutoff")
            if forecast.forecast_at < dossier.created_at:
                raise DossierStoreError("forecast vintage cannot predate dossier creation")
            path = self._forecast_path(forecast.ticker, forecast.id, create=True)
            if path.is_symlink():
                raise DossierStoreError("immutable forecast cannot be a symlink")
            if path.exists():
                existing, existing_hash = self._load_forecast_path(path)
                if existing != forecast or existing_hash != record["forecast_hash"]:
                    raise DossierStoreError("forecast ID already exists with different content")
            else:
                atomic_write(path, content)
                stored = True
        return ForecastSaveResult(
            forecast_id=forecast.id,
            forecast_hash=record["forecast_hash"],
            stored=stored,
        )

    def load_forecast(self, ticker: str, forecast_id: str) -> ForecastRecord:
        path = self._forecast_path(ticker, forecast_id, create=False)
        if not path.exists():
            raise DossierStoreError("forecast does not exist")
        forecast, _ = self._load_forecast_path(path)
        if forecast.ticker != ticker:
            raise DossierStoreError("forecast ticker mismatch")
        return forecast


def score_forecast(
    forecast: ForecastRecord,
    actual: ReportedActual,
    *,
    evaluation_cutoff: datetime,
) -> ForecastScore:
    """Compare exactly matched accounting values without mutating the forecast."""

    evaluation_cutoff = _aware(evaluation_cutoff, "evaluation_cutoff")
    dimensions = (
        (forecast.ticker, actual.ticker, "ticker"),
        (forecast.metric, actual.metric, "metric"),
        (forecast.basis, actual.basis, "reported basis"),
        (forecast.unit, actual.unit, "unit"),
        (forecast.scale, actual.scale, "scale"),
        (forecast.period_start, actual.period_start, "period start"),
        (forecast.period_end, actual.period_end, "period end"),
    )
    for forecast_value, actual_value, name in dimensions:
        if forecast_value != actual_value:
            raise DossierStoreError(f"forecast and actual {name} mismatch")
    if actual.published_at <= forecast.forecast_at:
        raise DossierStoreError("actual publication must be after the forecast vintage")
    if actual.published_at > evaluation_cutoff:
        raise DossierStoreError("actual publication is after the evaluation cutoff")

    with localcontext() as context:
        context.prec = 40
        forecast_value = forecast.normalized_value
        actual_value = actual.normalized_value
        signed_error = forecast_value - actual_value
        absolute_error = abs(signed_error)
        if actual_value == 0:
            percentage_error = None
            absolute_percentage_error = None
        else:
            percentage_error = signed_error / abs(actual_value)
            absolute_percentage_error = abs(percentage_error)
    return ForecastScore(
        forecast_id=forecast.id,
        actual_source_id=actual.source_id,
        ticker=forecast.ticker,
        metric=forecast.metric,
        basis=forecast.basis,
        unit=forecast.unit,
        period_start=forecast.period_start,
        period_end=forecast.period_end,
        forecast_value=forecast_value,
        actual_value=actual_value,
        signed_error=signed_error,
        absolute_error=absolute_error,
        percentage_error=percentage_error,
        absolute_percentage_error=absolute_percentage_error,
        evaluated_at=evaluation_cutoff,
    )
