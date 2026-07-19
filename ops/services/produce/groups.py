"""分组产线驱动(`ops produce --grouped`)—— sync + pre-check + run。

设计正主 `docs/design/factor-produce-groups.md`,不变量在 `ops/core/prodgroup.py`
(roster/顺序冻结,唯一合法编辑 = 单腿 `dumpAlphaFile` 属性翻转)。组拓扑的
SSOT 是 PG 两表(roster/ordinal/muted);盘面 group.xml 是**派生物**——sync 的
职责就是让 XML 收敛到 DB 状态,并校验"DB 序 == XML 腿序"这条承重不变量。

sync(每次跑前):
  ① 不变量校验:DB roster 序 == group.xml 腿序;不一致 = 现场被手改过,
    该组跳过并响亮报(绝不带病跑 —— 序号错位 = 静默污染);
  ② 静音收敛:腿不在 delay1 ACTIVE、或冻结副本与 alpha_src 漂移(= 重入库)
    → 置 muted;回库且代码一致的腿解除静音(muted 只翻属性,序号不动);
  ③ pending:delay1 ACTIVE 不在任何 active 组 → pending 池(per-factor 跑,
    临时副本把 dump/pnl/checkpoint 指到新根)。
pre-check:未静音腿的冻结 .py 存在 + py_compile 可编译;坏腿自动静音(gsim
无腿级容错,一条坏腿整组死)。只能查语法/存在性,import 级错误由首跑暴露,
故障域限于单组。
run:逐组 `factor_lock(group:<gid>)` → run_cp.py checkpoint 续跑;首跑
savedi=0 天然全史 = bootstrap。单组失败不阻断;失败 >0 退出码 1(cron 判据)。
"""
from __future__ import annotations

import filecmp
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from ops.core.dumpfiles import dump_dates
from ops.core.paths import FactorPaths
from ops.core.prodgroup import (
    ONLY_DELAY,
    GroupParams,
    as_list,
    group_legs,
    mute_legs,
)
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import FactorRepository
from ops.utils.log import logger
from ops.utils.printer import banner, bottom, error, info, warn
from ops.utils.xmlio import load_xml, save_xml


def _params_or_die(config: Config) -> GroupParams:
    params = GroupParams.maybe_from_config(config)
    if params is None:
        raise SystemExit(
            "ops produce --grouped: config 缺 produce.grouped.root —— "
            "在 config.yaml 补齐(参考 template/config.yaml)")
    return params


def _params_of(config: Config) -> GroupParams:
    """worker 内取参(入口已校验过存在性;fork 子进程拿不到父进程的局部量)。"""
    params = GroupParams.maybe_from_config(config)
    assert params is not None, "produce.grouped.root 未配置"
    return params


def _active_delay1(repo: FactorRepository) -> set[str]:
    return {f.name for f in repo.find(status="active")
            if f.snapshot and f.snapshot.delay == ONLY_DELAY}


def _code_drifted(frozen_dir: Path, live_dir: Path) -> bool:
    """冻结副本 vs alpha_src 活代码:.py 集合或内容任一不同 = 漂移(= 重入库)。
    只比 .py(XML/meta 会被生产化/生命周期改写,不构成代码漂移)。"""
    if not live_dir.is_dir():
        return True                       # alpha_src 目录消失 = rm,按漂移静音
    frozen = {p.name for p in frozen_dir.glob("*.py")}
    live = {p.name for p in live_dir.glob("*.py")}
    if frozen != live:
        return True
    return any(not filecmp.cmp(frozen_dir / n, live_dir / n, shallow=False)
               for n in frozen)


def _precheck_leg(code_file: Path) -> str | None:
    """返回 None = 通过;否则失败原因。只查存在性 + 语法(import 级首跑暴露)。
    用内建 compile() 而非 py_compile:不落任何字节码(冻结副本目录保持干净)。"""
    if not code_file.is_file():
        return f"冻结副本缺失: {code_file}"
    try:
        compile(code_file.read_text(encoding="utf-8"), str(code_file), "exec")
    except SyntaxError as e:
        return f"语法错误: {str(e)[:120]}"
    return None


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def sync_groups(repo: FactorRepository, params: GroupParams, config: Config,
                ) -> tuple[list[str], list[str], list[str]]:
    """DB/XML/ACTIVE 三方收敛。返回 (notes, corrupted_gids, pending_names)。"""
    notes: list[str] = []
    corrupted: list[str] = []
    active_d1 = _active_delay1(repo)
    membership = repo.group_membership()

    for g in repo.groups():
        gdir = Path(params.group_dir(g.author, g.gid))
        xml_file = gdir / "group.xml"
        members = repo.group_members(g.gid)
        db_order = [m.factor for m in members]           # 按 ordinal 排好
        muted_db = {m.factor for m in members if m.muted}
        if not xml_file.is_file():
            corrupted.append(g.gid)
            notes.append(f"✘ {g.gid}({g.author}): group.xml 缺失({xml_file}),跳过")
            continue
        cfg = load_xml(xml_file)
        if group_legs(cfg) != db_order:
            corrupted.append(g.gid)
            notes.append(f"✘ {g.gid}({g.author}): DB 序 ≠ XML 腿序(现场被改过?),跳过")
            continue

        desired_muted = {f for f in db_order
                         if f not in active_d1
                         or _code_drifted(gdir / "code" / f,
                                          FactorPaths.of(f, config).src)}
        # 回库且代码一致的腿允许解除静音(muted 是属性,序号未动,checkpoint 安全)
        to_mute = desired_muted - muted_db
        to_unmute = (muted_db - desired_muted) & active_d1
        if to_mute:
            repo.set_group_muted(g.gid, to_mute, True)
            mute_legs(cfg, to_mute, mute=True)
        if to_unmute:
            repo.set_group_muted(g.gid, to_unmute, False)
            mute_legs(cfg, to_unmute, mute=False)
        if to_mute or to_unmute:
            save_xml(xml_file, cfg)
            notes.append(f"⊘ {g.gid}({g.author}): 静音 {sorted(to_mute) or '—'}"
                         f" / 解除 {sorted(to_unmute) or '—'}")

    pending = sorted(n for n in active_d1 if n not in membership)
    return notes, corrupted, pending


# ---------------------------------------------------------------------------
# run(组 / pending;worker 须顶层,ProcessPool 要 pickle)
# ---------------------------------------------------------------------------

def _run_group(gid: str, author: str, config: Config,
               timeout: int | None = None) -> tuple[str, str, str]:
    """Returns (gid, status, detail);status ∈ {ok, locked, failed}。"""
    params = _params_of(config)
    gdir = Path(params.group_dir(author, gid))
    try:
        with factor_lock(f"group:{gid}", config):
            t0 = time.monotonic()
            log = gdir / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}.log"
            Runner.run_backtest(gdir / "group.xml", config, timeout=timeout,
                                log_path=log)
            return (gid, "ok", f"{time.monotonic() - t0:.0f}s, log {log}")
    except FactorLocked:
        return (gid, "locked", "被另一个进程占用")
    except Exception as e:
        logger.exception("produce group failed gid={}", gid)
        msg = str(e)
        return (gid, "failed", ("…" + msg[-300:]) if len(msg) > 300 else msg)


def _run_pending(name: str, config: Config,
                 timeout: int | None = None) -> tuple[str, str, str]:
    """pending 池 per-factor:临时副本把 dump/pnl/checkpoint 指到新根
    (归档 XML 指旧 dataset —— 绝不能直跑,产出会落进 cchang 侧)。"""
    params = _params_of(config)
    try:
        with factor_lock(name, config):
            rec = FactorRepository(config).record(name)
            if rec is None or rec.status != FactorStatus.ACTIVE:
                got = rec.status.value if rec else "无记录"
                return (name, "skipped", f"状态已变: {got}")
            xmls = sorted(FactorPaths.of(name, config).src.glob("*.xml"))
            if not xmls:
                return (name, "failed", "alpha_src 无 XML")
            ck = Path(params.pending_checkpoint_root) / name
            ck.mkdir(parents=True, exist_ok=True)
            log = Path(params.pending_log_root) / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.log"
            with tempfile.TemporaryDirectory(prefix="ops-produce-pending-") as td:
                cfg = load_xml(xmls[0])
                cfg["gsim"]["Constants"]["@checkpointDir"] = str(ck) + "/"
                cfg["gsim"]["Portfolio"]["Alpha"]["@dumpAlphaDir"] = params.dump_root
                cfg["gsim"]["Portfolio"]["Stats"]["@pnlDir"] = params.pnl_root
                tmp_xml = Path(td) / xmls[0].name
                save_xml(tmp_xml, cfg)
                Runner.run_backtest(tmp_xml, config, timeout=timeout, log_path=log)
            latest = max(dump_dates(Path(params.dump_root) / name), default=None)
            detail = f"dump 至 {latest}" if latest else "⚠ dump 目录为空"
            return (name, "ok", detail)
    except FactorLocked:
        return (name, "locked", "被另一个进程占用")
    except Exception as e:
        logger.exception("produce pending failed factor={}", name)
        msg = str(e)
        return (name, "failed", ("…" + msg[-300:]) if len(msg) > 300 else msg)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_produce_groups(args) -> None:
    config = Config.load(args.config_path)
    if config.env_overrides:
        warn(f"⚠ OPS_* 环境变量覆盖生效: {', '.join(config.env_overrides)} "
             "(hosts 声明被压掉;确认有意为之,否则 unset 后重跑)")
    params = _params_or_die(config)
    repo = FactorRepository(config)
    timeout: int | None = getattr(args, "timeout", None)

    # 产线三根的叶子必须预先存在:gsim Stats 对 pnlDir 是"不存在才 makedirs",
    # 多组并行首跑会撞 FileExistsError 竞态(170 试点 g001/g002 实测)
    for d in (params.dump_root, params.pnl_root, params.pending_checkpoint_root):
        Path(d).mkdir(parents=True, exist_ok=True)

    banner("分组产线")

    notes, corrupted, pending = sync_groups(repo, params, config)
    for n in notes:
        (error if n.startswith("✘") else warn)(f"  {n}")
    if getattr(args, "pending_only", False) and getattr(args, "skip_pending", False):
        error("ops produce --grouped: --pending-only 与 --skip-pending 语义互斥")
        raise SystemExit(1)
    if getattr(args, "skip_pending", False):
        if pending:
            info(f"  pending {len(pending)} 个按 --skip-pending 跳过")
        pending = []
    groups = [] if getattr(args, "pending_only", False) else \
        [g for g in repo.groups() if g.gid not in corrupted]
    info(f"  同步: 组 {len(repo.groups())}(跳过 {len(corrupted)}) | "
         f"pending {len(pending)}")
    if getattr(args, "sync_only", False):
        bottom()
        return

    # pre-check:坏腿自动静音(单腿炸 = 整组死,不能带跑)
    runnable: list[tuple[str, str]] = []          # (gid, author)
    for g in groups:
        gdir = Path(params.group_dir(g.author, g.gid))
        cfg = load_xml(gdir / "group.xml")
        bad: dict[str, str] = {}
        for leg in as_list(cfg["gsim"]["Portfolio"].get("Alpha")):
            if leg.get("@dumpAlphaFile") != "true":
                continue
            fid = str(leg.get("@id"))
            pys = sorted((gdir / "code" / fid).glob("*.py"))
            if not pys:
                bad[fid] = f"冻结副本无 .py: {gdir / 'code' / fid}"
                continue
            for py in pys:
                err = _precheck_leg(py)
                if err:
                    bad[fid] = err
                    break
        if bad:
            repo.set_group_muted(g.gid, set(bad), True)
            mute_legs(cfg, set(bad), mute=True)
            save_xml(gdir / "group.xml", cfg)
            for f, err in bad.items():
                warn(f"  ⚠ {g.gid}: {f} 自动静音({err})")
        runnable.append((g.gid, g.author))

    if getattr(args, "dry_run", False):
        for gid, author in runnable:
            gdir = Path(params.group_dir(author, gid))
            cfg = load_xml(gdir / "group.xml")
            legs = as_list(cfg["gsim"]["Portfolio"].get("Alpha"))
            muted_n = sum(1 for a in legs if a.get("@dumpAlphaFile") != "true")
            ck = (gdir / "checkpoint" / "archive.bin").exists()
            info(f"  - {gid}({author}): 腿 {len(legs)}(静音 {muted_n}) "
                 f"checkpoint={'有' if ck else '首跑全史'}")
        bottom()
        return

    total = len(runnable) + len(pending)
    if not total:
        info("  没有可生产的组或 pending 因子")
        bottom()
        return

    workers = max(1, min(args.workers, total))
    ok = locked = failed = 0
    failures: list[tuple[str, str]] = []

    def _handle(tag: str, res: tuple[str, str, str], i: int) -> None:
        nonlocal ok, locked, failed
        name, status, detail = res
        prefix = f"[{i:>{len(str(total))}}/{total}]"
        if status == "ok":
            ok += 1
            info(f"{prefix} ✔ {tag}{name}  {detail}")
        elif status == "locked":
            locked += 1
            warn(f"{prefix} ⚠ {tag}{name} {detail},跳过")
        elif status == "skipped":
            warn(f"{prefix} ⚠ {tag}{name} 跳过: {detail}")
        else:
            failed += 1
            failures.append((f"{tag}{name}", detail))
            error(f"{prefix} ✘ {tag}{name}: {detail}")

    if workers == 1:
        # 串行进程内(单 worker 无并行收益;也是测试注入 fake 的路径)
        i = 0
        for gid, author in runnable:
            i += 1
            _handle("", _run_group(gid, author, config, timeout), i)
        for name in pending:
            i += 1
            _handle("[p] ", _run_pending(name, config, timeout), i)
    else:
        futures: dict = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for gid, author in runnable:
                futures[pool.submit(_run_group, gid, author, config, timeout)] = ""
            for name in pending:
                futures[pool.submit(_run_pending, name, config, timeout)] = "[p] "
            for i, fut in enumerate(as_completed(futures), 1):
                _handle(futures[fut], fut.result(), i)

    banner("产线汇总")
    info(f"✔ 成功 : {ok:>4}")
    if locked:
        warn(f"⚠ 占用 : {locked:>4}")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for n, r in failures[:20]:
            error(f"  - {n}: {r}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
    if failed:
        raise SystemExit(1)
