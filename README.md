# ops tools

Gsim Operations CLI for alpha factor validation and management.

## Installation

```bash
uv sync
```

## Usage

```bash
uv run ops --help
uv run ops check --help
uv run ops list --help
uv run ops info --help
uv run ops health --help
```

## Subcommands

| Command | Description |
|---------|-------------|
| `check` | Factor validation pipeline (bias, checkpoint, backtest, compliance, correlation) |
| `list`  | List factors in library (filter, sort, metrics, datasources) |
| `info`  | Show factor details (metadata, metrics, data sources) |
| `health`| Factor library health check (orphans, missing PNL/metrics/datasources) |

## Factor Workflow

```
User Workspace                    ops check                     Factor Library
/mnt/storage/dropbox/    ──────────────────────────────►    /mnt/storage/alphalib/
  {user}/{date}/Alpha*/         6-stage validation              alpha_src/
                                                                alpha_dump/
                                                                alpha_pnl/
```

### Validation Pipeline (ops check)

1. **Checkbias** — Short backtest with DataFirewall (AST-injected) to detect forward-looking bias
2. **Checkpoint** — Breakpoint validation (5 days)
3. **Long Backtest** — Full historical (20150101-20251231)
4. **Compliance** — Position limits (max 5% per stock, min 50 long/short, 100 total)
5. **Correlation** — Factor correlation < 0.7 against existing library
6. **Archive** — Save metrics, move to library

### Examples

```bash
uv run ops check -u wbai -s 20260401 -e 20260415          # Validate factors
uv run ops list --sort shrp -n 10                          # Top 10 by Sharpe
uv run ops list --filter-by "ret>30,tables=ashare*"        # Filter by metrics/tables
uv run ops info AlphaXxx                                   # Factor details
uv run ops health --fix                                    # Auto-fix missing metrics
```
