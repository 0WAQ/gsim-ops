# Core

## Layer Architecture

Project is organized in 4 layers: `cli/` (argparse + output) → `services/` (orchestration) → `core/` (data models) + `infra/` (I/O, external systems). `utils/` for shared utilities.

## Key Modules

- **infra/config.py**: `Config` class loads YAML. Resolution order: `OPS_CONFIG` env var -> `./config.yaml` -> project root `config.yaml`. Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables (`OPS_GSIM_HOME`, `OPS_STORAGE`, `OPS_WORKSPACE`).
- **core/alpha/metadata.py**: `AlphaMetadata` parses XML configs and Python factor code. Constructor calls `_modify_always()` which modifies XML on disk (paths from config, not hardcoded). `_modify_always` also updates per-Data module `niodatapath` for L2 data (replaces `/datasvc/data/cc/` prefix with config's `nio_data_path`).
- **core/library.py**: `LibraryScanner` scans `alpha_src/` and publishes the index (name/author/has_pnl/dump_days/delay) to the DerivedStore (see `infra/derived/`). Freshness = alpha_src mtime vs the store's `index_built_at` watermark (shared across machines); `ops list --refresh` forces a rescan. 其扫描产物类也叫 `FactorInfo`(与 `infra/info/` 的 store dataclass 同名但不同物,一个是扫盘结果、一个是 factor_info 表模型)。**过渡状态**:三表重构后 metrics/datasources/bcorr 已迁 `factor_snapshot`,但 index 组仍暂存 DerivedStore(`derived/` 层未删,是待清理的僵尸层)。
- **core/metrics.py**: `Metrics` dataclass (ret, tvr, shrp, mdd, fitness). Serialization keys use `ret%`, `tvr%`, `mdd%` to indicate percentage fields.
- **infra/gsim/runner.py**: `Runner` static methods shell out to gsim tools (`run_backtest`, `run_simsummary`, `run_bcorr`) via `subprocess.run` with configurable timeout.
- **infra/ssh.py**: Paramiko-based SSH client.

## State Models

| File | Purpose |
|------|---------|
| `core/state.py` | `FactorStatus` enum, `CheckRecord`, `FactorRecord`(纯状态机,2026-07-06 起不含 author / submitted_by) |
| `core/factormeta.py` | `FactorMeta` dataclass + `META_VERSION` + load/save |
| `infra/info/` | `FactorInfo` dataclass + `InfoStore` ABC + `PostgresInfoStore`(factor_info 表:身份信息 author/discovery_method/created_at) |
| `infra/snapshot/` | `FactorSnapshot` dataclass + `SnapshotStore` ABC + `PostgresSnapshotStore`(factor_snapshot 表:入库时不可变快照 metrics+datasources+index+bcorr) |
| `infra/store/pg_store.py` | Postgres state backend (真相源 since 2026-07-04), `SELECT FOR UPDATE` + check_history JSONB;2026-07-06 去 library_id / author,`id SERIAL` 主键 + `name UNIQUE`,外键引 factor_info |
| `infra/store/redis_store.py` | Redis state backend (回退; 实例仍是 JFS metadata 后端) |
| `infra/store/json_store.py` | JSON state backend, fcntl cross-process lock, atomic write |
| `infra/query.py` | `query_factors(config, ...)` — list 读 info+state+snapshot 三表的唯一入口,返回 `FactorRow = (info, status, last_fail_stage, snapshot)`(当前三次查 + 内存按 name JOIN,TODO 单条 SQL)。health 不走此入口(直接 LibraryScanner.scan + snapshot_store.list) |
| `infra/lock.py` | Per-factor advisory lock (`factor_lock(name, config)` / `FactorLocked`);postgres 后端跨机 PG advisory lock,json/redis 回退 fcntl |

**三表外键**:`factor_state.name` / `factor_snapshot.name` 均 `REFERENCES factor_info(name) ON DELETE CASCADE`。删 factor_info 级联删 state + snapshot(`ops rm` 走这条)。

## Factor Metrics

Metrics (ret%, shrp, mdd%, tvr%, fitness) 由 `simsummary` 算出,存 `factor_snapshot` 表(`infra/snapshot/`),按 `name` 键。

**语义(2026-07-06 变更)**:这些指标是**入库时不可变快照**(`snapshot_at = factor_state.entered_at`),不是"最新表现"。因子通过 check、archive 入库前,`_persist_derived` 跑 simsummary 并把 metrics + datasources + bcorr + index 四组一次性 insert 进 factor_snapshot,之后永不更新。如需最新表现须重跑 backtest。

- **旧路径**:`ops refresh [--metrics]` 重算——**已废弃删除**(命令不存在)。快照不可刷新。
- REJECTED 因子不写 snapshot(未入库)。

**simsummary output columns** (whitespace-separated):
```
dates long short pnl ret% tvr% shrp(IR) dd% win fitness ddStart ddEnd
[0]   [1]  [2]   [3] [4]  [5]  [6] [7]  [8] [9] [10]   [11]    [12]
```

## NIO Data Types (`/usr/local/gsim/gsim/utils/NioData.py`)

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

## Gsim Data Structure

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

## Config Fields

```yaml
users:                             # User email mappings
path:
  dropbox_path:                    # Source dropbox (/mnt/storage/dropbox/, QR-owned, read-only)
  staging:                         # ops-owned workspace for in-flight factors (flat layout)
  alpha_src:                       # Factor source code (post-archive)
  alpha_dump:                      # Daily target positions per factor
  alpha_pnl:                       # Backtest results
  recycle:                         # 已退役 (2026-07),保留字段兼容;不再有因子落此
  pnl_prod_path:                   # Gsim production PNL
  pnl_alphalib:                    # Archive PNL
  pnl_automated:                   # 机器挖掘因子 pnl 池 (discovery_method=automated),archive 时分流
  pnl_manual:                      # 人工挖掘因子 pnl 池 (discovery_method=manual)
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
