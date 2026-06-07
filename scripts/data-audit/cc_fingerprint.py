#!/usr/bin/env python3
"""
cc_all 跨机数据指纹生成器
========================

用途: 检测两台机器上 cc_all 内的 .npy 数据是否"大致一致" (T <= 20241231 范围)。
对每个 .npy 算逐日聚合指纹 (sum + nan_count 沿 N 轴归约), 不传原始数据。

设计原则:
- 只看数据本身, 不看 mtime / size / .meta
- 浮点累积容差: 比对时用 np.allclose(rtol=1e-5)
- L2 / 5min / delta / universe mask / 3D 都跳, v1 只比 2D float64 / int8 主体
- 自包含, 仅依赖 numpy

用法 (各地都跑一次):
    python cc_fingerprint.py --root /datasvc/data/cc_all --out fp_<site>.npz
    # 例:
    python cc_fingerprint.py --out fp_160.npz
    python cc_fingerprint.py --out fp_147.npz   # 在 147 上跑

输出:
    fp_<site>.npz          每文件的 sum/nan_count/shape/dtype, ~10-50MB
    fp_<site>.skip.json    跳过的文件 + 原因 (debug 用)
    fp_<site>.summary.json 总览 (跑了多少, 多少 skip, 总耗时)

后续 比对脚本: cc_fingerprint_diff.py
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

CUTOFF_YYYYMMDD = 20241231

# 跳目录 (顶层, 用户确认: L2 系列 + universe mask + 3D + delta + meta)
SKIP_DIRS = {
    # L2 (147 已知缺 25 年前数据, 不可比)
    'cn_equity', 'cn_equity_feature', 'cn_equity_feature_5min', 'realtime',
    # 切片 (奇怪 shape, 单独处理)
    'delta',
    # 3D (v1 不处理)
    'Interval5m',
    # universe / 元数据 (跟数据无关, user 不要比)
    '__universe',
    'ALL', 'ALL_GIM', 'ALL_TRD', 'FULL',
    'HS300', 'ZZ500', 'ZZ1000', 'ipo',
    'TOP1000', 'TOP1500', 'TOP2000', 'TOP2600', 'TOP3000', 'TOP3300', 'TOP4000',
}


def load_universe(root: Path):
    """Read Dates.npy + Instruments.npy."""
    dates_file = root / '__universe' / 'Dates.npy'
    insts_file = root / '__universe' / 'Instruments.npy'
    if not dates_file.exists():
        raise SystemExit(f"missing {dates_file}")
    if not insts_file.exists():
        raise SystemExit(f"missing {insts_file}")
    dates = np.fromfile(dates_file, dtype='int64')
    insts = np.fromfile(insts_file, dtype='U32')
    return dates, insts


def infer_2d(file_size: int, n_inst: int):
    """Try to infer (T, N) shape + dtype. Returns (T, dtype_str) or (None, reason_str)."""
    if file_size == 0:
        return None, "empty"
    # float64 优先
    row_bytes = n_inst * 8
    if file_size % row_bytes == 0:
        return file_size // row_bytes, 'float64'
    # int8 备选
    if file_size % n_inst == 0:
        return file_size // n_inst, 'int8'
    return None, f"unfit float64/int8: size={file_size} N={n_inst}"


def fingerprint_file(npy: Path, n_inst: int, cutoff_idx: int):
    """
    Compute fingerprint for one .npy file.
    Returns (fp_dict | None, skip_reason | None).
    fp_dict keys: 'sum', 'nan', 'shape', 'dtype'
    """
    try:
        file_size = npy.stat().st_size
    except OSError as e:
        return None, f"stat fail: {e}"

    T_full, info = infer_2d(file_size, n_inst)
    if T_full is None:
        return None, info
    dtype = info
    shape = (T_full, n_inst)

    if T_full < cutoff_idx:
        # File shorter than cutoff - use what's there
        t_end = T_full
    else:
        t_end = cutoff_idx
    if t_end == 0:
        return None, "no rows in cutoff"

    try:
        arr = np.memmap(npy, dtype=dtype, mode='r', shape=shape)
        slc = np.asarray(arr[:t_end])  # materialize only the slice we need
    except Exception as e:
        return None, f"memmap/read fail: {e}"

    if dtype == 'float64':
        fp_sum = np.nansum(slc, axis=1).astype('float64')
        fp_nan = np.isnan(slc).sum(axis=1).astype('int32')
    else:  # int8
        fp_sum = slc.sum(axis=1, dtype='int64').astype('float64')
        fp_nan = np.zeros(t_end, dtype='int32')

    return {
        'sum':   fp_sum,
        'nan':   fp_nan,
        'shape': np.asarray(shape, dtype='int64'),
        'dtype': np.asarray([0 if dtype == 'float64' else 1], dtype='int8'),
    }, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', default='/datasvc/data/cc_all', help='cc_all root')
    p.add_argument('--out', required=True, help='output fingerprint .npz path')
    p.add_argument('--limit', type=int, default=0, help='process only first N files (debug)')
    p.add_argument('--progress-every', type=int, default=100, help='log every N files')
    args = p.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"[i] root={root}", flush=True)
    print(f"[i] out={out}", flush=True)
    print(f"[i] cutoff={CUTOFF_YYYYMMDD}", flush=True)

    dates, insts = load_universe(root)
    N = len(insts)
    cutoff_mask = dates <= CUTOFF_YYYYMMDD
    if not cutoff_mask.any():
        raise SystemExit(f"no dates <= {CUTOFF_YYYYMMDD}")
    cutoff_idx = int(cutoff_mask.sum())
    print(f"[i] N={N} dates_total={len(dates)} dates<=cutoff={cutoff_idx}", flush=True)
    print(f"[i] first_date={int(dates[0])} last_in_cutoff={int(dates[cutoff_idx-1])}", flush=True)

    # Walk: top-level dirs only, recurse into each (some have nested subdirs)
    eligible = []
    skipped_dirs = []
    for entry in sorted(os.listdir(root)):
        if entry.startswith('.'):
            continue
        if entry in SKIP_DIRS:
            skipped_dirs.append(entry)
            continue
        subdir = root / entry
        if not subdir.is_dir():
            continue
        for npy in sorted(subdir.rglob('*.npy')):
            try:
                if not npy.is_file():
                    continue
            except OSError:
                continue
            eligible.append(npy)
    print(f"[i] skipped dirs: {skipped_dirs}", flush=True)
    print(f"[i] {len(eligible)} candidate .npy files under non-skipped dirs", flush=True)
    if args.limit:
        eligible = eligible[:args.limit]
        print(f"[i] limited to first {args.limit}", flush=True)

    fingerprints = {}
    skipped = []
    t0 = time.time()
    bytes_read = 0
    for i, npy in enumerate(eligible):
        rel = str(npy.relative_to(root))
        fp, err = fingerprint_file(npy, N, cutoff_idx)
        if fp is None:
            skipped.append([rel, err])
            continue
        # Pack
        for k, v in fp.items():
            fingerprints[f"{rel}|{k}"] = v
        bytes_read += int(fp['shape'][0]) * N * (8 if fp['dtype'][0] == 0 else 1)

        if (i + 1) % args.progress_every == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 1e-6)
            eta = (len(eligible) - i - 1) / max(rate, 1e-6)
            print(f"[{i+1}/{len(eligible)}] "
                  f"elapsed={elapsed:.0f}s rate={rate:.1f}/s eta={eta:.0f}s "
                  f"read={bytes_read/1e9:.1f}GB", flush=True)

    elapsed = time.time() - t0
    n_fp = len([k for k in fingerprints if k.endswith('|sum')])
    print(f"[i] done in {elapsed:.0f}s: {n_fp} files fingerprinted, {len(skipped)} skipped", flush=True)

    # Save fingerprint
    print(f"[i] saving to {out} ...", flush=True)
    np.savez_compressed(out, **fingerprints)
    sz_mb = out.stat().st_size / 1024 / 1024
    print(f"[i] fingerprint: {sz_mb:.1f} MB", flush=True)

    # Save skip log
    skip_log = out.with_suffix('.skip.json')
    with open(skip_log, 'w') as f:
        json.dump(skipped, f, indent=2, ensure_ascii=False)
    print(f"[i] skip log: {skip_log}", flush=True)

    # Save summary
    summary = {
        'root': str(root),
        'cutoff': CUTOFF_YYYYMMDD,
        'cutoff_idx': cutoff_idx,
        'N': int(N),
        'first_date': int(dates[0]),
        'last_in_cutoff': int(dates[cutoff_idx - 1]),
        'n_fingerprinted': n_fp,
        'n_skipped': len(skipped),
        'skipped_dirs': skipped_dirs,
        'elapsed_sec': round(elapsed, 1),
        'bytes_read_gb': round(bytes_read / 1e9, 2),
    }
    sum_log = out.with_suffix('.summary.json')
    with open(sum_log, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[i] summary: {sum_log}", flush=True)


if __name__ == '__main__':
    main()
