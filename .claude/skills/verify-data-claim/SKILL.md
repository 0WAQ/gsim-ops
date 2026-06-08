---
name: verify-data-claim
description: Verify a user-reported data anomaly (e.g. "field X in 2019 has no data", "field Y in 20241231 is 0"). Handles fuzzy field name matching, time range parsing, and classifies each claim as real-bug / by-design-misread / NaN-as-0 / side-finding. Use when user pastes feedback from researchers about specific cc fields.
---

# Verify Data Claim

核实用户反馈的具体数据异常, 自动区分**真 bug** 和**误读**, 顺手挖出**副作用线索**。

## 调用

```
/verify-data-claim <自由文本反馈>
```

例:
```
/verify-data-claim AShareMoneyFlow.{buy_volume_med_order, open_money_flow_pct_volume_1} 2019和2020没有数据; equ_factor_derive/AssetImpairLossTTM 20241230/20241231 都是0
```

## 步骤

直接调 `cc-data-auditor` subagent, 把用户原文整段传过去。subagent 负责:

### 1. 解析反馈

拆出每个"主张"(claim):
- 字段名 (可能写错, 需 fuzzy 匹配)
- 时间范围 (`2019和2020` / `20241230/31` / `最近一周`)
- 异常类型 (没数据 / 是 0 / 是 NaN / 是 inf / 范围异常 / ...)

### 2. fuzzy 匹配字段名

模糊规则:
- `_1` ↔ `_l` (视觉 / `l` = large)
- `money_flow` ↔ `moneyflow`
- `pct_change` ↔ `pctchange`
- 大小写不敏感
- 后缀变体: 用户说 `*_ALL20` 实际匹配整组同后缀

```bash
# 找候选
find /datasvc/data/cc_all -maxdepth 3 -iname '*<fuzzy_keyword>*' 2>/dev/null
```

- 候选 = 0: 报"找不到, 类似的有..."
- 候选 = 1: 直接验证
- 候选 ≥ 2: 列出来问父对话

### 3. 跨 4 个 root 验证

四个 root 必查:
- `/datasvc/data/cc_all` (生产)
- `/datasvc/data/cc_2024` (旧)
- `/tank/vault/datasvc/data/cc_2024` (新)
- `/tank/vault/datasvc/data/cc_2025` (新)

对每个 root 在用户指定的时间范围内统计:
- nan / zero / nonzero_finite 数 (按 cell 或 按日)
- min / max / 几个样例值
- **first_data_date / last_data_date** (沿 N 归约找首末有效日) — 关键, 用来 distinguish:
  - "字段从来没数据 (源真没)" — first_data_date 为 None
  - "字段中段停更" (例 HK_HOLDVOL_CHG_*20 截至 20240816) — last_data_date 远早于 cutoff
  - "字段范围正常但被 trim 截掉" — last_data_date 接近 cutoff
- 这几个值要在每个反馈条目里报出, 帮助用户判断是 build 漏还是源没

### 4. 分类结论

每条 claim 归类:

| 分类 | 触发条件 | 例 |
|---|---|---|
| ✓ **真 bug** | 应有数据但全 NaN / 全 0, 跨多 root 都缺 | AMF 2019-2020 在老 cc_2024 上全 NaN, 新 root 上有 → 老 root build 漏 |
| ⚠ **by-design 误读** | 用户报"20241231 是 NaN" 而该 root enddate=20241231 | enddate 那天永远 NaN 占位 |
| ⚠ **NaN vs 0 误读** | 实际是 NaN, 用户用 nansum/fillna(0) 当成 0 看了 | 报告说"是 0" 实际 cell 值是 NaN |
| · **副作用线索** | 查该字段时发现别的真问题 | 反馈说 HK_HOLDVOL_CHG_ALL20 在 20241231 是 0, 实际是该字段从 2024-08-16 起停更 10 个月 |

### 5. 输出

```
## 反馈核实

### 条目 1: <原文摘要>
- 字段: <fuzzy 匹配后实际文件名>
- 状态: ✓ / ⚠ / · 
- 证据:
   cc_all      : nan=X, zero=Y, finite=Z
   cc_2024_old : ...
   cc_2024_new : ...
   cc_2025_new : ...
- 结论: <1-2 句>
- action: <推荐用户做什么>

### 条目 2: ...

## 副作用发现 (如果有)
- <顺手挖出的真 bug>
```

## 何时不该用

- 用户问的是"哪些字段有问题" (没具体反馈) → 用 `/audit-cc`
- 用户问的是代码 bug 怎么改 → 主对话直接讨论
