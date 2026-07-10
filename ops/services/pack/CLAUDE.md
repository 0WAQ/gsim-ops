# Pack

Aggregates per-date `.npy` dumps into per-factor matrices for downstream consumers.

**Source**: `alpha_dump/AlphaXxx/{year}/{month}/{YYYYMMDD}{v1|v2}.npy` (each shape `(H,)`)
**Target**: `alpha_feature/AlphaXxx.{v1|v2}.npy` — memmap, shape `(PACK_L, H)` = `(3900, 5484)`, float64

**Offset rule (delay-dependent)**: per-date file at date `D` is placed at row `date_to_idx[D] + offset`, where `offset` is read from each factor's `meta.json` `delay` field:
- `delay=0` → `offset = 0` (gsim writes dump at the same di it acts on)
- `delay=1` → `offset = -1` (signal computed at close of D is used to trade D+1, so it sits on row D-1 as "yesterday's prediction")

Missing/unreadable `meta.json` defaults to `delay=1` with a warning, preserving legacy behavior. `pack_one`, `pack_one_incremental`, and `verify_sample` all branch on the same `delay`. `run_pack` looks up delay once per candidate before dispatching to workers.

## Shape Policy

- `PACK_L = 3900` hardcoded — covers historical universe up to 20251231, matches the check pipeline's backtest end date
- `H` derived from `__universe/Instruments.npy` at write time (currently 5484, stable for 1-2 years)
- Rows with `di >= PACK_L` are silently skipped (future dates from daily incremental data don't belong in the historical pack)
- Per-date arrays longer than `H` raise `ValueError`; shorter are placed at `[di, :h0]` with NaN right-padding (future-proofing for instrument growth)
- **Daily incremental** (rows beyond 20251231) is a separate, not-yet-built path — pre-allocated buffer / generational files / zarr were considered

## Access Paths

1. **Batch CLI** (`ops pack`): scans `alpha_dump/`, skips already-packed unless `--force`, `ProcessPoolExecutor` parallel (default 10 workers), wraps each factor in `factor_lock`。`-u`/`--status` 过滤走 `repo.find(author, status, include_submitted=True)` 单条三表 JOIN(2026-07-09 阶段 3,退役 store.list + info.list 内存交集;缺省全状态,显式 `--status` 按其精确过滤)
2. **Incremental** (`pack_one_incremental`): if target memmap doesn't exist → falls back to full `pack_one`; otherwise opens `mode='r+'` and overwrites only requested date rows. Currently only callable directly or via future `ops pack --date`

## Atomic Write

Full rewrites go through `.{name}.{v}.npy.tmp` + `os.replace` so a crashed pack never leaves a partial file in the target path.

## Verification

After each `pack_one`, `verify_sample` picks up to `VERIFY_SAMPLES = 5` random per-date source files, reloads each, compares against the target memmap row within `ATOL = 1e-6` (NaN-aware). Any mismatch raises and marks the factor failed in the batch summary. `--no-verify` skips this.
