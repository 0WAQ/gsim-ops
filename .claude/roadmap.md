# Roadmap

Feature checklist. Done items are kept for reference. See `.claude/plans.md` for detailed design of unstarted items.

> 注(2026-07-20):下列 [x] 含已退役命令(sync/backfill/health 等)与已删概念
> (derived 层),退役清单以根 `CLAUDE.md` Removed 节为准。

## Factor Storage & Management
- [x] `ops list` - List all factors (filter by author)
- [x] `ops info <factor>` - View factor details
- [x] Index caching for fast queries
- [x] Factor data sources (parse `dr.getData()` from Python code; resolved to tables via npy index)
- [x] Filter by tables/fields/metrics (`--filter-by "tables=ashare*,ret>30"`)
- [x] PNL metrics in info/list (ret%, shrp, mdd%, tvr%, fitness from simsummary)
- [x] Batch metrics refresh (`ops list --refresh-metrics`)
- [x] Incremental metrics update (saved during `ops check` archive step)
- [x] Sort and limit (`ops list --sort shrp -n 10`)
- [x] `ops health` - Factor library health check
- [x] Factor state tracking (submitted/checking/active/rejected lifecycle)
- [x] `ops submit` - Structured factor submission from dropbox to staging
- [x] `ops status` - Query factor lifecycle state
- [x] `ops backfill` - One-shot meta.json + ACTIVE state for legacy factors
- [x] Per-factor advisory lock (concurrent submit/check safety)
- [x] ~~State ↔ filesystem reconcile on check startup~~ (reconcile 已下线 2026-07；crash-mid-check 由下次 `ops check` 扫 staging 自愈，无对账)
- [x] `ops pack` - Aggregate alpha_dump → alpha_feature (batch + incremental from check)
- [x] `ops sync` - Cross-server library sync via S3/boto3 (data + state, stable library_id replaces hash cache keys)
- [x] `ops rm` - Hard-delete a factor (src/pnl/dump/feature + state 行 + derived 行，不可逆，无墓碑；仅 `-y` 跳过确认，无 `--force`)
- [ ] ~~`ops sync gc` - Reclaim remote files for DELETED factors~~ (SUPERSEDED 2026-07-04：soft-delete/DELETED 模型已废弃，`ops rm` 改硬删，无墓碑可回收)
- [ ] `ops factor` namespace consolidating add/rm/check/run/info/list (see `.claude/plans.md`)
- [ ] Daily incremental pack path (`ops pack --date YYYYMMDD`,设计已完成暂缓实施 — 详见 `.claude/plans.md` "ops pack Incremental Mode")
- [ ] Alphalib storage backend migration (single JuiceFS mount + Redis metadata; Git runs on top of JuiceFS — see `.claude/plans.md`)
- [ ] Factor registry, versioning, tags/categories
- [ ] Enable/disable, archive/unarchive factors

## Factor Lifecycle & Monitoring
- [ ] Automated Feishu notifications on check pass/fail
- [ ] Rolling IC / IC_IR monitoring (20/60 day windows)
- [ ] Factor coverage monitoring (sudden drop = data source failure)
- [ ] Factor autocorrelation monitoring (spike = factor death)
- [ ] Correlation drift detection
- [ ] `ops monitor` command (cron-based)
- [ ] Threshold-based decay alerts

## Computation & Orchestration
- [ ] Factor computation DAG with dependency tracking
- [ ] Incremental update vs full recompute
- [ ] Retry with exponential backoff
- [ ] `ops run` for orchestrated factor computation
- [ ] Batch operations: `ops retire`, `ops restage`

## Factor Analysis
- [ ] Factor-to-factor correlation matrix, clustering, redundancy detection
- [ ] PNL decomposition, alpha decay, turnover analysis
- [ ] Max drawdown, volatility, VaR/CVaR

## Factor Combination
- [ ] Multi-factor synthesis (equal, IC-weighted, optimization-based)
- [ ] Factor orthogonalization (residualization, PCA, Gram-Schmidt)
- [ ] Portfolio optimization (mean-variance, risk parity, constraints)

## Production & Service
- [ ] FastAPI wrapper over services layer
- [ ] Redis cache layer
- [ ] Streamlit/Grafana dashboard
- [ ] Daily signal/position generation, smoothing, transaction cost modeling
- [ ] Cron scheduling, failure alerting, run history
- [ ] Live PNL tracking, health dashboard, anomaly detection
