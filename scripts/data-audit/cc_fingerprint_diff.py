#!/usr/bin/env python3
"""
cc_all 指纹比对器
================

比对两份 cc_fingerprint.py 产出的 .npz, 报告"大致一致" / "不一致" 的 .npy 列表。

判定: 对每个共名 .npy, 取 (T 共同长度) 范围内的 sum 和 nan_count 各自做 np.allclose:
  - sum:  np.allclose(rtol=1e-5, atol=0, equal_nan=True)
  - nan:  exact equality (NaN 数量应当完全相等)
形状 / dtype 不一致直接归"不一致 (结构差)"。

用法:
    python cc_fingerprint_diff.py fp_160.npz fp_147.npz [--out report.json]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np


def load_fingerprint(path: Path):
    """Load npz into dict keyed by file relpath."""
    npz = np.load(path, allow_pickle=False)
    files = {}
    for k in npz.files:
        rel, _, attr = k.rpartition('|')
        files.setdefault(rel, {})[attr] = npz[k]
    return files


def compare_one(fp_a: dict, fp_b: dict, rtol: float, trim_last: int):
    """
    Compare one file's fingerprints.

    trim_last:
        从两边各 T 共同前缀的尾部再砍掉 N 行, 用来排除"build_cc enddate 那天
        永远是 NaN 占位"造成的固有 nan_diff。默认 1 (砍最后一天)。

    Returns (status, details_dict).
    status: 'match' | 'shape_diff' | 'dtype_diff' | 'sum_diff' | 'nan_diff'
    """
    if not np.array_equal(fp_a['dtype'], fp_b['dtype']):
        return 'dtype_diff', {
            'a_dtype': int(fp_a['dtype'][0]),
            'b_dtype': int(fp_b['dtype'][0]),
        }

    a_shape = fp_a['shape']
    b_shape = fp_b['shape']
    # 维度不同 (1D vs 2D vs 3D)
    if len(a_shape) != len(b_shape):
        return 'shape_diff', {
            'a_shape': a_shape.tolist(),
            'b_shape': b_shape.tolist(),
            'reason': f'ndim differs: {len(a_shape)} vs {len(b_shape)}',
        }
    # 各轴 (除 T) 必须一致
    if len(a_shape) >= 2 and not np.array_equal(a_shape[1:], b_shape[1:]):
        return 'shape_diff', {
            'a_shape': a_shape.tolist(),
            'b_shape': b_shape.tolist(),
            'reason': 'non-T axis differs',
        }

    # T 可能各自不同长度 (各地 cutoff 不同), 取共同前缀
    sum_a = fp_a['sum']
    sum_b = fp_b['sum']
    nan_a = fp_a['nan']
    nan_b = fp_b['nan']
    T_common = min(len(sum_a), len(sum_b))
    if T_common == 0:
        return 'shape_diff', {
            'a_shape': a_shape.tolist(),
            'b_shape': b_shape.tolist(),
            'reason': 'no T overlap',
        }

    sum_a, sum_b = sum_a[:T_common], sum_b[:T_common]
    nan_a, nan_b = nan_a[:T_common], nan_b[:T_common]

    # 砍掉尾部 trim_last 天 (排除 build_cc enddate 那天必然 NaN 占位的固有差异)
    if trim_last > 0 and T_common > trim_last:
        T_compare = T_common - trim_last
        sum_a, sum_b = sum_a[:T_compare], sum_b[:T_compare]
        nan_a, nan_b = nan_a[:T_compare], nan_b[:T_compare]
    else:
        T_compare = T_common

    if not np.array_equal(nan_a, nan_b):
        diff_mask = nan_a != nan_b
        bad_idx = np.where(diff_mask)[0]
        return 'nan_diff', {
            'T_common': int(T_common),
            'T_compare': int(T_compare),
            'trim_last': int(trim_last),
            'n_bad_days': int(bad_idx.size),
            'first_bad_day_idx': int(bad_idx[0]),
            'first_bad_day_nan_a': int(nan_a[bad_idx[0]]),
            'first_bad_day_nan_b': int(nan_b[bad_idx[0]]),
        }

    if not np.allclose(sum_a, sum_b, rtol=rtol, atol=0.0, equal_nan=True):
        diff_mask = ~np.isclose(sum_a, sum_b, rtol=rtol, atol=0.0, equal_nan=True)
        bad_idx = np.where(diff_mask)[0]
        # 最大相对误差
        with np.errstate(divide='ignore', invalid='ignore'):
            rel_err = np.abs(sum_a - sum_b) / np.maximum(np.abs(sum_a), np.abs(sum_b))
            rel_err = np.where(np.isnan(rel_err), 0, rel_err)
        return 'sum_diff', {
            'T_common': int(T_common),
            'T_compare': int(T_compare),
            'trim_last': int(trim_last),
            'n_bad_days': int(bad_idx.size),
            'first_bad_day_idx': int(bad_idx[0]),
            'max_rel_err': float(np.nanmax(rel_err)),
            'first_bad_day_sum_a': float(sum_a[bad_idx[0]]),
            'first_bad_day_sum_b': float(sum_b[bad_idx[0]]),
        }

    return 'match', {'T_common': int(T_common), 'T_compare': int(T_compare)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('fp_a', help='指纹 .npz (基准, e.g. fp_160.npz)')
    p.add_argument('fp_b', help='指纹 .npz (对照, e.g. fp_147.npz)')
    p.add_argument('--rtol', type=float, default=1e-5)
    p.add_argument('--trim-last', type=int, default=1,
                   help='砍掉 T 共同前缀尾部 N 天 (默认 1, 排除 build_cc enddate 那天 NaN 占位的固有差异; 设 0 关闭)')
    p.add_argument('--out', default='cc_diff_report.json', help='详细报告 JSON 输出路径')
    args = p.parse_args()

    fp_a_path = Path(args.fp_a)
    fp_b_path = Path(args.fp_b)
    print(f"[i] A = {fp_a_path}", flush=True)
    print(f"[i] B = {fp_b_path}", flush=True)
    print(f"[i] rtol = {args.rtol}", flush=True)
    print(f"[i] trim_last = {args.trim_last} (排除 build_cc enddate 那天的 NaN 占位)", flush=True)

    files_a = load_fingerprint(fp_a_path)
    files_b = load_fingerprint(fp_b_path)
    keys_a = set(files_a)
    keys_b = set(files_b)
    print(f"[i] A: {len(keys_a)} files, B: {len(keys_b)} files", flush=True)

    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = sorted(keys_a & keys_b)
    print(f"[i] only_A={len(only_a)} only_B={len(only_b)} common={len(common)}", flush=True)

    results = {
        'match': [],
        'dtype_diff': [],
        'shape_diff': [],
        'sum_diff': [],
        'nan_diff': [],
    }
    diff_details = {}

    for rel in common:
        status, det = compare_one(files_a[rel], files_b[rel], args.rtol, args.trim_last)
        results[status].append(rel)
        if status != 'match':
            diff_details[rel] = {'status': status, **det}

    print(f"\n[结果]", flush=True)
    print(f"  match:      {len(results['match'])}", flush=True)
    print(f"  sum_diff:   {len(results['sum_diff'])}", flush=True)
    print(f"  nan_diff:   {len(results['nan_diff'])}", flush=True)
    print(f"  shape_diff: {len(results['shape_diff'])}", flush=True)
    print(f"  dtype_diff: {len(results['dtype_diff'])}", flush=True)

    # 报告前 10 个 sum_diff
    if results['sum_diff']:
        print(f"\n[sum_diff 样例 (max 10)]", flush=True)
        for rel in results['sum_diff'][:10]:
            d = diff_details[rel]
            print(f"  {rel}: max_rel_err={d['max_rel_err']:.2e} bad_days={d['n_bad_days']}/{d['T_common']}",
                  flush=True)

    if results['nan_diff']:
        print(f"\n[nan_diff 样例 (max 10)]", flush=True)
        for rel in results['nan_diff'][:10]:
            d = diff_details[rel]
            print(f"  {rel}: bad_days={d['n_bad_days']}/{d['T_common']}", flush=True)

    if only_a:
        print(f"\n[A 独有 (max 20)]", flush=True)
        for rel in only_a[:20]:
            print(f"  {rel}", flush=True)
    if only_b:
        print(f"\n[B 独有 (max 20)]", flush=True)
        for rel in only_b[:20]:
            print(f"  {rel}", flush=True)

    # JSON dump
    out = Path(args.out)
    report = {
        'fp_a': str(fp_a_path),
        'fp_b': str(fp_b_path),
        'rtol': args.rtol,
        'summary': {k: len(v) for k, v in results.items()},
        'only_a_count': len(only_a),
        'only_b_count': len(only_b),
        'only_a': only_a,
        'only_b': only_b,
        'lists': results,
        'diff_details': diff_details,
    }
    with open(out, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n[i] full report -> {out}", flush=True)


if __name__ == '__main__':
    main()
