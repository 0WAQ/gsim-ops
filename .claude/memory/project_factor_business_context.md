---
name: factor-business-context
description: 因子业务背景:alpha_src 权限模型、数据产物可再生性、当前无真正生产环境、REJECTED 因子的多样性价值(approve)
metadata: 
  node_type: memory
  type: project
  originSessionId: 43713e1f-b5bd-4fb7-9b3b-689b0790c707
---

## alpha_src 权限模型

- alpha_src 是所有因子的 src 归档,不区分状态(ACTIVE/REJECTED 都在里面,靠 state 区分;**没有 DELETED 状态**——rm 是彻底硬删,删了就不在库里了)
- 研究员没有 alpha_src 的读写权限
- (recycle 目录已于 2026-07 退役,REJECTED 因子 src 与 ACTIVE 同在 alpha_src,不再有独立副本)

## 数据产物可再生性

| 产物 | 可再生? | 代价 | 说明 |
|---|---|---|---|
| alpha_src | 不可再生 | — | 研究员智力产出 |
| alpha_pnl | 代价高 | 长回测耗时,且不同 Stats 模块产出不同 | 尽量保留 |
| alpha_dump | 可再生 | gsim 每日产出 | 中间产物 |
| alpha_feature | 可再生 | ops pack 重建 | dump 聚合 |

## 当前生产状态

- 目前没有真正的生产环境(gsim 实盘未上线)
- ACTIVE 因子"参与生产"是未来状态,当前只是"通过验证入库"
- 因此 restage(原 recheck)ACTIVE 因子时不需要考虑"暂停生产"问题

## REJECTED 因子的业务价值

- 有些手写因子质量很高,但被机器挖的因子挤占生存空间(correlation 阶段被拒)
- 更根本:自动流水线只认业绩+低相关,盲区是**不看数据使用覆盖**。用了稀缺数据但相关/业绩不占优的因子会被 correlation 必拒且无自动路径可救 —— `ops approve` 就是为此的人工闸(REJECTED→ACTIVE,不重跑),放行"扩数据覆盖多样性"的因子。详见 [[project-cli-command-redesign]]。
- 后两阶段(compliance/correlation)失败的因子数据产物完整,有分析参考价值

**How to apply:** 设计命令和流程时,考虑研究员无 alpha_src 权限这一约束;保留 REJECTED 因子的完整数据产物(后两阶段失败时)供研究员分析。
