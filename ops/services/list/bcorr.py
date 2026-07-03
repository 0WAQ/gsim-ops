import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ops.core.library import FactorInfo
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
    # bcorr 输出已排序，取最后一行（最大相关系数）
    name, corr = others[-1]
    return {"max_bcorr": corr, "max_bcorr_factor": name}


def _worker(args: tuple[str, Path, bool, Path]) -> tuple[str, dict | None]:
    name, pnl_path, has_pnl, config_path = args
    config = Config.load(config_path)
    fake = FactorInfo(
        name=name, author="", src_path=Path(), dump_path=Path(),
        pnl_path=pnl_path, has_pnl=has_pnl, dump_days=0,
    )
    return name, _compute_max_bcorr(fake, config)


def refresh_bcorr(
    factors: list[FactorInfo], config: Config, config_path: Path,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, dict]:
    # Workers only compute (no store handle in subprocesses); the parent writes.
    store = _store(config_path)
    targets = [f for f in factors if f.has_pnl]
    if not targets:
        return load_bcorr(config_path)

    payload = [(f.name, f.pnl_path, f.has_pnl, config_path) for f in targets]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker, p) for p in payload]
        for fut in tqdm(as_completed(futures), total=len(futures), desc="bcorr"):
            name, result = fut.result()
            if result:
                store.upsert_bcorr(name, result["max_bcorr"], result["max_bcorr_factor"])

    return load_bcorr(config_path)


def merge_bcorr(
    factors: list[FactorInfo], bcorr: dict[str, dict]
) -> list[FactorInfo]:
    for factor in factors:
        factor.bcorr = bcorr.get(factor.name)
    return factors


def update_bcorr(config_path: Path, name: str, max_bcorr: float, max_bcorr_factor: str) -> None:
    _store(config_path).upsert_bcorr(name, max_bcorr, max_bcorr_factor)
