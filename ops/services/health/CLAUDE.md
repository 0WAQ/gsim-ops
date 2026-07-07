# Health

> **待清理**:本命令计划删除(见根 CLAUDE.md Phase G 剩余)。metrics/datasources 迁 `factor_snapshot`(入库时不可变快照)后,`--fix` 的"重算补全"语义与"快照不可变"相冲突,仅作过渡保留。

Factor library health check. Scans for inconsistencies between the filesystem (alpha_src / alpha_dump / alpha_pnl) and the factor snapshot 层 (`infra/snapshot/`)。

派生数据 (metrics/datasources) 一次 `_load_derived_maps` 读回(单 `snapshot_store.list()`,同一 `FactorSnapshot` 上取 metrics presence + fields/tables),替代旧的两次全表扫描。

## Checks

- **orphan-dump**: dump dir exists but no matching source in alpha_src
- **orphan-pnl**: pnl dir exists but no matching source
- **missing-dump**: source exists but dump_days == 0
- **missing-pnl**: source exists but no pnl directory
- **missing-metrics**: has pnl but no metrics group in the snapshot
- **missing-datasources**: no datasources group in the snapshot
- **unresolved-tables**: fields parsed but 0 tables resolved

## --fix

调 `refresh_metrics` / `refresh_datasources`(`ops/services/list/`)重算并写 snapshot,再重新评估。不修 orphans / missing dump-pnl(需人工)。**注意**:与快照不可变的语义有张力,是待清理项。

## Filtering

- `--user` filters factors by author before checking
