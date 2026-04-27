# ops tools

Gsim Operations CLI for alpha factor validation and management.

## Installation

```bash
uv sync
```

## Usage

```bash
uv run ops --help
uv run ops submit --help
uv run ops check --help
uv run ops status --help
uv run ops list --help
uv run ops info --help
uv run ops health --help
uv run ops backfill --help
```

## Subcommands

| Command | Description |
|---------|-------------|
| `submit`  | Submit factors from dropbox to staging, generate `meta.json`, mark state=SUBMITTED |
| `check`   | 6-stage validation pipeline (runs in staging, archives to library or recycle) |
| `status`  | Query factor lifecycle state (submitted/active/rejected/...) |
| `list`    | List factors in library (filter, sort, metrics, datasources) |
| `info`    | Show factor details (metadata, metrics, data sources) |
| `health`  | Factor library health check (orphans, missing PNL/metrics/datasources) |
| `backfill`| One-shot: generate `meta.json` + ACTIVE state for existing factors in `alpha_src/` |

## Factor Workflow

```
dropbox/{user}/{date}/AlphaXxx/   (QR-owned, read-only source)
    │  ops submit
    ▼
staging/AlphaXxx/  +  meta.json   (state=SUBMITTED, flat layout)
    │  ops check
    ├── pass ──► alpha_src/AlphaXxx/                  (state=ACTIVE)
    └── fail ──► recycle/{user}/{stage}/AlphaXxx/     (state=REJECTED)
```

State is tracked in `~/.cache/ops/factor_state.json` (JSON store, fcntl-locked).
`meta.json` lives inside each factor directory and serves as the factor's identity card.

### Validation Pipeline (ops check)

1. **Checkbias** — Short backtest with DataFirewall (AST-injected) to detect forward-looking bias
2. **Checkpoint** — Breakpoint validation (5 days)
3. **Long Backtest** — Full historical (20150101-20251231)
4. **Compliance** — Position limits (max 5% per stock, min 50 long/short, 100 total)
5. **Correlation** — Factor correlation < 0.7 against existing library
6. **Archive** — Save metrics, move to library

### Examples

```bash
uv run ops submit -u wbai -s 20260401                      # Submit a day's factors
uv run ops submit -u wbai -s 20260401 -f AlphaWbaiReversal # Submit one factor
uv run ops check                                           # Check everything in staging
uv run ops status AlphaWbaiReversal                        # Query one factor's state
uv run ops status -u wbai --status submitted               # Filter by author/state
uv run ops backfill --dry-run                              # Preview backfill on alpha_src/
uv run ops list --sort shrp -n 10                          # Top 10 by Sharpe
uv run ops list --filter-by "ret>30,tables=ashare*"        # Filter by metrics/tables
uv run ops info AlphaXxx                                   # Factor details
uv run ops health --fix                                    # Auto-fix missing metrics
```
