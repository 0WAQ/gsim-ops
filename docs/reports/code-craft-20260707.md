# gsim-ops 代码工艺审查(2026-07-07)

**定位**:本文与 `project-review-20260707.md`(bug 报告)互补——那份讲"哪里会坏",
本文讲"怎么写得优雅"。不重复 bug,只谈软件工程最佳实践。

**方法**:7 个工艺视角并行审查(Python 惯用法 / 抽象与 API / 重复与 DRY / 错误处理
纪律 / 资源生命周期 / 一致性约定 / 测试工艺),每个视角的事实性断言(行号、计数)
由独立 agent 复核,少数计数误差已修正。所有断言以 HEAD `7a764f8` 为准。

---

## 〇、总评

**这是一个"一人两方言"的代码库**(git:51 个 commit 里 50 个同一作者)。2026-07 世代
的代码(rm/restage/approve/cancel/clear、PG 三 store)有一套真实存在且相当好的 house
style:模块 docstring 以 `ops rm — 彻底删除一个因子` 开头、`run_<cmd>(args)` 入口、
`_verb_noun` 私有助手、banner/confirm/lock-loop 脚手架、每目录 CLAUDE.md。旧世代
(check 流水线、run、utils/、xml_prepare)则是 star import、emoji 打印、硬编码路径、
一个平行的死 Gsim runner。

**惯用法基线高于平均**:PEP 604 union 为主、领域模型全用 dataclass、pathlib 几乎
全覆盖(仅 3 个文件 9 处 `os.path`)、全库只有 1 个裸 `except:`、零可变默认参数、
513 处 f-string 且零 `%`/`.format` 混用。

**系统性短板只有一个:好方言没有任何工具在守。** `pyrightconfig.json` 躺在仓库里但
pyright 不在 dev 依赖、当前 38 个报错(其中包含真实 schema 漂移);没有 ruff/black
配置(有个游离的 `.ruff_cache` 说明手跑过但从未采纳),6 条候选规则试跑 219 个发现
(105 个可自动修复);多处"单一真相源"契约靠注释维持("三处不能 drift"、"Must match
keys used in _run_one_locked")。**增长模式是 clone-and-edit**:五个生命周期命令是
同一模板的五份手抄,四个 PG store 各自手卷相同的 pool/DDL/时间戳管道。

下面按杠杆从高到低组织成 9 条工作主线(A–I)+ 一组风格守则(J)。同一问题被多个
视角命中的只写一次。

---

## A. 工具链先行(最高杠杆,一次搞定)

**现状**:`pyrightconfig.json` 存在但无人执行 → 38 错误,其中有**真实漂移**:
`redis_store.py:41` 仍写 `rec.author`(字段已删,pyright 直接报
`Cannot access attribute "author"`)、`store/base.py:14` ABC 与 PG 实现签名不符
(reportIncompatibleMethodOverride)——这两条正是 bug 报告里"回退后端已死"的问题,
**一个被执行的类型检查器本可以在提交当天就拦住它们**。

**动作**:
1. `uv add --group dev ruff pyright`;
2. `[tool.ruff]` target-version=py310,line-length=100;`lint.select = ["F","E7","I","UP006","UP007","UP035","UP045","B006","B008"]`;
   第一阶段 `ruff check --fix`(I001+F401 共 105 个自动修复),第二阶段烧掉 F403/F405(见 B1);
3. 修完 38 个 pyright 错误后,把 `uv run ruff check ops && uv run pyright && uv run pytest -m "not slow"`
   接进 CI(bug 报告行动清单第 5 条的同一个 CI);
4. 跳过 pydocstyle/D 系规则——它们会和双语 docstring 约定打架(见 J10)。

---

## B. 消灭"靠注释维持的契约"

### B1. 31 处 star import,和一条靠加载顺序活着的注释

check.py 一个文件堆了 7 个 `import *`,并被迫写下这条注释(check.py:30-32):
> "Imported AFTER the `results.*` star imports so its Status doesn't get shadowed
> by the stub Status enum in core/alpha/results/base.py."

**import 顺序是承重结构**——这是 star import 文化的极限形态。全库 31 处(ruff F403),
82 处经由 star import 解析的名字(F405)。printer.py 没有 `__all__`,所以
`from ops.utils.printer import *` 顺带把 `shutil`、`Console`、`_console` 注进了
check.py 和 run.py 的命名空间。

**动作**:全部改显式导入(checker 们只需要 `from .base import Checker, CheckFail,
CheckSkip`;check.py 只需要 ~6 个 printer 名字);包级 `__init__.py` 用显式名 +
`__all__`;删掉 results/base.py 里空的 stub `Status` 枚举(空枚举没人能用)——
遮蔽问题随之消失,那条注释可以删了。

### B2. stage 身份在 ≥5 处各自维护 → 一张 Stage 表

现状:`STAGES` 元组(check.py:49,带注释"Must match keys used in _run_one_locked")、
`_RETRYABLE_STAGES`(:45)、`_LATE_STAGES`(藏在 on_reject 里,:264)、12 个单行异常
子类各自硬编码 stage 字符串(`ValidateFail` → `super().__init__("validate", ...)`)、
`_run_one_locked` 里六段复制的 4 行块(:364-400)。没有任何代码 catch 具体子类
(grep 证实)——12 个子类是纯开销。

**动作**(三个视角同一结论,测试视角还指出注入 seam 也会因此变干净):

```python
@dataclass(frozen=True)
class Stage:
    name: str
    prepare: Callable[[AlphaMetadata], None]
    make_checker: Callable[[Config], Checker]
    retryable: bool = False              # 取代 _RETRYABLE_STAGES
    keep_artifacts_on_fail: bool = False # 取代 _LATE_STAGES

PIPELINE: tuple[Stage, ...] = (
    Stage("validate", prepare_for_validate, ValidateChecker, retryable=True),
    Stage("checkbias", prepare_for_checkbias, CheckbiasChecker),
    ...
)
STAGES = tuple(s.name for s in PIPELINE)   # 派生,不再手抄
```

`_run_one_locked` 变成一个循环;路由读 `e.stage.retryable`;on_reject 读
`stage.keep_artifacts_on_fail`;12 个异常子类删除,直接 `raise CheckFail(stage, msg)`;
测试的 fake-checker 注入走 `{name: checker}` 字典,契约不再是"六个平行属性名"。

### B3. 时间戳:13 份拷贝 + 一次跨后端私有导入

`def _now() -> str: datetime.now().isoformat(timespec="seconds")` 在
json/pg/redis 三个 store 里逐字定义三次;同一表达式在 check.py 内联 8 次
(305/314/347/407/428/445/461/481)、submit.py:83、backfill.py:69;**approve.py:20
从 json_store 私有导入 `_now`——而生产跑的是 PG 后端**。`_ts_in`/`_ts_out` 在
state 和 snapshot 两个 pg_store 里逐字重复 26 行,snapshot 的 docstring 写着
"与 state_store 一致"——一致性靠注释。

**动作**:`ops/utils/clock.py` 提供 `now_iso()`;`ops/infra/pg.py`(见 D)收编
`ts_in`/`ts_out`,把不变量写进一处 docstring:"时间戳为 naive 本地 ISO 秒;PG 写入
时打本地 tz、读出时剥离"。bug 报告里 info store 的 8 小时偏移,根因就是这个约定
没有单一定义点,新 store 抄漏了。

---

## C. 一个批量命令骨架(收益最大的单笔重构)

restage/approve/cancel/clear 四个 `run_*` 是同一骨架的四份手抄(rm 是近亲),
757 行里约 200 行近乎逐字重复:

- `_resolve_targets` ×4:同一段 `info_store.list(author=...) 取 name 集合 →
  store.list() → 取交集 → sort` 舞步(cancel 和 approve 只差资格谓词);
- `_print_plan` ×4:同样的 N+1 `info_store.get(name)` 查 author;
- 确认块 ×4:`ans = input(f"  确认 {verb} {n} 个因子? [y/N] ")...` 6 行;
- lock 循环 ×4(approve.py:147-152 与 restage.py:214-219 逐字节相同,另两处
  近乎相同);
- 复制漂移已经发生:**restage 缺了其它三个命令都有的 name+`-u` 互斥检查**——
  clone-and-edit 的经典病征。

**动作**:`ops/services/_batch.py`,用小的可组合函数而非模板方法类:

```python
@dataclass
class BatchResult:
    done: list[str]
    skipped: list[tuple[str, str]]   # (name, reason)
    failed: list[tuple[str, str]]

def confirm_or_abort(verb: str, n: int, yes: bool) -> bool: ...

def apply_locked(targets, config, action: Callable[[T], None], *,
                 name_of=attrgetter("name"), verb: str) -> BatchResult:
    # 独占:with factor_lock(...);FactorLocked → warn+计数;
    # Exception → printer.error 且 logger.exception(强制,见 E)
```

每个命令只保留资格谓词和 `_x_one` 动作。**一石多鸟**:bug 报告里的 TOCTOU 修复
(锁内重取 + from-status CAS)只需要在这一处落地;8 个写命令从不 import loguru 的
问题(失败零诊断痕迹)在 helper 里一次解决;`run_*` 返回 `BatchResult` 后,测试
可以断言 `res.skipped == [("AlphaX", "status=active")]` 而不是"跑完后状态没变"
这种把'正确拒绝'和'静默没做'混为一谈的代理断言;exit code(见 E)也从这里出。

---

## D. 存储层:一个门面、一个池、一套约定

### D1. FactorRepository 门面

每个写命令手工构造 2-3 个 store 并手工 join;`query_factors` 是第四份手卷 join,
自带 TODO 承认该是一条 SQL。**没有任何对象拥有"一个因子"这个聚合**。

**动作**:`ops/infra/repository.py`:

```python
class FactorRepository:            # 拥有唯一的 ConnectionPool
    def get(self, name) -> FactorView | None: ...       # info+state+snapshot
    def find(self, *, author=None, status=None, field=None, ...) -> list[FactorView]:
        ...                        # 真正的三表 LEFT JOIN,退役 query_factors
    def register(self, info, record): ...               # submit 双表写,一个事务
    def transition(self, name, to, *, expect: FactorStatus | None = None): ...
    def delete(self, name) -> bool: ...                  # info 删,级联
```

三个 ABC 降级为内部 row-gateway;service 层只见 Repository。bug 报告里
"info+state 写无事务"、"list -n 下推错乱"、"批量交集逻辑"都在这一层一次修对。

### D2. 每进程一个池(注册表),DDL 滚出构造函数

四个 PG store 都在 `__init__` 里 `ConnectionPool(open=True)` + `CREATE TABLE`;
~25 个 `default_*_store()` 调用点,全库 **0 个 `.close()`**;max_size 在 4 与 10
之间随意漂移。check 的 archive 路径每因子在 fork 出的 worker 里造 3-4 个池。

```python
# ops/infra/pg.py
@lru_cache(maxsize=None)
def get_pool(conninfo: str) -> ConnectionPool: ...
# fork 守卫:os.getpid() 变了就清缓存(父进程的池在子进程不可用)

def ensure_schema(pool, ddl: str) -> None: ...   # 每 pool+ddl 只跑一次
```

`default_*_store` 工厂内部改走注册表,调用点一行不改。三个 store 因共享 conninfo
而自然共享一个池。

### D3. 返回值约定统一

同一动词三种契约:`StateStore.delete -> bool`("Returns True if it existed")、
`InfoStore.delete -> None`、`SnapshotStore.delete -> None`。**rm.py:82 已经被咬**:
`if default_info_store(config).delete(name):` 永远为假,确认信息永不打印。

**规则**:查询(`get`/`list`)返回 None/空;变更统一 `-> bool` 或抛
`ops/infra/errors.py` 里的类型化异常(`FactorNotFound`、`SnapshotAlreadyExists`)。
调用方 catch 具体异常,不再 catch Exception。

### D4. 两个同名 FactorInfo

`core/library.py:18`(扫盘产物:src_path/has_pnl/dump_days)与 `infra/info/base.py:7`
(factor_info 表行:author/discovery_method)同名不同物,并在 list.py:105-135 的
同一个函数里相遇。**把扫盘产物改名 `ScannedFactor`**,其 `author` 字段改名
`author_guess`——它来自目录名正则(library.py:37),不是权威身份,名字应当说出这一点。

---

## E. 错误处理:从"两个代码库"到一份成文政策

**诊断**:check 流水线内部的错误边界设计得**真心不错**——CheckFail/CheckSkip 携带
stage,check.py:423-488 把四种结局(skip→SUBMITTED、可重试失败→SUBMITTED、质量失败
→REJECTED、意外→logger.exception+回退)路由得清清楚楚,printer vs loguru 双通道在
log.py 里有成文分工。**边界之外全靠即兴**:63 处 `except Exception` + 1 处裸
`except:`,其中 ~27 处无日志静默吞掉(旗舰:xml_prepare 五个 prepare_* 全部
`except Exception: ...`);8 个写命令 service 从不 import loguru——而 check 的
汇总语却告诉用户"完整失败原因见 ~/.cache/ops/logs/ops.log"。异常谱系四个无关根
(CheckFail/CheckSkip、BacktestError 用 `.stage` 鸭子类型模仿而不继承、ScriptError、
FactorLocked(RuntimeError));所有 `run_*` 返回 None,`ops sync push` 打印
"✘ 失败: N" 然后 exit 0——对 cron 和脚本全盲。全库没有任何针对 PG/JFS 瞬断的重试
(只有 Redis 的 CAS 循环),一次 PG 抖动就把 30 分钟的 check 扔进 catch-all。

**成文政策**(写进根 CLAUDE.md,评审时执行):

1. **异常谱系**:`ops/errors.py` — `OpsError` 根;`UserError`(输入错/因子不存在:
   只打印消息,exit 2);`InfraError`(gsim/PG/JFS 环境:logger.exception + 一行
   消息,exit 3)。BacktestError/ScriptError 归于 InfraError,FactorLocked、
   CheckFail/CheckSkip 归于 OpsError。main() 建立唯一错误边界与 exit-code 契约;
   批量命令按 BatchResult.failed 数量决定 exit code。
2. **`except Exception` 只允许出现在三个高度**:(a) 进程边界(main);(b) 批量
   per-item 边界(C 的 helper,强制记日志);(c) 声明过的降级路径——必须
   logger.warning/exception 且在 docstring 写明降级契约("解析失败时返回 []")。
   清理/finally 路径 catch `OSError`,不 catch Exception。
   **`except Exception: pass` / `...` 一律禁止**;确属故意的抑制用
   `contextlib.suppress(OSError)` 把意图写在字面上。
3. **重试**:`ops/infra/retry.py` 一个 `retry_transient(attempts=3, on=(OperationalError,
   PoolTimeout))` 装饰器,**只**应用在 PG store 的连接咽喉点(不在调用点、不碰
   gsim 子进程——30 分钟的回测不是"瞬态")。
4. **双通道纪律**:printer 只准 ops/cli 与 ops/services 命令模块导入;ops/infra 与
   ops/core 只说异常 + loguru。现行违例:feishu_send.py(infra)import printer 且
   `sys.exit(1)` ×4;utils.Gsim 在工具类里打 "✅/❌"。
5. **删副作用 `__repr__`**:`CheckFail.__repr__` 里有一个 `print(self.args[0])`
   (checker/base.py:15)——任何 f-string/日志 repr 它都会向 stdout 喷字;
   BacktestError 的 `__repr__` 逻辑与之相反。两个都删,默认 repr 严格更好。

---

## F. 资源生命周期:把老代码已会的惯用法提取出来

**有趣的事实**:JSON 时代的代码(json_store、etag_cache、merge)是全库工艺最好的
——fcntl 守卫的读改写、tmp+fsync+os.replace、陈旧 tmp 清理,三样全对。但这些惯用法
是**按模块复制**而不是提取,于是最新的代码(PG store、report writer、XML 改写)
反而退化回裸写。

1. **`ops/utils/fs.py` — atomic_write 一处定义**:tmp+mkstemp+fsync+replace 现有
   4 份 JSON 实现(其中 derived/json_store.py:82 **悄悄丢了** 其它三份都有的 fsync)
   + pack 的 numpy 变体;~12 个裸写点(report.py、meta.json 保存、XML 保存)逐个换成
   `atomic_write_text/json/bytes`。
2. **`edit_xml` 上下文管理器**:`xmltodict.parse → 改 dict → unparse(pretty=True,
   full_document=False) → write` 在 6 个文件 8 个 unparse 点上有三种序列化实现。
   `with edit_xml(path) as cfg: cfg["gsim"]["Universe"]["@startdate"] = ...` 一统;
   xml_prepare 四个克隆 prepare_* 顺势变成"stage → XML 覆盖表"的数据(和 B2 的
   Stage 表合流),吞异常的 try 块只在一处决定一次。
3. **子进程脊柱**:runner.py 四个方法三种失败契约(run_bcorr 连 TimeoutExpired 都
   吞掉返回 None);统一为一个 `_run(cmd, timeout, ...)`:`start_new_session=True` +
   超时 `os.killpg(SIGKILL)` 收割整棵 gsim 进程树,超时/非零退出翻译成类型化
   InfraError。utils.py 里那个硬编码 `/usr/local/gsim/.venv/bin/python` 的死
   Gsim 类整个删除。
4. **进程池 worker 用模块级函数**:check.py `pool.submit(self.run_one, ...)` 每个
   任务 pickle 整个 CheckerPipeline(含全部 metadatas 和六个 checker);pack.py
   已经示范了正确写法(模块级函数 + 显式参数 + 每进程缓存的 config),照抄即可。
5. **memmap 生命周期**:pack 三处用 `del mm` 引用计数技巧;一个 `open_memmap()`
   上下文管理器收编(顺手修掉 `mms: dict[tuple[str], np.memmap]` 这个错误注解)。

---

## G. Config 治理

- **47 个公有属性**在 `__init__` 里命令式赋值(config.py:50-164),跨 7 个领域;
  死重量:`max_workers`/`thres`/`recycle`/`pnl_pool_path`/`sync_remote` 零使用。
- **它自己的调用方都不信任它**:15 处 `getattr(config, "state_backend", None)` 式
  防御访问(lock.py:103、store/__init__.py:19、sudo.py:103-109 等)——而这些属性
  **全部无条件被设置**,默认值是死代码,唯一效果是把拼写错误变成静默 None。
- **`Config.load` 有 22 个调用点**:一次 `ops health --fix` 解析 config.yaml 5+ 次
  (main→sudo ×2→scanner 重载→refresh_* 再载)。
- **魔法数没有名字**:validate 窗口 "20241201" 在两个函数里各写一遍;长回测窗口
  xml_prepare 写 20150101-20251231,cli/run.py 默认却是 20100101-20251231——两处
  各自硬编码,已经漂移。

**动作**:Config 改 `@dataclass(frozen=True)` + `from_yaml()` classmethod;按域切片
(`ComplianceCfg`、`CorrelationCfg`、`StateBackendCfg`…),构造时校验(缺阈值在
`ops` 启动时带着 yaml 路径报错——Ousterhout 的 define errors out of existence);
checker 收自己的切片而不是整个神对象;house rule:**`run_<cmd>` 做唯一一次
`Config.load`,往下全部传 `config: Config`,不传 config_path**;回测窗口进
`checker.windows` 配置块或至少一个常量头。

---

## H. CLI 一致性套件

`ops/cli/common.py`:`add_config_arg`(现状 18 处逐字复制)、`add_yes_arg`(5 处)、
`add_user_arg`(9 处,统一 LowerAction + dest='user',顺带修掉 status 命令独有的
dest='author' 和 run/pack 漏掉的小写归一)、`status_choices()`(从 FactorStatus 派生,
删掉 pack.py:28 手抄的字符串列表——它还包含 DB 已拒收的 decaying/retired)、
`add_factor_name_arg`(4 处)。LowerAction 从 utils/utils.py 搬来——**9 个 CLI 模块
为一个 3 行的 argparse Action 每次启动付一次 paramiko import**。

补上 16 个子命令里 8 个缺失的 `help=`(`ops --help` 现在一半是空白)。短旗词汇表
写进 ops/cli/CLAUDE.md:`-s`=--status、日期只用长旗 `--start/--end`、`-f`=--factor-name。

**明确不做的**(假 DRY,见 J 末尾):不要中心化 `-s`/`-f` 的声明——它们在不同命令
里语义不同,共享 helper 会把历史包袱固化成 API。

---

## I. 测试工艺:补上金字塔的中间层

现有底子好:conftest 有成文隔离模型、工厂 fixture(make_factor/make_args)、
fake-checker 是真正的 DI seam、e2e 用确定性假因子按 stage 引爆。**塌的是中间层**:

1. **info/snapshot 无 JSON 实现 → 所有 service 测试都要真 PG**。补
   `JsonInfoStore`/`JsonSnapshotStore`(镜像 JsonStateStore,复用 atomic-write;
   test-only 可免 fcntl),挂在现有 `state.backend: json` 开关后;conftest 分裂出
   `test_config_json`(纯 tmp_path,submit/cancel/restage/routing 全部脱离 PG)。
2. **PG 隔离改用 per-test schema**(现行 library_id 分区的列已被三表迁移删掉):
   `CREATE SCHEMA "t_<uuid>"` + conninfo 带 `options='-c search_path=t_<uuid>'`,
   store 的 `CREATE TABLE IF NOT EXISTS` 自动落进该 schema,teardown 一句
   `DROP SCHEMA ... CASCADE`。**零 store 代码改动**,天然并行安全。
3. **删掉两个脚本式测试文件**(test_all_services.py / test_end_to_end.py:
   print 当断言、硬编码生产 conninfo、绕过全部 fixture、无 pg marker 所以无
   auto-skip),场景改写成正经契约测试:`test_info_store_pg.py`、
   `test_snapshot_store_pg.py`(insert-only 不变性、list 下推)、
   `test_query_factors.py`、一个级联删除测试。
4. **共享构造器**:`_store(config)` 在 6 个文件里逐字定义 → fixture;
   `FactorRecord(...)` 手工构造 29 次(单文件最多 15 次)→ `make_record(name, **kw)`
   + `seed_factor(store, config, name, status, artifacts=...)`。
5. **参数化矩阵**:reject 路由只测了 compliance 和 checkbias 各一——
   `@pytest.mark.parametrize("stage", ["compliance","correlation"])`(late)与
   `["checkbias","checkpoint"]`(early)补全;cancel 的 (status, force, survives)
   五元组一张表。
6. **后端契约套件**:json 与 PG 的 state store 测试是两份手抄(delete/list_filters
   逐字节相同)→ 一个 `state_store_any` fixture 参数化两后端,同一契约跑两遍。
7. **e2e**:`_seed_pool_competitor` 是个 body 为 `pass` 的死函数,真实种池逻辑在两个
   测试里内联重复、各付一次全量回测 → session-scoped fixture 缓存竞品 pnl;
   correlation 测试的 `assert status in (REJECTED, SUBMITTED)` 二选一断言收紧为
   确定的 REJECTED。

---

## J. 风格守则(13 条,浓缩)

1. **无 star import**(B1;ruff F403/F405 守门)。
2. **ruff + pyright 进 CI**(A)。
3. **批量命令走 `_batch.py` 骨架**(C)。
4. **Config 每进程加载一次**,向下传对象不传路径(G)。
5. **CLI 声明套件 + 短旗词汇表**(H)。
6. **一个 Console、一套状态色板**:`Console(width=...)` 逐字复制 5 处;
   `_STATUS_STYLE` 定义了**两份且颜色互相矛盾**(list.py:21 SUBMITTED=yellow,
   status.py:16 SUBMITTED=green)——"REJECTED 是什么颜色"应该恰好在一个文件里有答案
   (printer.py 或 core/state.py 旁的纯数据)。
7. **一个时间真相源**(B3)。
8. **三个 store ABC 同动词同契约**(D3),PG 管道进 `infra/pg.py`(D2)。
9. **service `__init__.py` 统一一种写法**:现存四种风格(相对+__all__ / 绝对+__all__ /
   裸 star / 空文件);统一为 `from .<mod> import run_<cmd>` + `__all__`。
10. **双语 docstring 是模式不是混乱,把它写成规则**:现状 23 中文 / 17 英文 / 84 缺失,
    分裂是干净的时间分层——2026-07 世代全部以 `ops rm — 彻底删除一个因子` 式中文
    开头。规则:命令模块与 infra store 必须有模块 docstring;首行
    `ops <cmd> — <一句话语义>`;运维理由/语义/变更记录用中文(团队的细微差别在
    中文里),机械的参数/返回契约可用英文;**重构时永不把既有中文理由翻译成英文**。
    84 个缺失的补上。
11. **utils/ 不是杂物抽屉**:一个能力恰好一个家。删 `Gsim`(硬编码路径的死 runner)、
    `Remote`(stub)、`debug()`(无限循环)、空的 exception 包;LowerAction 归 CLI;
    `check_path_exists` 里的 `sys.exit` 改为抛 FileNotFoundError——工具函数报告错误,
    入口点决定进程命运。改完后 `grep -rn 'utils.utils'` 应为零。
12. **给数字起名字**:出现两次的字面量、或运维总有一天要改的字面量,必须有名字
    (G 的窗口配置)。
13. **入口点统一 `run_<subcommand>(args) -> None`**:16 个里 15 个已符合,run 子命令的
    `run_factors` 改名 `run_run`(机械性比美观值钱);8 个缺失的 `-> None` 注解补齐。

### 假重复清单(长得像、但**不要**合并)

- **derived/ 层与 snapshot 层的管道相似** → 不要为它做任何 dedup,该层整体待删
  (bug 报告 §2.1),对僵尸做重构是负价值;
- **submit 与 check 的循环**只有表面纹理相似(submit 是 dropbox 扫描+回滚语义,
  check 是进程池+路由)→ 各留各的,最多共享 confirm/printer 小件;
- **cancel 与 clear 的目标解析**:state-record 在场/缺席之分是设计本身 → 只共享
  机械脚手架,永不合并解析逻辑;
- **`-s`/`-f` 短旗**:语义随命令不同,中心化声明会固化不一致而不是消除它。

---

## 动手顺序建议

| 步 | 内容 | 工作量 | 提前解锁 |
|---|---|---|---|
| 1 | A 工具链 + B1 star import + J6/J7 小件 | 1-2 天 | 此后所有改动有守门员 |
| 2 | E 错误政策(errors.py + main 边界 + exit code) | 1 天 | 批量骨架依赖 BatchResult |
| 3 | C 批量骨架(顺手落 TOCTOU 修复) | 1-2 天 | 五个命令瘦身,测试可断言 |
| 4 | D2 池注册表 + D3 返回值约定 | 1 天 | 修池泄漏 |
| 5 | B2 Stage 表 + F2 edit_xml(check 流水线内科手术) | 2-3 天 | check.py 从 594 行瘦身 |
| 6 | I 测试中间层(Json store + schema 隔离) | 2 天 | 此后重构有安全网 |
| 7 | D1 FactorRepository(最大单件,最后动) | 3-5 天 | 收编 query_factors/事务 |
| 8 | G Config、H CLI、J 其余 | 陆续 | 机械性,随手做 |

一句话总结:**这个库不缺品味——新世代代码证明作者知道好代码长什么样;缺的是把
品味变成机器可执行的规则(工具链、共享内核、成文政策),让旧世代赶上新世代,
让下一次"迁移到一半"不再产生两种方言。**

---

*七视角多 agent 审查 + 事实复核生成;行号以 HEAD `7a764f8` 为准。与
`project-review-20260707.md`(bug 与架构)配套阅读。*
