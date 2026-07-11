# DISCOVER 170 环境采集结果(只读)

采集日期 2026-07-11,目标机 **server-170**(10.9.100.170)。
执行:160 经 SSH 远程 `bash -s`(BatchMode,无 TTY)。全部只读,未改任何东西。

**密码打码说明**:本次采集输出中**未出现任何密码/密钥** —— JFS env 用明文
`redis://`(无 password 字段),故无 `****` 打码项。

## 采集摘要(给 migrate-mount 实现者)

- **env 键名**(`/etc/juicefs-poc.env`):`JFS_MOUNT` / `JFS_CACHE_DIR` /
  `JFS_LOCAL_DIR` / `JFS_META_URL` / `JFS_REDIS_LOCAL` / `JFS_CACHE_SIZE_MB`
  —— migrate 改写这 6 个键即覆盖挂载点/cache/sidecar 路径。
- **unit 不读 juicefs-poc.env**:`juicefs-alphalib.service` 的 `EnvironmentFile`
  指向 `/etc/juicefs/alphalib.env`,且 `--cache-dir` / meta URL / 挂载点**硬编码在
  ExecStart 里**(不是变量)。**改 env 不够,必须重渲染/改写 unit** ——
  这是本次采集对实现方式的关键判定。
- **待搬存量极小**:sidecar `/ext4/alphalib.local` = 16K(近空),cache
  `/ext4/jfs-cache` = 4.0K(近空)。170 上没有实际 sidecar/cache 数据要搬。
- **目标盘 `/nvme125`**:12T ZFS,已用 256K(1%);已存在子 dataset
  `nvme125/{datasvc,checkpoint,alpha_dump,alpha_pnl}`,且 alpha_dump/alpha_pnl/
  checkpoint **已被 wbai:alpha-data 填过**(6944/6945 项,Jul 10 时间戳)——
  目标盘非空白,migrate 需处理已存在目录。
- **sudo 无 NOPASSWD、SSH 无 TTY**:所有 `sudo` 命令报
  `sudo: a password is required`(group 1 的 `/etc/juicefs/` 列目录、group 6 的
  `lsof +D`)。migrate 若要在 170 跑写操作,需解决提权(NOPASSWD wrapper 或本机执行)。
- **`uv` / `ops` 不在 PATH**:`command -v uv` 空、`command -v ops` 空;仅
  `~/.local/bin/ops`(uv tool binary)存在。170 上 `uv run ops` 不可用,
  同 150(见 VERIFY-OPS-SETUP-RESULT)。
- **`/mnt/storage/alphalib` 兼容软链在 170 不存在**(group 6 无输出)。
- **status.sh 3/12 异常**:meta URL TCP 探测失败(status.sh 拿 `mymaster,...`
  整串当 host 探,是脚本探测方式问题,非挂载故障 —— 挂载 active、writeback 空闲);
  sidecar 软链 `../alphalib.local/...` 相对目标与 env `JFS_LOCAL_DIR` 期望的绝对
  `/ext4/alphalib.local/...` 判定不符(status.sh 的字面比较)。
- PG(10.9.100.160:15432)端口可达。

---

## 逐条原文

### GROUP 1:现役 JFS 部署声明

```
--- cat /etc/juicefs-poc.env ---
# Per-host JuiceFS path override. Written by join.sh.
JFS_MOUNT=/ext4/alphalib
JFS_CACHE_DIR=/ext4/jfs-cache
JFS_LOCAL_DIR=/ext4/alphalib.local
JFS_META_URL=redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0
JFS_REDIS_LOCAL=0
JFS_CACHE_SIZE_MB=102400
--- sudo ls -la /etc/juicefs/ ---
sudo: a terminal is required to read the password; either use the -S option to read from standard input or configure an askpass helper
sudo: a password is required
--- sudo grep -c . /etc/juicefs/*.env ---
sudo: a terminal is required to read the password; either use the -S option to read from standard input or configure an askpass helper
sudo: a password is required
```

> `/etc/juicefs/*.env` 的文件名/行数因 sudo 无 TTY 未取到(报错原样保留)。unit 里
> `EnvironmentFile=/etc/juicefs/alphalib.env` 证实该文件存在(见 GROUP 2)。

### GROUP 2:systemd unit 结构

```
--- systemctl cat juicefs-alphalib ---
# /etc/systemd/system/juicefs-alphalib.service
[Unit]
Description=JuiceFS mount /ext4/alphalib
After=network-online.target
Wants=network-online.target


[Service]
Type=forking
EnvironmentFile=/etc/juicefs/alphalib.env
ExecStartPre=/bin/mkdir -p /ext4/alphalib
ExecStart=/usr/local/bin/juicefs mount \
  --cache-dir=/ext4/jfs-cache \
  --cache-size=102400 \
  --writeback \
  --background \
  redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0 /ext4/alphalib
# 三级 fallback: 标准 umount → fusermount lazy → umount -l
# 防有进程持有 mount 时卡 deactivating。前两步失败也继续 (- 前缀 + bash || 链)。
ExecStop=/bin/bash -c '/usr/local/bin/juicefs umount /ext4/alphalib 2>/dev/null || /bin/fusermount -uz /ext4/alphalib 2>/dev/null || /bin/umount -l /ext4/alphalib 2>/dev/null || true'
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
--- is-enabled / is-active ---
enabled
active
```

> **关键**:`--cache-dir` / meta URL / 挂载点均硬编码在 ExecStart,不引用
> EnvironmentFile 的变量。migrate 改 `/etc/juicefs-poc.env` 无法改变实际挂载点,
> **必须改写 unit**(或改 `/etc/juicefs/alphalib.env` 且把 unit 改成引用变量)。

### GROUP 3:挂载现状 + 健康基线

```
--- grep -i juicefs /proc/mounts ---
JuiceFS:alphalib /ext4/alphalib fuse.juicefs rw,nosuid,nodev,relatime,user_id=0,group_id=0,default_permissions,allow_other,max_read=131072 0 0
--- status.sh ---
=== Mount ===
  ✓ /ext4/alphalib mounted (JuiceFS:alphalib)
  ✓ juicefs-alphalib.service active

=== Cache ===
  ✓ cache dir 存在 (/ext4/jfs-cache), free 3.9T (用 sudo 看 used)

=== Redis ===

=== JFS internal stats ===
  ✗ TCP mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380 不通
  ✓ writeback 空闲 (staging=0 writing=0)
  ✓ staging_block_errors=0
  ✓ object_request_errors=0
    object_request_uploading=0 (并发 S3 PUT)

=== Sidecar (/ext4/alphalib.local) ===
  ✗ alpha_dump: symlink -> ../alphalib.local/alpha_dump, 本机 JFS_LOCAL_DIR 期望 /ext4/alphalib.local/alpha_dump (改 /etc/juicefs-poc.env)
  ✗ staging: symlink -> ../alphalib.local/staging, 本机 JFS_LOCAL_DIR 期望 /ext4/alphalib.local/staging (改 /etc/juicefs-poc.env)

=== Groups + umask ===
  ✓ alpha-core (gid=59000) [wbai,cchang,ywang]
  ✓ alpha-data (gid=59001) [wbai,cchang,ywang]
  ✓ /etc/profile.d/ops-umask.sh: umask 0002

=== 数据目录概览 ===
    alpha_src: 48278 files, 2.4G
    alpha_pnl: 8235 files, 2.8G
    alpha_feature: 16010 files, 2.5T

=== 汇总 ===
  3 / 12 项异常
status.sh exit=1
```

> 3 项异常均为**探测/判据问题,非挂载故障**:(1) status.sh 把 `mymaster,IP,IP,IP`
> 整串当 host 做 TCP 探测,必然不通(meta URL 是 sentinel 多端点格式);(2)(3)
> sidecar 软链是相对路径 `../alphalib.local/...`,status.sh 与 env 绝对路径字面比较
> 判不符。挂载本身 active、writeback 空闲、无 block/object error。

### GROUP 4:目标盘 /nvme125 状态

```
--- df -h /nvme125 ---
Filesystem      Size  Used Avail Use% Mounted on
nvme125          12T  256K   12T   1% /nvme125
--- ls -la /nvme125/ | head -15 ---
total 4174
drwxr-xr-x    6 root root          6 Jul  8 10:50 .
drwxr-xr-x   30 root root       4096 Jul 10 16:25 ..
drwxrwxr-x 6944 wbai alpha-data 6944 Jul 10 22:03 alpha_dump
drwxrwx---    2 wbai alpha-data 6945 Jul 10 22:03 alpha_pnl
drwxrwx--- 6945 wbai alpha-data 6945 Jul 10 22:18 checkpoint
drwxr-xr-x    5 wbai wbai          5 Jun 17 10:30 datasvc
--- mount | grep nvme125 ---
nvme125 on /nvme125 type zfs (rw,noatime,xattr,noacl,casesensitive)
nvme125/datasvc on /nvme125/datasvc type zfs (rw,noatime,xattr,noacl,casesensitive)
nvme125/checkpoint on /nvme125/checkpoint type zfs (rw,noatime,xattr,noacl,casesensitive)
nvme125/alpha_dump on /nvme125/alpha_dump type zfs (rw,noatime,xattr,noacl,casesensitive)
nvme125/alpha_pnl on /nvme125/alpha_pnl type zfs (rw,noatime,xattr,noacl,casesensitive)
```

> `/nvme125` 是 12T ZFS pool,已建 4 个子 dataset,其中 alpha_dump / alpha_pnl /
> checkpoint 已被填(wbai:alpha-data,~6944/6945 项,Jul 10 22:xx)。**目标盘非空白**
> —— migrate-mount 要考虑 `/nvme125/alphalib` 落点与这些已存在目录的关系。

### GROUP 5:待搬存量(sidecar + cache)

```
--- du -sh /ext4/alphalib.local ---
16K	/ext4/alphalib.local
--- ls -la /ext4/alphalib.local/ ---
total 20
drwxr-sr-x 5 root  alpha-data 4096 Jun 24 09:47 .
drwxr-xr-x 7 yifei yifei      4096 Jul  8 17:37 ..
drwxr-sr-x 2 root  alpha-data 4096 Jun 24 09:47 alpha_dump
drwxr-xr-t 2 root  root       4096 Jun 24 09:47 recycle
drwxr-s--- 2 root  alpha-core 4096 Jun 24 09:47 staging
--- du -sh /ext4/jfs-cache (env JFS_CACHE_DIR) ---
4.0K	/ext4/jfs-cache
```

> sidecar 与 cache 都近空(16K / 4K)。170 不承担 submit/check 写职责,sidecar 无实数据;
> migrate 时几乎无 sidecar/cache 数据搬迁成本。

### GROUP 6:兼容软链与占用

```
--- ls -la /mnt/storage/alphalib ---
--- ls -la /mnt/storage/ ---
--- sudo lsof +D /ext4/alphalib ---
lsof exit=0
```

> `/mnt/storage/alphalib` 及 `/mnt/storage/` 均无输出(170 上不存在该兼容软链 /
> 目录)。`sudo lsof +D` 因 sudo 无 TTY 报错被 `2>/dev/null` 吞掉,head 无输出、
> exit=0 来自 head —— **占用情况未取到**(sudo 提权受限;需本机 TTY 或 NOPASSWD 复采)。

### GROUP 7:ops 侧现状

```
--- ls ~/gsim-ops ---
CLAUDE.md
config.yaml
docs
--- command -v uv / ops / ~/.local/bin/ops ---
/home/wbai/.local/bin/ops
--- sudo -n true ---
sudo: a password is required
sudo-nopasswd exit=1
--- hostname ---
server-170
--- PG port check ---
PG-PORT-OK
```

> `~/gsim-ops` 存在(有 repo)。`command -v uv` 空、`command -v ops` 空 —— **uv 与
> ops 均不在 PATH**;仅 `~/.local/bin/ops`(uv tool binary)存在。`uv run ops`
> 在 170 不可用(同 150)。sudo **无 NOPASSWD**(exit=1)。hostname=`server-170`
> (与 config 声明一致)。PG 15432 端口可达。

### GROUP 8:权限组现状

```
--- getent group alpha-core alpha-data ---
alpha-core:x:59000:wbai,cchang,ywang
alpha-data:x:59001:wbai,cchang,ywang
```

---

## 对 migrate-mount 实现的输入结论

1. **改 env 不够,必须动 unit** —— ExecStart 硬编码挂载点/cache/meta,
   EnvironmentFile 变量未被引用。实现要么重渲染整个 unit,要么把 unit 改成引用
   `/etc/juicefs/alphalib.env` 变量后再改该文件。
2. **env 6 键**是路径真相源(`JFS_MOUNT`/`JFS_CACHE_DIR`/`JFS_LOCAL_DIR`/
   `JFS_META_URL`/`JFS_REDIS_LOCAL`/`JFS_CACHE_SIZE_MB`);migrate 需同步改写。
3. **数据搬迁量可忽略**(sidecar 16K + cache 4K)—— 迁移主要是"改配置 + 重挂",
   非"搬数据"。
4. **目标盘已有子 dataset 且部分填过** —— `/nvme125/alphalib` 落点需与已存在
   alpha_dump/alpha_pnl/checkpoint 目录协调。
5. **提权是拦路石**:170 sudo 无 NOPASSWD、SSH 无 TTY。migrate 的写步骤要么本机
   TTY 执行,要么先落 NOPASSWD wrapper。
6. **170 无 `uv`/`ops` on PATH**、无 `/mnt/storage/alphalib` 兼容软链 —— setup
   在 170 跑要用 `~/.local/bin/ops`,兼容软链若需要得由 setup 补建。
