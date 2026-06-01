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

`config.yaml` 与 `config.prod.yaml` 默认共用 `library_id = alphalib` → 同一个 S3 prefix、同一份本地 cache。这是有意为之:staging 与 prod 共享因子库视图,只是 `alpha_src` 物理路径不同。SUBMITTED 状态因子在 staging 目录(非 `alpha_src`),sync 不会推它的数据;它的 state 条目会通过 merge 漂到远端,作为"in flight"元数据,远端 pull 端按 status 跳过,不污染数据。

## Cache Layout (`ops/infra/cache.py`)

- `~/.cache/ops/lib/<library_id>/{index,metrics,datasources,factor_state}.json`
- `index.json` is **not** synced (1h TTL, regenerated on demand); locks (`~/.cache/ops/locks/`) are fcntl, per-machine, never synced.
- Manifest 文件 (`sync_manifest.json`) 已废弃,现在 sync 不再维护任何本地指纹缓存。

## True Diff Engine (`diff.py`)

Sync 改成"真同步":每次都列举两端,直接 diff。

- `walk_local(root)` → `{relpath: FileInfo(size, mtime)}` 遍历本地数据目录(跳过 dotfile)。
- `list_remote(s3, prefix)` → 同结构 + S3 LastModified/ETag。基于 `S3Client.list_objects` 分页列举,千级因子量级一次几百毫秒到一两秒。
- `diff(local, remote)` → `DirDiff` 四类清单:`only_local / only_remote / differ / identical`。判等只看 size(content 漂移由 `verify --deep` 后续覆盖,暂未实现)。

  > **Known bug (待修复)**: `alpha_feature/*.npy` 是定长 memmap (`PACK_L, H` × float64),内容变了 size 不变 → diff 误判 `identical`,`sync push` 不上传。2026-06-01 pack delay-bug 修复后本地重打 677 个 delay=0 因子,但 `ops sync push --dry-run` 看不到差异,远端仍是错位版本。修复方向二选一:(a) size 相同时再比 mtime,本地显著新升级为 `differ`(注意:不同机器 touch 同 size 文件是正常场景,可能误判);(b) 实现 `verify --deep` + `push --deep` 用 S3 etag/md5 比较。修完后需手动 force re-push 这 677 个文件同步远端。
- `newer_side(rel, d)` → `local / remote / tie`。`MTIME_TOLERANCE = 2s` 吸收文件系统量化和 S3 上传时间差。

## Push (`push`)

每个数据目录独立 diff:
- `only_local` → 上传。
- `differ` 且本地更新 → 覆盖远端。
- `differ` 且远端更新或 tie → 报冲突,**不**覆盖远端(让用户先 pull)。
- `only_remote` → 静默忽略(删除是 gc 的职责,sync 不删远端)。
- 并发 `ThreadPoolExecutor(8)` 上传。

Pre-push check 基于 `updated_at` 而非 key set:只有当远端某 key 的 `updated_at` 严格新于本地才报 behind,避免本地清过 orphan 后 push 被卡。

## Pull (`pull`)

State merge 在前,然后每个数据目录 diff:
- `only_remote` + `differ` 且远端更新 → 候选下载。
- 候选名 → 查 `factor_state.json` 的 status:DELETED / SUBMITTED 跳过(tombstone 不拉数据,SUBMITTED 在 staging 不进库)。
- `differ` 且本地更新或 tie → 报冲突,**不**覆盖本地(让用户先 push)。

REJECTED 与 ACTIVE 一视同仁:按远端实际存在的文件拉,缺哪个就少哪个。

## State Merge (`merge.py`)

三个状态文件 (`factor_state.json`, `metrics.json`, `datasources.json`) 都按 per-entry `updated_at` ISO 时间戳合并:
1. 下载远端 `.state/<file>` 到 tmp。
2. Per-name:取 `updated_at` 较新的;平局保留本地。
3. 原子写本地,然后上传到远端。

`factor_state.json` merge 时持 `JsonStateStore` 的 fcntl 锁,避免并发 `ops check` 写丢失。Missing `updated_at` 视作 `1970-01-01`。

## --force-state

本地 state 被刻意修剪过(例:清掉 orphan)且远端 still 有的场景。`--force-state` 跳过 pre-push check 和 timestamp merge,直接用本地 state 覆盖远端。慎用。

## Soft-delete

`ops rm <name>` 把 state flip 成 DELETED(tombstone)。Pull 端按 status 跳过该因子数据;远端实际文件 sync 永不删,需要 `ops sync gc`(尚未实现)回收。

## Verify

`ops sync verify` 实跑:对三个数据目录两端列举 → 输出 `only_local / only_remote / 大小不一致` 三类清单。只读,不修改任何东西。后续可加 `--deep` 启 etag 校验。

## Operations

- `ops sync push` — list+diff 文件级增量推送 + state merge
- `ops sync pull` — state merge + list+diff 文件级增量拉取(按 status 过滤)
- `ops sync status` — 不扫数据目录,只对比两端 state 总数 / 仅本地 / 仅远端 / 远端更新数
- `ops sync verify` — 三个数据目录文件级两端校验
