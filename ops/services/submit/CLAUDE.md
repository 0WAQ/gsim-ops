# Submit & Factor Lifecycle

## State Machine

`SUBMITTED → CHECKING → ACTIVE | REJECTED`

(DELETED 是 soft-delete 标记,由 `ops rm` 进入;DECAYING/RETIRED 暂未实现)

`ops cancel` / `ops clear` 不是状态转移,而是把因子从生命周期里**完全移除**:
- `ops cancel`: state 有 record (SUBMITTED / `--force` 含 CHECKING) → 删 staging + 硬删 state record(从未 ACTIVE,无 tombstone)
- `ops clear`: state 无 record(进程被 SIGKILL 等非常态崩溃留下的 staging 残骸)→ 仅删 staging 目录

**Flow**:
```
dropbox/{user}/{date}/AlphaXxx/      (QR-owned, read-only source)
    │  ops submit  → factor_lock → re-check state → copy → parse → meta.json + state=SUBMITTED
    │     │
    │     └─ 文件数不合规 / parse 失败 / store.put 异常:自动 rmtree staging,
    │        不留 orphan(只有进程崩溃才需要 ops clear 兜底)
    ▼
staging/AlphaXxx/  +  meta.json      (flat layout, ops-owned)
    │  ops cancel  → 删 staging + 硬删 state record(撤回未入库因子)
    │
    │  ops check   → 7-stage pipeline run
    ├── pass ──► alpha_src/AlphaXxx/                  state=ACTIVE
    │                │  ops recheck   (原代码不变,重跑 check;--purge 顺带清 dump/feature)
    │                │  ops resubmit  (从 dropbox 提交新代码,version += 1)
    │                │  → 搬回 staging/ + state→SUBMITTED
    │                ▼
    │            [回到 staging,等待下一次 ops check]
    ├── fail (validate/long_backtest)
    │            → staging/ (kept in-place)            state→SUBMITTED  (retry via ops check --retry)
    └── fail (checkbias/checkpoint/compliance/correlation/archive)
                 → alpha_src/AlphaXxx/ (src 归档)      state=REJECTED
                     │  ops recheck -s rejected         → staging/ + state→SUBMITTED
                     ▼
                 [same flow as new factor]
```

**A factor record is never deleted from state.json** — it transitions through statuses but stays. REJECTED records keep `last_fail_stage` / `last_fail_reason` for auditing. (reconcile 已下线;不再有自动 drop orphan 逻辑。)

## Two Persistence Layers

- **`meta.json`** inside each factor directory — the factor's *identity card*. Fields: name, author, birthday, universe, category, delay, backdays, dump_alpha, has_intraday_curve, operations, declared_data_modules, datasources (fields+tables), code_lines, frequency, submitted_by, submitted_at. Travels with the factor through staging → alpha_src. Defined in `ops/core/factormeta.py`. Persistent — must not be regenerated lossily.
- **`~/.cache/ops/factor_state.json`** — per-host lifecycle state (FactorRecord: name, author, status, updated_at, submitted_at/by, history of CheckRecord). JSON backend with fcntl locking; can be rebuilt from meta.json + directory location.

## Backfilled Factors

The 2551 legacy entries have `submitted_at = null` and `submitted_by = null`. Their real submission time is not knowable — only `entered_at` (the moment backfill ran) is set. Code reading these fields must tolerate `None`.

## Author Resolution (`parser.py`)

1. `_infer_author_from_dir()` — strips `Alpha` prefix and takes the leading lowercase run (`AlphaFguo20260303LLM010` → `fguo`). 目录命名规范 `Alpha{User}{Xxx}` 是权威来源。
2. 推不出来(返回 `unknown`)→ 回退到 XML `<Description author="...">`,若其又落入 `_GENERIC_AUTHORS = {"gsim_users", "unknown", ""}` 则最终为 `"unknown"`。

**Watch out**: `_infer_author_from_dir` 纯词法,不识身份。`AlphaInterpFoo` → `interp`,哪怕是 lhw 提交的。`submit_one` 会在 `meta.author != submitted_by` 时打 warn。`ops cancel -u <user>` / `ops clear -u <user>` 按推断 author 过滤(不是 `submitted_by`),off-spec 命名会落到非预期 bucket。拿不准用单因子模式或 `ops status -u <user>` 看推断结果。

## XML Normalization (`normalize.py`)

Submit auto-rewrites mismatched ids in-place so the factor is runnable from any location:
- `Portfolio.Alpha.@id` → `{dir_name}` (e.g. `AlphaFguo20260520GA001`)
- `Portfolio.Alpha.@module` → `{dir_name}Mod` (must match `Modules.Alpha.@id`, otherwise gsim can't find the class)
- `Modules.Alpha.@id` → `{dir_name}Mod`
- `Modules.Alpha.@module` stem → `{dir_name}`

After `to_lib` / `on_reject`, check rewrites `Modules.Alpha.@module` to the .py's new absolute path so the factor stays independently runnable from alpha_src. `__pycache__` is stripped before every move.

## Submit Atomicity (`submit.py::run_submit`)

每个因子串行走一遍 `factor_lock → 锁内 re-check state → _copy_one_to_staging → submit_one`,
关闭了 filter→copy→lock 之间的并发窗口。任何阶段失败(parse 抛错、`store.put` 异常、文件数
不合规)都会 `rmtree(staged)` 回滚,正常路径下不再产生 orphan staging。`_build_npy_index`
在 batch 入口扫一次,传给每个 `parse_factor()` 复用,避免 N 个因子 N 次全盘 scan。

`copy_to_staging(config, dirs)` 是给 `ops resubmit` 留的批量 wrapper,内部就是循环 `_copy_one_to_staging`。

## Backfill (`services/backfill/backfill.py`)

One-shot for legacy factors in `alpha_src/` (originally 2194, now 2551 in prod) — builds the npy_index once and reuses it across all `parse_factor()` calls (the optional `npy_index` param avoids 2551 redundant filesystem walks). Skips records that already exist in state.
