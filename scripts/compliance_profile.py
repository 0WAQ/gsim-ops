#!/usr/bin/env python3
"""compliance 重做批 · 违规画像(survey 缓存的后处理,纯本地)。

回答 summary.csv 回答不了的问题:**违规是毛刺还是持续?落在因子生命周期的
哪一段?** —— 现行"任一天违规即拒"是否过严、容忍度定多少、要不要跳过暖机
头部,全取决于违规天的数量与位置分布,而 summary 只有全窗极值。

输入是 compliance_survey.py 的 --out 目录(npz 逐日缓存 + summary.csv +
universe_dates.npy)。不碰生产路径、不需要 config/PG —— 在持有缓存的机器上
直接跑,分钟级。

阈值默认取现行 config 值(阈值不变是已拍的前提,变的是聚合规则),算符逐位
对齐 ComplianceChecker._check_position:maxpos 严格 >、三个 count 严格 <、
无效日(空/全 NaN/零敞口,即缓存里 total_abs==0)跳过不计。

每因子输出列(violations.csv):
- valid_days / viol_days / viol_frac      违规天数与占比(any 口径)
- viol_maxpos / viol_long / viol_short / viol_total   分规则违规天数
- max_streak                              最长连续违规(有效日序列上,gap 不断链)
- viol_head252                            首 252 个有效日内的违规数(暖机集中度)
- viol_tail762                            末 762 个有效日内的违规数(≈旧 checker
                                          窗口)。旧窗是末 762 个 dump 文件(含 skip
                                          日),本列是末 762 个有效日 —— 数学上是旧
                                          判定的**上包络**:结构上不漏报任何旧拒(无
                                          假阴),仅在近端有 skip 日时偏严多报。**两
                                          源语义不同**:dump 源(compliance-rejected)
                                          窗口尾端对齐当年 check 日、逐日数字与 checker
                                          逐位相同,是"复现旧判定"的正解;feature 源
                                          (ACTIVE)尾端锚在 pack 水平线(20251231)、
                                          非当年 check 日,故其 tail762 读作"当前尾窗
                                          是否仍违规",不是当年判定复现。
- clean_suffix_days                       最后一个违规日之后的干净有效日数
                                          (旧规则本质 = clean_suffix >= 窗口长)
- first_viol_date / last_viol_date        违规首末日(delay=1 的 feature 源标签
                                          早 ≤1 交易日,见 survey docstring)

用法(在跑过 survey 的机器上):
    uv run python scripts/compliance_profile.py --cache ~/compliance-survey
产出 <cache>/violations.csv + 终端聚合速报;断点无关(全量重算,秒~分钟级)。
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import numpy as np

PACK_L = 3900          # survey 写死的 npz 行数(正主在 compliance_survey.py);
                       # universe_dates.npy 标尺须 >= 它,否则行→日期映射越界


def _longest_run(mask: np.ndarray) -> int:
    """布尔序列里最长连续 True 段的长度。"""
    if not mask.any():
        return 0
    m = np.concatenate(([False], mask, [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return int((ends - starts).max())


def profile_one(npz_path: Path, dates: np.ndarray, args) -> dict:
    d = np.load(npz_path)
    total_abs = d["total_abs"]
    maxpos = d["max_pos_pct"]
    lc = d["long_count"].astype(np.int64)
    sc = d["short_count"].astype(np.int64)

    valid = total_abs > 0
    n = int(valid.sum())
    row = {"name": npz_path.stem, "valid_days": n}
    if n == 0:
        return row

    vi = np.flatnonzero(valid)
    # maxpos[vi] 可能含 NaN(单股 +inf 坏权重 → inf/inf=NaN):NaN > 阈值 = False,
    # 该日不计违规 —— 与 checker 逐位一致(它同样 inf/inf=NaN 不触发)。compliance
    # 不是坏权重的守门人(该由 checkbias/回测更早拦);errstate 压 NaN 比较的告警。
    with np.errstate(invalid="ignore"):
        v_max = maxpos[vi] > args.max_pos
    v_long = lc[vi] < args.min_long
    v_short = sc[vi] < args.min_short
    v_total = (lc[vi] + sc[vi]) < args.min_total
    v_any = v_max | v_long | v_short | v_total

    viol = int(v_any.sum())
    row.update({
        "viol_days": viol,
        "viol_frac": round(viol / n, 6),
        "viol_maxpos": int(v_max.sum()),
        "viol_long": int(v_long.sum()),
        "viol_short": int(v_short.sum()),
        "viol_total": int(v_total.sum()),
        "max_streak": _longest_run(v_any),
        # max(0,·):head 负数时 v_any[:-k] 会算成"除末 k 天外全部"(与 tail 的
        # v_any[-0:]=整段 同类切片陷阱),显式钳到空窗
        "viol_head252": int(v_any[:max(0, args.head)].sum()),
        "viol_tail762": int(v_any[-args.tail:].sum()) if args.tail > 0 else 0,
    })
    if viol:
        pos = np.flatnonzero(v_any)
        row["first_viol_date"] = int(dates[vi[pos[0]]])
        row["last_viol_date"] = int(dates[vi[pos[-1]]])
        row["clean_suffix_days"] = n - 1 - int(pos[-1])
    else:
        row["clean_suffix_days"] = n
    return row


def _report(rows: list[dict]) -> None:
    """终端速报:验证跑通 + 给判读一个第一眼。细切片对着 CSV 做。"""
    by_status = Counter(r["status"] for r in rows)
    violators = [r for r in rows if r.get("viol_days", 0) > 0]
    print(f"\n因子 {len(rows)}({dict(by_status)});有违规日 {len(violators)}")
    if not violators:
        return
    vd = sorted(r["viol_days"] for r in violators)
    print(f"violators 的 viol_days: p50={vd[len(vd)//2]} "
          f"p90={vd[len(vd)*9//10]} max={vd[-1]}")
    # 粗分型:毛刺(≤5 天且连违 ≤3)/ 头部集中(违规全落首 252 有效日)/ 持续
    blip = sum(1 for r in violators
               if r["viol_days"] <= 5 and r["max_streak"] <= 3)
    headonly = sum(1 for r in violators
                   if r["viol_days"] == r["viol_head252"])
    persistent = sum(1 for r in violators if r["viol_frac"] > 0.05)
    print(f"粗分型: 毛刺(≤5天,连违≤3) {blip} / 违规全在头部252 {headonly} / "
          f"持续(>5%天数) {persistent}")
    for st in ("active", "rejected", "submitted"):
        sub = [r for r in violators if r["status"] == st]
        tail = sum(1 for r in sub if r["viol_tail762"] > 0)
        print(f"  {st}: 违规者 {len(sub)},其中尾窗762内仍违规 {tail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="violation 画像(survey npz 缓存 → 每因子违规天数/位置分布)")
    parser.add_argument("--cache", type=Path, required=True,
                        help="compliance_survey.py 的 --out 目录")
    parser.add_argument("--out", type=Path,
                        help="输出 CSV(默认 <cache>/violations.csv)")
    parser.add_argument("--max-pos", type=float, default=0.05)
    parser.add_argument("--min-long", type=int, default=50)
    parser.add_argument("--min-short", type=int, default=50)
    parser.add_argument("--min-total", type=int, default=100)
    parser.add_argument("--head", type=int, default=252,
                        help="头部窗口(有效日),暖机集中度判据")
    parser.add_argument("--tail", type=int, default=762,
                        help="尾部窗口(有效日),≈旧 checker 窗口")
    args = parser.parse_args()
    out = args.out or args.cache / "violations.csv"

    dates = np.load(args.cache / "universe_dates.npy")
    # npz 恒 PACK_L 行,行号直接索引 dates;标尺短于 PACK_L 时 first/last_viol_date
    # 会越界(feature 源若在 row>=len(dates) 有非零数据即触发)。显式响亮失败,
    # 而非让 profile_one 半路抛裸 IndexError —— 缓存的 universe 与打包 universe 不
    # 是同一个(survey 的 --config-path 指了短 universe?)才会发生,需重跑 survey。
    if len(dates) < PACK_L:
        sys.exit(f"universe_dates.npy 长 {len(dates)} < npz 行数 {PACK_L}:"
                 f"缓存标尺与 npz 不匹配,请用打包时的同一 universe 重跑 survey")

    # summary.csv 供 status/source/delay(身份列);npz 才是逐日数据正主
    meta: dict[str, dict] = {}
    with (args.cache / "summary.csv").open() as f:
        for r in csv.DictReader(f):
            meta[r["name"]] = {"status": r["status"], "source": r["source"],
                               "delay": r["delay"]}

    npzs = sorted(args.cache.glob("*.npz"))
    rows = []
    for i, p in enumerate(npzs, 1):
        row = profile_one(p, dates, args)
        row.update(meta.get(row["name"],
                            {"status": "?", "source": "?", "delay": ""}))
        rows.append(row)
        if i % 1000 == 0:
            print(f"[{i}/{len(npzs)}]", flush=True)

    fields = ["name", "status", "source", "delay", "valid_days",
              "viol_days", "viol_frac",
              "viol_maxpos", "viol_long", "viol_short", "viol_total",
              "max_streak", "viol_head252", "viol_tail762",
              "clean_suffix_days", "first_viol_date", "last_viol_date"]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # 两侧孤儿是不同集合,分开报(旧写法 len(meta)-len(rows) 是净差,npz 多于
    # summary 时变负且标签误导):summary_only = 有摘要行但无 npz(零有效日/出错,
    # 天然无画像);npz_only = 有 npz 但摘要无行(survey 中断残留,status 标 '?')。
    npz_names = {r["name"] for r in rows}
    summary_only = [n for n in meta if n not in npz_names]
    npz_only = [r["name"] for r in rows if r["name"] not in meta]
    print(f"\n输出: {out}({len(rows)} 行)")
    if summary_only:
        print(f"  summary 有行无 npz {len(summary_only)} 个(零有效日/出错,无画像)")
    if npz_only:
        print(f"  npz 无 summary 行 {len(npz_only)} 个(survey 中断残留,status='?')")
    _report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
