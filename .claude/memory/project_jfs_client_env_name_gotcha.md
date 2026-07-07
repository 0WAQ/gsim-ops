---
name: project_jfs_client_env_name_gotcha
description: 新接 JFS client 后 ops 报 redis AuthenticationError 的根因 — 密码文件名 alphalib.env vs alphalib-jfs.env 不匹配
metadata: 
  node_type: memory
  type: project
  originSessionId: ab1122af-be67-4a76-9035-1c964ffae24e
---

新接入一台 JuiceFS alphalib client(`join.sh`)后,挂载正常但 `ops list` 等命令报
`redis.exceptions.AuthenticationError: Authentication required`(state redis NOAUTH)。

根因:`join.sh` 把元数据密码写进 `/etc/juicefs/alphalib.env`(不带 `-jfs`),
但 `config.yaml` 的 `state.redis.password_file` 指向 `/etc/juicefs/alphalib-jfs.env`
(带 `-jfs`)。160/150 上是 `-jfs` 名是因为它们跑过 `06-meta-migrate.sh`(那步把
client env 改名带 `-jfs`);纯 client 走 join 不跑 meta-migrate,所以只有 `alphalib.env`,
缺 ops 期望的 `alphalib-jfs.env`。ops 的 `ensure_redis_password`(`ops/infra/sudo.py`)
`sudo grep` 一个不存在的文件 → 空密码 → AUTH 失败。挂载本身不受影响,只 ops state 炸。

**Why**: 文件系统挂载(juicefs FUSE 直接用 join 写的 `alphalib.env`)和 ops state redis
(走 config.yaml 的 password_file)是两条独立的密码读取路径,只有后者用带 `-jfs` 的名。

**How to apply**: 接完新 client 立刻补一份(内容同一密码):
`echo "META_PASSWORD=$(sudo grep -oP 'META_PASSWORD=\K.*' /etc/juicefs/alphalib.env)" | sudo tee /etc/juicefs/alphalib-jfs.env >/dev/null && sudo chmod 600 /etc/juicefs/alphalib-jfs.env`
已固化进 `scripts/juicefs-poc/README.md` 的 "Client 节点" 小节。

2026-06-24 接入 server-170 (10.9.100.170, /ext4/alphalib, cache 100G) 时踩到。
相关拓扑见 [[reference-server-topology]]。
