from pathlib import Path

from ops.infra.cache import cache_path
from ops.infra.config import Config, get_default_config_path
from .base import StateStore
from .json_store import JsonStateStore


def _default_state_path() -> Path:
    config = Config.load(get_default_config_path())
    return cache_path(config.library_id, "factor_state.json")


def default_store() -> StateStore:
    return JsonStateStore(_default_state_path())


__all__ = ["StateStore", "JsonStateStore", "default_store"]
