# Infra

底层 I/O 和外部系统交互层。

## Config (`config.py`)

`Config` class loads YAML. Resolution order: `OPS_CONFIG` env var → `./config.yaml` → project root `config.yaml`.

Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables with `OPS_` prefix (e.g. `OPS_GSIM_HOME` → `gsim_home`).

Key attributes: all `path.*` fields as `Path`, `compliance`/`correlation`/`checkpoint` dicts, `library_id`。

**State backend (`state.*` in yaml)**:
- `state.backend: postgres | json`(`config.yaml` 用 postgres)
- `postgres`: `state.postgres.{host,port,dbname,user}` + password (literal / `password_env` / `password_file`+`password_key`),复用 `_build_pg_conninfo`。真相源,factor_state 表。
- `json`: 单机 dev/test 后端(~/.cache/ops/lib/<lib>/factor_state.json)。**不是生产回退。**
- redis 后端与 `config.prod-legacy.yaml` 已于 2026-07-07 (Wave 1) 删除 —— 两者自三表拆分起已不可用,是假保险(JOURNAL F1/F2)。**redis-sentinel 实例是 JFS metadata 后端,与 ops 无关,不可停。**

## Sudo Self-Elevation (`sudo.py`)

JFS 集中运维模型下 `alpha_src` / `staging` / `alpha_pnl` 等都是 root-owned,wbai 直接写会 EACCES。

`maybe_elevate(args)`:进程入口检测 `args.sub-command ∈ WRITE_COMMANDS` (submit/restage/check/**run**/rm/approve/cancel/clear/pack/backfill) **且** `alpha_src.st_uid == 0` → `os.execvp('sudo --preserve-env=OPS_* ops <argv>')` 替换自身。read-only 命令和 alpha_src 非 root-owned 环境都 no-op。

`ensure_redis_password` 钩子随 redis state 后端一并删除(2026-07-07 Wave 1)。
`maybe_elevate(args)` 在 `ops/main.py` 入口调用;`run` 已补进 WRITE_COMMANDS,sudo 只用
`--preserve-env=<白名单>`(去掉了架空白名单的 `-E`)。

## Cache (`cache.py`)

All ops state/cache files live under `~/.cache/ops/`.

- New layout: `~/.cache/ops/lib/<library_id>/<filename>`
- `cache_path(library_id, filename, legacy_hash=...)` resolves path + one-shot migrates legacy files
- `library_cache_dir(library_id)` returns the dir, ensuring it exists
- Locks at `~/.cache/ops/locks/` — fcntl, per-machine(**仅 json dev/test 后端用**;postgres 后端走跨机 PG advisory lock,见 `lock.py`)
- `cache.py` 现仅剩 json dev/test 后端的 factor_state.json + locks 用(derived.json 随僵尸层退役,2026-07-07 Wave 2)。

## Info (`info/`)

因子**身份信息**存储层(2026-07-06 从 factor_state.author 拆出)。`factor_info` 表:身份是不可变属性,与生命周期状态、入库快照三表分离。

- `base.py` — `FactorInfo` dataclass(name / author / discovery_method / created_at)+ `InfoStore` ABC(`get` / `upsert` / `delete` / `list(author=...)`)
- `pg_store.py` — `PostgresInfoStore`,`factor_info` 表(`id SERIAL` 主键,`name UNIQUE`)。**三表的根**:`factor_state` / `factor_snapshot` 的 `name` 外键都 `REFERENCES factor_info(name) ON DELETE CASCADE`,删 info 级联删另两表(`ops rm` 走这条)。
- `__init__.py` — `default_info_store(config)`(用 `config.state_postgres_conninfo`,与 state/snapshot 同库)
- 写入方:`submit`(新因子 upsert)、`backfill`(legacy 因子补 author + discovery_method)

## Snapshot (`snapshot/`)

因子**入库时快照**存储层(2026-07-06,取代 derived 层的 metrics/datasources/bcorr 三组)。`factor_snapshot` 表。

- `base.py` — `FactorSnapshot` dataclass(metrics 组 ret/shrp/mdd/tvr/fitness、datasources 组 fields/tables、`delay`(入库时 XML 解析定死,与 metrics 同性质不可变)、bcorr 组 max_bcorr/max_bcorr_factor、`snapshot_at`)+ `SnapshotStore` ABC(`get` / `insert` / `delete` / `list(field/table_glob/metrics/sort_by/limit)`)。**注**:原 index 组的 has_pnl/dump_days 已删列(可变物理事实,与快照不可变冲突;需实时状态走 `LibraryScanner` 扫盘)。
- **语义**:快照**不可变**——只有 `insert`(check 通过时一次性写)和 `delete`(`ops rm`),**没有 update**。`snapshot_at = factor_state.entered_at`。字段值是"入库时表现",非"最新表现";要最新须重跑 backtest。旧 `ops refresh` 重算路径已删除。
- `pg_store.py` — `PostgresSnapshotStore`,`factor_snapshot` 表(`id SERIAL` 主键,`name UNIQUE`,外键引 factor_info)。GIN(fields/tables) 反查、ret/shrp B-tree 索引。`list(...)` 把 field/tables/metrics/sort_by/limit 拼成 WHERE/ORDER BY/LIMIT 下推 SQL(承接原 DerivedStore.get_all 的下推语义,metric 键 SQL 表达式在 `_METRIC_EXPR`)。has_pnl/dump_days 删列后,list 因子集判据 = `factor_state.status != 'submitted'`(2026-07-07 Wave 2,纯 PG 零扫盘;见 `query.py`)。删列迁移 `scripts/postgres/migrate_drop_snapshot_index_cols.sql`。
- `__init__.py` — `default_snapshot_store(config)`(用 `config.state_postgres_conninfo`,无 JSON 回退,永远 PG)
- 写入方:`check` archive 阶段 `_persist_derived`(先 transition state 设 entered_at,再 insert snapshot)

## Query (`query.py`)

`query_factors(config, ...)` — **list 读三表的唯一入口,也是"库内因子集"的定义处**(status 缺省 = `factor_state.status != 'submitted'`,2026-07-07 Wave 2)。返回 `FactorRow = (info, status, last_fail_stage, snapshot)`。limit 不再下推(P0-5 修复,合并后由调用方截断)。

- 参数语义同原 DerivedStore.get_all(author/field/table_glob/has_index/metrics/sort_by/n)外加 `status`(state 侧过滤)。
- **当前实现**:三次独立查询(info_store.list + state_store.list + snapshot_store.list)+ 内存按 name 合并。**TODO**:优化为单条 SQL LEFT JOIN(见 `query.py` 注释)。
- 只支持 Postgres 后端(单库永远 PG);非 PG 抛 `NotImplementedError`。

## Derived — 已删除(2026-07-07 Wave 2)

`infra/derived/` 整层(base/pg_store/json_store,~700 行)随 Wave 2 退役
(JOURNAL V2):metrics/datasources/bcorr 三组 2026-07-06 已迁 `snapshot/`;
最后的 index 缓存组自迁移起就是坏的(derived_meta 丢 library_id 列,get_meta
每次 UndefinedColumn 被吞 → 每次 list 白付 ~25s 扫盘,full-review P0-4)。
生产库 `factor_derived`/`derived_meta` 两张僵尸表用
`scripts/postgres/migrate_drop_derived.sql` 手动清理。

## Lock (`lock.py`)

Per-factor advisory lock. Serializes all ops mutations on a single factor. **两个后端,按 `config.state_backend` 选**:

- **postgres**(生产):PG session-level advisory lock (`pg_try_advisory_lock(hashtext('ops:factor_lock'), hashtext(name))`),**跨机**。锁键 2026-07-07 起用固定命名空间(原 `hashtext(library_id)` 会随 config 文件不同而锁不同的锁,S18);conninfo 缺失**硬错误**,不再静默降级单机锁(F4)。用**专用连接**(非 state pool)持有整个临界区;session 级锁在**连接断开时自动释放**,无死锁残留。
- **json**(单机 dev/test):per-machine `fcntl` 文件锁 `~/.cache/ops/locks/{name}.lock`。

- Non-blocking(两后端一致):`FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name, config): ...`(config 选后端 + 提供 conninfo)

## Store (`store/`)

两个后端(postgres 生产 / json dev-test),通过 `state.backend` 切换。`default_store(config)` 根据 backend 返回对应实现。

### `pg_store.py` (default since 2026-07-04, 真相源)

`PostgresStateStore` — 因子生命周期真相源,从 Redis 迁入。`factor_state` 表 `id SERIAL` 主键 + `name UNIQUE`(2026-07-06 去掉 library_id / author / submitted_by —— 永远单库,author 移到 factor_info)。`name` 外键 `REFERENCES factor_info(name) ON DELETE CASCADE`。`FactorRecord` 现为纯状态机(status/version/时间戳/last_fail_*/check_history),不含身份字段。原子性用 PG 事务 + `SELECT ... FOR UPDATE` 行级锁替代 Redis 的 WATCH/MULTI/EXEC(transition/append_check 锁行读改写,天然串行,无应用层重试)。时间戳列 TIMESTAMPTZ,读写边界做 ISO string ↔ 本地 tz 转换(`_ts_in`/`_ts_out`,与 Redis `_now()` 格式一致 —— naive datetime 必须打本地 tz 再入库,否则 PG 当 UTC 偏 8h)。连接池/UPSERT 范式同 `snapshot/pg_store.py`。迁移工具 `ops/tools/state_to_pg.py`(旧)+ `scripts/postgres/migrate_to_snapshot.sql`(三表迁移)。

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
- Methods: `get`, `put`, `list`, `transition`, `append_check`, `delete`

## S3 — 已删除(2026-07-07 Wave 1)

`s3.py` 随 sync 栈整体退役(JOURNAL F1);boto3/tqdm 依赖同批移除。
**遗留义务:MinIO 密钥曾入库(git 历史仍在),必须轮换。**

## Gsim Runner (`gsim/runner.py`)

Static methods shell out to gsim tools via `subprocess.run`:
- `run_backtest(xml_path, config)` — runs gsim backtest, raises `BacktestError` on failure
- `run_simsummary(pnl_path, config)` → `Metrics | None`
- `run_bcorr(pnl_file, config, pools=None)` → `list[(factor, corr)] | None`;对 `pools` 里每个 pnl 目录各跑一次 bcorr 合并结果,缺省 `pools=[pnl_prod_path, pnl_alphalib]`(全库)。`resolve_bcorr_pools(config, discovery_method)` 按因子来源返回同类池(automated/manual 各比各的,legacy 回退全库)。

Configurable timeout from `config.timeout`.
