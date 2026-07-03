"""派生层存储 —— 后端分发.

`default_derived_store(config)` 按 config.derived_backend 选后端:
  - json (默认): ~/.cache/ops/lib/<lib>/derived.json,单机回退
  - postgres: factor_derived 宽表,三机共享 + 可查询

与 ops/infra/store/ (state 后端) 同样的"插后端"范式:上层只认 DerivedStore
接口,换后端零业务改动。
"""
from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config
from .base import DerivedStore, DerivedRecord
from .json_store import JsonDerivedStore


def _default_json_path(config: Config) -> Path:
    return cache_path(config.library_id, "derived.json")


def default_derived_store(config: Config) -> DerivedStore:
    backend = (getattr(config, "derived_backend", None) or "json").lower()
    if backend == "json":
        return JsonDerivedStore(_default_json_path(config))
    if backend == "postgres":
        conninfo = getattr(config, "derived_postgres_conninfo", None)
        if not conninfo:
            raise ValueError(
                "config.derived.postgres.{host,dbname,user,...} required when derived_backend=postgres"
            )
        # Import lazily so json-only environments don't need psycopg installed.
        from .pg_store import PostgresDerivedStore
        return PostgresDerivedStore(conninfo=conninfo, library_id=config.library_id)
    raise ValueError(f"unknown derived_backend: {backend!r} (json | postgres)")


__all__ = [
    "DerivedStore",
    "DerivedRecord",
    "JsonDerivedStore",
    "default_derived_store",
]
