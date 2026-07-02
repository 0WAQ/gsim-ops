# Check Pipeline

`CheckerPipeline` in `check.py` runs 7 stages sequentially per factor:

0. **Validate** - Minimal backtest (20241201-20241202) without DataFirewall — validates factor code/config can run at all
1. **Checkbias** - Short backtest (20241201-20241231) with DataFirewall injection for forward-looking bias detection
2. **Checkpoint** - Breakpoint stability validation (5-day checkpoint)
3. **Long Backtest** - Full historical backtest (20150101-20251231), pure run, no checks
4. **Compliance** - Position limits (max 5% per stock), min stock counts (50 long, 50 short, 100 total)
5. **Correlation** - 相关性 + 业绩门槛 (单一 stage,见下)
6. **Archive** - Run simsummary, save metrics to index, move to library; Fail: move to recycle folder

**Correlation stage 门槛** (`checker/correlation_checker.py`):

| 项 | 门槛 | config key |
|---|---|---|
| ret% (年化) | ≥ 阈值 | `correlation.ret%` (默认 10.0) |
| shrp | > 阈值 | `correlation.shrp` (默认 2.0) |
| tvr% (换手) | ≤ 阈值,delay 分桶 | `correlation.tvr_d0%` (默认 60.0) / `tvr_d1%` (默认 50.0) |
| bcorr | < 阈值 否则需打败竞品 | `correlation.corr_threshold` (默认 0.7) |

`tvr` 上限按因子 `<Alpha @delay>` 选 d0/d1。任一项不达标 → `CorrelationFail` (REJECTED + recycle,日志含违反项,例 `tvr%=55.00 > 50.0 (delay=1)`)。

**Failure semantics**:
- validate / long_backtest fail → revert to SUBMITTED, factor stays in staging (environmental/config issue, retry via `ops check --retry`)
- checkbias / checkpoint / compliance / correlation / archive fail → REJECTED, factor moved to recycle (factor quality issue, QR must fix code and re-submit)

Uses `ProcessPoolExecutor` (max 20 workers) for parallel factor checking.

Checkers inherit from `Checker` ABC in `checker/base.py`. Failures raise `CheckFail`; skippable issues raise `CheckSkip`.

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

## Reconciliation (`reconcile.py`)

`ops check` runs a reconcile pass first. Walks every record in state.json against the filesystem (staging / alpha_src / recycle) and repairs drift caused by processes dying between a filesystem move and the matching state transition:

| state status | location found in | action |
|---|---|---|
| SUBMITTED | staging | ok |
| SUBMITTED | alpha_src | → ACTIVE (move done, state didn't catch up) |
| SUBMITTED | recycle | → REJECTED |
| SUBMITTED | nowhere | drop record |
| CHECKING | staging | → SUBMITTED (crashed mid-check) |
| CHECKING | alpha_src | → ACTIVE |
| CHECKING | recycle | → REJECTED |
| CHECKING | nowhere | drop record |
| ACTIVE | not in alpha_src | warn (don't auto-fix — surprising) |
| REJECTED | not in recycle | warn |
| DELETED | staging | → SUBMITTED (re-submitted, tombstone invalidated) |
| DELETED | alpha_src | → ACTIVE (tombstone invalidated) |
| DELETED | recycle | → REJECTED (tombstone invalidated) |
| DELETED | nowhere | ok |

Filesystem is the source of truth; reconcile only touches state.

## Concurrency

Every submit / check operation on a factor acquires a non-blocking per-factor fcntl lock at `~/.cache/ops/locks/{name}.lock`. If contended, the caller logs a warning and skips (no queueing). This is *advisory* — protects against two `ops` processes racing on the same factor, not against external rm/mv.
