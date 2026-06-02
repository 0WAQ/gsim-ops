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

- `~/.cache/ops/lib/<library_id>/{index,metrics,datasources,factor_state,local_etag_cache}.json`
- `index.json` is **not** synced (1h TTL, regenerated on demand); locks (`~/.cache/ops/locks/`) are fcntl, per-machine, never synced.
- `local_etag_cache.json` 也只在本机:记录每个本地数据文件的 `(mtime, size, etag)`,让 sync 不必每次 hash 482GB。
- Manifest 文件 (`sync_manifest.json`) 已废弃,现在 sync 不再维护任何远端可见的指纹缓存。

## True Diff Engine (`diff.py`)

Sync 改成"真同步":每次都列举两端,直接 diff,**etag 是权威判等依据**。

- `walk_local(root, subdir, cache, recompute=False)` → `{relpath: FileInfo(size, mtime, etag)}`。每个文件 stat 完查 `etag_cache`,`(mtime, size)` 命中就用缓存的 etag;miss 才 `compute_s3_etag` 读盘算 md5。caller 收尾时 `etag_cache.save` 落盘。`recompute=True` (`--deep`) 全部重算。
- `list_remote(s3, prefix)` → 同结构,etag 由 S3 list 免费返回(单文件 md5 hex,多分片 `<md5>-N`)。
- `diff(local, remote)` → `DirDiff` 四类清单:`only_local / only_remote / differ / identical`。

判等规则:
1. rel 仅在一侧 → `only_local` / `only_remote`
2. 两侧都有 + `local.etag == remote.etag` → `identical`
3. 两侧都有 + etag 不同 → `differ`

mtime 不参与 identical/differ 分类。同 mtime+size 不同内容的边缘场景由 `--deep`(强制重算)兜底。

`compute_s3_etag(path)` 按 `S3_MULTIPART_THRESHOLD/CHUNKSIZE = 8MB` 复刻 boto3 的 etag 算法:小于阈值是 `md5(file)`,超过是 `md5(concat(md5(part)))-N`。这两个常量也喂给 `S3Client` 的 `TransferConfig`,确保上传/复算一致。

### Conflict 策略

`differ` 文件**不自动选方向**,push/pull 都报 conflict。用户需手工解决:
- push 遇冲突:删远端旧版本,或先 pull(拉到本地后再决定)
- pull 遇冲突:删本地旧版本,或先 push(推到远端后再决定)

这样避免了 mtime 的不可靠性(跨机器生产顺序、mtime 被 touch/rsync 污染等场景)。

### mtime 校准

S3 LastModified 是**上传时间**,本地文件 mtime 是**编辑时间**。校准的目的是**让 etag 缓存命中**:如果 push 后本地 mtime 没被校准,下次 walk 时 mtime 不等于缓存里的 mtime,etag 会被重算——纯粹浪费 CPU。

`_push_dir` 上传成功后 `head_object` 拿 LastModified,`_pull_dir` 用 list 里的 mtime,统一 `os.utime(local, (lm, lm))`,并同步把新 `(mtime, size, etag)` 写回 `etag_cache`。

## Push (`push`)

每个数据目录独立 diff:
- `only_local` → 上传。
- `differ` → 报冲突,**不**自动覆盖远端。用户需手工解决(删远端或先 pull)。
- `only_remote` → 静默忽略(删除是 gc 的职责,sync 不删远端)。
- 并发 `ThreadPoolExecutor(8)` 上传。
- 上传完更新本地 `etag_cache`。

Pre-push check 基于 `updated_at` 而非 key set:只有当远端某 key 的 `updated_at` 严格新于本地才报 behind,避免本地清过 orphan 后 push 被卡。

## Pull (`pull`)

State merge 在前,然后每个数据目录 diff:
- `only_remote` → 候选下载(按 status 过滤)。
- `differ` → 报冲突,**不**自动覆盖本地。用户需手工解决(删本地或先 push)。
- 候选名 → 查 `factor_state.json` 的 status:DELETED / SUBMITTED 跳过(tombstone 不拉数据,SUBMITTED 在 staging 不进库)。
- 下载过程 S3 返回 404(list 之后远端被删/被覆盖)→ 报错,不静默 success。
- 下载完更新本地 `etag_cache`。

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

`ops sync verify` 实跑:对三个数据目录两端列举 → 输出 `only_local / only_remote / etag 不一致` 三类清单。只读。
`ops sync verify --deep`:忽略本地 etag 缓存,强制重算所有本地 etag,捕捉缓存里 mtime/size 没动但内容已坏的场景。代价是读全部本地文件(~500MB/s),`alpha_feature` 482GB 全跑约半小时。

## Operations

- `ops sync push` — list+diff (etag) 文件级增量推送 + state merge
- `ops sync push --deep` — 同上,忽略本地 etag 缓存重算
- `ops sync pull` — state merge + list+diff (etag) 文件级增量拉取(按 status 过滤)
- `ops sync pull --deep` — 同上,忽略本地 etag 缓存重算
- `ops sync status` — 不扫数据目录,只对比两端 state 总数 / 仅本地 / 仅远端 / 远端更新数
- `ops sync verify` — 三个数据目录 etag 级两端校验
- `ops sync verify --deep` — 上面基础上忽略缓存重算(慢)
