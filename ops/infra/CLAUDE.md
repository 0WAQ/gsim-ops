# Infra

底层 I/O 和外部系统交互层。

## Config (`config.py`)

`Config` class loads YAML. Resolution order: `OPS_CONFIG` env var → `./config.yaml` → project root `config.yaml`.

Supports `${var_name}` variable substitution from the `vars:` block in YAML, overridable by environment variables with `OPS_` prefix (e.g. `OPS_GSIM_HOME` → `gsim_home`).

Key attributes: all `path.*` fields as `Path`, `compliance`/`correlation`/`checkpoint` dicts, `sync_remote`, `library_id`, S3 credentials.

## Cache (`cache.py`)

All ops state/cache files live under `~/.cache/ops/`.

- New layout: `~/.cache/ops/lib/<library_id>/<filename>`
- `cache_path(library_id, filename, legacy_hash=...)` resolves path + one-shot migrates legacy files
- `library_cache_dir(library_id)` returns the dir, ensuring it exists
- Locks stay at `~/.cache/ops/locks/` — fcntl, per-machine, never synced

## Lock (`lock.py`)

Per-factor advisory fcntl lock. Serializes all ops mutations on a single factor across processes.

- Lock files at `~/.cache/ops/locks/{factor_name}.lock`
- Non-blocking: `FactorLocked` raised immediately if contended (no queueing)
- Usage: `with factor_lock(name): ...`

## Store (`store/json_store.py`)

`JsonStateStore` — JSON-backed state persistence with fcntl cross-process locking.

- Single fcntl lock over the full read-modify-write window
- Atomic write via tempfile + `os.replace`
- Stale `.tmp` cleanup (> 1h) on lock acquisition
- Methods: `get`, `list`, `upsert`, `transition`, `bulk_upsert`

## S3 (`s3.py`)

Thin boto3 wrapper for sync. `S3Client(endpoint_url, access_key_id, secret_access_key, bucket)`.

Methods: `upload`, `download`, `list_objects`, `upload_dir`, `download_dir`. ThreadPoolExecutor(8) for parallel transfers.

## Gsim Runner (`gsim/runner.py`)

Static methods shell out to gsim tools via `subprocess.run`:
- `run_backtest(xml_path, config)` — runs gsim backtest, raises `BacktestError` on failure
- `run_simsummary(pnl_path, config)` → `Metrics | None`
- `run_bcorr(pnl1, pnl2_or_dir, config)` → correlation float

Configurable timeout from `config.timeout`.

## Notify (`notify/`)

- `feishu_send.py` — Feishu (Lark) webhook notifications (APP_ID/APP_SECRET hardcoded, tech debt)
- `email.py` — commented out, placeholder
