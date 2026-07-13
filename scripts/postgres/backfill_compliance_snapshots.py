#!/usr/bin/env python3
"""legacy 清理批 ②:compliance 被拒因子测得快照补跑(2026-07-13,判读锚点 22 条,全库)。

背景:schema v3 回填(migrate_v3_measured_snapshots.py)只覆盖 correlation 被拒
(fail_reason 自带指标可解析);compliance 被拒的 fail_reason 是持仓合规文本,
无指标可解析 —— 但 long_backtest 已跑完、pnl 已归档在盘(compliance 属
keep_artifacts_on_fail 晚期 stage),对 pnl 跑 simsummary 得到的就是**该次 check
测得的表现**,不是离线重算(pnl 是该次 check 的产物,离线重算禁令针对重跑
backtest 产生新表现)。

处置:对 status='rejected' 且最近失败在 compliance 且无快照的因子,
Runner.run_simsummary(alpha_pnl/<name>) → repo.attach_snapshot(
measured_at = 该次失败 check 事件的 at)。delay/datasources 从
alpha_src/<name>/meta.json 补(v3 同款,尽力而为);bcorr 组置 None ——
correlation 在 compliance 之后,该次 check 根本没跑到,无测得值(NULL 诚实)。

缺省 dry-run(simsummary 照跑,打印将写入的指标;不落库);--apply 执行。
幂等:只补"无快照"因子,二跑候选为 0。apply 后用**新直连**(绕开连接池)
复核落库行数(v3 教训:打印 ≠ 持久化)。

用法(160,repo 根目录;须能访问 alpha_pnl 与 gsim simsummary):
    uv run python scripts/postgres/backfill_compliance_snapshots.py            # dry-run
    uv run python scripts/postgres/backfill_compliance_snapshots.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.core.factor import FactorSnapshot  # noqa: E402
from ops.core.paths import FactorPaths  # noqa: E402
from ops.infra.config import Config, get_default_config_path  # noqa: E402
from ops.infra.gsim.runner import Runner  # noqa: E402
from ops.infra.repository import FactorRepository  # noqa: E402

# 规模守卫:判读锚点 22(2026-07-13,全库)。超出 = 环境/判据异常,停止待判读。
_GUARD_MAX = 60


def main() -> int:
    parser = argparse.ArgumentParser(
        description="compliance 被拒因子测得快照补跑(simsummary 现算;缺省 dry-run)")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--apply", action="store_true",
                        help="执行落库(默认 dry-run 只列计划)")
    args = parser.parse_args()

    config = Config.load(args.config_path)
    if not config.state_postgres_conninfo:
        print("错误: config 无 postgres conninfo(本脚本只服务 PG 后端)",
              file=sys.stderr)
        return 2
    repo = FactorRepository(config)

    candidates = [x for x in repo.find(status="rejected", fail_stage="compliance")
                  if x.snapshot is None]
    print(f"候选(rejected + 最近失败 compliance + 无快照): {len(candidates)} "
          "(判读锚点 22, 全库 2026-07-13)")
    if len(candidates) > _GUARD_MAX:
        print(f"错误: 候选数超守卫 {_GUARD_MAX} —— 环境/判据异常, 停止待判读",
              file=sys.stderr)
        return 2

    plans, skipped = [], []
    for x in candidates:
        if x.last_fail is None or not x.last_fail.at:
            skipped.append((x.name, "无失败 check 事件 at(measured_at 无锚)"))
            continue
        paths = FactorPaths.of(x.name, config)
        if not paths.pnl.is_file():
            skipped.append((x.name, f"pnl 不在盘: {paths.pnl}"))
            continue
        metrics = Runner.run_simsummary(paths.pnl, config)
        if metrics is None:
            skipped.append((x.name, "simsummary 失败/输出不可解析"))
            continue
        delay = fields = tables = None
        try:
            meta = json.loads(paths.src_meta.read_text())
            delay = meta.get("delay")
            ds = meta.get("datasources") or {}
            fields = ds.get("fields") or None
            tables = ds.get("tables") or None
        except (OSError, ValueError):
            # meta 缺失/不可读:仅指标回填(v3 脚本同策略,不因此弃整条)
            pass
        plans.append((x, metrics, delay, fields, tables))

    print(f"可补跑 {len(plans)},跳过 {len(skipped)}")
    for name, why in skipped:
        print(f"    skip {name}: {why}")
    for x, m, delay, _f, _t in plans:
        print(f"    plan {x.name}: measured_at={x.last_fail.at} "
              f"ret={m.ret} shrp={m.shrp} mdd={m.mdd} tvr={m.tvr} "
              f"fitness={m.fitness} delay={delay}")

    if not args.apply:
        print("\ndry-run 结束(未写任何行;执行加 --apply)")
        return 0

    for x, m, delay, fields, tables in plans:
        repo.attach_snapshot(FactorSnapshot(
            name=x.name, ret=m.ret, shrp=m.shrp, mdd=m.mdd, tvr=m.tvr,
            fitness=m.fitness, fields=fields, tables=tables, delay=delay),
            measured_at=x.last_fail.at)

    # v3 教训:打印 ≠ 持久化 —— apply 后新直连(绕开池)复核落库行数
    import psycopg
    names = [x.name for x, *_ in plans]
    persisted = 0
    if names:
        with psycopg.connect(config.state_postgres_conninfo) as check_conn:
            row = check_conn.execute(
                "SELECT count(*) FROM factor_snapshot WHERE name = ANY(%s)",
                (names,)).fetchone()
            persisted = row[0] if row else 0
    ok = persisted == len(plans)
    print(f"\napply 结束: 计划 {len(plans)} 条, 新直连复核落库 {persisted} 条"
          + ("" if ok else " ⚠ 数量不符, 立即判读"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
