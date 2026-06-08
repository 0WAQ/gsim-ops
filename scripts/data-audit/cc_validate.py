#!/usr/bin/env python3
"""
cc 单 root 数据质量校验
=====================

针对一个 cc root, 扫所有 .npy 数据文件, 给每个文件出质量报告:
- 全 0 / 全 NaN / 部分 NaN
- inf / -inf 数量
- value/volume/trades 类是否含负值 (按字段名识别)
- 数值范围 (min/max/mean) 是否合理

输出 JSON, 默认尾部 trim 最后一日 (排除 build_cc enddate 那天 NaN 占位)。

用法:
    python cc_validate.py --root /datasvc/data/cc_all --out cc_validate_report.json
    python cc_validate.py --root /tank/vault/datasvc/data/cc_2025 --out report.json --filter 'AShareMoneyFlow*'

退出码:
    0 = 没发现严重问题
    1 = 有 critical (全 0 文件 / 应非负字段含负值)
"""
import argparse
import fnmatch
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

# 默认跳的目录 (跟 cc_fingerprint.py 一致)
# 注: 3D / 1D 已支持, 这里只跳真正的特殊目录
SKIP_DIRS = {
    'cn_equity', 'cn_equity_feature', 'cn_equity_feature_5min', 'realtime',
    'delta',  # per-date 切片, 形状不规则
    '__universe',  # 元数据, 不是数据
    'ALL', 'ALL_GIM', 'ALL_TRD', 'FULL',
    'HS300', 'ZZ500', 'ZZ1000', 'ipo',
    'TOP1000', 'TOP1500', 'TOP2000', 'TOP2600', 'TOP3000', 'TOP3300', 'TOP4000',
}

# 3D 数据的可能 K 值 (中间轴): 49=5min bars, 12=季度 fore, 3=年度 fore
KNOWN_3D_K = [49, 12, 3]

# 按字段名识别"应非负"
NONNEG_KEYWORDS = ['_value_', '_volume_', '_trades_', '_count', 'turnover', 'amo',
                   'vol', 'open', 'high', 'low', 'close', 'price', 'cap', 'weight',
                   'shares']
# 应非负但允许 0 / 例外 (这些字段名出现就跳过非负检查)
NONNEG_EXCEPTIONS = ['ret', 'pctchange', 'change', 'inflow', 'diff', '_act',
                     'pct', 'rate', 'net_', 'pct_value', 'pct_volume',
                     'moneyflow', 'mfd_inflow', 'value_diff', 'volume_diff']


def is_nonneg_field(name: str) -> bool:
    """field name 推断是否应该非负 (粗略, 仅警告级别)."""
    n = name.lower()
    if any(e in n for e in NONNEG_EXCEPTIONS):
        return False
    return any(k in n for k in NONNEG_KEYWORDS)


def infer_shape(file_size: int, n_inst: int):
    """
    推断 .npy 形状.
    Returns ((shape_tuple), dtype, ndim, err_msg).
    Order: 3D float64 (T, K, N) > 2D float64 > 2D int8 > 1D float64.
    3D 优先, 因为 3D float64 文件大小同时被 N*8 整除 (会被 2D 误抓).
    用 "T 在合理范围 1000-10000" 来排除噪声 K 匹配.
    """
    if file_size == 0:
        return None, None, None, "empty file"

    # 3D float64 (T, K, N) 优先 — Interval5m 等
    for K in KNOWN_3D_K:
        denom = K * n_inst * 8
        if file_size % denom == 0:
            T = file_size // denom
            if 1000 <= T <= 10000:  # 合理的 cc T 范围
                return (T, K, n_inst), 'float64', 3, None

    # 2D float64
    row_bytes = n_inst * 8
    if file_size % row_bytes == 0:
        T = file_size // row_bytes
        if 1000 <= T <= 10000:
            return (T, n_inst), 'float64', 2, None

    # 2D int8
    if file_size % n_inst == 0:
        T = file_size // n_inst
        if 1000 <= T <= 10000:
            return (T, n_inst), 'int8', 2, None

    # 1D float64
    if file_size % 8 == 0:
        T = file_size // 8
        if 1000 <= T <= 10000:
            return (T,), 'float64', 1, None

    return None, None, None, f"unfit shape: size={file_size} N={n_inst}"


def validate_file(npy: Path, n_inst: int, cutoff_idx: int, trim_last: int):
    """
    扫一个 .npy, 返回质量摘要 dict 或 skip 原因.
    支持 1D (T,) / 2D (T, N) / 3D (T, K, N) float64 + 2D (T, N) int8.
    """
    try:
        size = npy.stat().st_size
    except OSError as e:
        return None, f"stat fail: {e}"

    shape, dtype, ndim, err = infer_shape(size, n_inst)
    if shape is None:
        return None, err
    T_full = shape[0]

    t_end = min(cutoff_idx, T_full)
    t_end = max(0, t_end - trim_last)
    if t_end == 0:
        return None, "no rows in cutoff after trim"

    arr = np.memmap(npy, dtype=dtype, mode='r', shape=shape)
    slc = np.asarray(arr[:t_end])

    total_cells = slc.size
    flat = slc.ravel()

    if dtype == 'float64':
        m_nan = np.isnan(flat)
        m_finite = np.isfinite(flat)
        m_posinf = np.isposinf(flat)
        m_neginf = np.isneginf(flat)
        n_nan = int(m_nan.sum())
        n_finite = int(m_finite.sum())
        n_posinf = int(m_posinf.sum())
        n_neginf = int(m_neginf.sum())
        if n_finite == 0:
            stats = {'min': None, 'max': None, 'mean': None, 'n_neg': 0, 'n_zero': 0, 'n_pos': 0}
        else:
            finite = flat[m_finite]
            stats = {
                'min': float(finite.min()),
                'max': float(finite.max()),
                'mean': float(finite.mean()),
                'n_neg': int((finite < 0).sum()),
                'n_zero': int((finite == 0).sum()),
                'n_pos': int((finite > 0).sum()),
            }
    else:  # int8
        n_nan = 0  # int8 没有 NaN
        n_finite = total_cells
        n_posinf = n_neginf = 0
        stats = {
            'min': int(slc.min()),
            'max': int(slc.max()),
            'mean': float(slc.mean()),
            'n_neg': int((slc < 0).sum()),
            'n_zero': int((slc == 0).sum()),
            'n_pos': int((slc > 0).sum()),
        }

    # 每天 NaN 数 (沿非 T 轴归约), 用来 detect 全 NaN 天 / 全 finite 天 + 有效数据范围
    if dtype == 'float64':
        if ndim == 1:
            # 1D (T,): 每"天"就一个值, NaN per day 等于该值是否 NaN
            nan_per_day = np.isnan(slc).astype('int64')
            cells_per_day = 1
        elif ndim == 2:
            nan_per_day = np.isnan(slc).sum(axis=1)
            cells_per_day = n_inst
        else:  # 3D (T, K, N)
            nan_per_day = np.isnan(slc).sum(axis=(1, 2))
            cells_per_day = slc.shape[1] * slc.shape[2]
        all_nan_days = int((nan_per_day == cells_per_day).sum())
        no_nan_days = int((nan_per_day == 0).sum())
        data_day_mask = nan_per_day < cells_per_day
    else:
        all_nan_days = no_nan_days = 0
        data_day_mask = np.ones(t_end, dtype=bool) if t_end > 0 else np.zeros(0, dtype=bool)

    # 找首末有效数据 idx
    if data_day_mask.any():
        valid_idx = np.where(data_day_mask)[0]
        first_data_idx = int(valid_idx[0])
        last_data_idx = int(valid_idx[-1])
    else:
        first_data_idx = -1
        last_data_idx = -1

    # 综合判定
    flags = []
    severity = 'ok'
    field_name = npy.stem

    if dtype == 'float64' and stats['n_zero'] == n_finite and n_finite > 0:
        flags.append('all_zero')
        severity = 'critical'
    if dtype == 'float64' and n_finite == 0:
        flags.append('all_nan')
        severity = 'warn'  # 可能是源数据真没
    if n_posinf + n_neginf > 0:
        flags.append(f'inf:{n_posinf+n_neginf}')
        severity = 'critical' if severity == 'ok' else severity
    if is_nonneg_field(field_name) and stats['n_neg'] > 0:
        flags.append(f'neg_in_nonneg:{stats["n_neg"]}')
        severity = 'critical' if severity == 'ok' else severity

    return {
        'shape': [int(x) for x in shape],
        'ndim': int(ndim),
        'dtype': dtype,
        'cells_scanned': int(total_cells),
        't_end': int(t_end),
        't_full': int(T_full),
        'n_nan': int(n_nan),
        'n_finite': int(n_finite),
        'n_posinf': int(n_posinf),
        'n_neginf': int(n_neginf),
        'all_nan_days': all_nan_days,
        'no_nan_days': no_nan_days,
        'first_data_idx': first_data_idx,
        'last_data_idx': last_data_idx,
        'stats': stats,
        'flags': flags,
        'severity': severity,
    }, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', required=True, help='cc root, e.g. /datasvc/data/cc_all')
    p.add_argument('--out', required=True, help='输出 JSON 路径')
    p.add_argument('--cutoff', type=int, default=20241231, help='只扫 dates <= cutoff')
    p.add_argument('--trim-last', type=int, default=1,
                   help='砍掉尾部 N 天 (默认 1, 排除 enddate 那天 NaN 占位)')
    p.add_argument('--filter', default='*', help='文件名 glob 过滤 (e.g. AShareMoneyFlow*)')
    p.add_argument('--limit', type=int, default=0, help='只扫前 N 文件 (debug)')
    p.add_argument('--progress-every', type=int, default=200)
    args = p.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        raise SystemExit(f"root 不存在: {root}")

    dates = np.fromfile(root / '__universe' / 'Dates.npy', dtype='int64')
    insts = np.fromfile(root / '__universe' / 'Instruments.npy', dtype='U32')
    N = len(insts)
    cutoff_idx = int((dates <= args.cutoff).sum())

    print(f"[i] root={root}", flush=True)
    print(f"[i] cutoff={args.cutoff} idx={cutoff_idx}  trim_last={args.trim_last}", flush=True)
    print(f"[i] N={N} dates_total={len(dates)}", flush=True)

    # walk
    eligible = []
    for entry in sorted(os.listdir(root)):
        if entry.startswith('.'):
            continue
        if entry in SKIP_DIRS:
            continue
        sub = root / entry
        if not sub.is_dir():
            continue
        for f in sorted(sub.rglob('*.npy')):
            try:
                if f.is_file() and fnmatch.fnmatch(f.name, args.filter):
                    eligible.append(f)
            except OSError:
                continue
    print(f"[i] 待扫 {len(eligible)} 文件", flush=True)
    if args.limit:
        eligible = eligible[:args.limit]
        print(f"[i] limit -> {args.limit}", flush=True)

    results = {}
    skipped = []
    severity_counts = {'ok': 0, 'warn': 0, 'critical': 0}
    t0 = time.time()
    for i, npy in enumerate(eligible):
        rel = str(npy.relative_to(root))
        rep, err = validate_file(npy, N, cutoff_idx, args.trim_last)
        if rep is None:
            skipped.append([rel, err])
            continue
        # idx → YYYYMMDD
        if rep['first_data_idx'] >= 0:
            rep['first_data_date'] = int(dates[rep['first_data_idx']])
            rep['last_data_date'] = int(dates[rep['last_data_idx']])
        else:
            rep['first_data_date'] = None
            rep['last_data_date'] = None
        results[rel] = rep
        severity_counts[rep['severity']] += 1
        if (i + 1) % args.progress_every == 0:
            el = time.time() - t0
            rate = (i + 1) / max(el, 1e-6)
            print(f"[{i+1}/{len(eligible)}] elapsed={el:.0f}s rate={rate:.1f}/s", flush=True)

    elapsed = time.time() - t0

    # 同目录 cohort freshness 比对: 按一级目录分组, 末日远早于同组中位数的 flag stale
    from collections import defaultdict
    by_dir = defaultdict(list)
    for rel, rep in results.items():
        if rep.get('last_data_date'):
            by_dir[rel.split('/')[0]].append((rel, rep))
    stale_findings = {}
    STALE_GAP_DAYS = 30  # 同组末日相差 30 个交易日以上算 stale
    for d, items in by_dir.items():
        if len(items) < 3:
            continue  # 太少不做 cohort 比对
        last_dates = sorted(rep['last_data_idx'] for _, rep in items)
        median_last_idx = last_dates[len(last_dates) // 2]
        for rel, rep in items:
            gap = median_last_idx - rep['last_data_idx']
            if gap >= STALE_GAP_DAYS:
                rep.setdefault('flags', []).append(f'stale:{gap}d_behind_cohort')
                # stale 一定升级为 critical (这是真问题, 跟 build 漏一样级别)
                if rep['severity'] == 'ok':
                    rep['severity'] = 'critical'
                stale_findings[rel] = {
                    'last_data_date': rep['last_data_date'],
                    'cohort_median_date': int(dates[median_last_idx]),
                    'gap_days': int(gap),
                    'dir': d,
                }
    # 重新统计 severity (因为 stale 可能升级了)
    severity_counts = {'ok': 0, 'warn': 0, 'critical': 0}
    for rep in results.values():
        severity_counts[rep['severity']] += 1

    out = {
        'root': str(root),
        'cutoff': args.cutoff,
        'cutoff_idx': cutoff_idx,
        'trim_last': args.trim_last,
        'N': int(N),
        'filter': args.filter,
        'n_scanned': len(results),
        'n_skipped': len(skipped),
        'severity_counts': severity_counts,
        'elapsed_sec': round(elapsed, 1),
        'stale_findings': stale_findings,
        'results': results,
        'skipped': skipped,
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n[i] 完成: scan={len(results)} skip={len(skipped)} 耗时={elapsed:.0f}s", flush=True)
    print(f"[i] 严重度: ok={severity_counts['ok']} "
          f"warn={severity_counts['warn']} critical={severity_counts['critical']}", flush=True)
    if stale_findings:
        print(f"[i] freshness 失守 ({len(stale_findings)}): 同目录 cohort 末日落后 ≥{STALE_GAP_DAYS}d", flush=True)
    print(f"[i] 报告: {args.out}", flush=True)

    # 打印 stale findings (新加的最重要类)
    if stale_findings:
        print(f"\n=== STALE / freshness 失守 ({len(stale_findings)}) ===", flush=True)
        for rel, info in list(stale_findings.items())[:15]:
            print(f"  {rel}: last={info['last_data_date']} "
                  f"(cohort median={info['cohort_median_date']}, gap={info['gap_days']}d)", flush=True)
        if len(stale_findings) > 15:
            print(f"  ... 共 {len(stale_findings)} 个", flush=True)

    # 打印 critical 文件清单 (排除 stale-only, 那些上面已经打)
    critical = [(k, v) for k, v in results.items()
                if v['severity'] == 'critical' and k not in stale_findings]
    if critical:
        print(f"\n=== CRITICAL 非 stale ({len(critical)}) ===", flush=True)
        for k, v in critical[:20]:
            print(f"  {k}: {v['flags']} (max={v['stats']['max']}, n_neg={v['stats']['n_neg']})", flush=True)
        if len(critical) > 20:
            print(f"  ... 共 {len(critical)} 个, 完整列表见 JSON", flush=True)

    sys.exit(1 if severity_counts['critical'] > 0 else 0)


if __name__ == '__main__':
    main()
