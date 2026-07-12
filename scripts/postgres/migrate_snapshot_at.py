#!/usr/bin/env python3
"""一次性迁移:修正 ACTIVE 因子的 snapshot_at != entered_at(迁移期存量)。

背景(2026-07-12,doctor 立项随批小件):factor_snapshot 语义是入库时不可变
快照,`snapshot_at = entered_at` 是硬不变量(repo.attach_snapshot 强制)。
三表迁移期(2026-07-06 前)的存量行不满足 —— Factor.__post_init__ 软校验
每次全库读 WARNING 刷屏。illegal 侧(entered_at 为空却带快照)由
`ops doctor --fix snapshot-stale` 删行;本脚本处理 mismatch 侧(ACTIVE、
entered_at 非空、仅时间戳不符):UPDATE snapshot_at = entered_at。

**为什么不在 doctor 里做**:快照不可变,doctor 只读/只删,不开 UPDATE 口子;
时间戳等值化是一次性数据迁移,归 scripts/postgres(生产 schema/数据迁移的
既有 owner)。

用法(160,以 doctor 的 JSON 报告为名单;默认 dry-run):
    ops doctor --family snapshot-stale --format json > /tmp/doctor.json
    uv run python scripts/postgres/migrate_snapshot_at.py --input /tmp/doctor.json
    uv run python scripts/postgres/migrate_snapshot_at.py --input /tmp/doctor.json --apply

安全面:只 UPDATE factor_snapshot.snapshot_at;仅名单内 kind=mismatch 的名字、
仅 status='active'、仅 entered_at 非空的行;不 INSERT/DELETE、不碰其它列/表。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.infra.config import Config, get_default_config_path  # noqa: E402


def load_names(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text())
    names = [f["name"]
             for fam in report.get("families", [])
             if fam.get("family_id") == "snapshot-stale"
             for f in fam.get("findings", [])
             if f.get("kind") == "mismatch"]
    return sorted(set(names))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="snapshot_at := entered_at(仅 doctor mismatch 名单内的 ACTIVE 行)")
    parser.add_argument("--input", type=Path, required=True,
                        help="ops doctor --format json 的报告文件")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--apply", action="store_true",
                        help="执行 UPDATE(默认 dry-run 只列计划)")
    args = parser.parse_args()

    names = load_names(args.input)
    if not names:
        print("名单为空(报告里无 snapshot-stale/mismatch),无事可做")
        return 0
    print(f"名单(mismatch): {len(names)} 条")

    config = Config.load(args.config_path)
    conninfo = config.state_postgres_conninfo
    if not conninfo:
        print("错误: config 无 postgres conninfo", file=sys.stderr)
        return 2

    import psycopg
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute(
            """SELECT s.name, s.snapshot_at, st.entered_at
               FROM factor_snapshot s JOIN factor_state st ON st.name = s.name
               WHERE s.name = ANY(%s) AND st.status = 'active'
                 AND st.entered_at IS NOT NULL
                 AND s.snapshot_at IS DISTINCT FROM st.entered_at""",
            (names,)).fetchall()
        print(f"实际命中(ACTIVE + entered_at 非空 + 仍不符): {len(rows)} 行")
        for name, snap_at, entered_at in rows[:20]:
            print(f"  {name}: snapshot_at {snap_at} -> {entered_at}")
        if len(rows) > 20:
            print(f"  …另有 {len(rows) - 20} 行")

        if not args.apply:
            print("\ndry-run 结束(未写任何行;执行加 --apply)")
            return 0

        cur = conn.execute(
            """UPDATE factor_snapshot s SET snapshot_at = st.entered_at
               FROM factor_state st
               WHERE st.name = s.name AND s.name = ANY(%s)
                 AND st.status = 'active' AND st.entered_at IS NOT NULL
                 AND s.snapshot_at IS DISTINCT FROM st.entered_at""",
            (names,))
        conn.commit()
        print(f"\napply 结束: UPDATE {cur.rowcount} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
