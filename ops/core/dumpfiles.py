"""alpha_dump 逐日文件布局的走查正主(SSOT)。

布局:`<dump_dir>/<YYYY>/<MM>/<YYYYMMDD><v1|v2>.npy` —— gsim dump 输出与入库
alpha_dump 同构。v1/v2 是 gsim 的两个持仓口径,不是因子代码版本
(那是 factor_state.version)。

命名容错:存量并存 `20260102v2.npy` 与 `20260102.v2.npy` 两种写法(check
工作区扫描的 glob 两者都吃,pack 历史上只认无点形态)。解析两种都认;
**写出方(produce 安装 / repo.archive 搬运)沿用 gsim 真实产出名,不改名**。

同一布局的另两份存量走查(services/check/checker/dumpscan.py 扫 check 工作区、
core/library.py::_count_dump_days 扫盘对账)后议收敛到此,见 .claude/plans.md;
pack 已切换。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

DUMP_VERSIONS = ("v1", "v2")


def parse_dump_name(filename: str) -> tuple[int, str] | None:
    """`20260102v2.npy` / `20260102.v2.npy` → (20260102, 'v2');不合布局 → None。"""
    if not filename.endswith(".npy"):
        return None
    stem = filename[:-4]
    date_part, rest = stem[:8], stem[8:]
    if len(date_part) < 8 or not date_part.isdigit():
        return None
    if rest.startswith("."):
        rest = rest[1:]
    if rest not in DUMP_VERSIONS:
        return None
    return int(date_part), rest


def iter_dump_files(dump_dir: Path) -> Iterator[tuple[int, str, Path]]:
    """Yield (date, version, path)。目录不存在 → 空;非布局文件静默跳过。"""
    if not dump_dir.exists():
        return
    for year in dump_dir.iterdir():
        if not year.is_dir():
            continue
        for month in year.iterdir():
            if not month.is_dir():
                continue
            for f in month.iterdir():
                parsed = parse_dump_name(f.name)
                if parsed is None:
                    continue
                yield parsed[0], parsed[1], f


def dump_dates(dump_dir: Path, *, require_both: bool = False) -> set[int]:
    """已有 dump 的日期集。require_both=True 时 v1 与 v2 齐全才算有 ——
    安装中断留下的"半日"按缺失计,重产覆盖即自愈(残缺物不是用户数据)。"""
    seen: dict[int, set[str]] = {}
    for date, version, _ in iter_dump_files(dump_dir):
        seen.setdefault(date, set()).add(version)
    if not require_both:
        return set(seen)
    return {d for d, vs in seen.items() if len(vs) == len(DUMP_VERSIONS)}


def month_dir(dump_dir: Path, date: int) -> Path:
    """某日 dump 的落盘目录 `<dump_dir>/<YYYY>/<MM>/`(纯拼接,不建目录)。"""
    s = f"{date:08d}"
    return dump_dir / s[:4] / s[4:6]
