---
name: incident-cc-data-drift-2026-06-06
description: "cc_all 数据 160 vs 147 指纹比对结果 (2026-06-06), 91% 等价, 重点问题: pwang 在 160 缺 2011-2012, Basedata/st.npy dtype 漂移, fore_* 异步 build 差异"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# 事件: cc_all 数据 160 vs 147 指纹比对

**发现日期**: 2026-06-06
**严重级别**: MEDIUM
**状态**: 待决, 本地不动手
**详细报告**: `docs/incidents/2026-06-06-cc-data-drift-160-vs-147.md`
**工具**: `scripts/data-audit/cc_fingerprint{,_diff}.py`

## 一句话摘要

cc_all 在 160 (北京 IDC) 和 147 (上海中信 IDC) 上, T ≤ 20241231 范围内 2106 个共同 .npy 文件, 91% 等价 (sum + nan_count 沿 N 归约后 `np.allclose(rtol=1e-5)` 通过)。剩 9% 集中在三类问题。

## 数据架构隐含含义 (给未来 session)

1. **build_cc 跨机基本可信**: 即使代码有 silent drift, 主流数据等价 → `source_ref/Dmgr_*` 是数据 build 的 ground truth, 其确认 byte-identical 跟数据 91% 等价吻合
2. **forecast 类 feature 跨机不严格一致是设计行为**: `income_statement_fore_*`, `financial_summary_fore_*`, `revenue_forecast_*`, `DmgrPwang_industry_*` 这些数据来自 datayes 每天更新的 forecast, 两机异步 build 必然差。不是 bug。
3. **Basedata 跨机有边缘 case**: `cap.npy` / `capfree.npy` 在个别日期股票计数差 1, `st.npy` 直接 dtype 不同。这些跟 universe 一致性强相关, 影响因子的 mask
4. **pwang 因子在 160 上时间窗 < 2013-01 全 NaN**: 用 pwang 因子做长历史回测要意识到

## 关键发现

| 问题 | 严重度 | 数量 | 处置 |
|---|---|---|---|
| pwang industry 在 160 缺 20110104~20121231 (487 连续天) | 高 | 41 文件 | 待 wbai/pwang 决定重 build |
| Basedata/st.npy dtype 跨机不同 (160=int8 / 147=float64) | 高 | 1 | 跟代码漂移一起请示 |
| 财务 / forecast 异步 build 差异 | 中 | 137 文件 | 接受现状, 文档化 |
| Basedata/cap / capfree 边缘日期 1 股差 | 低 | 2 文件 | 接受现状 |
| Basedata/status 单日 1 bit 翻转 | 误报 | 1 文件 | v2 修指纹 |
| signal_rsh 残留 (160) + _bak 残留 (147) | 清理 | 2 文件 | 安全清理 |
| Dmgr_MarketStats 147 独有 | 待查 | 1 文件 | 确认 160 该不该补 |

## How to apply (未来 session 处理这块时)

1. **不要 unilaterally 修复数据** — 全部待决
2. 对于 forecast 类 feature, 在涉及"跨机 reproducibility"的设计 / 文档里要明示**异步 build 差异是设计行为**
3. pwang 因子 / 用 pwang 派生的 combo, 在 160 上跑 pre-2013 回测会拿全 NaN — 设计时要意识到
4. `Basedata/st.npy` schema 不一致 → 涉及 ST 过滤的逻辑, 跨机行为可能不同
5. 工具 `scripts/data-audit/cc_fingerprint.py` 可用, 后续接 cron 定期审计

## 关联事件

- [[incident-gsim-code-drift-2026-06-06]] — 代码 drift 报告 (同一天审计批次)。`Basedata/st.npy` dtype 漂移跟代码报告里 `umgr_all.py` / `Universe.so vs .py` 差异直接相关, **应一起决策**
- [[reference-server-topology]] — 三地拓扑 + cc 各跑 build_cc 的设计前提 (本次审计验证了这条)
- [[reference-cc-all-data-layout]] — cc 物理 layout (本次审计未触发任何 layout 假设修正, 内容仍可信)
