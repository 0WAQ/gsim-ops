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

## 文件

```
_lib.sh             公共自检 helper (sudo / 二进制 / systemd / TCP / mountpoint), source
config.sh           配置 + 凭证解析 (rclone.conf 回退), source; ./config.sh --show 预览

00-install.sh       主节点: 装 redis + juicefs
01-provision.sh     主节点: MinIO bucket + format JFS + 临时挂载
02-layout.sh        主节点: sidecar symlink + 两组权限
03-redis.sh         主节点: redis 网络化 + 密码 + AUTH
04-systemd.sh       主节点 / Client: 渲染 juicefs-<name>.service

join.sh             Client 一键接入(内部调 00-install.sh + 04-systemd.sh)
verify.sh           PoC 验证套件 (basic / memmap / git / redis-fail)
teardown.sh         卸载 (--purge 才真删数据)

verify_memmap.py    alpha_feature memmap 仿真,verify.sh memmap 调
```

`_lib.sh` 提供 `require_sudo / require_bin / require_systemd / require_dir / require_mountpoint / require_tcp`,所有可执行脚本顶部都做自检,缺啥说啥。

## 配置

调参全在 `config.sh`,`./config.sh --show` 预览。

| 变量 | 默认 | 备注 |
|---|---|---|
| `JFS_NAME` | `alphalib` | 卷名;决定 unit 名 `juicefs-<name>.service` 和 env 文件名 |
| `JFS_BUCKET` | `alphalib-juicefs` | MinIO bucket |
| `JFS_MOUNT` | `/tank/vault/alphalib` | 挂载点 |
| `JFS_LOCAL_DIR` | `${JFS_MOUNT}.local` | 本地 sidecar(每机一份,不进 JFS) |
| `JFS_CACHE_DIR` | `/tank/vault/juicefs-cache` | 本地 chunk cache |
| `JFS_CACHE_SIZE_MB` | `512000` | cache 上限(500 GB) |
| `JFS_META_URL` | `redis://127.0.0.1:6379/0` | client 节点覆盖为主节点 IP |
| `JFS_REDIS_LOCAL` | `1` | 0 = unit 不依赖本地 redis-server.service(给 join 用) |
| `JFS_CLIENT_ONLY` | `0` | 1 = `00-install.sh` 跳过 redis(给 join 用) |

### Per-host 路径覆盖

`/tank/vault/...` 只在 160 (ZFS pool) 上存在,其他节点磁盘布局可能完全不同。
`config.sh` 自动 source `/etc/juicefs-poc.env`,文件里的值覆盖默认。格式:

```bash
# /etc/juicefs-poc.env  (mode 644, owned by root)
JFS_MOUNT=/mnt/jfs/alphalib
JFS_CACHE_DIR=/mnt/jfs/cache
JFS_LOCAL_DIR=/mnt/jfs/alphalib.local
```

Client 节点首次跑 `join.sh` 必须带 `--mount / --cache`(`--local` 可省,默认 `<mount>.local`),
join.sh 会把它们写进 `/etc/juicefs-poc.env`,之后再跑可省略。

主节点想用非默认路径同理:手写 `/etc/juicefs-poc.env`,然后从 `00-install.sh` 起步。

### MinIO 凭证

只在 `01-provision.sh` 需要。挂载、AUTH、跨节点全程不用。
环境变量优先级:`MINIO_ROOT_USER/PASSWORD` > `MINIO_ACCESS_KEY/SECRET_KEY` > `rclone.conf [39000]`。
带 sudo 必须 `sudo -E` 透传环境。

## 部署

### 主节点(按编号顺序,每步幂等)

```bash
sudo -E bash 00-install.sh      # redis + juicefs
sudo -E bash 01-provision.sh    # MinIO bucket + format + 临时挂
sudo -E bash 02-layout.sh       # sidecar symlink + 两组权限
sudo -E bash 03-redis.sh        # redis 网络化 + 生成密码
sudo -E bash 04-systemd.sh      # systemd 接管挂载
sudo systemctl start juicefs-alphalib.service
```

需要 `/etc/profile.d/ops-umask.sh` (内容 `umask 0002`),否则组写位失效。

### Client 节点

```bash
# [主节点] 取密码 + scp 脚本目录
sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib.env
scp -r scripts/juicefs-poc <client>:/tmp/

# [client] 首次:必须给 --mount 和 --cache (会写到 /etc/juicefs-poc.env)
ssh <client> 'sudo bash /tmp/juicefs-poc/join.sh \
  --meta-host 10.9.100.160 \
  --mount /mnt/jfs/alphalib \
  --cache /mnt/jfs/cache'
# 交互输密码;或非交互: echo $PASS | sudo bash ... --password-stdin

# 后续重跑 (复用 /etc/juicefs-poc.env 里的路径)
ssh <client> 'sudo bash /tmp/juicefs-poc/join.sh --meta-host 10.9.100.160'
```

## 验证

```bash
bash verify.sh             # 全套
bash verify.sh basic       # 100MB IO / flock / stat / 可见性
bash verify.sh memmap      # alpha_feature 模式仿真
bash verify.sh git         # 500 commit + log/blame/status/diff,本地 ZFS 对照
sudo -E bash verify.sh redis-fail   # Redis kill 注入(需 sudo)
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
- `alpha_src / staging` 没有 others 位:研究员看自己代码走外部入口,不直接读 FS
- `recycle` 嵌套一层 unixId,`02-layout.sh` 按现有子目录名 `chown <unixId>:<primary>`
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

- [x] PoC 两轮通过(basic / memmap / git / redis-fail)
- [x] sidecar 改 symlink + 权限模型(`02-layout.sh`)
- [x] `/etc/profile.d/ops-umask.sh`
- [x] systemd unit 接管主节点挂载(`04-systemd.sh`)
- [x] Redis 网络化 + AUTH(`03-redis.sh`)
- [ ] Client 节点接入(待 150 实测,跑 `join.sh`)
- [ ] 跨节点一致性 + flock 真锁验证
- [ ] Redis Sentinel(需要第三台 sudo 节点)
- [ ] 全量数据迁入

## 失败回退

PoC 完全独立,不动 `/mnt/storage/alphalib/`,不动现有任何路径。

放弃(主节点):`sudo -E bash teardown.sh --purge` — 卸卷 + 删 bucket + 删 cache。
Client 节点单独退出:`sudo systemctl disable --now juicefs-alphalib.service`。

## 踩过的坑

- `rclone.conf` 的 `no_check_bucket = true` 让 `rclone mkdir` 假成功;`01-provision.sh` 加真 PutObject 兜底
- rclone `:s3,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL;改用临时 config 文件
- `juicefs format` 日志 `minio://http://endpoint/bucket/bucket/...` 看着重复,实际 S3 调用没问题
- `mount --writeback` 先落 cache 异步上传,延迟低但断电丢未上传数据;生产权衡
- MinIO 不可达时挂载 hang,先 `curl ${MINIO_ENDPOINT}/minio/health/live`
- 改 redis.conf 前必须先 stop juicefs unit,否则现挂载在 AUTH 切换瞬间全 EIO。`03-redis.sh` 已自动停
- `usermod -aG` 不影响已有 SSH session;验证用 `sg <group> -c <cmd>`,或重连
- 密码出现在 `juicefs mount` 的 cmdline 会被 `ps` 看到;`04-systemd.sh` 通过 `EnvironmentFile=` 注入 `META_PASSWORD`,URL 不带密码
