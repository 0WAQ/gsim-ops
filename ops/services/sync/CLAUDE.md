# Sync

Cross-server factor library sync via S3. Ships **data + state together** so a new machine bootstraps with `ops sync pull`.

## Remote Layout

```
<sync.remote>/<library_id>/
├── alpha_src/
├── alpha_pnl/
├── alpha_feature/
└── .state/              # dotfile so it's hidden from casual `rclone ls`
    ├── factor_state.json
    ├── metrics.json
    └── datasources.json
```

**`library_id`** (`Config.library_id`): defaults to `alpha_src.parent.name` (e.g. `alphalib`), overridable via `sync.library_id`. Two machines pointing at the same logical library get the same id regardless of absolute paths — which is what lets state files travel.

## Cache Layout (`ops/infra/cache.py`)

- Old: `~/.cache/ops/{md5(config_path)[:8]}.{index|metrics|datasources}.json` + `~/.cache/ops/factor_state.json`
- New: `~/.cache/ops/lib/<library_id>/{index,metrics,datasources,factor_state}.json`
- `cache_path(library_id, filename, legacy_hash=...)` resolves the new path and one-shot migrates any legacy file on first call — no manual migration step
- `index.json` is **not** synced (1h TTL, regenerated on demand); locks (`~/.cache/ops/locks/`) are fcntl, per-machine, never synced

## Per-subdir Transfer Strategy

| Subdir | Method | Why |
|---|---|---|
| `alpha_feature` | direct S3 upload per file | 2 large .npy per factor |
| `alpha_src`, `alpha_pnl` | S3 upload_dir / upload | Few small files |

**Note**: `alpha_dump` is not synced — it is a local-only intermediate product.

## Transport

`ops/infra/s3.py` (`S3Client`) wraps boto3. Config in `sync.s3` (endpoint_url, access_key_id, secret_access_key, bucket). ThreadPoolExecutor(8 workers) parallelizes push/pull.

## Manifest Fingerprint (`manifest.py`)

`SyncManifest` tracks per-factor mtimes for incremental push:
- `src_mtime` / `pnl_mtime` / `feature_mtime` — max mtime in each subtree
- `dump_latest` / `dump_count` — retained for local `ops pack` incremental detection (not used by sync)

Scan walks one `os.scandir(alpha_src)` (one stat per factor, not per file), descending into a factor's dirs only when its top-level fingerprint moved. Changed factors are uploaded via boto3. Manifest is only advanced after successful upload; partial pushes naturally re-send next time.

## State Merge (`merge.py`)

Each of `factor_state.json`, `metrics.json`, `datasources.json` carries a per-entry `updated_at` ISO timestamp. The sync step:
1. Download remote `.state/<file>` to tmp
2. Per-name: pick the entry with newer `updated_at`; tie → keep local
3. Atomic write merged result to local, then upload to remote

`factor_state.json` merge holds the JsonStateStore fcntl lock so a concurrent `ops check` finishing on this machine can't lose its write. Missing `updated_at` on legacy entries treated as `1970-01-01`. `index.json` is **not** synced (1h TTL, regenerable). `sync_manifest.json` is per-machine, also not synced.

## First-run

No `--bootstrap` / `init` flags exposed:
- `ops sync push` on a machine without a manifest: treats it as empty — every factor looks new to `scan_changes`. Upload is additive so already-present remote files are skipped; manifest is written only after a successful push.
- `ops sync pull` on an empty machine (zero local factors): full S3 download of every data dir, then build the manifest from what just landed.

## Pull Semantics

Pull always merges state first. If the local library is empty, falls back to a full S3 download of every data dir. Otherwise uses the merged `factor_state.json` as the "remote manifest of factor names": names present in remote state but missing on local disk are fetched (parallelized via ThreadPoolExecutor).

## --force-state

When local state was intentionally pruned (e.g., cleaned orphan records after deleting empty factor dirs), the pre-push check would refuse because remote state has more keys. `--force-state` skips both the pre-push check and the timestamp merge — it uploads local state files directly to overwrite remote. Use sparingly; normal push should go through merge.

## Soft-delete Interaction

`ops rm <name>` flips state to `DELETED` (a tombstone) — `list`/`health` hide it by default; `ops list -s deleted` shows them. The tombstone propagates to other machines via the next `ops sync push` (state merge). **Sync never deletes remote objects** — soft-delete on machine A causes machine B's next `list` to drop the factor too, but the remote files persist. Reclaiming remote disk for deleted factors is the job of the (deferred) `ops sync gc`. `ops rm --force` drops the *local* dump dir + feature `.npy` (src/pnl always kept).

## Operations

- `ops sync push` — incremental data + state merge
- `ops sync pull` — state merge + pull factors referenced by state but missing locally
- `ops sync status` — counts only (no data scan); reports local-vs-remote-state diff
- `ops sync verify` — placeholder (alpha_dump verify removed)
