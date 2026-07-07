#!/usr/bin/env python3
"""补充 factor_info.discovery_method 字段.

迁移脚本执行后，discovery_method 为 NULL。本脚本从两个来源推断:
1. alpha_src/<name>/meta.json 的 discovery_method 字段
2. 回退: pnl 副本位置 (pnl_automated vs pnl_manual)

用法:
    uv run python scripts/postgres/backfill_discovery_method.py            # 默认 ops
    uv run python scripts/postgres/backfill_discovery_method.py --db ops_test
"""
import argparse
import json
from pathlib import Path
import psycopg

# 读取密码
password = Path("scripts/postgres/.env").read_text().strip().split("=", 1)[1]

# 路径配置 (从 config.yaml 读或硬编码)
ALPHA_SRC = Path("/tank/vault/alphalib/alpha_src")
PNL_AUTOMATED = Path("/mnt/storage/alphalib/pnl_automated")
PNL_MANUAL = Path("/mnt/storage/alphalib/pnl_manual")


def infer_discovery_method(name: str) -> str | None:
    """推断 discovery_method: meta.json > pnl 位置 > None."""
    # 1. 尝试从 meta.json 读
    meta_path = ALPHA_SRC / name / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if "discovery_method" in meta:
                return meta["discovery_method"]
        except Exception:
            pass

    # 2. 从 pnl 副本位置推断
    if (PNL_AUTOMATED / name).exists():
        return "automated"
    elif (PNL_MANUAL / name).exists():
        return "manual"

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="ops", help="目标数据库 (默认 ops, 测试用 ops_test)")
    args = parser.parse_args()

    conn = psycopg.connect(
        host="10.9.100.160",
        port=15432,
        user="ops",
        password=password,
        dbname=args.db,
    )

    cur = conn.cursor()

    # 获取所有 discovery_method 为 NULL 的因子
    cur.execute("SELECT id, name FROM factor_info WHERE discovery_method IS NULL")
    rows = cur.fetchall()

    print(f"[{args.db}] Found {len(rows)} factors without discovery_method")

    updated = 0
    counts = {"automated": 0, "manual": 0}
    for factor_id, name in rows:
        dm = infer_discovery_method(name)
        if dm:
            cur.execute(
                "UPDATE factor_info SET discovery_method = %s WHERE id = %s",
                (dm, factor_id),
            )
            updated += 1
            counts[dm] = counts.get(dm, 0) + 1

    conn.commit()
    print(f"\nUpdated {updated}/{len(rows)} factors")
    print(f"  automated: {counts.get('automated', 0)}")
    print(f"  manual: {counts.get('manual', 0)}")
    print(f"  未推断出 (保持 NULL): {len(rows) - updated}")


if __name__ == "__main__":
    main()
