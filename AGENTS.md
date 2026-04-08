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
| `check-bias` | Bias checking | `ops/check_bias/` |
| `cp` | Dropbox file transfer + compilation | `ops/cp/` |

Note: `scp` and `compiler` subcommands exist but are commented out in `main.py`.

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

- **config.yaml** - Runtime config with hardcoded paths:
  - Dropbox paths (`/mnt/storage/dropbox/`, `/home/wbai/alpha/dropbox`)
  - PNL paths (`/usr/local/gsim/pnl_prod`, `/usr/local/gsim/pnl_pool`)
  - Alpha library paths
  - User email mappings
  - Compliance thresholds (max_position_pct: 0.05, min_stocks: 50)
  - Correlation threshold: 0.7

## Architecture

```
ops/
├── main.py           # CLI entry + subparser registration
├── common/           # Shared utilities
│   ├── config.py     # Config loader (YAML)
│   ├── runner.py     # Backtest runner
│   ├── ssh.py        # SSH client init
│   ├── email.py      # Email notifications
│   └── alpha/        # Alpha metadata, results, checkers
├── check/            # Factor validation pipeline
│   ├── check.py      # Main pipeline orchestrator
│   └── checker/      # Compliance, checkpoint, correlation checkers
├── check_bias/       # Bias checking
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
