"""Factor 身份信息 store (factor_info 表).

factor_info 存储因子的不可变身份信息: name/author/discovery_method。
与 factor_state (生命周期状态) / factor_snapshot (入库时快照) 分离。
"""
from .base import FactorInfo, InfoStore
from .pg_store import PostgresInfoStore

__all__ = ["FactorInfo", "InfoStore", "PostgresInfoStore", "default_info_store"]


def default_info_store(config) -> InfoStore:
    """根据 config 返回对应的 InfoStore 实现。

    当前只有 Postgres 实现（单库永远用 PG，无 JSON 回退）。
    """
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if not conninfo:
        raise ValueError("factor_info 需要 Postgres 后端，但未配置 state.postgres")
    return PostgresInfoStore(conninfo)
