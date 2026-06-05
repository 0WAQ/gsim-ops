import json
import hashlib
import time
from datetime import datetime
from pathlib import Path

from ops.core.library import FactorInfo
from ops.core.metrics import Metrics
from ops.infra.config import Config
from ops.infra.cache import cache_path
from ops.infra.gsim.runner import Runner

METRICS_VERSION = 1


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_metrics_path(config_path: Path) -> Path:
    legacy_hash = hashlib.md5(str(config_path.resolve()).encode()).hexdigest()[:8]
    library_id = Config.load(config_path).library_id
    return cache_path(library_id, "metrics.json", legacy_hash=legacy_hash)


def load_metrics(config_path: Path) -> dict[str, Metrics]:
    path = _get_metrics_path(config_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data: dict = json.load(f)

        if data.get("version") != METRICS_VERSION:
            return {}

        return {
            name: Metrics.from_dict(m)
            for name, m in data.get("metrics", {}).items()
        }
    except Exception:
        return {}


def _save_metrics(config_path: Path, metrics: dict[str, Metrics]) -> None:
    path = _get_metrics_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    data = {
        "version": METRICS_VERSION,
        "created_at": time.time(),
        "metrics": {
            name: {**m.to_dict(), "updated_at": now}
            for name, m in metrics.items()
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def refresh_metrics(
    factors: list[FactorInfo], config: Config, config_path: Path
) -> dict[str, Metrics]:
    metrics = load_metrics(config_path)
    for factor in factors:
        if not factor.has_pnl:
            continue
        result = Runner.run_simsummary(factor.pnl_path, config)
        if result:
            metrics[factor.name] = result

    _save_metrics(config_path, metrics)
    return metrics


def merge_metrics(
    factors: list[FactorInfo], metrics: dict[str, Metrics]
) -> list[FactorInfo]:
    for factor in factors:
        factor.metrics = metrics.get(factor.name)
    return factors


def update_metrics(config_path: Path, name: str, m: Metrics) -> None:
    path = _get_metrics_path(config_path)
    data: dict = {"version": METRICS_VERSION, "created_at": time.time(), "metrics": {}}

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    data.setdefault("metrics", {})[name] = {**m.to_dict(), "updated_at": _now_iso()}
    data["created_at"] = time.time()

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
