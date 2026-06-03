# JuiceFS PoC

验证 JuiceFS 作为 alphalib 共享文件系统的可行性。背景和长期规划见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。

## 拓扑

```
主节点 160                            MinIO 10.9.100.145:39000
├── Redis 6379  (metadata, 听 0.0.0.0 + requirepass)
├── JuiceFS client (FUSE) ─────────→  bucket: alphalib-juicefs
└── /tank/vault/alphalib/ (mount)
└── /tank/vault/juicefs-cache (500 GB,ZFS-on-NVMe)

Client 节点 150 / ...
├── JuiceFS client (FUSE)
├── META_PASSWORD via /etc/juicefs/alphalib.env (0600 root)
└── meta URL → redis://10.9.100.160:6379/0
```

挂载点 `/tank/vault/alphalib/`,和现有 `/mnt/storage/alphalib/` 并列,不冲突。

## 配置

调参全在 `00-config.sh`,`./00-config.sh --show` 预览。

| 变量 | 默认 | 备注 |
|---|---|---|
| `JFS_NAME` | `alphalib` | 卷名;同时决定 unit 名 `juicefs-<name>.service` 和 env 文件名 |
| `JFS_BUCKET` | `alphalib-juicefs` | MinIO bucket |
| `JFS_MOUNT` | `/tank/vault/alphalib` | 挂载点 |
| `JFS_LOCAL_DIR` | `/tank/vault/alphalib.local` | 本地 sidecar(每机一份,不进 JFS) |
| `JFS_CACHE_DIR` | `/tank/vault/juicefs-cache` | 本地 chunk cache |
| `JFS_CACHE_SIZE_MB` | `512000` | cache 上限(500 GB) |
| `JFS_META_URL` | `redis://127.0.0.1:6379/0` | client 节点应覆盖为主节点 IP |
| `JFS_REDIS_LOCAL` | `1` | 0 = 不依赖本地 redis-server.service(client 节点) |
| `JFS_CLIENT_ONLY` | `0` | 1 = 01-install.sh 跳过 redis 安装 |

**MinIO 凭证只在主节点 bootstrap 时(02/03)需要**。挂载、AUTH、跨节点全程不用。
环境变量优先:`MINIO_ROOT_USER/PASSWORD` > `MINIO_ACCESS_KEY/SECRET_KEY` > `rclone.conf [39000]`。
带 sudo 必须 `sudo -E` 透传环境。

## 脚本

| 脚本 | 做什么 |
|---|---|
| `_lib.sh` | 公共自检 helper(sudo / 二进制 / systemd / TCP / mountpoint),内部 source |
| `00-config.sh` | 配置 + 凭证解析,内部 source |
| `01-install.sh` | 装 redis-server + juicefs;`JFS_CLIENT_ONLY=1` 跳 redis |
| `02-prepare.sh` | 建 MinIO bucket + cache 目录 |
| `03-format-mount.sh` | `juicefs format` + 临时手动挂(后续被 10 接管) |
| `04-verify-basic.sh` | 读写 / flock / stat / 可见性 5 项 |
| `05-verify-memmap.sh` | alpha_feature 模式仿真 |
| `06-verify-git.sh` | 500 提交 git 基线 |
| `07-verify-redis-failure.sh` | Redis 故障注入 |
| `08-relocate-local-dirs.sh` | `alpha_dump / staging / recycle` 改 symlink → sidecar |
| `09-setup-perms.sh` | 应用权限模型(见下) |
| `10-systemd-unit.sh` | 写 `juicefs-<name>.service`,开机自挂 |
| `11-redis-network.sh` | Redis 听 0.0.0.0 + requirepass,密码存 `/etc/juicefs/<name>.env` |
| `12-join-cluster.sh` | **Client 节点一键加入**(装 client + 拿密码 + 测连通 + 起 service) |
| `99-teardown.sh` | 卸载;`--purge` 删 metadata + bucket + cache |

所有脚本顶部自检 sudo / 缺失二进制 / systemd / 远端可达,缺啥说啥。

## 部署

### 主节点(一次性)

```bash
sudo -E bash 01-install.sh                # redis + juicefs
sudo -E bash 02-prepare.sh                # MinIO bucket
sudo -E bash 03-format-mount.sh           # juicefs format + 临时挂
sudo -E bash 08-relocate-local-dirs.sh    # sidecar
sudo -E bash 09-setup-perms.sh            # 权限模型
sudo -E bash 11-redis-network.sh          # redis 网络化 + 生成密码
sudo -E bash 10-systemd-unit.sh           # systemd 接管挂载
sudo systemctl start juicefs-alphalib.service
```

需要 `/etc/profile.d/ops-umask.sh` (内容: `umask 0002`),否则组写位失效。

### Client 节点

```bash
# [主节点] 取密码 + scp 脚本目录
sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib.env
scp -r scripts/juicefs-poc <client>:/tmp/

# [client]
ssh <client> 'sudo bash /tmp/juicefs-poc/12-join-cluster.sh --meta-host 10.9.100.160'
# 交互提示输密码;或非交互: echo $PASS | sudo bash ... --password-stdin
```

## 权限模型

两组,owner 一律 root(recycle 子目录除外),enforcement 走 gid。**不用 POSIX ACL**,靠 setgid + umask 0002。

| 组 | gid | 成员 | 作用 |
|---|---|---|---|
| `alpha-core` | 59000 | wbai | 读 alpha_src / staging |
| `alpha-data` | 59001 | wbai | 读写 alpha_pnl / alpha_feature / alpha_dump |

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
- `recycle` 嵌套一层 unixId,09 按现有子目录名 `chown <unixId>:<primary>`
- umask 0002 必须在 `/etc/profile.d/ops-umask.sh` 装好

## 验证结果(2026-06-02)

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

## 当前进度

- [x] PoC 两轮通过(基础 IO / memmap / git / Redis 故障)
- [x] 08 sidecar 改 symlink
- [x] 09 权限模型(setgid + umask,无 ACL)
- [x] `/etc/profile.d/ops-umask.sh`
- [x] 10 systemd unit 接管主节点挂载
- [x] 11 Redis 网络化 + AUTH
- [ ] 12 client 节点接入(待 150 实测)
- [ ] 跨节点一致性 + flock 真锁验证
- [ ] Redis Sentinel(需要第三台 sudo 节点)
- [ ] 全量数据迁入

## 失败回退

PoC 完全独立,不动 `/mnt/storage/alphalib/`,不动现有任何路径。

放弃(主节点跑):`sudo -E bash 99-teardown.sh --purge`,卸卷 + 删 bucket + 删 cache + 清 Redis。
Client 节点单独退出:`sudo systemctl disable --now juicefs-<name>.service`。

## 踩过的坑

- `rclone.conf` 的 `no_check_bucket = true` 让 `rclone mkdir` 假成功;02 加真 PutObject 兜底
- rclone `:s3,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL;02/99 改用临时 config 文件
- `juicefs format` 日志 `minio://http://endpoint/bucket/bucket/...` 看着重复,实际 S3 调用没问题
- `mount --writeback` 先落 cache 异步上传,延迟低但断电丢未上传数据;生产权衡
- MinIO 不可达时挂载 hang,先 `curl ${MINIO_ENDPOINT}/minio/health/live`
- 改 redis.conf 前必须先 `systemctl stop juicefs-<name>.service`,否则现挂载在 AUTH 切换瞬间全 EIO。11 已经会自动停
- `usermod -aG` 不影响已有 SSH session;验证用 `sg <group> -c <cmd>`,或重连
- 密码出现在 `juicefs mount` 的 cmdline 会被 `ps` 看到;10 通过 `EnvironmentFile=` 注入 `META_PASSWORD`,URL 不带密码
