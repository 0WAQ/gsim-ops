"""删除闸 —— 所有 doctor fixer 的唯一执行出口(五道闸)。

任何族的修复动作都不许绕过本模块直接动盘/动 PG:判定函数(checks.py 的
scan/recheck)可能写错,但闸是集中防线 —— 判定 bug 最多导致"该删没删",
不可能升级为误删。

五道闸:
1. 逐条非阻塞 factor_lock(拿不到 → LOCKED 跳过;check/submit/rm 全程持锁,
   互斥天然挡撞车);
2. 锁内 repo 新读重验漂移仍成立(TOCTOU 双钥:扫描 → 人工确认 → 执行的
   分钟级窗口里,因子可能已 restage → 重检 → 重新 ACTIVE);
3. ACTIVE 绝缘集中断言:因子当前 ACTIVE → 一律拒绝(不依赖各族 recheck
   写对)。唯一豁免是点开头 `.*.tmp` 残渣 —— 按形状豁免:没有任何被消费的
   产物是点开头 tmp 文件;
4. 路径闸:目标由 fixer.resolve 现场重拼(不信扫描期缓存),realpath 必须
   落在该 fixer 声明的允许根内(白名单),且绝不落在
   alpha_src / alpha_pnl / staging 内(禁区双保险 —— 哪怕 config 配错也拦);
5. 形态闸:unlink 只删文件、rmtree 只删真实目录(不跟软链)——
   "pnl 是单文件"陷阱的机制化。

ENOENT / 重验不成立 → VANISHED(与并发 ops rm 抢删属正常,不算错误)。
逐条独立执行:中断后重跑 doctor 即重扫收敛,无需 journal。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ops.core.state import FactorStatus
from ops.infra.lock import FactorLocked, factor_lock
from ops.utils.log import logger

from .checks import Fixer
from .findings import BLOCKED, ERROR, FIXED, LOCKED, VANISHED, Finding

_ALLOWED_ACTIONS = ("unlink", "rmtree", "discard_snapshot")


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _is_tmp_residue(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(".tmp")


def execute(finding: Finding, fixer: Fixer, config, repo) -> tuple[str, str]:
    """执行单条修复,返回 (outcome, err)。outcome ∈ findings.{FIXED,LOCKED,
    VANISHED,BLOCKED,ERROR}。BLOCKED = 闸拒绝(不变量兜住了判定 bug,值得人看)。"""
    action = fixer.plan.action
    if action not in _ALLOWED_ACTIONS:
        return BLOCKED, f"未注册的 action: {action!r}"

    try:
        with factor_lock(finding.name, config):
            return _execute_locked(finding, fixer, config, repo)
    except FactorLocked:
        return LOCKED, "因子锁被他人持有(在跑的 check/submit/rm),跳过"
    except Exception as e:  # noqa: BLE001 — 单条失败不拖垮整批
        logger.warning("doctor fix error {}/{}: {}", finding.family, finding.name, e)
        return ERROR, str(e)


def _execute_locked(finding: Finding, fixer: Fixer, config, repo) -> tuple[str, str]:
    action = fixer.plan.action

    # 2. 锁内重验(新读 PG)
    factor = repo.get(finding.name)
    if not fixer.recheck(finding, factor):
        return VANISHED, "锁内重验:漂移已不成立(并发写路径已处理)"

    # discard_snapshot 无盘面目标:ACTIVE 绝缘后直接走 repo API
    if action == "discard_snapshot":
        if (factor is not None and factor.state is not None
                and factor.state.status == FactorStatus.ACTIVE):
            return BLOCKED, "因子 ACTIVE,拒绝 discard(绝缘不变量)"
        repo.discard_snapshot(finding.name)
        return FIXED, ""

    # 4. 路径闸:现场重拼 + realpath 包含(白名单)+ 双层禁区
    target = Path(fixer.resolve(finding, config))
    real = target.parent.resolve() / target.name   # leaf 不 resolve:软链目标本身要可判形态
    allowed = tuple(Path(r).resolve() for r in fixer.allowed_roots(config))
    if not allowed or not _within(real, allowed):
        return BLOCKED, f"目标 {real} 不在允许根 {tuple(map(str, allowed))} 内"
    # 禁区 a(包含型):alpha_src/alpha_pnl/staging 整树绝不可入 —— 合法 fixer
    # 的允许根不含它们,config 配错也拦。
    forbidden = tuple(p.resolve() for p in
                      (config.alpha_src, config.alpha_pnl, config.staging))
    if _within(real, forbidden):
        return BLOCKED, f"目标 {real} 落在禁区(alpha_src/alpha_pnl/staging)"
    # 禁区 b(等值型,对抗评审 2026-07-12):目标绝不许**就是**(或包含)任何
    # config 声明的数据根 —— 合法目标永远在允许根的下一级。包含型放不进
    # feature/双池(pack-tmp/pool-ghost 的合法目标就在其下),等值型没有此
    # 冲突,专拦"allowed_roots 与扫描源同一 config 键派生"时的错配自引用
    # (如 alpha_dump 指错一级,白名单必然包含目标,唯此闸能拦)。
    declared = []
    for attr in ("alpha_src", "alpha_pnl", "staging", "alpha_feature",
                 "alpha_dump", "pnl_automated", "pnl_manual", "dropbox_path",
                 "pnl_alphalib", "pnl_prod_path"):
        p = getattr(config, attr, None)
        if p:
            declared.append(Path(p).resolve())
    if any(real == d or real in d.parents for d in declared):
        return BLOCKED, (f"目标 {real} 是/包含某个 config 声明的数据根"
                         "(疑似 config 错配,拒绝)")

    # 3. ACTIVE 绝缘(形状豁免:点开头 .tmp 残渣不是任何消费物)
    if (factor is not None and factor.state is not None
            and factor.state.status == FactorStatus.ACTIVE
            and not _is_tmp_residue(real)):
        return BLOCKED, "因子 ACTIVE,拒绝删除其产物(绝缘不变量)"

    # 可选盘面复验(pack-tmp 重读 mtime:同名新 tmp 是在跑 pack 的活文件)
    if fixer.path_ok is not None and not fixer.path_ok(real):
        return VANISHED, "执行时刻盘面复验不成立(目标已变化/非残渣)"

    # 5. 形态闸 + 执行
    try:
        if action == "unlink":
            if real.is_dir():
                return BLOCKED, "unlink 目标是目录(形态闸:池副本/feature 是单文件)"
            real.unlink()
        else:  # rmtree
            if real.is_symlink():
                return BLOCKED, "rmtree 目标是软链(形态闸:不跟软链删)"
            if not real.is_dir():
                if not real.exists():
                    return VANISHED, "目标已不存在"
                return BLOCKED, "rmtree 目标不是目录(形态闸)"
            shutil.rmtree(real)
    except FileNotFoundError:
        return VANISHED, "目标已不存在(并发 rm 抢删属正常)"
    return FIXED, ""
