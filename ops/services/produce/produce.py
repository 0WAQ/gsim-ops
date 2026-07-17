"""`ops produce` 编排:在库(ACTIVE)因子的 alpha_dump 日增生产。

无状态命令:不写 factor_history(每日全库会产生海量噪音事件)、不 transition
—— dump 文件本身即记录,幂等重跑收敛。dump 是本机 sidecar,在哪台跑就落哪台
(生产消费机 = 170)。

每因子流程(worker,factor_lock 内):锁内复验 ACTIVE → wipe 工作区 → 拷 src
副本 → 改 XML(xml_prepare)→ gsim 跑缺失段 → 只把缺失日原子安装进 sidecar。
段内已存在的完整日重算了也丢弃 —— 缺省绝不覆盖已有数据;--force 显式作用域
+ 确认后 wanted = 全窗口,覆盖即重产。

已知语义(写给后来人):restage → 重过 check 会把 dump 整目录换成本次 check
的 ≤20251231 产物,2026+ 段被抹掉是归档的既有行为 —— 下次 produce 视为缺失
自动整段重填,无需特判。
"""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from ops.core.dumpfiles import dump_dates, iter_dump_files, month_dir
from ops.core.paths import FactorPaths
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import FactorRepository
from ops.services._batch import confirm_or_abort
from ops.services.produce.dates import (
    ProduceError,
    missing_dates,
    resolve_axis,
    resolve_target,
    window_dates,
)
from ops.services.produce.xml_prepare import prepare_produce_xml
from ops.utils.factor_dir import clean_pycache, rewrite_module_path
from ops.utils.log import logger
from ops.utils.printer import banner, bottom, error, info, warn


def _install(produced_dir: Path, dump_dir: Path,
             wanted: set[int]) -> tuple[list[int], list[int]]:
    """把工作区产出中 wanted 日期的 dump 安装进 sidecar,返回
    (installed_dates, all_nan_dates)。

    覆盖策略由 wanted 承载:缺省模式 wanted = 缺失集(既有日根本不在集合里,
    绝不触碰);--force 模式 wanted = 全窗口(覆盖即重产)。跨文件系统
    (工作区 → sidecar)move 非原子,统一 tmp + os.replace;逐日先 v1 后 v2,
    中断残留被 require_both 口径收敛为"缺失"。"""
    by_date: dict[int, list[tuple[str, Path]]] = {}
    for date, version, f in iter_dump_files(produced_dir):
        if date in wanted:
            by_date.setdefault(date, []).append((version, f))

    installed: list[int] = []
    nan_dates: list[int] = []
    for date in sorted(by_date):
        files = sorted(by_date[date])          # v1 先于 v2
        target_dir = month_dir(dump_dir, date)
        target_dir.mkdir(parents=True, exist_ok=True)
        all_nan = True
        for _version, src in files:
            arr = np.load(src)
            if arr.size and not bool(np.isnan(arr).all()):
                all_nan = False
            tmp = target_dir / f".{src.name}.tmp"
            shutil.copyfile(src, tmp)
            os.replace(tmp, target_dir / src.name)
        installed.append(date)
        if all_nan:
            nan_dates.append(date)             # 无效日合法(compliance 同语义),计数供 warn
    return installed, nan_dates


def _produce_one(name: str, dates: list[int], config: Config,
                 backtest_fn) -> tuple[list[int], list[int]]:
    """单因子:工作区副本 → gsim 跑段 → 安装。返回 (installed, all_nan)。"""
    paths = FactorPaths.of(name, config)
    ws = config.produce_workspace
    assert ws is not None
    src_work = ws / "src" / name
    dump_root = ws / "alpha"                   # gsim 在其下自建 <@id>/YYYY/MM/
    produced = dump_root / name
    ckpt = ws / "checkpoint" / name
    pnl_dir = ws / "pnl"

    # 开跑先 wipe(上次失败的残场保留到此刻供排查);checkpoint 必须全新 ——
    # 陈旧 checkpoint 会被 gsim load 直接崩
    for d in (src_work, produced, ckpt):
        shutil.rmtree(d, ignore_errors=True)
    for d in (dump_root, ckpt, pnl_dir):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copytree(paths.src, src_work)
    clean_pycache(src_work)
    rewrite_module_path(src_work)              # @module → 工作区副本的 .py
    xml = prepare_produce_xml(
        src_work, start=dates[0], end=dates[-1],
        nio_root=config.produce_nio_data_path, dump_root=dump_root,
        pnl_dir=pnl_dir, checkpoint_dir=ckpt)

    backtest_fn(xml, config)

    installed, nan_dates = _install(produced, paths.dump, set(dates))
    # 安装成功后清工作区(失败路径不清,残场留给排查,下次开跑 wipe)
    for d in (src_work, produced, ckpt):
        shutil.rmtree(d, ignore_errors=True)
    return installed, nan_dates


def _produce_worker(name: str, dates: list[int], config: Config,
                    backtest_fn=None) -> tuple[str, str, str]:
    """Returns (name, status, detail). status ∈ {ok, locked, skipped, failed}。"""
    fn = backtest_fn or Runner.run_backtest
    try:
        with factor_lock(name, config):
            # 锁内复验:排队/确认期间可能被 restage/rm(worker 内 repo 现构造,
            # fork 子进程不得共享父进程 PG 池,见 check.py::_repo)
            rec = FactorRepository(config).record(name)
            if rec is None or rec.status != FactorStatus.ACTIVE:
                got = rec.status.value if rec else "无记录"
                return (name, "skipped", f"状态已变: {got}")
            installed, nan_dates = _produce_one(name, dates, config, fn)
            if not installed:
                return (name, "failed",
                        f"gsim 零产出:请求 {dates[0]}..{dates[-1]} 共 {len(dates)} 天")
            detail = f"+{len(installed)} 天 ({installed[0]}..{installed[-1]})"
            shortfall = len(dates) - len(installed)
            if shortfall:
                detail += f" ⚠ 少产 {shortfall} 天"
            if nan_dates:
                detail += f" ⚠ 全 NaN {len(nan_dates)} 天"
            return (name, "ok", detail)
    except FactorLocked:
        return (name, "locked", "被另一个进程占用")
    except Exception as e:
        logger.exception("produce failed factor={}", name)
        return (name, "failed", str(e)[:300])


def _require_produce_config(config: Config) -> None:
    missing = [k for k, v in (
        ("produce.nio_data_path", config.produce_nio_data_path),
        ("produce.production_start", config.production_start),
        ("produce.workspace", config.produce_workspace),
    ) if v is None]
    if missing:
        raise SystemExit(
            f"ops produce: config 缺 {', '.join(missing)} —— 在 config.yaml 加 "
            "produce: 块(参考 template/config.yaml)")


def _parse_yyyymmdd(val: str | None, flag: str) -> int | None:
    if val is None:
        return None
    if not (len(val) == 8 and val.isdigit()):
        raise ProduceError(f"{flag} 需要 YYYYMMDD,得到 {val!r}")
    return int(val)


def _select_names(config: Config, factors: list[str], user: str | None,
                  ) -> list[str]:
    """定候选因子集。显式点名走 record 逐个验(json 后端也可用);批量走
    find(status='active')(PG)。"""
    repo = FactorRepository(config)
    if factors:
        names = []
        for name in factors:
            rec = repo.record(name)
            if rec is None:
                warn(f"  ⚠ {name} 无记录,跳过")
            elif rec.status != FactorStatus.ACTIVE:
                warn(f"  ⚠ {name} 状态 {rec.status.value}(非 active),跳过")
            else:
                names.append(name)
        return names
    try:
        return sorted(f.name for f in repo.find(status="active", author=user))
    except NotImplementedError:
        raise SystemExit(
            "ops produce: 批量模式需要 postgres state 后端;json dev/test 后端"
            "请显式点名因子") from None


def run_produce(args):
    config = Config.load(args.config_path)
    _require_produce_config(config)

    factors: list[str] = list(getattr(args, "factors", []) or [])
    user: str | None = getattr(args, "user", None)
    force: bool = args.force
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)
    workers: int = args.workers

    try:
        explicit_date = _parse_yyyymmdd(getattr(args, "date", None), "--date")
        explicit_start = _parse_yyyymmdd(getattr(args, "start", None), "--start")
        production_start = int(config.production_start)

        # --force 是覆盖已有 dump 的破坏性重产:必须显式锚定作用域(--date,
        # 可选 --start 扩成区间),不许"force 补到最新"这种无界作用域
        if force and explicit_date is None:
            raise ProduceError("--force 必须显式给 --date(单日)或 --start + --date(区间)")
        if explicit_start is not None and not force:
            raise ProduceError("--start 仅与 --force 连用(缺省模式起点自动推导)")
        if explicit_start is not None and explicit_start < production_start:
            raise ProduceError(
                f"--start {explicit_start} 早于生产起点 {production_start} —— "
                "2025 及以前是 check 流程的产物,produce 永不触碰")
        if factors and user:
            raise ProduceError("显式因子与 -u/--user 不能同时给(语义歧义)")

        axis, latest_ready = resolve_axis(config)
        target = resolve_target(axis, latest_ready, explicit_date)
    except ProduceError as e:
        error(f"ops produce: {e}")
        raise SystemExit(1) from None

    start = explicit_start if explicit_start is not None else (
        explicit_date if force else production_start)
    window = window_dates(axis, start, target)

    banner(f"因子增量生产 (目标日 {target})")
    names = _select_names(config, factors, user)
    if not names:
        info("  没有可生产的 ACTIVE 因子")
        bottom()
        return

    # 缺失推导(--force 无视已有 = 重产;缺省 require_both,半日按缺失计)
    work: list[tuple[str, list[int]]] = []
    up_to_date = 0
    for name in names:
        if force:
            todo = list(window)
        else:
            existing = dump_dates(FactorPaths.of(name, config).dump,
                                  require_both=True)
            todo = missing_dates(window, existing)
        if todo:
            work.append((name, todo))
        else:
            up_to_date += 1

    info(f"  ACTIVE {len(names)} 个:待生产 {len(work)},已最新 {up_to_date}")
    if not work:
        bottom()
        return

    if dry_run:
        for name, todo in work:
            info(f"  - {name}: 缺 {len(todo)} 天 ({todo[0]}..{todo[-1]})")
        bottom()
        return

    if force and not confirm_or_abort("重产", len(work), yes):
        return

    ok = locked = skipped = failed = 0
    failures: list[tuple[str, str]] = []
    workers = max(1, min(workers, len(work)))
    total = len(work)

    def _handle(res: tuple[str, str, str], i: int) -> None:
        nonlocal ok, locked, skipped, failed
        name, status, detail = res
        prefix = f"[{i:>{len(str(total))}}/{total}]"
        if status == "ok":
            ok += 1
            info(f"{prefix} ✔ {name}  {detail}")
        elif status == "locked":
            locked += 1
            warn(f"{prefix} ⚠ {name} {detail},跳过")
        elif status == "skipped":
            skipped += 1
            warn(f"{prefix} ⚠ {name} 跳过: {detail}")
        else:
            failed += 1
            failures.append((name, detail))
            error(f"{prefix} ✘ {name}: {detail}")

    if workers == 1:
        # 串行走进程内(测试注入 fake backtest_fn 的路径;单 worker 无并行收益)
        for i, (name, todo) in enumerate(work, 1):
            _handle(_produce_worker(name, todo, config), i)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_produce_worker, name, todo, config)
                       for name, todo in work]
            for i, fut in enumerate(as_completed(futures), 1):
                _handle(fut.result(), i)

    banner("生产汇总")
    info(f"✔ 生产 : {ok:>4}")
    if up_to_date:
        info(f"⊝ 已最新: {up_to_date:>4}")
    if locked:
        warn(f"⚠ 占用 : {locked:>4}")
    if skipped:
        warn(f"⚠ 跳过 : {skipped:>4}")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for n, r in failures[:20]:
            error(f"  - {n}: {r}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
    if failed:
        # 未来 cron 的退出码语义(doctor FAIL→1 同例):有失败必须非零
        raise SystemExit(1)
