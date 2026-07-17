# Infra

底层 I/O 和外部系统交互层。

## Config (`config.py`)

`Config` class loads YAML. Resolution order: `OPS_CONFIG` env var → `./config.yaml` → project root `config.yaml`.
**OPS_CONFIG 一旦设置就是唯一候选**——指向不存在的文件不回落(typo 静默换 config
比崩溃可怕),由 `Config.load` 响亮退出(SystemExit + 可行动修法;uv tool install
从任意 cwd 跑、三级解析全落空同此,不再裸 FileNotFoundError)。`get_default_config_path`
在 parser 注册期被调用,永不退出/抛错(`ops --help` 不能炸)。
测试 `tests/test_config_resolution.py`。

Supports `${var_name}` variable substitution from the `vars:` block in YAML. 变量优先级(2026-07-11 hosts 声明,ops setup 配套):**OPS_* 环境变量 > `hosts:[本机 hostname]` > vars 基础值** —— 每机挂载点差异进 hosts 块按 hostname 精确匹配,同一份 config 四机零环境变量可用;命中情况回填 `config.hostname` / `config.host_declared` 供 `ops setup` 报告。

Key attributes: all `path.*` fields as `Path`, `compliance`/`correlation`/`checkpoint` dicts, `library_id`。

**State backend (`state.*` in yaml)**:
- `state.backend: postgres | json`(`config.yaml` 用 postgres)
- `postgres`: `state.postgres.{host,port,dbname,user}` + password (literal / `password_env` / `password_file`+`password_key`),复用 `_build_pg_conninfo`。真相源,factor_state 表。
- `json`: 单机 dev/test 后端(~/.cache/ops/lib/<lib>/factor_state.json)。**不是生产回退。**
- redis 后端与 `config.prod-legacy.yaml` 已于 2026-07-07 (Wave 1) 删除 —— 两者自三表拆分起已不可用,是假保险(JOURNAL F1/F2)。**redis-sentinel 实例是 JFS metadata 后端,与 ops 无关,不可停。**
- **仅测试的两个键**(I2,2026-07-11;生产 config 绝不设置):
  `state.postgres.options` → conninfo 透传 libpq 选项(测试注入
  `-csearch_path=<schema>` 做 per-session schema 隔离,值不能含空格);
  `state.lock_namespace` → advisory lock 命名空间覆盖(见 lock.py)。

## Sudo Self-Elevation (`sudo.py`)

JFS 集中运维模型下 `alpha_src` / `staging` / `alpha_pnl` 等都是 root-owned,wbai 直接写会 EACCES。

`maybe_elevate(args)`:进程入口检测 `args.is_write_command` **且** `alpha_src.st_uid == 0` → `os.execvp('sudo --preserve-env=OPS_* ops <argv>')` 替换自身。read-only 命令和 alpha_src 非 root-owned 环境都 no-op。

**写命令集由 cli 注册处 `mark_write(parser)` 声明派生**(S16,2026-07-10;
`ops/cli/common.py`,`set_defaults(is_write_command=True)`):原 `WRITE_COMMANDS`
手抄集合是多真相源(`run` 曾缺席 → JFS 下非 root 直接 EACCES,full-review 1.2),
已删除。当前 10 个写命令声明:submit/restage/check/run/rm/approve/cancel/clear/
pack/setup(backfill 2026-07-13 随命令退役出列);声明集钉在
`tests/test_pure.py::test_write_command_declarations_match_registry`。

`ensure_redis_password` 钩子随 redis state 后端一并删除(2026-07-07 Wave 1)。
`maybe_elevate(args)` 在 `ops/main.py` 入口调用;sudo 只用
`--preserve-env=<白名单>`(去掉了架空白名单的 `-E`)。

## Cache (`cache.py`)

All ops state/cache files live under `~/.cache/ops/`.

- New layout: `~/.cache/ops/lib/<library_id>/<filename>`
- `cache_path(library_id, filename, legacy_hash=...)` resolves path + one-shot migrates legacy files
- `library_cache_dir(library_id)` returns the dir, ensuring it exists
- Locks at `~/.cache/ops/locks/` — fcntl, per-machine(**仅 json dev/test 后端用**;postgres 后端走跨机 PG advisory lock,见 `lock.py`)
- `cache.py` 现仅剩 json dev/test 后端的 factor_state.json + locks 用(derived.json 随僵尸层退役,2026-07-07 Wave 2)。
- **`CACHE_ROOT` 常量的正主在 `ops/utils/cachedir.py`**(2026-07-09 迁出:utils.log 也要用它,原先 utils→infra 反向依赖违反分层,import-linter C1/C5);cache.py re-export 保住旧导入路径。

## Errors (`errors.py`)

infra 层类型化异常集中地(full-review D3,2026-07-09):`StateConflict`(自
store/base 迁居,原处 re-export 保兼容)+ `FactorNotFound`(KeyError 子类 ——
transition/append_check 原先抛裸 KeyError,存量 `except KeyError` 继续有效)。
约定:变更操作返回 bool(delete = "存在且已删",State/Info/Snapshot 三家已对齐)
或抛这里的类型化异常;只定义有 raise 方的异常,不预建空壳。

## PG Pool Registry (`pg.py`,full-review D2)

**唯一建 PG 池的地方**。三个 PG store(state/info/snapshot)`__init__` 里
`self.pool = get_pool(conninfo)` + `ensure_schema(self.pool, _SCHEMA)`,不再各自
`ConnectionPool(...)`。合治两个故障:

- **连接打爆**(生产 P0):原先 `default_*_store()` 每调一次新建一个池(min_size=1
  立刻占 1 连接),`ops check` 在 `run_one` 里**每因子**建 state/info/snapshot 三池,
  一个 worker 处理 K 因子攒 3K 池、3K 连接到进程退出才放,20 worker 秒破 PG 默认
  `max_connections=100` → `FATAL: too many clients already`。`get_pool(conninfo)` 按
  `(pid, conninfo)` 缓存,同进程**同 conninfo 只一个池**;三表同库同 conninfo → 塌成
  一个共享池,worker 连接占用 3K→1。`ensure_schema` 保证每 `(池, DDL)` 只建表一次。
- **退出刷屏**:`ConnectionPool(open=True)` 与 worker 线程成环 → 池活到解释器关闭 →
  `__del__` join 线程抛 `cannot join current thread`(无害但每池刷一次)。`get_pool`
  登记池,`atexit` 退出前显式 `close()`,此后 `__del__` 空转。

**fork 安全**:缓存键带 pid,子进程 pid 不同 → 自建自己的池;`register_at_fork`
在子进程清空缓存/登记表(丢弃继承自父进程、worker 线程已不存活的池对象);`atexit`
只关本进程建的池(`ops check` worker 各自建、各自关)。行为测试
`tests/test_pg_pool_cleanup.py`(假池,无需 PG:去重 / ensure_schema 一次 / pid 过滤 /
fork 重置)。~~DDL 彻底滚出 store `__init__`~~(2026-07-09 阶段 2 完成,见下方
Schema 节);另收编 `ts_in`/`ts_out`(ISO string ↔ TIMESTAMPTZ 边界转换,原
store/snapshot 两个 pg_store 各自镜像)。**剩余**:`max_size` 参数化。另有 `probe(conninfo, statements=, timeout=5)`
(2026-07-11):诊断用有界直连,不走池注册表(ops setup 的 PG/锁检查用 ——
池的重连重试会让"不可达"挂起半分钟,诊断必须秒级失败)。

## Schema (`schema.py`)

三表 DDL 的唯一代码入口(2026-07-09 阶段 2,DDL 滚出 store `__init__`)。
`ensure_schemas(conninfo)` 按 **FK 依赖序**(info → state → snapshot)幂等引导
——原先靠"恰好 info store 先被构造"维持,空库上先建 factor_state 直接
UndefinedTable。store 构造现在零副作用;生产 schema 的真正 owner 是
`scripts/postgres` 迁移脚本。调用方:`FactorRepository` 首次触达 PG 时懒调用、
tests/conftest.py 的 `pg_conninfo` fixture 显式调用。**注意**:不经 Repository
直用 `default_*_store` 的路径在全新空库上不再自动建表。

## Repository (`repository.py`)

`FactorRepository` —— **service 层读写因子的唯一门面**(factor-aggregate-plan
阶段 2,full-review D1)。构造便宜(store 懒加载),`FactorRepository(config)`
即用。check 的 fork worker **禁止**共享父进程实例(懒加载 store 捏着父进程的
PG 池引用),按需现构造(`get_pool` 按 pid 去重,见 check.py `_repo()`)。

**记录面**:`get(name) -> Factor | None`(存在性语义 = factor_info 有行)/
`find(...)` —— **单条三表 LEFT JOIN**,list 的联合读唯一入口 + "库内因子集"
定义处(status 缺省 = `!= 'submitted'`;`include_submitted=True` 且 status 未给
时返回全状态 —— status/cancel/pack 批量 resolve 的"任何记录"语义,2026-07-09
阶段 3;snapshot 下推经 `snapshot_where`/
`metric_order_expr` 与单表 list 共享;返回的 Factor.state 不含 check_history;
limit 仅显式给定时下推)/ `register(identity, ...)` —— **info+state 单事务
原子写**(store 的 `upsert_on`/`put_on` 静态方法组合;submit/
check._ensure_record 的唯一双表写入口,原第三方 backfill 2026-07-13 退役)/
`record`/`transition`(CAS 透传)/
`append_check` / `attach_snapshot(snapshot, measured_at)`(v3:snapshot_at = 测得时刻,新测量替换)/ `discard_snapshot`(离库快照失效)/ `delete`(info 级联)/ `exists` /
`lock`(factor_lock 门面)。

**产物面**:`paths(name) -> FactorPaths`;`purge_artifacts(name, scope)` ——
`ArtifactScope.CHECK`(pnl + bcorr 池副本,离库一律回收,防自鬼影 PV7)/
`ArtifactScope.SERVING`(dump + feature,last-known-good,--purge/REJECTED 才清)。
收编原 services/rm 的 `_purge_artifacts`/`_recycle_check_artifacts` 跨包 helper。
搬运三件套(2026-07-10 阶段 3 第二批):`archive(name, *, src_dir, dump_dir,
pnl_file, discovery_method)` —— 归档入库,收编原 check.to_lib 全部搬运
(clean_pycache + src→alpha_src + @module 重指 + dump/pnl 搬库 + 按来源分流
池副本 + 身份兜底断言,第一道闸仍在 check.run_one 入口)/ `recall(name)` ——
alpha_src→staging,收编 restage 搬运半边(存在性/占用校验 + clean_pycache +
move + @module 重指;**move 不是 copy**,召回后 staging 是唯一副本)/
`unstage(name) -> bool` —— 删 staging 目录(cancel/clear/rm 三处 rmtree 收编,
True = 存在且已删)。

**json dev/test 后端降级语义**:register 只写 state、get 合成仅含 name 的
identity、find 抛 NotImplementedError、discard_snapshot no-op —— 控制流测试
无需 PG。测试 `tests/test_repository.py`(json 组 CI 常跑 + PG 组)。

## Info (`info/`)

因子**身份信息**存储层(2026-07-06 从 factor_state.author 拆出)。`factor_info` 表:身份是不可变属性,与生命周期状态、入库快照三表分离。

- `base.py` — `FactorInfo` dataclass(name / author / discovery_method / created_at)+ `InfoStore` ABC(`get` / `upsert` / `delete` / `list(author=...)`)
- `pg_store.py` — `PostgresInfoStore`,`factor_info` 表(`id SERIAL` 主键,`name UNIQUE`)。**三表的根**:`factor_state` / `factor_snapshot` 的 `name` 外键都 `REFERENCES factor_info(name) ON DELETE CASCADE`,删 info 级联删另两表(`ops rm` 走这条)。
- `__init__.py` — `default_info_store(config)`(用 `config.state_postgres_conninfo`,与 state/snapshot 同库)
- 写入方:`submit`(新因子 upsert;原 `backfill` 补录通道 2026-07-13 退役)

## Snapshot (`snapshot/`)

因子**测得快照**存储层(v3 2026-07-13 语义变更:最近一次 check 测得的表现,被拒也写 —— v2b 审计表卸掉了快照的"入库见证"兼职后解锁;2026-07-06 曾为"入库时快照")。`factor_snapshot` 表。

- `base.py` — `FactorSnapshot` dataclass(metrics 组 ret/shrp/mdd/tvr/fitness、datasources 组 fields/tables、`delay`(入库时 XML 解析定死,与 metrics 同性质不可变)、bcorr 组 max_bcorr/max_bcorr_factor、`snapshot_at`)+ `SnapshotStore` ABC(`get` / `insert` / `delete` / `list(field/table_glob/metrics/sort_by/limit)`)。**注**:原 index 组的 has_pnl/dump_days 已删列(可变物理事实,与快照不可变冲突;需实时状态走 `LibraryScanner` 扫盘)。
- **语义(v3)**:snapshot_at = 测得时刻(该次 check 事件的 at);pass 与 correlation/compliance 失败都写,新测量原子替换(delete+insert),每行不可变;仍只由 check 写,永无离线重算(`ops refresh` 已删)。写快照 ≠ 入库。doctor 对账判据 = snapshot_at ⇔ 最近 check 事件(legacy 无事件锚 entered_at)。
- `pg_store.py` — `PostgresSnapshotStore`,`factor_snapshot` 表(`id SERIAL` 主键,`name UNIQUE`,外键引 factor_info)。fields/tables 是 **TEXT[]**(v2b 2026-07-12,原 JSONB 是 derived 层搬来的偷懒类型;psycopg 原生 list 适配,包含查询 `@>` 吃 GIN(array_ops),glob 经 unnest+LIKE)。GIN(fields/tables) 反查、ret/shrp B-tree 索引。`list(...)` 把 field/tables/metrics/sort_by/limit 拼成 WHERE/ORDER BY/LIMIT 下推 SQL(承接原 DerivedStore.get_all 的下推语义;metric 键 SQL 表达式自 `ops/core/metrics.py::SNAPSHOT_METRICS` 注册表派生,2026-07-11 S8 收敛,原 `_METRIC_EXPR` 手抄映射删除)。has_pnl/dump_days 已删(代码侧 2026-07-06;生产删列 2026-07-12 v2a 补执行 —— 迁移脚本写了拖了六天没跑,用户查活表发现,教训见 scripts/postgres/README.md 迁移台账);list 因子集判据 = `factor_state.status != 'submitted'`(2026-07-07 Wave 2,纯 PG 零扫盘;见 `query.py`)。删列迁移 `scripts/postgres/migrate_drop_snapshot_index_cols.sql`。
- `__init__.py` — `default_snapshot_store(config)`(用 `config.state_postgres_conninfo`,无 JSON 回退,永远 PG)
- 写入方:`check` 的 `_persist_derived`(pass → archive 段;correlation/compliance 失败 → reject 分支,v3)

## Query — 已删除(2026-07-09 阶段 2)

`query.py`(query_factors + FactorRow,三次查 + 内存合并)由
`repository.py::FactorRepository.find`(单条三表 LEFT JOIN)取代,物理删除。
"库内因子集"定义随迁 find(status 缺省 = `!= 'submitted'` 不变)。

## Derived — 已删除(2026-07-07 Wave 2)

`infra/derived/` 整层(base/pg_store/json_store,~700 行)随 Wave 2 退役
(JOURNAL V2):metrics/datasources/bcorr 三组 2026-07-06 已迁 `snapshot/`;
最后的 index 缓存组自迁移起就是坏的(derived_meta 丢 library_id 列,get_meta
每次 UndefinedColumn 被吞 → 每次 list 白付 ~25s 扫盘,full-review P0-4)。
生产库 `factor_derived`/`derived_meta` 两张僵尸表用
`scripts/postgres/migrate_drop_derived.sql` 手动清理。

## Lock (`lock.py`)

Per-factor advisory lock. Serializes all ops mutations on a single factor. **两个后端,按 `config.state_backend` 选**:

- **postgres**(生产):PG session-level advisory lock (`pg_try_advisory_lock(hashtext('ops:factor_lock'), hashtext(name))`),**跨机**。锁键 2026-07-07 起用固定命名空间(原 `hashtext(library_id)` 会随 config 文件不同而锁不同的锁,S18);conninfo 缺失**硬错误**,不再静默降级单机锁(F4)。用**专用连接**(非 state pool)持有整个临界区;session 级锁在**连接断开时自动释放**,无死锁残留。命名空间有一个**仅测试**的注入口 `config.lock_namespace`(I2,2026-07-11:advisory lock 是库级作用域,per-session schema 隔离挡不住,并行 pytest 须各锁各的命名空间;生产一律走固定缺省 —— S18 的教训就是锁键漂移)。
- **json**(单机 dev/test):per-machine `fcntl` 文件锁 `~/.cache/ops/locks/{name}.lock`。

- Non-blocking(两后端一致):`FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name, config): ...`(config 选后端 + 提供 conninfo)

## Store (`store/`)

两个后端(postgres 生产 / json dev-test),通过 `state.backend` 切换。`default_store(config)` 根据 backend 返回对应实现。

### `pg_store.py` (default since 2026-07-04, 真相源)

`PostgresStateStore` — 因子生命周期真相源,从 Redis 迁入。`factor_state` 表 `id SERIAL` 主键 + `name UNIQUE`(2026-07-06 去掉 library_id / author / submitted_by —— 永远单库,author 移到 factor_info)。`name` 外键 `REFERENCES factor_info(name) ON DELETE CASCADE`。`FactorRecord` 现为纯状态机(status/version/submitted_at/entered_at/updated_at;**v2b 2026-07-12:rejected_at/last_fail_stage/last_fail_reason 三列 + check_history JSONB 退役**,事实迁 `factor_history` 全操作审计表 —— 同模块持有其 DDL 与唯一发射口 `emit_on`)。**factor_history**:一次操作一条记录(op ∈ submit/overwrite/check/approve/restage/cancel/rm/backfill/entered,`HISTORY_OPS` 与 DB chk_op 同一提交改),刻意无 FK(**历史活过 ops rm**),actor 经 `ops/utils/actor.py::current_actor`(SUDO_USER 优先)。发射与业务写**同事务**:transition 的 `op:` 参数 + 置 ACTIVE 自动发 'entered'(三径合流)、append_check = op='check' 事件、repo.delete/register 在各自事务内 emit。读侧:`get()` 的 check_history 从事件表组装(内存形态保留);`last_fail(name)` = 最新 passed=FALSE 的 check 事件(原三列的派生替身);`history(name)` = 完整时间线。v2c(遗留项④):check 全史自 FactorRecord 剥离,按需 `checks(name)`(PG 从事件表组装 / json 读记录侧原始列表);json 后端 op/actor 忽略、last_fail 扫描合成、history 合成 check 事件(status 时间线两后端统一)。原子性用 PG 事务 + `SELECT ... FOR UPDATE` 行级锁替代 Redis 的 WATCH/MULTI/EXEC(transition/append_check 锁行读改写,天然串行,无应用层重试)。时间戳列 TIMESTAMPTZ,读写边界做 ISO string ↔ 本地 tz 转换(`_ts_in`/`_ts_out`,与 Redis `_now()` 格式一致 —— naive datetime 必须打本地 tz 再入库,否则 PG 当 UTC 偏 8h)。连接池/UPSERT 范式同 `snapshot/pg_store.py`。迁移工具 `ops/tools/state_to_pg.py`(旧)+ `scripts/postgres/migrate_to_snapshot.sql`(三表迁移)。

### `redis_store.py` — 已删除(2026-07-07 Wave 1)

state 2026-07-04 迁 PG 后 redis 仅名义回退;三表拆分后它读写已删字段,每次 put 必炸,
作为回退是假保险,连同 `ensure_redis_password`、redis 依赖一并删除(JOURNAL F2)。
**redis-sentinel 实例本身是 JFS metadata 后端,不可停 —— 删的只是 ops 侧代码。**

### `json_store.py` (单机 dev/test 后端)

`JsonStateStore` — JSON-backed state persistence with fcntl cross-process locking。
测试套件的无 PG 层用它;**不是生产回退**。

- Single fcntl lock over the full read-modify-write window
- Atomic write via tempfile + `os.replace`
- Stale `.tmp` cleanup (> 1h) on lock acquisition
- Methods: `get`, `put`, `list`, `transition`, `append_check`, `delete`, `checks`, `last_fail`, `history`(后三个 v2b/v2c 派生读;check 史存记录侧 raw dict,由 store 管理 —— FactorRecord 已无该字段,写回须从 raw 保留)

## S3 — 已删除(2026-07-07 Wave 1)

`s3.py` 随 sync 栈整体退役(JOURNAL F1);boto3/tqdm 依赖同批移除。
**遗留义务:MinIO 密钥曾入库(git 历史仍在),必须轮换。**

## Gsim Runner (`gsim/runner.py`)

Static methods shell out to gsim tools via `subprocess.run`:
- `run_backtest(xml_path, config)` — runs gsim backtest, raises `BacktestError` on failure
- `run_simsummary(pnl_path, config)` → `Metrics | None`
- `run_bcorr(pnl_file, config, pools=None)` → `list[(factor, corr)] | None`;对 `pools` 里每个 pnl 目录各跑一次 bcorr 合并结果,缺省 `pools=[pnl_prod_path, pnl_alphalib]`(全库)。`resolve_bcorr_pools(config, discovery_method)` 按因子来源返回同类池(automated/manual 各比各的;来源未知回退全库 —— 2026-07-13 discovery_method NOT NULL 后 check 路径恒有值,此支降为防御兜底)。

Configurable timeout from `config.timeout`.
