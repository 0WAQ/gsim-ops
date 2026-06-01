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

## Alphalib Storage Backend Migration (Not Started)

远端是对象存储 (S3-compatible),gsim 和用户态代码依赖 POSIX 文件系统。当前 `ops sync` 做手工 push/pull 桥接,机器变多 + 每台都有写时,维护成本和一致性风险都会涨。短期已通过修补 sync 逻辑临时使用,长期目标是按数据类别用对的后端,让 alphalib 对调用方表现为本地文件系统。

**数据特性分类**:

| 类别 | 大小 | 写模式 | 写主体 | 一致性要求 |
|---|---|---|---|---|
| `alpha_src` (.py/.xml/.md) | KB,文本 | 极低(submit/resubmit) | 单 author | 强(代码不可乱) |
| `alpha_pnl` (空格 csv) | 几十 KB ~ MB | 日增 append 一行 | 单机 owner | 中(可重算) |
| `alpha_feature` (np.memmap) | ~170 MB/因子,定长 | 日增写一行(原地) | 单机 owner | 中(并发读多) |
| `alpha_checkpoint` (pickle,未来) | 未定 | 整文件重写 | 单机 owner | 取决于设计 |
| `.state` | KB | 高(每次状态转移) | 多机都会写 | 强(merge 冲突=故障) |

**已知约束**:
- 当前 1-2 台机器,会扩到 3-4 台
- 写是分区的:新因子在 author 本机生产+pack,旧因子在中央机统一日更生产+pack。同一文件不会有跨机并发写
- 读是全局共享的:所有机器都要看到所有因子的最新 feature/pnl 做研究和复测
- size-only diff bug 当前用临时 sync 改动绕过,长期不应在 sync 模型里继续打补丁

**目标架构**:

```
alpha_src/          → Git (内部 GitLab/Gitea)
alpha_pnl/          → JuiceFS  (append 友好,chunk diff 友好)
alpha_feature/      → JuiceFS  (memmap + 日增写一行,chunk-level diff 强需求)
alpha_checkpoint/   → JuiceFS  (前提:按因子/日期切碎,文件 < 10MB)
                      or 本地 SSD (如果可再生,完全绕开同步)
.state/             → PostgreSQL / Redis / SQLite  (脱离文件,获得真正的锁)
```

**选型理由**:

- **JuiceFS**: POSIX 完整(gsim 无需改),metadata 引擎做分布式锁,数据 chunk 化存对象存储(默认 4MB block),本地 cache LRU 命中热数据。memmap 写一行只脏化对应 chunk,~4MB 上传,比 sync 全量重传 170MB/因子小约 40x。代价:多维护一个 metadata 服务(Redis/TiKV),首次需 `juicefs sync` 把现有 S3 数据导成 chunk 格式
- **alpha_src 走 Git**: 现有 submit/resubmit 已经手工模拟 commit/version,直接用 Git 白送 history/diff/blame/code review,对象存储里没有这些
- **.state 脱离文件**: 当前"tied updated_at 留本地"是没有强一致环境下的补丁,多机并发写一定会丢更新,只是概率低。机器变多后必须升级

**设计原则**(给将来引入 checkpoint 等新数据类型时的指引):

1. 粒度切碎:按因子 + 时间段拆分,避免单个大文件被反复全量重写
2. 优先 append/列存:npy/npz/parquet 优于 pickle,pickle 仅用于小型非数值结构
3. 不可再生才上共享存储:可重算的中间产物应该放本地 SSD,绕开所有同步
4. immutable 优于 mutable:日志型(每次新文件)比覆盖型对 chunk-diff 友好

**迁移路径**(分阶段,低风险,每个 Phase 可独立部署可回退):

| Phase | 内容 | 备注 |
|---|---|---|
| A | 修补 sync 短期可用 | 进行中,size-only bug 绕过,撑到长期方案落地 |
| B | `alpha_src` 迁 Git | 改造成本最低,收益直观。`ops submit/resubmit` 改写为 `git commit + push` |
| C | `.state` 迁数据库 | 优先级看多机并发写频率,频率高就提前做 |
| D | JuiceFS PoC | 单机挂载 + 新 bucket,影子模式双写 1-2 周,验证 gsim 读写、ops pack 并发 |
| E | `alpha_feature` + `alpha_pnl` 切 JuiceFS | 全量 `juicefs sync` 导入,切 config 指向挂载点,`ops sync push/pull` 退役 |
| F | checkpoint 落地 | 按设计原则实现,默认放 JuiceFS,可再生的版本放本地 SSD |

**关联 TODO**:
- `ops pack` 增量模式(roadmap 别处提到)在 Phase E 之前完成更好,但 JuiceFS chunk diff 不强依赖它
- Phase E 完成后,`ops sync verify` 可降级为"挂载点 vs 备份 bucket"的对账巡检
