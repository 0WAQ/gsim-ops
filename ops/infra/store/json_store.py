import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ops.core.state import CheckRecord, FactorRecord, FactorStatus

from .base import StateStore

STALE_TMP_AGE_SECONDS = 3600


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonStateStore(StateStore):
    """JSON-backed store. Single fcntl lock over the full read-modify-write window."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")
        if not self.lock_path.exists():
            self.lock_path.touch()

    def _cleanup_stale_tmp(self) -> None:
        """Remove orphan .tmp files older than STALE_TMP_AGE_SECONDS.

        Must be called while holding the lock — otherwise we may delete a tmp
        file another process just created and is about to os.replace().
        """
        cutoff = time.time() - STALE_TMP_AGE_SECONDS
        for p in self.path.parent.glob(f".{self.path.name}.*.tmp"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass

    @contextmanager
    def _locked(self):
        with self.lock_path.open("r+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                self._cleanup_stale_tmp()
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_records(self) -> dict[str, FactorRecord]:
        raw = self.path.read_text() or "{}"
        data = json.loads(raw)
        return {k: FactorRecord.from_dict(v) for k, v in data.items()}

    def _atomic_write(self, records: dict[str, FactorRecord]) -> None:
        payload = {k: v.to_dict() for k, v in records.items()}
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def get(self, name: str) -> FactorRecord | None:
        with self._locked():
            return self._read_records().get(name)

    def put(self, record: FactorRecord) -> None:
        with self._locked():
            record.updated_at = _now()
            records = self._read_records()
            records[record.name] = record
            self._atomic_write(records)

    def list(self, status: FactorStatus | None = None) -> list[FactorRecord]:
        # author 过滤已删:FactorRecord 无 author 字段,原实现 r.author 直接
        # AttributeError(坏回退的一部分,full-review P0-2)。author 走 InfoStore。
        with self._locked():
            out = list(self._read_records().values())
        if status is not None:
            out = [r for r in out if r.status == status]
        return out

    def transition(self, name: str, to_status: FactorStatus, **updates) -> FactorRecord:
        with self._locked():
            records = self._read_records()
            rec = records.get(name)
            if rec is None:
                raise KeyError(f"factor not found: {name}")
            rec.status = to_status
            for k, v in updates.items():
                setattr(rec, k, v)
            rec.updated_at = _now()
            records[name] = rec
            self._atomic_write(records)
            return rec

    def append_check(self, name: str, check: CheckRecord) -> None:
        with self._locked():
            records = self._read_records()
            rec = records.get(name)
            if rec is None:
                raise KeyError(f"factor not found: {name}")
            rec.check_history.append(check)
            rec.updated_at = _now()
            records[name] = rec
            self._atomic_write(records)

    def delete(self, name: str) -> bool:
        with self._locked():
            records = self._read_records()
            if name not in records:
                return False
            del records[name]
            self._atomic_write(records)
            return True
