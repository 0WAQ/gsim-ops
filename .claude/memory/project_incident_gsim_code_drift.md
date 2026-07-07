---
name: incident-gsim-code-drift-2026-06-06
description: "CRITICAL — gsim 代码三地双向漂移事件 (2026-06-06 发现), 实盘 147 vs 研究 160 同名源/编译文件不一致, 待上级决策, 本地不动手 sync"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# 事件: gsim 代码三地双向漂移

**发现日期**: 2026-06-06
**严重级别**: CRITICAL
**状态**: 已上报待决, 本地不修
**详细报告**: `docs/incidents/2026-06-06-gsim-code-drift-three-sites.md`

## 一句话摘要

实盘机 147 (10.12.174.152) 跟数据/研究机 160 (10.9.100.160) 上的 `gsim/` 代码长期双向独立演化, 同名源文件 + `.so` 内容不一致, 包括回测核心 `alpha_node.so` (体积差 6 倍 / 时差半年), combo 算法 (production gating 只在 147), 相关性计算 (1e-8 数值保护只在 147), Universe 实现 (160=.so / 147=.py + abi3), FeatureReader (只在 160), 实盘优化算子 (RiskOpt/Topt/StatsOptV1 只在 147)。

## 业务含义 (给未来 session)

- **160 跑出来的 PNL ≠ 147 实盘 PNL**, 不能假设跨机器一致
- **`ops check` correlation 阶段的数值跨机器不一致** (Oputil 1e-8 差异)
- **`ops pack` 出的 alpha_feature 矩阵 147 读不了** (147 没 FeatureReader)
- **147 实盘 XML 在 160 上 ImportError** (优化算子缺失), 反之亦然
- **没有 single source of truth**, 不能简单单向 sync 任何一边

## How to apply (未来 session 处理这块时)

1. **不要尝试 unilateral 修复或 sync** — 已确认要请示上级决策, 任何文件覆盖都可能丢真实业务逻辑
2. 任何"在 160 上 reproduce 实盘行为" / "假设三地代码一致"的设计前提都要先 challenge
3. ops 设计时**不要假设** alphalib 自动喂给实盘 combo —— 147 缺 FeatureReader, 这条桥本身没通
4. 涉及 correlation 数值的逻辑要意识到 Oputil 行为有跨机差异
5. **`.so` 是黑箱**: 框架级源码不在 wbai 手里, RiskOpt / StatsOpt / alpha_node 等编译产物归属和源码访问待上级澄清
6. 如果用户要继续推进相关工作, 先确认上级是否已经回复决策 (Q1-Q5 见 incident doc)

## 决策点摘要 (详见 incident doc §10)

| 编号 | 问题 |
|---|---|
| Q1 | canonical source 是 147 / 160 / 独立 git / 维持现状? |
| Q2 | 双向 fork 怎么 reconcile, 谁的改动回流给谁? |
| Q3 | 防再次漂移的机制 (git+CI / cron 比对 / JFS / 不做)? |
| Q4 | `.so` 编译权限和源码归属 (alpha_node / RiskOpt / StatsOpt)? |
| Q5 | 短期止血 (冻结改动? 加跨机一致性 check?)? |

## 复现

```bash
mkdir -p /tmp/147/gsim
rclone copy 39000:external-sync/147/gsim/ /tmp/147/gsim/
# 详见 incident doc §11
```

相关:
- [[reference-server-topology]] — 三地拓扑 + 147 角色
- [[reference-gsim-architecture]] — gsim 框架结构 (Stats V5/V6 等)
- [[reference-gsim-data-modules]] — Dmgr / Umgr / NIO_MATRIX
- [[factor-business-context]] — "当前无真生产环境" 这条要校正: combo 在 147 跑实盘, 只是 alphalib 没投产
