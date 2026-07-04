"""JSON 派生层后端 (回退/单机).

单文件 ~/.cache/ops/lib/<lib>/derived.json,DerivedRecord 形态。作为 postgres
的回退 (dev / 无 PG 环境 / config.prod-legacy)。不读旧的 metrics.json /
datasources.json / bcorr.json —— 那些由 ops/tools/derived_migrate.py 一次性灌进来,
或直接 `ops list --refresh` 从 JFS 重建。

读-改-写在 fcntl 锁下,tempfile + os.replace 原子落盘,防并发写互相覆盖。
"""
import fcntl
import fnmatch
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import DerivedStore, DerivedRecord

DERIVED_VERSION = 1


class JsonDerivedStore(DerivedStore):
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != DERIVED_VERSION:
                return {}
            return data.get("records", {})
        except Exception:
            return {}

    def _read_meta(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != DERIVED_VERSION:
                return {}
            return data.get("meta", {})
        except Exception:
            return {}

    def _write(self, records: dict[str, dict[str, Any]], meta: dict[str, str] | None = None) -> None:
        # Preserve meta across record writes (and vice versa) — both live in one file.
        if meta is None:
            meta = self._read_meta()
        data = {"version": DERIVED_VERSION, "records": records, "meta": meta}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get_all(
        self,
        author: str | None = None,
        *,
        field: str | None = None,
        table_glob: str | None = None,
    ) -> dict[str, DerivedRecord]:
        records = self._read()
        out: dict[str, DerivedRecord] = {}
        for name, d in records.items():
            if author is not None and d.get("author") != author:
                continue
            if field is not None and field not in (d.get("fields") or []):
                continue
            if table_glob is not None and not any(
                fnmatch.fnmatch(t, table_glob) for t in (d.get("tables") or [])
            ):
                continue
            out[name] = DerivedRecord(**d)
        return out

    def get(self, name: str) -> DerivedRecord | None:
        d = self._read().get(name)
        return DerivedRecord(**d) if d else None

    def _merge(self, name: str, updates: dict[str, Any]) -> None:
        with self._locked():
            records = self._read()
            cur = records.get(name) or {"name": name}
            cur.update(updates)
            cur["name"] = name
            records[name] = cur
            self._write(records)

    def upsert_index(self, entries: dict[str, dict[str, Any]]) -> None:
        if not entries:
            return
        with self._locked():
            records = self._read()
            for name, e in entries.items():
                cur = records.get(name) or {"name": name}
                cur.update({
                    "name": name,
                    "author": e.get("author"),
                    "has_pnl": e.get("has_pnl"),
                    "dump_days": e.get("dump_days"),
                    "delay": e.get("delay"),
                })
                records[name] = cur
            self._write(records)

    def upsert_metrics(self, name: str, m: dict[str, Any]) -> None:
        self._merge(name, {
            "ret": m.get("ret"), "shrp": m.get("shrp"), "mdd": m.get("mdd"),
            "tvr": m.get("tvr"), "fitness": m.get("fitness"),
        })

    def upsert_datasources(self, name: str, fields: list[str], tables: list[str]) -> None:
        self._merge(name, {"fields": fields, "tables": tables})

    def upsert_bcorr(self, name: str, max_bcorr: float, max_bcorr_factor: str) -> None:
        self._merge(name, {"max_bcorr": max_bcorr, "max_bcorr_factor": max_bcorr_factor})

    def delete(self, name: str) -> bool:
        with self._locked():
            records = self._read()
            existed = name in records
            if existed:
                del records[name]
                self._write(records)
            return existed

    def get_meta(self, key: str) -> str | None:
        return self._read_meta().get(key)

    def set_meta(self, key: str, value: str) -> None:
        with self._locked():
            records = self._read()
            meta = self._read_meta()
            meta[key] = value
            self._write(records, meta)
