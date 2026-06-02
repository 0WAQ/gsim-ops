"""Local etag cache for sync diff.

Sync uses S3 etag (the same value boto3's TransferConfig produces) as the
authoritative identity for "are these two files the same content". The
remote etag is free from `list_objects`; the local etag is not — computing
it requires reading the file. For ~482GB of alpha_feature that's ~16min
per sync at 500MB/s.

This cache amortizes that cost: keyed by (rel, mtime, size), it returns a
previously-computed etag when the file hasn't visibly changed since the
last walk. A `touch` invalidates (mtime changed), as does any in-place
rewrite (size or mtime changed). Same-mtime+same-size+different-content
is the cache's one blind spot; `--deep` (recompute=True at walk time)
bypasses the cache for that case.

Cache file: ~/.cache/ops/lib/<library_id>/local_etag_cache.json
Schema: { "<subdir>/<rel>": { mtime, size, etag } }
"""
import json
import os
import tempfile
from pathlib import Path

from ops.infra.cache import cache_path


CACHE_FILENAME = "local_etag_cache.json"


def _key(subdir: str, rel: str) -> str:
    return f"{subdir}/{rel}"


def load(library_id: str) -> dict:
    """Read cache JSON. Returns {} on missing/corrupt."""
    p = cache_path(library_id, CACHE_FILENAME)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def lookup(cache: dict, subdir: str, rel: str,
           mtime: float, size: int,
           tolerance: float = 1e-3) -> str | None:
    """Return cached etag if (mtime, size) match within tolerance; else None.

    Sub-millisecond mtime tolerance absorbs the float round-trip through
    JSON without letting a real edit slip through.
    """
    entry = cache.get(_key(subdir, rel))
    if not isinstance(entry, dict):
        return None
    if entry.get("size") != size:
        return None
    cm = entry.get("mtime")
    if not isinstance(cm, (int, float)):
        return None
    if abs(cm - mtime) > tolerance:
        return None
    etag = entry.get("etag")
    return etag if isinstance(etag, str) and etag else None


def put(cache: dict, subdir: str, rel: str,
        mtime: float, size: int, etag: str) -> None:
    cache[_key(subdir, rel)] = {"mtime": mtime, "size": size, "etag": etag}


def prune(cache: dict, subdir: str, present_rels: set[str]) -> int:
    """Drop entries under subdir whose rel isn't in present_rels.

    Other subdirs' entries are left untouched — push/pull/verify each
    only know about one subdir at a time. Returns count removed.
    """
    prefix = f"{subdir}/"
    present_keys = {f"{prefix}{r}" for r in present_rels}
    stale = [k for k in cache
             if k.startswith(prefix) and k not in present_keys]
    for k in stale:
        cache.pop(k, None)
    return len(stale)


def save(library_id: str, cache: dict) -> None:
    """Atomic write — tmp file + os.replace."""
    p = cache_path(library_id, CACHE_FILENAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent),
                               prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
