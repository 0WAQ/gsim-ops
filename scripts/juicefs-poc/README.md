# JuiceFS PoC

验证 JuiceFS 作为 alphalib 共享文件系统是否满足 gsim + ops 的使用模式。详细动机和迁移路径见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。

**第一轮 PoC 已通过 (2026-06-02)**,关键指标见底部"实测结果"。

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

## 第二轮 TODO

- [ ] 完整 `ops check` 在 JuiceFS 上跑一个真实因子,PNL 与本地 bit-level 对比
- [ ] `ops pack --date YYYYMMDD` 增量模式落地 + 量化 MinIO 上 chunk 增量(预期 ~4MB/天/因子)
- [ ] 跨节点验证(第二台机器挂同一卷,验证 A 写 B 立刻可见)
- [ ] flock 跨节点真锁(同上前提)
- [ ] Redis 短暂挂掉:JuiceFS hang 行为 + 自动恢复
- [ ] Git on JuiceFS 性能 —— Phase D 前置依赖,模拟几百因子量级 `git log/blame`

## 失败回退

PoC 完全独立,不动现有任何东西:不动旧 MinIO bucket、不动 `/mnt/storage/alphalib/`、不动 `ops sync` 链路。

放弃 PoC: `sudo -E bash 99-teardown.sh --purge`,一键彻底清干净。

## 备忘 / 踩过的坑

- **`rclone.conf` 里 `no_check_bucket = true` 会让 `rclone mkdir` 假装成功**(其实没建)。02-prepare.sh 已加真实 PutObject 验证(rcat → ls → delete)兜底
- **rclone `:s3,key=value,...:bucket` 即席 backend 不能处理含冒号的 endpoint URL**(http:// 里的 `:` 把语法解析烂了)。02/99 改用临时 rclone config 文件注入凭证
- `juicefs format` 日志里 `minio://http://endpoint/bucket/bucket/volume/` 看着 bucket 重复了,实际 S3 调用没问题,只是显示格式
- `juicefs mount --writeback`: 写入先落 cache 异步上传 MinIO,延迟低但断电会丢未上传数据。PoC 可接受,生产要权衡(可改 `--writeback=false` 或加 `--upload-delay`)
- MinIO endpoint 不可达时,挂载会 hang。卡住先 `curl ${MINIO_ENDPOINT}/minio/health/live` 排查
