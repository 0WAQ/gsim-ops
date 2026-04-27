import json
import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ops.core.state import FactorRecord, FactorStatus, CheckRecord
from .base import StateStore


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonStateStore(StateStore):
    """JSON-backed store. One file, fcntl-locked during read-modify-write."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")

    @contextmanager
    def _locked(self, mode: str):
        with self.path.open(mode) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield f
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _load_all(self) -> dict[str, FactorRecord]:
        with self._locked("r") as f:
            raw = json.loads(f.read() or "{}")
        return {k: FactorRecord.from_dict(v) for k, v in raw.items()}

    def _save_all(self, records: dict[str, FactorRecord]) -> None:
        payload = {k: v.to_dict() for k, v in records.items()}
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        tmp.replace(self.path)

    def get(self, name: str) -> FactorRecord | None:
        return self._load_all().get(name)

    def put(self, record: FactorRecord) -> None:
        record.updated_at = _now()
        records = self._load_all()
        records[record.name] = record
        self._save_all(records)

    def list(self,
             author: str | None = None,
             status: FactorStatus | None = None) -> list[FactorRecord]:
        out = list(self._load_all().values())
        if author is not None:
            out = [r for r in out if r.author == author]
        if status is not None:
            out = [r for r in out if r.status == status]
        return out

    def transition(self, name: str, to_status: FactorStatus, **updates) -> FactorRecord:
        records = self._load_all()
        rec = records.get(name)
        if rec is None:
            raise KeyError(f"factor not found: {name}")
        rec.status = to_status
        for k, v in updates.items():
            setattr(rec, k, v)
        rec.updated_at = _now()
        records[name] = rec
        self._save_all(records)
        return rec

    def append_check(self, name: str, check: CheckRecord) -> None:
        records = self._load_all()
        rec = records.get(name)
        if rec is None:
            raise KeyError(f"factor not found: {name}")
        rec.check_history.append(check)
        rec.updated_at = _now()
        records[name] = rec
        self._save_all(records)
