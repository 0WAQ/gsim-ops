---
name: project-alpha-dump-constraints
description: Why alpha_dump uses per-date small npy files and the dependency chain between dump/feature/gsim
metadata: 
  node_type: memory
  type: project
  originSessionId: 4d30b5fa-c0fc-4da2-b265-d86f4740e681
---

alpha_dump 使用每天一个 .npy 的小文件格式是 gsim 的历史遗留设计：

1. gsim 回测输出就是 per-date .npy（v1=原始信号, v2=经过 Operation 处理后的信号）
2. gsim 支持直接导入 alpha_dump 做 combo 等二次组合
3. alpha_feature 是 pack 后的矩阵，给 QR 做模型/ML 用，所以叫"特征"

**Why:** gsim 既是 dump 的生产者也是消费者，格式由 gsim 决定。

**How to apply:** 如果要消除小文件问题(迁移到 zarr 等), 需要先为 gsim 实现一个 reader 模块, 能从 feature 矩阵中按日期切片返回等价的 dump 数据给 gsim 其它模块(combo 等)。`AlphaLoadFeat` 在 2026-05-28 已实现 (`/usr/local/gsim/gsim/alpha/module/alpha_load_feature.py`, 见 [[alpha-dump-to-feature-migration]]), 但 gsim 侧消费切换还没完成, 在此之前 alpha_dump 不能丢弃。

相关: [[alpha-dump-to-feature-migration]], [[gsim-architecture]]
