---
name: audit-cc
description: Validate one cc root and summarize quality issues (all_zero / inf / freshness gaps), filtering out heuristic noise. Use when user asks "check cc_all", "audit cc_2025", "看下 cc_2024 有什么问题" etc.
---

# Audit CC Root

对一个 cc root (默认 `/datasvc/data/cc_all`) 跑完整质量审计, 自动剔除 heuristic 误报, 只报真问题。

## 调用

```
/audit-cc [root]
/audit-cc                                     # 默认 /datasvc/data/cc_all
/audit-cc /tank/vault/datasvc/data/cc_2024    # 指定 root
```

## 步骤

1. **跑 cc_validate**

   ```bash
   cd /home/wbai/gsim-ops/scripts/data-audit
   python3 cc_validate.py --root <ROOT> --out /tmp/audit_<basename>.json \
       --progress-every 200 > /tmp/audit_<basename>.log 2>&1
   ```

   全量 ~15-30 min, 后台跑 (`&`), 报 PID 给用户, 用 `wait $PID` 或者 `while kill -0` 等待。

2. **freshness 失守自动 flag**: `cc_validate.py` 已内置 cohort 比对, 报告里 `stale_findings` 字段直接列出"末日远早于同目录 cohort median 的字段"。无需额外脚本。

   触发条件: 同目录文件数 ≥ 3, 该字段 `last_data_idx` 比 cohort median 早 ≥30 个交易日。

   例: `HK_HOLDVOL_CHG_*20` 末日 20240816, 同目录 17 字段末日 20260601, gap=89d → 自动 critical。

3. **调 subagent `cc-data-auditor` 解读结果**:

   - 把 `/tmp/audit_*.json` 路径给它
   - 让它分类: `真 critical (build 漏 / inf)` / `freshness 失守` / `已知 incident (link)` / `heuristic 误报 (drop)`
   - 输出结构化报告

4. **输出给用户**:

```
## CC Root 审计: <ROOT>
扫了 N 文件, 真问题 X 个, 已知问题 Y 个, 误报 Z 个

### ✓ Freshness 失守 (X, 来自 stale_findings)
- field: last=YYYYMMDD (cohort median=YYYYMMDD, gap=Xd)
- 推测: 源数据停推 / config 改动 / module bug

### ✓ 真 build 漏 (all_zero, X 个)
- file1: ...
- file2: ...

### ✓ 真 inf bug (除零, X 个)
- file: 数量 X, 推测来源 dmgr_xxx.py

### · 已知 incident (link)
- ...

### Heuristic 误报 (skip, X 个)
不展开, 详细 grep `/tmp/audit_*.json` 里 severity=critical
```

## 何时不该用这个 skill

- 用户问"X 字段有问题吗" → 用 `/verify-data-claim`
- 用户问"A 跟 B 数据一致吗" → 用 `/compare-cc`
- 用户问"为什么 inf" / 想看具体代码 bug → 主对话直接读代码
