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
uv run ops cp                        # Copy factors from remote dropbox
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
| `check` | Alpha factor validation pipeline | `ops/check/` |
| `cp` | Dropbox file transfer via SSH | `ops/cp/` |
| `list` | List factors in the library | `ops/list/` |
| `info` | Show factor details | `ops/info/` |

Deprecated subcommands (`scp`, `compiler`) are commented out in `main.py`.

### Check Pipeline (`ops/check/`)

`CheckerPipeline` in `check.py` runs 6 stages sequentially per factor:

1. **Checkbias** - Short backtest (20241201-20241231) with DataFirewall injection for forward-looking bias detection
2. **Checkpoint** - Breakpoint validation (5 days)
3. **Long Backtest** - Full historical (20150101-20251231)
4. **Compliance** - Position limits (max 5% per stock), min stock counts (50 long, 50 short, 100 total)
5. **Correlation** - Factor correlation < 0.7 threshold against existing library
6. **Archive** - Pass: move to library; Fail: move to recycle folder

Uses `ProcessPoolExecutor` (max 20 workers) for parallel factor checking.

Checkers inherit from `Checker` ABC in `ops/check/checker/base.py`. Failures raise `CheckFail`; skippable issues raise `CheckSkip`.

### Common Infrastructure (`ops/common/`)

- **config.py**: `Config` class loads YAML. Resolution order: `OPS_CONFIG` env var -> `./config.yaml` -> project root `config.yaml`.
- **alpha/metadata.py**: `AlphaMetadata` parses XML configs and Python factor code. **Warning**: constructor has side effects (`_modify_always()` modifies XML on disk).
- **runner.py**: `Runner` static methods shell out to gsim tools (`run_backtest`, `run_simsummary`, `run_bcorr`) via `subprocess.run` with 30-min timeout.
- **library.py**: `LibraryScanner` scans `alpha_src/` with JSON index caching at `~/.cache/ops/` (1-hour TTL, `INDEX_MAX_AGE_SECONDS = 3600`). `--refresh` forces rebuild. Performance: ~1.7s cold -> ~0.26s cached.
- **ssh.py**: Paramiko-based SSH client.

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
├── core/                  # Pure business logic (no filesystem/subprocess)
│   ├── models.py          # All data models (AlphaKey, FactorInfo, Metrics, CheckResult...)
│   └── checker/           # Checkers (pure rule evaluation)
│       ├── base.py        # CheckFail/CheckSkip + Checker ABC
│       ├── compliance.py  # Input: ndarray, not file paths
│       ├── checkpoint.py
│       ├── checkbias.py   # DataFirewall injection logic
│       └── correlation.py
│
├── services/              # Orchestration: combines core + infra
│   ├── library.py         # Factor library ops (scan, get, filter, health)
│   ├── check.py           # Check pipeline (read files -> call checkers -> archive)
│   └── gsim.py            # Gsim interaction (backtest, simsummary, bcorr)
│
├── infra/                 # Infrastructure: file I/O, external systems
│   ├── config.py          # Config loading + path resolution
│   ├── cache.py           # Index cache (~/.cache/ops/)
│   ├── xml.py             # XML read/write (extracted from AlphaMetadata)
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
├── api/                   # (Future) Web API layer
│
└── utils.py               # Common utilities (date_range, md5sum, LowerAction)
```

**Design principle**: CLI and future API both call the same services layer. Core logic has no I/O dependencies.

**Execution plan** (7 waves, 17 tasks):

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1-2 | Create directory structure + `core/models.py`, extract `ops/utils.py` |
| 2 | 3-7 | Migrate infra layer: config, cache, xml (extract from AlphaMetadata), ssh, notify |
| 3 | 8 | Migrate `core/checker/` (all checkers, pure rule evaluation) |
| 4 | 9-11 | Create services: `gsim.py` (merge Runner+Gsim), `library.py`, `check.py` |
| 5 | 12-15 | Migrate CLI: `fmt.py`, `main.py`, `list.py`, `info.py`, `check.py`, `cp.py` |
| 6 | 16 | Delete old code (common/, check/, list/, info/, cp/, compiler/, scp/) |
| 7 | 17 | Full verification + fix imports |

**Key migration details**:
- Task 1: Merge data models from `common/alpha/key.py`, `common/library.py`, `common/metrics.py`, `common/alpha/results/`, `common/alpha/report.py` into `core/models.py`
- Task 5 (xml.py): `_modify_always()` side effect extracted as `prepare_for_check()` standalone function, called explicitly by `services/check.py` instead of in constructor
- Task 9 (gsim.py): Merge `common/runner.py` Runner + `common/utils.py` Gsim into single `GsimService` class, single `BacktestError`
- Task 13: Update `pyproject.toml` entry point to `ops.cli.main:main`

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
| 1 | 1-2 | Data source parser (`ops/common/datasource.py`), enhance `Metrics` with `dd` field and `from_pnl()` class method |
| 2 | 3-5 | Integrate into `LibraryScanner` (new fields + cache version bump), enhance `ops info` and `ops list` output |
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

## Roadmap

### Factor Storage & Management
- [x] `ops list` - List all factors (filter by author)
- [x] `ops info <factor>` - View factor details
- [x] Index caching for fast queries
- [ ] Factor data sources (parse `dr.getData()` from Python code)
- [ ] PNL metrics in info/list (from simsummary, not Readme)
- [ ] `ops health` - Factor library health check
- [ ] Factor registry, versioning, tags/categories
- [ ] `ops search <keyword>`
- [ ] Enable/disable, archive/unarchive factors
- [ ] Multi-machine factor sync, backup/restore

### Factor Analysis
- [ ] Factor-to-factor correlation matrix, clustering, redundancy detection
- [ ] PNL decomposition, alpha decay, turnover analysis
- [ ] Max drawdown, volatility, VaR/CVaR

### Factor Combination
- [ ] Multi-factor synthesis (equal, IC-weighted, optimization-based)
- [ ] Factor orthogonalization (residualization, PCA, Gram-Schmidt)
- [ ] Portfolio optimization (mean-variance, risk parity, constraints)

### Production Deployment
- [ ] Daily signal/position generation, smoothing, transaction cost modeling
- [ ] Cron scheduling, failure alerting (email, Feishu), run history
- [ ] Live PNL tracking, health dashboard, anomaly detection
