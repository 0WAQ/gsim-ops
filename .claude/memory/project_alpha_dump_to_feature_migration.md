---
name: alpha-dump-to-feature-migration
description: alpha_dump 小文件问题和向 alpha_feature 迁移的背景、动机和当前状态
metadata: 
  node_type: memory
  type: project
  originSessionId: 0dc2ca03-0ee3-4cac-b874-f0ee5ec0c49d
---

gsim 正在从 `alpha_dump`（小文件）向 `alpha_feature`（聚合文件）迁移。

## 问题

`alpha_dump` 将因子值组织为 `yyyy/mm/yyyymmdd{v1,v2}.npy` 格式：
- 20100101-20251231 约 140 个文件夹，5400 个文件/因子
- 3000 个因子 = 42 万个文件夹，1600 万个文件
- 存储和传输都非常低效

## 解决方案

`alpha_feature` 将每个因子的所有日期聚合为单个文件，通过 gsim alpha module `AlphaLoadFeat` 加载 (源码 `/usr/local/gsim/gsim/alpha/module/alpha_load_feature.py`)。注意它是 alpha module 不是独立 reader, 通过 combo XML 实例化, 路径约定 `{featDir}/{alphaId}.{ver}.npy`, 用 `np.memmap` 切片取当日 alpha。

## 当前状态（2026-05-28）

- gsim 新增 `combo` 模块，支持通过 `alpha_feature` 加载因子 (`AlphaLoadFeat`)
- `alpha_dump` 逐步弃用，推荐使用 `alpha_feature`
- ops 已实现 `ops pack` 命令，将 `alpha_dump` 聚合为 `alpha_feature`
- sync 已优化：alpha_dump 降级为纯本地中间产物，不再同步

## 待实现

`ops pack` 增量模式 (见 [[ops-roadmap-ideas]]):
- `ops pack --date YYYYMMDD`
- PACK_L 动态化
- 并发安全

**Why:** alpha_dump 是 gsim 遗留设计，小文件问题严重影响存储和传输效率。gsim 既产出又消费 dump，消除小文件需先实现 gsim feature reader（已完成）。

**How to apply:** 
- 新因子开发时，优先使用 `alpha_feature` + `FeatureReader`
- 避免在 sync 中传输 `alpha_dump`
- `ops pack` 相关开发需考虑增量模式和并发安全
