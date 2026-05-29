import json
import hashlib
import time
from datetime import datetime
from pathlib import Path

from ops.core.library import FactorInfo
from ops.infra.config import Config
from ops.infra.cache import cache_path
from ops.infra.gsim.runner import Runner

BCORR_VERSION = 1


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_bcorr_path(config_path: Path) -> Path:
    legacy_hash = hashlib.md5(str(config_path.resolve()).encode()).hexdigest()[:8]
    library_id = Config.load(config_path).library_id
    return cache_path(library_id, "bcorr.json", legacy_hash=legacy_hash)


def load_bcorr(config_path: Path) -> dict[str, dict]:
    path = _get_bcorr_path(config_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data: dict = json.load(f)

        if data.get("version") != BCORR_VERSION:
            return {}

        return data.get("bcorr", {})
    except Exception:
        return {}


def _save_bcorr(config_path: Path, bcorr: dict[str, dict]) -> None:
    path = _get_bcorr_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    data = {
        "version": BCORR_VERSION,
        "created_at": time.time(),
        "bcorr": {
            name: {**v, "updated_at": now}
            for name, v in bcorr.items()
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _compute_max_bcorr(factor: FactorInfo, config: Config) -> dict | None:
    if not factor.has_pnl:
        return None
    corrs = Runner.run_bcorr(factor.pnl_path, config)
    if not corrs:
        return None
    # Exclude self (bcorr against own pnl always == 1)
    others = [(n, c) for n, c in corrs if n != factor.name]
    if not others:
        return None
    name, corr = max(others, key=lambda x: abs(x[1]))
    return {"max_bcorr": corr, "max_bcorr_factor": name}


def refresh_bcorr(
    factors: list[FactorInfo], config: Config, config_path: Path
) -> dict[str, dict]:
    bcorr: dict[str, dict] = {}
    for factor in factors:
        result = _compute_max_bcorr(factor, config)
        if result:
            bcorr[factor.name] = result

    _save_bcorr(config_path, bcorr)
    return bcorr


def merge_bcorr(
    factors: list[FactorInfo], bcorr: dict[str, dict]
) -> list[FactorInfo]:
    for factor in factors:
        factor.bcorr = bcorr.get(factor.name)
    return factors


def update_bcorr(config_path: Path, name: str, max_bcorr: float, max_bcorr_factor: str) -> None:
    path = _get_bcorr_path(config_path)
    data: dict = {"version": BCORR_VERSION, "created_at": time.time(), "bcorr": {}}

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    data.setdefault("bcorr", {})[name] = {
        "max_bcorr": max_bcorr,
        "max_bcorr_factor": max_bcorr_factor,
        "updated_at": _now_iso(),
    }
    data["created_at"] = time.time()

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
