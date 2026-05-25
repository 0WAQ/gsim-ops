"""Three-way merge for the three synced state files.

Each file holds a dict of per-factor records; we pick the entry with the
newest `updated_at` from either side. Tie → keep local (cheaper).

Files merged here:
- factor_state.json       (root is {name: FactorRecord}, top-level keys)
- metrics.json            (records nested under "metrics" key)
- datasources.json        (records nested under "datasources" key)

The factor_state.json case must be performed while holding the
JsonStateStore lock — see `merge_factor_state`.
"""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from ops.infra.store.json_store import JsonStateStore


EPOCH = "1970-01-01T00:00:00"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pick_newer(local: dict, remote: dict, *, key: str = "updated_at"
                ) -> dict[str, dict]:
    """Per-key dict merge — entry with newer `updated_at` wins. Tie → local."""
    out = dict(local)
    for name, rval in remote.items():
        lval = out.get(name)
        if lval is None:
            out[name] = rval
            continue
        l_ts = lval.get(key) or EPOCH
        r_ts = rval.get(key) or EPOCH
        if r_ts > l_ts:
            out[name] = rval
    return out


# ───────────────────────── per-file merges ──────────────────────────────

def merge_factor_state(local_path: Path, remote_path: Path) -> tuple[int, int]:
    """Merge factor_state.json (root-level dict).

    Acquires the JsonStateStore lock around the read-merge-write so a
    concurrent `ops check` finishing on this machine doesn't race us.
    Returns (added_from_remote, updated_from_remote).
    """
    remote = _read_json(remote_path) or {}
    if not isinstance(remote, dict):
        return (0, 0)

    store = JsonStateStore(local_path)
    added = 0
    updated = 0
    with store._locked():
        local = _read_json(local_path) or {}
        if not isinstance(local, dict):
            local = {}
        for name, rval in remote.items():
            lval = local.get(name)
            if lval is None:
                added += 1
                local[name] = rval
                continue
            l_ts = (lval.get("updated_at") if isinstance(lval, dict) else None) or EPOCH
            r_ts = (rval.get("updated_at") if isinstance(rval, dict) else None) or EPOCH
            if r_ts > l_ts:
                updated += 1
                local[name] = rval
        _atomic_write_json(local_path, local)
    return added, updated


def _merge_nested(local_path: Path, remote_path: Path, *,
                  records_key: str, version: int) -> tuple[int, int]:
    """Merge metrics.json or datasources.json shape:
    {version, created_at, <records_key>: {name: {...}}}"""
    remote = _read_json(remote_path) or {}
    remote_records = (remote.get(records_key) or {}) if isinstance(remote, dict) else {}
    if not isinstance(remote_records, dict):
        remote_records = {}

    local = _read_json(local_path) or {
        "version": version,
        "created_at": datetime.now().timestamp(),
        records_key: {},
    }
    local_records = local.get(records_key) or {}
    if not isinstance(local_records, dict):
        local_records = {}

    added = sum(1 for n in remote_records if n not in local_records)
    merged = _pick_newer(local_records, remote_records)
    updated = sum(
        1 for n, v in remote_records.items()
        if n in local_records and merged[n] is v
    )

    local[records_key] = merged
    local["version"] = version
    local["created_at"] = datetime.now().timestamp()
    _atomic_write_json(local_path, local)
    return added, updated


def merge_metrics(local_path: Path, remote_path: Path) -> tuple[int, int]:
    from ops.services.list.metrics import METRICS_VERSION
    return _merge_nested(local_path, remote_path,
                         records_key="metrics", version=METRICS_VERSION)


def merge_datasources(local_path: Path, remote_path: Path) -> tuple[int, int]:
    from ops.services.list.datasource import DATASOURCES_VERSION
    return _merge_nested(local_path, remote_path,
                         records_key="datasources", version=DATASOURCES_VERSION)


MERGERS: dict[str, Callable[[Path, Path], tuple[int, int]]] = {
    "factor_state.json": merge_factor_state,
    "metrics.json":      merge_metrics,
    "datasources.json":  merge_datasources,
}
