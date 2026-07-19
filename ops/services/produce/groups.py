"""分组产线驱动(`ops produce --grouped`)—— 三态:组产 / 单产 / 待产。

状态模型(正主 `docs/design/factor-produce-groups.md`;可生产 = ACTIVE,
现行生产闸 delay==1):
  在产 = in-group(组产,roster 在 PG)+ single-factor(单产,注册表在 PG);
  pending(待产)= 可生产 − 在产 —— 纯推导,只报告**永不生产**(新到因子
  天然屏蔽)。状态转移:pending → 单产 = 显式点名(人工闸);→ 组产 =
  bootstrap 封组;组产 → 单产 = 代码漂移自动降级(产线连续性);
  单产重漂移 = 重冻结 + 删 checkpoint 全段重跑。准入幂等,重准入 = 重冻结。

sync(每次跑前,DB/XML/ACTIVE 三方收敛):
  ① 组:DB roster 序 == group.xml 腿序(不一致 = 现场被改过,跳过响亮报);
    腿不在 delay1 ACTIVE 或冻结副本漂移 → 静音;回库且代码一致 → 解除静音;
  ② 单产:离 ACTIVE → 注册移除(退回待产);冻结副本漂移 → 重冻结;
  ③ 待产 = delay1 ACTIVE − roster − 单产注册表,只计数报告。

run:逐组/逐单 run_cp.py checkpoint 续跑;组级锁 group:<gid>,单产锁因子名。
pre-check 只护组(组 = 故障域,单腿炸整组死);单产是大小为 1 的故障域,
失败自含。gsim 输出全量落盘(组 logs/ 与单产 logs/)。失败 >0 退出码 1。
"""
from __future__ import annotations

import filecmp
import shutil
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
    build_single_xml,
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
# 单产准入 / 重冻结
# ---------------------------------------------------------------------------

def _admit_single(name: str, repo: FactorRepository, params: GroupParams,
                  config: Config) -> None:
    """pending/drift → 单产准入:冻结副本 + 生成单产 XML + 注册。

    幂等:重准入 = 重冻结 + 重建 XML。checkpoint 默认保留(代码未变的重准入
    不该清状态);drift 重冻结见 _refresh_single —— 先删 checkpoint 再重准入,
    全段重跑。
    """
    factor = repo.get(name)
    if factor is None:
        raise SystemExit(f"单产准入: {name} 无记录")
    author = factor.identity.author or ""
    src = FactorPaths.of(name, config).src
    xmls = sorted(src.glob("*.xml"))
    if not xmls:
        raise SystemExit(f"单产准入: {name} alpha_src 无 XML({src})")
    sdir = Path(params.single_dir(author, name))
    (sdir / "checkpoint").mkdir(parents=True, exist_ok=True)
    (sdir / "logs").mkdir(parents=True, exist_ok=True)
    code = sdir / "code"
    if code.exists():
        shutil.rmtree(code)
    shutil.copytree(src, code)
    save_xml(sdir / f"{name}.xml",
             build_single_xml(load_xml(xmls[0]), params, author, name))
    repo.admit_single(name, author)


def _refresh_single(name: str, author: str, repo: FactorRepository,
                    params: GroupParams, config: Config) -> None:
    """单产代码漂移 = 重冻结:删 checkpoint(全段重跑)+ 重准入。"""
    ck = Path(params.single_checkpoint_dir(author, name))
    if ck.exists():
        shutil.rmtree(ck)
    _admit_single(name, repo, params, config)


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
    singles = {s.factor for s in repo.singles()}

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

        drifted = {f for f in db_order
                   if f in active_d1
                   and _code_drifted(gdir / "code" / f, FactorPaths.of(f, config).src)}
        desired_muted = {f for f in db_order if f not in active_d1 or f in drifted}
        to_mute = desired_muted - muted_db
        to_unmute = (muted_db - desired_muted) & active_d1
        # 漂移降级:仍在 ACTIVE 但代码漂移 → 组内静音 + 转单产(自动,产线连续性)
        for f in sorted(drifted - singles):
            _admit_single(f, repo, params, config)
            singles.add(f)
            notes.append(f"↓ {f}: 代码漂移,组内静音并转单产")
        # 回组(unmute):若曾在单产,注册移除 —— 一个因子只有一个生产之家
        for f in sorted(to_unmute & singles):
            repo.remove_single(f)
            singles.discard(f)
            notes.append(f"↑ {f}: 回组生产,单产注册移除")
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

    # 单产收敛:离 ACTIVE 移除;漂移重冻结(刚准入的已是最新,跳过)
    for s in repo.singles():
        if s.factor not in active_d1:
            repo.remove_single(s.factor)
            notes.append(f"⊝ {s.factor}: 不在 delay1 ACTIVE,单产注册移除(退回待产)")
            continue
        if s.factor in singles and _code_drifted(
                Path(params.single_dir(s.author, s.factor)) / "code",
                FactorPaths.of(s.factor, config).src):
            _refresh_single(s.factor, s.author, repo, params, config)
            notes.append(f"↻ {s.factor}: 代码漂移,单产重冻结(全段重跑)")

    pending = sorted(n for n in active_d1
                     if n not in membership and n not in singles)
    return notes, corrupted, pending


# ---------------------------------------------------------------------------
# run(组 / 单产;worker 须顶层,ProcessPool 要 pickle)
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


def _run_single(name: str, author: str, config: Config,
                timeout: int | None = None) -> tuple[str, str, str]:
    """单产续跑(单 <Alpha> 形态;故障域 = 自己)。status 同 _run_group。"""
    params = _params_of(config)
    sdir = Path(params.single_dir(author, name))
    try:
        with factor_lock(name, config):
            rec = FactorRepository(config).record(name)
            if rec is None or rec.status != FactorStatus.ACTIVE:
                got = rec.status.value if rec else "无记录"
                return (name, "skipped", f"状态已变: {got}")
            xml = sdir / f"{name}.xml"
            if not xml.is_file():
                return (name, "failed", f"单产 XML 缺失({xml}),未准入?")
            log = sdir / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}.log"
            Runner.run_backtest(xml, config, timeout=timeout, log_path=log)
            latest = max(dump_dates(Path(params.dump_root) / name), default=None)
            detail = f"dump 至 {latest}" if latest else "⚠ dump 目录为空"
            return (name, "ok", detail)
    except FactorLocked:
        return (name, "locked", "被另一个进程占用")
    except Exception as e:
        logger.exception("produce single failed factor={}", name)
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

    groups_only: bool = getattr(args, "groups_only", False)
    single_only: list[str] | None = getattr(args, "single_only", None)
    if groups_only and single_only is not None:
        error("ops produce --grouped: --groups-only 与 --single-only 语义互斥")
        raise SystemExit(1)

    # 共享产物根的叶子必须预先存在:gsim Stats 对 pnlDir 是"不存在才 makedirs",
    # 多组并行首跑会撞 FileExistsError 竞态(170 试点 g001/g002 实测)
    for d in (params.dump_root, params.pnl_root):
        Path(d).mkdir(parents=True, exist_ok=True)

    banner("分组产线")

    notes, corrupted, pending = sync_groups(repo, params, config)
    for n in notes:
        (error if n.startswith("✘") else warn)(f"  {n}")

    registered = {s.factor: s.author for s in repo.singles()}
    run_groups = single_only is None
    run_singles: list[tuple[str, str]] = []
    if single_only is not None:
        # --single-only:无名字 = 全部注册单产;有名字 = 点名的(pending 先准入)
        for n in (single_only or sorted(registered)):
            if n in registered:
                run_singles.append((n, registered[n]))
            elif n in pending:
                _admit_single(n, repo, params, config)
                factor = repo.get(n)
                author = (factor.identity.author or "") if factor else ""
                registered[n] = author
                run_singles.append((n, author))
                info(f"  + {n}: 准入单产(pending → single)")
            else:
                warn(f"  ⚠ {n}: 非 delay1 ACTIVE,跳过")
    elif not groups_only:
        run_singles = sorted(registered.items())

    groups = [g for g in repo.groups() if g.gid not in corrupted] if run_groups else []
    info(f"  同步: 组 {len(repo.groups())}(跑 {len(groups)},跳过 {len(corrupted)}) | "
         f"单产 {len(run_singles)} | 待产 {len(pending)}(不生产)")
    if getattr(args, "sync_only", False):
        bottom()
        return

    # pre-check 只护组(组 = 故障域):坏腿自动静音
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
        for name, author in run_singles:
            sdir = Path(params.single_dir(author, name))
            ck = (sdir / "checkpoint" / "archive.bin").exists()
            info(f"  - [s] {name}: checkpoint={'有' if ck else '首跑全史'}")
        bottom()
        return

    total = len(runnable) + len(run_singles)
    if not total:
        info("  没有可生产的组或单产因子")
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
        for name, author in run_singles:
            i += 1
            _handle("[s] ", _run_single(name, author, config, timeout), i)
    else:
        futures: dict = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for gid, author in runnable:
                futures[pool.submit(_run_group, gid, author, config, timeout)] = ""
            for name, author in run_singles:
                futures[pool.submit(_run_single, name, author, config, timeout)] = "[s] "
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
