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
- Locks stay at `~/.cache/ops/locks/` — fcntl, per-machine, never synced
- Index/metrics/datasources/bcorr **已迁 Postgres**(2026-07-04, 见 `derived/`)。`cache.py` 现仅剩 json 回退后端的 `derived.json` + locks 用;PG 后端下不再写这些缓存。

## Derived (`derived/`)

派生层 (index/metrics/datasources/bcorr) 的存储抽象,替代原 per-machine
`~/.cache/ops/lib/<lib>/*.json`。三机共享 + 查询不扫盘。范式与 `store/` 一致。

- `base.py` — `DerivedStore` ABC + `DerivedRecord` dataclass(一个因子的四组派生数据合一,扁平字段)。方法:`get_all(author=None)` / `get(name)` / `upsert_index/metrics/datasources/bcorr` / `get_meta/set_meta` / `delete`
- `pg_store.py` — `PostgresDerivedStore`,单张 `factor_derived` 宽表 (library_id, name) 主键,四组独立 UPSERT,GIN(fields/tables) 反查索引;`derived_meta` 表存 `index_built_at` 水位;psycopg3 连接池
- `json_store.py` — `JsonDerivedStore`,单文件 `derived.json`,fcntl 锁 + 原子写,回退用
- `__init__.py` — `default_derived_store(config)` 按 `config.derived_backend` 分发 (json 默认 / postgres)
- **读写分离**:读侧 (list/info/health) 直接消费 `DerivedRecord`;写侧 `refresh_*`(services/list/)收 names 生产派生数据。index 由 `LibraryScanner.scan()` 扫盘后 publish,新鲜度靠 alpha_src mtime vs `index_built_at` 水位跨机判定
- 迁移工具 `ops/tools/derived_migrate.py`;部署 `scripts/postgres/README.md`

## Lock (`lock.py`)

Per-factor advisory fcntl lock. Serializes all ops mutations on a single factor across processes.

- Lock files at `~/.cache/ops/locks/{factor_name}.lock`
- Non-blocking: `FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name): ...`

## Store (`store/`)

三个后端,通过 `state.backend` 切换。`default_store(config)` 根据 backend 返回对应实现。

### `pg_store.py` (default since 2026-07-04, 真相源)

`PostgresStateStore` — 因子生命周期真相源,从 Redis 迁入。`factor_state` 表 (library_id,name) 主键,check_history 存 JSONB 列。原子性用 PG 事务 + `SELECT ... FOR UPDATE` 行级锁替代 Redis 的 WATCH/MULTI/EXEC(transition/append_check 锁行读改写,天然串行,无应用层重试)。时间戳列 TIMESTAMPTZ,读写边界做 ISO string ↔ 本地 tz 转换(`_ts_in`/`_ts_out`,与 Redis `_now()` 格式一致 —— naive datetime 必须打本地 tz 再入库,否则 PG 当 UTC 偏 8h)。连接池/UPSERT 范式同 `derived/pg_store.py`。迁移工具 `ops/tools/state_to_pg.py`。

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
