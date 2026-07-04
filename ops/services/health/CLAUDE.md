# Health

Factor library health check. Scans for inconsistencies between filesystem state and cached metadata.

## Checks

- **orphan-dump**: dump dir exists but no matching source in alpha_src
- **orphan-pnl**: pnl dir exists but no matching source
- **missing-dump**: source exists but dump_days == 0
- **missing-pnl**: source exists but no pnl directory
- **missing-metrics**: has pnl but no cached metrics entry
- **missing-datasources**: no cached datasources entry
- **unresolved-tables**: fields parsed but 0 tables resolved

## --fix

Auto-refreshes missing metrics and datasources by running `refresh_metrics` / `refresh_datasources`. Re-evaluates issues after fix. Does not fix orphans or missing dump/pnl (those require manual intervention).

## Filtering

- `--user` filters factors by author before checking
