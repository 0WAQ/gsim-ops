# Infra

底层 I/O 和外部系统交互层。

## Config (`config.py`)

`Config` class loads YAML. Resolution order: `OPS_CONFIG` env var → `./config.yaml` → project root `config.yaml`.

Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables with `OPS_` prefix (e.g. `OPS_GSIM_HOME` → `gsim_home`).

Key attributes: all `path.*` fields as `Path`, `compliance`/`correlation`/`checkpoint` dicts, `sync_remote`, `library_id`, S3 credentials.

**State backend (`state.*` in yaml)**:
- `state.backend: json | redis | postgres` (default `json`;`config.yaml` 2026-07-04 起用 `postgres`)
- For `postgres`: `state.postgres.{host,port,dbname,user}` + password (literal / `password_env` / `password_file`+`password_key`),复用 `_build_pg_conninfo`。真相源,factor_state 表。
- For `redis`(回退): `state.redis.url` + password 三层 fallback。**注意 redis 仍是 JFS metadata 后端,不可停**。
- 三层 fallback 顺序见 `config.py` 注释。`config.prod-legacy.yaml` 用 json,`config.yaml` 用 postgres(state)+ postgres(derived)。

`config.prod-legacy.yaml` 是上线前的 prod (S3 sync 模型),保留作紧急回退。

## Sudo Self-Elevation (`sudo.py`)

JFS 集中运维模型下 `alpha_src` / `staging` / `alpha_pnl` 等都是 root-owned,wbai 直接写会 EACCES。

`maybe_elevate(args)`:进程入口检测 `args.sub-command ∈ WRITE_COMMANDS` (submit/restage/check/rm/approve/cancel/clear/pack/backfill) **且** `alpha_src.st_uid == 0` → `os.execvp('sudo -E --preserve-env=OPS_* ops <argv>')` 替换自身。read-only 命令和 legacy prod (alpha_src wbai-owned) 都 no-op。

`ensure_redis_password(args)`:wbai shell 没 `OPS_STATE_REDIS_PASSWORD` env 时,从 `config.state.redis.password_file` (默认 `/etc/juicefs/alphalib-jfs.env`) 通过 `sudo grep` 一次拿密码塞进 env,后续 `maybe_elevate` 的 sudo `--preserve-env` 透传到 root 子进程。**故意不 `os.path.exists` 检查** password_file:`/etc/juicefs/` 是 `0700 root:root`,wbai stat 不到,但 sudo 跑成 root 能读。

两者都在 `ops/main.py` 入口调,顺序:`ensure_redis_password(args)` → `maybe_elevate(args)` → `args.func(args)`。

## Cache (`cache.py`)

All ops state/cache files live under `~/.cache/ops/`.

- New layout: `~/.cache/ops/lib/<library_id>/<filename>`
- `cache_path(library_id, filename, legacy_hash=...)` resolves path + one-shot migrates legacy files
- `library_cache_dir(library_id)` returns the dir, ensuring it exists
- Locks at `~/.cache/ops/locks/` — fcntl, per-machine(**仅 json/redis 回退后端用**;postgres 后端走跨机 PG advisory lock,见 `lock.py`)
- Index/metrics/datasources/bcorr **已迁 Postgres**(2026-07-04 迁 derived,2026-07-06 metrics/datasources/bcorr 再迁 `factor_snapshot`)。`cache.py` 现仅剩 json 回退后端 + `derived.json`(僵尸 index 缓存)+ locks 用;PG 后端下不再写这些缓存。

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
- `pg_store.py` — `PostgresSnapshotStore`,`factor_snapshot` 表(`id SERIAL` 主键,`name UNIQUE`,外键引 factor_info)。GIN(fields/tables) 反查、ret/shrp B-tree 索引。`list(...)` 把 field/tables/metrics/sort_by/limit 拼成 WHERE/ORDER BY/LIMIT 下推 SQL(承接原 DerivedStore.get_all 的下推语义,metric 键 SQL 表达式在 `_METRIC_EXPR`)。has_pnl/dump_days 删列后,list 因子集改由 `LibraryScanner.scan()` 扫盘白名单界定(见 `services/list/`)。删列迁移 `scripts/postgres/migrate_drop_snapshot_index_cols.sql`。
- `__init__.py` — `default_snapshot_store(config)`(用 `config.state_postgres_conninfo`,无 JSON 回退,永远 PG)
- 写入方:`check` archive 阶段 `_persist_derived`(先 transition state 设 entered_at,再 insert snapshot)

## Query (`query.py`)

`query_factors(config, ...)` — **list / health 读三表的唯一入口**。返回 `FactorRow = (info, status, last_fail_stage, snapshot)`。

- 参数语义同原 DerivedStore.get_all(author/field/table_glob/has_index/metrics/sort_by/n)外加 `status`(state 侧过滤)。
- **当前实现**:三次独立查询(info_store.list + state_store.list + snapshot_store.list)+ 内存按 name 合并。**TODO**:优化为单条 SQL LEFT JOIN(见 `query.py` 注释)。
- 只支持 Postgres 后端(单库永远 PG);非 PG 抛 `NotImplementedError`。

## Derived (`derived/`) — 僵尸层,待清理

**过渡状态(2026-07-06)**:metrics/datasources/bcorr 三组已迁 `snapshot/`,本层**只剩 index 组仍被 `LibraryScanner` 用作跨机 index 缓存**(`derived.json` / `factor_derived` 的 index 组 + `index_built_at` 水位)。代码尚未删除,是待清理僵尸层。下方描述保留原样供参考,但读侧 metrics/datasources/bcorr 已不走这里,而走 `query.py` / `snapshot/`。

## Derived 内部(历史,index 缓存仍用)

派生层 (index/metrics/datasources/bcorr) 的存储抽象,替代原 per-machine
`~/.cache/ops/lib/<lib>/*.json`。三机共享 + 查询不扫盘。范式与 `store/` 一致。

- `base.py` — `DerivedStore` ABC + `DerivedRecord` dataclass(一个因子的四组派生数据合一,扁平字段)。方法:`get_all(...)` / `get(name)` / `upsert_index/metrics/datasources/bcorr` / `get_meta/set_meta` / `delete`。`get_all` 除 `author` 外带一组关键字下推参:`field` / `table_glob`(datasource 反查)、`has_index`(只留 author 非空即在 alpha_src)、`metrics`([(key,op,threshold)])、`sort_by`、`limit`,全部有向后兼容默认值,纯预筛(上层仍全量兜底,结果逐位等价)。另导出数值键取值/排序的**单一 Python 真相源** `metric_get(rec,key)` / `sort_key(rec,key)` + `_SORTABLE_KEYS`,供 list.py 内存兜底 + json 后端复用;pg 后端 SQL 表达式须逐键镜像,三处不能 drift
- `pg_store.py` — `PostgresDerivedStore`,单张 `factor_derived` 宽表 (library_id, name) 主键,四组独立 UPSERT,GIN(fields/tables) 反查索引;`derived_meta` 表存 `index_built_at` 水位;psycopg3 连接池。`get_all` 把 `has_index`/`field`/`tables`/`metrics`/`sort_by`/`limit` 拼成 WHERE/ORDER BY/LIMIT 下推 SQL,metric 键的 SQL 表达式在 `_METRIC_EXPR`(镜像 base.metric_get,如 `bcorr → abs(max_bcorr)`)、op 白名单 `_SQL_OPS`。派生层谓词拆到 `_derived_where(prefix=...)`,`get_all` 与 `join_state` 共用(单一真相源);`_metric_expr(prefix)` 同源生成无别名 (`_METRIC_EXPR`) 与 `d.` 别名 (`_METRIC_EXPR_D`) 两版
- `join_state(...)` — 派生层 `LEFT JOIN factor_state`(同库同 library_id),一次查回 `(DerivedRecord, status, last_fail_stage)`,`--status` 精确下推 `s.status = %s`。**本 store 唯一跨表处**:明知 factor_state 的 schema,换 list 热路径一次 JOIN。仅 pg 后端可用(json 两个独立文件 JOIN 不成立)
- `json_store.py` — `JsonDerivedStore`,单文件 `derived.json`,fcntl 锁 + 原子写,回退用;`get_all` 的下推参在内存里镜像同语义(复用 `base.metric_get`/`sort_key`,`_passes_metrics` + `_OP_FUNCS`)。无 `join_state`(回退走两次读)
- `__init__.py` — `default_derived_store(config)` 按 `config.derived_backend` 分发 (json 默认 / postgres)
- **联合读 (`../query.py`) — ⚠ 以下为迁移前历史,已不成立**:旧 `query_factors` 曾返回 `FactorRow = (DerivedRecord, status, last_fail_stage)`,同库 postgres 走 `join_state` 一条 JOIN。**三表重构后 (2026-07-06) query.py 改读 info+state+snapshot 三表,返回 `FactorRow = (info, status, last_fail_stage, snapshot)`,当前是三次查 + 内存合并 (无 join_state)。当前行为见上文 Query 节 / `core/CLAUDE.md`。** `health` 不走 `query_factors`(直接 `snapshot_store.list`)
- **读写分离**:读侧 (list/info/health) 直接消费 `DerivedRecord`;写侧 `refresh_*`(services/list/)收 names 生产派生数据。index 由 `LibraryScanner.scan()` 扫盘后 publish,新鲜度靠 alpha_src mtime vs `index_built_at` 水位跨机判定
- 迁移工具 `ops/tools/derived_migrate.py`;部署 `scripts/postgres/README.md`

## Lock (`lock.py`)

Per-factor advisory lock. Serializes all ops mutations on a single factor. **两个后端,按 `config.state_backend` 选**:

- **postgres**(生产):PG session-level advisory lock (`pg_try_advisory_lock(hashtext(lib), hashtext(name))`),**跨机**。state 在共享 PG、staging 在共享 JFS,三机都能 `ops check` 同一 staging —— per-machine 文件锁挡不住跨机并发,PG advisory lock 能(PG 是三机唯一强一致存储)。用**专用连接**(非 state pool)持有整个临界区;session 级锁在**连接断开时自动释放**(机器崩溃/SIGKILL/断电),无死锁残留。
- **json / redis / 无 conninfo**:回退 per-machine `fcntl` 文件锁 `~/.cache/ops/locks/{name}.lock`(单机 json 正确;redis-state 退役中,本就 fcntl)。

- Non-blocking(两后端一致):`FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name, config): ...`(config 选后端 + 提供 conninfo)

## Store (`store/`)

三个后端,通过 `state.backend` 切换。`default_store(config)` 根据 backend 返回对应实现。

### `pg_store.py` (default since 2026-07-04, 真相源)

`PostgresStateStore` — 因子生命周期真相源,从 Redis 迁入。`factor_state` 表 `id SERIAL` 主键 + `name UNIQUE`(2026-07-06 去掉 library_id / author / submitted_by —— 永远单库,author 移到 factor_info)。`name` 外键 `REFERENCES factor_info(name) ON DELETE CASCADE`。`FactorRecord` 现为纯状态机(status/version/时间戳/last_fail_*/check_history),不含身份字段。原子性用 PG 事务 + `SELECT ... FOR UPDATE` 行级锁替代 Redis 的 WATCH/MULTI/EXEC(transition/append_check 锁行读改写,天然串行,无应用层重试)。时间戳列 TIMESTAMPTZ,读写边界做 ISO string ↔ 本地 tz 转换(`_ts_in`/`_ts_out`,与 Redis `_now()` 格式一致 —— naive datetime 必须打本地 tz 再入库,否则 PG 当 UTC 偏 8h)。连接池/UPSERT 范式同 `snapshot/pg_store.py`。迁移工具 `ops/tools/state_to_pg.py`(旧)+ `scripts/postgres/migrate_to_snapshot.sql`(三表迁移)。

### `redis_store.py` (回退, 且仍是 JFS metadata 后端)

`RedisStateStore` — state 2026-07-04 已迁 PG,此后 redis 仅作 state 回退。**但承载它的 Redis 实例 (`...:26380/mymaster/0` sentinel) 同时是 JuiceFS `/tank/vault/alphalib` 的 metadata 后端,进程不可停**。两种 URL scheme:

- `redis://host:port/db` — 直连单实例(legacy / 单机部署)
- `redis-sentinel://h1:p1,h2:p2,h3:p3/service_name/db` — Sentinel-aware,自动 failover

Sentinel 路径构造 `redis.sentinel.Sentinel(...).master_for(service, ...)`,每次 connection 重新 resolve master。`protocol=2` 强制经典 AUTH(redis-py 8.x `from_url` 不 honor protocol=2,直接用 `redis.Redis()` ctor 避雷,见 commit fc4d8f8)。

Schema:`state-meta:<lib>` / `state-index:<lib>` set / `state:<lib>:<name>` hash / `state-checks:<lib>:<name>` list。WATCH/MULTI/EXEC 做 read-modify-write,append_check 原子。

### `json_store.py` (legacy fallback)

`JsonStateStore` — JSON-backed state persistence with fcntl cross-process locking. 用于 `config.prod-legacy.yaml`(回退路径)。

- Single fcntl lock over the full read-modify-write window
- Atomic write via tempfile + `os.replace`
- Stale `.tmp` cleanup (> 1h) on lock acquisition
- Methods: `get`, `list`, `upsert`, `transition`, `bulk_upsert`

## S3 (`s3.py`)

Thin boto3 wrapper for `ops sync`. **JFS 上线后 (`config.yaml`) 不走 S3**,仅在 `config.prod-legacy.yaml` 路径下使用,后续整体退役。

`S3Client(endpoint_url, access_key_id, secret_access_key, bucket)`. Methods: `upload`, `download`, `list_objects`, `upload_dir`, `download_dir`. ThreadPoolExecutor(8) for parallel transfers.

## Gsim Runner (`gsim/runner.py`)

Static methods shell out to gsim tools via `subprocess.run`:
- `run_backtest(xml_path, config)` — runs gsim backtest, raises `BacktestError` on failure
- `run_simsummary(pnl_path, config)` → `Metrics | None`
- `run_bcorr(pnl_file, config, pools=None)` → `list[(factor, corr)] | None`;对 `pools` 里每个 pnl 目录各跑一次 bcorr 合并结果,缺省 `pools=[pnl_prod_path, pnl_alphalib]`(全库)。`resolve_bcorr_pools(config, discovery_method)` 按因子来源返回同类池(automated/manual 各比各的,legacy 回退全库)。

Configurable timeout from `config.timeout`.

## Notify (`notify/`)

- `feishu_send.py` — Feishu (Lark) webhook notifications (APP_ID/APP_SECRET hardcoded, tech debt)
- `email.py` — commented out, placeholder
