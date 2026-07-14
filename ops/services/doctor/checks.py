"""doctor 检查族注册表 —— 盘 ↔ PG 对账的应然形态(SSOT)。

每族一个 `DoctorFamily`(scan 纯函数判定,fixer 可选)。新增族 = 表里加一行,
与 check 流水线 PIPELINE / setup CHECKS 同款模式。severity 在 kind 级。

**立场**:缺省纯只读;修复只上三类低爆炸半径动作(池鬼影 unlink / 非法快照
discard / 本机 dump 孤儿 rmtree + pack tmp 残渣 unlink),其余 report-only +
转介既有命令 —— doctor 不复制 clear/cancel/rm 的第二套删除逻辑,**绝不造
数据、绝不碰 ACTIVE 因子产物、绝不碰 alpha_src**。

**显式拒收(防回潮)**:
- "ACTIVE 缺 dump"检查:dump 产在消费机(170),本机看不到 ≠ 不存在 ——
  在 160 跑会对几乎全部 ACTIVE 因子误报。整条不做,直到 PG 记录产物所在
  host(挂账)。
- dump 日期缺口:需 25s 深扫 + 交易日历,且无修复原语、无具名消费方。
- 任何"补数据"类 fix(补池副本/补 state/伪造快照):approve 豁免因子合法
  无池副本、info 孤儿修复方向有歧义 —— doctor 只删漂移物和指路。
- ACTIVE 因子 snapshot_at != entered_at 的时间戳修正:归一次性
  scripts/postgres/migrate_snapshot_at.py(以 doctor JSON 为名单),
  不给 doctor 开 UPDATE 口子(快照不可变语义)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ops.core.state import FactorStatus

from .findings import FAIL, WARN, FamilySkip, Finding, FixPlan, Inventory

# pack 原子写残渣:.<name>.v2.npy.tmp(pack.py `.{target.name}.tmp`);
# 超过该龄期仍在 = worker 崩溃遗留(在跑的 pack 不会留这么久)。
PACK_TMP_MAX_AGE_S = 24 * 3600


@dataclass(frozen=True)
class Fixer:
    """族的修复声明。实际执行全部经 guards.execute(五道闸唯一出口)。

    recheck 在 factor_lock 锁内重验漂移仍成立(TOCTOU 双钥:扫描→人工确认→
    执行的分钟级窗口里,因子可能已 restage → 重检 → 重新 ACTIVE)。
    """
    plan: FixPlan
    # (finding, config) -> Path(unlink/rmtree)| str 因子名(discard_snapshot)
    resolve: Callable
    # (finding, factor: Factor | None) -> bool;factor 是锁内 repo.get 的新读
    recheck: Callable
    # (config) -> tuple[Path, ...] 允许的删除根(realpath 包含校验白名单)
    allowed_roots: Callable = lambda config: ()
    # 可选盘面复验 (finding, path) -> bool:执行时刻对目标现场再验一道
    # (pack-tmp 重读 mtime 防删在跑 pack 的活文件;feature-orphan 验文件名
    # 合式);False → 不删,记 VANISHED
    path_ok: Callable | None = None


@dataclass(frozen=True)
class DoctorFamily:
    family_id: str
    title: str
    scope: str                    # pg | global | host
    areas: tuple[str, ...]        # 依赖的盘面区(不可用 → 整族 skip);() = 纯 PG
    scan: Callable                # (Inventory) -> list[Finding] 纯函数
    population: Callable          # (Inventory) -> int 分母
    fixer: Fixer | None = None


# ------------------------------------------------------------------ pool-ghost

def classify_pool(factor, pool_kind: str) -> tuple[str, str]:
    """(verdict, reason);verdict ∈ {'ok', 'ghost', 'wrong-pool'}。

    移植自 scripts/reconcile_bcorr_pools.py —— 判定表不改语义。
    政策(repo.purge_artifacts CHECK 面):池里有副本 ⇔ 因子 ACTIVE 在库。
    """
    if factor is None:
        return "ghost", "PG 无记录(已 rm / 从未入库)"
    if factor.state is None:
        return "ghost", "info 孤儿(有身份无状态)"
    if factor.state.status != FactorStatus.ACTIVE:
        return "ghost", f"status={factor.state.status.value}(离库副本未回收)"
    dm = factor.identity.discovery_method
    if dm in ("automated", "manual") and dm != pool_kind:
        return "wrong-pool", f"discovery_method={dm} 却在 pnl_{pool_kind}"
    return "ok", ""


def _scan_pool_ghost(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    pools = {"automated": inv.areas["pool_automated"],
             "manual": inv.areas["pool_manual"]}
    for kind, area in pools.items():
        for e in sorted(area.entries, key=lambda x: x.name):
            if e.is_dir or e.name.startswith("."):
                out.append(Finding(e.name, "pool-ghost", "alien", WARN,
                                   "非单文件形态,不是池副本(人工确认)",
                                   path=str(area.root / e.name)))
                continue
            factor = inv.factors.get(e.name)
            verdict, reason = classify_pool(factor, kind)
            if verdict == "ghost":
                if factor is not None and factor.state is None:
                    # info 孤儿从自动删除集剔除:该名字同时被 info-orphan 族
                    # FAIL 级"须人工诊断"命中 —— 先删证据后诊断是顺序矛盾。
                    out.append(Finding(e.name, "pool-ghost", "ghost-info-orphan",
                                       WARN, reason + ";随 info-orphan 判读后处理",
                                       path=str(area.root / e.name), ref=kind))
                else:
                    out.append(Finding(e.name, "pool-ghost", "ghost", FAIL,
                                       reason, fixable=True,
                                       path=str(area.root / e.name), ref=kind))
            elif verdict == "wrong-pool":
                out.append(Finding(e.name, "pool-ghost", "wrong-pool", WARN,
                                   reason, path=str(area.root / e.name), ref=kind))
    # 反向:ACTIVE + 来源明确但池里没副本 —— 只报告(approve 豁免合法无副本,
    # REJECTED 不拷池;绝不自动补)
    for name, x in sorted(inv.factors.items()):
        if (x.state is not None and x.state.status == FactorStatus.ACTIVE
                and x.identity.discovery_method in pools
                and name not in pools[x.identity.discovery_method].names):
            out.append(Finding(name, "pool-ghost", "missing", WARN,
                               f"ACTIVE 缺 pnl_{x.identity.discovery_method} 池副本"
                               "(approve 豁免属合法;瞬态:archive 拷贝中)",
                               ref=x.identity.discovery_method))
    return out


def _resolve_pool(finding, config):
    from ops.core.paths import FactorPaths
    p = FactorPaths.of(finding.name, config)
    return p.pool_automated if finding.ref == "automated" else p.pool_manual


def _recheck_pool(finding, factor) -> bool:
    verdict, _ = classify_pool(factor, finding.ref)
    return verdict == "ghost" and not (factor is not None and factor.state is None)


_POOL_FIXER = Fixer(
    plan=FixPlan(
        action="unlink",
        target="pnl_automated/、pnl_manual/ 两个 bcorr 分流池目录内被判 ghost 的单文件副本",
        keeps="不碰 alpha_pnl/(pnl_alphalib 是它的别名)、不碰 src/dump/feature/staging、不写 PG;"
              "wrong-pool 与 missing 永远只报告",
    ),
    resolve=_resolve_pool,
    recheck=_recheck_pool,
    allowed_roots=lambda config: (config.pnl_automated, config.pnl_manual),
)


# --------------------------------------------------------------- snapshot-stale

def _scan_snapshot_stale(inv: Inventory) -> list[Finding]:
    """测得快照:snapshot = 最近一次 check 测得的表现,被拒也写。
    判据:snapshot_at 必须锚定最近一次 check 事件的 at(与 factor_history
    交叉对账);无任何 check 事件的 legacy 快照锚 entered_at。
    全族只报告 —— 时间戳修正走一次性脚本,doctor 不 UPDATE 快照。"""
    out: list[Finding] = []
    for name, x in sorted(inv.factors.items()):
        if x.snapshot is None or x.state is None:
            continue
        expected = inv.last_check_at.get(name) or x.state.entered_at
        if expected is None:
            out.append(Finding(name, "snapshot-stale", "unanchored", WARN,
                               f"快照无锚(无 check 事件且 entered_at 为空,"
                               f"snapshot_at={x.snapshot.snapshot_at})—— 来源不明,人工判读"))
        elif x.snapshot.snapshot_at != expected:
            out.append(Finding(name, "snapshot-stale", "mismatch", WARN,
                               f"snapshot_at={x.snapshot.snapshot_at} != 期望 {expected}"
                               "(最近 check 事件 at,无事件则 entered_at;"
                               "时间戳修正走一次性脚本,doctor 不 UPDATE)"))
    return out


# -------------------------------------------------------------- timeline-drift

def _scan_timeline_drift(inv: Inventory) -> list[Finding]:
    """词汇表不变量:`created_at <= submitted_at`(首提逐字符相等;
    submitted_at=NULL 是设计内值,跳过不判)。违反 = 写路径 bug 信号,
    本族保证再犯不静默。全族只报告:身份表修正走一次性迁移脚本,
    doctor 不 UPDATE。ISO 同构串,字典序即时间序。"""
    out: list[Finding] = []
    for name, x in sorted(inv.factors.items()):
        if x.state is None:
            continue
        created, submitted = x.identity.created_at, x.state.submitted_at
        if created and submitted and created > submitted:
            out.append(Finding(name, "timeline-drift", "created-after-submitted",
                               WARN,
                               f"created_at={created} > submitted_at={submitted}"
                               "(词汇表不变量;新增违反 = 写路径 bug,"
                               "修正走一次性迁移脚本)"))
    return out


# ----------------------------------------------------------------- info-orphan

def _scan_info_orphan(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    lookup = [("src", inv.areas.get("alpha_src")),
              ("staging", inv.areas.get("staging")),
              ("pnl", inv.areas.get("alpha_pnl"))]
    # 盲区:区不可读时"看不见产物"≠"没有产物",
    # 绝不能在盲区下生成删除转介 —— rm 会连 alpha_src 一起级联删。
    blind = [label for label, a in lookup if a is None or a.error]
    for name, x in sorted(inv.factors.items()):
        if x.state is not None:
            continue
        spots = [label for label, a in lookup
                 if a is not None and not a.error and name in a.names]
        if spots:
            action = f"人工判读(盘面有产物: {'/'.join(spots)})"
        elif blind:
            action = f"人工判读(盘面区不可读,无法确认无产物: {'/'.join(blind)})"
        else:
            # 不带 -y:ops rm 自己的交互确认(打印完整删除清单)留作最后人闸
            action = f"ops rm {name}"
        # register 已事务化(info+state 单事务)—— 新增孤儿 = 写路径 bug 信号
        out.append(Finding(name, "info-orphan", "orphan", FAIL,
                           "factor_info 有行、factor_state 无行", action=action))
    return out


# ------------------------------------------------------------------- src-drift

_IN_LIB = (FactorStatus.ACTIVE, FactorStatus.REJECTED)


def _scan_src_drift(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    src = inv.areas["alpha_src"]
    staging = inv.areas.get("staging")
    staging_names = (staging.names if staging is not None and not staging.error
                     else set())
    dir_names = {e.name for e in src.entries if e.is_dir}
    for name, x in sorted(inv.factors.items()):
        # active + rejected 都归档进 alpha_src(check.on_reject 同样归档)
        if x.state is not None and x.state.status in _IN_LIB and name not in dir_names:
            if name in staging_names:
                # 交叉核对 staging:recall 是 move 不是 copy —— restage 崩在
                # move 后 transition 前,唯一副本就在 staging,不是"源码丢失",
                # 别把人指去 dropbox 反查。
                out.append(Finding(name, "src-drift", "lib-missing-staged", WARN,
                                   f"PG {x.state.status.value} 但源码在 staging/"
                                   f"{name}/(crash 中断的 restage / 搬运中)",
                                   action="人工核对 staging 副本后由下次 ops check "
                                          "捡起重跑(状态会被覆盖),勿走 dropbox 反查"))
                continue
            out.append(Finding(name, "src-drift", "lib-missing", FAIL,
                               f"PG {x.state.status.value} 在库但 alpha_src/{name}/ 缺失"
                               "(源码唯一副本;记录是找回线索,绝不因此删 PG)",
                               action="人工:dropbox 按 author+日期反查原始投递"))
    for name in sorted(dir_names - set(inv.factors)):
        if name.startswith("."):
            continue
        out.append(Finding(name, "src-drift", "src-orphan", WARN,
                           "alpha_src 有目录但 PG 全无记录",
                           path=str(src.root / name),
                           action="人工判读(ops backfill 已退役,补录无命令通道;"
                                  "历史残渣处置见 cleanup_src_orphans.py 先例);"
                                  "v1 铁律:alpha_src 不进任何删除集"))
    return out


# --------------------------------------------------------------- staging-drift

def _scan_staging_drift(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    staging = inv.areas["staging"]
    dir_names = {e.name for e in staging.entries if e.is_dir and not e.name.startswith(".")}
    for name in sorted(dir_names):
        x = inv.factors.get(name)
        if x is None or x.state is None:
            out.append(Finding(name, "staging-drift", "orphan-dir", WARN,
                               "staging 有目录但 state 无记录(crash 残留)",
                               path=str(staging.root / name),
                               action=f"ops clear {name}"))
    for name, x in sorted(inv.factors.items()):
        if (x.state is not None
                and x.state.status in (FactorStatus.SUBMITTED, FactorStatus.CHECKING)
                and name not in dir_names):
            out.append(Finding(name, "staging-drift", "missing-dir", WARN,
                               f"state={x.state.status.value} 但 staging/{name}/ 缺失"
                               "(瞬态可能:archive 搬运中;持续存在才是 crash)",
                               action=f"ops cancel {name} --force"))
    return out


# -------------------------------------------------------------- artifact-orphan

def _feature_factor_name(fname: str) -> str | None:
    """<name>.v1.npy / <name>.v2.npy -> name;不合式返回 None。"""
    for v in ("v1", "v2"):
        suffix = f".{v}.npy"
        if fname.endswith(suffix):
            return fname[:-len(suffix)]
    return None


def _scan_artifact_orphan(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    pnl = inv.areas["alpha_pnl"]
    feature = inv.areas["alpha_feature"]
    for e in sorted(pnl.entries, key=lambda x: x.name):
        if e.is_dir:
            # "pnl 是单文件"是布局 SSOT;目录形态是远古残留 —— 报 alien
            # 不静默吞
            out.append(Finding(e.name, "artifact-orphan", "alien", WARN,
                               "alpha_pnl 下目录形态,非单文件 pnl(远古残留,人工确认)",
                               path=str(pnl.root / e.name)))
            continue
        if e.name.startswith("."):
            continue
        if e.name not in inv.factors:
            out.append(Finding(e.name, "artifact-orphan", "pnl-orphan", WARN,
                               "alpha_pnl 有文件但 PG 全无记录(兼 legacy 回退 bcorr 池,"
                               "v1 只报告 —— 基线判读后 v1.1 再议放闸)",
                               path=str(pnl.root / e.name)))
    for e in sorted(feature.entries, key=lambda x: x.name):
        if e.is_dir:
            out.append(Finding(e.name, "artifact-orphan", "alien", WARN,
                               "alpha_feature 下目录形态,非单文件 feature(人工确认)",
                               path=str(feature.root / e.name)))
            continue
        if e.name.startswith(".") and e.name.endswith(".npy.tmp"):
            # pack 原子写残渣(.<name>.vN.npy.tmp)。在跑的 pack 不会留 24h。
            age = inv.now - e.mtime
            if age > PACK_TMP_MAX_AGE_S:
                base = _feature_factor_name(e.name[1:-len(".tmp")]) or e.name
                out.append(Finding(base, "artifact-orphan", "pack-tmp", WARN,
                                   f"pack 原子写残渣(崩溃遗留,age={age / 3600:.0f}h)",
                                   fixable=True, path=str(feature.root / e.name),
                                   ref=e.name))
            continue
        if e.name.startswith("."):
            continue
        name = _feature_factor_name(e.name)
        if name is None:
            out.append(Finding(e.name, "artifact-orphan", "alien", WARN,
                               "alpha_feature 下不合式文件(人工确认)",
                               path=str(feature.root / e.name)))
        elif name not in inv.factors:
            # 放闸:PG 全无记录的孤儿 feature 可修
            out.append(Finding(name, "artifact-orphan", "feature-orphan", WARN,
                               "alpha_feature 有文件但 PG 全无记录",
                               fixable=True, path=str(feature.root / e.name),
                               ref=e.name))
    return out


def _resolve_feature_file(finding, config):
    return config.alpha_feature / finding.ref


def _recheck_artifact(finding, factor) -> bool:
    """按 kind 分派锁内重验。"""
    if finding.kind == "pack-tmp":
        # tmp 残渣与 PG 状态无关(哪怕因子 ACTIVE,点开头 tmp 也不是消费物);
        # 龄期在 guards 的路径/形态闸后由文件系统重验(mtime 重读)。
        return finding.ref.startswith(".") and finding.ref.endswith(".npy.tmp")
    if finding.kind == "feature-orphan":
        # 锁内重验:因子仍然 PG 全无记录(并发 submit/backfill 已登记 → 不删)
        return factor is None
    return False   # 其它 kind(alien / pnl-orphan)永不可修


def _artifact_path_ok(finding, path) -> bool:
    """执行时刻盘面复验(按 kind)。"""
    if finding.kind == "pack-tmp":
        # 重读 mtime:同名文件若是在跑 pack 刚写的新 tmp,不是残渣,不删
        import time
        try:
            return (time.time() - path.stat().st_mtime) > PACK_TMP_MAX_AGE_S
        except OSError:
            return False
    if finding.kind == "feature-orphan":
        # 只删标准命名的正式单文件(点开头/不合式文件是 alien,不归这里)
        return (not path.name.startswith(".")
                and _feature_factor_name(path.name) == finding.name)
    return False


_ARTIFACT_FIXER = Fixer(
    plan=FixPlan(
        action="unlink",
        target="alpha_feature/ 下两类:①PG 全无记录的孤儿 <name>.vN.npy;"
               "②点开头 `.*.npy.tmp` 且 mtime>24h 的 pack 崩溃残渣",
        keeps="不碰任何 PG 有记录因子的 feature、不碰 alien 不合式文件(只报告)、"
              "不碰 alpha_pnl 孤儿(无判读材料,仍只报告)、不碰 PG",
    ),
    resolve=_resolve_feature_file,
    recheck=_recheck_artifact,
    allowed_roots=lambda config: (config.alpha_feature,),
    path_ok=_artifact_path_ok,
)


# ----------------------------------------------------------------- dump-orphan

def _scan_dump_orphan(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    dump = inv.areas["dump_local"]
    # 错配绊线:alpha_dump 指错一级(config 少写 /
    # sidecar 软链错指)时,扫到的是 alphalib 根 —— 条目名会撞库区名
    # (alpha_src/staging/…),它们全都"PG 无因子记录",若不拦会整批判成
    # fixable 孤儿。区内出现任何库区名条目 = 疑似指错,整族弃权零发现。
    reserved = {a.root.name for a in inv.areas.values()}
    hit = sorted(e.name for e in dump.entries if e.name in reserved)
    if hit:
        raise FamilySkip(
            f"疑似 alpha_dump 指错(区内出现 alphalib 区名条目: {', '.join(hit)})"
            "—— 先跑 ops setup --check 核对部署,本族零发现零动作")
    for e in sorted(dump.entries, key=lambda x: x.name):
        if not e.is_dir or e.name.startswith("."):
            continue
        if e.name not in inv.factors:
            out.append(Finding(e.name, "dump-orphan", "orphan", WARN,
                               "本机 dump sidecar 有目录但 PG 全无记录"
                               "(跨机 rm 清不到的残留;dump 是 check 确定性产物,可重生成)",
                               fixable=True, path=str(dump.root / e.name)))
    return out


def _resolve_dump(finding, config):
    from ops.core.paths import FactorPaths
    return FactorPaths.of(finding.name, config).dump


def _recheck_dump(finding, factor) -> bool:
    return factor is None      # 仍然 PG 全无记录才删


_DUMP_FIXER = Fixer(
    plan=FixPlan(
        action="rmtree",
        target="本机 alpha_dump sidecar 内 PG 全无记录的 dump 目录(仅本机视界,各机各跑)",
        keeps="不碰 alpha_dump 软链本身、不碰任何有 PG 记录的因子目录、不碰共享面、不写 PG",
    ),
    resolve=_resolve_dump,
    recheck=_recheck_dump,
    allowed_roots=lambda config: (config.alpha_dump,),
)


# ------------------------------------------------------------------ 注册表

def _pop_factors(inv: Inventory) -> int:
    return len(inv.factors)


FAMILIES: tuple[DoctorFamily, ...] = (
    DoctorFamily("pool-ghost", "bcorr 池副本 ⇔ ACTIVE 在库", "global",
                 ("pool_automated", "pool_manual"),
                 _scan_pool_ghost,
                 lambda inv: len(inv.areas["pool_automated"].entries)
                 + len(inv.areas["pool_manual"].entries),
                 _POOL_FIXER),
    DoctorFamily("snapshot-stale", "测得快照 snapshot_at ⇔ 最近 check 事件", "pg", (),
                 _scan_snapshot_stale,
                 lambda inv: sum(1 for x in inv.factors.values()
                                 if x.snapshot is not None)),
    DoctorFamily("timeline-drift", "created_at <= submitted_at 不变量", "pg", (),
                 _scan_timeline_drift, _pop_factors),
    DoctorFamily("info-orphan", "factor_info ⇔ factor_state 成对", "pg", (),
                 _scan_info_orphan, _pop_factors),
    DoctorFamily("src-drift", "alpha_src 目录 ⇔ PG 在库集", "global",
                 ("alpha_src",), _scan_src_drift,
                 lambda inv: len(inv.areas["alpha_src"].entries)),
    DoctorFamily("staging-drift", "staging 目录 ⇔ factor_state", "global",
                 ("staging",), _scan_staging_drift,
                 lambda inv: len(inv.areas["staging"].entries)),
    DoctorFamily("artifact-orphan", "alpha_pnl / alpha_feature ⇔ factor_info", "global",
                 ("alpha_pnl", "alpha_feature"), _scan_artifact_orphan,
                 lambda inv: len(inv.areas["alpha_pnl"].entries)
                 + len(inv.areas["alpha_feature"].entries),
                 _ARTIFACT_FIXER),
    DoctorFamily("dump-orphan", "本机 dump sidecar ⇔ factor_info", "host",
                 ("dump_local",), _scan_dump_orphan,
                 lambda inv: len(inv.areas["dump_local"].entries),
                 _DUMP_FIXER),
)

FAMILY_IDS = tuple(f.family_id for f in FAMILIES)
FIXABLE_IDS = tuple(f.family_id for f in FAMILIES if f.fixer is not None)
