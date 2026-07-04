# Health

Factor library health check. Scans for inconsistencies between the filesystem (alpha_src / alpha_dump / alpha_pnl) and the DerivedStore (`infra/derived/`, Postgres or json fallback).

派生数据 (metrics/datasources) 一次 `_load_derived_maps` 读回(单 `get_all`,同一 DerivedRecord 上取 metrics presence + fields/tables),替代旧的两次全表扫描 (`load_metrics` + `load_datasources`)。

## Checks

- **orphan-dump**: dump dir exists but no matching source in alpha_src
- **orphan-pnl**: pnl dir exists but no matching source
- **missing-dump**: source exists but dump_days == 0
- **missing-pnl**: source exists but no pnl directory
- **missing-metrics**: has pnl but no metrics group in the DerivedStore
- **missing-datasources**: no datasources group in the DerivedStore
- **unresolved-tables**: fields parsed but 0 tables resolved

## --fix

Auto-refreshes missing metrics and datasources by running `refresh_metrics` / `refresh_datasources`. Re-evaluates issues after fix. Does not fix orphans or missing dump/pnl (those require manual intervention).

## Filtering

- `--user` filters factors by author before checking
