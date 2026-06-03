# JuiceFS PoC

验证 JuiceFS 作为 alphalib 共享文件系统是否满足 gsim + ops 的使用模式。详细动机和迁移路径见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。

**两轮 PoC 已通过 (2026-06-02)**。当前在做 **Step 1: 挂载布局 + 权限模型**(进行中,见下方同名章节),完成后进入 Step 2 持久化。跨节点 / Redis HA / 全量迁入需要等第二/第三台机器,见底部"下一步"。

## 拓扑(PoC 阶段)

```
[本机]                          [MinIO 服务器 10.9.100.145:39000 / 外网 103.237.248.189:39000]
├── ops / gsim                 └── 现有 bucket 不动 + 新 bucket alphalib-juicefs
├── JuiceFS client
├── Redis (本地 127.0.0.1:6379)
└── cache: /tank/vault/juicefs-cache (500 GB)
        │
        └─→ chunk 上传到 MinIO 新 bucket
```

挂载点 `/tank/vault/alphalib/`,和现有 `/mnt/storage/alphalib/`(指向 `/tank/vault/storage/` 软链)同级并列,不冲突。

PoC 通过后,Redis 会挪到 MinIO 那台做服务化(`juicefs config --meta-url=...` 一行命令切,数据无损)。

## 凭证(关键)

`rclone.conf` 里默认的 `[39000]` profile (`external-client`) **是受限只读凭证,不能创建 bucket / PutObject**。PoC 期需要 MinIO **root 账号**或同等权限的 key,用环境变量注入:

```bash
export MINIO_ROOT_USER=<root-ak>
export MINIO_ROOT_PASSWORD=<root-sk>
# 可选:覆盖 endpoint
# export MINIO_ENDPOINT=http://10.9.100.145:39000
```

`00-config.sh` 优先级:`MINIO_ROOT_USER/PASSWORD` 环境变量 > `MINIO_ACCESS_KEY/SECRET_KEY` > `rclone.conf [39000]`。

调用 `sudo` 的脚本(02/03/99)必须用 `sudo -E` 把环境变量带到 root。

> 安全提醒: PoC 跑完后旋转一次 root key —— 第一轮 02-prepare.sh 早期版本曾在 rclone 报错里把 secret 打到日志,可能已泄露。

## 配置

所有可调参数在 `00-config.sh`。`./00-config.sh --show` 预览当前值。

| 变量 | 默认 | 说明 |
|---|---|---|
| `RCLONE_PROFILE` | `39000` | rclone.conf 段名,凭证回退时用 |
| `JFS_BUCKET` | `alphalib-juicefs` | MinIO 新 bucket |
| `JFS_MOUNT` | `/tank/vault/alphalib` | 挂载点 |
| `JFS_CACHE_DIR` | `/tank/vault/juicefs-cache` | 本地 cache(/tank ZFS-on-NVMe) |
| `JFS_CACHE_SIZE_MB` | `512000` | cache 上限 (500 GB) |
| `JFS_META_URL` | `redis://127.0.0.1:6379/0` | metadata 引擎 |

## 流程

按顺序跑,每个脚本幂等,失败可重跑:

| 脚本 | 做什么 | 调用方式 |
|---|---|---|
| `01-install.sh` | 装 redis + juicefs client | `sudo bash 01-install.sh` |
| `02-prepare.sh` | 建 MinIO bucket(实测写入)+ 本地 cache 目录 | `sudo -E bash 02-prepare.sh` |
| `03-format-mount.sh` | `juicefs format` + `juicefs mount --writeback` | `sudo -E bash 03-format-mount.sh` |
| `04-verify-basic.sh` | 5 项基础测试(读写/flock/stat/可见性) | `bash 04-verify-basic.sh` |
| `05-verify-memmap.sh` | alpha_feature 模式仿真 | `bash 05-verify-memmap.sh` |
| `06-verify-git.sh` | Git on JuiceFS 500 提交基线 + ZFS 对照 | `bash 06-verify-git.sh` |
| `07-verify-redis-failure.sh` | Redis 故障注入(stop/start,观察 IO 行为) | `sudo -E bash 07-verify-redis-failure.sh` |
| `08-relocate-local-dirs.sh` | `alpha_dump / staging / recycle` 搬出 JuiceFS,改 symlink → 本地 sidecar (`$JFS_LOCAL_DIR`) | `sudo -E bash 08-relocate-local-dirs.sh` |
| `09-setup-perms.sh` | 两层组权限模型 (`alpha-core` / `alpha-data`) | `sudo -E bash 09-setup-perms.sh` |
| `99-teardown.sh` | 卸载;`--purge` 才删数据 | `sudo -E bash 99-teardown.sh [--purge]` |

挂载后 `/tank/vault/alphalib/` 是 root 拥有但 mode 777,wbai 用户可直接读写,不需要再 sudo。

## 实测结果(2026-06-02 第一轮)

| 验证项 | 实测 | 通过 |
|---|---|---|
| 100 MB 文件写 | 333 ms (~300 MB/s) | ✅ |
| 100 MB 文件 re-read(cache 命中) | 176 ms | ✅ |
| flock 跨进程串行 | 5 ms 切换 gap | ✅ |
| `ls -la` 1000 小文件 | 15 ms | ✅(Redis ping 34µs) |
| 创建 171 MB memmap | 1.05 s | ✅ |
| **memmap 日增写一行(alpha_feature 关键路径)** | **35 ms** | ✅ |
| 100 行随机扫描 | 54 ms | ✅ |
| 跨进程 reopen 读 | bit-level 一致 | ✅ |

## 第二轮结果 (2026-06-02)

| 验证项 | 结论 |
|---|---|
| **完整 `ops check` 跑真实因子** | ✅ `OPS_CONFIG=config.juicefs.yaml ops check` 全流水线通过(AlphaWbaiReversal),耗时 ~3.5min 持平本地。曾暴露 state store 忽略 `-c` 的 bug,已于 045fcb1 修复 |
| **Git on JuiceFS 性能** | ✅ 500 commit 基线:JuiceFS commit 75ms (vs 本地 ZFS 21ms,3.5x),`git log/blame/status/diff` 全部 <250ms。**Phase D 共享工作区模式可行,不需要降级到 bare repo + clone** |
| **Redis 故障注入** | ✅ 结论非常明确:**JuiceFS 不 hang,Redis 一停立刻 EIO**。所有 syscall(读/写/stat/unlink)全部失败,整个挂载点瘫。**Phase C 上线前必须配 Redis Sentinel 主从**,详见 `.claude/plans.md` 的 "Redis HA 部署" |
| ops pack 增量模式 | ⏸ 设计完成,实施暂缓 —— 见 `.claude/plans.md` 的 "ops pack Incremental Mode" |
| 跨节点验证 | ⏸ 等第二台机器 |

## Step 1: 挂载布局 + 权限模型 (进行中, 2026-06-03)

把 PoC 验证完的挂载点收成可长期跑的形态:目录布局清理 + 两层组权限。

### 布局

```
/tank/vault/alphalib/          ← JuiceFS 挂载点(共享,跨机)
├── alpha_src/         root:alpha-core 2750     ← 只 alpha-core 读;零直接写,改写必须 sudo
├── alpha_pnl/         root:alpha-data 2770+ACL ← 所有研究员读写
├── alpha_feature/     root:alpha-data 2770+ACL ← 所有研究员读写
├── alpha_dump   →     /tank/vault/alphalib.local/alpha_dump   (symlink)
├── staging      →     /tank/vault/alphalib.local/staging      (symlink)
└── recycle      →     /tank/vault/alphalib.local/recycle      (symlink)

/tank/vault/alphalib.local/    ← 本地 sidecar (每机各自一份,不进 JuiceFS)
├── alpha_dump/        root:alpha-data 2770+ACL
├── staging/           root:alpha-data 2770+ACL
└── recycle/           root:alpha-data 2770+ACL
```

`alpha_dump / staging / recycle` 是每机各自的中间产物,不应该走 JuiceFS。08 把它们替换成指向本地 sidecar 的 symlink,这样所有 ops 代码路径不用改 —— 看到的还是 `alphalib/alpha_dump/...`。

### 权限模型(两组制)

| 组 | gid | 成员 | 能做什么 |
|---|---|---|---|
| `alpha-core` | 59000 | wbai | 读所有 alpha_src 代码 |
| `alpha-data` | 59001 | wbai | 读写 alpha_pnl / alpha_feature / alphalib.local/* |

- gid 自管,选 59xxx 段(GID_MAX=60000 以下,远离常见 7/8/9000 段,避免和 LDAP/系统组冲突)
- **owner 全部 root**,enforcement 100% 走 gid,不信任 uid
- **零研究员直接写 alpha_src**:连作者改自己的代码也不行,只能走 ops 流(`ops submit/resubmit/recheck` 内部 sudo)
- alpha-data 是普通研究员组(写数据产物),alpha-core 是受限读组(看代码)
- ACL: alpha_pnl/feature/local 上有 default ACL `g:alpha-data:rwx`,保证新建文件自动可读写

### 当前进度

| 项 | 状态 |
|---|---|
| 08: alpha_dump/staging/recycle 改 symlink → alphalib.local | ✅ |
| JuiceFS `--enable-acl` 已开 | ✅(需 remount 后 setfacl 才生效,已重挂) |
| 09 [1/6] 创建组 alpha-core / alpha-data | ✅ |
| 09 [2/6] usermod -aG | ✅ (NSS 层) |
| 09 [3/6] 清理 A 模式 alpha-author-* 残留 | ✅ |
| 09 [4/6] alpha_pnl / alpha_feature 权限 + 默认 ACL | ✅ |
| 09 [5/6] alpha_src 整棵 root:alpha-core 2750 | ✅ |
| 09 [6/6] alphalib.local 权限 + 默认 ACL | ❌ **default ACL 没打上**(原因见下) |

### 卡点:ZFS pool 默认 `acltype=off`

`/tank/vault/alphalib.local/` 在 ZFS 上,ZFS pool 默认 `acltype=off` 不支持 POSIX ACL。
09 之前没探针,`setfacl -d -m` 静默返回 EOPNOTSUPP,结果 owner/group/mode 都对,但 default
ACL 缺失。已在 09 顶部加 fail-fast 探针,下一个 session 跑到这里会立刻报错并打印修复指引。

**立即下一步**(下个 session 第一件事):

```bash
# 1. 开 ZFS POSIX ACL(只对 tank/vault,不动 cc/rawdata/mdl 这些不相关 dataset)
sudo zfs set acltype=posixacl tank/vault
sudo zfs set xattr=sa tank/vault          # POSIX ACL 存到 SA,性能远好于 DIR xattr

# 2. remount 让内核重读 mount option(JuiceFS 是独立 FUSE,不在 tank/vault dataset 上,不受影响)
sudo mount -o remount /tank/vault
mount | grep '^tank/vault '               # 应该看不到 noacl 了

# 3. 重跑 09 把 default ACL 补上(顶部新加的探针会先验证 ACL 真的可用)
sudo -E bash scripts/juicefs-poc/09-setup-perms.sh

# 4. 验证 alphalib.local 的 default ACL 出现
getfacl /tank/vault/alphalib.local
# 期望末尾出现 default:group:alpha-data:rwx 等条目
```

### 已验证(基于现在的部分完成状态)

用 `sg alpha-core / sg alpha-data` 子 shell 验证(避开 SSH session 组缓存):

| 场景 | 结果 |
|---|---|
| `sg alpha-core` ls alpha_src | ✅ 看到 AlphaWbaiReversal |
| `sg alpha-core` 写 alpha_src | ✅ Permission denied(零直接写,符合设计) |
| `sg alpha-data` rw alpha_pnl / alpha_feature / alphalib.local | ✅ |
| `sg alpha-data` ls alpha_src | ✅ Permission denied(数据组看不到代码) |

注意:`id wbai` (NSS 视图) 已经显示新组,但**当前 SSH session 内的所有 shell 都拿不到**,
因为 supplementary groups 是 login 时锁定的,`usermod -aG` 不会反推已有 session。需要:
`exec su - $USER`,或者退出 SSH 重新连。`sg <group> -c <cmd>` 是临时验证的替代品。

### Phase C 前置:ops 写 src 路径要套 sudo wrapper

alpha_src 现在 root-owned,wbai 进程直接 `shutil.move/copy` 进去会 EACCES。
扫到的待改路径:

| 文件 | 行 | 操作 |
|---|---|---|
| `ops/services/check/check.py` | 134 | `shutil.move(staging → alpha_src)` |
| `ops/services/check/check.py` | 155 | `shutil.copy(staging → alpha_src)` |
| `ops/services/submit/*` | — | submit 写新因子到 alpha_src |
| `ops/services/resubmit/*` | — | resubmit 覆写已有因子代码 |

写 src 全部集中在 submit / resubmit / check 归档,三处。Phase C 全量迁入 JuiceFS 之前
必须把这些包一层 sudo(或者用 setuid helper)。

## 下一步(交接给后续 session)

按优先级:

0. **收尾 Step 1**(见上面"卡点":开 ZFS acltype + remount + 重跑 09)
1. **Step 2: 持久化**(systemd unit for redis-server + .mount unit for JuiceFS,处理重启自起,简化多机部署)
2. **跨节点验证**(等第二台机器):
   - 把 Redis 改为监听 `0.0.0.0` + 设密码 + ACL,目前只绑 `127.0.0.1`
   - 第二台机 `juicefs mount` 同一卷,验证 A 写 B 立刻可见 + flock 跨节点真锁
3. **Redis Sentinel 部署**(等第三台机器,Phase C 硬前置):见 `.claude/plans.md` 的 "Redis HA 部署"
4. **Phase C 全量迁入**:`juicefs sync` 把现有 `/mnt/storage/alphalib/` 灌进新 bucket,切 config,`ops sync push/pull` 退役
   - **前置**: Step 1 的 ops sudo wrapper 改造(见上面 Phase C 前置表)
5. **Phase D/E/F**:Git 接入、`.state` 简化、checkpoint 落地

## 失败回退

PoC 完全独立,不动现有任何东西:不动旧 MinIO bucket、不动 `/mnt/storage/alphalib/`、不动 `ops sync` 链路。

放弃 PoC: `sudo -E bash 99-teardown.sh --purge`,一键彻底清干净。

## 备忘 / 踩过的坑

- **`rclone.conf` 里 `no_check_bucket = true` 会让 `rclone mkdir` 假装成功**(其实没建)。02-prepare.sh 已加真实 PutObject 验证(rcat → ls → delete)兜底
- **rclone `:s3,key=value,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL**(http:// 里的 `:` 把语法解析烂了)。02/99 改用临时 rclone config 文件注入凭证
- `juicefs format` 日志里 `minio://http://endpoint/bucket/bucket/volume/` 看着 bucket 重复了,实际 S3 调用没问题,只是显示格式
- `juicefs mount --writeback`: 写入先落 cache 异步上传 MinIO,延迟低但断电会丢未上传数据。PoC 可接受,生产要权衡(可改 `--writeback=false` 或加 `--upload-delay`)
- MinIO endpoint 不可达时,挂载会 hang。卡住先 `curl ${MINIO_ENDPOINT}/minio/health/live` 排查
