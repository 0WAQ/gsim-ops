# ops 文档

## 从哪读起

- **想懂整个系统怎么搭的** → [architecture.md](architecture.md)(架构总览,入口)
- **想用 ops 命令** → [`../README.md`](../README.md)(命令速查)
- **写 gsim 因子(研究员)** → [gsim/README.md](gsim/README.md)

## 目录

| 目录 / 文件 | 内容 |
|---|---|
| [architecture.md](architecture.md) | **架构总览**——自顶向下的地图,链接以下各区 |
| [design/](design/) | 设计决策记录(schema v2·v3 / 聚合施工图 / 共享 staging / legacy 清理 / combo) |
| [gsim/](gsim/) | gsim 框架 + 因子开发(研究员面向) |
| [remediation/](remediation/) | 执行手册 + JOURNAL(每批迁移的手册与记账) |
| [reports/](reports/) | 审计 / 评审报告(check 报告、full-review) |
| [incidents/](incidents/) | 事故记录(gsim 代码漂移 / cc 数据漂移 / Interval5m) |
| [ops/](ops/) | 运维 SOP(NFS cc 迁移等) |
