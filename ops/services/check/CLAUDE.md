# Check Pipeline

`CheckerPipeline` in `check.py` runs 7 stages sequentially per factor:

0. **Validate** - Minimal backtest (20241201-20241202) without DataFirewall — validates factor code/config can run at all
1. **Checkbias** - Short backtest (20241201-20241231) with DataFirewall injection for forward-looking bias detection
2. **Checkpoint** - Breakpoint stability validation (5-day checkpoint)
3. **Long Backtest** - Full historical backtest (20150101-20251231), pure run, no checks
4. **Compliance** - Position limits (max 5% per stock), min stock counts (50 long, 50 short, 100 total)
5. **Correlation** - 相关性 + 业绩门槛 (单一 stage,见下)
6. **Archive** - Run simsummary, persist derived data, move to library。入库前 `_persist_derived` 把三组派生数据一次性落库(index 组另由 `LibraryScanner` 扫盘 publish):**metrics**(simsummary)、**datasources**(AST parse `getData` + npy index)、**bcorr**(correlation stage 已算出的 `max_bcorr`,之前被丢弃,现捕获落库,零额外计算)。必须在 `to_lib` 之前调 —— datasources 依赖 `factor.py_file`(此时仍在 staging)。各组独立 try,派生库丢了不致命(可 `ops refresh` 重建),不阻断入库。按 `discovery_method` 把 pnl 额外拷一份到 `pnl_automated/` 或 `pnl_manual/` (仅入库成功时,REJECTED 不拷;来源未知则 warn 跳过);Fail: mark REJECTED (src 归档到 alpha_src)

**派生数据落库时机**:主路径(pass→archive)四组齐全。REJECTED 因子(`on_reject`)的 datasources/bcorr **不补**,靠 `ops refresh` 运维补。

**Correlation stage 门槛** (`checker/correlation_checker.py`):

| 项 | 门槛 | config key |
|---|---|---|
| ret% (年化) | ≥ 阈值 | `correlation.ret%` (默认 10.0) |
| shrp | > 阈值 | `correlation.shrp` (默认 2.0) |
| tvr% (换手) | ≤ 阈值,delay 分桶 | `correlation.tvr_d0%` (默认 60.0) / `tvr_d1%` (默认 50.0) |
| bcorr | < 阈值 否则需打败竞品 | `correlation.corr_threshold` (默认 0.7) |

`tvr` 上限按因子 `<Alpha @delay>` 选 d0/d1。任一项不达标 → `CorrelationFail` (REJECTED,日志含违反项,例 `tvr%=55.00 > 50.0 (delay=1)`)。

**bcorr 按因子来源分池** (`discovery_method`):bcorr 只在同类因子间比较,人工因子和机器因子互不撞车。`resolve_bcorr_pools(config, discovery_method)` (`infra/gsim/runner.py`) 决定对比池:`automated` → `pnl_automated/`,`manual` → `pnl_manual/`,来源未知 (legacy 因子 meta/XML 无此字段) 回退全库 (`pnl_prod_path` + `pnl_alphalib`,即分类前旧行为)。高相关时"打败竞品"的竞品业绩 (`_get_prod_factor_metrics`) 也从同类池取。`discovery_method` 由 `AlphaMetadata` 从 XML `<Description @discovery_method>` 读入。`run_bcorr(pnl, config, pools=None)` 缺省 pools 时仍走全库 (`ops refresh --bcorr` 未分池,保持全库统计)。

**Failure semantics**:
- validate / long_backtest fail → revert to SUBMITTED, factor stays in staging (environmental/config issue, retry via `ops check --retry`)
- checkbias / checkpoint / compliance / correlation / archive fail → REJECTED (factor quality issue, QR must fix code and re-submit)

失败因子的 src 归档到 `alpha_src/`(与 ACTIVE 同库,状态靠 state 的 `status`/`last_fail_stage`/`last_fail_reason` 区分,不靠目录位置)。compliance/correlation 这类 late-stage 失败额外保留 pnl + dump(数据完整,有分析价值);checkbias/checkpoint 失败清掉 dump/feature(短期数据不完整)。staging 原物在归档后清除。**不再有 recycle 目录**(见 `on_reject`,原 `to_recycle`)。

Uses `ProcessPoolExecutor` (max 20 workers) for parallel factor checking.

Checkers inherit from `Checker` ABC in `checker/base.py`. Failures raise `CheckFail`; skippable issues raise `CheckSkip`.

`CheckerPipeline.__init__` 收一个可选 `checkers: dict[str, Checker] | None`(依赖注入):不传时照旧 new 真的 gsim-backed checker(生产行为不变),测试注入 fake checker 在指定 stage 抛 `CheckFail`/`CheckSkip`/`Exception` 来验路由,不碰 gsim。路由/自愈/锁的单测见 `tests/test_check_routing.py` + `tests/test_check_scan.py`。

## Checkbias DataFirewall (`checker/firewall.py`)

Uses AST to inject `@DataFirewall(delay=X, data_attrs={...})` decorator onto the factor's `generate` method.

**AST analysis** (`checkbias_checker.py`):
1. `_GetDataAttrCollector` scans the factor's `__init__` for `self.xxx = dr.getData(...)` and `self.xxx = dr.getData(...).data` assignments
2. Collected attribute names + `ALWAYS_GUARD = {'valid'}` form the `data_attrs` set
3. `_GenerateDecoratorInjector` injects `@DataFirewall(delay=X, data_attrs={...})` onto `generate`

**Runtime**: DataFirewall only wraps attributes in `data_attrs` with `_SafeProxy`. User-created buffers (`np.zeros`, `np.full`, `.copy()`, etc.) are never wrapped — only `dr.getData()` results are subject to forward-looking checks.

**`_SafeProxy` behavior**:
- `__getitem__`: validates date index against `max_di`, truncates data along axis 0 for ndim >= 2 (1D arrays not truncated — may be instrument-dimension)
- **框架级静态数据按 getData tag 排除**:`checkbias_checker.STATIC_TAGS`(当前 `{'ipodate'}`)里的 tag,collector 收集 attr 时直接跳过,**不注入 firewall**。这类数据(如 `ipodate` 每股上市日期,1D `NIO_VECTOR`,长度=instrument 数)不随交易日变化、开盘前已知,factor 常 `self.ipodate[:n]` 按票维切片,若被 wrap 会把票维下标误判成日期前视。按 tag(非 attr 名)排除,QR 命名成 `self.ipo`/`self.ipodate` 都兜得住。`valid` 是另一条路径(`ALWAYS_GUARD`/`ALWAYS_ALLOW_DI`,注入但放行 `[di]`)。
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

## Firewall Injection

`checkbias_checker.py` never mutates the factor's original `.py`. Writes the injected source (firewall code + AST-decorated factor) to `{factor}_firewall.py`, points XML's `Modules.Alpha.@module` at the temp file for the backtest, and restores XML + deletes the temp in `finally`. A crash mid-injection therefore can't leave a half-decorated `.py` that double-decorates next run. AST also guards against pre-existing `@DataFirewall` decorators just in case.

## State drift & crash recovery

reconcile 已下线(state 上共享 redis 后,per-host 本地 `staging` 视图无权裁决全局 state)。
crash 恢复靠两点自愈:`ops check` **按 staging 目录扫描**(不看 state status),崩在半路仍在
staging 的因子下次照样重跑,并覆盖其 `CHECKING` 状态;redis state 原子写,drift 窗口只在
"移动文件 → 改 state" 两步之间且极小。真正需要人工介入的残留用 `ops rm` / 后续 `ops doctor` 处理。

## Concurrency

Every submit / check operation on a factor acquires a non-blocking per-factor lock (`factor_lock(name, config)`). postgres 后端用**跨机 PG advisory lock**(CHECKING 期间真正防三机并发 check 同一因子);json/redis 回退用 per-machine fcntl。If contended, the caller logs a warning and skips (no queueing). This is *advisory* — protects against two `ops` processes (跨机或同机) racing on the same factor, not against external rm/mv. 见 `infra/lock.py`。
