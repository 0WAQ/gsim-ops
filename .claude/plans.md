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

远端是对象存储 (S3-compatible),gsim 和用户态代码依赖 POSIX 文件系统。当前 `ops sync` 做手工 push/pull 桥接,机器变多 + 每台都有写时,维护成本和一致性风险都会涨。短期已通过修补 sync 逻辑临时使用,长期目标是**只引入一个共享文件系统(JuiceFS)**,让 alphalib 对调用方表现为本地文件系统,所有数据类别都长在它上面,Git 作为 app 层用法跑在 JuiceFS 之上,不需要独立的 Git 服务器或 DB。

**单一框架决策**: 早期版本曾考虑 src→Git服务器 / pnl+feature→JuiceFS / state→PostgreSQL 的混合方案,但多技术栈带来的运维与心智成本不值得。横向对比后,只有 JuiceFS 满足"gsim 一行不改 + 多机共享 POSIX"的核心约束,其他单技术方案(全 Git / 全 DB / lakeFS / DVC)要么对二进制日更不友好,要么需要改造 gsim 的数据访问层。因此选定 **JuiceFS 一框打底,Git 跑在它上面**。

**数据特性分类**:

| 类别 | 大小 | 写模式 | 写主体 | 一致性要求 |
|---|---|---|---|---|
| `alpha_src` (.py/.xml/.md) | KB,文本 | 极低(submit/resubmit) | 单 author | 强(代码不可乱) |
| `alpha_pnl` (空格 csv) | 几十 KB ~ MB | 日增 append 一行 | 单机 owner | 中(可重算) |
| `alpha_feature` (np.memmap) | ~170 MB/因子,定长 | 日增写一行(原地) | 单机 owner | 中(并发读多) |
| `alpha_checkpoint` (pickle,未来) | 未定 | 整文件重写 | 单机 owner | 取决于设计 |
| `.state` | KB | 高(每次状态转移) | 多机都会写 | 强(per-factor 分区,锁靠 JuiceFS) |

**已知约束**:
- 当前 1-2 台机器,会扩到 3-4 台
- 写是分区的:新因子在 author 本机生产+pack,旧因子在中央机统一日更生产+pack。同一文件不会有跨机并发写
- 读是全局共享的:所有机器都要看到所有因子的最新 feature/pnl 做研究和复测
- size-only diff bug 当前用临时 sync 改动绕过,长期不应在 sync 模型里继续打补丁

**目标架构**(单一 JuiceFS 挂载点 + Redis 做 metadata 引擎):

```
/mnt/alphalib/          ← 单一 JuiceFS 挂载点
├── alpha_src/          ← Git 仓库,.git 也在挂载点上(共享工作区模式)
│   ├── .git/
│   ├── AlphaXxx/
│   └── ...
├── alpha_pnl/          ← 日增 append,小文件 append 友好
├── alpha_feature/      ← memmap 日增写一行,chunk-level diff(~4MB/天/因子)
├── alpha_checkpoint/   ← 按因子/日期切碎,文件 < 10MB 优先
└── .state/             ← JSON,per-factor 文件 + flock
```

**外部依赖**: 仅 **Redis** 一个(JuiceFS metadata)。无独立 Git 服务器、无 PostgreSQL、无 SQLite。

**选型理由**:

- **JuiceFS**: POSIX 完整(gsim 无需改),metadata 引擎做分布式锁,数据 chunk 化存对象存储(默认 4MB block),本地 cache LRU 命中热数据。memmap 写一行只脏化对应 chunk,~4MB 上传,比 sync 全量重传 170MB/因子小约 40x。代价:多维护一个 metadata 服务(Redis),首次需 `juicefs sync` 把现有 S3 数据导成 chunk 格式
- **Git on JuiceFS(共享工作区模式)**: `.git/` 直接放在 JuiceFS 挂载点上,所有机器看到同一棵树和同一个 `.git/`。`ops submit/resubmit` 拿 flock 后 `git add + git commit`,没有 push/pull(JuiceFS 已替 Git 做完分布式那层)。alpha_src 总量小(几十 MB),FUSE 上的 git 性能损失可接受。如果后续 src 体量涨到痛了,可以平滑升级到"中心 bare repo + 各机本地 clone"模式,无破坏
- **`.state` 留文件,不进 DB**: 写是 per-factor 分区的,JuiceFS 的 POSIX 文件锁就够用。当前"tied updated_at 留本地"那套 merge 补丁可以删掉,退化为"谁的 mtime 新用谁的"。批量改 state(如 `ops approve -u wbai`)用一个全局 lock 文件 + flock 兜底

**接受的妥协**(单一框架的代价):

1. **alpha_src 没有开箱即用的 git history,但有可补救路径**: 共享工作区模式下 `git log/blame` 走 FUSE 会慢几倍(对几百因子规模可接受);`ops diff/log/blame` 命令在 ops 层包一层 `git` subprocess 即可
2. **批量 state 修改需要应用层加锁**: 不是白送,但代码量很小(一个 flock context manager)

**设计原则**(给将来引入 checkpoint 等新数据类型时的指引):

1. 粒度切碎:按因子 + 时间段拆分,避免单个大文件被反复全量重写
2. 优先 append/列存:npy/npz/parquet 优于 pickle,pickle 仅用于小型非数值结构
3. 不可再生才上共享存储:可重算的中间产物应该放本地 SSD,绕开所有同步
4. immutable 优于 mutable:日志型(每次新文件)比覆盖型对 chunk-diff 友好

**迁移路径**(分阶段,低风险,每个 Phase 可独立部署可回退):

| Phase | 内容 | 备注 |
|---|---|---|
| A | 修补 sync 短期可用 | 进行中,size-only bug 绕过,撑到长期方案落地 |
| B | JuiceFS PoC | **第一轮完成 (2026-06-02)**,见下面"PoC 进展"。剩余项见 Phase B-2 |
| B-2 | JuiceFS PoC 第二轮 | 真实 `ops check` 跑通、`ops pack` 增量模式 + chunk 增量量化、跨节点验证(等第二台)、Redis 故障注入、Git on JuiceFS 性能 |
| C | 全量数据迁入 JuiceFS | **前置:Redis HA 必须先上(Sentinel 或 TiKV),详见下方"Redis HA 部署"**。`juicefs sync` 把 alpha_src / alpha_pnl / alpha_feature / .state 从现有 S3 导成 chunk 格式;切 config 指向 `/tank/vault/alphalib/`;`ops sync push/pull` 退役(`sync verify` 降级为巡检) |
| D | alpha_src 接入 Git | 在 `/tank/vault/alphalib/alpha_src/` 初始化 Git 仓库,改造 `ops submit/resubmit` 调 `git add + commit`(加 flock),新增 `ops diff/log/blame` 命令 |
| E | `.state` merge 逻辑简化 | 删掉"tied updated_at 留本地"补丁,改成"mtime 比较 + per-factor 文件锁";批量修改加全局 flock |
| F | checkpoint 落地 | 按设计原则实现,默认放 JuiceFS,可再生的版本放本地 SSD |

**PoC 进展**(2026-06-02 第一轮):

| 验证项 | 结果 |
|---|---|
| 基础读写(小文件 + 100MB) | ✅ 100MB 写 333ms (~300 MB/s),re-read 命中 cache 176ms |
| flock 跨进程串行化 | ✅ 释放→获得间隔 5ms |
| metadata 性能(stat 1000 文件) | ✅ 15ms,Redis ping 34µs |
| **memmap 日增写一行** | ✅ **35ms/因子**,折合 677 因子顺序 pack ≈ 24s |
| 跨进程可见性 | ✅ 写者退出后,新进程立刻读到一致内容 |
| 完整 `ops check` 跑真实因子 | ✅ AlphaWbaiReversal 全流水线通过 (2026-06-02 第二轮),总耗时 ~3.5min 持平本地。暴露 bug:state store 忽略 `-c`(已记 CLAUDE.md tech debt) |
| `ops pack` 增量模式 + chunk 增量量化 | ⏸ 留给 B-2 |
| 跨节点验证 | ⏸ 留给 B-2(等第二台机器) |
| Redis 故障注入 | ✅ (2026-06-02 第二轮) 结论:**JuiceFS 不 hang,Redis 一停立刻 EIO**。所有 syscall(读/写/stat/unlink)全部失败,整个挂载点瘫。**Phase C 前置:必须上 Redis Sentinel 主从** |
| Git on JuiceFS 性能 | ✅ 500 提交基线 (2026-06-02 第二轮): commit 75ms (vs ZFS 21ms),`git log/blame/status` 全部 <250ms。**Phase D Model A(共享工作区)可行,不需要降级到 Model B** |

**PoC 拓扑**(本机单点):

- 挂载点 `/tank/vault/alphalib/`(和现有 `/tank/vault/storage/` = `/mnt/storage/` 软链同级,不冲突)
- 本地 Redis (`127.0.0.1:6379`,PoC 期单实例,生产化挪到 MinIO 那台)
- 本地 cache `/tank/vault/juicefs-cache/`,500 GB 上限
- MinIO 新 bucket `alphalib-juicefs`(独立于现有 bucket,失败回退零成本)
- 凭证: 用 MinIO root key,通过环境变量 `MINIO_ROOT_USER/MINIO_ROOT_PASSWORD` 注入(rclone.conf 里的 `external-client` 是受限只读凭证,不够用)
- 脚本: `scripts/juicefs-poc/`

**关联 TODO**:
- `ops pack` 增量模式见下面独立章节"ops pack Incremental Mode";在 Phase C 之前完成更好,但 JuiceFS chunk diff 不强依赖它
- Phase D 可以和 Phase C 并行,因为 Git 改造不依赖 JuiceFS 是否切完;但放在 C 之后做,因为要先验证 FUSE 上 git 的性能可接受(B-2 包含此项)
- Phase C 完成后,`ops sync` 整个子命令可以删除或保留 `verify` 作为对账
- PoC 期间用了 MinIO root key(暴露在过日志里),进 Phase C 前要旋转一次,并申请专用受限 key

## Redis HA 部署(Phase C 前置,Not Started)

PoC 第二轮 (2026-06-02) 的故障注入数据证明:**单 Redis 是 JuiceFS 集群的硬单点**。Redis 一停,所有节点的所有文件操作(读/写/stat/unlink)立刻 EIO,正在跑的 `ops check`/`pack`/用户脚本会直接崩。Phase C 上线前必须解决,否则一次 Redis 升级/OOM/systemd 重启就毁掉当天所有产出。

**方案对比**:

| 方案 | 故障切换时间 | 部署复杂度 | 资源占用 | 适合规模 |
|---|---|---|---|---|
| 单 Redis + RDB | 不可用(只能恢复数据,不恢复服务) | 极简 | 1 进程 | 仅 PoC |
| **Redis Sentinel(主从 + 哨兵)** | ~10 秒自动 failover | 中等 | 2 Redis + 3 Sentinel,几百 MB 内存 | **3-10 台节点首选** |
| Redis Cluster | 同上,分片 | 高 | ≥ 6 节点 | 数据量超百 GB 才需要 |
| TiKV | 秒级,自带 HA | 高,3 节点起 | 重 | JuiceFS 官方推荐企业级方案 |

**选 Sentinel 的理由**:
- 我们 metadata 体量(几十万因子文件 + 状态)永远到不了需要 Cluster 分片的规模
- TiKV 部署运维成本明显高于 Sentinel,边际收益不值
- 10 秒 failover 窗口可接受 —— 用户脚本如果在这窗口里写入会拿到瞬时 EIO,需要 ops 这层做一次重试包装(放到 Phase E 一起做)

**部署拓扑**(3 节点起,机器够就分散到不同物理机):

```
node1: redis-master  + sentinel
node2: redis-replica + sentinel
node3:                 sentinel    (轻量,可以是 ops 客户端机)
```

- Sentinel 必须 ≥ 3 个且分散在不同机器,否则脑裂时无法达成 quorum
- master 和 replica 不能放同一台机,否则机器挂 = 主从同挂 = HA 失效
- JuiceFS 元信息 URL 从 `redis://127.0.0.1:6379/1` 改成 `redis-sentinel://127.0.0.1:26379,host2:26379,host3:26379/mymaster/1`,JuiceFS 客户端会自动跟随 master 切换

**待做**:
1. 决定 3 台机器分配(目前只有 2 台,等第三台 → 或者临时把第三个 sentinel 放在 MinIO 服务器上)
2. 写 Ansible/Shell 部署脚本 + 配置模板(`sentinel.conf` 的 `quorum=2`、`down-after-milliseconds=5000`、`failover-timeout=10000`)
3. 故障演练:一次完整的 master kill → sentinel 选举 → replica 晋升 → JuiceFS 客户端自动重连 → 业务恢复
4. RDB + AOF 持久化策略(防止双主同时挂时数据丢失)
5. 监控:Sentinel 状态 + master 切换告警(挂到 Feishu 或 Prometheus)

**前置依赖**:
- 第三台机器到位(或 MinIO 那台兼任 sentinel)
- 内网时钟同步正常(NTP),否则 Sentinel 选举会抖
- 防火墙开 6379 / 26379

## ops pack Incremental Mode (Not Started)

把 `ops pack` 从"全量重写 alpha_feature/{name}.{v}.npy"升级成"按需只覆写指定日期那一行"。这是 Phase 3 的 roadmap 项,设计已完成,工程量小但**暂缓实施**(2026-06-02 决定)。

**为什么需要**:
- 当前每次 `ops pack` 都重写整文件(170 MB/因子)。在 JuiceFS 上意味着所有 chunk 都脏化,全量上传,40x 浪费
- 增量模式下 mmap('r+') 只写指定行 → 单 chunk(4 MB)脏化 → S3 增量 ~4 MB
- 是 Phase E 切到 JuiceFS 后,日更场景成本的主要决定因素

**为什么暂缓**:
- 现有 `ops sync` 模型下,即使做了增量 pack,sync 那侧仍按文件级 size+mtime 比对,等大小判断会漏掉(等做完 JuiceFS 迁移再做才能真正吃到收益)
- Phase B-2 第二轮 PoC 可以用一次性脚本量化 chunk 增量(不需要正式集成到 ops),数据足够支撑 Phase C 决策

**已实现部分**:
- `ops/services/pack/pack.py:164` 的 `pack_one_incremental(name, dates, config)` 已写好:`mmap('r+')` 覆写指定行,目标不存在则回退全量
- 缺的只是 CLI 接入和 worker 路由

**待做(总工程量 ~30 行代码 + 0.5 小时验证)**:

1. **CLI 加 `--date YYYYMMDD`**(`ops/cli/pack.py`):
   ```
   ops pack --date 20260602                     # 所有有该日期 dump 的因子
   ops pack --date 20260602 -f AlphaXxx         # 单因子单日期
   ops pack --date 20260601,20260602            # 多日期,逗号分隔
   ```
   不支持 range / "last:N" 之类的复杂语法,先简单

2. **互斥规则**:
   - `--date` + `--force` → 报错(语义冲突)
   - `--date` + `--factor` → 允许
   - `--date` 无 `--factor` → 扫所有 `alpha_dump/Alpha*/{Y}/{M}/{date}*.npy` 存在的因子

3. **service 层小重构**:`pack_one_incremental` 签名从 `(name, dates, config)` 改成 `(name, dates, alpha_dump, alpha_feature, alpha_src, date_to_idx, shape, delay, verify)`,和 `pack_one` 对齐。避免在 worker 子进程里重复 `load_universe()`

4. **`_pack_worker` 加分支**:`dates is None ? pack_one : pack_one_incremental`,保留 `factor_lock` 包装(per-factor 串行,天然解决"同因子同天并发覆写"的竞争)

5. **验证脚本**(在 JuiceFS PoC 环境跑一次,不入正式代码):
   ```bash
   B0=$(rclone size poc:alphalib-juicefs/ --json | jq .bytes)
   ops pack -c config.juicefs.yaml --date 20251231 -f AlphaWbaiReversal
   sleep 10  # writeback 上传
   B1=$(rclone size poc:alphalib-juicefs/ --json | jq .bytes)
   echo "delta = $((B1 - B0)) bytes"
   ```
   **预期 delta ≈ 4 MB(单 chunk),vs 全量 ~170 MB。这数字定 Phase C 成本估算的生死**

**不打算做的**:
- `--verify-only-touched-dates`:增量 sample 验证。增量本身简单(只覆写一行),出错概率低,verify 收益不大;默认行为可让用户用 `--no-verify` 加速
- `PACK_L` 动态化:独立的 roadmap 项,和增量无关
- range / "last:N" 等糖语法:用 cron + 单日期循环即可

**触发条件**:
- 立即做的前提:Phase D/E 准备启动,需要增量来压成本
- 或者:有人开始关心日更全量重写的 S3 流量费用
