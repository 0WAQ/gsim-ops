# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ops** is a Python CLI for alpha factor validation, backtesting, and lifecycle management. It orchestrates a 6-stage validation pipeline for quantitative trading factors before they enter the production factor library.

## Commands

```bash
uv sync                              # Install dependencies (uses uv, not pip)
uv run ops --help                    # CLI help
uv run ops check <factors>           # Run 6-stage validation pipeline
uv run ops list                      # List factors in library (staging)
uv run ops list -c config.prod.yaml  # List factors in production library
uv run ops list --author wbai        # Filter by author
uv run ops list --refresh            # Force rebuild index cache
uv run ops list --format json        # JSON output
uv run ops info <factor-name>        # Show factor details
uv run ops health                    # Factor library health check
uv run ops health --fix              # Auto-refresh missing metrics/datasources
```

No test suite exists. Python 3.10+ required (see `.python-version`). Package manager is **uv** (not pip).

```bash
uv sync          # Install dependencies
uv add <pkg>     # Add new dependency
uv run <cmd>     # Run command in venv
```

## Architecture

Entry point: `ops/main.py` (argparse dispatcher). Each subcommand lives in its own package under `ops/` with an `args.py` (CLI registration) and implementation module.

| Subcommand | Purpose | Module |
|------------|---------|--------|
| `check` | Alpha factor validation pipeline | `ops/services/check/` |
| `list` | List factors in the library | `ops/cli/list.py` + `ops/services/list/` |
| `info` | Show factor details | `ops/cli/info.py` + `ops/services/info/` |
| `health` | Factor library health check | `ops/cli/health.py` + `ops/services/health/` |

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
  dropbox_path:                    # Source dropbox (/mnt/storage/dropbox/)
  dropbox_path_target:             # Local copy target
  alpha_src:                       # Factor source code
  alpha_dump:                      # Daily target positions per factor
  alpha_pnl:                       # Backtest results
  recycle:                         # Failed factors destination
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

**Phase 1: 状态管理 + 通知自动化**

State tracking integrated into `CheckerPipeline`; structured Feishu notifications on check pass/fail; new `ops submit` and `ops status` commands.

New modules:
| File | Purpose |
|------|---------|
| `core/state.py` | `FactorStatus` enum, `CheckRecord`, `FactorRecord` dataclass |
| `infra/store/base.py` | `StateStore` ABC |
| `infra/store/json_store.py` | JSON file backend (`~/.cache/ops/factor_state.json`), fcntl locking |
| `infra/notify/notifier.py` | Wraps `FeishuBot`, typed methods: `notify_check_passed()`, `notify_check_failed()` |
| `services/submit/submit.py` | Validate dropbox structure, record state, optionally trigger check |
| `services/status/status.py` | Query factor state by name/author |
| `cli/submit.py` | `ops submit -u wang -s 20250420` |
| `cli/status.py` | `ops status [AlphaXxx] [-u author]` |

Modified: `services/check/check.py` (~15 lines: state transitions + notify), `main.py` (register subcommands).

**Phase 2: 因子质量监控** — Rolling IC/IR, coverage, autocorrelation, correlation drift. SQLite replaces JSON store. `ops monitor` command (cron). Threshold alerts via Feishu.

**Phase 3: 计算编排** — Factor DAG, incremental updates, retry/alerting. `ops run`, `ops retire`, `ops recheck`.

**Phase 4: 服务化** — FastAPI over services layer, Redis cache, Streamlit/Grafana dashboard.

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
- [ ] Factor state tracking (submitted/checking/active/rejected lifecycle)
- [ ] `ops submit` - Structured factor submission from dropbox
- [ ] `ops status` - Query factor lifecycle state
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
