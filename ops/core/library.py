"""Factor library scanner for querying factors in alphalib."""

import json
import re
import time
from pathlib import Path
from dataclasses import dataclass

from ops.infra.config import Config


INDEX_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days -- belt-and-suspenders fallback;
                                       # primary invalidation is alpha_src mtime
                                       # vs the DerivedStore's index_built_at watermark


@dataclass
class FactorInfo:
    """A factor as seen on the filesystem: identity + paths + index fields.

    This is the *scan* product -- what a directory walk produces and what the
    index group of the DerivedStore persists. Derived values (metrics /
    datasources / bcorr) are NOT here; read those from the DerivedStore
    directly (DerivedRecord). Paths are reconstructed from the live Config,
    never persisted (they depend on the node's mount root)."""
    name: str
    author: str
    src_path: Path
    dump_path: Path
    pnl_path: Path
    has_pnl: bool
    dump_days: int
    delay: int | None = None


class LibraryScanner:
    AUTHOR_PATTERN = re.compile(r"^Alpha([A-Z][a-z]+)")

    def __init__(self, config: Config, config_path: Path, use_cache: bool = True):
        self.config = config
        self.alpha_src = config.alpha_src
        self.alpha_dump = config.alpha_dump
        self.alpha_pnl = config.alpha_pnl
        self.use_cache = use_cache

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

    # --- DerivedStore-backed index (shared across machines) ------------------
    #
    # The index (name/author/has_pnl/dump_days/delay) lives in the derived
    # store alongside metrics/datasources/bcorr. Freshness is a single
    # library-level watermark `index_built_at` (epoch seconds) compared against
    # alpha_src's mtime -- which sits on shared JFS, so all machines see the
    # same value. Whichever machine first notices alpha_src changed pays the
    # ~25s directory scan and republishes; every other machine then reads the
    # index straight from Postgres (~0.1s). No per-machine JSON, no per-machine
    # rescan.

    def _store(self):
        from ops.infra.derived import default_derived_store
        return default_derived_store(self.config)

    def _record_to_info(self, rec) -> FactorInfo:
        name = rec.name
        return FactorInfo(
            name=name,
            author=rec.author if rec.author is not None else self._parse_author(name),
            src_path=self.alpha_src / name,
            dump_path=self.alpha_dump / name,
            pnl_path=self.alpha_pnl / name,
            has_pnl=bool(rec.has_pnl),
            dump_days=rec.dump_days or 0,
            delay=rec.delay,
        )

    def _load_index_from_store(self) -> list[FactorInfo] | None:
        """Fast path: read index from the store if it's fresh vs alpha_src mtime."""
        try:
            store = self._store()
            built_at_raw = store.get_meta("index_built_at")
            if not built_at_raw:
                return None
            built_at = float(built_at_raw)
            try:
                src_mtime = self.alpha_src.stat().st_mtime
                if src_mtime > built_at:
                    return None  # alpha_src changed since last build -> stale
            except OSError:
                pass  # mount down: trust the store
            if time.time() - built_at > INDEX_MAX_AGE_SECONDS:
                return None  # TTL backstop (catches dump_days drift w/o mkdir)
            recs = store.get_all()
            if not recs:
                return None
            # Only surface factors that actually have index data (author set on
            # scan); bcorr/metrics-only rows without an index shouldn't appear.
            return [
                self._record_to_info(r) for r in recs.values()
                if r.author is not None or r.has_pnl is not None
            ]
        except Exception:
            return None

    def _publish_index(self, factors: list[FactorInfo]) -> None:
        try:
            store = self._store()
            store.upsert_index({
                f.name: {
                    "author": f.author,
                    "has_pnl": f.has_pnl,
                    "dump_days": f.dump_days,
                    "delay": f.delay,
                }
                for f in factors
            })
            store.set_meta("index_built_at", repr(time.time()))
        except Exception:
            pass

    def _read_delay(self, factor_dir: Path) -> int | None:
        meta_path = factor_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text())
            return data.get("delay")
        except Exception:
            return None

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
            delay = self._read_delay(factor_dir)

            factors.append(
                FactorInfo(
                    name=name,
                    author=author,
                    src_path=factor_dir,
                    dump_path=dump_path,
                    pnl_path=pnl_path,
                    has_pnl=has_pnl,
                    dump_days=dump_days,
                    delay=delay,
                )
            )

        return factors

    def scan(self, refresh: bool = False) -> list[FactorInfo]:
        if self.use_cache and not refresh:
            cached = self._load_index_from_store()
            if cached is not None:
                return cached

        factors = self._scan_directory()

        if self.use_cache:
            self._publish_index(factors)

        return factors

    def get(self, name: str, use_cache: bool = True) -> FactorInfo | None:
        if use_cache and self.use_cache:
            cached = self._load_index_from_store()
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
