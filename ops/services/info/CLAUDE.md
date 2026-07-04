# Info

显示单个因子的详细信息。

## 输出内容

- 基本信息: author, paths (src/dump/pnl)
- 统计: dump_days, date range, has_pnl
- Metrics: ret%, shrp, mdd%, tvr%, fitness (from cached metrics)
- Data Sources: tables + fields (from cached datasources)

## 数据来源

- `LibraryScanner.get(name)` — 因子基本信息
- `LibraryScanner.get_dump_date_range(name)` — dump 日期范围
- `default_derived_store(config).get(name)` — 单条 `DerivedRecord`,一次读回 metrics + 数据源(fields/tables)

如果 metrics/datasources 缺失，提示用户运行 `ops refresh --metrics` / `ops refresh --datasources`。
