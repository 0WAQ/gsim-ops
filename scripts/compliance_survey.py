#!/usr/bin/env python3
"""compliance 重做批 · 全库持仓摸底(纯只读;2026-07-13 立项)。

背景:现行 compliance 判定(尾窗 762 + 空天静默跳过 + 任一天违规即拒)的
判定基数随数据起始时间漂移。重做前用户拍板**先测量后定策** —— 起始日/
容忍度/有效天数下限等一切阈值,都等全库分布数据出来再定。

本脚本对每个有 alpha_feature v2 的因子,产出**阈值无关的逐日原始统计**
(四列:总敞口 / 最大单股占比 / 多头持股数 / 空头持股数),落 per-factor
npz 缓存 + 全库汇总 CSV。任何候选政策("5% 改 4.5% 多拒多少","容忍
0.5% 天数放行多少")之后都是对 ~1GB 缓存的秒级查询,不再碰 ~1.3TB 原始
feature。

数据源与格式事实(核对自 ops/services/pack/pack.py):
- alpha_feature/<name>.v2.npy 是**裸 memmap 无 npy 头**(pack 用 np.memmap
  直写),shape = (3900, H),H = 文件字节数 / (3900*8),float64;
- 行 i 对应 __universe/Dates.npy 的第 i 个交易日(delay=1 因子有 -1 行
  偏移 —— 对分布统计无影响,只是日期标签差一天,判读时知道即可);
- 全 NaN 行 = 该日无 dump(数据未起 / 空洞),正是有效起始日与 gap 的
  判定依据。

输出(--out 目录):
- universe_dates.npy           行号 → 交易日(int,一次)
- <name>.npz                   逐日四列:total_abs / max_pos_pct(空日 NaN)/
                               long_count / short_count(int,空日 0)
- summary.csv                  每因子一行:name,status,delay,rows,
                               first_valid_row,first_valid_date,
                               last_valid_date,valid_days,
                               gap_days(首末有效日之间的空日数),
                               p50/p95/p99/max of max_pos_pct,
                               min/p05 of long_count/short_count
- coverage-missing.txt         PG 在册但无 feature v2 的名单(name status)

断点续跑:已有 <name>.npz 跳过(重跑只补新增)。--limit N 冒烟;
--factor X 单因子。全程零写生产路径。

用法(160,repo 根目录;~1.3TB 顺序读,建议 nohup):
    uv run python scripts/compliance_survey.py --out ~/compliance-survey
    nohup uv run python scripts/compliance_survey.py --out ~/compliance-survey \
        > ~/compliance-survey.log 2>&1 &
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.core.paths import FactorPaths                          # noqa: E402
from ops.infra.config import Config, get_default_config_path    # noqa: E402
from ops.infra.repository import FactorRepository               # noqa: E402

PACK_L = 3900          # pack.py 同款(行数写死;正主注释见彼处)
CHUNK_ROWS = 512       # 分块读,单因子峰值内存 ~22MB(512*5484*8)


def survey_one(feature: Path, out_npz: Path) -> dict | None:
    """单因子:feature v2 裸 memmap → 逐日四列统计,落 npz。返回汇总行。"""
    nbytes = feature.stat().st_size
    if nbytes % (PACK_L * 8) != 0:
        return {"error": f"字节数 {nbytes} 不是 {PACK_L}*8 的整数倍,非 pack 形状"}
    h = nbytes // (PACK_L * 8)
    mm = np.memmap(feature, mode="r", dtype=np.float64, shape=(PACK_L, h))

    total_abs = np.empty(PACK_L, dtype=np.float64)
    max_abs = np.empty(PACK_L, dtype=np.float64)
    long_cnt = np.empty(PACK_L, dtype=np.int32)
    short_cnt = np.empty(PACK_L, dtype=np.int32)
    for lo in range(0, PACK_L, CHUNK_ROWS):
        hi = min(lo + CHUNK_ROWS, PACK_L)
        blk = np.asarray(mm[lo:hi])                 # 实际读盘发生在这里
        a = np.abs(blk)
        total_abs[lo:hi] = np.nansum(a, axis=1)
        # 全 NaN 行 nanmax 会警告并给 NaN —— 先兜 0 再还原
        with np.errstate(all="ignore"):
            m = np.nanmax(np.where(np.isnan(a), -np.inf, a), axis=1)
        max_abs[lo:hi] = np.where(np.isinf(m), np.nan, m)
        long_cnt[lo:hi] = np.nansum(blk > 0, axis=1)
        short_cnt[lo:hi] = np.nansum(blk < 0, axis=1)
    del mm

    valid = total_abs > 0                            # 有效日:有任何非零持仓
    with np.errstate(invalid="ignore", divide="ignore"):
        max_pos_pct = np.where(valid, max_abs / total_abs, np.nan)

    np.savez_compressed(out_npz,
                        total_abs=total_abs.astype(np.float64),
                        max_pos_pct=max_pos_pct.astype(np.float64),
                        long_count=long_cnt, short_count=short_cnt)

    if not valid.any():
        return {"valid_days": 0}
    idx = np.flatnonzero(valid)
    first, last = int(idx[0]), int(idx[-1])
    vp = max_pos_pct[valid]
    lc, sc = long_cnt[valid], short_cnt[valid]
    return {
        "valid_days": int(valid.sum()),
        "first_valid_row": first,
        "last_valid_row": last,
        "gap_days": int((last - first + 1) - valid.sum()),
        "maxpos_p50": round(float(np.percentile(vp, 50)), 6),
        "maxpos_p95": round(float(np.percentile(vp, 95)), 6),
        "maxpos_p99": round(float(np.percentile(vp, 99)), 6),
        "maxpos_max": round(float(vp.max()), 6),
        "long_min": int(lc.min()), "long_p05": int(np.percentile(lc, 5)),
        "short_min": int(sc.min()), "short_p05": int(np.percentile(sc, 5)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全库持仓摸底(feature v2 → 逐日统计缓存;纯只读,可断点续跑)")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--out", type=Path, required=True,
                        help="输出目录(npz 缓存 + summary.csv)")
    parser.add_argument("--factor", help="只跑一个因子(调试)")
    parser.add_argument("--limit", type=int, help="只跑前 N 个(冒烟)")
    args = parser.parse_args()

    config = Config.load(args.config_path)
    args.out.mkdir(parents=True, exist_ok=True)

    # 行号 → 交易日 标尺(一次)
    dates_file = config.nio_data_path / "__universe" / "Dates.npy"
    dates = np.array(np.memmap(dates_file, mode="r", dtype=int))
    np.save(args.out / "universe_dates.npy", dates)

    repo = FactorRepository(config)
    factors = repo.find(include_submitted=True)
    if args.factor:
        factors = [x for x in factors if x.name == args.factor]
    factors.sort(key=lambda x: x.name)

    rows, missing, done, skipped = [], [], 0, 0
    for i, x in enumerate(factors):
        if args.limit and done + skipped >= args.limit:
            break
        feature = FactorPaths.of(x.name, config).feature("v2")
        status = x.status.value if x.status else "no-state"
        if not feature.is_file():
            missing.append((x.name, status))
            continue
        out_npz = args.out / f"{x.name}.npz"
        if out_npz.exists():
            skipped += 1
            continue
        stat = survey_one(feature, out_npz)
        row = {"name": x.name, "status": status,
               "delay": x.snapshot.delay if x.snapshot else None,
               "rows": PACK_L}
        row.update(stat or {})
        rows.append(row)
        done += 1
        if done % 200 == 0:
            print(f"[{done}] {x.name}", flush=True)

    # summary 追加写(断点续跑时保留已有行;首跑写表头)
    fields = ["name", "status", "delay", "rows", "valid_days",
              "first_valid_row", "first_valid_date", "last_valid_date",
              "last_valid_row", "gap_days",
              "maxpos_p50", "maxpos_p95", "maxpos_p99", "maxpos_max",
              "long_min", "long_p05", "short_min", "short_p05", "error"]
    summary = args.out / "summary.csv"
    new_file = not summary.exists()
    with summary.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in rows:
            if "first_valid_row" in r:
                r["first_valid_date"] = int(dates[r["first_valid_row"]])
                r["last_valid_date"] = int(dates[r["last_valid_row"]])
            w.writerow(r)

    (args.out / "coverage-missing.txt").write_text(
        "".join(f"{n} {s}\n" for n, s in missing))

    print(f"\n完成: 本次统计 {done},续跑跳过 {skipped},"
          f"无 feature v2 {len(missing)}(coverage-missing.txt)")
    print(f"输出: {summary} + {done} 个 npz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
