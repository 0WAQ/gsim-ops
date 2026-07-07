---
name: reference-server-topology
description: "三地三机房物理服务器拓扑 (本地办公室 / 北京 IDC / 上海中信 IDC), NFS owner + 客户端关系, 数据流方向, JFS vs NFS 分工, 监控方式"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# 服务器拓扑 (2026-06 状态)

物理三地, **数据各地一份本地副本**(不是镜像同步, 是 rawdata CSV 增量 + 各地独立 build_cc)。CLAUDE.md hosts 表只列了部分机器, 这里是全量。

## 三地分布

```
┌────────────────────────────────────────────────────────────────────────┐
│ 上海中信托管机房 (内网隔离, 跨段 WAN)                                   │
│  ├ 147 = 10.12.174.152  (rawdata 抓取 + cc first build + 实盘 combo)   │
│  └ 内网隔离, ops 代码不在这台                                          │
└────────────────────────┬───────────────────────────────────────────────┘
                         │ 每日 CSV 增量 (rawdata)
                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 北京托管机房 (10.9.100.0/24, 生产 IDC)                                  │
│  ├ 160 = 10.9.100.160  ┐ JFS master + L2 feature 生产节点 +            │
│  │                     │ NFS owner (/datasvc/data/, 导出给 145/150)    │
│  │                     │ ZFS pool /tank/vault/, redis-jfs:6380 master  │
│  │                     │ sentinel:26380                                │
│  ├ 150 = 10.9.100.150  → JFS client + NFS 客户端 + redis replica +     │
│  │                       sentinel:26380                                │
│  └ 145 = 10.9.100.145  → JFS client + NFS 客户端 (透明读 /datasvc/data/)│
│         (CLAUDE.md 老说"不在 JFS 集群" → 已校正, 实际也在)             │
└────────────────────────┬───────────────────────────────────────────────┘
                         │ 历史 CSV (早期一次/偶发推送, 不在生产链路内)
                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 本地办公室 (10.6.100.0/24, 本地 LAN)                                    │
│  ├ 144 = 10.6.100.144  ┐ NFS owner (本地 owner, 导出给 145/146) +      │
│  │                     │ JFS client 跨段挂 (/storage/vault/...) +      │
│  │                     │ sentinel:26380 (纯投票)                       │
│  │                     │ 冷副本: 只有 cc_2024 + cc_2025, 没 cc_all     │
│  ├ 145 = 10.6.100.145  → NFS 客户端                                    │
│  └ 146 = 10.6.100.146  → NFS 客户端                                    │
└────────────────────────────────────────────────────────────────────────┘
```

**注意**: 本地 `10.6.100.145` 跟北京 `10.9.100.145` **是两台完全不同的机器**, 同号巧合。

## 数据流方向 (rawdata + cc)

```
147 (上海中信, 内网出口)
  ├ wind (Oracle) / datayes (MySQL) / citics (ClickHouse) 抽 → 每日 CSV
  ├ 本地 build_cc → 147 cc (最新)
  └ 增量 CSV ──→ 北京 160
                  ├ 本地 build_cc → 160 cc (跟 147 同水位)
                  └ NFS export /datasvc/data/ → 145 / 150 (透明读)

本地 144 (办公室)
  └ 早期一次性 CSV → 自建 cc_2024 / cc_2025 (冻结, 不更新) → NFS to 145/146
```

含义:
1. **三地 cc 都不是 byte-identical**: rawdata CSV 一样, 但 build_cc 时间窗 + config 可能不同
2. **本地 144 是冷副本**: 看不到 2026-01-01 之后的数据 (实际 cutoff 看 cc_2025/.meta), 适合离线研究, 不能用于实时回测 / OOS 验证
3. **唯一能看到当前交易日数据的机器**: 147 (最快) / 160 / 145 / 150 (北京 IDC 三台, 同时 NFS 共享)

## yifei L2 数据生产 (在北京 160, 不在 147)

校正之前理解 —— L2 推送流不落 147, 落 **北京 160**:

```
每日 ~20:00 (盘后)
  → yifei 框架在 160 上完成当日 L2 .npy 增量 (cn_equity_feature/* 等)
  → 第二天 wbai 手动 backfill: 整合 / 注册成 cc 平面对应 feature tag
```

含义:
- L2 跟 rawdata 是两条独立 pipeline (上游不同, 落地节点不同)
- 即使 yifei 直接生产了 .npy, **还需要 wbai 隔天介入**才能让 gsim DataReader 读到 (跟 [[reference-company-data-architecture]] 里"feature 设计 / 生产 / adapter 三件事三拨人"完全契合)
- **147 上的 L2 没有 2025 前数据** 现在合理了: L2 不在 147 产, 只是后期 sync 才有些, 自然没历史

## dm 层处理 = 跟 cc 一样

`dm_src/` 下的派生 feature (`Dipv`, `Dpv*`, `Dmgr_MktRet` 等) **各地各跑一遍**, 不同步 .npy。所以 cc_all 下 `Dipv/.meta` 三地水位也可能不同步。dm 跟 cc 同 pattern, 不算独立第三层调度。

## JFS vs NFS 分工 (历史 + 现状)

| 系统 | 服务对象 | 角色 | 上线时间 |
|---|---|---|---|
| NFS (160 owner / 144 owner) | cc / dm / L2 feature (`/datasvc/data/`) | 单 owner 多读 (各地 owner 各管各的) | 早期 |
| JFS (160 master + 150/144 client) | 因子库 alphalib (`/tank/vault/alphalib/`) | 多机多写场景 (sync push / ops 多机协同) | 2026-06-05 上线 |

**为什么不全切 JFS**:
- cc 各地各 build, 没有"多机一致性"需求, NFS 单 owner 模型够用
- alphalib 必须强一致 (研究员从任意机器都能 push factor 进同一仓), NFS 单 owner 搞不定
- 两套并存不是设计目标, 是历史叠加

## 监控 = 人工日志检查

每天 wbai 人工查日志, 没有自动告警。
- CSV 增量 147→160 跑挂 / 漏一天 → 看日志
- L2 backfill 失败 → 看日志
- 三地 build_cc 水位不一致 → 看 `.meta` cutoff date
- 三地 cc 脑裂 (相同日期 build 出不同值, 比如 wbai 改了 source_ref/Dmgr 没推同步) → 没自动检测

**已知运维负债**, 未来要自动化, 暂时靠手。

## 实盘 combo (147) 跟 ops alphalib 当前无关

- 147 上跑的 combo 输入 alpha 不消费 alphalib (因子库未投产)
- ops 这边动 alphalib 的任何操作**目前不会影响 147 实盘**
- "因子库投生产" 的标志暂未定义, 之后再讨论

## 跨段网络注意

| 段 | 范围 |
|---|---|
| 10.6/16 (本地办公网) | 本地办公室 144 / 145 / 146 |
| 10.9/16 (北京 IDC) | 160 / 150 / 145 |
| 10.12/16 (上海中信 IDC) | 147 |

- 三段互通但 144 ↔ IDC 走跨段路由, 带宽 / 延迟显著差于 IDC 内部
- **写并发场景生产留 IDC**, 144 主要做研究
- 任何"跨地域脚本"考虑 144 = WAN 节点 (超时调宽 / 避免 chatty 协议)
- 147 内网隔离, ops 代码不在那, 远程操作受限

相关:
- [[reference-company-data-architecture]] — 数据层架构 (rawdata / cc / dm / feature)
- [[reference-cc-all-data-layout]] — cc 物理 layout + .meta + shape
- [[reference-gsim-data-modules]] — Dmgr/Umgr 模板
- [[reference-gsim-xml-config]] — XML config 写法
- [[gsim-architecture]] — gsim 核心模块
