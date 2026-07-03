import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ops.infra.config import Config
from ops.infra.derived import default_derived_store
from ops.infra.gsim.runner import Runner

DEFAULT_WORKERS = max(1, min(16, (os.cpu_count() or 4) - 2))


def _store(config_path: Path):
    return default_derived_store(Config.load(config_path))


def load_bcorr(config_path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, rec in _store(config_path).get_all().items():
        if rec.max_bcorr is None:
            continue
        out[name] = {"max_bcorr": rec.max_bcorr, "max_bcorr_factor": rec.max_bcorr_factor}
    return out


def _compute_max_bcorr(name: str, pnl_path: Path, config: Config) -> dict | None:
    corrs = Runner.run_bcorr(pnl_path, config)
    if not corrs:
        return None
    # Exclude self (bcorr against own pnl always == 1)
    others = [(n, c) for n, c in corrs if n != name]
    if not others:
        return None
    # bcorr 输出已排序，取最后一行（最大相关系数）
    other, corr = others[-1]
    return {"max_bcorr": corr, "max_bcorr_factor": other}


def _worker(args: tuple[str, Path, Path]) -> tuple[str, dict | None]:
    name, pnl_path, config_path = args
    config = Config.load(config_path)
    return name, _compute_max_bcorr(name, pnl_path, config)


def refresh_bcorr(
    names: list[str], config: Config, config_path: Path,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, dict]:
    """Recompute max bcorr for the given factor names (parallel), write to the
    store. Only factors with an existing pnl are computed. Paths from config."""
    store = _store(config_path)
    targets = [n for n in names if (config.alpha_pnl / n).exists()]
    if not targets:
        return load_bcorr(config_path)

    payload = [(n, config.alpha_pnl / n, config_path) for n in targets]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker, p) for p in payload]
        for fut in tqdm(as_completed(futures), total=len(futures), desc="bcorr"):
            name, result = fut.result()
            if result:
                store.upsert_bcorr(name, result["max_bcorr"], result["max_bcorr_factor"])

    return load_bcorr(config_path)
