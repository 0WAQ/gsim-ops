#!/usr/bin/env python3
"""compliance 重做批 · 全库持仓摸底(纯只读;2026-07-13 立项)。

背景:现行 compliance 判定(尾窗 762 + 空天静默跳过 + 任一天违规即拒)的
判定基数随数据起始时间漂移。重做前用户拍板**先测量后定策** —— 起始日/
容忍度/有效天数下限等一切阈值,都等全库分布数据出来再定。

本脚本对每个因子产出**阈值无关的逐日原始统计**(四列:总敞口 / 最大单股占比 /
多头持股数 / 空头持股数),落 per-factor npz 缓存 + 全库汇总 CSV。任何候选政策
("5% 改 4.5% 多拒多少","容忍 0.5% 天数放行多少")之后都是对 ~1GB 缓存的秒级
查询,不再碰原始盘面。

**两个数据源(--source auto 默认 feature 优先 dump 回落)**:
- **feature**:`alpha_feature/<name>.v2.npy`,**裸 memmap 无 npy 头**(pack 直写),
  shape = (3900, H),H = 字节数 / (3900*8),float64。快、JFS 共享,但只在
  **packed(≈ACTIVE)** 因子上有。
- **dump**:`alpha_dump/<name>/YYYY/MM/<yyyymmdd>*.v2.npy`,逐日 (H,) 向量。
  **被拒因子(compliance/correlation)无 feature,只有 dump** —— 补上 dump 才覆盖
  完整判定域(定阈值最需要看的正是被拒尾巴)。dump 是**本机 sidecar**,dump 路径
  只能在持有该因子 dump 的机器上跑(消费/check 机)。
- 两源同 npz 格式(PACK_L 行);全 NaN / 零敞口行 = 该日无有效持仓(数据未起 /
  空洞),正是有效起始日与 gap 的判据。**逐日分布统计(百分位 / 计数 / valid_days /
  gap)与数据源无关**:每日四元组两源逐位等价(feature 的 nansum-over-padded 与
  dump 的 sum-over-valid 仅差 ~1e-16 FP 舍入,远低于任何阈值),feature 的 pack 行
  偏移只是常量平移,不改多重集。**唯一源相关处**:feature 行号含 delay 偏移
  (delay=1 时行 i 存的是交易日 i+1 的 dump),故 first/last_valid_date 在 delay=1
  feature-读因子上比真实 dump 日早 ≤1 交易日 —— 只动日期标签、不碰阈值分布;dump
  路径无偏移,标签与 compliance checker 的文件名日期一致。跨源精确对齐(把 rejected
  因子的 dump 折进来时)留作后续。

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
- coverage-missing.txt         feature 与本机 dump 都没有的名单(name status)

断点续跑:已有 <name>.npz 跳过(重跑只补新增)。--limit N 冒烟;
--factor X 单因子。全程零写生产路径。summary 的 `source` 列标每因子的数据来源。

用法(持有 dump 的机器,repo 根目录;顺序读大,建议 nohup):
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

from ops.core.paths import FactorPaths  # noqa: E402
from ops.infra.config import Config, get_default_config_path  # noqa: E402
from ops.infra.repository import FactorRepository  # noqa: E402
from ops.services.check.checker.dumpscan import v2npy_files  # noqa: E402

PACK_L = 3900          # pack.py 同款(行数写死;正主注释见彼处)
CHUNK_ROWS = 512       # 分块读,单因子峰值内存 ~22MB(512*5484*8)


def _empty_rows():
    return (np.zeros(PACK_L, np.float64), np.full(PACK_L, np.nan, np.float64),
            np.zeros(PACK_L, np.int32), np.zeros(PACK_L, np.int32))


def _summarize(total_abs, max_abs, long_cnt, short_cnt, out_npz: Path) -> dict:
    """四组逐日数组 → 落 npz + 汇总行(feature / dump 两路径共用,格式一致)。"""
    valid = total_abs > 0                            # 有效日:有任何非零持仓
    with np.errstate(invalid="ignore", divide="ignore"):
        max_pos_pct = np.where(valid, max_abs / total_abs, np.nan)
    np.savez_compressed(out_npz,
                        total_abs=total_abs.astype(np.float64),
                        max_pos_pct=max_pos_pct.astype(np.float64),
                        long_count=long_cnt.astype(np.int32),
                        short_count=short_cnt.astype(np.int32))
    if not valid.any():
        return {"valid_days": 0}
    idx = np.flatnonzero(valid)
    first, last = int(idx[0]), int(idx[-1])
    vp = max_pos_pct[valid]
    lc, sc = long_cnt[valid], short_cnt[valid]
    # min_total_stocks 判据是逐日 long+short 之和的下限;long_min/short_min 可能落在
    # 不同天,min(a+b)≠min(a)+min(b),故单列 —— 四条 compliance 阈值全从 summary 可复算
    tot = lc + sc
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
        "total_min": int(tot.min()), "total_p05": int(np.percentile(tot, 5)),
    }


def survey_one_feature(feature: Path, out_npz: Path) -> dict:
    """feature v2 裸 memmap((PACK_L, H) 矩阵)→ 逐日四列统计。快、JFS 共享,
    但只在 packed(≈ACTIVE)因子上有。"""
    nbytes = feature.stat().st_size
    if nbytes % (PACK_L * 8) != 0:
        return {"error": f"字节数 {nbytes} 不是 {PACK_L}*8 的整数倍,非 pack 形状"}
    h = nbytes // (PACK_L * 8)
    mm = np.memmap(feature, mode="r", dtype=np.float64, shape=(PACK_L, h))
    total_abs, max_abs, long_cnt, short_cnt = _empty_rows()
    for lo in range(0, PACK_L, CHUNK_ROWS):
        hi = min(lo + CHUNK_ROWS, PACK_L)
        blk = np.asarray(mm[lo:hi])                 # 实际读盘发生在这里
        a = np.abs(blk)
        total_abs[lo:hi] = np.nansum(a, axis=1)
        # 全 NaN 行 nanmax 会警告并给 NaN —— 先兜 0 再还原
        with np.errstate(all="ignore"):
            m = np.nanmax(np.where(np.isnan(a), -np.inf, a), axis=1)
        # 只把全 NaN 行的 -inf 哨兵还原成 NaN;真 +inf(不该出现的坏权重)保留,
        # 不静默吞成 NaN —— 与 checker 一致地让坏数据显形而非消失
        max_abs[lo:hi] = np.where(m == -np.inf, np.nan, m)
        long_cnt[lo:hi] = np.nansum(blk > 0, axis=1)
        short_cnt[lo:hi] = np.nansum(blk < 0, axis=1)
    del mm
    return _summarize(total_abs, max_abs, long_cnt, short_cnt, out_npz)


def survey_one_dump(dump_dir: Path, date_to_idx: dict, out_npz: Path) -> dict:
    """逐日 dump 文件(<yyyymmdd>*.v2.npy,各 (H,) 向量)→ 同格式统计。

    dump 是被拒因子(无 feature)的唯一持仓来源——feature 只覆盖 packed 因子,
    补上 dump 才覆盖完整 compliance 判定域(active + compliance/correlation 被拒)。
    按 universe 交易日映射到行,与 feature 路径同 npz 格式;不做 delay 偏移 —— dump
    的行 = 文件名日期直落 date_to_idx,与 compliance checker 的日期语义一致(见模块
    docstring 的源相关性说明)。dump 是本机 sidecar,须在持有 dump 的机器上跑。
    """
    files = v2npy_files(dump_dir)
    if not files:
        return {"error": "dump 目录无 v2 文件"}
    total_abs, max_abs, long_cnt, short_cnt = _empty_rows()
    placed = 0
    for f in files:
        try:
            di = date_to_idx.get(int(f.name[:8]))
        except ValueError:
            continue
        # di 越界(>=PACK_L)= 20251231 后的日增日:在 pack 水平线外、feature 侧也无,
        # 与 pack.py 同域丢弃(否则 total_abs[di] 越界会崩整轮扫描)。di 恒 >=0(枚举下标)。
        if di is None or di >= PACK_L:               # None=日期不在 universe(未来日等)
            continue
        try:
            data = np.load(f)
        except Exception:
            continue
        v = data[~np.isnan(data)]
        ta = float(np.sum(np.abs(v))) if v.size else 0.0
        if ta == 0:                                  # 空/全 NaN/零敞口 = 无效日
            continue
        total_abs[di] = ta
        max_abs[di] = float(np.max(np.abs(v)))
        long_cnt[di] = int((v > 0).sum())
        short_cnt[di] = int((v < 0).sum())
        placed += 1
    if placed == 0:
        return {"valid_days": 0}
    return _summarize(total_abs, max_abs, long_cnt, short_cnt, out_npz)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全库持仓摸底(feature v2 → 逐日统计缓存;纯只读,可断点续跑)")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--out", type=Path, required=True,
                        help="输出目录(npz 缓存 + summary.csv)")
    parser.add_argument("--factor", help="只跑一个因子(调试)")
    parser.add_argument("--limit", type=int, help="只跑前 N 个(冒烟)")
    parser.add_argument("--source", choices=["auto", "feature", "dump"],
                        default="auto",
                        help="数据源:auto=feature 优先 dump 回落(默认,覆盖完整"
                             "判定域)/ feature=仅 packed / dump=仅本机 dump")
    args = parser.parse_args()

    config = Config.load(args.config_path)
    args.out.mkdir(parents=True, exist_ok=True)

    # 行号 → 交易日 标尺(一次);dump 路径按此把 <yyyymmdd> 映射到行
    dates_file = config.nio_data_path / "__universe" / "Dates.npy"
    dates = np.array(np.memmap(dates_file, mode="r", dtype=int))
    date_to_idx = {int(d): i for i, d in enumerate(dates)}
    np.save(args.out / "universe_dates.npy", dates)

    repo = FactorRepository(config)
    factors = repo.find(include_submitted=True)
    if args.factor:
        factors = [x for x in factors if x.name == args.factor]
    factors.sort(key=lambda x: x.name)

    rows, missing, done, skipped = [], [], 0, 0
    src_counts = {"feature": 0, "dump": 0}
    for x in factors:
        if args.limit and done + skipped >= args.limit:
            break
        paths = FactorPaths.of(x.name, config)
        status = x.status.value if x.status else "no-state"
        out_npz = args.out / f"{x.name}.npz"
        if out_npz.exists():
            skipped += 1
            continue

        # 源选择:feature 优先(快、JFS),无则回落 dump(本机 sidecar)
        feature = paths.feature("v2")
        use_feature = feature.is_file() and args.source in ("auto", "feature")
        use_dump = (not use_feature and paths.dump.is_dir()
                    and args.source in ("auto", "dump"))
        if use_feature:
            stat, source = survey_one_feature(feature, out_npz), "feature"
        elif use_dump:
            stat, source = survey_one_dump(paths.dump, date_to_idx, out_npz), "dump"
        else:
            missing.append((x.name, status))
            continue

        row = {"name": x.name, "status": status, "source": source,
               "delay": x.snapshot.delay if x.snapshot else None, "rows": PACK_L}
        row.update(stat or {})
        rows.append(row)
        src_counts[source] += 1
        done += 1
        if done % 200 == 0:
            print(f"[{done}] {x.name} ({source})", flush=True)

    # summary 追加写(断点续跑时保留已有行;首跑写表头)
    fields = ["name", "status", "source", "delay", "rows", "valid_days",
              "first_valid_row", "first_valid_date", "last_valid_date",
              "last_valid_row", "gap_days",
              "maxpos_p50", "maxpos_p95", "maxpos_p99", "maxpos_max",
              "long_min", "long_p05", "short_min", "short_p05",
              "total_min", "total_p05", "error"]
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

    print(f"\n完成: 本次统计 {done}(feature {src_counts['feature']} / "
          f"dump {src_counts['dump']}),续跑跳过 {skipped},"
          f"无源可用 {len(missing)}(coverage-missing.txt)")
    print(f"输出: {summary} + {done} 个 npz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
