# AGENTS.md

## Project Overview

**ops** is a Gsim Operations CLI tool for managing alpha factor validation, backtesting, and file transfers. It's a Python package with subcommands.

## Quick Start

```bash
# Install dependencies
uv sync

# Run the CLI
uv run ops --help

# Activate venv manually if needed
source .venv/bin/activate
```

## Python Version

- **Required**: Python 3.10+
- `.python-version` file at root (contains `3.10`)

## Package Manager

Uses **uv** (not pip). All dependencies in `pyproject.toml`, locked in `uv.lock`.

```bash
uv sync          # Install dependencies
uv add <pkg>     # Add new dependency
uv run <cmd>     # Run command in venv
```

## CLI Subcommands

The main entry point is `ops/main.py`. Current active subcommands:

| Subcommand | Purpose | Module |
|------------|---------|--------|
| `check` | Alpha factor validation pipeline | `ops/check/` |
| `cp` | Dropbox file transfer + compilation | `ops/cp/` |
| `list` | List factors in the library | `ops/list/` |
| `info` | Show factor details | `ops/info/` |

Deprecated subcommands: `scp`, `compiler` - all commented out in `main.py`.

## Check Pipeline (ops check)

The main use case. Runs these stages in order:

1. **Checkbias** - Short backtest (20141201-20141231)
2. **Checkpoint** - Breakpoint validation
3. **Long Backtest** - Full backtest (20150101-20251231)
4. **Compliance** - Position limits, stock count rules
5. **Correlation** - Factor correlation threshold (< 0.7)
6. **Archive** - Pass: move to library; Fail: move to recycle

Uses `ProcessPoolExecutor` for parallel execution (max 20 workers).

## Configuration

### Dual Config Strategy (Staging vs Production)

**Important**: `config.yaml` 中的因子库路径是临时路径（staging），用于 `ops check` 输出和手动审核。审核通过后手动合并到正式因子库。

- **config.yaml** - 临时/staging 路径，`ops check` 输出到此
- **config.prod.yaml** - 生产路径（如需要），指向 `/mnt/storage/alphalib/`

使用方式：
```bash
ops list                      # 使用默认 config.yaml（临时库）
ops list -c config.prod.yaml  # 查看正式库
```

### Config Fields

- Dropbox paths (`/mnt/storage/dropbox/`, `/home/wbai/alpha/dropbox`)
- PNL paths (`/usr/local/gsim/pnl_prod`, `/usr/local/gsim/pnl_pool`)
- Alpha library paths (staging vs production)
- User email mappings
- Compliance thresholds (max_position_pct: 0.05, min_stocks: 50)
- Correlation threshold: 0.7

## Key Concepts

### 1. Gsim Backtest Framework
Located at `/usr/local/gsim/`. The core backtesting engine that ops interacts with.

### 2. User Factor Workspace
Users write factors in:
```
/mnt/storage/dropbox/{unix_id}/{yyyymmdd}/Alpha{UnixId}{FactorName}/
```
- `{unix_id}` - User's Unix ID (e.g., `wbai`, `chaoqun`)
- `{yyyymmdd}` - Date folder (e.g., `20251030`)
- `Alpha{UnixId}{FactorName}` - Factor directory (e.g., `AlphaWbaiMomentum`)

### 3. Factor Library (after ops processing)
Located at `/mnt/storage/alphalib/`:
```
/mnt/storage/alphalib/
├── alpha_src/      # Factor source code
├── alpha_dump/     # Daily target positions per factor
└── alpha_feature/  # Aggregated alpha_dump data
```

### 4. Factor Directory Structure
Each factor in `alpha_src/` contains:
```
AlphaXxx/
├── AlphaXxx.py           # Factor code (inherits gsim.AlphaBase)
├── Config.Xxx.xml        # gsim config file
└── Readme.Xxx.txt        # Backtest report (PNL, correlation, etc.)
```

**Factor Code (.py)**:
- Inherits `gsim.AlphaBase`
- Uses `DataRegistry.getData('table.column')` to fetch data
- Implements `generate(di)` method to produce alpha signal

**Config (.xml)** - Key sections:
- `<Data>` modules: Declares available data (BUT unreliable - QRs copy-paste all)
- `<Description>`: Metadata (author, category, universe, delay)
- `<Operations>`: Post-processing chain (Decay, Rank, Neutralize)

**Data Source Tracking**:
- **DO NOT trust XML `<Data>` declarations** - QRs include all modules by default
- **Parse Python code** for actual usage: extract `dr.getData('xxx')` calls

### 5. Gsim Data Structure

**Data Layer Overview**:
```
/datasvc/
├── rawdata/           # 原始数据（parquet/csv）
│   ├── ashareeodprices/
│   ├── AShareMoneyFlow/
│   ├── asharebalancesheet/
│   ├── Interval5m/
│   └── ...
├── data/cc/           # 缓存数据（memmap .npy）- gsim 直接读取
│   ├── __universe/    # 日期和股票索引
│   ├── ashareeodprices/
│   ├── Interval5m/
│   └── ...
└── template/          # 配置模板（包含所有 Data 模块定义）
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
| 日线K线 | `ashareeodprices` | `dr.getData('ashareeodprices.s_dq_close')` |
| 资金流 | `AShareMoneyFlow` | `dr.getData('AShareMoneyFlow.net_inflow_rate_volume')` |
| 财务报表 | `asharebalancesheet`, `ashareincome`, `asharecashflow` | `dr.getData('asharebalancesheet.accounts_payable')` |
| 分析师预期 | `ashareconsensusrollingdata_*` | `dr.getData('ashareconsensusrollingdata_CAGR.est_eps')` |
| 5分钟K线 | `Interval5m` | `dr.getData('Interval5m.close')` |
| 指数行情 | `aindexeodprices` | `dr.getData('aindexeodprices.s_dq_pctchange_000905')` |

### 6. Gsim Tools
```bash
# Run backtest
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml

# PNL summary
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl

# Correlation test
/usr/local/gsim/dataops/bcorr pnl1 pnl2
/usr/local/gsim/dataops/bcorr pnl1 pnl_folder/
```

## Architecture

```
ops/
├── main.py           # CLI entry + subparser registration
├── common/           # Shared utilities
│   ├── config.py     # Config loader (YAML)
│   ├── library.py    # LibraryScanner - factor library scanning
│   ├── runner.py     # Backtest runner
│   ├── ssh.py        # SSH client init
│   ├── email.py      # Email notifications
│   └── alpha/        # Alpha metadata, results, checkers
├── check/            # Factor validation pipeline
│   ├── check.py      # Main pipeline orchestrator
│   └── checker/      # Compliance, checkpoint, correlation checkers
├── list/             # ops list command
│   ├── args.py       # Argument parser
│   └── list.py       # List implementation
├── info/             # ops info command
│   ├── args.py       # Argument parser
│   └── info.py       # Info implementation
├── compiler/         # Factor compilation (commented out)
├── cp/               # Dropbox transfer + compile
└── scp/              # SCP transfer (commented out)
```

## Key Dependencies

- **paramiko** - SSH connections
- **scp** - SCP file transfer
- **pandas**, **numpy** - Data processing
- **lxml** - XML config manipulation
- **colorama** - Terminal colors/output
- **tqdm** - Progress bars
- **pyyaml** - Config parsing

## Testing

No test infrastructure found. No `tests/` directory, no pytest config.

## File Patterns

- XML configs stored alongside alpha factors
- Results archived to `/mnt/storage/alphalib/alpha_pnl`
- Failed factors moved to `/home/wbai/alpha/recycle`

## Important Paths (from config.yaml)

These are hardcoded server paths - do not change:

```
/mnt/storage/dropbox/              # Source dropbox
/home/wbai/alpha/dropbox/          # Local copy target
/usr/local/gsim/pnl_prod           # Production PNL
/mnt/storage/alphalib/alpha_pnl    # Alpha PNL archive
/tmp/alphalib/                     # Temp alpha dump
```

## Performance Considerations

### Index Caching

`ops list` 使用 JSON 索引缓存加速查询：

```
~/.cache/ops/
├── {config_hash}.index.json   # 按 config 路径 hash 区分
```

- 缓存有效期：1 小时（`INDEX_MAX_AGE_SECONDS = 3600`）
- 过期后自动重建
- `ops list --refresh` 强制刷新缓存
- 性能：无缓存 ~1.7s → 有缓存 ~0.26s

## Planned Features

### 1. Factor Storage & Management

#### Factor Metadata Management
- [x] `ops list` - List all factors (filter by author, date, status)
- [x] `ops info <factor>` - View factor details
- [x] Index caching for fast queries
- [ ] Factor data sources (解析 Python 代码中 `dr.getData()` 调用) ← **next: plan factor-management-enhance**
- [ ] PNL metrics in info/list (从 simsummary 获取, 不信任 Readme) ← **next**
- [ ] Factor registry (name, author, create date, status)
- [ ] Factor versioning (track updates to same factor)
- [ ] Tags/categories (momentum, value, technical, etc.)

#### Factor Lifecycle
- [ ] Enable/disable factors (soft delete)
- [ ] Archive/unarchive factors
- [ ] Expired factor cleanup policy

#### Factor Query
- [ ] `ops search <keyword>` - Search factors

#### Factor Export/Sync
- [ ] Export factor to specified directory
- [ ] Multi-machine factor sync
- [ ] Backup/restore

#### Data Integrity
- [ ] `ops health` - Factor library health check ← **next: plan factor-management-enhance**
- [ ] Detect missing dates in alpha_dump
- [ ] Validate consistency between source code and positions
- [ ] Periodic health checks

### 2. Factor Analysis

#### Correlation Analysis
- [ ] Factor-to-factor correlation matrix
- [ ] Correlation clustering / grouping
- [ ] Redundancy detection (auto-flag highly correlated factors)

#### Performance Attribution
- [ ] PNL decomposition (by sector, market cap, etc.)
- [ ] Alpha decay analysis
- [ ] Turnover analysis

#### Risk Analysis
- [ ] Max drawdown calculation
- [ ] Volatility metrics
- [ ] VaR / CVaR estimation

### 3. Factor Combination

#### Multi-Factor Synthesis
- [ ] Factor weighting schemes (equal, IC-weighted, optimization-based)
- [ ] Factor blending / ensemble
- [ ] Dynamic weight adjustment

#### Factor Orthogonalization
- [ ] Residualization (remove correlation)
- [ ] PCA-based decomposition
- [ ] Gram-Schmidt orthogonalization

#### Portfolio Optimization
- [ ] Mean-variance optimization
- [ ] Risk parity
- [ ] Constraint handling (sector, position limits)

### 4. Production Deployment

#### Signal Generation
- [ ] Daily signal/position generation from factor library
- [ ] Signal smoothing / filtering
- [ ] Transaction cost modeling

#### Scheduling & Automation
- [ ] Cron-based daily runs
- [ ] Failure alerting (email, Feishu)
- [ ] Run history and logs

#### Monitoring
- [ ] Live PNL tracking
- [ ] Factor health dashboard
- [ ] Anomaly detection (sudden performance drop)
