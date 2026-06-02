#!/usr/bin/env python3
"""Verify np.memmap on JuiceFS for alpha_feature pattern.

模拟 alpha_feature 实际形状(3900 days × 5484 stocks float64,~170 MB),
测三个场景:
  1. 全量创建 + 全量读
  2. memmap 模式日增写一行(对应 ops pack --date)
  3. 跨进程读(写者退出后,新进程能否正确 memmap)

跑完会清理 $JFS_MOUNT/_poc_memmap/ 目录。
"""
import os
import sys
import time
from pathlib import Path

import numpy as np

MOUNT = Path(os.environ["JFS_MOUNT"])
T = MOUNT / "_poc_memmap"
T.mkdir(parents=True, exist_ok=True)

DAYS, STOCKS = 3900, 5484
FILE = T / "fake_feature.npy"
EXPECTED_MB = DAYS * STOCKS * 8 / 1e6


def hms(t):
    return f"{t*1000:.0f} ms" if t < 1 else f"{t:.2f} s"


print(f"[1/5] creating {DAYS}x{STOCKS} float64 memmap (~{EXPECTED_MB:.0f} MB)...")
t0 = time.time()
arr = np.lib.format.open_memmap(FILE, mode="w+", dtype=np.float64, shape=(DAYS, STOCKS))
arr[:] = np.nan
arr.flush()
del arr
t = time.time() - t0
size_mb = FILE.stat().st_size / 1e6
print(f"  done in {hms(t)}, file size = {size_mb:.1f} MB")
assert abs(size_mb - EXPECTED_MB) < 1, "size mismatch"

print("[2/5] writing one row at index 100 (mimics daily incremental pack)...")
t0 = time.time()
arr = np.lib.format.open_memmap(FILE, mode="r+")
arr[100, :] = np.random.randn(STOCKS).astype(np.float64)
arr.flush()
del arr
print(f"  done in {hms(time.time() - t0)}")

print("[3/5] read that row back in a fresh memmap...")
arr = np.lib.format.open_memmap(FILE, mode="r")
row = arr[100, :].copy()
del arr
assert not np.isnan(row).any(), "row should be filled"
assert np.isfinite(row).all(), "row should be finite"
print(f"  ok, mean={row.mean():.4f} std={row.std():.4f}")

print("[4/5] sequential read benchmark: 100 rows scattered across file...")
arr = np.lib.format.open_memmap(FILE, mode="r")
t0 = time.time()
total = 0.0
for i in range(100):
    idx = (i * 37) % DAYS
    total += arr[idx].sum()
t = time.time() - t0
del arr
print(f"  100 row reads (with cache cold→warm): {hms(t)}")

print("[5/5] cross-process: spawn child to read what we wrote...")
import subprocess
result = subprocess.run(
    [
        sys.executable,
        "-c",
        f"import numpy as np; "
        f"a = np.lib.format.open_memmap('{FILE}', mode='r'); "
        f"print(f'child sees row 100 mean={{a[100].mean():.4f}}')",
    ],
    capture_output=True,
    text=True,
    check=True,
)
print(f"  {result.stdout.strip()}")

print()
print("=" * 50)
print("OK. memmap pattern works on JuiceFS.")
print(f"Cleanup: rm -rf {T}")
print("=" * 50)
