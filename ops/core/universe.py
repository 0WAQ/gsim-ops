"""cc 数据根的元数据读取:__universe 轴 + `.meta` 快照锁。

cc 布局事实(正主 docs/gsim/cc-data-layout.md):
- `__universe/Dates.npy`(int64 YYYYMMDD)/ `Instruments.npy`(U32)是交易日 /
  股票轴,gsim 自定义二进制(**无 numpy header**),np.memmap 直读;
- 每个数据目录下的 `.meta` 是 gsim 的硬约束 / 快照锁(三行:lastDate /
  dateCapacity / instrumentCapacity),gsim 读 memmap 严格按它截断。判断
  "gsim 能看到哪天"必须读 `.meta` —— Dates.npy 物理长度可以超出 gsim 可见
  范围(cc_2025 正是文件 symlink cc_all、独立 `.meta` 锁 3900 行的时点快照)。

消费方:pack(全量聚合的日期轴)与 produce(日增就绪判定)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

DATES_FILE = "__universe/Dates.npy"
INSTRUMENTS_FILE = "__universe/Instruments.npy"
META_FILE = ".meta"


def load_universe(nio_data_path: Path) -> tuple[np.ndarray, np.ndarray, dict[int, int]]:
    dates = np.array(np.memmap(nio_data_path / DATES_FILE, mode="r", dtype=int))
    ins = np.array(np.memmap(nio_data_path / INSTRUMENTS_FILE, mode="r",
                             dtype=np.dtype("U32")))
    return dates, ins, {int(d): i for i, d in enumerate(dates)}


@dataclass(frozen=True)
class CcMeta:
    last_date: int
    date_capacity: int
    instrument_capacity: int


def read_cc_meta(data_dir: Path) -> CcMeta:
    """解析数据目录的 `.meta`。缺失 / 格式异常直接抛 —— `.meta` 是 gsim 可见性
    的唯一凭据,猜缺省值 = 静默错判就绪。"""
    meta_file = data_dir / META_FILE
    tokens = meta_file.read_text(encoding="utf-8").split()
    try:
        return CcMeta(
            last_date=int(tokens[0]),
            date_capacity=int(tokens[tokens.index("dateCapacity") + 1]),
            instrument_capacity=int(tokens[tokens.index("instrumentCapacity") + 1]),
        )
    except (ValueError, IndexError) as e:
        raise ValueError(f".meta 格式异常: {meta_file}") from e
