"""Aggregate per-date alpha_dump files into per-factor matrix (alpha_feature).

Source layout:  alpha_dump/AlphaXxx/{year}/{month}/{YYYYMMDD}{v1|v2}.npy   (each is shape (H,))
Target layout:  alpha_feature/AlphaXxx.{v1|v2}.npy                          (memmap, shape (L, H))

Offset depends on each factor's `delay` (from meta.json):
- delay=0: dump at date D is written at the same di — row = date_to_idx[D]
- delay=1: dump at date D is next-day signal — row = date_to_idx[D] - 1
"""
import json
import os
import random
import shutil
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


DATES_FILE = "__universe/Dates.npy"
INSTRUMENTS_FILE = "__universe/Instruments.npy"
DTYPE = np.float64
VERSIONS = ("v1", "v2")
ATOL = 1e-6
VERIFY_SAMPLES = 5

# Pack 流程只覆盖到 20251231(check 流程上限),日增 (>20251231) 走另一条路径。
# L 写死 3900;H 当前 5484,1-2 年内不变,变了再说。
PACK_L = 3900


def load_universe(nio_data_path: Path) -> tuple[np.ndarray, np.ndarray, dict[int, int]]:
    dates = np.array(np.memmap(nio_data_path / DATES_FILE, mode="r", dtype=int))
    ins = np.array(np.memmap(nio_data_path / INSTRUMENTS_FILE, mode="r", dtype=np.dtype("U32")))
    return dates, ins, {int(d): i for i, d in enumerate(dates)}


def _read_delay(name: str, alpha_src: Path) -> int:
    """Read delay from meta.json. Defaults to 1 (legacy assumption) if missing/unreadable."""
    meta = alpha_src / name / "meta.json"
    if not meta.exists():
        warn(f"{name} meta.json 缺失,默认 delay=1")
        return 1
    try:
        return int(json.loads(meta.read_text()).get("delay", 1))
    except Exception as e:
        warn(f"{name} 读取 delay 失败 ({e}),默认 delay=1")
        return 1


def _offset_for_delay(delay: int) -> int:
    return 0 if delay == 0 else -1


def _iter_date_files(factor_dump_dir: Path):
    """Yield (date:int, version:str, path:Path) for each per-date npy under a factor."""
    if not factor_dump_dir.exists():
        return
    for year in factor_dump_dir.iterdir():
        if not year.is_dir():
            continue
        for month in year.iterdir():
            if not month.is_dir():
                continue
            for f in month.iterdir():
                if not f.name.endswith(".npy"):
                    continue
                stem = f.name[:-4]
                if len(stem) < 10:
                    continue
                try:
                    date = int(stem[:8])
                except ValueError:
                    continue
                version = stem[8:10]
                if version not in VERSIONS:
                    continue
                yield date, version, f


def _atomic_write_memmap(target: Path, ram: np.ndarray) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        mm = np.memmap(tmp, mode="w+", shape=ram.shape, dtype=ram.dtype)
        mm[:] = ram[:]
        mm.flush()
        del mm
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
        raise


def verify_sample(name: str, factor_dump_dir: Path, alpha_feature: Path,
                  date_to_idx: dict[int, int], shape: tuple[int, int],
                  delay: int) -> None:
    """Pick up to VERIFY_SAMPLES random dates that have source files, compare
    each source against the corresponding row in the target memmap. Raise on
    any mismatch.
    """
    offset = _offset_for_delay(delay)
    candidates = [c for c in _iter_date_files(factor_dump_dir)
                  if (di := date_to_idx.get(c[0])) is not None
                  and 0 <= di + offset < shape[0]]
    if not candidates:
        return
    picks = random.sample(candidates, min(VERIFY_SAMPLES, len(candidates)))

    mms: dict[tuple[str], np.memmap] = {}
    try:
        for date, version, src_path in picks:
            di = date_to_idx[date] + offset
            if version not in mms:
                mms[version] = np.memmap(alpha_feature / f"{name}.{version}.npy",
                                         mode="r", shape=shape, dtype=DTYPE)
            src = np.load(src_path)
            row = np.array(mms[version][di, :src.shape[0]])
            diff = np.abs(np.where(np.isnan(src) & np.isnan(row), 0.0, src - row))
            if not np.all(diff < ATOL):
                raise AssertionError(
                    f"sanity check failed: {name} {version} date={date} di={di} "
                    f"max_diff={float(np.nanmax(diff)):.3e}"
                )
    finally:
        for mm in mms.values():
            del mm


def pack_one(name: str, alpha_dump: Path, alpha_feature: Path,
             date_to_idx: dict[int, int], shape: tuple[int, int],
             delay: int, verify: bool = True) -> None:
    """Full rewrite: build RAM matrix from per-date files, write memmap atomically."""
    L, H = shape
    ram = {v: np.full((L, H), np.nan, dtype=DTYPE) for v in VERSIONS}
    offset = _offset_for_delay(delay)

    factor_dump_dir = alpha_dump / name
    for date, version, f in _iter_date_files(factor_dump_dir):
        di = date_to_idx.get(date)
        if di is None:
            continue
        di += offset
        if di < 0 or di >= L:
            continue
        arr = np.load(f)
        h0 = arr.shape[0]
        if h0 > H:
            raise ValueError(f"{name} {f.name} H={h0} > pack H={H}")
        ram[version][di, :h0] = arr

    for v in VERSIONS:
        _atomic_write_memmap(alpha_feature / f"{name}.{v}.npy", ram[v])

    if verify:
        verify_sample(name, factor_dump_dir, alpha_feature, date_to_idx, shape, delay)


def pack_one_incremental(name: str, dates: list[int], config: Config) -> None:
    """Cheap update: mmap the existing target('r+') and overwrite only the rows
    for `dates`. If the target doesn't exist, fall back to full pack_one.
    """
    nio = load_universe(config.nio_data_path)
    universe_dates, instruments, date_to_idx = nio
    shape = (PACK_L, len(instruments))
    delay = _read_delay(name, config.alpha_src)

    v1 = config.alpha_feature / f"{name}.v1.npy"
    v2 = config.alpha_feature / f"{name}.v2.npy"
    if not v1.exists() or not v2.exists():
        pack_one(name, config.alpha_dump, config.alpha_feature, date_to_idx, shape, delay)
        return

    factor_dump_dir = config.alpha_dump / name
    wanted = set(dates) if dates else None
    offset = _offset_for_delay(delay)

    mms = {v: np.memmap(config.alpha_feature / f"{name}.{v}.npy",
                        mode="r+", shape=shape, dtype=DTYPE) for v in VERSIONS}
    try:
        for date, version, f in _iter_date_files(factor_dump_dir):
            if wanted is not None and date not in wanted:
                continue
            di = date_to_idx.get(date)
            if di is None:
                continue
            di += offset
            if di < 0 or di >= PACK_L:
                continue
            arr = np.load(f)
            h0 = arr.shape[0]
            if h0 > shape[1]:
                raise ValueError(f"{name} {f.name} H={h0} > pack H={shape[1]}")
            mms[version][di, :h0] = arr
        for v in VERSIONS:
            mms[v].flush()
    finally:
        for mm in mms.values():
            del mm


def _list_dump_factors(alpha_dump: Path) -> list[str]:
    if not alpha_dump.exists():
        return []
    return sorted(d.name for d in alpha_dump.iterdir()
                  if d.is_dir() and d.name.startswith("Alpha"))


def _is_packed(name: str, alpha_feature: Path) -> bool:
    return (alpha_feature / f"{name}.v1.npy").exists() and \
           (alpha_feature / f"{name}.v2.npy").exists()


def _pack_worker(name: str, alpha_dump: Path, alpha_feature: Path,
                 date_to_idx: dict[int, int], shape: tuple[int, int],
                 delay: int, verify: bool) -> tuple[str, str, str]:
    """Returns (name, status, msg). status in {ok, locked, failed}."""
    try:
        with factor_lock(name):
            pack_one(name, alpha_dump, alpha_feature, date_to_idx, shape, delay, verify=verify)
        return (name, "ok", "")
    except FactorLocked:
        return (name, "locked", "held by another process")
    except Exception as e:
        return (name, "failed", str(e))


def run_pack(args):
    from pathlib import Path as _P
    config_path: _P = args.config_path
    factor_name: str | None = args.factor
    force: bool = args.force
    verify: bool = not args.no_verify
    workers: int = args.workers

    config = Config.load(config_path)
    config.alpha_feature.mkdir(parents=True, exist_ok=True)

    dates, ins, date_to_idx = load_universe(config.nio_data_path)
    shape = (PACK_L, len(ins))

    banner(f"因子打包 (L={shape[0]}, H={shape[1]})")

    if factor_name is not None:
        candidates = [factor_name]
    else:
        candidates = _list_dump_factors(config.alpha_dump)

    if not force:
        before = len(candidates)
        candidates = [n for n in candidates if not _is_packed(n, config.alpha_feature)]
        info(f"扫描 {before} 个因子,跳过已打包 {before - len(candidates)} 个,待处理 {len(candidates)}")
    else:
        info(f"扫描 {len(candidates)} 个因子 (--force)")

    if not candidates:
        bottom()
        return

    ok = locked = failed = 0
    failures: list[tuple[str, str]] = []

    workers = max(1, min(workers, len(candidates)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_pack_worker, n, config.alpha_dump, config.alpha_feature,
                               date_to_idx, shape, _read_delay(n, config.alpha_src), verify)
                   for n in candidates]
        total = len(futures)
        for i, fut in enumerate(as_completed(futures), 1):
            name, status, msg = fut.result()
            prefix = f"[{i:>{len(str(total))}}/{total}]"
            if status == "ok":
                ok += 1
                info(f"{prefix} ✔ {name}")
            elif status == "locked":
                locked += 1
                warn(f"{prefix} ⚠ {name} 占用,跳过")
            else:
                failed += 1
                failures.append((name, msg))
                error(f"{prefix} ✘ {name}: {msg}")

    banner("打包汇总")
    info(f"✔ 完成 : {ok:>4}")
    if locked:
        warn(f"⚠ 占用 : {locked:>4}")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for n, r in failures[:20]:
            error(f"  - {n}: {r}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
