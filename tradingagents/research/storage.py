"""Atomic local artifacts and content-bound checkpoints; no application imports."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from .contracts import ResearchRequest

ENGINE_VERSION = "research-v2-m0"


def _encode(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Path, Decimal)):
        return str(value)
    raise TypeError(f"unsupported artifact value: {type(value).__name__}")


def canonical_json(value) -> bytes:
    return json.dumps(value, default=_encode, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def read_json(path: Path, *, max_bytes: int = 32 * 1024 * 1024):
    if path.stat().st_size > max_bytes:
        raise ValueError("artifact exceeds size allowance")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON constant")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def request_identity(request: ResearchRequest) -> str:
    settings = request.model_dump(mode="json", exclude={"output_dir", "dossier_dir"})
    for name in ("evidence_path", "prior_dossier_path"):
        path = getattr(request, name)
        settings[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
    return digest({"engine": ENGINE_VERSION, "settings": settings})


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".research-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class CheckpointStore:
    def __init__(self, directory: Path, identity: str):
        self.directory = directory
        self.identity = identity
        self._locked = False

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / ".research.lock"
        # Do not follow a planted lock symlink.
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("research destination is in use") from exc
            self._locked = True
            manifest_path = self.directory / "research_checkpoint.json"
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                if manifest != {"schema_version": 1, "identity": self.identity}:
                    raise ValueError("incompatible research checkpoint")
            else:
                if set(self.directory.iterdir()) - {lock_path}:
                    raise ValueError("research destination is not empty")
                atomic_write(manifest_path, canonical_json({"schema_version": 1,
                                                           "identity": self.identity}))
            yield self
        finally:
            self._locked = False
            os.close(fd)

    def _stage_path(self, stage: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", stage):
            raise ValueError("invalid checkpoint stage")
        return self.directory / "stages" / f"{stage}.json"

    def save_stage(self, stage: str, inputs, output) -> None:
        if not self._locked:
            raise ValueError("checkpoint writes require the run lock")
        record = {"schema_version": 1, "identity": self.identity,
                  "inputs_hash": digest(inputs), "output": output,
                  "output_hash": digest(output)}
        atomic_write(self._stage_path(stage), canonical_json(record))

    def load_stage(self, stage: str, inputs):
        path = self._stage_path(stage)
        if not path.exists():
            return None
        record = read_json(path)
        if (record.get("schema_version") != 1 or record.get("identity") != self.identity
                or record.get("output_hash") != digest(record.get("output"))):
            raise ValueError("invalid research stage checkpoint")
        if record.get("inputs_hash") != digest(inputs):
            return None
        return record["output"]
