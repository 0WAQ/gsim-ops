from pathlib import Path

from .base import StateStore
from .json_store import JsonStateStore


DEFAULT_STATE_PATH = Path.home() / ".cache" / "ops" / "factor_state.json"


def default_store() -> StateStore:
    return JsonStateStore(DEFAULT_STATE_PATH)


__all__ = ["StateStore", "JsonStateStore", "default_store", "DEFAULT_STATE_PATH"]
