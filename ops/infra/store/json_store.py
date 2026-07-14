import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ops.core.state import CheckRecord, FactorRecord, FactorStatus, HistoryEvent
from ops.infra.errors import FactorNotFound

from .base import StateConflict, StateStore

STALE_TMP_AGE_SECONDS = 3600


from ops.utils.clock import now_iso as _now  # 单一真相源,见 utils/clock.py


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

    def _read_raw(self) -> dict[str, dict]:
        """原始 dict 形态。check_history 键由 store 管理(FactorRecord
        已剥离该字段,from_dict 会丢弃它 —— 写回时必须从 raw 保留)。"""
        return json.loads(self.path.read_text() or "{}")

    def _read_records(self) -> dict[str, FactorRecord]:
        return {k: FactorRecord.from_dict(v) for k, v in self._read_raw().items()}

    def _atomic_write(self, payload: dict[str, dict]) -> None:
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
            raw = self._read_raw()
            d = record.to_dict()
            d["check_history"] = raw.get(record.name, {}).get("check_history", [])
            raw[record.name] = d
            self._atomic_write(raw)

    def list(self, status: FactorStatus | None = None) -> list[FactorRecord]:
        # author 过滤已删:FactorRecord 无 author 字段,r.author 直接
        # AttributeError。author 走 InfoStore。
        with self._locked():
            out = list(self._read_records().values())
        if status is not None:
            out = [r for r in out if r.status == status]
        return out

    def transition(self, name: str, to_status: FactorStatus,
                   expect: FactorStatus | None = None,
                   op: str | None = None, actor: str | None = None,
                   **updates) -> FactorRecord:
        # op/actor 接受并忽略:dev/test 后端无事件表,审计走 PG
        with self._locked():
            raw = self._read_raw()
            if name not in raw:
                raise FactorNotFound(f"factor not found: {name}")
            rec = FactorRecord.from_dict(raw[name])
            if expect is not None and rec.status != expect:
                raise StateConflict(
                    f"{name}: status={rec.status.value}, expect={expect.value}")
            rec.status = to_status
            for k, v in updates.items():
                setattr(rec, k, v)
            rec.updated_at = _now()
            d = rec.to_dict()
            d["check_history"] = raw[name].get("check_history", [])
            raw[name] = d
            self._atomic_write(raw)
            return rec

    def append_check(self, name: str, check: CheckRecord,
                     actor: str | None = None) -> None:
        with self._locked():
            raw = self._read_raw()
            if name not in raw:
                raise FactorNotFound(f"factor not found: {name}")
            raw[name].setdefault("check_history", []).append(check.to_dict())
            raw[name]["updated_at"] = _now()
            self._atomic_write(raw)

    def delete(self, name: str, op: str | None = None,
               actor: str | None = None) -> bool:
        with self._locked():
            raw = self._read_raw()
            if name not in raw:
                return False
            del raw[name]
            self._atomic_write(raw)
            return True

    def checks(self, name: str) -> "list[CheckRecord]":
        with self._locked():
            raw = self._read_raw()
        return [CheckRecord.from_dict(c)
                for c in raw.get(name, {}).get("check_history", [])]

    def last_fail(self, name: str) -> HistoryEvent | None:
        """从存储的 check 列表扫描合成(dev/test 后端无事件表)——
        与 PG 派生语义一致:最新一条 passed=False 的 check。"""
        for c in reversed(self.checks(name)):
            if c.passed is False:
                return HistoryEvent(
                    name=name, op="check",
                    at=c.finished_at or c.started_at,
                    started_at=c.started_at, passed=False,
                    failed_stage=c.failed_stage, fail_reason=c.fail_reason,
                )
        return None

    def latest_check_ats(self) -> "dict[str, str]":
        with self._locked():
            raw = self._read_raw()
        out = {}
        for name, d in raw.items():
            checks = d.get("check_history", [])
            if checks:
                c = checks[-1]
                out[name] = c.get("finished_at") or c.get("started_at") or ""
        return out

    def history(self, name: str) -> "list[HistoryEvent]":
        """合成 check 事件时间线(无事件表,生命周期 op 缺席)——
        使 status 详情在 dev/test 后端也有时间线可渲染。"""
        return [HistoryEvent(
                    name=name, op="check",
                    at=c.finished_at or c.started_at,
                    started_at=c.started_at, passed=c.passed,
                    failed_stage=c.failed_stage, fail_reason=c.fail_reason)
                for c in self.checks(name)]
