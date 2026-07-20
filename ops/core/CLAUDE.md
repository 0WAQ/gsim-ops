# Core

## Layer Architecture

Project is organized in 4 layers: `cli/` (argparse + output) → `services/` (orchestration) → `core/` (data models) + `infra/` (I/O, external systems). `utils/` for shared utilities.

## Key Modules

- **infra/config.py**: `Config` class loads YAML. Resolution order: `OPS_CONFIG` env var -> `./config.yaml` -> project root `config.yaml`. Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables (`OPS_GSIM_HOME`, `OPS_STORAGE`, `OPS_WORKSPACE`).
- **core/alpha/metadata.py**: `AlphaMetadata` parses XML configs and Python factor code。构造只读盘解析,无写盘副作用;alpha_dump 工作区扫描(原 get_v2npy_files / get_last_v*npy_file)2026-07-11 迁出至 `ops/services/check/checker/dumpscan.py`(领域类型不带盘面 I/O),死代码 get_last_v1npy_file 删除。niodatapath 改写在 `_update_data_niodatapath`(纯内存,把 `/datasvc/data/cc/` 前缀换成 config 的 `nio_data_path`),由 check 的 `prepare_for_initial`(`ops/services/check/xml_prepare.py`)调用并经 `ops/utils/xmlio.save_xml` 落盘。
- **core/library.py**: `LibraryScanner` — 磁盘视角的对账工具(2026-07-07 Wave 2 起退出命令热路径:list 因子集=factor_state、info 存在性=factor_info,均纯 PG)。`scan()` 纯磁盘遍历无缓存,留给未来 ops doctor;`get()` 供 info 做单因子现场 stat。扫描产物类 `ScannedFactor`(2026-07-09 更名,原名 FactorInfo 与 `infra/info/` 表模型同名撞车,D4;`author_guess` 明示目录名正则是猜测)。core 不再运行期 import infra(Config 走 TYPE_CHECKING 纯类型引用)。
- **core/factor.py**: `Factor` 聚合 —— **全库唯一叫"因子"的类型**(2026-07-09 阶段 2):`identity: FactorIdentity`(身份,factor_info 的领域形态)+ `state: FactorRecord | None` + `snapshot: FactorSnapshot | None` 三切面,由 `FactorRepository` 组装,service 层只见它。软校验:snapshot 存在 ⇒ `snapshot_at == entered_at`(warn 不炸;注意 **approve 合法产生无快照的 ACTIVE**,故没有"ACTIVE ⇒ snapshot"不变量)。FactorIdentity / FactorSnapshot 的 dataclass 正主在此(infra/info、infra/snapshot 分别以别名/re-import 保存量路径)。
- **core/datasource.py**: 数据源解析纯函数(2026-07-09 自 services/list 迁入):`parse_datasources`(AST 走查 `getData` 字面量)/ `build_npy_index`(扫 nio_data_path,L2 `cn_equity*` 软链特例)/ `resolve_tables`。submit/check 共用。
- **core/factormeta.py**: `FactorMeta`(meta.json 身份证格式)+ `parse_factor`(因子目录 → FactorMeta,2026-07-09 自 services/submit/parser.py 迁入)+ `infer_author_from_dir`(目录名 → author 词法推断)。
- **core/paths.py**: `FactorPaths` — 盘面布局唯一正主(SSOT S4,2026-07-09)。`FactorPaths.of(name, config)` 拼出因子在 alphalib 的全部落点;布局事实由类型承载:src/staging/dump 是目录,pnl/池副本/feature 是**单文件**;feature 命名 `<name>.<v1|v2>.npy`(`FEATURE_VERSIONS`);meta.json 文件名常量 `META_FILENAME`。冻结可 pickle(pack 的 ProcessPool worker 直传)。**任何地方不得再手写 `config.alpha_xxx / name`**。边界:check 期工作区路径(pnl_path/alpha_path/checkpoint_path)属 AlphaMetadata 工作台,不在此列。布局契约测试 `tests/test_factor_paths.py`。
- **core/prodxml.py**: 归档生产化改写 SSOT(factor-produce-v3.md §4):三张声明式
  规则表(SET/REPLACE/SUFFIX_STRIP)把因子 XML 改成生产态,`repo.archive` 归档时 +
  存量迁移脚本调用;纯函数、幂等;坑位备忘(Universe 例外 / Mod 削除范围 /
  module basename)固化在 module docstring。参数 `ProdParams.from_config`(produce 块)。
- **core/dumpfiles.py** / **core/universe.py**: alpha_dump 逐日布局走查正主 /
  cc 数据根元数据读取(轴 + .meta 快照锁)。pack 与 produce 共用。
- **core/metrics.py**: `Metrics` dataclass (ret, tvr, shrp, mdd, fitness). Serialization keys use `ret%`, `tvr%`, `mdd%` to indicate percentage fields. 另有 **`SNAPSHOT_METRICS` 注册表 + `metric_value()`**(2026-07-11,SSOT S8):可过滤/排序 metric 键集与取值语义(bcorr=abs(max_bcorr))的唯一定义,SQL 下推表达式(infra/snapshot)、list 内存兜底、CLI --sort-by choices(经 cli/common)三方派生。新增可排序 metric = 注册表加一行(snapshot 表须有对应列)。
- **infra/gsim/runner.py**: `Runner` static methods shell out to gsim tools (`run_backtest`, `run_simsummary`, `run_bcorr`) via `subprocess.run` with configurable timeout.

## State Models

| File | Purpose |
|------|---------|
| `core/state.py` | `FactorStatus` enum, `CheckRecord`, `HistoryEvent` + `HISTORY_OPS`(factor_history 领域形态,v2b), `FactorRecord`(纯状态机,2026-07-06 起不含 author / submitted_by;v2b 起不含 rejected_at/last_fail_* —— 派生自事件表;`CORRELATION` 常量) |
| `core/factor.py` | `Factor` 聚合 + `FactorIdentity` + `FactorSnapshot`(三切面领域类型,2026-07-09)+ `last_fail: HistoryEvent` 派生切面与 `correlation_rejected()` 谓词(v2b 自 FactorRecord 上移 —— 需要 state+history 两个切面) |
| `core/factormeta.py` | `FactorMeta` dataclass + `META_VERSION` + load/save + `parse_factor`/`infer_author_from_dir` |
| `infra/info/` | `InfoStore` ABC + `PostgresInfoStore`(factor_info 表;`FactorInfo` 是 core `FactorIdentity` 的别名) |
| `infra/snapshot/` | `SnapshotStore` ABC + `PostgresSnapshotStore`(factor_snapshot 表;`FactorSnapshot` dataclass 正主在 core/factor.py) |
| `infra/repository.py` | `FactorRepository` —— service 层读写因子的唯一门面(get/find/register/transition/attach_snapshot/delete/exists/lock + paths/purge_artifacts) |
| `infra/store/pg_store.py` | Postgres state backend (真相源 since 2026-07-04), `SELECT FOR UPDATE`;v2b:check_history JSONB 退役,factor_history 全操作审计表(DDL + emit_on 在此;无 FK 活过 rm)+ last_fail/history 派生读 |
| `infra/store/json_store.py` | JSON state backend(单机 dev/test;非生产回退), fcntl cross-process lock, atomic write |
| `infra/lock.py` | Per-factor advisory lock (`factor_lock(name, config)` / `FactorLocked`);postgres 后端跨机 PG advisory lock(conninfo 缺失硬错误),json dev/test 后端 fcntl |

**三表外键**:`factor_state.name` / `factor_snapshot.name` 均 `REFERENCES factor_info(name) ON DELETE CASCADE`。删 factor_info 级联删 state + snapshot(`ops rm` 走这条)。

## Factor Metrics

Metrics (ret%, shrp, mdd%, tvr%, fitness) 由 `simsummary` 算出,存 `factor_snapshot` 表(`infra/snapshot/`),按 `name` 键。

**语义(v3 2026-07-13:测得快照)**:最近一次 check 测得的表现(snapshot_at = 测得时刻)。pass 与 correlation/compliance 失败都写(被拒因子在 list/approve 评审可见指标);每行不可变、新测量原子替换;仍只由 check 写,如需最新表现须重跑 backtest(无离线重算)。

- **旧路径**:`ops refresh [--metrics]` 重算——**已废弃删除**(命令不存在)。快照不可刷新。
- checkbias/checkpoint 等早期失败没测出指标,不写(NULL 诚实);correlation/compliance 失败写(v3)。

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
  compliance:                      # 全史每日 + 容忍 + 严重违规立拒(2026-07-16 重做)
    max_position_pct: 0.05         # Max 5% per stock
    min_total_stocks: 100
    min_long_stocks: 50
    min_short_stocks: 50
    violation_tolerance: 10        # 全史违规日 > 此值才拒
    hard_position_mult: 2.0        # 严重违规线 = max_position_pct × 此(单日超线立拒)
  correlation:
    corr_threshold: 0.7            # Pass if < 0.7
produce:                           # 归档生产化 + ops produce 驱动共用(factor-produce-v3.md §7)
  nio_data_path:                   # 生产数据根(170 本机 cc_all;与 check 的 path.nio_data_path 分族)
  enddate:                         # TODAY(盘前产 T 日仓位)/ TODAY-1(只看回测)/ 钉死日
  startdate:                       # 20110101(照抄现役产线,D2)
  backdays:                        # 256(D2/D10;>256 自 20110101 起 gsim 崩)
  checkpoint_root:                 # 产线 dataset 三根(170 本机,沿用现役资产 D4)
  dump_root:
  pnl_root:
  datasvc_prefix:                  # REPLACE-② 前缀迁移(空串 = 不迁移;Universe 例外内置)
  module_prefix:                   # @module 跨机稳定前缀(D11)
```
注:这些值在**归档时写死进 XML**(`core/prodxml.py`),改 config 只影响此后
归档的因子;整改存量重跑 `scripts/migrate_prod_xml.py`。
