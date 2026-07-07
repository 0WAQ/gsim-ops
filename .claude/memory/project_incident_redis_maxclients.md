---
name: project_incident_redis_maxclients
description: "2026-06-23 redis-jfs:6380 maxclients 打满事故: 512 核 × juicefs go-redis 连接池数学, 治标 (ss -K 踢连接 + maxclients 50000 持久化), 根因待治"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3107dea5-dc5f-446e-81f9-4372c95ad0dd
---

# INCIDENT: redis-jfs 6380 maxclients 打满 (2026-06-23)

`ops list` 等所有命令崩在 `redis.exceptions.ConnectionError: max number of clients reached`。6380 是 **JuiceFS metadata + ops state 共生** 实例 (见 [[reference-server-topology]]),连接满后新连接连 AUTH 阶段就被拒,redis-cli 自己都进不去 → 死锁。

## 根因: 512 核 × juicefs 连接池, 不是泄漏

- **160 是 512 核** (`nproc=512`)。juicefs (go-redis) 连接池按核数 × 倍数算,单个 mount 进程 (`juicefs mount ... /tank/vault/alphalib`) 就持有 **5580 个 socket** 连到 6380。
- 事故时连接总数 **12314 > 默认 maxclients 10000**。来源: 150 ~7135 + 160 本机 ~5600 + 145/sentinel 零头。150 同样大核数,同理撑出几千。
- **不是连接泄漏 bug**: 踢连接后 juicefs 重建连接池到稳态 (160 ~5134) 就停了,90s 不再涨。是 **maxclients 10000 对 512 核 JFS 集群从一开始就配低了**,硬件规模与配置不匹配。
- 误判记录: 中途因为"踢后连接从 2042 涨回 5133"误判成持续泄漏,实际是池子重建到稳态,建完即止。判断增速要看稳态斜率,不是踢后瞬时回升。

## 处置 (已完成, 治标)

1. **挤空隙失败**: 连接死贴上限且无 churn (no connection closes → no free slot),`CONFIG SET` 重试永远挤不进。`scripts/redis-bump-maxclients.sh` 这条路在满载时走不通。
2. **ss -K 强制踢连接**: `ss -K state established '( sport = :6380 )' dst 10.9.100.150` 内核层 destroy 150→160:6380 的 socket,释放 3519 条 (5561→2042)。juicefs 为 metadata 断连设计,自动重连,`--writeback` 缓存在本地磁盘 cache-dir 不在 socket 里,**不丢数据**。需内核 `CONFIG_INET_DIAG_DESTROY` (160 有)。脚本 `scripts/redis-drain-and-bump.sh`。
3. **maxclients 调大**: 腾出 slot 后 `CONFIG SET maxclients 50000` 立即生效 (fd 上限 `LimitNOFILE=65535` ≥ 50000+32,能真生效)。
4. **持久化**: `CONFIG REWRITE` 报 `Permission denied` (redis 对 config 目录无写权)。改走直接写配置文件: `/etc/redis-jfs/redis.conf` 追加 `maxclients 50000` (脚本 `scripts/redis-persist-maxclients.sh`,先备份 `.bak-<ts>`,不重启 redis 故不触发 failover)。

## 关键事实 (排障必读)

- **6380 真实配置文件 = `/etc/redis-jfs/redis.conf`** (不是 `/etc/redis/redis.conf`,后者是 6379)。redis 进程 `config_file` 字段才权威。
- 6380 redis 进程 ppid=1 (systemd 拉起),但 `redis-server@6380.service` 显示 `inactive/disabled` — 6380 是另一套部署,不是模板 unit 管的。改持久化别动那个模板。
- redis 版本 7.0.15,`requirepass` 密码在 `/etc/juicefs/alphalib-jfs.env` 的 `META_PASSWORD` (sudo 才能读,`0700 root:root`)。
- **诊断脚本** (git 未跟踪,工作区 `scripts/`): `redis-diag.sh` (只读全量诊断,最有用) / `redis-drain-and-bump.sh` (踢+调大) / `redis-persist-maxclients.sh` (持久化) / `redis-bump-maxclients.sh` (纯挤空隙,满载无效,保留作参考)。
- **sudo 跨上下文坑**: 用户终端 `sudo -v` 刷新的 timestamp 不被 Claude Bash tool 继承 (不同 tty/上下文)。特权命令必须用户在自己终端跑,Claude 给只读诊断脚本 + 据输出给改法。

## 根因待治 (follow-up, 见 .claude/plans.md)

- maxclients 50000 是治标; 真正该做: 给 juicefs mount 设连接池上限 (`--max-conns` 或核数相关参数),从源头压连接数。512 核裸用默认池系数对任何共享 redis 都是炸弹。
- **ops state 与 JFS metadata 共生在同一 6380** 是结构性隐患: juicefs 池把 redis 占满,ops 跟着遭殃。评估把 ops state 拆到独立 redis 实例。
- 监控缺失: 连接数打满前无告警 (跟 [[reference-server-topology]] "监控=人工" 一致)。
