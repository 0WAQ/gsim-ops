# Info

显示单个因子的详细信息。

## 输出内容

- 基本信息: author, paths (src/dump/pnl)
- 统计: dump_days, date range, has_pnl
- Metrics (入库时快照): ret%, shrp, mdd%, tvr%, fitness + snapshot_at
- Data Sources: tables + fields (入库时快照)

## 数据来源

- `LibraryScanner.get(name)` — 因子基本信息 + dump 日期范围
- `default_info_store(config).get(name)` — 身份信息 (author 等)
- `default_snapshot_store(config).get(name)` — 单条 `FactorSnapshot`,一次读回入库时 metrics + 数据源 (fields/tables) + snapshot_at

Metrics/datasources 是**入库时不可变快照**(非最新表现)。缺失说明因子未入库或未通过 check;`ops refresh` 已删除,无重算路径。
