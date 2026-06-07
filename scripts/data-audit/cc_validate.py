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
SKIP_DIRS = {
    'cn_equity', 'cn_equity_feature', 'cn_equity_feature_5min', 'realtime',
    'delta', 'Interval5m',  # 3D, 用专门工具
    '__universe',
    'ALL', 'ALL_GIM', 'ALL_TRD', 'FULL',
    'HS300', 'ZZ500', 'ZZ1000', 'ipo',
    'TOP1000', 'TOP1500', 'TOP2000', 'TOP2600', 'TOP3000', 'TOP3300', 'TOP4000',
}

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


def infer_2d(file_size: int, n_inst: int):
    if file_size == 0:
        return None, None, "empty file"
    row_bytes = n_inst * 8
    if file_size % row_bytes == 0:
        return file_size // row_bytes, 'float64', None
    if file_size % n_inst == 0:
        return file_size // n_inst, 'int8', None
    return None, None, f"unfit float64/int8: size={file_size} N={n_inst}"


def validate_file(npy: Path, n_inst: int, cutoff_idx: int, trim_last: int):
    """
    扫一个 .npy, 返回质量摘要 dict 或 skip 原因.
    """
    try:
        size = npy.stat().st_size
    except OSError as e:
        return None, f"stat fail: {e}"

    T_full, dtype, err = infer_2d(size, n_inst)
    if T_full is None:
        return None, err

    t_end = min(cutoff_idx, T_full)
    t_end = max(0, t_end - trim_last)
    if t_end == 0:
        return None, "no rows in cutoff after trim"

    arr = np.memmap(npy, dtype=dtype, mode='r', shape=(T_full, n_inst))
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

    # 每天 NaN 数, 用来 detect 全 NaN 天 / 全 finite 天
    if dtype == 'float64':
        nan_per_day = np.isnan(slc).sum(axis=1)
        all_nan_days = int((nan_per_day == n_inst).sum())
        no_nan_days = int((nan_per_day == 0).sum())
    else:
        all_nan_days = no_nan_days = 0

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
        'shape': [int(T_full), int(n_inst)],
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
        results[rel] = rep
        severity_counts[rep['severity']] += 1
        if (i + 1) % args.progress_every == 0:
            el = time.time() - t0
            rate = (i + 1) / max(el, 1e-6)
            print(f"[{i+1}/{len(eligible)}] elapsed={el:.0f}s rate={rate:.1f}/s", flush=True)

    elapsed = time.time() - t0
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
        'results': results,
        'skipped': skipped,
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n[i] 完成: scan={len(results)} skip={len(skipped)} 耗时={elapsed:.0f}s", flush=True)
    print(f"[i] 严重度: ok={severity_counts['ok']} "
          f"warn={severity_counts['warn']} critical={severity_counts['critical']}", flush=True)
    print(f"[i] 报告: {args.out}", flush=True)

    # 打印 critical 文件清单
    critical = [(k, v) for k, v in results.items() if v['severity'] == 'critical']
    if critical:
        print(f"\n=== CRITICAL ({len(critical)}) ===", flush=True)
        for k, v in critical[:20]:
            print(f"  {k}: {v['flags']} (max={v['stats']['max']}, n_neg={v['stats']['n_neg']})", flush=True)
        if len(critical) > 20:
            print(f"  ... 共 {len(critical)} 个, 完整列表见 JSON", flush=True)

    sys.exit(1 if severity_counts['critical'] > 0 else 0)


if __name__ == '__main__':
    main()
