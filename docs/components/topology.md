# 多机拓扑与并发

三地三机房,跨段互通但带宽/延迟差异显著。完整拓扑表 + 数据同步链见
[`../../CLAUDE.md`](../../CLAUDE.md) "Hosts / Network Topology"。

## 七机一览

| Host | IP 段 | 位置 | 角色 |
|---|---|---|---|
| **147** | 10.12 | 上海中信 | rawdata 抓取出口 + cc first build + 实盘 combo(内网隔离,无 ops) |
| **160** | 10.9 | 北京 IDC | JFS master + ZFS pool + redis-jfs 6380 master + **NFS owner** + PG + yifei L2 生产 |
| **150** | 10.9 | 北京 IDC | JFS client + NFS 客户端 + redis 6380 replica |
| **145** | 10.9 | 北京 IDC | JFS client + NFS 客户端 + JFS 卷对象存储落盘机 |
| **170** | 10.9 | 北京 IDC | JFS client(独立 12T nvme pool)+ **check 消费机** |
| **144** | 10.6 | 本地办公网 | JFS client(跨段 LAN→IDC)+ 本地 NFS owner + **冷副本 / 跨段容灾** |
| local-145/146 | 10.6 | 本地办公网 | 本地 NFS 客户端(挂 144) |

**网段**:10.6(本地办公)/ 10.9(北京 IDC)/ 10.12(上海中信)。144 ↔ IDC 走跨段路由,
写并发场景把生产留 IDC,**144 当 WAN 节点**(超时调宽、避免 chatty 协议)。

## JFS vs NFS 分工

- **JuiceFS** 只服务 alphalib(因子库多机多写,2026-06 上线):挂载点共享 alphalib 卷,
  metadata 走 `redis-sentinel://...:26380/mymaster`(实测 failover 9.12s)。挂载点 per-host
  可不同([storage-layout.md](storage-layout.md))。新 client 用
  `scripts/juicefs-poc/join.sh` 接入。
- **NFS**(老方案保留):cc / dm / L2 feature(单 owner 多读,各地 owner 各管各的)。

两套分场景共存。**关键运维事实**:redis-jfs 6380 是 JFS metadata 后端,**绝不可停、绝不
FLUSHDB**(停进程 = 挂因子库文件);ops state 早已迁 PG,不再用它。

## 共享 staging + 队列消费

staging 2026-07-11 起是 JFS 共享实目录:**任意机器 submit 入队 → 170 消费机 check → 任意
机器看结果**。消灭了"在哪台 submit 就必须在哪台 check"的绑定。设计见
[`../design/shared-staging-queue.md`](../design/shared-staging-queue.md)。

## 跨机 factor_lock

所有对单因子的变更(submit/check/restage/rm/approve)先取非阻塞 per-factor 锁
([`ops/infra/lock.py`](../../ops/infra/lock.py)):

- **postgres 后端(生产)**:**跨机 PG advisory lock**(`pg_try_advisory_lock`,专用连接,
  session 级,连接断开自动释放,无死锁残留)。per-machine fcntl 挡不住跨机对同一因子的并发
  变更(state 共享 PG + 产物共享 JFS + staging 共享)——跨机锁正是防重复消费/并发撞车的机制。
- **json dev/test 后端**:per-machine fcntl。

竞争即跳过(no queueing),记 warning。签名 `factor_lock(name, config)`。

## sudo self-elevation

共享路径 root-owned,wbai 直接写会 EACCES。[`ops/infra/sudo.py`](../../ops/infra/sudo.py) 的
`maybe_elevate(args)`:**写命令**(cli 注册处 `mark_write` 声明 → `args.is_write_command`)
且 `alpha_src` root-owned 时 → `os.execvp('sudo --preserve-env=OPS_* ops …')` 替换自身;
read-only 命令直通。写命令集是声明派生(非手抄),钉在 `tests/test_pure.py`。

## crash 恢复

reconcile 已下线(state 共享 PG 后,per-host 本地 staging 视图无权裁决全局 state)。自愈靠:
`ops check` **按 staging 目录扫描**(不看 state status),崩在半路仍在 staging 的因子下次照样
重跑并覆盖其 CHECKING 状态;PG state 事务原子写,drift 窗口极小。残留用 `ops rm` / `ops doctor`
([`../../ops/services/doctor/CLAUDE.md`](../../ops/services/doctor/CLAUDE.md))兜底。

→ 回 [架构总览](../architecture.md#8-多机拓扑与并发)
