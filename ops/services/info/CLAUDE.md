# Info

显示单个因子的详细信息。

## 输出内容

- 基本信息: author, paths (src/dump/pnl)
- 统计: dump_days, date range, has_pnl
- Metrics (入库时快照): ret%, shrp, mdd%, tvr%, fitness + snapshot_at
- Data Sources: tables + fields (入库时快照)

## 数据来源

- **存在性判据 = `factor_info`(PG)**(2026-07-07 Wave 2;原用 alpha_src 目录存在,与 status/rm 的 state 判据不一致,同一因子可能 status 存在、info not found,full-review S5)
- `FactorRepository.get(name)` — 一次取 `Factor` 聚合(2026-07-09,取代三个 store 各查一次):`identity`(author)、`state`(标题栏显示 status)、`snapshot`(入库时 metrics + 数据源 + snapshot_at);返回 None 即 factor_info 无行
- `LibraryScanner.get(name)` — 单因子现场 stat(物理事实:has_pnl/dump_days;src 目录缺失时显式提示漂移而不是 not found)

Metrics/datasources 是**入库时不可变快照**(非最新表现)。缺失说明因子未入库或未通过 check;`ops refresh` 已删除,无重算路径。
