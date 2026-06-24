# JuiceFS 生产部署

2026-06-05 上线: JuiceFS 作为 alphalib 共享文件系统,跨三机 (160 master + 150 replica + 144 LAN client) 透明读写。Redis Sentinel HA (3 sentinel:26380, master 160, replica 150, failover 实测 9.12 s)。

背景和长期规划见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。

## 拓扑

```
主节点 160 (IDC, ZFS pool)                      MinIO 10.9.100.145:39000
├── redis-server.service     :6379  alphalib biz   bucket: alphalib-juicefs
│                                    (本来就有, JuiceFS 不动)
├── redis-jfs.service        :6380  JFS metadata master + ops state
│   ├── AOF on (appendfsync everysec)
│   └── masterauth = requirepass (failover 后自动变 replica 时用)
├── redis-sentinel-jfs.service :26380  Sentinel #1 monitoring mymaster
├── JuiceFS client (FUSE) ───────────────────────→
└── /tank/vault/alphalib/         (mount, writeback)
    /tank/vault/juicefs-cache     (500 GB, ZFS-on-NVMe)

Client 节点 150 (IDC)
├── redis-jfs.service        :6380  Replica of 160 (replicaof + masterauth)
├── redis-sentinel-jfs.service :26380  Sentinel #2
├── JuiceFS client (FUSE) -> /tank/vault/alphalib
└── /etc/juicefs/alphalib-jfs.env  (0600 root, META_PASSWORD)

Client 节点 144 (本地 LAN, 跨段 to IDC, 磁盘布局不同)
├── redis-sentinel-jfs.service :26380  Sentinel #3 (纯投票, 无 redis 数据)
├── JuiceFS client (FUSE) -> /storage/vault/alphalib
├── /tank/vault/alphalib.local -> /storage/vault/alphalib.local  (软链对齐绝对路径)
└── 详见故障排除 "sidecar symlink 不一致" 情况 B

ops state URL (config.yaml state.redis.url):
  redis-sentinel://160:26380,150:26380,144:26380/mymaster/0
```

挂载点 `/tank/vault/alphalib/` 即生产路径。`/mnt/storage/alphalib/` 是上线前 prod,保留作紧急回退,稳定后清理。

**为什么独立 redis 实例**:见踩过的坑 "多产品共用 redis db 的风险"。共享是结构性风险(stop / SAVE / FLUSHDB / OOM 都跨产品爆),分实例后两边完全独立的 lifecycle / 持久化 / HA / 升级周期。

## 文件

```
_lib.sh             公共自检 helper (sudo / 二进制 / systemd / TCP / mountpoint)
config.sh           配置 + 凭证解析 (rclone.conf 回退); ./config.sh --show 预览

bootstrap-primary.sh  主节点一把梭 (00 -> 01 -> 02 -> 03 -> 04)
00-install.sh         redis + juicefs 二进制
01-provision.sh       MinIO bucket + format JFS + 临时挂载
02-layout.sh          sidecar symlink + 顶层权限 + 组成员
03-redis.sh           redis 网络化 + 密码 + AOF (运行时 config set, 不重启)
04-systemd.sh         juicefs-<name>.service 渲染

06-redis-jfs.sh     起独立 redis 实例 (port 6380, AOF on, 进程级隔离, masterauth 预设)
06-meta-migrate.sh  把 metadata 从 6379 迁到 6380 (反向白名单, MIGRATE in batches)

07-redis-replica.sh    Replica 节点 (默认 150) 上起 redis-jfs:6380 作为 master 的 replica
08-sentinel.sh         每节点 (160/150/144) 上起 redis-sentinel-jfs:26380 (Sentinel #N)
09-switch-meta-url.sh  把节点 JFS_META_URL 从直连切到 sentinel 发现

join.sh             Client 一键接入(自带 group/umask/sidecar + 写 META_URL 到 host env)
05-migrate.sh       数据迁移: rsync + 等 writeback drain + chown + 对账
status.sh           健康检查: mount/redis/AOF/writeback/sidecar/groups
verify.sh           验证套件 (basic / memmap / git / redis-fail)
verify_memmap.py    alpha_feature memmap 仿真,verify.sh memmap 调
teardown.sh         卸载 (--purge 才真删数据)
```

`_lib.sh` 提供 `require_sudo / require_bin / require_systemd / require_dir / require_mountpoint / require_tcp`,所有可执行脚本顶部都做自检,缺啥说啥。

## 配置

调参全在 `config.sh`,`./config.sh --show` 预览。

| 变量 | 默认 | 备注 |
|---|---|---|
| `JFS_NAME` | `alphalib` | 卷名;决定 unit 名 `juicefs-<name>.service` 和默认 env 文件名 |
| `JFS_BUCKET` | `alphalib-juicefs` | MinIO bucket |
| `JFS_MOUNT` | `/tank/vault/alphalib` | 挂载点 |
| `JFS_LOCAL_DIR` | `${JFS_MOUNT}.local` | 本地 sidecar(每机一份,不进 JFS) |
| `JFS_CACHE_DIR` | `/tank/vault/juicefs-cache` | 本地 chunk cache |
| `JFS_CACHE_SIZE_MB` | `512000` | cache 上限(500 GB) |
| `JFS_META_URL` | `redis://127.0.0.1:6379/0` | 跑过 06-meta-migrate 后变 `redis://<host>:6380/0`,跑过 09-switch-meta-url 后变 sentinel URL `redis://mymaster,h1,h2,h3:26380/0` |
| `JFS_ENV_FILE` | `/etc/juicefs/<name>.env` | 06-meta-migrate 后覆盖为 `<name>-jfs.env` |
| `JFS_REDIS_LOCAL` | `1` | 0 = unit 不依赖本地 redis(给 client 用,join.sh 自动写 0)。Sentinel HA 后即使为 1 也只是 `Wants` 不是 `Requires` |
| `JFS_REDIS_UNIT` | `redis-server.service` | 06-meta-migrate 后改 `redis-jfs.service` |
| `JFS_CLIENT_ONLY` | `0` | 1 = `00-install.sh` 跳过 redis(给 join 用) |

### Per-host 路径覆盖

`/tank/vault/...` 只在 160 (ZFS pool) 上存在,其他节点磁盘布局可能完全不同。
`config.sh` 自动 source `/etc/juicefs-poc.env`,文件里的值覆盖默认。

```bash
# /etc/juicefs-poc.env  (mode 644, owned by root)
JFS_MOUNT=/mnt/jfs/alphalib
JFS_CACHE_DIR=/mnt/jfs/cache
JFS_LOCAL_DIR=/mnt/jfs/alphalib.local

# 跑过 06-meta-migrate.sh 后, 同一个文件也会自动加上:
JFS_META_URL=redis://10.9.100.160:6380/0
JFS_ENV_FILE=/etc/juicefs/alphalib-jfs.env
JFS_REDIS_UNIT=redis-jfs.service
```

Client 节点首次跑 `join.sh` 必须带 `--mount / --cache` + `--meta-host` + `--meta-port`,
join.sh 把它们全写进 `/etc/juicefs-poc.env`,后续重跑只需要 `--meta-host`。

主节点想用非默认路径同理:手写 `/etc/juicefs-poc.env`,然后从 `00-install.sh` 起步。

### MinIO 凭证

只在 `01-provision.sh` 需要。挂载、AUTH、跨节点全程不用。
环境变量优先级:`MINIO_ROOT_USER/PASSWORD` > `MINIO_ACCESS_KEY/SECRET_KEY` > `rclone.conf [39000]`。
带 sudo 必须 `sudo -E` 透传环境。

## 部署

### 主节点:全新部署(干净机器)

```bash
sudo -E bash bootstrap-primary.sh                    # 00 -> 01 -> 02 -> 03 -> 04
sudo -E bash 06-redis-jfs.sh                         # 独立 redis 6380 (空)
sudo -E bash 06-meta-migrate.sh                      # 迁 metadata 6379 -> 6380 (含交互确认)
sudo systemctl start juicefs-alphalib.service
sudo bash status.sh                                  # 期望 15/15 ✓
```

干净机器上 03/06-redis-jfs 顺序不可省 — 03 给业务侧 6379 配 AOF + 密码,06-redis-jfs 起独立 6380。如果机器上 6379 是别的产品在用,03 必须问那个产品 owner 才能跑。

### 主节点:分步(每步幂等)

```bash
sudo -E bash 00-install.sh         # redis + juicefs 二进制
sudo -E bash 01-provision.sh       # MinIO bucket + format + 临时挂
sudo -E bash 02-layout.sh          # sidecar symlink + 两组权限 (顶层 only)
sudo -E bash 03-redis.sh           # 业务侧 redis 6379: 网络化 + 密码 + AOF
sudo -E bash 04-systemd.sh         # systemd 接管挂载 (此时 unit 用 6379)
sudo systemctl start juicefs-alphalib.service

# 分离 metadata 到独立 6380
sudo -E bash 06-redis-jfs.sh       # 起独立 redis 实例 6380, AOF on, 空
sudo -E bash 06-meta-migrate.sh --investigate   # 先看 keys 分类
sudo -E bash 06-meta-migrate.sh    # 真迁: stop juicefs -> MIGRATE -> 改 conf -> start
# 06-meta-migrate.sh 自动重渲染 04-systemd.sh 用 6380, 重启 unit

# 把 ops state (factor_state.json) 也搬进 6380, 多节点强一致
export OPS_STATE_REDIS_PASSWORD=$(sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib-jfs.env)
uv run python -m ops.tools.state_migrate \
  --json ~/.cache/ops/lib/alphalib-juicefs/factor_state.json \
  --url  redis://127.0.0.1:6380/0 \
  --lib  alphalib-juicefs \
  --pass-env OPS_STATE_REDIS_PASSWORD --reset
# config.yaml 已经设了 state.backend: redis, 之后 ops 自动走新 store
```

需要 `/etc/profile.d/ops-umask.sh` (内容 `umask 0002`),否则组写位失效。`02-layout.sh` 和 `join.sh` 会自动建。

### Client 节点

主节点完成 06-meta-migrate 之后才接 client(client 直接连 6380,跳过 6379 阶段)。

```bash
# [主节点] 取 6380 密码 + scp 脚本目录
sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib-jfs.env
scp -r scripts/juicefs-poc <client>:/tmp/

# [client] 首次:--mount / --cache / --meta-host / --meta-port (会写到 /etc/juicefs-poc.env)
ssh <client> 'sudo bash /tmp/juicefs-poc/join.sh \
  --meta-host 10.9.100.160 \
  --meta-port 6380 \
  --mount /mnt/jfs/alphalib \
  --cache /mnt/jfs/cache'
# 交互输密码;或非交互: echo $PASS | sudo bash ... --password-stdin

# 后续重跑 (复用 /etc/juicefs-poc.env 里的路径 + META_URL)
ssh <client> 'sudo bash /tmp/juicefs-poc/join.sh --meta-host 10.9.100.160 --meta-port 6380'
```

`join.sh` 末尾自动校验 sidecar symlink 的 target 等于本机 `JFS_LOCAL_DIR`,跨节点路径不一致会报错并提示。

**⚠ join 后必须补 `alphalib-jfs.env`(否则 ops 跑不了)**:`join.sh` 把密码写进 `/etc/juicefs/alphalib.env`(不带 `-jfs`,client 名),但 `config.yaml` 的 `state.redis.password_file` 指向 `/etc/juicefs/alphalib-jfs.env`(带 `-jfs`,主节点 06-meta-migrate 后的名)。文件名对不上 → `ops` 启动 `sudo grep` 拿到空密码 → `redis.exceptions.AuthenticationError: Authentication required`。挂载本身不受影响,只有 ops state 读写炸。client 不跑 06-meta-migrate 所以不会自动生成带 `-jfs` 的名。补一份(内容同一密码)即可:

```bash
echo "META_PASSWORD=$(sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib.env)" \
  | sudo tee /etc/juicefs/alphalib-jfs.env >/dev/null
sudo chmod 600 /etc/juicefs/alphalib-jfs.env && sudo chown root:root /etc/juicefs/alphalib-jfs.env
```

### Sentinel HA 部署(160/150/144)

主节点 6380 实例起好之后接着部:

```bash
# [1] Replica 节点 150 (在 150 上跑)
sudo bash 07-redis-replica.sh             # MASTER_HOST 默认 10.9.100.160

# [2] 三机各部一个 sentinel (160/150/144 上各跑一次)
sudo bash 08-sentinel.sh                  # MASTER_HOST 默认 10.9.100.160, port 26380, quorum=2

# [3] 三机各跑一次, 把 JFS_META_URL 切到 sentinel 发现
sudo bash 09-switch-meta-url.sh           # 读 sentinel get-master-addr 验证, 改 env, restart unit
```

部署后 `ops/infra/store/redis_store.py` 的 sentinel-aware 客户端会自动跟随 master 切换。failover 实测 9.12 s(`down-after-milliseconds=5000` + 投票 + 同步)。

**踩过的坑**(已固化在脚本):

1. `06-redis-jfs.sh` 现在预设 `masterauth = requirepass` — failover 后原 master 起回来变 replica 时 sentinel 写 `replicaof` 但**不会补 masterauth**,缺了就 `master_link_status=down`
2. `04-systemd.sh` 改 `Wants=` (soft dep) 而非 `Requires=` — 否则 stop 本机 redis-jfs 会带停 JFS unit,跟 HA 初衷违背
3. 144 上若只装 `apt install redis-sentinel` 没装 redis-server,sentinel 是 broken symlink 指向 `redis-check-rdb` 启动 226/NAMESPACE 失败 — `apt install redis-server` 后 `systemctl disable redis-server.service`

### Client 节点跑 ops 命令(自动)

**2026-06-05 上线后**:`ops` 进程自带 password auto-discovery + self-elevate。**无需手动 export 任何环境变量**。

```bash
ops list                                  # 不带 -c, 默认走 config.yaml = JFS
ops submit -u wbai -s 20260605            # 写命令, 自动 sudo 提权 (ops/infra/sudo.py)
ops status AlphaXxx                       # 跨机 redis state 强一致
```

原理(`ops/infra/sudo.py` + `ops/infra/config.py`):

1. `ops` 启动检测 `state.redis.password_file: /etc/juicefs/alphalib-jfs.env`(`config.yaml` 写好的)
2. wbai shell 没 `OPS_STATE_REDIS_PASSWORD` env → `ensure_redis_password()` 通过 `sudo grep` 一次拿密码塞进 env (sudo prompt 一次,5 min cache 命中后续无感)
3. 如果是 write 命令(submit/recheck/check/...) + 检测到 `alpha_src` 是 root-owned → `maybe_elevate()` 自动 `os.execvp('sudo -E --preserve-env=OPS_* ops ...')` 把自身提权成 root,带 env 透传

`alphalib_root` 路径覆盖通过 `OPS_ALPHALIB_ROOT` env(只 144 这种 `/storage/vault/` 布局需要,160/150 用默认 `/tank/vault/alphalib`)。可以写进 `~/.profile`。

> 紧急回退: `ops xxx -c config.prod-legacy.yaml` 走旧 `/mnt/storage/alphalib/` + S3 sync 模型(只用于上线异常时,正常情况不需要)。

LibraryScanner 还需要本地 `~/.cache/ops/lib/<library_id>/{index,metrics,bcorr}.json`。首次 `--refresh` 慢(3000+ 因子 stat 跨网络),建议从主节点 scp:

```bash
# 主节点跑
scp ~/.cache/ops/lib/alphalib-juicefs/{index,metrics,bcorr}.json \
    wbai@<client>:.cache/ops/lib/alphalib-juicefs/
```

> **2026-06-04 之后**:`index.json` 已经不再序列化 src_path/dump_path/pnl_path(`INDEX_VERSION=6`,只存 name/author/has_pnl 等业务字段),路径在 `from_dict` 时按本机 `Config` 现场拼。所以从主节点 scp 过来的 cache 在 144(磁盘布局不同)上**直接可用**,`ops info` 会显示本机视角的路径(`/storage/vault/alphalib/...`),`ops list --refresh-datasources` 也能正确读 .py。

> **cache 失效策略 (2026-06-04 之后)**:`_load_index` 不再走粗暴 1h TTL,改成跟 `alpha_src` **目录 mtime** 比较 — 新增/删除因子(顶层 mkdir/rmdir)会更新 mtime → cache 自动失效;改某个因子内部的 .py/Readme 不动 mtime → cache 复用。7 天 TTL 仅作"目录长期不动也兜底刷新"的安全网。日常使用基本永不无故重 scan。

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
4. **修正 ownership** — `alpha_src: chown -R root:alpha-core`;`alpha_pnl/feature: chown -R root:alpha-data`;dir 加 setgid。集中运维,所有写都走 sudo
5. **对账** — 文件数 / 字节数 / 抽样 N 个 md5 (默认 10,环境变量 `SAMPLE_N=50` 可调)

## 健康检查

```bash
sudo bash status.sh
```

输出 mount / cache / redis (AUTH + AOF) / JFS staging 队列 + 错误计数 / sidecar symlink 一致性 / 组成员 / umask / 数据目录概览。任何 ✗ 项 exit 1。

自动读 `JFS_META_URL` / `JFS_ENV_FILE`,所以分实例前后都对得上 — 跑过 06-meta-migrate 之后 status.sh 自动探 6380。

## 验证

```bash
bash verify.sh             # 全套
bash verify.sh basic       # 100MB IO / flock / stat / 可见性
bash verify.sh memmap      # alpha_feature 模式仿真
bash verify.sh git         # 500 commit + log/blame/status/diff,本地 ZFS 对照
sudo -E bash verify.sh redis-fail   # Redis kill 注入(需 sudo)
```

## 权限模型

**集中运维**:owner 一律 root(recycle 子目录除外),所有写都通过 sudo。group 仅作跨机一致性 label,不授予写权限。**不用 POSIX ACL**,只靠 setgid 继承组。

| 组 | gid | 成员 | 作用 |
|---|---|---|---|
| `alpha-core` | 59000 | wbai | 读 alpha_src / staging |
| `alpha-data` | 59001 | wbai | 读 alpha_pnl / alpha_feature / alpha_dump |

```
JFS  /tank/vault/alphalib/        root:alpha-data 2755
├── alpha_src/       root:alpha-core 2750     core 读, 仅 sudo 写
├── alpha_pnl/       root:alpha-data 2755     data 读, 仅 sudo 写
├── alpha_feature/   root:alpha-data 2755     data 读, 仅 sudo 写
├── alpha_dump  →    /tank/vault/alphalib.local/alpha_dump   (symlink)
├── staging     →    /tank/vault/alphalib.local/staging      (symlink)
└── recycle     →    /tank/vault/alphalib.local/recycle      (symlink)

本地 /tank/vault/alphalib.local/   root:alpha-data 2755
├── staging/         root:alpha-core 2750     core 读, 仅 sudo 写
├── alpha_dump/      root:alpha-data 2755     data 读, 仅 sudo 写
└── recycle/         root:root       1755     sticky
    └── <unixId>/    <unixId>:<grp>  0700     只用户自己
```

- gid 选 59xxx(GID_MAX=60000 以下,避开 7/8/9000 常见段)
- 所有 ops 写路径(`submit / resubmit / recheck / check / pack / rm` 等)必须 sudo 跑;Phase C 之前需要补 sudo wrapper 让 `uv run ops ...` 自动 elevate
- `recycle` 是研究员私有,嵌套一层 unixId 由用户自己写;sticky bit 防互删
- `alpha-core/alpha-data` 组 membership 主要用途:跨机器统一 group label(NFS / FUSE 上 gid 数字必须各机一致),并保证未来若放开 group write 位时不需要重 chown

## 验证结果

| 项 | 实测 |
|---|---|
| 100 MB 顺序写 | 333 ms (~300 MB/s) |
| 100 MB re-read (cache 命中) | 176 ms |
| flock 跨进程串行 | 5 ms 切换 gap |
| 1000 小文件 ls | 15 ms |
| memmap 日增 1 行 | 35 ms |
| 跨进程 reopen 一致性 | bit-level OK |
| 500 提交 git | commit 75ms,log/blame/status/diff < 250ms |
| Redis kill | JuiceFS 不 hang,所有 syscall 立刻 EIO → **已上 Sentinel HA (B-8)**: failover 9.12 s |
| 服务器重启 (writeback drain 中) | 数据完整(ExecStop 链 sync + ZFS cache 续传)|
| Redis dump.rdb 恢复 | 69111 keys 从备份完整恢复 |
| 跨节点 (160 ↔ 150) visibility + flock | bit-level OK, 锁互斥, 释放后另一端立刻获得 |
| Redis Sentinel failover (160 master → 150) | 9.12s 自动 promote, ops/JFS 透明重连 (2026-06-05) |
| 三机数据对账 | alpha_src 3217 / pnl 3153 / feature 5876 / 936 GB bit-level 一致 (2026-06-05) |
| `ops check` 完整 7-stage on JFS + sentinel state | 通过 (2026-06-05) |

## 当前进度

**已完成 (2026-06-05 上线)**:

- [x] sidecar 改 symlink + 集中运维权限模型(`02-layout.sh`,owner 一律 root,group 仅作 label)
- [x] systemd unit 接管挂载(`04-systemd.sh`,`Wants=` soft dep 不带停)+ umount 三级 fallback
- [x] Redis 网络化 + AUTH + AOF + masterauth 预设(`03-redis.sh` / `06-redis-jfs.sh`)
- [x] Client 节点接入(`join.sh`,150 / 144 实测通过)
- [x] 跨节点可见性 + flock 真锁验证
- [x] 数据迁移脚本化(`05-migrate.sh`)+ 三机对账完美
- [x] 健康检查(`status.sh`)+ 跨段 du/find 超时
- [x] 服务器异常重启场景验证(writeback drain 中重启数据完整)
- [x] Redis metadata 灾备路径验证(空 AOF 误覆盖事故,从 dump.rdb 完整恢复)
- [x] 独立 redis 实例 6380(`06-redis-jfs.sh` / `06-meta-migrate.sh`)
- [x] 第三节点 144 接入(磁盘布局不同 `/storage/vault/`,软链对齐 sidecar 绝对路径)
- [x] `ops` state 后端切 redis(`ops/infra/store/redis_store.py` + `state.backend: redis`)
- [x] **Redis Sentinel HA**(`07-redis-replica.sh` / `08-sentinel.sh` / `09-switch-meta-url.sh`,3 sentinel:26380,failover 9.12 s,2026-06-05)
- [x] **JFS 全量上线**(默认 config 切到 JFS,旧 prod 存 `config.prod-legacy.yaml` 回退,2026-06-05)
- [x] **ops self-elevate sudo wrapper**(`ops/infra/sudo.py`,write 命令检测 root-owned alpha_src 自动提权,2026-06-05)
- [x] **ops 密码 auto-discovery**(`state.redis.password_file` + sudo grep,fresh shell 无需手动 export,2026-06-05)

**剩余 (nice-to-have)**:

- [ ] 写入重试 wrapper:failover 5-10 s 窗口的 redis EIO retry(3 次 backoff 2/5/10 s)
- [ ] `LibraryScanner` index/metrics/bcorr cache 进 redis(目前仍 per-machine `~/.cache/ops/lib/`,跨节点 first-scan 慢 + count 偶尔差 1-2)
- [ ] `ops sync push/pull` 加 deprecation warning + 整体退役(留 `sync verify` 作巡检)
- [ ] sudo NOPASSWD wrapper(root-owned binary + 限定命令,完全去掉 sudo prompt)
- [ ] MinIO root key rotation(PoC 用 root key 暴露过日志)
- [ ] 删 prod 数据 `/mnt/storage/alphalib/`(上线稳定一周后)

## 失败回退

JFS 跟旧 prod `/mnt/storage/alphalib/` 并存(后者已不主用,留作 backup)。`config.prod-legacy.yaml` 仍指向旧路径,**任何 ops 命令加 `-c config.prod-legacy.yaml` 即回到 S3 sync 模型**,JFS 这边不动。

完全放弃(主节点):`sudo -E bash teardown.sh --purge` — 卸卷 + 删 bucket + 删 cache。
Client 节点单独退出:`sudo systemctl disable --now juicefs-alphalib.service`。

## 故障排除

### 已知约束:`redis-server.service:6379` 还在跑但 JFS 不动它

server-160 有两个 redis 实例:`redis-server.service:6379`(alphalib biz 业务)和 `redis-jfs.service:6380`(JuiceFS metadata + ops state)。06-meta-migrate.sh 之后,JuiceFS 跟 ops 全部连 6380,6379 上没有 JFS 相关 key,但**仍然属于 alphalib biz**,不能随便停或 disable。

确认方式:`ss -tn '( sport = :6379 or dport = :6379 )'`,看到 biz 节点的连接就说明有人用。

### 卷已 format 还想重 format

`01-provision.sh` 检测到卷已存在会跳过 format(避免凭证再进 ps)。强制重 format 先销:
```bash
sudo -E bash teardown.sh --purge   # 必须 --purge 才删 metadata
sudo -E bash 01-provision.sh
```

### Redis 密码 rotate(主节点)

`03-redis.sh` 和 `06-redis-jfs.sh` 都复用已有 env 文件,不会自动换密码。强制 rotate:
```bash
# 业务侧 6379 (alphalib biz, 谨慎: 影响业务方)
sudo systemctl stop juicefs-alphalib.service
sudo rm /etc/juicefs/alphalib.env
sudo -E bash 03-redis.sh
sudo systemctl start juicefs-alphalib.service

# JFS 专用 6380
sudo systemctl stop juicefs-alphalib.service
sudo rm /etc/juicefs/alphalib-jfs.env
# 直接改 redis-jfs.service.conf 里的 requirepass 然后 redis-cli config set requirepass <new>
# 06-redis-jfs.sh 没有 rotate 模式, 因为是新实例,删 env 重跑会和 conf 里的旧密码冲突。
# 临时方案:redis-cli ... config set requirepass <new>, config rewrite, 再改 env file
```
client 上失效现象:`juicefs-alphalib.service` 反复 EIO / restart,旧密码不通过 AUTH。修复:
```bash
sudo rm /etc/juicefs/alphalib-jfs.env
sudo bash /tmp/juicefs-poc/join.sh --meta-host <主节点 IP> --meta-port 6380   # 提示输新密码
```

### umount 卡住 (systemd stop 不动)

`04-systemd.sh` ExecStop 已带三级 fallback (`juicefs umount` → `fusermount -uz` → `umount -l`)。
完全卡死(连 lazy 都不行)只剩重启。

### sidecar symlink 不一致

`status.sh` / `join.sh` 末尾会报 `JFS 里 symlink target != 本机 JFS_LOCAL_DIR`。

JFS 里 alpha_dump / staging / recycle 是 symlink,target 是**绝对路径**(02-layout.sh 在主节点写死,例如 `/tank/vault/alphalib.local/alpha_dump`)。这个 target 跨节点共享,所有 client 必须有这个绝对路径才能解析。

**情况 A:本机 `JFS_LOCAL_DIR` 跟主节点不一致(同一个 mount root)**

改 `/etc/juicefs-poc.env` 让 `JFS_LOCAL_DIR` 跟主节点完全一致,重跑 `join.sh`。

**情况 B:本机磁盘布局完全不同(例如 144:`/storage/vault/`,主节点:`/tank/vault/`)**

不能改 mount root(本机就没那个路径),做软链让 JFS 的绝对路径在本机能解析:

```bash
# 1. 软链 /tank/vault/alphalib.local -> 本机实际位置
sudo mkdir -p /tank/vault
sudo ln -sn /storage/vault/alphalib.local /tank/vault/alphalib.local

# 2. host env JFS_LOCAL_DIR 改成跟 JFS symlink target 一致 (status.sh check 用)
sudo sed -i 's|^JFS_LOCAL_DIR=.*|JFS_LOCAL_DIR=/tank/vault/alphalib.local|' /etc/juicefs-poc.env

# 3. 验
readlink -f /storage/vault/alphalib/alpha_dump   # -> /storage/vault/alphalib.local/alpha_dump
```

JFS_MOUNT 留在 `/storage/vault/alphalib` 没问题(mount 点跟 sidecar target 路径独立)。**实测 2026-06-04 在 144(`/storage/vault/`)上跑通过这个流程**。

**情况 C:`readlink` 多了个尾斜杠(`alpha_dump/` vs `alpha_dump`)**

之前 `ln -sf <target>/ <link>` 留下的。修法:`sudo rm <link> && sudo ln -sn <target_no_slash> <link>`。

> **长期 TODO**:02-layout.sh 应该改用相对路径 symlink(`alpha_dump → ../alphalib.local/alpha_dump`),消除"每加一个磁盘布局不同的节点就要做绝对路径软链"的负担。改动要 stop 所有节点的 unit + 删旧 symlink + 用相对路径重建,留下次维护窗口做。

### 重跑 02-layout.sh 后某因子作者权限丢了

不应该发生(`apply_top_dir` 只动顶层)。如果之前跑过老版本(recursive chown),用 `05-migrate.sh` 重做 alpha_src 那一段(它保留作者 user)。

### 服务器异常重启 / 突然断电

JFS 数据保护是分层的,任一层失守都不立刻丢,但要逐层确认:

1. **chunk 数据(JFS cache)**:`04-systemd.sh` 渲染的 ExecStop 三级链(`juicefs umount` → `fusermount -uz` → `umount -l`)在 systemd 正常关机会触发 sync;ZFS cache 持久,重启后 unit 自动起 + 从 cache 续传未上传的 staging block。**前提**:cache 不能放 tmpfs / 内存盘
2. **元数据(redis)**:必须 AOF on (`appendfsync everysec`,最多丢 1s 写入)。AOF off + 异常断电 = 可能丢 RDB save 周期内的写入(默认 1h/15min/1min)。`status.sh` 会报 `AOF off ✗`

恢复检查(顺序):

```bash
# 1. 服务起来了吗
systemctl is-active juicefs-alphalib.service redis-jfs.service
mountpoint /tank/vault/alphalib

# 2. 健康一把梭
sudo bash status.sh

# 3. staging 是否在续传, errors 是否在累积
grep -E 'staging|object_request' /tank/vault/alphalib/.stats

# 4. 抽样读, 验证 chunk 完整
ls /tank/vault/alphalib/alpha_feature | wc -l                                # 文件数对得上源吗
find /tank/vault/alphalib/alpha_feature -type f | shuf -n 5 | xargs md5sum   # 抽样能算 md5 = chunk 完整
```

`staging_block_errors=0` 才是真没丢。`object_request_errors` 是累积重试值(看比例,见踩过的坑)。

### 切 config (`-c config.yaml`) 后 `ops list` 看不到 status / fail_stage 列

**2026-06-04 之后**:`config.yaml` 已经把 state 切到 redis,所有节点共享同一份 state。任何节点跑 list 都能看到颜色和 `fail_stage`,**前提是**:

1. `OPS_STATE_REDIS_PASSWORD` 已 export(从 `/etc/juicefs/alphalib-jfs.env` 或 client 上的 `alphalib.env` 取)
2. 本机能 TCP 通主节点 6380

如果两个前提都满足还是没颜色,看下面 "RedisStateStore 连不上 / NoneType has no attribute 'name'"。

**历史方法(2026-06-04 之前 / state.backend=json 时)**:把 prod 的 JSON state 拷一份过来,两份独立不互通。已废弃。

### RedisStateStore 连不上 redis-jfs:6380

现象:`ops list -c config.yaml` 抛 `redis.exceptions.AuthenticationError: HELLO must be called...` 或 `ConnectionError`。

检查项:

```bash
# 1. env 真传进 ops 进程?
echo "PASS_LEN=${#OPS_STATE_REDIS_PASSWORD}"   # 期望 48
uv run env | grep OPS_STATE                    # 子进程视角

# 2. TCP 通?
timeout 3 bash -c 'echo > /dev/tcp/10.9.100.160/6380' && echo OK

# 3. 直接 ping
redis-cli -h 10.9.100.160 -p 6380 -a "$OPS_STATE_REDIS_PASSWORD" --no-auth-warning ping
```

如果 ping 报 `HELLO must be called with the client already authenticated`,你的 ops 还是老代码(`from_url`)— pull 到 commit `fc4d8f8` 或之后版本(改用 `Redis(protocol=2)` 直接构造,绕开 redis-py 8.x 的 HELLO 默认)。

### `ops list` 在 client 节点("No factors found")

LibraryScanner 在 `~/.cache/ops/lib/<library_id>/index.json` 留 cache。client 节点第一次跑要 iterdir 3000+ 因子 + stat 每个 .py/.xml,跨节点 attr cache 冷 = **3-10 分钟**(看着像卡)。

加速:从主节点 scp cache 过来(state 进 redis 后,这个 cache 是唯一的本地依赖)。

```bash
# 在主节点跑
scp ~/.cache/ops/lib/alphalib-juicefs/{index,metrics,bcorr}.json \
    wbai@<client>:.cache/ops/lib/alphalib-juicefs/
```

注意 `index.json` 里 `src_path` 是主节点视角(`/tank/vault/...`),client 上是 `/storage/vault/...`。`list` 不读 src_path,所以没事;`info`/`status` 一类需要打开文件的命令需要 client 自己 `--refresh` 一次。长期 TODO:LibraryScanner cache 也搬 redis,跨节点直接共享。

### Redis 数据丢失 / `database is not formatted` 错误

现象:`juicefs-alphalib.service` 反复 restart,journal 里 `<FATAL>: database is not formatted`,但 redis 还能 ping。`redis-cli dbsize` 跟期望差很多。

根因可能:

1. **空 AOF 优先于 RDB**:Redis 7 在 `appendonly` 从 no 切 yes 时如果 conf 直接改 + restart,会优先空 AOF 加载,RDB 被绕过(详见踩过的坑)
2. **SHUTDOWN SAVE 写脏 dump.rdb**:`systemctl stop redis` 时如果内存不全,save 出来的 dump.rdb 会覆盖原本完好的(详见踩过的坑)
3. **MIGRATE 漏 keys**:`06-meta-migrate.sh` 白名单不全, 漏迁的 JFS keys 让新 unit 报 not formatted。`s{inode}` (symlink target) 之类是高发漏点(详见踩过的坑)

恢复(参考 `/var/backups/redis-recover-*` 路径,假设 dump.rdb 备份还在):

```bash
# 1. stop redis 务必用 SHUTDOWN NOSAVE,不要 systemctl stop
ENV=/etc/juicefs/alphalib-jfs.env   # 或 alphalib.env, 看哪边坏
sudo bash -c ". $ENV && redis-cli -h <host> -p <port> -a \"\$META_PASSWORD\" config set save ''"
sudo bash -c ". $ENV && redis-cli -h <host> -p <port> -a \"\$META_PASSWORD\" shutdown nosave"

# 2. 临时 systemd override 防 auto-restart
UNIT=redis-jfs.service   # 或 redis-server.service
sudo mkdir -p /run/systemd/system/$UNIT.d
echo -e '[Service]\nRestart=no' | sudo tee /run/systemd/system/$UNIT.d/recover.conf
sudo systemctl daemon-reload

# 3. 把好的 dump.rdb 搬回去, appendonlydir 里的 .aof 文件全清掉
RDIR=/var/lib/redis-jfs   # 或 /var/lib/redis
sudo cp /var/backups/redis-recover-<TS>/dump.rdb $RDIR/dump.rdb
sudo chown redis:redis $RDIR/dump.rdb
sudo rm -f $RDIR/appendonlydir/*

# 4. 启动 + 验证 dbsize
sudo systemctl start $UNIT
# 不要改 conf 重启来开 AOF, 而是运行时:
redis-cli ... config set appendonly yes && redis-cli ... config rewrite

# 5. 清理 override
sudo rm /run/systemd/system/$UNIT.d/recover.conf
sudo systemctl daemon-reload
```

实测 2026-06-04 在 160 上触发过一次,69111 个 JFS keys 全恢复。

### MIGRATE 后 mount 起来但 sidecar EIO / readlink 报错

现象:`mountpoint` ✓ 但 `readlink /tank/vault/alphalib/alpha_dump` Input/output error。

根因:`06-meta-migrate.sh` 白名单漏了 symlink keys。`s14365` 之类的 key 名容易被误归类为 `session*`(JFS schema 里 session 是 `sessionHB` / `sessions` / `session{sid}`,跟 `s{inode}` 不是一个家族)。

修法:把漏掉的 keys 手动迁过去
```bash
PASS_BIZ=$(sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib.env)
PASS_JFS=$(sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib-jfs.env)
redis-cli -p 6379 -a "$PASS_BIZ" --no-auth-warning --scan | grep -v sessionInfos > /tmp/missed.txt
redis-cli -p 6379 -a "$PASS_BIZ" --no-auth-warning MIGRATE 127.0.0.1 6380 "" 0 5000 REPLACE AUTH "$PASS_JFS" KEYS $(cat /tmp/missed.txt | tr '\n' ' ')
sudo systemctl restart juicefs-alphalib.service
```

或者改 `06-meta-migrate.sh` 的 `BIZ_KEYS` / `BIZ_PREFIXES`,把业务侧的真实 keys 加进去,确认 `--investigate` 输出干净后再 apply(反向白名单更稳)。

## 踩过的坑

- `rclone.conf` 的 `no_check_bucket = true` 让 `rclone mkdir` 假成功;`01-provision.sh` 加真 PutObject 兜底
- rclone `:s3,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL;改用临时 config 文件
- `juicefs format` 日志 `minio://http://endpoint/bucket/bucket/...` 看着重复,实际 S3 调用没问题
- `mount --writeback` 先落 cache 异步上传,延迟低但断电丢未上传数据;`05-migrate.sh` 等 `staging_blocks=0` 才进对账
- MinIO 不可达时挂载 hang,先 `curl ${MINIO_ENDPOINT}/minio/health/live`
- 改 redis.conf 前必须先 stop juicefs unit,否则现挂载在 AUTH 切换瞬间全 EIO。`03-redis.sh` 已改成运行时 `config set`,不需要重启 redis
- `usermod -aG` 不影响已有 SSH session;验证用 `sg <group> -c <cmd>`,或重连
- 密码出现在 `juicefs mount` 的 cmdline 会被 `ps` 看到;`04-systemd.sh` 通过 `EnvironmentFile=` 注入 `META_PASSWORD`,URL 不带密码
- `juicefs format` 的 `--access-key/--secret-key` 仍然会进 ps;`01-provision.sh` 检测到卷已存在直接跳过 format,把暴露窗口压到第一次部署
- `02-layout.sh` 老版用 `chown -R/chmod -R` 会毁掉作者 ownership;现在只改顶层 + setgid,内层 owner 由 `05-migrate.sh` 或 ops/submit 管
- 服务器异常重启数据没丢 = 三件事一起救:(a) systemd 走 ExecStop 三级链干净 unmount (b) JFS cache 在持久 FS (ZFS) 而不是 tmpfs (c) redis AOF on。任一缺一就有窗口风险。实测 2026-06-03 服务器重启时 staging=92912 (362G) 还在 cache,重启后从 cache 续传成功,数据完整;但 AOF 当时还是 off,RDB 周期救了一把,纯运气
- `juicefs_object_request_errors` 是累积重试计数,不是丢数据指标。看比例:`errors / object_request_durations_*_total`,2-3% 在 MinIO 偶发限流/抖动是正常水位。真正的丢数据指标是 `juicefs_staging_block_errors`,必须为 0
- `ops` 的 state (`factor_state.json`) 在 per-machine `~/.cache/ops/lib/<library_id>/`,不在 JFS 里也不被 rsync 带过来。换 config(改了 library_id)之后 list 看到的因子没 status,是这个原因。长期方案待定(state 进 JFS / 沿用 sync)
- **Redis 7 切 AOF 雷**:在 `appendonly no` 状态下跑着的 redis,如果改 conf 加 `appendonly yes` + restart,redis 会 **创建空 AOF base + 优先于 RDB 加载**,数据归零(redis-cli 还能 ping、persistence info 显示 aof_enabled=1,但 dbsize=0)。dump.rdb 还在但被绕过。修法:不用重启,运行时 `config set appendonly yes` + 等 BGREWRITEAOF + `config rewrite` 写回 conf。03-redis.sh / 06-redis-jfs.sh 都是这个流程。实测 2026-06-04 在 server-160 触发过一次,69111 个 JFS keys 走运因为 dump.rdb 完好可恢复(`/var/backups/redis-recover-*`)
- **多产品共用 redis db 的风险**:server-160 上 redis 6379/db0 原来同时被 alphalib 业务(session 数据)和 JuiceFS PoC 共用。代价:(a) 任何重启都得跨产品协调,(b) `redis-cli flushdb` 类操作影响面是产品集合并集 (c) `systemctl stop redis` 会触发 SHUTDOWN SAVE,如果当时 redis 内存不全(比如启动失败的退化状态),会用脏内存覆盖 dump.rdb。**分库 (db0/db1) 不够**,因为持久化文件还是共享的,SAVE/OOM/SHUTDOWN 仍跨产品。必须分实例 — `06-redis-jfs.sh` 起独立 redis-jfs.service:6380,`06-meta-migrate.sh` 把 JFS keys 整体搬过去
- **`systemctl stop redis` 默认触发 SHUTDOWN SAVE**:redis-server.service 退出时 redis 会做一次 RDB save。如果当时内存里数据不完整(刚 boot 失败、还没 load 完、被人 flushdb 一半),这次 save 会写脏 dump.rdb,后续启动数据全废。安全停 redis 用 `redis-cli SHUTDOWN NOSAVE`,或者先 `config set save ''` 关 RDB 触发器再 stop
- **JuiceFS Redis schema 比 docs 多 + key 名前缀容易撞车**:juicefs/pkg/meta/redis.go 里能找到 inode / chunk / dir 等核心 prefix,但 `lastCleanupTrash/Files/Sessions`、`nextCleanupSlices/nextTrash`、`dirUsedInodes/Space/DataLength`、`x{inode}` (xattr)、`s{inode}` (symlink target) 这些 docs 没列。其中 `s{inode}` 是高危坑:正则 `^s[a-z]` 看上去能匹配 session,实际跟 symlink target 撞名,把它误归类成 biz key = sidecar 全部 EIO。`06-meta-migrate.sh` 改成反向白名单(声明 biz 的 keys, 其他都迁) — 漏迁的代价(EIO)比多迁(无害)高得多
- **同一个 key 名两个产品都在用**:alphalib biz 和 JuiceFS 都有 `sessionInfos`(虽然 schema 不同),但因为分实例了,两边互不干扰。教训:看到 key 名冲突先想想是不是真的同一个东西
- **不能在 fuse mount 的子目录里重启 fuse unit**:`cd /tank/vault/alphalib/...` 然后 `systemctl restart juicefs-alphalib.service`,unit 重启过程中你 shell 的 cwd 在 fuse 上变 stale,fork/exec 拿到 ESTALE,shell 看起来"卡住"或"突然退出"。修法:操作 fuse unit 前先 `cd ~`
- **join.sh 老版本不写 `JFS_META_URL` 进 `/etc/juicefs-poc.env`**:导致 client 上 status.sh 走 config.sh 默认值 `127.0.0.1:6379`,但 mount 实际连的是远端 6380(因为 join.sh 调 04-systemd 时通过 env 传过去了)。修法:c70908c 之后 join.sh 把 `JFS_META_URL` + `JFS_REDIS_LOCAL=0` 都写进 host env,单一真值
- **Redis 7 conf 不支持行尾注释**:`appendonly yes  # 注释` 会被 parser 当成 `appendonly yes "#" "注释"` 报 wrong number of arguments,redis 起不来。注释只能独占一行
- **`ls -alF` 在 mount 根上报 `.stats/.accesslog/.config/.trash: No data available`**:这是 JuiceFS 的 4 个内部 pseudo-files,只支持 `cat`(`.stats` / `.config` / `.accesslog`)或 `ls .trash/`(已删文件),对它们调 `stat()` 返回 ENODATA。`ls` 默认对每条目 stat 取大小所以报错。不是数据问题,不影响业务。绕开:`ls -l --hide='.[a-z]*'` 或干脆不带 -F
- **redis-py 8.x 默认 HELLO 跟 requirepass-only server 不兼容**:8.x 默认发 RESP3 + HELLO 握手,如果 redis server 只配了 `requirepass`(没用 ACL `default` user),HELLO 会被拒绝(`HELLO must be called with the client already authenticated`)。修法:绕开 `from_url`,直接 `redis.Redis(host=..., port=..., password=..., protocol=2)`,protocol=2 强制走经典 AUTH-then-commands。`from_url` 在 8.x 上对 `protocol` kwarg 的处理被观察到会丢。代码见 `ops/infra/store/redis_store.py` 的 `__init__`
- **`OPS_<VAR>` env 在 ssh 子进程要重 export**:`Config._resolve_vars` 支持任何 `vars:` 块 key 的 `OPS_` 前缀环境变量覆盖。客户端节点(磁盘布局跟主节点不同)必须先 `export OPS_ALPHALIB_ROOT=/storage/vault/alphalib`(或对应路径)再跑 `ops *`,否则 alpha_src 解析成主节点的 `/tank/vault/...`,本机不存在 = `No factors found`。永久化方案:写 ~/.bashrc 或 wrapper 脚本
- **per-machine cache 跟 cross-node state 的混合**:state 进 redis 之后,跨节点强一致;但 `~/.cache/ops/lib/<library_id>/{index,metrics,bcorr}.json` 还是 per-machine(因为 LibraryScanner 还没改)。client 节点首次 `--refresh` 慢(3000+ stat 跨网络),要么等要么 scp 主节点的 cache 过来。长期方向:LibraryScanner cache 也搬 redis
- **`index.json` 曾经序列化主节点绝对路径(已修复)**:`INDEX_VERSION<=5` 把 `src_path/dump_path/pnl_path` 当字符串存进 cache,从主节点 scp 过来的 cache 在 144 上指向不存在的 `/tank/vault/...`,导致 `ops info` 打印 stale 路径,`ops list --refresh-datasources` silently 解析失败。**v6(2026-06-04 后)** 路径不再进 cache,`from_dict(data, config)` 拿当前节点的 `Config.alpha_src/alpha_dump/alpha_pnl` 现场拼,跨节点 cache scp 直接可用。旧 v5 cache 加载时被自动拒绝并重建
- **`usermod -aG` 不影响已开的 SSH session 的 supplementary groups**:把 wbai 加到 alpha-core / alpha-data 之后,**当前已经登录的 SSH session 看不到新组**,所有走 group 权限的访问都 EACCES(典型现象:`ls /tank/vault/alphalib/staging` 报 Permission denied,但其他登录方式 / `sudo -u wbai ls ...` 能看到)。`id wbai` 显示对了但 `id -nG`(当前 process supplementary groups)还是旧的。修法:**退出当前 SSH 重连**,或 `exec sg alpha-core bash`,或 `newgrp alpha-core`(单 shell 临时)。`02-layout.sh` / `join.sh` 跑完都应该提示重连
