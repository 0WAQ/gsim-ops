# 共享 staging + PG 队列消费(多机 submit / 集中 check)

**需求**(2026-07-11,用户):QR 在多台机器(144/150/160)投递因子,目前必须
"在哪台 submit 就在哪台 check"。目标:submit 入队,由消费机(170,生产机、
数据全)统一消费跑 check,人不再跟着机器跑。

## 设计:队列 = PG 状态表,不引入新中间件

用户原设想是"submit 进任务队列 + 一/多台消费"。结论:**队列已经存在** ——
PG `factor_state` 就是队列(`submitted` = 排队,`checking` = 消费中),跨机
advisory lock = 防重复消费,check 的 staging 扫描 = 领任务。不再架 Redis/MQ:
多一个有状态服务要运维,且队列条目与 PG 状态构成第二真相源(本库刚清完一轮
多真相源病,不再造新的)。

**唯一缺口是数据面**:staging 是本机 sidecar 软链(2026-07-11 实测校正),
submit 拷进 A 机的 staging,B 机看不见。补上这一块 = staging 上 JFS。

## 正确性论证(为什么共享化后现有代码就是安全的消费者)

三道既有机制,合起来恰好构成跨机队列消费的安全性:

1. **完成门(半拷贝不可见)**:check 扫描跳过无 `meta.json` 的目录;submit
   的顺序是 copytree → normalize → parse → **meta.save** → PG register ——
   meta.json 是目录内容完备的最后一笔,worker 看到 meta.json 时源码/XML 必已
   齐全。
2. **互斥(不重复消费)**:submit 全程持因子锁(跨机 PG advisory),worker
   对拿不到锁的因子直接 skip(已有 `locked` 计数);两台 worker 同扫也按因子
   天然分片。meta.json 已写、register 未写的毫秒窗口同样被锁盖住。
3. **崩溃自愈**:submit 崩在 meta.json 后 register 前 → 锁随进程死亡释放,
   留下"有 meta 无记录"目录 → worker `_ensure_record` 补建记录照常消费
   (既有语义,跨机后依然成立)。

其余不变量不受影响:身份守卫(目录名 == @id)在 run_one 入口;identity
divergence 整单拒绝;CHECKING 残留重扫自愈。

**产物落点**(共享化后):pnl / bcorr 池 / feature / src → JFS 共享(不变);
**dump 落消费机(170)本机 sidecar** → `ops pack` 必须在 170 跑(dump 本机性
不变,只是"本机"从提交机变成消费机)。

## 上线顺序(部署手册 docs/remediation/DEPLOY-SHARED-STAGING.md)

0. ~~170 部署 ops~~ **已由 ops setup 工程完成**(2026-07-11:挂载迁
   /nvme125 + PG 凭证 + `setup --check` FAIL 0,见 MIGRATE-170-RESULT)。
1. staging 共享化(分钟级窗口,禁 submit/check/restage):挂载点内的 staging
   软链是**单一 JFS 对象**(160/150 的 ls mtime 差 8h = 时区显示,同一 inode),
   删软链 + 建实目录一次全局生效;各机 sidecar 存量搬进共享目录。
2. 金丝雀跨机流转:160 submit → 170 check → 160 看结果;restage 回环;rm 清理。
3. 文档同批:CLAUDE.md/config 注释的 staging 行改回"共享"(这次是部署变更,
   不是文档漂移)。

## 明确不做 / 后议

- **消费节奏**(cron / 常驻 watch):用户定的"框架先行,节奏后议"。当前
  170 手动 `ops check` 即可消费全队列;节奏化是加一个 cron 行的事。
- **自动 submit**(扫 dropbox、READY 约定、防半成品):独立后续件,与本框架
  正交 —— 框架不依赖它,它落地后只是把"入队"也自动化。
- **check 并发限流**:`min(20, n_factors)` 硬编码(check.py);170 与 yifei
  clickhouse 同盘 /ext4,如实测有 IO 争抢再做成 config 参数(挂账)。
- **PG 记录 staging 位置**:共享化后此挂账自然消灭(全局只有一个 staging)。
