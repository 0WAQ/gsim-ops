"""Factor 入库时快照 store (factor_snapshot 表).

factor_snapshot 存储因子入库时（check 通过那一刻）的所有派生数据快照：
- metrics: ret/shrp/mdd/tvr/fitness
- datasources: fields/tables
- delay: 入库时从 XML 解析定死（原 index 组的 has_pnl/dump_days 是可变物理事实，
  与快照不可变语义冲突已删除；需实时状态走 LibraryScanner 扫盘）
- bcorr: max_bcorr/max_bcorr_factor

关键特性：
1. snapshot_at = factor_state.entered_at (入库时间)
2. 写入一次，永不更新（不可变快照）
3. 替代原 factor_derived 的可重算语义
"""
from .base import FactorSnapshot, SnapshotStore
from .pg_store import PostgresSnapshotStore

__all__ = ["FactorSnapshot", "SnapshotStore", "PostgresSnapshotStore", "default_snapshot_store"]


def default_snapshot_store(config) -> SnapshotStore:
    """根据 config 返回对应的 SnapshotStore 实现。

    当前只有 Postgres 实现（单库永远用 PG，无 JSON 回退）。
    """
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if not conninfo:
        raise ValueError("factor_snapshot 需要 Postgres 后端，但未配置 state.postgres")
    return PostgresSnapshotStore(conninfo)
