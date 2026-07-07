from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config
from .base import StateStore
from .json_store import JsonStateStore
from .redis_store import RedisStateStore


def _default_state_path(config: Config) -> Path:
    return cache_path(config.library_id, "factor_state.json")


def default_store(config: Config) -> StateStore:
    """返回对应的 StateStore 实现。

    2026-07-06 重构: Postgres 后端不再需要 library_id (永远单库)。
    """
    backend = (getattr(config, "state_backend", None) or "json").lower()
    if backend == "json":
        return JsonStateStore(_default_state_path(config))
    if backend == "redis":
        url = getattr(config, "state_redis_url", None)
        if not url:
            raise ValueError("config.state.redis.url is required when state_backend=redis")
        password = getattr(config, "state_redis_password", None)
        return RedisStateStore(url=url, library_id=config.library_id, password=password)
    if backend == "postgres":
        conninfo = getattr(config, "state_postgres_conninfo", None)
        if not conninfo:
            raise ValueError(
                "config.state.postgres.{host,dbname,user,...} required when state_backend=postgres"
            )
        from .pg_store import PostgresStateStore
        return PostgresStateStore(conninfo=conninfo)
    raise ValueError(f"unknown state_backend: {backend!r} (json | redis | postgres)")


__all__ = ["StateStore", "JsonStateStore", "RedisStateStore", "default_store"]
