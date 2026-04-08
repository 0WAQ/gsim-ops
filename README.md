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
uv run ops cp --help
```

## Subcommands

| Command | Description |
|---------|-------------|
| `check` | Factor validation pipeline (bias, checkpoint, backtest, compliance, correlation) |
| `cp` | Copy factors from dropbox and compile |

## Factor Workflow

```
User Workspace                    ops check                     Factor Library
/mnt/storage/dropbox/    ──────────────────────────────►    /mnt/storage/alphalib/
  {user}/{date}/Alpha*/         6-stage validation              alpha_src/
                                                                alpha_dump/
                                                                alpha_feature/
```

## Roadmap

### 1. Factor Storage & Management

#### Factor Metadata Management
- [ ] Factor registry (name, author, create date, status)
- [ ] Factor versioning (track updates to same factor)
- [ ] Tags/categories (momentum, value, technical, etc.)

#### Factor Lifecycle
- [ ] Enable/disable factors (soft delete)
- [ ] Archive/unarchive factors
- [ ] Expired factor cleanup policy

#### Factor Query
- [ ] `ops list` - List all factors (filter by author, date, status)
- [ ] `ops info <factor>` - View factor details
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
