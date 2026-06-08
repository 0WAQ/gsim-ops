# report/

cc 数据审计报告归档。

每份审计报告对应一次跨多 root / 跨服务器的全量比对, 包含:

- 顶层 markdown: 人类可读的总结 + 推荐 action
- `data/`: 原始 JSON (validate / diff 输出), 可机器解析或重新分析

## 历史报告

| 日期 | 报告 | 范围 |
|---|---|---|
| 2026-06-08 | [本地 cc 全量审计](2026-06-08-local-cc-audit.md) | 4 本地 root (cc_all + 老 cc_2024 + 新 cc_2024 + 新 cc_2025), 跳过 147 |

## 怎么再跑一次

```bash
# 用 skill (推荐)
/audit-cc /datasvc/data/cc_all

# 或者直接跑
cd /home/wbai/gsim-ops/scripts/data-audit
python3 cc_validate.py --root <ROOT> --out report/data/validate_<name>.json
```

跑完报告 + 原始数据放本目录, 让 cc-data-auditor subagent 解读 + 写新 markdown。
