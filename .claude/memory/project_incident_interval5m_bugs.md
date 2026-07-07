---
name: incident-interval5m-bugs-2026-06-07
description: "Interval5m 派生字段 (pctchange, ret, vwap) 在 cc_all 全 0 (build 漏) + 源码 3 处除零保护缺失 bug, 待 wbai 重 build / 修代码"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# 事件: Interval5m 数据缺陷

**发现日期**: 2026-06-07
**严重级别**: MEDIUM
**状态**: 待修, 本地不动手 (用户要求先报告为 bug)
**详细报告**: `docs/incidents/2026-06-07-interval5m-bugs.md`
**关联代码**: `/usr/local/gsim/source_ref/interval_5m_zx.py`

## 一句话摘要

`Interval5m.{pctchange, ret, vwap}` 三个派生字段, 在 cc_all 上**整个全 0** (build 漏, 不是 NaN 是真 0), 在 cc_2024 / cc_2025 上数据基本可用 (99.9999% 合理) 但**源码有 3 个除零保护缺失 bug** 导致尾部少量 inf / 负 vwap (1e-3% ~ 1e-4% 比例)。

## 关键发现

| 问题 | 影响范围 | 处置 |
|---|---|---|
| cc_all 上三个 derived 字段全 0 | ~10 亿 cells, 任何因子读 cc_all 这三字段拿到 0 不报错 | wbai 已确认要重 build cc_all |
| `ret` 公式不防 `open == 0` → +inf | 20.1 万 cells (0.045% of finite) | 改 `interval_5m_zx.py:103` 加保护 |
| `pctchange` 公式不防 `close[ti-1] == 0` → +inf | 132 cells | 改 `interval_5m_zx.py:104` |
| `vwap` 公式不防 `amo < 0` → 负值 | 249 cells | 改 `interval_5m_zx.py:106-110` |

## 业务含义 (给未来 session)

1. **cc_all 上 Interval5m 三 derived 字段在重 build 之前不能用** — 拿到全 0 静默错
2. cc_2024 / cc_2025 上**数据基本可用**, 但偶发 inf / 负 vwap, 下游代码不能假设这些字段恒 finite / 恒正
3. 源码 `interval_5m_zx.py` 是 wbai 维护的, 不是黑箱, 可以本地修 (跟之前的 .so 编译产物漂移问题不同)
4. cc_2024 / cc_2025 在这三个字段上 **byte-identical**, 说明这俩 root 是同源 build (或一边 copy 自另一边)

## How to apply (未来 session 处理这块时)

1. 任何用 cc_all 上 Interval5m derived 字段的因子, 在 wbai 完成 rebuild 之前**不要相信结果**, 数据是 0
2. 涉及 vwap 的代码注意防 `< 0` (源数据偶发负数)
3. 涉及 ret / pctchange 的代码注意防 `inf` (现有数据有, 修复后没有)
4. 修代码很简单 (3 处保护性 if), 但**用户当前要求先报告不修**

## 修复路径 (待用户授权后)

```python
# Bug 2.1 修法 (line 103):
op = self.open[di, ti, ii]
self.ret[di, ti, ii] = (self.close[di, ti, ii] / op - 1.0) if op != 0 else np.nan

# Bug 2.2 修法 (line 104):
prev_close = self.close[di, ti-1, ii] if ti != 0 else 0
self.pctchange[di, ti, ii] = ((self.close[di, ti, ii] / prev_close) - 1.0) if (ti != 0 and prev_close != 0) else np.nan

# Bug 2.3 修法 (line 106-110): 加 amo < 0 检查
amo = self.amo[di, ti, ii]
vol = self.vol[di, ti, ii]
if vol == 0 or np.isnan(vol) or amo < 0 or np.isnan(amo):
    self.vwap[di, ti, ii] = np.nan
else:
    self.vwap[di, ti, ii] = amo / vol
```

## 关联事件

- [[incident-gsim-code-drift-2026-06-06]] — gsim 代码三地漂移 (CRITICAL, 黑箱 .so 问题, 不能本地修)
- [[incident-cc-data-drift-2026-06-06]] — cc 数据 160 vs 147 漂移 (MEDIUM, 含本次 Interval5m 异常被发现的上下文)
- [[reference-cc-all-data-layout]] — cc 物理 layout, 含"enddate 那天 NaN 占位"规则
- [[reference-gsim-data-modules]] — Dmgr/Umgr 模板 (Interval5m 也是这个模板)
