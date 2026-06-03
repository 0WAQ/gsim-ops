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
_lib.sh             公共自检 helper (sudo / 二进制 / systemd / TCP / mountpoint)
config.sh           配置 + 凭证解析 (rclone.conf 回退); ./config.sh --show 预览

bootstrap-primary.sh  主节点一把梭 (00 -> 01 -> 02 -> 03 -> 04)
00-install.sh         redis + juicefs 二进制
01-provision.sh       MinIO bucket + format JFS + 临时挂载
02-layout.sh          sidecar symlink + 顶层权限 + 组成员
03-redis.sh           redis 网络化 + 密码 + AUTH + AOF
04-systemd.sh         juicefs-<name>.service 渲染

join.sh             Client 一键接入(自带 group/umask/sidecar 设置)
05-migrate.sh       数据迁移: rsync + 等 writeback drain + chown + 对账
status.sh           健康检查: mount/redis/AOF/writeback/sidecar/groups
verify.sh           PoC 验证套件 (basic / memmap / git / redis-fail)
verify_memmap.py    alpha_feature memmap 仿真,verify.sh memmap 调
teardown.sh         卸载 (--purge 才真删数据)
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

### 主节点

一把梭(任一步失败即停):

```bash
sudo -E bash bootstrap-primary.sh
sudo systemctl start juicefs-alphalib.service
```

或分步,每步幂等:

```bash
sudo -E bash 00-install.sh      # redis + juicefs
sudo -E bash 01-provision.sh    # MinIO bucket + format + 临时挂
sudo -E bash 02-layout.sh       # sidecar symlink + 两组权限 (顶层 only)
sudo -E bash 03-redis.sh        # redis 网络化 + 密码 + AOF
sudo -E bash 04-systemd.sh      # systemd 接管挂载
sudo systemctl start juicefs-alphalib.service
```

需要 `/etc/profile.d/ops-umask.sh` (内容 `umask 0002`),否则组写位失效。`02-layout.sh` 和 `join.sh` 会自动建。

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

`join.sh` 末尾自动校验 sidecar symlink 的 target 等于本机 `JFS_LOCAL_DIR`,跨节点路径不一致会报错并提示。

## 数据迁移

```bash
sudo -E bash 05-migrate.sh --dry-run               # 看文件数 / 字节,不动
sudo -E bash 05-migrate.sh                         # 全量 (alpha_src + pnl + feature)
sudo -E bash 05-migrate.sh --only alpha_src        # 单目录
sudo -E bash 05-migrate.sh --skip-verify           # 跳对账 (只 rsync + chown)
SRC=/path sudo -E bash 05-migrate.sh               # 自定义源
```

大量数据建议后台跑,`tail -f` 看进度:

```bash
nohup sudo -E bash 05-migrate.sh > /tmp/jfs-migrate.log 2>&1 &
tail -f /tmp/jfs-migrate.log
```

脚本步骤(每步幂等):

1. **pre-flight** — mount 可达 / 源存在 / writeback 当前队列空 / cache 空间
2. **rsync -a** — 增量同步,中断重跑续传
3. **等 writeback drain** — `juicefs_staging_blocks=0` 才进下一步,防 cache 里有未上传 chunk = 关机丢
4. **修正 ownership** — `alpha_src: chown -R :alpha-core` 保留作者 user;`alpha_pnl/feature: chown -R root:alpha-data`;dir 加 setgid
5. **对账** — 文件数 / 字节数 / 抽样 N 个 md5 (默认 10,环境变量 `SAMPLE_N=50` 可调)

## 健康检查

```bash
sudo bash status.sh
```

输出 mount / cache / redis (AUTH + AOF) / JFS staging 队列 + 错误计数 / sidecar symlink 一致性 / 组成员 / umask / 数据目录概览。任何 ✗ 项 exit 1。

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
| 服务器重启 (writeback drain 中) | 数据完整(ExecStop 链 sync + ZFS cache 续传)|

## 当前进度

- [x] PoC 两轮通过(basic / memmap / git / redis-fail)
- [x] sidecar 改 symlink + 权限模型(`02-layout.sh`)
- [x] `/etc/profile.d/ops-umask.sh`(`02-layout.sh` / `join.sh` 自动建)
- [x] systemd unit 接管主节点挂载(`04-systemd.sh`)+ umount 三级 fallback
- [x] Redis 网络化 + AUTH + AOF(`03-redis.sh`)
- [x] Client 节点接入(150 实测通过 `join.sh`)
- [x] 跨节点可见性 + flock 真锁验证
- [x] 数据迁移流程脚本化(`05-migrate.sh`)
- [x] 健康检查(`status.sh`)
- [x] 服务器异常重启场景验证(2026-06-03,writeback drain 中重启,数据完整)
- [ ] Redis Sentinel(需要第三台 sudo 节点)
- [ ] 全量数据迁入(进行中)

## 失败回退

PoC 完全独立,不动 `/mnt/storage/alphalib/`,不动现有任何路径。

放弃(主节点):`sudo -E bash teardown.sh --purge` — 卸卷 + 删 bucket + 删 cache。
Client 节点单独退出:`sudo systemctl disable --now juicefs-alphalib.service`。

## 故障排除

### 卷已 format 还想重 format

`01-provision.sh` 检测到卷已存在会跳过 format(避免凭证再进 ps)。强制重 format 先销:
```bash
sudo -E bash teardown.sh --purge   # 必须 --purge 才删 metadata
sudo -E bash 01-provision.sh
```

### Redis 密码 rotate(主节点)

`03-redis.sh` 复用已有 `/etc/juicefs/<name>.env`,不会自动换密码。强制 rotate:
```bash
sudo rm /etc/juicefs/alphalib.env
sudo -E bash 03-redis.sh           # 生成新密码,重启 redis,自动停掉本地 juicefs unit
sudo systemctl start juicefs-alphalib.service
# 所有 client 节点必须重跑 join.sh 输入新密码
```
client 上失效现象:`juicefs-alphalib.service` 反复 EIO / restart,`/etc/juicefs/alphalib.env` 里是旧密码。修复:
```bash
sudo rm /etc/juicefs/alphalib.env
sudo bash /tmp/juicefs-poc/join.sh --meta-host <主节点 IP>   # 提示输新密码
```

### umount 卡住 (systemd stop 不动)

`04-systemd.sh` ExecStop 已带三级 fallback (`juicefs umount` → `fusermount -uz` → `umount -l`)。
完全卡死(连 lazy 都不行)只剩重启。

### sidecar symlink 不一致

`status.sh` / `join.sh` 末尾会报 `JFS 里 symlink target != 本机 JFS_LOCAL_DIR`。
修法:让本机 `JFS_LOCAL_DIR` 跟主节点完全一致,改 `/etc/juicefs-poc.env` 重跑 `join.sh`。
若主节点路径写错了,只能在主节点改路径 + 重做 sidecar(影响所有 client)。

### 重跑 02-layout.sh 后某因子作者权限丢了

不应该发生(`apply_top_dir` 只动顶层)。如果之前跑过老版本(recursive chown),用 `05-migrate.sh` 重做 alpha_src 那一段(它保留作者 user)。

### 服务器异常重启 / 突然断电

JFS 数据保护是分层的,任一层失守都不立刻丢,但要逐层确认:

1. **chunk 数据(JFS cache)**:`04-systemd.sh` 渲染的 ExecStop 三级链(`juicefs umount` → `fusermount -uz` → `umount -l`)在 systemd 正常关机会触发 sync;ZFS cache 持久,重启后 unit 自动起 + 从 cache 续传未上传的 staging block。**前提**:cache 不能放 tmpfs / 内存盘
2. **元数据(redis)**:必须 AOF on (`appendfsync everysec`,最多丢 1s 写入)。AOF off + 异常断电 = 可能丢 RDB save 周期内的写入(默认 1h/15min/1min)。`status.sh` 会报 `AOF off ✗`

恢复检查(顺序):

```bash
# 1. 服务起来了吗
systemctl is-active juicefs-alphalib.service redis-server.service
mountpoint /tank/vault/alphalib

# 2. 健康一把梭
sudo bash status.sh

# 3. staging 是否在续传, errors 是否在累积
grep -E 'staging|object_request' /tank/vault/alphalib/.stats

# 4. 抽样读, 验证 chunk 完整
ls /tank/vault/alphalib/alpha_feature | wc -l                                # 文件数对得上源吗
find /tank/vault/alphalib/alpha_feature -type f | shuf -n 5 | xargs md5sum  # 抽样能算 md5 = chunk 完整
```

`staging_block_errors=0` 才是真没丢。`object_request_errors` 是累积重试值(看比例,见踩过的坑)。

### 切 config (`-c config.juicefs.yaml`) 后 `ops list` 看不到 status / fail_stage 列

现象:`ops list -c config.juicefs.yaml` 输出表里所有因子都没颜色,表头也没 `fail_stage` 列。

根因:state 文件按 `library_id` 隔离,路径是 `~/.cache/ops/lib/<library_id>/factor_state.json`。`config.prod.yaml` 没显式设 library_id → 默认 `alphalib`;`config.juicefs.yaml` 显式设了 `alphalib-juicefs` 隔离 PoC,所以读了一个空文件。state 不在 JFS 里,在 per-machine `~/.cache`,迁数据时不会跟过来。

修法(PoC 期):

```bash
cp ~/.cache/ops/lib/alphalib/factor_state.json \
   ~/.cache/ops/lib/alphalib-juicefs/factor_state.json
```

注意之后两份 state **独立不互通**。任何 `-c config.juicefs.yaml` 的写操作(submit/check/approve/rm 等)只动 juicefs 副本,prod 那边不会跟。PoC 期就是要这个隔离;长期切 JuiceFS 之前需要决定 state 走 sync 还是搬进 JFS 共享。

## 踩过的坑

- `rclone.conf` 的 `no_check_bucket = true` 让 `rclone mkdir` 假成功;`01-provision.sh` 加真 PutObject 兜底
- rclone `:s3,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL;改用临时 config 文件
- `juicefs format` 日志 `minio://http://endpoint/bucket/bucket/...` 看着重复,实际 S3 调用没问题
- `mount --writeback` 先落 cache 异步上传,延迟低但断电丢未上传数据;`05-migrate.sh` 等 `staging_blocks=0` 才进对账
- MinIO 不可达时挂载 hang,先 `curl ${MINIO_ENDPOINT}/minio/health/live`
- 改 redis.conf 前必须先 stop juicefs unit,否则现挂载在 AUTH 切换瞬间全 EIO。`03-redis.sh` 已自动停
- `usermod -aG` 不影响已有 SSH session;验证用 `sg <group> -c <cmd>`,或重连
- 密码出现在 `juicefs mount` 的 cmdline 会被 `ps` 看到;`04-systemd.sh` 通过 `EnvironmentFile=` 注入 `META_PASSWORD`,URL 不带密码
- `juicefs format` 的 `--access-key/--secret-key` 仍然会进 ps;`01-provision.sh` 检测到卷已存在直接跳过 format,把暴露窗口压到第一次部署
- `02-layout.sh` 老版用 `chown -R/chmod -R` 会毁掉作者 ownership;现在只改顶层 + setgid,内层 owner 由 `05-migrate.sh` 或 ops/submit 管
- 服务器异常重启数据没丢 = 三件事一起救:(a) systemd 走 ExecStop 三级链干净 unmount (b) JFS cache 在持久 FS (ZFS) 而不是 tmpfs (c) redis AOF on。任一缺一就有窗口风险。实测 2026-06-03 服务器重启时 staging=92912 (362G) 还在 cache,重启后从 cache 续传成功,数据完整;但 AOF 当时还是 off,RDB 周期救了一把,纯运气
- `juicefs_object_request_errors` 是累积重试计数,不是丢数据指标。看比例:`errors / object_request_durations_*_total`,2-3% 在 MinIO 偶发限流/抖动是正常水位。真正的丢数据指标是 `juicefs_staging_block_errors`,必须为 0
- `ops` 的 state (`factor_state.json`) 在 per-machine `~/.cache/ops/lib/<library_id>/`,不在 JFS 里也不被 rsync 带过来。换 config(改了 library_id)之后 list 看到的因子没 status,是这个原因。长期方案待定(state 进 JFS / 沿用 sync)
