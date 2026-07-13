#!/usr/bin/env python3
"""legacy 清理批 ③:discovery_method 归一(池位置判值)+ NOT NULL 收口。

拍板(2026-07-13):字段不允许 NULL;'backfill' 不是发现方式。存量
'backfill'/NULL 行按 **pnl 分流池位置**判值(pnl_automated → automated,
pnl_manual → manual);两池皆无/两池皆在的残余由用户人工判定("我知道哪些
是机器因子哪些是人工因子"),经 `--assign` 名单文件喂给本脚本。

全部行落定后同一事务收口约束:`chk_discovery` 收窄 IN ('automated','manual')
+ `SET NOT NULL`(与代码侧 DDL / init/01-schema.sql 同步,test_schema_pin
钉住)。残余未清零时只落已判定的数据更新、约束跳过 —— 补 --assign 后重跑,
幂等可分批推进;候选清零后重跑仍会补挂约束(数据好了约束没上的中间态自愈)。

--assign 文件格式:每行 `<因子名> <automated|manual>`(空白分隔;# 注释行与
空行忽略)。人工判定优先于池位置(冲突时以人工为准并打印警告)。

缺省 dry-run(打印全量 unresolved 名单 = 用户人工判定的输入);--apply 执行
(autocommit 连接 + 显式事务,v3 教训:非 autocommit 下 transaction() 退化
savepoint,close 全回滚)。apply 后新直连复核数据与约束状态。

用法(160,repo 根目录):
    uv run python scripts/postgres/migrate_discovery_notnull.py                    # dry-run
    uv run python scripts/postgres/migrate_discovery_notnull.py --assign dm.txt --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.infra.config import Config, get_default_config_path  # noqa: E402

# 规模守卫:候选是小批 legacy 存量(07-06 时 NULL=109;'backfill' 为其后
# ops backfill 增量,预期同量级)。超出 = 环境/判据异常,停止待判读。
_GUARD_MAX = 1000

VALID = ("automated", "manual")

_CANDIDATES_SQL = ("SELECT i.name, coalesce(i.discovery_method, 'NULL'), "
                   "coalesce(s.status, 'no-state'), coalesce(i.author, '?') "
                   "FROM factor_info i LEFT JOIN factor_state s USING (name) "
                   "WHERE i.discovery_method IS NULL "
                   "   OR i.discovery_method = 'backfill' ORDER BY i.name")
_REMAINING_SQL = ("SELECT count(*) FROM factor_info "
                  "WHERE discovery_method IS NULL OR discovery_method = 'backfill'")


def _scalar(cur):
    row = cur.fetchone()
    assert row is not None  # COUNT/目录查询恒有一行
    return row[0]


def load_assign(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 2 or parts[1] not in VALID:
            raise SystemExit(
                f"--assign 第 {i} 行非法(须 `<name> <automated|manual>`): {line!r}")
        out[parts[0]] = parts[1]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="discovery_method 归一(池位置 + --assign 人工名单)+ NOT NULL 收口")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--assign", type=Path,
                        help="人工判定名单文件(每行 `<name> <automated|manual>`)")
    parser.add_argument("--apply", action="store_true",
                        help="执行(默认 dry-run 只列计划与 unresolved 名单)")
    args = parser.parse_args()

    config = Config.load(args.config_path)
    conninfo = config.state_postgres_conninfo
    if not conninfo:
        print("错误: config 无 postgres conninfo", file=sys.stderr)
        return 2
    assign = load_assign(args.assign) if args.assign else {}
    if assign:
        print(f"--assign 名单: {len(assign)} 条")

    import psycopg
    conn = psycopg.connect(conninfo, autocommit=True)
    rows = conn.execute(_CANDIDATES_SQL).fetchall()
    print(f"候选('backfill' 或 NULL): {len(rows)}")
    if len(rows) > _GUARD_MAX:
        print(f"错误: 候选数超守卫 {_GUARD_MAX} —— 环境/判据异常, 停止待判读",
              file=sys.stderr)
        return 2

    stray = sorted(set(assign) - {r[0] for r in rows})
    if stray:
        print(f"⚠ --assign 里 {len(stray)} 个名字不在候选集(已 rm / 已有值),忽略:")
        for n in stray[:10]:
            print(f"    {n}")

    plans, unresolved, conflicts = [], [], []
    for name, dm, status, author in rows:
        in_auto = (config.pnl_automated / name).exists()
        in_man = (config.pnl_manual / name).exists()
        pool_verdict = ("automated" if in_auto and not in_man
                        else "manual" if in_man and not in_auto else None)
        if name in assign:
            if pool_verdict and assign[name] != pool_verdict:
                conflicts.append((name, assign[name], pool_verdict))
            plans.append((name, assign[name], "assign"))
        elif pool_verdict:
            plans.append((name, pool_verdict, "pool"))
        elif in_auto and in_man:
            unresolved.append((name, dm, status, author, "两池皆在(冲突)"))
        else:
            unresolved.append((name, dm, status, author, "两池皆无"))

    n_auto = sum(1 for _, v, _s in plans if v == "automated")
    n_man = len(plans) - n_auto
    print(f"可判定 {len(plans)}(automated {n_auto} / manual {n_man};"
          f"其中人工名单 {sum(1 for *_, s in plans if s == 'assign')}),"
          f"unresolved {len(unresolved)}")
    for name, v, pool_v in conflicts:
        print(f"    ⚠ 冲突 {name}: 人工判定 {v} != 池位置 {pool_v}(以人工为准)")
    if unresolved:
        print("unresolved 全量(人工判定输入,格式即 --assign 左列):")
        for name, dm, status, author, why in unresolved:
            print(f"    {name}  dm={dm} status={status} author={author}  [{why}]")

    if not args.apply:
        print("\ndry-run 结束(未写任何行;执行加 --apply)")
        return 0

    with conn.transaction():
        for name, v, _src in plans:
            conn.execute("UPDATE factor_info SET discovery_method = %s "
                         "WHERE name = %s", (v, name))
        remaining = _scalar(conn.execute(_REMAINING_SQL))
        if remaining == 0:
            conn.execute("ALTER TABLE factor_info DROP CONSTRAINT IF EXISTS chk_discovery")
            conn.execute("ALTER TABLE factor_info ADD CONSTRAINT chk_discovery "
                         "CHECK (discovery_method IN ('automated', 'manual'))")
            conn.execute("ALTER TABLE factor_info "
                         "ALTER COLUMN discovery_method SET NOT NULL")
            print(f"UPDATE {len(plans)} 行;残余 0 → 约束已收口"
                  "(chk_discovery 收窄 + SET NOT NULL)")
        else:
            print(f"UPDATE {len(plans)} 行;残余 {remaining} 行未判定 —— "
                  "约束跳过(补 --assign 后重跑收口)")
    conn.close()

    # v3 教训:打印 ≠ 持久化 —— 新直连复核数据 + 约束状态
    with psycopg.connect(conninfo) as vconn:
        rem = _scalar(vconn.execute(_REMAINING_SQL))
        notnull = _scalar(vconn.execute(
            "SELECT attnotnull FROM pg_attribute "
            "WHERE attrelid = 'factor_info'::regclass "
            "  AND attname = 'discovery_method'"))
        condef_row = vconn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'chk_discovery' "
            "  AND conrelid = 'factor_info'::regclass").fetchone()
    condef = condef_row[0] if condef_row else "(无)"
    print(f"\n新直连复核: 残余 {rem} 行;NOT NULL={notnull};chk_discovery: {condef}")
    done = rem == 0 and notnull and "'backfill'" not in condef
    print("收口完成 ✅" if done else "尚未收口(残余待 --assign 或约束未挂)⚠")
    return 0


if __name__ == "__main__":
    sys.exit(main())
