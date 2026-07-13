"""doctor 执行引擎:采集 → 判定 → (确认后)修复 → 记账。零展示(渲染归 cli)。

采集刻意廉价:一次 `repo.find(include_submitted=True)`(单条三表 JOIN,秒级)
+ 各盘面区**浅 iterdir**(名字级),不用 LibraryScanner 的 ~25s 深扫 ——
dump 天数/日期缺口那类深扫检查是 v2(待具名消费方)。

PG 不可达 = 秒级硬失败(DoctorUnavailable → cli exit 2):对账命令没有 PG
就没有对账对象;探测走 infra/pg.probe 有界直连(setup 先例,绝不进池重试
挂半分钟)。单区 PermissionError → 依赖该区的族记 skip,不拖垮整份报告。
"""
from __future__ import annotations

import socket
import time
from pathlib import Path

from ops.infra.repository import FactorRepository
from ops.utils.log import logger

from . import guards
from .checks import FAMILIES, DoctorFamily
from .findings import Area, Entry, FamilyResult, FamilySkip, Inventory


class DoctorUnavailable(RuntimeError):
    """PG 不可达 / 后端不是 postgres —— doctor 无对账对象。"""


def _list_area(root: Path) -> Area:
    area = Area(root=root)
    try:
        for p in root.iterdir():
            try:
                st = p.stat()
                area.entries.append(Entry(name=p.name, is_dir=p.is_dir(),
                                          is_symlink=p.is_symlink(),
                                          mtime=st.st_mtime))
            except OSError:
                # 条目级 stat 失败(并发删除/坏链):按非目录条目记录,判定端兜底
                area.entries.append(Entry(name=p.name, is_dir=False,
                                          is_symlink=p.is_symlink()))
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError) as e:
        area.error = f"{type(e).__name__}: {e}"
    return area


def collect_inventory(config, repo: FactorRepository) -> Inventory:
    factors = {x.identity.name: x for x in repo.find(include_submitted=True)}
    last_check_at = repo.latest_check_ats()  # v3 测得快照对账的期望值
    areas = {
        "alpha_src": _list_area(config.alpha_src),
        "staging": _list_area(config.staging),
        "alpha_pnl": _list_area(config.alpha_pnl),
        "alpha_feature": _list_area(config.alpha_feature),
        "pool_automated": _list_area(config.pnl_automated),
        "pool_manual": _list_area(config.pnl_manual),
        # alpha_dump 是软链 → 本机 sidecar;iterdir 天然穿链,视界=本机
        "dump_local": _list_area(config.alpha_dump),
    }
    return Inventory(factors=factors, areas=areas,
                     hostname=socket.gethostname(), now=time.time(),
                     last_check_at=last_check_at)


def _probe_pg(config) -> None:
    backend = (getattr(config, "state_backend", None) or "json").lower()
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if backend != "postgres" or not conninfo:
        raise DoctorUnavailable(
            "doctor 需要 postgres state 后端(json dev 后端无三表可对账)")
    from ops.infra.pg import probe
    try:
        probe(conninfo)
    except Exception as e:  # noqa: BLE001 — 一切连接失败同一出口
        raise DoctorUnavailable(f"PG 不可达: {e}") from e


def run_doctor(config, *, families: list[str] | None = None,
               fix: tuple[str, ...] = (), confirm=None,
               ) -> tuple[Inventory, list[FamilyResult]]:
    """跑对账。families=None 全族;fix 内的族在 confirm(result, fixer) 返回
    True 后逐条经 guards.execute 修复(confirm=None 视为拒绝 —— 缺省绝不动)。
    返回 (inventory, results);渲染/退出码归 cli。
    """
    _probe_pg(config)
    repo = FactorRepository(config)
    inv = collect_inventory(config, repo)

    selected: list[DoctorFamily] = [
        f for f in FAMILIES if families is None or f.family_id in families]

    results: list[FamilyResult] = []
    for family in selected:
        fr = FamilyResult(family_id=family.family_id, title=family.title,
                          scope=family.scope)
        bad_area = next((a for a in family.areas if inv.areas[a].error), None)
        if bad_area is not None:
            fr.skip_reason = (f"盘面区 {bad_area} 不可用: "
                              f"{inv.areas[bad_area].error}(整族跳过;"
                              "无权限时检查用户组,root 目录归 ops setup --check)")
            results.append(fr)
            continue
        try:
            fr.population = family.population(inv)
            fr.findings = family.scan(inv)
        except FamilySkip as e:
            # scan 主动弃权(如疑似 config 错配)—— 零发现零动作
            fr.skip_reason = str(e)
            results.append(fr)
            continue
        except Exception as e:  # noqa: BLE001 — 单族崩溃不拖垮整份报告
            logger.warning("doctor family {} crashed: {}", family.family_id, e)
            fr.skip_reason = f"检查崩溃: {e}"
            results.append(fr)
            continue

        if family.family_id in fix and family.fixer is not None:
            fixables = [f for f in fr.findings if f.fixable]
            if fixables and confirm is not None and confirm(fr, family.fixer):
                for f in fixables:
                    outcome, err = guards.execute(f, family.fixer, config, repo)
                    fr.fix_log.append((f, outcome, err))
        results.append(fr)
    return inv, results


def fail_residual(results: list[FamilyResult]) -> int:
    """修复记账后仍成立的 FAIL 级漂移数(退出码依据)。"""
    from .findings import FAIL
    return sum(r.residual(FAIL) for r in results)
