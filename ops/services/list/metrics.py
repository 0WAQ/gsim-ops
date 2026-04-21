import json
import hashlib
import time
from pathlib import Path

from ops.core.library import FactorInfo
from ops.core.metrics import Metrics
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner

METRICS_VERSION = 1
CACHE_DIR = Path.home() / ".cache" / "ops"


def _get_metrics_path(config_path: Path) -> Path:
    config_hash = hashlib.md5(str(config_path.resolve()).encode()).hexdigest()[:8]
    return CACHE_DIR / f"{config_hash}.metrics.json"


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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version": METRICS_VERSION,
        "created_at": time.time(),
        "metrics": {name: m.to_dict() for name, m in metrics.items()},
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def refresh_metrics(
    factors: list[FactorInfo], config: Config, config_path: Path
) -> dict[str, Metrics]:
    metrics: dict[str, Metrics] = {}
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

    data.setdefault("metrics", {})[name] = m.to_dict()
    data["created_at"] = time.time()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
