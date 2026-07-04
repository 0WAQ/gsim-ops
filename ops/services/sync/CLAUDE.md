# Sync (LEGACY — deprecated post-2026-06-05 JFS go-live)

> **状态**: JFS 上线 (2026-06-05) 后,生产 ops 走 JuiceFS 共享文件系统 + redis-sentinel state,**不再使用 S3 sync**。本子命令仅对 `config.prod-legacy.yaml` 有效,留作紧急回退期使用。后续整体退役(plans.md Phase C 剩余项 #4)。
>
> 仍可用的命令:
> - `ops sync push / pull -c config.prod-legacy.yaml` — 旧 prod 跟 S3 之间增量同步
> - `ops sync verify -c config.prod-legacy.yaml` — 巡检对账(可能保留作通用工具)

本文档保留为完整记录(原本上线前的设计),Phase C 收尾时整体删除。

## Cross-server factor library sync via S3

Ships **data + state together** so a new machine bootstraps with `ops sync pull`.

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

`config.yaml` (JFS, redis backend, **不走 sync**) 与 `config.prod-legacy.yaml` (S3 sync) 默认共用 `library_id = alphalib`。这是有意为之:两边共享因子库视图,只是物理路径和后端不同。

## Cache Layout (`ops/infra/cache.py`)

- `~/.cache/ops/lib/<library_id>/{index,metrics,datasources,factor_state,local_etag_cache}.json`
- `index.json` is **not** synced; locks are fcntl per-machine.
- `local_etag_cache.json` 也只在本机:记录每个本地数据文件的 `(mtime, size, etag)`,让 sync 不必每次 hash 482GB。

## True Diff Engine (`diff.py`)

每次 push/pull 都列举两端 → diff,**etag 是权威判等依据**。

- `walk_local`: stat 后查 `etag_cache`,`(mtime, size)` 命中就用缓存 etag,miss 才读盘算 md5
- `list_remote`: etag 由 S3 list 免费返回(单文件 md5 hex,多分片 `<md5>-N`)
- `diff` 输出 `only_local / only_remote / differ / identical`
- `compute_s3_etag` 复刻 boto3 算法 (8MB threshold/chunksize)

### Conflict 策略

`differ` 文件**不自动选方向**,push/pull 都报 conflict。用户手工解决(删远端旧版本,或先 pull)。

### mtime 校准

S3 LastModified 是上传时间,本地文件 mtime 是编辑时间。校准目的是让 etag 缓存命中:`_push_dir` 上传成功后 `head_object` 拿 LastModified,`_pull_dir` 用 list 里的 mtime,统一 `os.utime(local, (lm, lm))`,同步把新 `(mtime, size, etag)` 写回 `etag_cache`。

## Push (`push`)

- `only_local` → 上传
- `differ` → 报冲突,不自动覆盖远端
- `only_remote` → 静默忽略(删除是 gc 职责)
- 并发 `ThreadPoolExecutor(8)`

Pre-push check 基于 `updated_at` 而非 key set:只有当远端某 key 的 `updated_at` 严格新于本地才报 behind。

## Pull (`pull`)

State merge 在前,然后每个数据目录 diff:
- `only_remote` → 候选下载(按 status 过滤)
- `differ` → 报冲突
- 候选名 → 查 `factor_state.json` status:SUBMITTED 跳过

## State Merge (`merge.py`)

三个状态文件按 per-entry `updated_at` ISO 时间戳合并:
1. 下载远端 `.state/<file>` 到 tmp
2. Per-name:取 `updated_at` 较新的;平局保留本地
3. 原子写本地,然后上传到远端

`factor_state.json` merge 时持 `JsonStateStore` 的 fcntl 锁。**Redis backend (config.yaml) 下整套 merge 不会被调用**,因为 `run_sync` 在 `_make_s3` 阶段已 exit。

## --force-state

本地 state 被刻意修剪过(例:清掉 orphan)且远端 still 有的场景。`--force-state` 跳过 pre-push check 和 timestamp merge,直接用本地 state 覆盖远端。慎用。

## 删除

`ops rm <name>` 现在彻底硬删因子(src/pnl/dump/feature + state + derived),不再有 DELETED tombstone。旧的"软删 + sync 跳过"模型已废弃(sync 本身也在退役中)。

## Verify

`ops sync verify` 实跑:对三个数据目录两端列举 → 输出 `only_local / only_remote / etag 不一致` 三类清单。只读。
`--deep`:忽略本地 etag 缓存重算,捕捉缓存里 mtime/size 没动但内容已坏的场景。

## Operations(只对 `-c config.prod-legacy.yaml` 有意义)

- `ops sync push` — list+diff (etag) 文件级增量推送 + state merge
- `ops sync push --deep` — 同上,忽略本地 etag 缓存重算
- `ops sync pull` — state merge + list+diff (etag) 文件级增量拉取
- `ops sync pull --deep` — 同上,忽略本地 etag 缓存重算
- `ops sync status` — 不扫数据,只对比两端 state 总数
- `ops sync verify` — 三个数据目录 etag 级两端校验
- `ops sync verify --deep` — 同上,忽略缓存重算
