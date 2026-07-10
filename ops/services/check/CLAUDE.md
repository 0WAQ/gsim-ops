# Check Pipeline

`CheckerPipeline` in `check.py` runs 7 stages sequentially per factor:

0. **Validate** - Minimal backtest (`VALIDATE_WINDOW`) without DataFirewall — validates factor code/config can run at all
1. **Checkbias** - Short backtest (`CHECKBIAS_WINDOW`) with DataFirewall injection for forward-looking bias detection
2. **Checkpoint** - Breakpoint stability validation (5-day checkpoint)
3. **Long Backtest** - Full historical backtest (`LONG_BACKTEST_WINDOW`), pure run, no checks
4. **Compliance** - Position limits (max 5% per stock), min stock counts (50 long, 50 short, 100 total)
5. **Correlation** - 相关性 + 业绩门槛 (单一 stage,见下)
6. **Archive** - Run simsummary, persist snapshot, move to library。

## Stage 表(`stages.py`,2026-07-07 Wave 4)

**stage 身份的唯一事实源是 `stages.py` 的 `PIPELINE` 元组**:每行一个
`Stage(name, make_checker, prepare, retryable, keep_artifacts_on_fail)`,顺序即执行
顺序。`STAGES` / `RETRYABLE_STAGES` / `KEEP_ARTIFACTS_STAGES` 全部由 PIPELINE 派生,
`_run_one_locked` 是一个 for-loop(原先 6 段复制粘贴的运行块 + 4 处手抄 stage
集合已删)。**新增 stage = 在 PIPELINE 加一行**。`CORRELATION` 常量从这里导出
(approve 的放行判定、archive 捕获 corr_result 落 bcorr 快照都引它,不再手写字符串)。

**异常归因**:`CheckFail`/`CheckSkip` **不携带 stage** —— 流水线在捕获时按"当前
正在跑的 stage"归因(`current_stage` 盖章)。原先 12 个单行异常子类
(ValidateFail…CorrelationFail)各自硬编码 stage 字符串,checker 代码复制到新
stage 时旧字符串跟着走即静默路由错误,已全部删除;checker 直接
`raise CheckFail("原因")`。

**XML prepare**(`xml_prepare.py`):每个 stage 的窗口/dump 开关声明式改写
(`_apply(factor, window=…, dump_pnl=…, dump_alpha=…)`),窗口是命名常量
(`VALIDATE_WINDOW`/`CHECKBIAS_WINDOW`/`LONG_BACKTEST_WINDOW`)。**prepare 失败
直接抛**(原先整段 `except: ...` 吞掉,stage 会拿上个 stage 的窗口继续错跑),由
unexpected-error 臂接住 → revert SUBMITTED + 完整日志。`prepare_for_archive` 只
"拆雷"(pnl/dump 输出目录改指 `/tmp/alphalib`,防手动重跑入库 XML 砸生产);
@module 由 `to_lib` 搬完目录后的 `rewrite_module_path`(`ops/utils/factor_dir.py`,
与 restage 共用)唯一负责。XML 读写统一走 `ops/utils/xmlio.py`(unparse 参数
只写一处)。

## Archive 细节

归档时先 `transition` state 设 `entered_at`(入库时间),再 `_persist_derived` 把四组派生数据一次性 **insert 进 `factor_snapshot`**(入库时不可变快照,`snapshot_at = entered_at`):**metrics**(simsummary)、**datasources**(AST parse `getData` + npy index)、**bcorr**(correlation stage 已算出的 `max_bcorr`,之前被丢弃,现捕获落库,零额外计算)、**delay**(XML 解析定死,入库时写真值;原 index 组的 has_pnl/dump_days 已删列)。必须在 `to_lib` 之前调 —— datasources 依赖 `factor.py_file`(此时仍在 staging)。各组独立 try,快照丢了不阻断入库(但快照不可 refresh 重算,`ops refresh` 已删除)。
**stale 自愈**(2026-07-07):insert 前若已存在同名 snapshot 行(迁移期 REJECTED 存量 /
restage 删失败残留)则先 delete 再 insert,并 warn 日志 —— 否则 UNIQUE 冲突被吞,
快照永远停在旧代码(full-review P0-1)。正常路径下旧行已被 restage/--overwrite 删除。按 `discovery_method` 把 pnl 额外拷一份到 `pnl_automated/` 或 `pnl_manual/` (仅入库成功时,REJECTED 不拷;来源未知则 warn 跳过);Fail: mark REJECTED (src 归档到 alpha_src)

**快照落库时机**:主路径(pass→archive)metrics/datasources/bcorr/delay 齐全(原 index 组的 has_pnl/dump_days 已随三表重构删列)。REJECTED 因子(`on_reject`)**不写 snapshot**(未入库);快照不可变,无运维补救路径(旧 `ops refresh` 已删)。

**Correlation stage 门槛** (`checker/correlation_checker.py`):

| 项 | 门槛 | config key |
|---|---|---|
| ret% (年化) | ≥ 阈值 | `correlation.ret%` (默认 10.0) |
| shrp | > 阈值 | `correlation.shrp` (默认 2.0) |
| tvr% (换手) | ≤ 阈值,delay 分桶 | `correlation.tvr_d0%` (默认 60.0) / `tvr_d1%` (默认 50.0) |
| bcorr | < 阈值 否则需打败竞品 | `correlation.corr_threshold` (默认 0.7) |

`tvr` 上限按因子 `<Alpha @delay>` 选 d0/d1。任一项不达标 → `CheckFail`,流水线归因 correlation(REJECTED,日志含违反项,例 `tvr%=55.00 > 50.0 (delay=1)`)。

**bcorr 排除自名**(2026-07-08 PV7):correlation checker 对 bcorr 结果过滤
`name == factor.name` —— 因子永远不该和自己比相关性。主修是离库回收池副本
(restage/`--overwrite`/rm 的 `_recycle_check_artifacts`),此处是防删除失败
残留再造"自鬼影"(自相关≈1 → 被迫打败几乎相同的自己 → 必拒)的双保险。

**bcorr 按因子来源分池** (`discovery_method`):bcorr 只在同类因子间比较,人工因子和机器因子互不撞车。`resolve_bcorr_pools(config, discovery_method)` (`infra/gsim/runner.py`) 决定对比池:`automated` → `pnl_automated/`,`manual` → `pnl_manual/`,来源未知 (legacy 因子 meta/XML 无此字段) 回退全库 (`pnl_prod_path` + `pnl_alphalib`,即分类前旧行为)。高相关时"打败竞品"的竞品业绩 (`_get_prod_factor_metrics`) 也从同类池取。`discovery_method` 由 `AlphaMetadata` 从 XML `<Description @discovery_method>` 读入(现存 `factor_info` 表)。`run_bcorr(pnl, config, pools=None)` 缺省 pools 时走全库统计。

**Failure semantics**(路由策略由 Stage 表的 `retryable` 声明):
- validate / long_backtest fail(retryable)→ revert to SUBMITTED, factor stays in staging (environmental/config issue,下次 ops check 自动重扫)
- checkbias / checkpoint / compliance / correlation / archive fail → REJECTED (factor quality issue, QR must fix code and re-submit)
- 任何 stage 的 prepare 落盘失败 / 非 CheckFail 异常 → unexpected 臂,revert SUBMITTED + 完整日志

失败因子的 src 归档到 `alpha_src/`(与 ACTIVE 同库,状态靠 state 的 `status`/`last_fail_stage`/`last_fail_reason` 区分,不靠目录位置)。compliance/correlation(`keep_artifacts_on_fail`)失败额外保留 pnl + dump(数据完整,有分析价值);checkbias/checkpoint 失败清掉 dump/feature(短期数据不完整)。staging 原物在归档后清除。**不再有 recycle 目录**(见 `on_reject`,原 `to_recycle`)。

Uses `ProcessPoolExecutor` (max 20 workers) for parallel factor checking.

Checkers inherit from `Checker` ABC in `checker/base.py`(`check()` 必须实现 + `clean()` 默认 no-op 钩子,流水线对每个 stage 通过后统一调用)。Failures raise `CheckFail`; skippable issues raise `CheckSkip` —— **不带 stage 参数**,流水线捕获时归因。

`CheckerPipeline.__init__` 收一个可选 `checkers: dict[str, Checker] | None`(依赖注入):不传时按 PIPELINE 表 new 真的 gsim-backed checker(生产行为不变),测试注入 fake checker 在指定 stage 抛 `CheckFail`/`CheckSkip`/`Exception` 来验路由,不碰 gsim。路由/自愈/锁的单测见 `tests/test_check_routing_json.py`(json 后端,CI 常跑)+ `tests/test_check_routing.py`(PG,含 pass→archive)+ `tests/test_check_scan.py`。

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

## 身份不变量(2026-07-10)

**staging 目录名 == XML @id** 是 check 全程依赖的不变量(submit 的
`normalize_factor_xml` 强制 @id := 目录名):state/lock/归档落点全键在 @id,
staging 原物键在目录名。发散时(手工放置 / 中断 submit 的 stale XML)归档会
rmtree `alpha_src/<@id>` —— 可能是另一个在库因子的唯一源码。防线两道:
`run_one` 入口在**任何状态写入前**整单拒绝(返回 error,残留重新 ops submit);
`to_lib` 兜底 RuntimeError(走 unexpected 臂)。to_lib 的 move 落点显式锚定
`paths.src`/`paths.dump`(与 rmtree/rewrite 同锚点)。用例
`tests/test_check_routing_json.py::test_identity_divergence_refused_before_state`。

## State drift & crash recovery

reconcile 已下线(state 共享 PG 后,per-host 本地 `staging` 视图无权裁决全局 state)。
crash 恢复靠两点自愈:`ops check` **按 staging 目录扫描**(不看 state status),崩在半路仍在
staging 的因子下次照样重跑,并覆盖其 `CHECKING` 状态;PG state 事务原子写,drift 窗口只在
"移动文件 → 改 state" 两步之间且极小。真正需要人工介入的残留用 `ops rm` / 后续 `ops doctor` 处理。

## Concurrency

Every submit / check operation on a factor acquires a non-blocking per-factor lock (`factor_lock(name, config)`). postgres 后端用**跨机 PG advisory lock**(CHECKING 期间真正防三机并发 check 同一因子);json dev/test 后端用 per-machine fcntl。If contended, the caller logs a warning and skips (no queueing). This is *advisory* — protects against two `ops` processes (跨机或同机) racing on the same factor, not against external rm/mv. 见 `infra/lock.py`。
