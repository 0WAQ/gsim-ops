---
name: gsim-architecture
description: gsim 回测框架的目录结构、核心模块、工具链和数据组织方式
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

gsim 是位于 `/usr/local/gsim/` 的量化因子回测框架, 是 ops 交互的核心引擎。

## 顶层目录结构

```
/usr/local/gsim/
├── gsim/              # 核心 Python 包 + C++ .so
│   ├── alpha/         # 因子基类 (AlphaBase)
│   ├── combo/         # 组合模块 (FeatureReader 加载 alpha_feature)
│   ├── data/          # 数据管理 (DataManagerMapped, builtin Dmgr/Umgr)
│   ├── stats/         # 统计模块 (stats_simple_v5/v6 等)
│   ├── operator/      # 因子后处理 (Decay, Rank, IndNeut 等)
│   ├── utils/         # 工具函数 (NioData 等)
│   ├── gsim_base.cpython-310-x86_64-linux-gnu.so
│   ├── gsim_portfolio.cpython-310-x86_64-linux-gnu.so
│   ├── gsim_checkpoint.cpython-310-x86_64-linux-gnu.so
│   ├── alpha_node.cpython-310-x86_64-linux-gnu.so
│   └── gsim.xsd       # XML config schema
├── tools/             # 分析工具 (simsummary.py, bcorr.py)
├── dataops/           # 编译版工具 (bcorr — 更快的 C++ 版本)
├── alpha_src/         # 因子源码示例 + 模板因子
├── combo_src/         # 组合源码 (.py / .so 都有)
├── source_ref/        # rawdata → cc 转换 module (~62 个 Dmgr/Umgr .py)
├── dm_src/            # cc → dm 派生 + level2 read-only adapter (~43 个 .py)
├── docs/              # gsim 自己的文档 (有 stats.md)
├── pnl_prod/          # 生产环境 PNL 池 (bcorr 默认对比对象)
├── pnl_prod_bak/      # 生产 PNL 备份
├── pnl_pool/          # delay=1 PNL 池
├── pnl_pool_d0/       # delay=0 PNL 池
├── pnl_pool_llm/      # LLM 因子 PNL 池
├── run.py             # 回测入口
├── run_cp.py          # checkpoint 回测专用入口
├── gsim_compact.py    # ?
├── read_npy.py        # 辅助脚本
├── pyproject.toml + uv.lock + .venv/
```

## 核心命令

```bash
# 回测
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml   # checkpoint 模式

# PNL 汇总
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl

# 相关性测试
/usr/local/gsim/dataops/bcorr pnl1 pnl2                          # 文件 vs 文件
/usr/local/gsim/dataops/bcorr pnl1 /usr/local/gsim/pnl_prod/     # 文件 vs 目录
```

## Stats 模块版本

`gsim/stats/` 下有一堆 `.so`, 多版本并存:
- `stats_simple_v5.so` / `stats_simple_v6.so` — 主线统计 (V5 / V6)
- `stats_simple_D0.so` — delay=0 专用
- `stats_bench.so` / `stats_bench_layer.so` — 指数增强 / 分层
- `stats_long.so` / `stats_longshort.so` / `stats_ls.so` — long-only / 多空
- `stats_index_gim_long.so` — 指数增强 GIM
- `StatsOptV5.so` — 优化版 V5
- `stats_naive.so`, `stats_simplex.so` 等

ops 当前用 **`StatsSimpleV6`** (`ops/services/check/xml_prepare.py:19`), 旧的 `/datasvc/template/config.read_cache.xml` 和 gsim 自带 `docs/stats.md` 还写 `StatsSimpleV5`。

**V5 ≡ V6 (功能上)**, V6 比 V5 多了 checkpoint 支持。所以 V5 的 mode 参数文档对 V6 同样适用:
- `0`: Long Short(多空)
- `1`: StatsBench(指数增强)
- `2`: StatsBenchLayer(分层统计, thres=90 表示 top 10%)
- `3`: Long Only(纯多头)

## 数据缓存系统

物理位置 `/datasvc/data/{cc_2024, cc_2025, cc_all}/`(只读, 二进制 memmap 平面), 详见 [[reference-cc-all-data-layout]] 和 [[reference-company-data-architecture]]。

模块层 (`source_ref/` + `dm_src/` + `gsim/data/`) 详见 [[reference-gsim-data-modules]]。XML 怎么把数据 module 串起来详见 [[reference-gsim-xml-config]]。

访问入口: `dr.getData('xxx.field')` —— 注意 tag 是扁平 namespace, 物理目录被忽略, 见 [[reference-gsim-data-modules]]。

## 性能关键模块 (.so)

C++ 编译产物 (cython, abi3 / cp310):
- `gsim/gsim_base.so` / `gsim_portfolio.so` / `gsim_checkpoint.so` / `alpha_node.so`
- `gsim/stats/stats_*.so`
- combo / alpha 的部分 .so

相关:
- [[reference-cc-all-data-layout]] — cc 物理 layout / shape / 字段清单
- [[reference-company-data-architecture]] — 三层架构 + owner 分工
- [[reference-gsim-data-modules]] — Dmgr/Umgr 模板 + NIO_MATRIX + tag namespace
- [[reference-gsim-xml-config]] — XML config 骨架
- [[factor-validation-pipeline]] — ops check 流水线
