# Pack

Aggregates per-date `.npy` dumps into per-factor matrices for downstream consumers.

**Source**: `alpha_dump/AlphaXxx/{year}/{month}/{YYYYMMDD}{v1|v2}.npy` (each shape `(H,)`)
**Target**: `alpha_feature/AlphaXxx.{v1|v2}.npy` — memmap, shape `(PACK_L, H)` = `(3900, 5484)`, float64

**Offset rule**: Per-date file at date `D` is placed at row `date_to_idx[D] - 1`. Gsim stores the *next-day* signal computed at close of day `D` — when read back as a feature on day `D-1`'s row, it serves as the previous-day prediction.

> **Known critical bug — offset is delay-dependent, current pack hardcodes delay=1 semantics.**
>
> The `di → di-1` mapping is only correct for **delay=1** factors (signal computed at close of `D`, used to trade `D+1` — read back at row of `D` so downstream sees it as "yesterday's prediction"). For **delay=0** factors, gsim writes the dump at the same `di` it acts on, so the correct mapping is `di → di` (no shift).
>
> Current `pack.py` applies the `-1` shift unconditionally, so **every delay=0 factor's feature is misaligned by one day**. Downstream models reading the feature memmap see yesterday's signal labeled as today's, leaking 1d of look-ahead in one direction or losing 1d of signal in the other depending on how the consumer interprets the row.
>
> **Fix sketch (deferred)**: pack must read each factor's `meta.json` for `delay`, then choose the offset (`0` for delay=0, `-1` for delay=1). `verify_sample` and the incremental path (`pack_one_incremental`) need the same branching. Re-pack all delay=0 factors once the fix lands. Until fixed, treat any model trained on delay=0 features as suspect.

## Shape Policy

- `PACK_L = 3900` hardcoded — covers historical universe up to 20251231, matches the check pipeline's backtest end date
- `H` derived from `__universe/Instruments.npy` at write time (currently 5484, stable for 1-2 years)
- Rows with `di >= PACK_L` are silently skipped (future dates from daily incremental data don't belong in the historical pack)
- Per-date arrays longer than `H` raise `ValueError`; shorter are placed at `[di, :h0]` with NaN right-padding (future-proofing for instrument growth)
- **Daily incremental** (rows beyond 20251231) is a separate, not-yet-built path — pre-allocated buffer / generational files / zarr were considered

## Access Paths

1. **Batch CLI** (`ops pack`): scans `alpha_dump/`, skips already-packed unless `--force`, `ProcessPoolExecutor` parallel (default 10 workers), wraps each factor in `factor_lock`
2. **Incremental** (`pack_one_incremental`): if target memmap doesn't exist → falls back to full `pack_one`; otherwise opens `mode='r+'` and overwrites only requested date rows. Currently only callable directly or via future `ops pack --date`

## Atomic Write

Full rewrites go through `.{name}.{v}.npy.tmp` + `os.replace` so a crashed pack never leaves a partial file in the target path.

## Verification

After each `pack_one`, `verify_sample` picks up to `VERIFY_SAMPLES = 5` random per-date source files, reloads each, compares against the target memmap row within `ATOL = 1e-6` (NaN-aware). Any mismatch raises and marks the factor failed in the batch summary. `--no-verify` skips this.
