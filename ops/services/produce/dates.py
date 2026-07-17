"""缺失日期推导 + 数据就绪判定。

就绪三重规则(闸门以 `.meta` 为准,Dates.npy 只做轴):
  ready(D) ⇔ D ∈ 数据根交易日轴
           ∧ D ≤ 各 canary 目录 .meta.lastDate 的最小值(gsim 严格按 .meta 截断,
             轴的物理长度可以超出 gsim 可见范围)
           ∧ canary close.npy 第 idx(D) 行有非 NaN(build_cc 的末行可能是 NaN
             占位 —— 只看 lastDate 会对着占位行产出垃圾 dump)

latest_ready = 自闸门日起在轴上向前回退(限 READY_BACKOFF)第一个 ready 日。
缺省模式目标日自动落 latest_ready;显式 --date 不就绪则响亮拒绝(用户明确
要了的日期不能静默换掉)。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ops.core.universe import CcMeta, load_universe, read_cc_meta
from ops.infra.config import Config

# 就绪回退上限:闸门日连续 NaN 占位超过这个数就不是"当日数据未 build 完"
# 而是数据链故障,响亮失败让人去查,不再往更早回退
READY_BACKOFF = 5


class ProduceError(Exception):
    """输入 / 数据前提不满足(非交易日、未就绪、config 缺键)。cli 侧转
    error + 非零退出。"""


def spot_check_close(data_dir: Path, idx: int, meta: CcMeta) -> bool:
    """canary 目录第 idx 行是否有真数据。无 close.npy 的 canary 只吃 .meta
    闸门(行情类目录都有 close;自定义 canary 缺字段不该让判定失真)。"""
    close = data_dir / "close.npy"
    if not close.exists():
        return True
    mm = np.memmap(close, dtype=np.float64, mode="r",
                   shape=(meta.date_capacity, meta.instrument_capacity))
    try:
        return bool(np.any(~np.isnan(np.array(mm[idx]))))
    finally:
        del mm


def resolve_axis(config: Config, spot_check=spot_check_close,
                 ) -> tuple[list[int], int]:
    """读数据根 → (交易日轴, latest_ready)。数据根不可达 / canary 全占位 → 抛。"""
    root = config.produce_nio_data_path
    assert root is not None  # run_produce 入口已验
    dates_arr, _, date_to_idx = load_universe(root)
    dates = [int(d) for d in dates_arr]

    metas = {name: read_cc_meta(root / name)
             for name in config.produce_readiness_dirs}
    gate = min(m.last_date for m in metas.values())

    candidates = [d for d in dates if d <= gate][-READY_BACKOFF:]
    for cand in reversed(candidates):
        idx = date_to_idx[cand]
        if all(idx < m.date_capacity and spot_check(root / name, idx, m)
               for name, m in metas.items()):
            return dates, cand
    raise ProduceError(
        f"数据未就绪:自闸门日 {gate} 向前 {READY_BACKOFF} 个交易日 canary 抽查"
        f"均无有效数据({root},canary={list(metas)})—— 检查数据链而非重试")


def resolve_target(dates: list[int], latest_ready: int,
                   explicit: int | None) -> int:
    """定目标日。缺省 = latest_ready;显式给的必须是就绪交易日。"""
    if explicit is None:
        return latest_ready
    if explicit not in set(dates):
        raise ProduceError(f"{explicit} 不是交易日(不在数据根日期轴上)")
    if explicit > latest_ready:
        raise ProduceError(f"{explicit} 数据未就绪(最新就绪日 {latest_ready})")
    return explicit


def window_dates(dates: list[int], start: int, end: int) -> list[int]:
    """轴上 [start, end] 的交易日(闭区间)。"""
    return [d for d in dates if start <= d <= end]


def missing_dates(window: list[int], existing: set[int]) -> list[int]:
    """缺失 = 窗口 - 已有。集合差,洞天然现形;调用方决定 existing 的口径
    (缺省 require_both —— 半日按缺失计)。"""
    return [d for d in window if d not in existing]
