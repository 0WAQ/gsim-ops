# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ops** is a Python CLI for alpha factor validation, backtesting, and lifecycle management. It orchestrates a 6-stage validation pipeline for quantitative trading factors before they enter the production factor library.

## Commands

```bash
uv sync                              # Install dependencies (uses uv, not pip)
uv run ops --help                    # CLI help
uv run ops submit -u wbai -s 20260401            # Submit a day's factors from dropbox
uv run ops submit -u wbai -s 20260401 -f Alpha   # Submit one factor
uv run ops check                                 # Run 6-stage pipeline on staging
uv run ops status AlphaXxx                       # Query factor lifecycle state
uv run ops status -u wbai --status submitted     # Filter by author/state
uv run ops backfill --dry-run                    # Preview backfill on alpha_src/
uv run ops backfill                              # Generate meta.json + ACTIVE for legacy factors
uv run ops list                      # List factors in library (staging)
uv run ops list -c config.prod.yaml  # List factors in production library
uv run ops list --author wbai        # Filter by author
uv run ops list --refresh            # Force rebuild index cache
uv run ops list --format json        # JSON output
uv run ops info <factor-name>        # Show factor details
uv run ops health                    # Factor library health check
uv run ops health --fix              # Auto-refresh missing metrics/datasources
uv run ops pack                      # Aggregate alpha_dump → alpha_feature (skip already-packed)
uv run ops pack --force              # Rewrite all factors
uv run ops pack --factor AlphaXxx    # Pack one factor
uv run ops sync push                 # Incremental push (manifest-driven + state merge)
uv run ops sync push --dry-run       # Preview transfers
uv run ops sync pull                 # Pull state (merge) + factors missing locally
uv run ops sync status               # Quick local-vs-remote summary (no data scan)
uv run ops sync verify               # Slow: rclone check across all dirs
uv run ops rm AlphaXxx               # 软删除:仅打 DELETED 标,文件保留
uv run ops rm AlphaXxx --force       # 同时删本地 dump + feature(保留 src/pnl)
```

No test suite exists. Python 3.10+ required (see `.python-version`). Package manager is **uv** (not pip).

```bash
uv sync          # Install dependencies
uv add <pkg>     # Add new dependency
uv run <cmd>     # Run command in venv
```

## Architecture

Entry point: `ops/main.py` (argparse dispatcher). Each subcommand lives in its own package under `ops/` with an `args.py` (CLI registration) and implementation module.

### Design Principles

**Destructive operations are opt-in.** Default behavior never deletes user data. Every destructive path lives behind an explicit flag or a separate subcommand. Established patterns to mirror when adding new commands:

- `ops rm` defaults to a state-only soft-delete (`DELETED` tombstone). `--force` removes local `alpha_dump/<name>/` and `alpha_feature/<name>.v{1,2}.npy` only; `alpha_src` and `alpha_pnl` are always preserved.
- `ops sync push` uses `rclone copy` (additive). It never issues `rclone delete`, even for `DELETED` factors. Remote cleanup is the job of the planned `ops sync gc` (opt-in, dry-run by default, `--apply` to actually purge).
- Bulk operations default to dry-run; require `--apply` (or equivalent) to execute.
- State merge prefers data preservation over precision: tied `updated_at` keeps local; missing timestamps treated as epoch zero.

When adding a new command that touches files, state, or remotes: default to the non-destructive path, surface the destructive variant behind a flag, and require explicit user authorization at the scope being acted on.


| Subcommand | Purpose | Module |
|------------|---------|--------|
| `submit` | Copy factors from dropbox to staging, generate `meta.json`, mark SUBMITTED | `ops/services/submit/` |
| `check` | Alpha factor validation pipeline (runs in-place on staging) | `ops/services/check/` |
| `status` | Query factor lifecycle state | `ops/services/status/` |
| `backfill` | One-shot: generate `meta.json` + ACTIVE for existing factors in `alpha_src/` | `ops/services/backfill/` |
| `list` | List factors in the library | `ops/cli/list.py` + `ops/services/list/` |
| `info` | Show factor details | `ops/cli/info.py` + `ops/services/info/` |
| `health` | Factor library health check | `ops/cli/health.py` + `ops/services/health/` |
| `pack` | Aggregate per-date `alpha_dump` files into per-factor `alpha_feature` matrices | `ops/cli/pack.py` + `ops/services/pack/` |
| `sync` | Push/pull factor library (data + state) across servers via rclone | `ops/cli/sync.py` + `ops/services/sync/` |

Removed subcommands: `cp`, `scp`, `compiler`.

### Check Pipeline (`ops/services/check/`)

`CheckerPipeline` in `check.py` runs 6 stages sequentially per factor:

1. **Checkbias** - Short backtest (20241201-20241231) with DataFirewall injection for forward-looking bias detection
2. **Checkpoint** - Breakpoint validation (5 days)
3. **Long Backtest** - Full historical (20150101-20251231)
4. **Compliance** - Position limits (max 5% per stock), min stock counts (50 long, 50 short, 100 total)
5. **Correlation** - Factor correlation < 0.7 threshold against existing library
6. **Archive** - Run simsummary, save metrics to index, move to library; Fail: move to recycle folder

Uses `ProcessPoolExecutor` (max 20 workers) for parallel factor checking.

Checkers inherit from `Checker` ABC in `ops/services/check/checker/base.py`. Failures raise `CheckFail`; skippable issues raise `CheckSkip`.

#### Checkbias DataFirewall (`ops/services/check/checker/firewall.py`)

Uses AST to inject `@DataFirewall(delay=X, data_attrs={...})` decorator onto the factor's `generate` method.

**AST analysis** (`checkbias_checker.py`):
1. `_GetDataAttrCollector` scans the factor's `__init__` for `self.xxx = dr.getData(...)` and `self.xxx = dr.getData(...).data` assignments
2. Collected attribute names + `ALWAYS_GUARD = {'valid'}` form the `data_attrs` set
3. `_GenerateDecoratorInjector` injects `@DataFirewall(delay=X, data_attrs={...})` onto `generate`

**Runtime**: DataFirewall only wraps attributes in `data_attrs` with `_SafeProxy`. User-created buffers (`np.zeros`, `np.full`, `.copy()`, etc.) are never wrapped — only `dr.getData()` results are subject to forward-looking checks.

**`_SafeProxy` behavior**:
- `__getitem__`: validates date index against `max_di`, truncates data along axis 0 for ndim >= 2 (1D arrays not truncated — may be instrument-dimension)
- `__setitem__`: delegates directly to underlying data (supports `self.alpha[idx] = value`)
- `__getattr__`: truncates sub-arrays (`.data`, ndarray attributes); returns original value for metadata (`.shape`, `.dtype`, `.ndim`) to avoid breaking buffer allocation

**Forward-looking access rules**:

| Factor delay | Data dimension | Rule |
|-------------|---------------|------|
| >= 1 | Any | Cannot access `data[di]` (only `data[:di]`) |
| 0 | 2D `[di, ii]` (daily) | Cannot access `data[di]` (daily data unknown until EOD) |
| 0 | 3D `[di, ti, ii]` (intraday) | Can access `data[di, :44, :]` (up to 14:30, ti <= 43) |

Exceptions:
- `self.valid` (in `ALWAYS_ALLOW_DI` set): always allows `[di]` access — tradability info is known before market open

The delay value is read from the factor's XML: `<Alpha delay="0">`.

### Pack (`ops/services/pack/`)

Aggregates per-date `.npy` dumps into per-factor matrices for downstream consumers.

**Source**: `alpha_dump/AlphaXxx/{year}/{month}/{YYYYMMDD}{v1|v2}.npy` (each shape `(H,)`)
**Target**: `alpha_feature/AlphaXxx.{v1|v2}.npy` — memmap, shape `(PACK_L, H)` = `(3900, 5484)`, float64

**Offset rule**: Per-date file at date `D` is placed at row `date_to_idx[D] - 1`. Gsim stores the *next-day* signal computed at close of day `D` — when read back as a feature on day `D-1`'s row, it serves as the previous-day prediction.

**Shape policy** (see `pack.py`):
- `PACK_L = 3900` hardcoded — covers historical universe up to 20251231, matches the check pipeline's backtest end date
- `H` derived from `__universe/Instruments.npy` at write time (currently 5484, stable for 1-2 years)
- Rows with `di >= PACK_L` are silently skipped (future dates from daily incremental data don't belong in the historical pack)
- Per-date arrays longer than `H` raise `ValueError`; shorter are placed at `[di, :h0]` with NaN right-padding (future-proofing for instrument growth)
- **Daily incremental** (rows beyond 20251231) is a separate, not-yet-built path — pre-allocated buffer / generational files / zarr were considered

**Two access paths**:
1. **Batch CLI** (`ops pack`): scans `alpha_dump/`, skips already-packed unless `--force`, `ProcessPoolExecutor` parallel (default 10 workers), wraps each factor in `factor_lock`
2. **Incremental from check** (`pack_one_incremental` called at end of `check.run_one`): if target memmap doesn't exist → falls back to full `pack_one`; otherwise opens `mode='r+'` and overwrites only requested date rows. Failures are non-fatal — warn and continue; `ops pack` will heal next run

**Atomic write**: full rewrites go through `.{name}.{v}.npy.tmp` + `os.replace` so a crashed pack never leaves a partial file in the target path.

**Verification**: after each `pack_one`, `verify_sample` picks up to `VERIFY_SAMPLES = 5` random per-date source files, reloads each, compares against the target memmap row within `ATOL = 1e-6` (NaN-aware). Any mismatch raises and marks the factor failed in the batch summary. `--no-verify` skips this.

### Sync (`ops/services/sync/`)

Cross-server factor library sync via rclone. Ships **data + state together** so a new machine bootstraps with `ops sync pull`.

**Remote layout**:
```
<sync.remote>/<library_id>/
├── alpha_src/
├── alpha_dump/
├── alpha_pnl/
├── alpha_feature/
└── .state/              # dotfile so it's hidden from casual `rclone ls`
    ├── factor_state.json
    ├── metrics.json
    └── datasources.json
```

**`library_id`** (`Config.library_id`): defaults to `alpha_src.parent.name` (e.g. `alphalib`), overridable via `sync.library_id`. Two machines pointing at the same logical library get the same id regardless of absolute paths — which is what lets state files travel.

**Cache layout** (`ops/infra/cache.py`):
- Old: `~/.cache/ops/{md5(config_path)[:8]}.{index|metrics|datasources}.json` + `~/.cache/ops/factor_state.json`
- New: `~/.cache/ops/lib/<library_id>/{index,metrics,datasources,factor_state}.json`
- `cache_path(library_id, filename, legacy_hash=...)` resolves the new path and one-shot migrates any legacy file on first call — no manual migration step
- `index.json` is **not** synced (1h TTL, regenerated on demand); locks (`~/.cache/ops/locks/`) are fcntl, per-machine, never synced

**Per-subdir rclone tuning** (`sync.py:DATA_FLAGS`):
| Subdir | Flags | Why |
|---|---|---|
| `alpha_dump` | `--transfers 32 --checkers 32` | Millions of tiny .npy files |
| `alpha_feature` | `--transfers 8 --checksum` | Large memmap, content-stable |
| `alpha_src`, `alpha_pnl` | defaults | Few small files |

**Transfer model: manifest-driven `rclone copy` (additive, never deletes)**.

`rclone sync` was rejected because (a) it must list both sides to compute the diff — alpha_dump alone has ~1.8M files; (b) it deletes destination files missing from the source, which would wipe factors that exist only on the other machine.

Instead, `ops sync push` keeps a per-machine `~/.cache/ops/lib/<library_id>/sync_manifest.json` recording each factor's fingerprint:
- `src_mtime` / `pnl_mtime` / `feature_v{1,2}_mtime` — max mtime within that subtree
- `dump_latest` (newest YYYYMMDD dir) + `dump_count` — alpha_dump grows by appending a new date dir per check; new date + changed count ⇒ only those date dirs need shipping

Scan walks one `os.scandir(alpha_src)` (one stat per factor, not per file), descending into a factor's dirs only when its top-level fingerprint moved. Changed files feed `rclone copy --files-from --no-traverse` so rclone skips the remote-side listing entirely. Manifest is only advanced after the corresponding `rclone copy` returns 0; partial pushes naturally re-send next time.

**First-run is automatic.** No `--bootstrap` / `init` flags exposed:
- `ops sync push` on a machine without a manifest: treats it as empty — every factor looks new to `scan_changes`. `rclone copy` is additive so already-present remote files are skipped; manifest is written only after a successful push.
- `ops sync pull` on an empty machine (zero local factors): full `rclone copy` of every data dir, then build the manifest from what just landed.

**State merge** (`ops/services/sync/merge.py`). Each of `factor_state.json`, `metrics.json`, `datasources.json` carries a per-entry `updated_at` ISO timestamp. The sync step:
1. `rclone copyto remote/.state/<file> /tmp/<file>` (3 small files, cheap)
2. Per-name: pick the entry with newer `updated_at`; tie → keep local
3. Atomic write merged result to local, then upload to remote

`factor_state.json` merge holds the JsonStateStore fcntl lock so a concurrent `ops check` finishing on this machine can't lose its write. Missing `updated_at` on legacy entries treated as `1970-01-01`. `index.json` is **not** synced (1h TTL, regenerable). `sync_manifest.json` is per-machine, also not synced.

**Pull semantics** — pull always merges state first. If the local library is empty, falls back to a full `rclone copy` of every data dir. Otherwise (manifest exists or just got built), uses the merged `factor_state.json` as the "remote manifest of factor names": names present in remote state but missing on local disk are fetched per-subdir (one `rclone copy` per factor).

**Operations**:
- `ops sync push` — incremental data + state merge
- `ops sync pull` — state merge + pull factors referenced by state but missing locally
- `ops sync status` — counts only (no data scan); reports local-vs-remote-state diff
- `ops sync verify` — full `rclone check` across all subdirs (slow; use occasionally)

**Soft-delete**: `ops rm <name>` flips state to `DELETED` (a tombstone) — `list`/`health` hide it by default; `ops list -s deleted` shows them. The tombstone propagates to other machines via the next `ops sync push` (state merge). **Sync never `rclone delete`s anything** — soft-delete on machine A causes machine B's next `list` to drop the factor too, but the remote files persist. Reclaiming remote disk for deleted factors is the job of the (deferred) `ops sync gc`. `ops rm --force` drops the *local* dump dir + feature `.npy` (src/pnl always kept).

### Factor Lifecycle

State machine: `SUBMITTED → CHECKING → ACTIVE | REJECTED → (DECAYING → RETIRED)`.

**Flow**:
```
dropbox/{user}/{date}/AlphaXxx/      (QR-owned, read-only source)
    │  ops submit  → parse_factor() → write meta.json + state=SUBMITTED
    ▼
staging/AlphaXxx/  +  meta.json      (flat layout, ops-owned)
    │  ops check   → reconcile → in-place pipeline run
    ├── pass ──► alpha_src/AlphaXxx/                  state=ACTIVE
    └── fail ──► recycle/{user}/{stage}/AlphaXxx/     state=REJECTED
```

**A factor record is never deleted from state.json** — it transitions through statuses but stays. REJECTED records keep `last_fail_stage` / `last_fail_reason` for auditing. The only thing reconcile drops are pure orphans (status SUBMITTED/CHECKING with no files anywhere on disk).

**Two persistence layers**:
- **`meta.json`** inside each factor directory — the factor's *identity card*. Fields: name, author, birthday, universe, category, delay, backdays, dump_alpha, has_intraday_curve, operations, declared_data_modules, datasources (fields+tables), code_lines, frequency, submitted_by, submitted_at. Travels with the factor through staging → alpha_src/recycle. Defined in `ops/core/factormeta.py`. Persistent — must not be regenerated lossily.
- **`~/.cache/ops/factor_state.json`** — per-host lifecycle state (FactorRecord: name, author, status, updated_at, submitted_at/by, history of CheckRecord). JSON backend with fcntl locking; can be rebuilt from meta.json + directory location.

**Backfilled factors** (the 2551 legacy entries) have `submitted_at = null` and `submitted_by = null`. Their real submission time is not knowable — only `entered_at` (the moment backfill ran) is set. Code reading these fields must tolerate `None`.

**Author resolution** (`services/submit/parser.py`):
1. XML `<Description author="...">`
2. If author is in `_GENERIC_AUTHORS = {"gsim_users", "unknown", ""}` — fall back to `_infer_author_from_dir()` which strips the `Alpha` prefix and lowercases the leading word (`AlphaFguo20260303LLM010` → `fguo`)
3. Else `"unknown"`

**XML normalization** (`services/submit/normalize.py`): submit auto-rewrites mismatched ids in-place so the factor is runnable from any location.
- `Portfolio.Alpha.@id` → `{dir_name}` (e.g. `AlphaFguo20260520GA001`)
- `Portfolio.Alpha.@module` → `{dir_name}Mod` (must match `Modules.Alpha.@id`, otherwise gsim can't find the class)
- `Modules.Alpha.@id` → `{dir_name}Mod`
- `Modules.Alpha.@module` stem → `{dir_name}`

After `to_lib` / `to_recycle`, check rewrites `Modules.Alpha.@module` to the .py's new absolute path so the factor stays independently runnable from alpha_src or recycle. `__pycache__` is stripped before every move.

**Checkbias firewall injection** (`services/check/checker/checkbias_checker.py`): never mutates the factor's original `.py`. Writes the injected source (firewall code + AST-decorated factor) to `{factor}_firewall.py`, points XML's `Modules.Alpha.@module` at the temp file for the backtest, and restores XML + deletes the temp in `finally`. A crash mid-injection therefore can't leave a half-decorated `.py` that double-decorates next run. AST also guards against pre-existing `@DataFirewall` decorators just in case.

**Concurrency** (`infra/lock.py`): every submit / check operation on a factor acquires a non-blocking per-factor fcntl lock at `~/.cache/ops/locks/{name}.lock`. If contended, the caller logs a warning and skips (no queueing). This is *advisory* — protects against two `ops` processes racing on the same factor, not against external rm/mv.

**Reconciliation** (`services/check/reconcile.py`): `ops check` runs a reconcile pass first. Walks every record in state.json against the filesystem (staging / alpha_src / recycle) and repairs drift caused by processes dying between a filesystem move and the matching state transition:

| state status | location found in | action |
|---|---|---|
| SUBMITTED | staging | ok |
| SUBMITTED | alpha_src | → ACTIVE (move done, state didn't catch up) |
| SUBMITTED | recycle | → REJECTED |
| SUBMITTED | nowhere | drop record |
| CHECKING | staging | → SUBMITTED (crashed mid-check) |
| CHECKING | alpha_src | → ACTIVE |
| CHECKING | recycle | → REJECTED |
| CHECKING | nowhere | drop record |
| ACTIVE | not in alpha_src | warn (don't auto-fix — surprising) |
| REJECTED | not in recycle | warn |

Filesystem is the source of truth; reconcile only touches state.

**Backfill** (`services/backfill/backfill.py`): one-shot for legacy factors in `alpha_src/` (originally 2194, now 2551 in prod) — builds the npy_index once and reuses it across all `parse_factor()` calls (the optional `npy_index` param avoids 2551 redundant filesystem walks). Skips records that already exist in state.

**Modules**:
| File | Purpose |
|------|---------|
| `core/state.py` | `FactorStatus` enum, `CheckRecord`, `FactorRecord` |
| `core/factormeta.py` | `FactorMeta` dataclass + `META_VERSION` + load/save |
| `infra/store/json_store.py` | JSON state backend, fcntl cross-process lock, atomic write |
| `infra/lock.py` | Per-factor advisory fcntl lock (`factor_lock(name)` / `FactorLocked`) |
| `services/submit/parser.py` | Parse xml/py → `FactorMeta` (author fallback, npy_index reuse) |
| `services/submit/normalize.py` | Auto-rewrite mismatched XML ids in-place |
| `services/submit/submit.py` | Scan dropbox → `copy_to_staging()` → `submit_one()` per factor (factor_lock-wrapped) |
| `services/check/check.py` | reconcile on startup; `_scan_factors` reads staging; `to_lib`/`to_recycle` clean __pycache__ + rewrite XML @module + state transition; incremental pack after archive |
| `services/check/reconcile.py` | state ↔ filesystem reconciliation |
| `services/check/checker/checkbias_checker.py` | AST-inject DataFirewall into a temp `_firewall.py` (original .py untouched) |
| `services/backfill/backfill.py` | Generate meta.json + ACTIVE for legacy `alpha_src/` factors |
| `services/pack/pack.py` | Aggregate per-date alpha_dump → alpha_feature memmap; full + incremental + sampled verify |
| `services/sync/sync.py` | rclone push/pull/status for data + state to a remote |
| `infra/cache.py` | `cache_path()` — ~/.cache/ops/lib/<library_id>/* with one-shot legacy hash migration |
| `services/status/status.py` | Query/format state records |

### Common Infrastructure

Project is organized in 4 layers: `cli/` (argparse + output) → `services/` (orchestration) → `core/` (data models) + `infra/` (I/O, external systems). `utils/` for shared utilities.

- **infra/config.py**: `Config` class loads YAML. Resolution order: `OPS_CONFIG` env var -> `./config.prod.yaml` -> project root `config.prod.yaml`. Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables (`OPS_GSIM_HOME`, `OPS_STORAGE`, `OPS_WORKSPACE`).
- **core/alpha/metadata.py**: `AlphaMetadata` parses XML configs and Python factor code. Constructor calls `_modify_always()` which modifies XML on disk (paths from config, not hardcoded). `_modify_always` also updates per-Data module `niodatapath` for L2 data (replaces `/datasvc/data/cc/` prefix with config's `nio_data_path`).
- **core/library.py**: `LibraryScanner` scans `alpha_src/` with JSON index caching at `~/.cache/ops/` (1-hour TTL, `INDEX_MAX_AGE_SECONDS = 3600`). `--refresh` forces rebuild.
- **core/metrics.py**: `Metrics` dataclass (ret, tvr, shrp, mdd, fitness). Serialization keys use `ret%`, `tvr%`, `mdd%` to indicate percentage fields.
- **infra/gsim/runner.py**: `Runner` static methods shell out to gsim tools (`run_backtest`, `run_simsummary`, `run_bcorr`) via `subprocess.run` with configurable timeout.
- **infra/ssh.py**: Paramiko-based SSH client.

### Factor Metrics

Metrics (ret%, shrp, mdd%, tvr%, fitness) are obtained via `simsummary` and cached in `~/.cache/ops/{hash}.metrics.json`.

**Two update paths**:
- **Batch**: `ops list --refresh-metrics` — runs simsummary on all factors with PNL files, writes full index
- **Incremental**: `ops check` — after a factor passes all checks and before archiving, runs simsummary and appends to index via `update_metrics()`

**Usage**:
```bash
ops list --refresh-metrics         # Batch refresh all metrics
ops list --sort shrp -n 10         # Top 10 by Sharpe
ops info AlphaXxx                  # Shows metrics if cached
```

**simsummary output columns** (whitespace-separated):
```
dates long short pnl ret% tvr% shrp(IR) dd% win fitness ddStart ddEnd
[0]   [1]  [2]   [3] [4]  [5]  [6] [7]  [8] [9] [10]   [11]    [12]
```

### Factor Data Sources

Data sources (tables and fields a factor reads via `dr.getData()`) are extracted by AST-parsing the factor `.py` and resolved to table names through an npy index. Cached at `~/.cache/ops/{hash}.datasources.json`.

**Resolution pipeline** (`ops/services/list/datasource.py`):
1. AST walk finds `*.getData(string_literal)` calls → `fields` list
2. `_build_npy_index(nio_data_path)` scans `/datasvc/data/cc/` to build `{npy_stem → table_dir}`
3. `resolve_tables(fields, index)` maps each field to its parent directory

**L2 data special case**: Directories starting with `cn_equity*` have one extra level — real `.npy` files live in `cn_equity_*/sub_table/` and the parent `cn_equity_*/` contains symlinks. The index follows symlinks only (`if npy_file.is_symlink()`) and uses the `sub_table` as the resolved table name.

**Usage**:
```bash
ops list --refresh-datasources      # Batch parse all factors
ops list --show-tables              # Add tables column
ops list --show-fields              # Add fields column
ops info AlphaXxx                   # Shows tables + fields
```

### Filter Syntax (`--filter-by`)

Comma-separated `key<op>value` expressions. Comparison ops (`>`, `<`, `>=`, `<=`, `=`, `!=`) need shell quoting to avoid stdout redirect: `--filter-by "ret>30,shrp>=1.5"`.

**Supported keys**:
- `tables` — glob match (fnmatch) against any factor table, e.g. `tables=ashare*`
- `field` — exact match against any factor field
- `ret`, `shrp`, `mdd`, `tvr`, `fitness`, `dump_days` — numeric comparison

Repeated keys AND together: `--filter-by "ret>20,ret<=30"`.

**Validation**: unknown keys, invalid syntax, and empty expressions print an error and exit early (no output). Regex was considered but deferred — glob covers the common case.

### Dual Config Strategy

`config.yaml` points to staging paths for `ops check` output and review. `config.prod.yaml` points to production `/mnt/storage/alphalib/`. After review, factors are manually merged to production. Pass `-c config.prod.yaml` to any command to target production.

### Config Fields

```yaml
users:                             # User email mappings
path:
  dropbox_path:                    # Source dropbox (/mnt/storage/dropbox/, QR-owned, read-only)
  staging:                         # ops-owned workspace for in-flight factors (flat layout)
  alpha_src:                       # Factor source code (post-archive)
  alpha_dump:                      # Daily target positions per factor
  alpha_pnl:                       # Backtest results
  recycle:                         # Failed factors destination ({user}/{stage}/AlphaXxx/)
  pnl_prod_path:                   # Gsim production PNL
  pnl_alphalib:                    # Archive PNL
script:
  run_script:                      # Gsim backtest runner
  simsummary_script:               # PNL summary tool
  bcorr_script:                    # Correlation calculator
checker:
  compliance:
    max_position_pct: 0.05         # Max 5% per stock
    min_total_stocks: 100
    min_long_stocks: 50
    min_short_stocks: 50
  correlation:
    corr_threshold: 0.7            # Pass if < 0.7
```

### Key Dependencies

- **paramiko** / **scp** - SSH connections and file transfer
- **pandas** / **numpy** - Data processing
- **lxml** / **xmltodict** - XML config manipulation
- **colorama** - Terminal colors
- **tqdm** - Progress bars
- **pyyaml** - Config parsing

## Key Concepts

### Gsim Backtest Framework

Located at `/usr/local/gsim/`. The core backtesting engine that ops interacts with.

```bash
# Run backtest
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml

# PNL summary
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl

# Correlation test
/usr/local/gsim/dataops/bcorr pnl1 pnl2
/usr/local/gsim/dataops/bcorr pnl1 pnl_folder/
```

### NIO Data Types (`/usr/local/gsim/gsim/utils/NioData.py`)

`dr.getData()` returns NIO objects, not raw numpy arrays. NIO is gsim's data container wrapping `np.ndarray` / `np.memmap`:

| Type | Dim | Shape |
|------|-----|-------|
| `NIO_VECTOR` | 1D | `(N,)` |
| `NIO_MATRIX` | 2D | `(N, M)` — dates × instruments |
| `NIO_CUBE` | 3D | `(N, D, M)` — dates × time_periods × instruments |

All inherit from `NIO_BASE`, which stores the underlying array in `.data` and delegates `__getitem__`/`__setitem__` to it.

**Two data access patterns in factor code**:
1. `self.xxx = dr.getData('...')` → attribute is a NIO object, access via `self.xxx[di]` (NIO delegates to `.data`)
2. `self.xxx = dr.getData('...').data` → attribute is `np.ndarray` or `np.memmap` directly (`memmap` is an `ndarray` subclass)

**Important**: NIO's numpy wrapper is incomplete, so many researchers use `.data` to get the raw array for easier computation.

### User Factor Workspace

Users write factors in:
```
/mnt/storage/dropbox/{unix_id}/{yyyymmdd}/Alpha{UnixId}{FactorName}/
```
- `{unix_id}` - User's Unix ID (e.g., `wbai`, `chaoqun`)
- `{yyyymmdd}` - Date folder (e.g., `20251030`)
- `Alpha{UnixId}{FactorName}` - Factor directory (e.g., `AlphaWbaiMomentum`)

### Factor Library Structure

Located at `/mnt/storage/alphalib/`:
```
/mnt/storage/alphalib/
├── alpha_src/      # Factor source code
├── alpha_dump/     # Daily target positions per factor
└── alpha_feature/  # Aggregated alpha_dump data
```

### Factor Directory Structure

Each factor in `alpha_src/` contains:
```
AlphaXxx/
├── AlphaXxx.py           # Factor code (inherits gsim.AlphaBase)
├── Config.Xxx.xml        # Gsim config file
└── Readme.Xxx.txt        # Backtest report (PNL, correlation, etc.)
```

**Factor Code (.py)**:
- Inherits `gsim.AlphaBase`
- Uses `DataRegistry.getData('table.column')` to fetch data
- Implements `generate(di)` method to produce alpha signal

**Config (.xml)** key sections:
- `<Data>` modules: Declares available data (BUT unreliable - QRs copy-paste all)
- `<Description>`: Metadata (author, category, universe, delay)
- `<Operations>`: Post-processing chain (Decay, Rank, Neutralize)

**Data Source Tracking**:
- **DO NOT trust XML `<Data>` declarations** - QRs include all modules by default
- **Parse Python code** for actual usage: extract `dr.getData('xxx')` calls

### Gsim Data Structure

```
/datasvc/
├── rawdata/           # Raw data (parquet/csv)
│   ├── ashareeodprices/
│   ├── AShareMoneyFlow/
│   ├── asharebalancesheet/
│   ├── Interval5m/
│   └── ...
├── data/cc/           # Cached data (memmap .npy) - gsim reads directly
│   ├── __universe/    # Date and instrument indices
│   ├── ashareeodprices/
│   ├── Interval5m/
│   └── ...
└── template/          # Config templates (all Data module definitions)
    └── config.read_cache.xml
```

**Universe Files** (`/datasvc/data/cc/__universe/`):
- `Dates.npy` - Trading dates index (int64, shape: ~3900)
- `Instruments.npy` - Stock codes index (U32, shape: ~5484)

**Feature Files** (memmap, ~7.6GB each):
- Shape: `[di][ti][ii]` = `[dates][time_periods][instruments]`
- `di` - Date index (0-3899)
- `ti` - Time index (0-48, 5-minute intervals)
- `ii` - Instrument index (0-5483)

**Common Data Sources**:

| Type | Source ID | Example |
|------|-----------|---------|
| Daily K-line | `ashareeodprices` | `dr.getData('ashareeodprices.s_dq_close')` |
| Money flow | `AShareMoneyFlow` | `dr.getData('AShareMoneyFlow.net_inflow_rate_volume')` |
| Balance sheet | `asharebalancesheet` | `dr.getData('asharebalancesheet.accounts_payable')` |
| Income | `ashareincome` | `dr.getData('ashareincome.xxx')` |
| Cash flow | `asharecashflow` | `dr.getData('asharecashflow.xxx')` |
| Analyst consensus | `ashareconsensusrollingdata_*` | `dr.getData('ashareconsensusrollingdata_CAGR.est_eps')` |
| 5-min K-line | `Interval5m` | `dr.getData('Interval5m.close')` |
| Index prices | `aindexeodprices` | `dr.getData('aindexeodprices.s_dq_pctchange_000905')` |

## Known Technical Debt (Deferred)

- **Stub files**: `core/alpha/results/base.py`, `results/checkpoint.py`, `results/checkbias.py` — placeholder, implement when needed
- **Dead code**: `infra/notify/email.py` is commented out — implement or delete later
- **Debug residual**: `utils/func.py` has a `debug()` with infinite loop — remove when cleaning up
- **Feishu credentials hardcoded**: `infra/notify/feishu_send.py` has APP_ID/APP_SECRET in source — move to config/env later
- **BacktestError duplicate**: `utils/exception/exception.py` (stub) vs `infra/gsim/runner.py` (real) — consolidate to one location
- **Checker inheritance inconsistent**: `ComplianceChecker` and `CorrelationChecker` don't inherit `Checker` base class — unify
- **`core/alpha/metadata.py` has I/O**: `_modify_always()`, `save()`, `get_v2npy_files()` do file I/O in core layer — extract to services/infra

## Plans

### Architecture Refactor (Not Started)

Restructure from current flat layout to layered architecture. All existing commands must keep working. No new features, no new dependencies.

**Current problems**:
1. `common/` is a grab-bag — config, SSH, email, gsim runner, alpha metadata all mixed
2. Business logic coupled with CLI — check pipeline logic embedded in argparse handler
3. `AlphaMetadata.__init__` modifies XML and writes to disk — constructor side effects
4. Hardcoded values — SSH username='wbai', host='10.6.100.146', password='123456'
5. Duplicate abstractions — `utils.Gsim` vs `runner.Runner`, two `BacktestError`
6. Stub code — `results/base.py`, `exception.py`, `checkpoint.py` are empty shells
7. No layering — adding future Web API requires rewrite

**Target structure**:
```
ops/
├── core/                  # Data models + pure computation (no I/O)
│   ├── alpha.py           # AlphaKey, AlphaMetadata (no disk write in constructor)
│   ├── metrics.py         # Metrics, CheckResult
│   └── library.py         # FactorInfo and related models
│
├── services/              # Orchestration: combines core + infra
│   ├── check.py           # Check pipeline scheduling (read files -> call checkers -> archive)
│   ├── checker/           # All 6 checkers together (they are pipeline stages)
│   │   ├── base.py        # CheckFail/CheckSkip + Checker ABC
│   │   ├── checkbias.py   # DataFirewall AST injection + backtest
│   │   ├── checkpoint.py  # Breakpoint validation
│   │   ├── backtest.py    # Long backtest
│   │   ├── compliance.py  # Position limits check
│   │   ├── correlation.py # Factor correlation check
│   │   ├── archive.py     # Pass/fail archiving
│   │   └── firewall.py    # DataFirewall + _SafeProxy
│   ├── gsim.py            # Gsim interaction (merge Runner+Gsim, single BacktestError)
│   └── library.py         # Factor library ops (scan, get, filter)
│
├── infra/                 # Infrastructure: file I/O, external systems
│   ├── config.py          # Config loading + path resolution + ${var} substitution
│   ├── cache.py           # Index cache (~/.cache/ops/)
│   ├── notify.py          # Feishu/email notifications
│   └── ssh.py             # SSH connections (username from config, not hardcoded)
│
├── cli/                   # CLI entry: argparse + formatted output
│   ├── main.py            # Entry point + subparser registration
│   ├── check.py           # ops check (thin: parse args -> call service -> output)
│   ├── list.py            # ops list
│   ├── info.py            # ops info
│   ├── cp.py              # ops cp
│   └── fmt.py             # Table/color/progress output utilities
│
└── utils.py               # Common utilities (date_range, md5sum, LowerAction)
```

**Design principles**:
- CLI and future API both call the same services layer
- Core has no I/O dependencies; services handle all I/O
- All 6 checkers live together in `services/checker/` (they are pipeline stages, not independent modules)
- Gradual migration: new code imports old code during transition, delete old modules one by one (no big-bang delete wave)
- No empty placeholder directories (no `api/` until needed)

**Execution plan** (5 waves, 14 tasks):

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1-3 | Skeleton: directory structure, `ops/utils.py`, `core/` models (alpha, metrics, library) |
| 2 | 4-7 | Infra layer: config, cache, ssh, notify |
| 3 | 8-10 | Services: `gsim.py` (merge Runner+Gsim), `checker/` (all 6 stages + firewall), `check.py` (pipeline), `library.py` |
| 4 | 11-13 | CLI: `fmt.py`, `main.py` + entry point update, subcommands (list, info, check, cp) |
| 5 | 14 | Delete old code incrementally + full verification + fix imports |

**Key migration details**:
- Task 1: Split models by domain — `core/alpha.py` (AlphaKey, AlphaMetadata), `core/metrics.py`, `core/library.py` (FactorInfo)
- Task 1: `AlphaMetadata.__init__` no longer writes to disk; `_modify_always()` extracted as `prepare_for_check()` in `services/check.py`
- Task 8: Merge `common/runner.py` Runner + `common/utils.py` Gsim into single `GsimService` class, single `BacktestError`
- Task 12: Update `pyproject.toml` entry point to `ops.cli.main:main`

**Verification after each wave**:
```bash
uv run ops --help
uv run ops list
uv run ops list -u jzhang
uv run ops info AlphaJzhang20260324GA002
uv run ops check --help
uv run ops cp --help
```

### Factor Management Enhancement (Not Started)

Enhance factor management: data source parsing, PNL metrics extraction, health checks.

**Deliverables**:
- Data source parser: extract `dr.getData()` calls from Python code
- PNL metrics extraction via `simsummary` (ret/shrp/dd/fitness)
- Enhanced `ops info` with data sources and PNL metrics
- Enhanced `ops list` with Sharpe column and `--sort` parameter
- New `ops health` command for library integrity checks

**getData call patterns** (observed from real code):
1. Simple: `dr.getData('ashareeodprices.s_dq_close')`
2. With .data: `dr.getData('ashareeodprices.s_dq_volume').data`
3. Special: `dr.getData('cap')`, `dr.getData('status')`, `dr.getData('st')`
4. Dynamic (f-string): `dr.getData(f'equ_fancy_factors_table{i}.xxx')` -> extract static part, mark dynamic as `<dynamic>`

**Key constraints**:
- DO NOT trust XML `<Data>` declarations for data sources
- DO NOT trust Readme.txt for PNL metrics — must use `simsummary` on actual PNL files

**Execution plan** (3 waves, 7 tasks):

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1-2 | Data source parser (`ops/common/datasource.py`), ~~enhance `Metrics` with `dd` field and `from_pnl()` class method~~ ✅ done |
| 2 | 3-5 | ~~Integrate into `LibraryScanner` (new fields + cache version bump), enhance `ops info` and `ops list` output~~ ✅ done |
| 3 | 6-7 | New `ops health` command: orphan factors, dump gaps, PNL missing, source missing, file integrity |

**Health check output format**:
```
Factor Library Health Check
────────────────────────────────────────────────────────────
OK: 7 factors in alpha_src
OK: 7 factors in alpha_dump
WARNING: 2 factors missing PNL files
ERROR: 1 factor has dump date gaps
────────────────────────────────────────────────────────────
Summary: 7 OK | 2 WARNING | 1 ERROR
```

### Factor Lifecycle Architecture (Next)

Factor lifecycle: `提交(submitted) → 验证中(checking) → 入库(active) / 拒绝(rejected) → 监控(monitored) → 衰减(decaying) → 废弃(retired)`.

**Phase 1: 状态管理 + submit/status/backfill + 一致性** ✅ done

Implemented `ops submit` / `ops status` / `ops backfill`, state tracking in `CheckerPipeline`, `meta.json` per factor as identity card, per-factor advisory lock (`infra/lock.py`), and reconcile pass at check startup. See [Factor Lifecycle](#factor-lifecycle).

**Phase 2: 因子质量监控** — Rolling IC/IR, coverage, autocorrelation, correlation drift. SQLite replaces JSON store. `ops monitor` command (cron). Threshold alerts via Feishu.

**Phase 3: 计算编排** — Factor DAG, incremental updates, retry/alerting. `ops run`, `ops retire`, `ops recheck`.

**Phase 4: 服务化** — FastAPI over services layer, Redis cache, Streamlit/Grafana dashboard.

### Consolidate `ops status` into `ops list` + `ops info` (Not Started)

`ops list -s <status>` now covers batch lifecycle filtering (with status-based row coloring) and `ops info <factor>` covers single-factor static info, so `ops status` is mostly redundant. Its only unique surface today is single-factor lifecycle history (the check history list).

**Plan**:
- Move single-factor history rendering into `ops info <factor>` (append a "Lifecycle" / "Check History" section to its existing output).
- Remove `ops status` subcommand: delete `ops/cli/status.py` registration and `ops/services/status/`. Drop the `ops status` line from CLAUDE.md and the example block.
- Verify nothing else imports `ops.services.status`.

**Why deferred**: cosmetic UX cleanup, no functional gap. Do once after the next round of feature work settles.

### `ops factor` Namespace + Cross-Machine Soft-Delete (Not Started)

The CLI surface has grown flat: `submit / check / list / info / health / pack / sync / rm / status / backfill`. The factor-lifecycle ones (`submit`, `check`, `rm`, `info`, `list`, `status`, `backfill`) all act on a single factor (or a query over factors) and naturally belong under one namespace. Plan: introduce `ops factor <verb>` as the canonical home, keep flat aliases for back-compat during transition.

**Target shape**:
```
ops factor add <name>      # alias: ops submit (one factor, possibly inline source)
ops factor rm <name>       # alias: ops rm        (current implementation)
ops factor check [name]    # alias: ops check
ops factor run <name>      # NEW — re-run an existing factor (for refresh / re-pack)
ops factor info <name>     # alias: ops info
ops factor list            # alias: ops list
ops factor status [name]   # alias: ops status   (until folded into info, see prior plan)
```

`pack`, `sync`, `health` stay top-level — they operate on the library, not a single factor.

**Why**: discoverability (`ops factor --help` enumerates everything one can do *to* a factor), and prepares the codebase for similar groupings later (`ops dataset ...`, `ops job ...`).

**Soft-delete model** (already partially landed via `ops rm`):

- `FactorStatus.DELETED` is a tombstone, not a record removal. Files default to staying on disk; `--force` removes local dump + feature only (src/pnl always kept, mirroring the rejected-factor retention rule).
- The tombstone propagates to other machines via `ops sync push` state merge — receivers will hide the factor from `list` / `health` automatically because those filter `status == DELETED` by default.
- **`ops sync push` does NOT issue `rclone delete` for DELETED factors.** State merge alone is the sync mechanism. Remote files stay until a future `ops sync gc` (separate, opt-in) reclaims them.
- `ops sync pull` does not auto-clean local files of newly-DELETED-on-remote factors either. Same reasoning: keep destructive ops out of routine sync; require explicit `gc`.

**`ops sync gc` (deferred)**:
- Walks `factor_state.json`, finds `status == DELETED` records older than some threshold (e.g. 30d), enumerates their remote `alpha_dump/<name>/`, `alpha_feature/<name>.v[12].npy`, and (with extra flag) `alpha_src/<name>/` + `alpha_pnl/<name>`, deletes via `rclone delete`.
- Default-dry-run; explicit `--apply` to actually purge.
- Updates manifest to drop the entries so subsequent push doesn't re-stat them.

**Execution waves** (when picked up):
| Wave | Description |
|---|---|
| 1 | Add `ops factor` parent parser; register existing subcommands as both flat (legacy) and nested (`factor X`). |
| 2 | Implement `ops factor run` (re-run + re-pack one factor in place). |
| 3 | Add `ops sync gc` with dry-run/apply. |
| 4 | Drop the flat aliases (one-shot deprecation, after team is on the new shape). |

## Roadmap

### Factor Storage & Management
- [x] `ops list` - List all factors (filter by author)
- [x] `ops info <factor>` - View factor details
- [x] Index caching for fast queries
- [x] Factor data sources (parse `dr.getData()` from Python code; resolved to tables via npy index)
- [x] Filter by tables/fields/metrics (`--filter-by "tables=ashare*,ret>30"`)
- [x] PNL metrics in info/list (ret%, shrp, mdd%, tvr%, fitness from simsummary)
- [x] Batch metrics refresh (`ops list --refresh-metrics`)
- [x] Incremental metrics update (saved during `ops check` archive step)
- [x] Sort and limit (`ops list --sort shrp -n 10`)
- [x] `ops health` - Factor library health check
- [x] Factor state tracking (submitted/checking/active/rejected lifecycle)
- [x] `ops submit` - Structured factor submission from dropbox to staging
- [x] `ops status` - Query factor lifecycle state
- [x] `ops backfill` - One-shot meta.json + ACTIVE state for legacy factors
- [x] Per-factor advisory lock (concurrent submit/check safety)
- [x] State ↔ filesystem reconcile on check startup
- [x] `ops pack` - Aggregate alpha_dump → alpha_feature (batch + incremental from check)
- [x] `ops sync` - Cross-server library sync via rclone (data + state, stable library_id replaces hash cache keys)
- [x] `ops rm` - Soft-delete a factor (DELETED tombstone; `--force` drops local dump+feature)
- [ ] `ops sync gc` - Reclaim remote files for DELETED factors (opt-in, separate from push/pull)
- [ ] `ops factor` namespace consolidating add/rm/check/run/info/list (see Plans)
- [ ] Daily incremental pack path (rows > 20251231; buffer / generational / zarr — design pending)
- [ ] Factor registry, versioning, tags/categories
- [ ] Enable/disable, archive/unarchive factors

### Factor Lifecycle & Monitoring
- [ ] Automated Feishu notifications on check pass/fail
- [ ] Rolling IC / IC_IR monitoring (20/60 day windows)
- [ ] Factor coverage monitoring (sudden drop = data source failure)
- [ ] Factor autocorrelation monitoring (spike = factor death)
- [ ] Correlation drift detection
- [ ] `ops monitor` command (cron-based)
- [ ] Threshold-based decay alerts

### Computation & Orchestration
- [ ] Factor computation DAG with dependency tracking
- [ ] Incremental update vs full recompute
- [ ] Retry with exponential backoff
- [ ] `ops run` for orchestrated factor computation
- [ ] Batch operations: `ops retire`, `ops recheck`

### Factor Analysis
- [ ] Factor-to-factor correlation matrix, clustering, redundancy detection
- [ ] PNL decomposition, alpha decay, turnover analysis
- [ ] Max drawdown, volatility, VaR/CVaR

### Factor Combination
- [ ] Multi-factor synthesis (equal, IC-weighted, optimization-based)
- [ ] Factor orthogonalization (residualization, PCA, Gram-Schmidt)
- [ ] Portfolio optimization (mean-variance, risk parity, constraints)

### Production & Service
- [ ] FastAPI wrapper over services layer
- [ ] Redis cache layer
- [ ] Streamlit/Grafana dashboard
- [ ] Daily signal/position generation, smoothing, transaction cost modeling
- [ ] Cron scheduling, failure alerting, run history
- [ ] Live PNL tracking, health dashboard, anomaly detection
