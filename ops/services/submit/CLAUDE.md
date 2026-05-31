# Submit & Factor Lifecycle

## State Machine

`SUBMITTED → CHECKING → ACTIVE | REJECTED`

(DELETED 是 soft-delete 标记,由 `ops rm` 进入;DECAYING/RETIRED 暂未实现)

**Flow**:
```
dropbox/{user}/{date}/AlphaXxx/      (QR-owned, read-only source)
    │  ops submit  → parse_factor() → write meta.json + state=SUBMITTED
    ▼
staging/AlphaXxx/  +  meta.json      (flat layout, ops-owned)
    │  ops check   → reconcile → 7-stage pipeline run
    ├── pass ──► alpha_src/AlphaXxx/                  state=ACTIVE
    │                │  ops recheck   (原代码不变,重跑 check;--purge 顺带清 dump/feature)
    │                │  ops resubmit  (从 dropbox 提交新代码,version += 1)
    │                │  → 搬回 staging/ + state→SUBMITTED
    │                ▼
    │            [回到 staging,等待下一次 ops check]
    ├── fail (validate/long_backtest)
    │            → staging/ (kept in-place)            state→SUBMITTED  (retry via ops check --retry)
    └── fail (checkbias/checkpoint/compliance/correlation/archive)
                 → recycle/{user}/{stage}/AlphaXxx/    state=REJECTED
                     │  ops recheck -s rejected         → staging/ + state→SUBMITTED
                     ▼
                 [same flow as new factor]
```

**A factor record is never deleted from state.json** — it transitions through statuses but stays. REJECTED records keep `last_fail_stage` / `last_fail_reason` for auditing. The only thing reconcile drops are pure orphans (status SUBMITTED/CHECKING with no files anywhere on disk).

## Two Persistence Layers

- **`meta.json`** inside each factor directory — the factor's *identity card*. Fields: name, author, birthday, universe, category, delay, backdays, dump_alpha, has_intraday_curve, operations, declared_data_modules, datasources (fields+tables), code_lines, frequency, submitted_by, submitted_at. Travels with the factor through staging → alpha_src/recycle. Defined in `ops/core/factormeta.py`. Persistent — must not be regenerated lossily.
- **`~/.cache/ops/factor_state.json`** — per-host lifecycle state (FactorRecord: name, author, status, updated_at, submitted_at/by, history of CheckRecord). JSON backend with fcntl locking; can be rebuilt from meta.json + directory location.

## Backfilled Factors

The 2551 legacy entries have `submitted_at = null` and `submitted_by = null`. Their real submission time is not knowable — only `entered_at` (the moment backfill ran) is set. Code reading these fields must tolerate `None`.

## Author Resolution (`parser.py`)

1. XML `<Description author="...">`
2. If author is in `_GENERIC_AUTHORS = {"gsim_users", "unknown", ""}` — fall back to `_infer_author_from_dir()` which strips the `Alpha` prefix and lowercases the leading word (`AlphaFguo20260303LLM010` → `fguo`)
3. Else `"unknown"`

## XML Normalization (`normalize.py`)

Submit auto-rewrites mismatched ids in-place so the factor is runnable from any location:
- `Portfolio.Alpha.@id` → `{dir_name}` (e.g. `AlphaFguo20260520GA001`)
- `Portfolio.Alpha.@module` → `{dir_name}Mod` (must match `Modules.Alpha.@id`, otherwise gsim can't find the class)
- `Modules.Alpha.@id` → `{dir_name}Mod`
- `Modules.Alpha.@module` stem → `{dir_name}`

After `to_lib` / `to_recycle`, check rewrites `Modules.Alpha.@module` to the .py's new absolute path so the factor stays independently runnable from alpha_src or recycle. `__pycache__` is stripped before every move.

## Backfill (`services/backfill/backfill.py`)

One-shot for legacy factors in `alpha_src/` (originally 2194, now 2551 in prod) — builds the npy_index once and reuses it across all `parse_factor()` calls (the optional `npy_index` param avoids 2551 redundant filesystem walks). Skips records that already exist in state.
