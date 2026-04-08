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

### Index Caching (TODO)

当前 `ops list` 和 `ops info` 每次都扫描目录，因子数量多时会很慢。

**计划方案：JSON 索引缓存**
```
/mnt/storage/alphalib/
├── .index.json          # 缓存索引文件
├── alpha_src/
├── alpha_dump/
└── alpha_feature/
```

- 首次扫描生成 `.index.json`
- 后续直接读索引，秒级响应
- `ops list --refresh` 强制重建索引
- 可选：检测目录 mtime 自动判断是否需要刷新

## Planned Features

### 1. Factor Storage & Management

#### Factor Metadata Management
- [x] `ops list` - List all factors (filter by author, date, status)
- [x] `ops info <factor>` - View factor details
- [ ] Index caching for fast queries
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
