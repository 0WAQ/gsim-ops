#!/usr/bin/env python3
"""v3 迁移:测得快照回填 + created_at 修正(schema-v3,2026-07-13)。

A. created_at := submitted_at —— 修正 created_at > submitted_at 的违反行
   (81 个 fguo,07-10 凌晨成因不明批量写,用户拍板不追直接改);合成
   submit 事件(actor='migration')的 at 同步修正,timeline 倒挂消解。
B. 测得快照回填:REJECTED 且无快照、最近失败在 correlation 的因子,
   从 fail_reason 解析 ret/shrp/mdd/tvr/fitness/bcorr(全库 738 条格式
   已验证),meta.json 补 delay + datasources,snapshot_at = 该次 check
   事件的 at。checkbias/checkpoint 无测得值不回填;compliance(22)留待
   可选人工补跑。

缺省 dry-run(只打印计划);--apply 执行。幂等:B 只补"无快照"的因子,
A 的 UPDATE 条件收敛后二跑零命中。

用法(160):
  python3 migrate_v3_measured_snapshots.py \
    --conninfo "host=127.0.0.1 port=15432 dbname=ops user=ops password=..." \
    --alpha-src /tank/vault/alphalib/alpha_src [--apply]
"""
import argparse
import json
import re
from pathlib import Path

import psycopg

_TOKEN = re.compile(r"(bcorr|ret|shrp|mdd|tvr|fitness)%?=(-?\d+(?:\.\d+)?)%?")


# 字段级合理域(生产 dry-run 实测:老 fail_reason 有 fitness=20150115 这类
# 日期错位脏值 —— 疑似当年 simsummary 负收益行列错位,原样入库即脏快照)。
# 越界字段单独置 None(隔离该字段,不弃整行),计数上报判读。
_BOUNDS = {"ret": (-1000, 1000), "shrp": (-50, 50), "mdd": (0, 100),
           "tvr": (0, 1000), "fitness": (-1000, 1000), "bcorr": (-1, 1)}


def parse_metrics(reason: str) -> tuple[dict[str, float], list[str]]:
    """fail_reason → (指标 dict, 越界被隔离的字段名)。key=value 扫描,
    同键取最后一次出现(阈值段与全量段重复时两处同值)。"""
    out: dict[str, float] = {}
    quarantined: list[str] = []
    for k, v in _TOKEN.findall(reason or ""):
        out[k] = float(v)
    for k in list(out):
        lo, hi = _BOUNDS[k]
        if not (lo <= out[k] <= hi):
            quarantined.append(f"{k}={out[k]}")
            out[k] = None  # type: ignore[assignment]
    return out, quarantined


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conninfo", required=True)
    ap.add_argument("--alpha-src", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = psycopg.connect(args.conninfo)
    conn.execute("SET TIME ZONE 'Asia/Shanghai'")

    # ---- A. created_at 修正(前置打印违反行数)
    viol = conn.execute(
        "SELECT count(*) FROM factor_info i JOIN factor_state s USING (name) "
        "WHERE s.submitted_at IS NOT NULL AND i.created_at > s.submitted_at"
    ).fetchone()[0]
    print(f"[A] created_at > submitted_at 违反行: {viol}")

    # ---- B. 回填候选:REJECTED、无快照、最近失败 = correlation
    rows = conn.execute("""
        SELECT s.name, lf.at, lf.fail_reason
        FROM factor_state s
        LEFT JOIN factor_snapshot n USING (name)
        JOIN LATERAL (
            SELECT at, failed_stage, fail_reason FROM factor_history h
            WHERE h.name = s.name AND h.op = 'check' AND h.passed = FALSE
            ORDER BY at DESC, id DESC LIMIT 1
        ) lf ON TRUE
        WHERE s.status = 'rejected' AND n.name IS NULL
          AND lf.failed_stage = 'correlation'
        ORDER BY s.name
    """).fetchall()

    plans, skipped, dirty = [], [], []
    for name, at, reason in rows:
        m, quarantined = parse_metrics(reason)
        if quarantined:
            dirty.append((name, quarantined))
        if "ret" not in m:
            skipped.append((name, "fail_reason 无指标"))
            continue
        delay = fields = tables = None
        meta_path = args.alpha_src / name / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
            delay = meta.get("delay")
            ds = meta.get("datasources") or {}
            fields = ds.get("fields") or None
            tables = ds.get("tables") or None
        except OSError:
            skipped.append((name, "meta.json 不可读(仅指标回填)"))
        plans.append((name, at, m, delay, fields, tables))

    print(f"[B] 回填候选 {len(rows)},可回填 {len(plans)},跳过 {len(skipped)},"
          f"含越界隔离字段 {len(dirty)}")
    for n, why in skipped[:10]:
        print(f"    skip {n}: {why}")
    for n, q in dirty[:20]:
        print(f"    dirty {n}: {', '.join(q)}(该字段置 None,其余照填)")
    for n, at, m, delay, _f, _t in plans[:5]:
        print(f"    plan {n}: at={at} {m} delay={delay}")

    if not args.apply:
        print("(dry-run;--apply 执行)")
        return

    with conn.transaction():
        # A:合成 submit 事件先修(引用旧 created_at 前),再修 created_at
        n1 = conn.execute(
            "UPDATE factor_history h SET at = s.submitted_at "
            "FROM factor_state s, factor_info i "
            "WHERE h.name = s.name AND i.name = s.name AND h.op = 'submit' "
            "AND h.actor = 'migration' AND s.submitted_at IS NOT NULL "
            "AND i.created_at > s.submitted_at AND h.at = i.created_at"
        ).rowcount
        n2 = conn.execute(
            "UPDATE factor_info i SET created_at = s.submitted_at "
            "FROM factor_state s WHERE i.name = s.name "
            "AND s.submitted_at IS NOT NULL AND i.created_at > s.submitted_at"
        ).rowcount
        print(f"[A] submit 事件修正 {n1} 行,created_at 修正 {n2} 行")
        for name, at, m, delay, fields, tables in plans:
            conn.execute(
                "INSERT INTO factor_snapshot (name, ret, shrp, mdd, tvr, fitness, "
                "fields, tables, delay, max_bcorr, snapshot_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (name, m.get("ret"), m.get("shrp"), m.get("mdd"), m.get("tvr"),
                 m.get("fitness"), fields, tables, delay, m.get("bcorr"), at))
        print(f"[B] 已回填 {len(plans)} 条测得快照")
    conn.close()


if __name__ == "__main__":
    main()
