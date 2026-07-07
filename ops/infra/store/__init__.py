from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config
from .base import StateStore
from .json_store import JsonStateStore


def _default_state_path(config: Config) -> Path:
    return cache_path(config.library_id, "factor_state.json")


def default_store(config: Config) -> StateStore:
    """返回对应的 StateStore 实现。

    - postgres: 生产真相源 (factor_state 表)。
    - json: 单机 dev/test 后端 (fcntl 锁 + 原子写) —— **不是生产回退**。

    2026-07-07 Wave 1: redis 后端删除。它自 2026-07-06 三表拆分起就与
    FactorRecord 不兼容 (读写已删的 author/submitted_by, 每次 put 必
    AttributeError), 作为"紧急回退"是假保险 (full-review P0-2/G1)。
    Redis-sentinel 实例本身是 JFS metadata 后端, 与 ops 无关, 不受影响。
    """
    backend = (getattr(config, "state_backend", None) or "json").lower()
    if backend == "json":
        return JsonStateStore(_default_state_path(config))
    if backend == "postgres":
        conninfo = getattr(config, "state_postgres_conninfo", None)
        if not conninfo:
            raise ValueError(
                "config.state.postgres.{host,dbname,user,...} required when state_backend=postgres"
            )
        from .pg_store import PostgresStateStore
        return PostgresStateStore(conninfo=conninfo)
    raise ValueError(
        f"unknown state_backend: {backend!r} (postgres | json; "
        f"redis 后端已于 2026-07-07 退役, 见 docs/remediation/JOURNAL.md F2)"
    )


__all__ = ["StateStore", "JsonStateStore", "default_store"]
