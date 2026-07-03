# Infra

底层 I/O 和外部系统交互层。

## Config (`config.py`)

`Config` class loads YAML. Resolution order: `OPS_CONFIG` env var → `./config.yaml` → project root `config.yaml`.

Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables with `OPS_` prefix (e.g. `OPS_GSIM_HOME` → `gsim_home`).

Key attributes: all `path.*` fields as `Path`, `compliance`/`correlation`/`checkpoint` dicts, `sync_remote`, `library_id`, S3 credentials.

**State backend (`state.*` in yaml)**:
- `state.backend: json | redis` (default `json`)
- For `redis`: `state.redis.url`, `state.redis.password` (literal) or `state.redis.password_env` (env var name) or **`state.redis.password_file` + `state.redis.password_key`** (file containing `KEY=value`, used as last resort)
- 三层 fallback 顺序见 `config.py` 注释。`config.prod-legacy.yaml` 用 json,`config.yaml`(2026-06-05 上线默认)用 redis-sentinel。

`config.prod-legacy.yaml` 是上线前的 prod (S3 sync 模型),保留作紧急回退。

## Sudo Self-Elevation (`sudo.py`)

JFS 集中运维模型下 `alpha_src` / `staging` / `alpha_pnl` 等都是 root-owned,wbai 直接写会 EACCES。

`maybe_elevate(args)`:进程入口检测 `args.sub-command ∈ WRITE_COMMANDS` (submit/resubmit/recheck/check/rm/approve/cancel/clear/pack/backfill) **且** `alpha_src.st_uid == 0` → `os.execvp('sudo -E --preserve-env=OPS_* ops <argv>')` 替换自身。read-only 命令和 legacy prod (alpha_src wbai-owned) 都 no-op。

`ensure_redis_password(args)`:wbai shell 没 `OPS_STATE_REDIS_PASSWORD` env 时,从 `config.state.redis.password_file` (默认 `/etc/juicefs/alphalib-jfs.env`) 通过 `sudo grep` 一次拿密码塞进 env,后续 `maybe_elevate` 的 sudo `--preserve-env` 透传到 root 子进程。**故意不 `os.path.exists` 检查** password_file:`/etc/juicefs/` 是 `0700 root:root`,wbai stat 不到,但 sudo 跑成 root 能读。

两者都在 `ops/main.py` 入口调,顺序:`ensure_redis_password(args)` → `maybe_elevate(args)` → `args.func(args)`。

## Cache (`cache.py`)

All ops state/cache files live under `~/.cache/ops/`.

- New layout: `~/.cache/ops/lib/<library_id>/<filename>`
- `cache_path(library_id, filename, legacy_hash=...)` resolves path + one-shot migrates legacy files
- `library_cache_dir(library_id)` returns the dir, ensuring it exists
- Locks stay at `~/.cache/ops/locks/` — fcntl, per-machine, never synced
- Index/metrics/bcorr cache 仍是 per-machine(已知 nice-to-have:搬 redis 让三机一致)

## Lock (`lock.py`)

Per-factor advisory fcntl lock. Serializes all ops mutations on a single factor across processes.

- Lock files at `~/.cache/ops/locks/{factor_name}.lock`
- Non-blocking: `FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name): ...`

## Store (`store/`)

两个后端,通过 `state.backend` 切换。`default_store(config)` 根据 backend 返回对应实现。

### `redis_store.py` (default since 2026-06-05)

`RedisStateStore` — 支持两种 URL scheme:

- `redis://host:port/db` — 直连单实例(legacy / 单机部署)
- `redis-sentinel://h1:p1,h2:p2,h3:p3/service_name/db` — Sentinel-aware,自动 failover

Sentinel 路径构造 `redis.sentinel.Sentinel(...).master_for(service, ...)`,每次 connection 重新 resolve master。`protocol=2` 强制经典 AUTH(redis-py 8.x `from_url` 不 honor protocol=2,直接用 `redis.Redis()` ctor 避雷,见 commit fc4d8f8)。

Schema 跟 `JsonStateStore` 1:1 映射:`state-meta:<lib>` / `state-index:<lib>` set / `state:<lib>:<name>` hash / `state-checks:<lib>:<name>` list。WATCH/MULTI/EXEC 做 read-modify-write,append_check 原子。

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
