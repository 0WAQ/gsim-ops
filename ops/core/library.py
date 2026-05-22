"""Factor library scanner for querying factors in alphalib."""

import hashlib
import json
import re
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from ops.infra.config import Config
from ops.infra.cache import cache_path
from ops.core.metrics import Metrics


INDEX_VERSION = 3
INDEX_MAX_AGE_SECONDS = 3600  # 1 hour


def _get_cache_path(config: Config, config_path: Path) -> Path:
    legacy_hash = hashlib.md5(str(config_path.resolve()).encode()).hexdigest()[:8]
    return cache_path(config.library_id, "index.json", legacy_hash=legacy_hash)


@dataclass
class FactorInfo:
    name: str
    author: str
    src_path: Path
    dump_path: Path
    pnl_path: Path
    has_pnl: bool
    dump_days: int
    metrics: Metrics | None = None
    datasources: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "author": self.author,
            "src_path": str(self.src_path),
            "dump_path": str(self.dump_path),
            "pnl_path": str(self.pnl_path),
            "has_pnl": self.has_pnl,
            "dump_days": self.dump_days,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "datasources": self.datasources,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactorInfo":
        metrics_data = data.get("metrics")
        return cls(
            name=data["name"],
            author=data["author"],
            src_path=Path(data["src_path"]),
            dump_path=Path(data["dump_path"]),
            pnl_path=Path(data["pnl_path"]),
            has_pnl=data["has_pnl"],
            dump_days=data["dump_days"],
            metrics=Metrics.from_dict(metrics_data) if metrics_data else None,
            datasources=data.get("datasources"),
        )


class LibraryScanner:
    AUTHOR_PATTERN = re.compile(r"^Alpha([A-Z][a-z]+)")

    def __init__(self, config: Config, config_path: Path, use_cache: bool = True):
        self.config = config
        self.alpha_src = config.alpha_src
        self.alpha_dump = config.alpha_dump
        self.alpha_pnl = config.alpha_pnl
        self.use_cache = use_cache
        self._index_path = _get_cache_path(config, config_path)

    @classmethod
    def from_config_path(
        cls, config_path: Path, use_cache: bool = True
    ) -> "LibraryScanner":
        config = Config.load(config_path)
        return cls(config, config_path, use_cache=use_cache)

    def _parse_author(self, name: str) -> str:
        match = self.AUTHOR_PATTERN.match(name)
        if match:
            return match.group(1).lower()
        return "unknown"

    def _count_dump_days(self, dump_path: Path) -> int:
        if not dump_path.exists():
            return 0

        count = 0
        try:
            for year_dir in dump_path.iterdir():
                if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
                    continue
                for month_dir in year_dir.iterdir():
                    if not month_dir.is_dir() or not re.match(
                        r"^\d{2}$", month_dir.name
                    ):
                        continue
                    count += len(list(month_dir.glob("*v2.npy")))
        except Exception:
            pass
        return count

    def _get_dump_date_range(self, dump_path: Path) -> tuple[str | None, str | None]:
        if not dump_path.exists():
            return None, None

        try:
            dates: list[str] = []
            for year_dir in sorted(dump_path.iterdir()):
                if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
                    continue
                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir() or not re.match(
                        r"^\d{2}$", month_dir.name
                    ):
                        continue
                    for npy_file in month_dir.glob("*v2.npy"):
                        date_match = re.match(r"^(\d{8})v2\.npy$", npy_file.name)
                        if date_match:
                            dates.append(date_match.group(1))

            if dates:
                dates.sort()
                return dates[0], dates[-1]
        except Exception:
            pass
        return None, None

    def _load_index(self) -> list[FactorInfo] | None:
        if not self._index_path.exists():
            return None

        try:
            with self._index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("version") != INDEX_VERSION:
                return None

            created_at = data.get("created_at", 0)
            if time.time() - created_at > INDEX_MAX_AGE_SECONDS:
                return None

            return [FactorInfo.from_dict(f) for f in data.get("factors", [])]
        except Exception:
            return None

    def _save_index(self, factors: list[FactorInfo]) -> None:
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": INDEX_VERSION,
                "created_at": time.time(),
                "factor_count": len(factors),
                "factors": [f.to_dict() for f in factors],
            }
            with self._index_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _scan_directory(self) -> list[FactorInfo]:
        factors: list[FactorInfo] = []

        if not self.alpha_src.exists():
            return factors

        for factor_dir in sorted(self.alpha_src.iterdir()):
            if not factor_dir.is_dir():
                continue

            name = factor_dir.name
            author = self._parse_author(name)
            dump_path = self.alpha_dump / name
            pnl_path = self.alpha_pnl / name
            has_pnl = pnl_path.exists()
            dump_days = self._count_dump_days(dump_path)

            factors.append(
                FactorInfo(
                    name=name,
                    author=author,
                    src_path=factor_dir,
                    dump_path=dump_path,
                    pnl_path=pnl_path,
                    has_pnl=has_pnl,
                    dump_days=dump_days,
                )
            )

        return factors

    def scan(self, refresh: bool = False) -> list[FactorInfo]:
        if self.use_cache and not refresh:
            cached = self._load_index()
            if cached is not None:
                return cached

        factors = self._scan_directory()

        if self.use_cache:
            self._save_index(factors)

        return factors

    def get(self, name: str, use_cache: bool = True) -> FactorInfo | None:
        if use_cache and self.use_cache:
            cached = self._load_index()
            if cached is not None:
                for f in cached:
                    if f.name == name:
                        return f

        src_path = self.alpha_src / name
        if not src_path.exists():
            return None

        dump_path = self.alpha_dump / name
        pnl_path = self.alpha_pnl / name

        return FactorInfo(
            name=name,
            author=self._parse_author(name),
            src_path=src_path,
            dump_path=dump_path,
            pnl_path=pnl_path,
            has_pnl=pnl_path.exists(),
            dump_days=self._count_dump_days(dump_path),
        )

    def get_dump_date_range(self, name: str) -> tuple[str | None, str | None]:
        dump_path = self.alpha_dump / name
        return self._get_dump_date_range(dump_path)

    def filter_by_author(
        self, factors: list[FactorInfo], author: str
    ) -> list[FactorInfo]:
        return [f for f in factors if f.author == author.lower()]
