#!/usr/bin/env python3
"""compliance 重做批 · 影子对比表(发布材料收尾,纯本地 join)。

输入(全在 report/compliance-survey/):
- compliance-rejected.csv   PG 导出:现役 rejected 中最近一次失败 stage=compliance
                            的因子(name,rejected_on,fail_reason;导出 SQL 见
                            docs/design/compliance-survey.md)
- violations.csv / summary.csv / coverage-missing.txt   摸底产物

输出:每条 compliance-rejected 因子在**新规则**(全史每日 + 容忍 10 + 硬顶 10%)
下的判定标签 + 汇总,落 shadow-compliance-rejected.csv 并打印。标签:

- 硬顶仍拒     maxpos_max > 10%(单日灾难,新旧一致拒)
- 超容忍仍拒   viol_days > 10(持续违规,新旧一致拒)
- 转放行(毛刺) 1 <= viol_days <= 10(政策有意豁免;能否入库还要过其它 stage)
- 零违规需查   摸底数据里一天都没违规 —— 当年拒但现数据干净,通常是 dump 已被
               重新生成 / 当年判定窗口数据不同,逐条人工看
- 无源盲区     feature 与本机 dump 双缺,摸底没覆盖(须去持有其 dump 的机器补)
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "report" / "compliance-survey"
HARD = 0.10          # max_position_pct(0.05) × hard_position_mult(2.0),同 config
TOLERANCE = 10       # violation_tolerance,同 config


def main() -> int:
    rejected_csv = DIR / "compliance-rejected.csv"
    if not rejected_csv.is_file():
        sys.exit(f"缺 {rejected_csv} —— 先跑 runbook 里的 PG 导出 SQL")

    vio = {r["name"]: r for r in csv.DictReader((DIR / "violations.csv").open())}
    summ = {r["name"]: r for r in csv.DictReader((DIR / "summary.csv").open())}
    missing = {line.split()[0] for line in
               (DIR / "coverage-missing.txt").read_text().splitlines() if line}

    rows = []
    for r in csv.DictReader(rejected_csv.open()):
        name = r["name"]
        out = {"name": name, "rejected_on": r.get("rejected_on", ""),
               "viol_days": "", "maxpos_max": "",
               "fail_reason": (r.get("fail_reason") or "")[:80]}
        v, s = vio.get(name), summ.get(name)
        if v is None:
            out["verdict"] = "无源盲区" if name in missing else "不在摸底内(需查)"
        else:
            viol_days = int(v["viol_days"] or 0)
            maxpos = float(s["maxpos_max"]) if s and s["maxpos_max"] else 0.0
            out["viol_days"] = viol_days
            out["maxpos_max"] = f"{maxpos:.4f}"
            if maxpos > HARD:
                out["verdict"] = "硬顶仍拒"
            elif viol_days > TOLERANCE:
                out["verdict"] = "超容忍仍拒"
            elif viol_days > 0:
                out["verdict"] = "转放行(毛刺)"
            else:
                out["verdict"] = "零违规需查"
        rows.append(out)

    order = ["硬顶仍拒", "超容忍仍拒", "转放行(毛刺)", "零违规需查",
             "无源盲区", "不在摸底内(需查)"]
    rows.sort(key=lambda r: (order.index(r["verdict"]),
                             -(r["viol_days"] or 0) if isinstance(r["viol_days"], int) else 0,
                             r["name"]))

    out_csv = DIR / "shadow-compliance-rejected.csv"
    fields = ["name", "verdict", "viol_days", "maxpos_max",
              "rejected_on", "fail_reason"]
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"compliance-rejected {len(rows)} 条 → {out_csv}\n")
    print(f"{'verdict':14s} {'viol':>5s} {'maxpos':>7s}  name")
    for r in rows:
        print(f"{r['verdict']:14s} {str(r['viol_days']):>5s} "
              f"{str(r['maxpos_max']):>7s}  {r['name']}")
    print(f"\n汇总: {dict(Counter(r['verdict'] for r in rows))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
