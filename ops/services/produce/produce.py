"""`ops produce` —— 因子产线薄驱动(sync + run,docs/design/factor-produce-v3.md)。

归档 XML 即生产态(入库时已由 repo.productionize_src 写好):本命令**不改写
任何 XML**,只做两件事:

① sync:ACTIVE 集(SSOT)⇔ checkpoint 目录集 —— 离库因子的 checkpoint 归
   `.retired/`(dump/pnl 缺省不删);新线无需构建,首跑 gsim savedi=0 天然全段。
② run:逐因子 factor_lock → 锁内复验 ACTIVE → 直接跑 alpha_src 的归档 XML
   (run_cp.py checkpoint 续跑,重写尾部 ~5+N 天,dump/pnl 直落产线 dataset)。

无状态(dump/checkpoint 文件即记录)、幂等(重复跑 = 续跑收敛)。失败>0
退出码 1(cron 判据)。运行时点 = T 日盘前(build_cc 之后):T 日 dump 由
T-1 数据经 delay 算出,正是当天实盘目标仓位。产线路径为 170 本机事实,
在别机跑会因路径不存在响亮失败。
"""
from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from ops.core.dumpfiles import dump_dates
from ops.core.paths import FactorPaths
from ops.core.prodxml import ProdParams
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import FactorRepository
from ops.services._batch import confirm_or_abort
from ops.utils.log import logger
from ops.utils.printer import banner, bottom, error, info, warn
from ops.utils.xmlio import load_xml, save_xml


def _params_or_die(config: Config) -> ProdParams:
    params = ProdParams.maybe_from_config(config)
    if params is None:
        raise SystemExit(
            "ops produce: config 缺 produce: 块 —— 在 config.yaml 补齐"
            "(参考 template/config.yaml 与 docs/design/factor-produce-v3.md §7)")
    return params


def _active_names(config: Config, user: str | None) -> list[str]:
    """全量 ACTIVE 因子名。-u 过滤需要 identity(PG);无过滤时 json 后端走
    state store(sync 对账与控制流测试无需 PG)。"""
    repo = FactorRepository(config)
    try:
        return sorted(f.name for f in repo.find(status="active", author=user))
    except NotImplementedError:
        if user:
            raise SystemExit(
                "ops produce: -u 过滤需要 postgres state 后端") from None
        from ops.infra.store import default_store
        return sorted(r.name for r in default_store(config).list(
            status=FactorStatus.ACTIVE))


def _resolve_selection(config: Config, factors: list[str],
                       user: str | None) -> list[str]:
    if not factors:
        return _active_names(config, user)
    repo = FactorRepository(config)
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


# ---------------------------------------------------------------------------
# sync:ACTIVE 集 ⇔ checkpoint 目录集
# ---------------------------------------------------------------------------

def sync_lines(active: set[str], ck_root: Path) -> tuple[list[str], list[str]]:
    """返回 (retired, fresh)。停线 = checkpoint 有而 ACTIVE 无 → 移入
    .retired/(dump/pnl 不动 —— 破坏性回收永远显式);新线 = ACTIVE 有而
    checkpoint 无(无需构建,首跑天然全段)。

    只认 Alpha* 目录 —— checkpoint 根可能与其它产线(combo)或杂物同住,
    非因子命名的目录一概不碰。
    """
    retired: list[str] = []
    existing: set[str] = set()
    if ck_root.exists():
        for d in sorted(ck_root.iterdir()):
            if not d.is_dir() or not d.name.startswith("Alpha"):
                continue
            if d.name in active:
                existing.add(d.name)
                continue
            dest = ck_root / ".retired" / d.name
            dest.parent.mkdir(exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)     # 同名旧退休残留,新退休覆盖
            shutil.move(str(d), str(dest))
            retired.append(d.name)
    fresh = sorted(active - existing)
    return retired, fresh


# ---------------------------------------------------------------------------
# run:逐因子驱动
# ---------------------------------------------------------------------------

def _archived_xml(paths: FactorPaths) -> Path | None:
    xmls = sorted(paths.src.glob("*.xml"))
    return xmls[0] if xmls else None


def _is_production_form(xml_file: Path, params: ProdParams) -> bool:
    """轻量守卫:dump 落点指产线 dataset 才是生产态 —— 存量未迁移的归档 XML
    (拆雷态,输出指 /tmp)绝不能跑,产出会静默丢失。"""
    try:
        gsim = load_xml(xml_file)["gsim"]
        return gsim["Portfolio"]["Alpha"].get("@dumpAlphaDir") == params.dump_root
    except Exception:
        return False


def _produce_worker(name: str, config: Config, force: bool = False,
                    enddate: str | None = None,
                    backtest_fn=None) -> tuple[str, str, str]:
    """Returns (name, status, detail);
    status ∈ {ok, locked, skipped, unmigrated, failed}。"""
    fn = backtest_fn or Runner.run_backtest
    params = ProdParams.from_config(config)
    try:
        with factor_lock(name, config):
            # 锁内复验:排队期间可能被 restage/rm(worker 内 repo 现构造,
            # fork 子进程不共享父进程 PG 池,见 check.py::_repo)
            rec = FactorRepository(config).record(name)
            if rec is None or rec.status != FactorStatus.ACTIVE:
                got = rec.status.value if rec else "无记录"
                return (name, "skipped", f"状态已变: {got}")

            paths = FactorPaths.of(name, config)
            xml = _archived_xml(paths)
            if xml is None:
                return (name, "failed", f"alpha_src 无 XML: {paths.src}")
            if not _is_production_form(xml, params):
                return (name, "unmigrated",
                        "归档 XML 非生产态 —— 先跑 scripts/migrate_prod_xml.py")

            ck = Path(params.checkpoint_root) / name
            if force and ck.exists():
                shutil.rmtree(ck)       # 重跑 = 删 checkpoint,gsim 自然全段

            if enddate is not None:
                # 钉死日重算:临时副本改 enddate,checkpoint 换一次性目录 ——
                # 用生产 checkpoint 跑回头日期会把存档点拽回过去(污染日更续跑)
                with tempfile.TemporaryDirectory(prefix="ops-produce-") as td:
                    cfg = load_xml(xml)
                    cfg["gsim"]["Universe"]["@enddate"] = enddate
                    cfg["gsim"]["Constants"]["@checkpointDir"] = td + "/ckpt/"
                    tmp_xml = Path(td) / xml.name
                    save_xml(tmp_xml, cfg)
                    fn(tmp_xml, config)
            else:
                fn(xml, config)

            latest = max(dump_dates(Path(params.dump_root) / name), default=None)
            detail = f"dump 至 {latest}" if latest else "⚠ 产线 dump 目录为空"
            return (name, "ok", detail)
    except FactorLocked:
        return (name, "locked", "被另一个进程占用")
    except Exception as e:
        logger.exception("produce failed factor={}", name)
        return (name, "failed", str(e)[:300])


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_produce(args):
    config = Config.load(args.config_path)
    params = _params_or_die(config)

    factors: list[str] = list(getattr(args, "factors", []) or [])
    user: str | None = getattr(args, "user", None)
    force: bool = args.force
    dry_run: bool = getattr(args, "dry_run", False)
    sync_only: bool = getattr(args, "sync_only", False)
    yes: bool = getattr(args, "yes", False)
    workers: int = args.workers
    enddate: str | None = getattr(args, "enddate", None)

    if factors and user:
        error("ops produce: 显式因子与 -u/--user 不能同时给(语义歧义)")
        raise SystemExit(1)
    if force and not factors:
        error("ops produce: --force(删 checkpoint 全段重跑)必须显式点名因子,"
              "不接受无界作用域")
        raise SystemExit(1)
    if enddate is not None and not (len(enddate) == 8 and enddate.isdigit()):
        error(f"ops produce: --enddate 需要 YYYYMMDD,得到 {enddate!r}")
        raise SystemExit(1)

    banner("因子产线")

    # sync:全量 ACTIVE 对账(过滤模式不做停线 —— 定向跑不该有全局副作用)
    ck_root = Path(params.checkpoint_root)
    if not factors and not user:
        active_all = _active_names(config, None)
        retired, fresh = sync_lines(set(active_all), ck_root)
        for n in retired:
            warn(f"  ⊝ 停线(离库): {n} → checkpoint 归 .retired/")
        info(f"  产线同步: ACTIVE {len(active_all)},停线 {len(retired)},"
             f"新线(首跑全段) {len(fresh)}")
        selected = active_all
    else:
        info("  定向模式: 跳过停线对账")
        selected = _resolve_selection(config, factors, user)

    if sync_only:
        bottom()
        return
    if not selected:
        info("  没有可生产的 ACTIVE 因子")
        bottom()
        return

    if dry_run:
        for name in selected:
            paths = FactorPaths.of(name, config)
            xml = _archived_xml(paths)
            ck = (ck_root / name).exists()
            latest = max(dump_dates(Path(params.dump_root) / name), default=None)
            form = ("无XML" if xml is None
                    else "生产态" if _is_production_form(xml, params) else "未迁移")
            info(f"  - {name}: xml={form} checkpoint={'有' if ck else '新线'} "
                 f"dump至={latest or '—'}")
        bottom()
        return

    if force and not confirm_or_abort("全段重跑", len(selected), yes):
        return

    ok = locked = skipped = unmigrated = failed = 0
    failures: list[tuple[str, str]] = []
    total = len(selected)
    workers = max(1, min(workers, total))

    def _handle(res: tuple[str, str, str], i: int) -> None:
        nonlocal ok, locked, skipped, unmigrated, failed
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
        elif status == "unmigrated":
            unmigrated += 1
            error(f"{prefix} ✘ {name}: {detail}")
        else:
            failed += 1
            failures.append((name, detail))
            error(f"{prefix} ✘ {name}: {detail}")

    if workers == 1:
        # 串行进程内(单 worker 无并行收益;也是测试注入 fake 的路径)
        for i, name in enumerate(selected, 1):
            _handle(_produce_worker(name, config, force, enddate), i)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_produce_worker, name, config, force, enddate)
                       for name in selected]
            for i, fut in enumerate(as_completed(futures), 1):
                _handle(fut.result(), i)

    banner("产线汇总")
    info(f"✔ 续跑 : {ok:>4}")
    if locked:
        warn(f"⚠ 占用 : {locked:>4}")
    if skipped:
        warn(f"⚠ 跳过 : {skipped:>4}")
    if unmigrated:
        error(f"✘ 未迁移: {unmigrated:>4}(先跑 scripts/migrate_prod_xml.py)")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for n, r in failures[:20]:
            error(f"  - {n}: {r}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
    if failed or unmigrated:
        # cron 判据:有失败/未迁移必须非零退出
        raise SystemExit(1)
