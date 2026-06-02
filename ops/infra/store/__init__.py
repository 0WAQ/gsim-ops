from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config
from .base import StateStore
from .json_store import JsonStateStore


def _default_state_path(config: Config) -> Path:
    return cache_path(config.library_id, "factor_state.json")


def default_store(config: Config) -> StateStore:
    return JsonStateStore(_default_state_path(config))


__all__ = ["StateStore", "JsonStateStore", "default_store"]
