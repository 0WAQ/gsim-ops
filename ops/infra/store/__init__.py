from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config
from .base import StateStore
from .json_store import JsonStateStore
from .redis_store import RedisStateStore


def _default_state_path(config: Config) -> Path:
    return cache_path(config.library_id, "factor_state.json")


def default_store(config: Config) -> StateStore:
    backend = (getattr(config, "state_backend", None) or "json").lower()
    if backend == "json":
        return JsonStateStore(_default_state_path(config))
    if backend == "redis":
        url = getattr(config, "state_redis_url", None)
        if not url:
            raise ValueError("config.state.redis.url is required when state_backend=redis")
        password = getattr(config, "state_redis_password", None)
        return RedisStateStore(url=url, library_id=config.library_id, password=password)
    raise ValueError(f"unknown state_backend: {backend!r} (json | redis)")


__all__ = ["StateStore", "JsonStateStore", "RedisStateStore", "default_store"]
