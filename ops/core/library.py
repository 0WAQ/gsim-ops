"""Factor library scanner — 磁盘视角的对账工具。

2026-07-07 Wave 2 (JOURNAL V1/V2): 本类退出所有命令的热路径 —— list 的因子集
判据改为 factor_state (PG),info 的存在性判据改为 factor_info (PG)。derived
索引缓存整层删除(它自三表迁移起已坏:derived_meta 丢了 library_id 列,
get_meta 每次 UndefinedColumn 被吞 → 缓存永久失效,每次 list 白付 ~25s 扫盘,
full-review P0-4)。

保留本类的唯一用途:**未来 ops doctor 的磁盘对账**(回答"盘上有什么、和 PG
漂移了没有")+ info 的单因子现场 stat。scan() 现在是纯磁盘遍历,无缓存。
注意 `author_guess` 字段是目录名正则的**猜测**,非权威(权威在 factor_info 表)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ops.core.paths import META_FILENAME, FactorPaths

if TYPE_CHECKING:
    # 仅类型引用:core 不得运行期依赖 infra(import-linter C1)。Config 实例由
    # 调用方构造后传入。
    from ops.infra.config import Config


@dataclass
class ScannedFactor:
    """A factor as seen on the filesystem: identity guess + paths + physical facts.

    This is the *scan* product -- what a directory walk produces. Paths are
    reconstructed from the live Config, never persisted (they depend on the
    node's mount root). 2026-07-09 更名(原名 FactorInfo 与 infra/info 的表模型
    同名撞车,full-review D4):`author_guess` 来自目录名正则,是**猜测**,
    权威身份在 factor_info 表。"""
    name: str
    author_guess: str
    src_path: Path
    dump_path: Path
    pnl_path: Path
    has_pnl: bool
    dump_days: int
    delay: int | None = None


class LibraryScanner:
    AUTHOR_PATTERN = re.compile(r"^Alpha([A-Z][a-z]+)")

    def __init__(self, config: Config):
        self.config = config
        self.alpha_src = config.alpha_src
        self.alpha_dump = config.alpha_dump
        self.alpha_pnl = config.alpha_pnl

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

    def _read_delay(self, factor_dir: Path) -> int | None:
        meta_path = factor_dir / META_FILENAME
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text())
            return data.get("delay")
        except Exception:
            return None

    def _scan_directory(self) -> list[ScannedFactor]:
        factors: list[ScannedFactor] = []

        if not self.alpha_src.exists():
            return factors

        for factor_dir in sorted(self.alpha_src.iterdir()):
            if not factor_dir.is_dir():
                continue

            name = factor_dir.name
            fp = FactorPaths.of(name, self.config)
            factors.append(
                ScannedFactor(
                    name=name,
                    author_guess=self._parse_author(name),
                    src_path=factor_dir,
                    dump_path=fp.dump,
                    pnl_path=fp.pnl,
                    has_pnl=fp.pnl.exists(),
                    dump_days=self._count_dump_days(fp.dump),
                    delay=self._read_delay(factor_dir),
                )
            )

        return factors

    def scan(self) -> list[ScannedFactor]:
        """纯磁盘遍历 alpha_src(~25s 全库)。仅供对账/doctor 场景;
        命令热路径一律走 PG(list=factor_state, info=factor_info)。"""
        return self._scan_directory()

    def get(self, name: str) -> ScannedFactor | None:
        """单因子现场 stat(便宜:只碰该因子的 src/dump/pnl 路径)。"""
        fp = FactorPaths.of(name, self.config)
        if not fp.src.exists():
            return None

        return ScannedFactor(
            name=name,
            author_guess=self._parse_author(name),
            src_path=fp.src,
            dump_path=fp.dump,
            pnl_path=fp.pnl,
            has_pnl=fp.pnl.exists(),
            dump_days=self._count_dump_days(fp.dump),
        )

    def get_dump_date_range(self, name: str) -> tuple[str | None, str | None]:
        return self._get_dump_date_range(FactorPaths.of(name, self.config).dump)

    def filter_by_author(
        self, factors: list[ScannedFactor], author: str
    ) -> list[ScannedFactor]:
        return [f for f in factors if f.author_guess == author.lower()]
