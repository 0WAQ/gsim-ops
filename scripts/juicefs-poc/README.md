# JuiceFS PoC

验证 JuiceFS 作为 alphalib 共享文件系统的可行性。背景和长期规划见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。

## 拓扑

```
[本机]                                [MinIO  10.9.100.145:39000]
├── JuiceFS client (FUSE)             └── bucket: alphalib-juicefs
├── Redis 127.0.0.1:6379  (metadata)
└── cache: /tank/vault/juicefs-cache  (500 GB,ZFS-on-NVMe)
        │
        └─→ chunk 上传 MinIO
```

挂载点 `/tank/vault/alphalib/`,和现有 `/mnt/storage/alphalib/` 并列,不冲突。

## 凭证

PoC 期需要 MinIO **root 账号**(rclone.conf 里默认 `[39000]` 只读不能建 bucket)。环境变量注入:

```bash
export MINIO_ROOT_USER=<root-ak>
export MINIO_ROOT_PASSWORD=<root-sk>
# 可选: export MINIO_ENDPOINT=http://10.9.100.145:39000
```

`00-config.sh` 优先级:`MINIO_ROOT_USER/PASSWORD` > `MINIO_ACCESS_KEY/SECRET_KEY` > `rclone.conf [39000]`。
带 sudo 的脚本必须 `sudo -E` 才能透传环境变量。PoC 跑完旋转一次 root key。

## 配置

调参全在 `00-config.sh`,`./00-config.sh --show` 预览。

| 变量 | 默认 |
|---|---|
| `JFS_BUCKET` | `alphalib-juicefs` |
| `JFS_MOUNT` | `/tank/vault/alphalib` |
| `JFS_LOCAL_DIR` | `/tank/vault/alphalib.local` |
| `JFS_CACHE_DIR` | `/tank/vault/juicefs-cache` |
| `JFS_CACHE_SIZE_MB` | `512000` |
| `JFS_META_URL` | `redis://127.0.0.1:6379/0` |

## 脚本

顺序执行,每个幂等。

| 脚本 | 做什么 |
|---|---|
| `01-install.sh` | 装 redis-server + juicefs client |
| `02-prepare.sh` | 建 MinIO bucket + 本地 cache 目录 |
| `03-format-mount.sh` | `juicefs format` + `mount --writeback` |
| `04-verify-basic.sh` | 读写 / flock / stat / 可见性 5 项 |
| `05-verify-memmap.sh` | alpha_feature 模式仿真 |
| `06-verify-git.sh` | 500 提交 git 基线 |
| `07-verify-redis-failure.sh` | Redis 故障注入 |
| `08-relocate-local-dirs.sh` | `alpha_dump / staging / recycle` 改 symlink → 本地 sidecar |
| `09-setup-perms.sh` | 应用权限模型(见下) |
| `99-teardown.sh` | 卸载(`--purge` 删数据) |

## 验证结果 (2026-06-02)

| 项 | 实测 |
|---|---|
| 100 MB 顺序写 | 333 ms (~300 MB/s) |
| 100 MB re-read (cache 命中) | 176 ms |
| flock 跨进程串行 | 5 ms 切换 gap |
| 1000 小文件 ls | 15 ms |
| memmap 日增 1 行 | 35 ms |
| 跨进程 reopen 一致性 | bit-level OK |
| 500 提交 git | commit 75ms,log/blame/status/diff < 250ms |
| Redis kill | JuiceFS 不 hang,所有 syscall 立刻 EIO;**生产前置 Redis Sentinel** |

## 权限模型

两个组,owner 一律 root(recycle 子目录除外),enforcement 走 gid。**不用 POSIX ACL**,靠 setgid + umask 0002。

| 组 | gid | 成员 | 作用 |
|---|---|---|---|
| `alpha-core` | 59000 | wbai | 读 alpha_src / staging |
| `alpha-data` | 59001 | wbai | 读写 alpha_pnl / alpha_feature / alpha_dump |

布局:

```
JFS  /tank/vault/alphalib/        root:alpha-data 2755
├── alpha_src/       root:alpha-core 2750     core 读
├── alpha_pnl/       root:alpha-data 2775     data 读写, others 读
├── alpha_feature/   root:alpha-data 2775     data 读写, others 读
├── alpha_dump  →    /tank/vault/alphalib.local/alpha_dump   (symlink)
├── staging     →    /tank/vault/alphalib.local/staging      (symlink)
└── recycle     →    /tank/vault/alphalib.local/recycle      (symlink)

本地 /tank/vault/alphalib.local/   root:alpha-data 2755
├── staging/         root:alpha-core 2770     core 读写
├── alpha_dump/      root:alpha-data 2775     data 读写, others 读
└── recycle/         root:root       1755     sticky
    └── <unixId>/    <unixId>:<grp>  0700     只用户自己
```

- gid 选 59xxx(GID_MAX=60000 以下,避开 7/8/9000 常见段)
- `alpha_src / staging` 没有 others 位:研究员看自己代码也走外部入口,不直接读 FS
- `recycle` 已嵌套一层 unixId,09 会按现有子目录的名字 `chown <unixId>:<primary>`
- umask 0002 必须在 `/etc/profile.d/ops-umask.sh` 装好,否则新文件 g-w,组写失效

## 当前进度

- [x] PoC 两轮通过(基础 IO / memmap / git / Redis 故障)
- [x] 08 sidecar 改 symlink
- [x] 09 重写为 setgid + umask 模型,不依赖 ACL
- [ ] 09 重跑一次(应用新规则到 alphalib.local)
- [ ] 装 `/etc/profile.d/ops-umask.sh`
- [ ] Step 2 持久化(systemd unit for redis-server + JuiceFS .mount unit)
- [ ] 跨节点验证(等第二台机器)
- [ ] Redis Sentinel(等第三台机器)
- [ ] 全量数据迁入

## 失败回退

PoC 完全独立,不动 `/mnt/storage/alphalib/`,不动现有任何路径。

放弃: `sudo -E bash 99-teardown.sh --purge`。

## 踩过的坑

- `rclone.conf` 的 `no_check_bucket = true` 让 `rclone mkdir` 假成功;02 加了真 PutObject 兜底
- rclone `:s3,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL;02/99 改用临时 config 文件
- `juicefs format` 日志 `minio://http://endpoint/bucket/bucket/...` 看着重复,实际 S3 调用没问题
- `mount --writeback` 先落 cache 异步上传,延迟低但断电丢未上传数据;生产权衡
- MinIO 不可达时挂载 hang,先 `curl ${MINIO_ENDPOINT}/minio/health/live`
- `usermod -aG` 不影响已有 SSH session;验证用 `sg <group> -c <cmd>` 子 shell,或重新登录
