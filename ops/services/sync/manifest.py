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
    pnl_mtime: float = 0.0                  # alpha_pnl/<name>  is a single file
    dump_latest: Optional[str] = None       # YYYYMMDD
    dump_count: int = 0
    feature_mtime: float = 0.0              # max(mtime(<name>.v1.npy), mtime(<name>.v2.npy))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactorFingerprint":
        d = dict(d)
        # tolerate v0 manifest where feature_mtime was a dict
        fm = d.get("feature_mtime", 0.0)
        if isinstance(fm, dict):
            d["feature_mtime"] = max(fm.values()) if fm else 0.0
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


_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{2}$")


def _dump_summary(dump_dir: Path) -> tuple[Optional[str], int]:
    """Return (latest YYYYMMDD date, total .npy count) under one factor's
    dump dir. Layout: <dump_dir>/<YYYY>/<MM>/<YYYYMMDD>v{1,2}.npy"""
    if not dump_dir.exists():
        return None, 0
    latest: Optional[str] = None
    total = 0
    with os.scandir(dump_dir) as it:
        for year_entry in it:
            if not year_entry.is_dir() or not _YEAR_RE.match(year_entry.name):
                continue
            with os.scandir(year_entry.path) as mon_it:
                for mon_entry in mon_it:
                    if not mon_entry.is_dir() or not _MONTH_RE.match(mon_entry.name):
                        continue
                    with os.scandir(mon_entry.path) as day_it:
                        for day_entry in day_it:
                            if not day_entry.is_file() or not day_entry.name.endswith(".npy"):
                                continue
                            total += 1
                            # Extract YYYYMMDD from "YYYYMMDDv1.npy" or "YYYYMMDDv2.npy"
                            date_str = day_entry.name[:8]
                            if _DATE_RE.match(date_str):
                                if latest is None or date_str > latest:
                                    latest = date_str
    return latest, total


def _file_mtime(path: Path) -> float:
    """Single-file mtime. 0 if missing."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _feature_versions_present(feature_dir: Path, name: str) -> list[str]:
    """Which of v1/v2 exist as alpha_feature/<name>.<v>.npy."""
    return [v for v in FEATURE_VERSIONS
            if (feature_dir / f"{name}.{v}.npy").exists()]


def _feature_mtime(feature_dir: Path, name: str) -> float:
    """Max mtime of alpha_feature/<name>.v1.npy and alpha_feature/<name>.v2.npy."""
    best = 0.0
    for v in FEATURE_VERSIONS:
        m = _file_mtime(feature_dir / f"{name}.{v}.npy")
        if m > best:
            best = m
    return best


def stat_factor(name: str, config: Config) -> FactorFingerprint:
    src_mtime = _max_mtime(config.alpha_src / name)
    pnl_mtime = _file_mtime(config.alpha_pnl / name)
    dump_latest, dump_count = _dump_summary(config.alpha_dump / name)
    feature_mtime = _feature_mtime(config.alpha_feature, name)
    return FactorFingerprint(
        src_mtime=src_mtime,
        pnl_mtime=pnl_mtime,
        dump_latest=dump_latest,
        dump_count=dump_count,
        feature_mtime=feature_mtime,
    )


def list_factor_names(config: Config) -> list[str]:
    """Union of factor names across all data dirs.

    Layout:
      alpha_src/<name>/        — dir
      alpha_dump/<name>/       — dir
      alpha_pnl/<name>         — file
      alpha_feature/<name>.v{1,2}.npy — files
    """
    names: set[str] = set()

    for d in (config.alpha_src, config.alpha_dump):
        if not d.exists():
            continue
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_dir() and not entry.name.startswith("."):
                        names.add(entry.name)
        except OSError:
            pass

    if config.alpha_pnl.exists():
        try:
            with os.scandir(config.alpha_pnl) as it:
                for entry in it:
                    if entry.is_file() and not entry.name.startswith("."):
                        names.add(entry.name)
        except OSError:
            pass

    if config.alpha_feature.exists():
        try:
            with os.scandir(config.alpha_feature) as it:
                for entry in it:
                    if not entry.is_file() or entry.name.startswith("."):
                        continue
                    n = entry.name
                    for v in FEATURE_VERSIONS:
                        suffix = f".{v}.npy"
                        if n.endswith(suffix):
                            names.add(n[:-len(suffix)])
                            break
        except OSError:
            pass

    return sorted(names)


def _newer_dump_dates(dump_dir: Path, prev_latest: Optional[str]) -> list[str]:
    """List year/month paths under dump_dir that contain dates > prev_latest.
    Returns sorted relative paths like ['2025/01', '2025/02', ...]."""
    if not dump_dir.exists():
        return []
    out: set[str] = set()
    with os.scandir(dump_dir) as it:
        for year_entry in it:
            if not year_entry.is_dir() or not _YEAR_RE.match(year_entry.name):
                continue
            y = year_entry.name
            with os.scandir(year_entry.path) as mon_it:
                for mon_entry in mon_it:
                    if not mon_entry.is_dir() or not _MONTH_RE.match(mon_entry.name):
                        continue
                    m = mon_entry.name
                    with os.scandir(mon_entry.path) as day_it:
                        for day_entry in day_it:
                            if not day_entry.is_file() or not day_entry.name.endswith(".npy"):
                                continue
                            date_str = day_entry.name[:8]
                            if not _DATE_RE.match(date_str):
                                continue
                            if prev_latest is None or date_str > prev_latest:
                                out.add(f"{y}/{m}")
                                break  # month already included
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
        old_feat = old.feature_mtime if old else 0.0

        if new.src_mtime > old_src:
            changes.alpha_src.add(name)
        if new.pnl_mtime > old_pnl:
            changes.alpha_pnl.add(name)
        if (new.dump_latest, new.dump_count) != (old_dump_latest, old_dump_count):
            dates = _newer_dump_dates(config.alpha_dump / name, old_dump_latest)
            if dates:
                changes.alpha_dump[name] = dates
        if new.feature_mtime > old_feat:
            versions = _feature_versions_present(config.alpha_feature, name)
            if versions:
                changes.alpha_feature[name] = versions

    return changes, fresh
