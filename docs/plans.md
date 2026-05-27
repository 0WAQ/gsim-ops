# Plans

Deferred / not-yet-started plans. See `CLAUDE.md` for current architecture.

## Architecture Refactor (Not Started)

Restructure from current flat layout to layered architecture. All existing commands must keep working. No new features, no new dependencies.

**Current problems**:
1. `common/` is a grab-bag — config, SSH, email, gsim runner, alpha metadata all mixed
2. Business logic coupled with CLI — check pipeline logic embedded in argparse handler
3. `AlphaMetadata.__init__` modifies XML and writes to disk — constructor side effects
4. Hardcoded values — SSH username='wbai', host='10.6.100.146', password='123456'
5. Duplicate abstractions — `utils.Gsim` vs `runner.Runner`, two `BacktestError`
6. Stub code — `results/base.py`, `exception.py`, `checkpoint.py` are empty shells
7. No layering — adding future Web API requires rewrite

**Target structure**:
```
ops/
├── core/                  # Data models + pure computation (no I/O)
│   ├── alpha.py           # AlphaKey, AlphaMetadata (no disk write in constructor)
│   ├── metrics.py         # Metrics, CheckResult
│   └── library.py         # FactorInfo and related models
│
├── services/              # Orchestration: combines core + infra
│   ├── check.py           # Check pipeline scheduling (read files -> call checkers -> archive)
│   ├── checker/           # All 6 checkers together (they are pipeline stages)
│   │   ├── base.py        # CheckFail/CheckSkip + Checker ABC
│   │   ├── checkbias.py   # DataFirewall AST injection + backtest
│   │   ├── checkpoint.py  # Breakpoint validation
│   │   ├── backtest.py    # Long backtest
│   │   ├── compliance.py  # Position limits check
│   │   ├── correlation.py # Factor correlation check
│   │   ├── archive.py     # Pass/fail archiving
│   │   └── firewall.py    # DataFirewall + _SafeProxy
│   ├── gsim.py            # Gsim interaction (merge Runner+Gsim, single BacktestError)
│   └── library.py         # Factor library ops (scan, get, filter)
│
├── infra/                 # Infrastructure: file I/O, external systems
│   ├── config.py          # Config loading + path resolution + ${var} substitution
│   ├── cache.py           # Index cache (~/.cache/ops/)
│   ├── notify.py          # Feishu/email notifications
│   └── ssh.py             # SSH connections (username from config, not hardcoded)
│
├── cli/                   # CLI entry: argparse + formatted output
│   ├── main.py            # Entry point + subparser registration
│   ├── check.py           # ops check (thin: parse args -> call service -> output)
│   ├── list.py            # ops list
│   ├── info.py            # ops info
│   ├── cp.py              # ops cp
│   └── fmt.py             # Table/color/progress output utilities
│
└── utils.py               # Common utilities (date_range, md5sum, LowerAction)
```

**Design principles**:
- CLI and future API both call the same services layer
- Core has no I/O dependencies; services handle all I/O
- All 6 checkers live together in `services/checker/` (they are pipeline stages, not independent modules)
- Gradual migration: new code imports old code during transition, delete old modules one by one (no big-bang delete wave)
- No empty placeholder directories (no `api/` until needed)

**Execution plan** (5 waves, 14 tasks):

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1-3 | Skeleton: directory structure, `ops/utils.py`, `core/` models (alpha, metrics, library) |
| 2 | 4-7 | Infra layer: config, cache, ssh, notify |
| 3 | 8-10 | Services: `gsim.py` (merge Runner+Gsim), `checker/` (all 6 stages + firewall), `check.py` (pipeline), `library.py` |
| 4 | 11-13 | CLI: `fmt.py`, `main.py` + entry point update, subcommands (list, info, check, cp) |
| 5 | 14 | Delete old code incrementally + full verification + fix imports |

**Key migration details**:
- Task 1: Split models by domain — `core/alpha.py` (AlphaKey, AlphaMetadata), `core/metrics.py`, `core/library.py` (FactorInfo)
- Task 1: `AlphaMetadata.__init__` no longer writes to disk; `_modify_always()` extracted as `prepare_for_check()` in `services/check.py`
- Task 8: Merge `common/runner.py` Runner + `common/utils.py` Gsim into single `GsimService` class, single `BacktestError`
- Task 12: Update `pyproject.toml` entry point to `ops.cli.main:main`

**Verification after each wave**:
```bash
uv run ops --help
uv run ops list
uv run ops list -u jzhang
uv run ops info AlphaJzhang20260324GA002
uv run ops check --help
uv run ops cp --help
```

## Factor Management Enhancement (Not Started)

Enhance factor management: data source parsing, PNL metrics extraction, health checks.

**Deliverables**:
- Data source parser: extract `dr.getData()` calls from Python code
- PNL metrics extraction via `simsummary` (ret/shrp/dd/fitness)
- Enhanced `ops info` with data sources and PNL metrics
- Enhanced `ops list` with Sharpe column and `--sort` parameter
- New `ops health` command for library integrity checks

**getData call patterns** (observed from real code):
1. Simple: `dr.getData('ashareeodprices.s_dq_close')`
2. With .data: `dr.getData('ashareeodprices.s_dq_volume').data`
3. Special: `dr.getData('cap')`, `dr.getData('status')`, `dr.getData('st')`
4. Dynamic (f-string): `dr.getData(f'equ_fancy_factors_table{i}.xxx')` -> extract static part, mark dynamic as `<dynamic>`

**Key constraints**:
- DO NOT trust XML `<Data>` declarations for data sources
- DO NOT trust Readme.txt for PNL metrics — must use `simsummary` on actual PNL files

**Execution plan** (3 waves, 7 tasks):

| Wave | Tasks | Description |
|------|-------|-------------|
| 1 | 1-2 | Data source parser (`ops/common/datasource.py`), ~~enhance `Metrics` with `dd` field and `from_pnl()` class method~~ ✅ done |
| 2 | 3-5 | ~~Integrate into `LibraryScanner` (new fields + cache version bump), enhance `ops info` and `ops list` output~~ ✅ done |
| 3 | 6-7 | New `ops health` command: orphan factors, dump gaps, PNL missing, source missing, file integrity |

**Health check output format**:
```
Factor Library Health Check
────────────────────────────────────────────────────────────
OK: 7 factors in alpha_src
OK: 7 factors in alpha_dump
WARNING: 2 factors missing PNL files
ERROR: 1 factor has dump date gaps
────────────────────────────────────────────────────────────
Summary: 7 OK | 2 WARNING | 1 ERROR
```

## Factor Lifecycle Architecture (Next)

Factor lifecycle: `提交(submitted) → 验证中(checking) → 入库(active) / 拒绝(rejected) → 监控(monitored) → 衰减(decaying) → 废弃(retired)`.

**Phase 1: 状态管理 + submit/status/backfill + 一致性** ✅ done

Implemented `ops submit` / `ops status` / `ops backfill`, state tracking in `CheckerPipeline`, `meta.json` per factor as identity card, per-factor advisory lock (`infra/lock.py`), and reconcile pass at check startup. See the Factor Lifecycle section in `CLAUDE.md`.

**Phase 2: 因子质量监控** — Rolling IC/IR, coverage, autocorrelation, correlation drift. SQLite replaces JSON store. `ops monitor` command (cron). Threshold alerts via Feishu.

**Phase 3: 计算编排** — Factor DAG, incremental updates, retry/alerting. `ops run`, `ops retire`, `ops recheck`.

**Phase 4: 服务化** — FastAPI over services layer, Redis cache, Streamlit/Grafana dashboard.

## Consolidate `ops status` into `ops list` + `ops info` (Not Started)

`ops list -s <status>` now covers batch lifecycle filtering (with status-based row coloring) and `ops info <factor>` covers single-factor static info, so `ops status` is mostly redundant. Its only unique surface today is single-factor lifecycle history (the check history list).

**Plan**:
- Move single-factor history rendering into `ops info <factor>` (append a "Lifecycle" / "Check History" section to its existing output).
- Remove `ops status` subcommand: delete `ops/cli/status.py` registration and `ops/services/status/`. Drop the `ops status` line from CLAUDE.md and the example block.
- Verify nothing else imports `ops.services.status`.

**Why deferred**: cosmetic UX cleanup, no functional gap. Do once after the next round of feature work settles.

## `ops factor` Namespace + Cross-Machine Soft-Delete (Not Started)

The CLI surface has grown flat: `submit / check / list / info / health / pack / sync / rm / status / backfill`. The factor-lifecycle ones (`submit`, `check`, `rm`, `info`, `list`, `status`, `backfill`) all act on a single factor (or a query over factors) and naturally belong under one namespace. Plan: introduce `ops factor <verb>` as the canonical home, keep flat aliases for back-compat during transition.

**Target shape**:
```
ops factor add <name>      # alias: ops submit (one factor, possibly inline source)
ops factor rm <name>       # alias: ops rm        (current implementation)
ops factor check [name]    # alias: ops check
ops factor run <name>      # NEW — re-run an existing factor (for refresh / re-pack)
ops factor info <name>     # alias: ops info
ops factor list            # alias: ops list
ops factor status [name]   # alias: ops status   (until folded into info, see prior plan)
```

`pack`, `sync`, `health` stay top-level — they operate on the library, not a single factor.

**Why**: discoverability (`ops factor --help` enumerates everything one can do *to* a factor), and prepares the codebase for similar groupings later (`ops dataset ...`, `ops job ...`).

**Soft-delete model** (already partially landed via `ops rm`):

- `FactorStatus.DELETED` is a tombstone, not a record removal. Files default to staying on disk; `--force` removes local dump + feature only (src/pnl always kept, mirroring the rejected-factor retention rule).
- The tombstone propagates to other machines via `ops sync push` state merge — receivers will hide the factor from `list` / `health` automatically because those filter `status == DELETED` by default.
- **`ops sync push` does NOT issue `rclone delete` for DELETED factors.** State merge alone is the sync mechanism. Remote files stay until a future `ops sync gc` (separate, opt-in) reclaims them.
- `ops sync pull` does not auto-clean local files of newly-DELETED-on-remote factors either. Same reasoning: keep destructive ops out of routine sync; require explicit `gc`.

**`ops sync gc` (deferred)**:
- Walks `factor_state.json`, finds `status == DELETED` records older than some threshold (e.g. 30d), enumerates their remote `alpha_dump/<name>/`, `alpha_feature/<name>.v[12].npy`, and (with extra flag) `alpha_src/<name>/` + `alpha_pnl/<name>`, deletes via `rclone delete`.
- Default-dry-run; explicit `--apply` to actually purge.
- Updates manifest to drop the entries so subsequent push doesn't re-stat them.

**Execution waves** (when picked up):
| Wave | Description |
|---|---|
| 1 | Add `ops factor` parent parser; register existing subcommands as both flat (legacy) and nested (`factor X`). |
| 2 | Implement `ops factor run` (re-run + re-pack one factor in place). |
| 3 | Add `ops sync gc` with dry-run/apply. |
| 4 | Drop the flat aliases (one-shot deprecation, after team is on the new shape). |
