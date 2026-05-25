"""Local sync manifest.

Tracks per-factor "fingerprints" (max mtime, dump latest date + count) so
`ops sync push` can avoid scanning 1.8M dump files: we only stat at the
factor-dir level, diff against the manifest, and feed rclone a
--files-from list.

Layout:
    ~/.cache/ops/lib/<library_id>/sync_manifest.json

A missing manifest forces the caller to pass --bootstrap (which walks
everything once and populates fresh).
"""
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ops.infra.config import Config
from ops.infra.cache import library_cache_dir


MANIFEST_VERSION = 1
MANIFEST_FILENAME = "sync_manifest.json"
FEATURE_VERSIONS = ("v1", "v2")
_DATE_RE = re.compile(r"^\d{8}$")


# ───────────────────────── dataclasses ──────────────────────────────────

@dataclass
class FactorFingerprint:
    src_mtime: float = 0.0
    pnl_mtime: float = 0.0
    dump_latest: Optional[str] = None      # YYYYMMDD
    dump_count: int = 0
    feature_mtime: dict = field(default_factory=dict)  # {"v1": ts, "v2": ts}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactorFingerprint":
        d = dict(d)
        d["feature_mtime"] = dict(d.get("feature_mtime") or {})
        return cls(**d)


@dataclass
class SyncManifest:
    version: int = MANIFEST_VERSION
    updated_at: str = ""
    factors: dict[str, FactorFingerprint] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "factors": {k: v.to_dict() for k, v in self.factors.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SyncManifest":
        return cls(
            version=d.get("version", MANIFEST_VERSION),
            updated_at=d.get("updated_at", ""),
            factors={
                k: FactorFingerprint.from_dict(v)
                for k, v in (d.get("factors") or {}).items()
            },
        )


@dataclass
class ChangeSet:
    alpha_src: set[str] = field(default_factory=set)                # factor names → push whole dir
    alpha_pnl: set[str] = field(default_factory=set)
    alpha_dump: dict[str, list[str]] = field(default_factory=dict)  # name → [YYYYMMDD, ...]
    alpha_feature: dict[str, list[str]] = field(default_factory=dict)  # name → ["v1", "v2"]

    def is_empty(self) -> bool:
        return not (self.alpha_src or self.alpha_pnl
                    or self.alpha_dump or self.alpha_feature)

    def total_factors(self) -> int:
        names: set[str] = set()
        names.update(self.alpha_src)
        names.update(self.alpha_pnl)
        names.update(self.alpha_dump.keys())
        names.update(self.alpha_feature.keys())
        return len(names)


# ───────────────────────── load / save ──────────────────────────────────

def manifest_path(library_id: str) -> Path:
    return library_cache_dir(library_id) / MANIFEST_FILENAME


def load_manifest(library_id: str) -> Optional[SyncManifest]:
    path = manifest_path(library_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get("version") != MANIFEST_VERSION:
        return None
    return SyncManifest.from_dict(data)


def save_manifest(library_id: str, manifest: SyncManifest) -> None:
    manifest.updated_at = datetime.now().isoformat(timespec="seconds")
    path = manifest_path(library_id)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ───────────────────────── stat helpers ─────────────────────────────────

def _max_mtime(root: Path) -> float:
    """Recursive max mtime under root. 0 if root missing / empty."""
    best = 0.0
    if not root.exists():
        return 0.0
    for dirpath, _dirnames, filenames in os.walk(root):
        for n in filenames:
            try:
                m = os.stat(os.path.join(dirpath, n)).st_mtime
                if m > best:
                    best = m
            except OSError:
                pass
    return best


def _dump_summary(dump_dir: Path) -> tuple[Optional[str], int]:
    """Return (latest YYYYMMDD dir name, total .npy count) under one factor's
    dump dir. Layout: <dump_dir>/<YYYYMMDD>/*.npy"""
    if not dump_dir.exists():
        return None, 0
    latest: Optional[str] = None
    total = 0
    with os.scandir(dump_dir) as it:
        for entry in it:
            if not entry.is_dir():
                continue
            name = entry.name
            if not _DATE_RE.match(name):
                continue
            if latest is None or name > latest:
                latest = name
            try:
                with os.scandir(entry.path) as sub:
                    total += sum(1 for e in sub
                                 if e.is_file() and e.name.endswith(".npy"))
            except OSError:
                pass
    return latest, total


def _feature_mtimes(feature_dir: Path) -> dict[str, float]:
    """Per-version max mtime under alpha_feature/<name>/<version>/."""
    out: dict[str, float] = {}
    for v in FEATURE_VERSIONS:
        vdir = feature_dir / v
        if vdir.exists():
            out[v] = _max_mtime(vdir)
    return out


def stat_factor(name: str, config: Config) -> FactorFingerprint:
    src_mtime = _max_mtime(config.alpha_src / name)
    pnl_mtime = _max_mtime(config.alpha_pnl / name)
    dump_latest, dump_count = _dump_summary(config.alpha_dump / name)
    feature = _feature_mtimes(config.alpha_feature / name)
    return FactorFingerprint(
        src_mtime=src_mtime,
        pnl_mtime=pnl_mtime,
        dump_latest=dump_latest,
        dump_count=dump_count,
        feature_mtime=feature,
    )


def list_factor_names(config: Config) -> list[str]:
    """Union of factor names appearing under any data dir."""
    names: set[str] = set()
    for d in (config.alpha_src, config.alpha_pnl,
              config.alpha_dump, config.alpha_feature):
        if not d.exists():
            continue
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_dir() and not entry.name.startswith("."):
                        names.add(entry.name)
        except OSError:
            pass
    return sorted(names)


def _newer_dump_dates(dump_dir: Path, prev_latest: Optional[str]) -> list[str]:
    """List date dirs strictly newer than prev_latest (or all if prev_latest is None)."""
    if not dump_dir.exists():
        return []
    out: list[str] = []
    with os.scandir(dump_dir) as it:
        for entry in it:
            if not entry.is_dir() or not _DATE_RE.match(entry.name):
                continue
            if prev_latest is None or entry.name > prev_latest:
                out.append(entry.name)
    return sorted(out)


# ───────────────────────── scanning ─────────────────────────────────────

def scan_changes(config: Config, manifest: SyncManifest
                 ) -> tuple[ChangeSet, dict[str, FactorFingerprint]]:
    """Walk local data dirs, diff against manifest, return:
    - ChangeSet describing what to push
    - dict of fresh fingerprints (to update the manifest after a successful push)
    """
    changes = ChangeSet()
    fresh: dict[str, FactorFingerprint] = {}

    for name in list_factor_names(config):
        new = stat_factor(name, config)
        fresh[name] = new
        old = manifest.factors.get(name)
        old_src = old.src_mtime if old else 0.0
        old_pnl = old.pnl_mtime if old else 0.0
        old_dump_latest = old.dump_latest if old else None
        old_dump_count = old.dump_count if old else 0
        old_feat = old.feature_mtime if old else {}

        if new.src_mtime > old_src:
            changes.alpha_src.add(name)
        if new.pnl_mtime > old_pnl:
            changes.alpha_pnl.add(name)
        if (new.dump_latest, new.dump_count) != (old_dump_latest, old_dump_count):
            dates = _newer_dump_dates(config.alpha_dump / name, old_dump_latest)
            if dates:
                changes.alpha_dump[name] = dates

        feat_versions: list[str] = []
        for v in FEATURE_VERSIONS:
            if new.feature_mtime.get(v, 0.0) > old_feat.get(v, 0.0):
                feat_versions.append(v)
        if feat_versions:
            changes.alpha_feature[name] = feat_versions

    return changes, fresh
