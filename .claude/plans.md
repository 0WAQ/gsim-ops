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

Implemented `ops submit` / `ops status` / `ops backfill`, state tracking in `CheckerPipeline`, `meta.json` per factor as identity card, per-factor advisory lock (`infra/lock.py`)。（原 reconcile pass at check startup 已下线 2026-07：crash-mid-check 由下次 `ops check` 扫 staging 自愈，无对账。）See the Factor Lifecycle section in `CLAUDE.md`.

**Phase 2: 因子质量监控** — Rolling IC/IR, coverage, autocorrelation, correlation drift. SQLite replaces JSON store. `ops monitor` command (cron). Threshold alerts via Feishu.

**Phase 3: 计算编排** — Factor DAG, incremental updates, retry/alerting. `ops run`, `ops retire`, `ops restage`.

**Phase 4: 服务化** — FastAPI over services layer, Redis cache, Streamlit/Grafana dashboard.

## Consolidate `ops status` into `ops list` + `ops info` (Not Started)

`ops list -s <status>` now covers batch lifecycle filtering (with status-based row coloring) and `ops info <factor>` covers single-factor static info, so `ops status` is mostly redundant. Its only unique surface today is single-factor lifecycle history (the check history list).

**Plan**:
- Move single-factor history rendering into `ops info <factor>` (append a "Lifecycle" / "Check History" section to its existing output).
- Remove `ops status` subcommand: delete `ops/cli/status.py` registration and `ops/services/status/`. Drop the `ops status` line from CLAUDE.md and the example block.
- Verify nothing else imports `ops.services.status`.

**Why deferred**: cosmetic UX cleanup, no functional gap. Do once after the next round of feature work settles.

## `ops factor` Namespace (Not Started) — soft-delete 部分已废弃见下

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

**~~Soft-delete model~~ (SUPERSEDED 2026-07-04)**: 原计划让 `ops rm` 打 `FactorStatus.DELETED` tombstone + `ops sync gc` 回收。**已废弃并反向实现**:DELETED 状态 + `deleted_at` 已从代码彻底移除,`ops rm` 现在是**彻底硬删**(src/pnl/dump/feature + state 行 + derived 行,不可逆,无墓碑)。设计哲学:因子要么存在(active/rejected/未来 decay)要么被删除,删除不是一种状态。`ops sync gc` 也不再需要(sync 整体退役中)。

**Execution waves** (when picked up — 仅剩 namespace 部分):
| Wave | Description |
|---|---|
| 1 | Add `ops factor` parent parser; register existing subcommands as both flat (legacy) and nested (`factor X`). |
| 2 | Implement `ops factor run` (re-run + re-pack one factor in place). |
| 3 | Drop the flat aliases (one-shot deprecation, after team is on the new shape). |

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
| B-3 | Step 1: 挂载布局 + 权限模型 | **完成 (2026-06-03,2026-06-04 简化为集中运维)**。sidecar symlink (alpha_dump/staging/recycle) + 两层组(alpha-core/alpha-data,仅作跨机 group label,不授予写)+ setgid。**集中运维**:owner 一律 root,所有写都走 sudo;group/other 只读。**放弃 POSIX ACL**(ZFS pool `acltype=off`,改 pool 风险大),改用 setgid 继承组,umask 0002 在 sudo-only 模型下不再必要(保留 hook 不删)。详见 `scripts/juicefs-poc/README.md` |
| B-4 | Step 2: 持久化 + 跨节点 | **完成 (2026-06-04)**。主节点 redis 网络化 + AUTH(密码进 `/etc/juicefs/<name>-jfs.env` 0600 root,由 systemd `EnvironmentFile=` 注入,不进 ps cmdline)+ AOF (`appendfsync everysec`);`04-systemd.sh` 渲染 unit 接管挂载 + ExecStop 三级 fallback。`join.sh` 实测 150 一键接入 + sidecar 一致性校验。跨节点 visibility + flock 实测通过 |
| B-5 | Step 3: 独立 redis 实例(分进程隔离) | **完成 (2026-06-04)**。`06-redis-jfs.sh` 起 `redis-jfs.service`:6380 专给 JuiceFS;`06-meta-migrate.sh` MIGRATE 69080 keys 6379→6380(反向白名单 + 分批 + 灾备 dump)。原因:server-160 上 6379 跟 alphalib biz 共用,任何 `systemctl stop redis` / OOM / FLUSHDB 会跨产品爆。**实测过一次事故**:改 conf 加 AOF 后 redis 7 用空 AOF 优先于 RDB,dbsize 归零;靠 `/var/backups/redis-recover-*` 完整恢复 69111 keys。03-redis.sh 已改成运行时 `config set` 不重启避雷 |
| B-6 | 灾备路径验证 | **完成 (2026-06-04)**。服务器异常重启(writeback drain 中,staging=92912 / 362G)→ ExecStop 链 sync + ZFS cache 持久 → 重启后 unit 自动起 + 从 cache 续传 → 数据 0 丢失。Redis dump.rdb 恢复实测通过 |
| B-7 | ops state 进 redis | **完成 (2026-06-04)**。新增 `ops/infra/store/redis_store.py` 实现 `StateStore` 接口,schema 跟 JsonStateStore 1:1 映射(`state:<lib>:<name>` hash + `state-index:<lib>` set + `state-checks:<lib>:<name>` list),WATCH/MULTI/EXEC 做 read-modify-write。`Config` 加 `state.backend: redis | json` 切换,`config.juicefs.yaml` 切到 redis 指向 6380。`ops/tools/state_migrate.py` 一次性 JSON→redis 迁移(3224 records 已迁完)。`ops list` 在 160 上跟 prod 路径输出 bit-for-bit 一致。**修了 redis-py 8.x `from_url` 不honor `protocol=2` 导致 requirepass-only server HELLO 失败的雷**(commit fc4d8f8)。**Phase C 的 state 同步前置已解决** |
| B-8 | Redis Sentinel HA | **完成 (2026-06-05)**。3-sentinel 集群:160 master+sentinel-① / 150 replica+sentinel-② / 144 sentinel-③(纯投票)。`07-redis-replica.sh` / `08-sentinel.sh` / `09-switch-meta-url.sh` 分别处理 replica / sentinel daemon / META_URL 切换。`ops/infra/store/redis_store.py` 加 `redis-sentinel://h:p,h:p/svc/db` URL scheme 支持,走 `redis.sentinel.Sentinel.master_for()` 自动 failover。**演练 9.12s failover**(down-after 5s + 投票),ops 透明重连,JFS 在三节点继续可用。**踩坑**:sentinel 写 `replicaof` 时不会补 `masterauth`,得在 conf 里预设(commit 39602aa fix);redis daemon 没 conf 写权所以 `CONFIG REWRITE` EACCES,持久化得 sudo sed |
| C | 全量数据迁入 JuiceFS | **完成 (2026-06-05)**。数据三机对账 entry count + 字节 + md5 完全一致 (alpha_src 3217 / alpha_pnl 3153 / alpha_feature 5876,总 936 GB)。`config.juicefs.yaml` 切 sentinel-aware redis-sentinel URL。**ops sudo wrapper** 通过 `ops/infra/sudo.py:maybe_elevate` 在 write 命令 + alpha_src root-owned 时自动 exec sudo (commit 928ae6f);**password auto-discovery** 通过 `state.redis.password_file` 让 fresh shell 也能跑 (commit dedcd63)。`04-systemd.sh` 把本机 redis-jfs 从 `Requires` 降到 `Wants` 避免 failover 演练时把 JFS unit 也带停 (commit 8a2f597)。三机 fresh shell smoke `ops list` / `ops status` 输出一致,user 无需手动 export env. **`ops sync push/pull` 暂未删,留作 fallback / 巡检 (`sync verify`),后续 Phase 清理** |
| D | alpha_src 接入 Git | 在 `/tank/vault/alphalib/alpha_src/` 初始化 Git 仓库,改造 `ops submit` 调 `git add + commit`(加 flock),新增 `ops diff/log/blame` 命令 |
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
| 完整 `ops check` 跑真实因子 | ✅ AlphaWbaiReversal 全流水线通过 (2026-06-02 第二轮),总耗时 ~3.5min 持平本地。曾暴露 state store 忽略 `-c` 的 bug,已于 045fcb1 修复 |
| `ops pack` 增量模式 + chunk 增量量化 | ⏸ 留给 B-2 |
| 跨节点验证 | ⏸ 留给 B-2(`join.sh` 已就绪,等 150 实测) → **B-2 已完成** 见下面新条目 |
| 跨节点验证 | ✅ (2026-06-04) 160 ↔ 150 visibility (bit-level OK) + flock 跨节点互斥 + 释放后另一端立即获得 |
| Redis 故障注入 | ✅ (2026-06-02 第二轮) 结论:**JuiceFS 不 hang,Redis 一停立刻 EIO**。所有 syscall(读/写/stat/unlink)全部失败,整个挂载点瘫。~~Phase C 前置:必须上 Redis Sentinel 主从~~ → **已部署 B-8 (2026-06-05)** |
| Redis Sentinel failover 演练 | ✅ (2026-06-05) 160 master kill → sentinel 5s 共识 + 4s 投票/同步 = **9.12s failover** → 150 promoted → JFS 三节点 mount/rw OK → ops 透明重连。旧 160 重启自动降级 replica(预设 masterauth 后) |
| Redis dump.rdb 灾备路径 | ✅ (2026-06-04) Redis 7 切 AOF 雷踩过一次,从 dump.rdb 完整恢复 69111 keys。recovery 流程已写进 README 故障排除 |
| 服务器异常重启 | ✅ (2026-06-03) writeback drain 中重启 (staging=92912/362G),ExecStop 链 sync + ZFS cache 持久 → 重启后续传 → 数据 0 丢失 |
| 独立 redis 实例 vs 共用 | ✅ (2026-06-04) `redis-jfs.service`:6380 与 alphalib biz 6379 进程级隔离,任一重启不影响另一边 |
| 第三节点 144 接入(不同磁盘布局) | ✅ (2026-06-04) `/storage/vault/` 而非 `/tank/vault/`,通过 `/tank/vault/alphalib.local -> /storage/vault/alphalib.local` 软链让 JFS sidecar 绝对路径解析正常 |
| ops state 进独立 redis 实例 | ✅ (2026-06-04) `RedisStateStore` 跟 `JsonStateStore` 同接口,1:1 schema 映射,3224 records 迁完,跨节点强一致。修了 redis-py 8.x 的 HELLO 雷(`from_url` 不 honor `protocol=2`,改用直接 `Redis()` ctor)|
| Git on JuiceFS 性能 | ✅ 500 提交基线 (2026-06-02 第二轮): commit 75ms (vs ZFS 21ms),`git log/blame/status` 全部 <250ms。**Phase D Model A(共享工作区)可行,不需要降级到 Model B** |

**PoC 拓扑**(主节点 + client 多节点,2026-06-04 起):

- 挂载点 `/tank/vault/alphalib/` **是 160-specific**(ZFS pool 在那)。其他节点通过 `/etc/juicefs-poc.env` 覆盖 `JFS_MOUNT/JFS_CACHE_DIR/JFS_LOCAL_DIR/JFS_META_URL`,`join.sh --mount/--cache/--meta-port` 自动写
- Redis **分两个进程实例**(B-5 之后):
  - `redis-server.service:6379` — alphalib biz 业务用,**JuiceFS 完全不动**
  - `redis-jfs.service:6380` — JuiceFS metadata + **ops state**(B-7 后)专用,AOF on (`appendfsync everysec`),独立 conf/data dir,密码存 `/etc/juicefs/<name>-jfs.env` (0600 root) 由 systemd `EnvironmentFile=` 注入。**B-8 后是 Sentinel-managed master,160 master + 150 replica + 144 sentinel-only,quorum=2**
- ops state 走 sentinel URL `redis-sentinel://160:26380,150:26380,144:26380/mymaster/0`(`config.yaml` 的 `state.redis.url`),所有节点强一致。**密码 auto-discovery** 通过 `state.redis.password_file: /etc/juicefs/alphalib-jfs.env`,client 上不需要手动 export(详见 `ops/infra/sudo.py:ensure_redis_password`)
- 本地 cache `/tank/vault/juicefs-cache/`(160),500 GB 上限;client 节点路径由 `/etc/juicefs-poc.env` 决定
- MinIO 新 bucket `alphalib-juicefs`(独立于现有 bucket,失败回退零成本)
- 凭证: 用 MinIO root key,通过 `MINIO_ROOT_USER/MINIO_ROOT_PASSWORD` 或 rclone.conf 注入,**只 `01-provision.sh` 用**
- 脚本: `scripts/juicefs-poc/` 主节点一把梭 `bootstrap-primary.sh` (00→04) + `06-redis-jfs.sh` + `06-meta-migrate.sh`;Sentinel HA `07-redis-replica.sh` + `08-sentinel.sh` + `09-switch-meta-url.sh`;client `join.sh` 一键;`status.sh` 健康检查;`05-migrate.sh` 数据迁入

**关联 TODO**:
- `ops pack` 增量模式见下面独立章节"ops pack Incremental Mode";JuiceFS chunk diff 不强依赖它
- Phase D 可以和 Phase C 并行,因为 Git 改造不依赖 JuiceFS 是否切完;但放在 C 之后做,因为要先验证 FUSE 上 git 的性能可接受(B-2 包含此项)
- ~~Phase C 完成后,`ops sync` 整个子命令可以删除或保留 `verify` 作为对账~~ **C 已完成 (2026-06-05), ops sync 暂未删, 留作 fallback;后续清理**
- MinIO root key rotation:上线期间仍用 root key,稳定后旋转 + 申请专用受限 key

**Phase C 上线后的剩余项**(2026-06-05 上线时已完成 #3 + #5 自身,剩 nice-to-have):

| # | 项 | 状态 |
|---|---|---|
| 1 | 写入重试 wrapper:failover 5-10 s 窗口 redis EIO retry (3 次 backoff 2/5/10 s) | TODO |
| 2 | `LibraryScanner` per-machine cache 不一致:各机 `~/.cache/ops/lib/<lib>/index.json` 独立。`ops list --refresh` 对齐,长期搬 redis | TODO |
| ~~3~~ | ~~切默认 config~~ | ✅ done (commit 8749da7, config.yaml = JFS) |
| 4 | `ops sync push/pull` 加 deprecation warning + 整体退役(留 `sync verify` 作巡检) | TODO |
| ~~5~~ | ~~ops sudo wrapper~~ | ✅ done (commit 928ae6f + dedcd63); 安全收尾(ops binary 装 root-owned 路径 + NOPASSWD)未做 |
| 5b | sudo NOPASSWD wrapper 安全收尾:ops 装 root-owned 路径(/usr/local/bin)+ `/etc/sudoers.d` 限定 NOPASSWD | TODO |
| 6 | MinIO root key rotation:PoC 期间用 root key 暴露过日志 | TODO |
| 7 | alpha_dump 退役:gsim feature reader 已就绪,alpha_dump 不再共享只本机临时;`ops sync verify alpha_dump` 段可删 | TODO |
| 8 | 删 prod 数据 `/mnt/storage/alphalib/`:上线稳定一周后 | TODO |

## Redis HA 部署(已完成 2026-06-05)

**已落地拓扑**:
- 160 (IDC, master ZFS pool): `redis-jfs.service:6380` master + `redis-sentinel-jfs.service:26380`
- 150 (IDC, JFS client): `redis-jfs.service:6380` replica (`replicaof 160 6380`) + sentinel
- 144 (LAN, JFS client, WAN to IDC): 只 sentinel,无 redis 数据

**实测数据**(2026-06-05):
- failover 时间 9.12s(`down-after-milliseconds=5000` + 投票 + 同步)
- ops list / JFS mount / rw 在 failover 后透明继续
- 旧 master 重启自动降级 replica(sentinel 通过 `REPLICAOF` 命令 + 我们预设的 `masterauth`)

**Sentinel quorum=2,3 个 sentinel 任意 1 个掉对 failover 无影响,任意 2 个掉则 quorum 不足无法 failover(但数据不丢)。**

部署脚本:`scripts/juicefs-poc/07-redis-replica.sh` / `08-sentinel.sh` / `09-switch-meta-url.sh`(每脚本顶部有用法)。ops 客户端 sentinel 支持在 `ops/infra/store/redis_store.py`(`redis-sentinel://` URL scheme)。

---

### 历史方案对比(留作参考)

最初的方案对比表:

| 方案 | 故障切换时间 | 部署复杂度 | 资源占用 | 适合规模 |
|---|---|---|---|---|
| 单 Redis + RDB | 不可用(只能恢复数据,不恢复服务) | 极简 | 1 进程 | 仅 PoC |
| **Redis Sentinel(主从 + 哨兵)** ✅ | ~10 秒自动 failover (实测 9.12s) | 中等 | 2 Redis + 3 Sentinel,几百 MB 内存 | **3-10 台节点首选** |
| Redis Cluster | 同上,分片 | 高 | ≥ 6 节点 | 数据量超百 GB 才需要 |
| TiKV | 秒级,自带 HA | 高,3 节点起 | 重 | JuiceFS 官方推荐企业级方案 |

选 Sentinel 的理由:
- metadata 体量(几十万因子)永远不需要 Cluster 分片
- TiKV 运维成本明显高
- 10s failover 可接受;ops 那层后续可以包写入重试(Phase E 一起做)

### 部署期间踩到的坑

1. **conf 缺 `masterauth`**:sentinel 写 `replicaof <new>` 时**不会**补 masterauth;原 master 起来变 replica 时 AUTH 失败,`master_link_status=down`。修法:**所有 redis 节点都预设 `masterauth = requirepass`**(commit 39602aa 把这条加进 `06-redis-jfs.sh`)。
2. **redis daemon 没 conf 写权**:`/etc/redis-jfs/redis.conf` 是 `0640 root:redis`,redis 用户只读。`CONFIG REWRITE` EACCES。所以 sentinel 触发的状态更新只在内存生效,机器重启就丢。修法:任何需要持久化的 conf 变更走 sudo sed 改文件,不靠 `CONFIG REWRITE`。
3. **144 上 redis-sentinel 是 broken symlink**:Ubuntu 上 redis-sentinel 是 redis-server 同一 binary 通过 argv[0] 切模式。如果只装 `redis-sentinel` 包没装 `redis-server`,sentinel 的 symlink 指向 `redis-check-rdb`,启动 226/NAMESPACE 失败。修法:**保证 sentinel 节点都装 `redis-server`**(然后 disable 不需要的 redis-server.service)。

### 待办

- **写入重试 wrapper**(放到 ops 那层):failover 5-10s 窗口内的写会拿到瞬时 EIO/connection refused。包 retry 3 次 backoff 2/5/10s。属 Phase E 收尾项。
- **监控**:Sentinel 状态 + master 切换告警(挂到 Feishu 或 Prometheus),目前是 manual `redis-cli -p 26380 sentinel master mymaster`。

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


## redis-jfs 6380 maxclients 根因治理 (2026-06-23 事故后, Not Started)

事故详情见 memory `project_incident_redis_maxclients`。已治标 (maxclients 10000→50000 + 持久化到 `/etc/redis-jfs/redis.conf`),根因未除。

**根因**: 160/150 是 512 核机器,juicefs (go-redis) 连接池按核数 × 倍数算,单 mount 进程持有 5000+ socket 连到共生的 6380 (JFS metadata + ops state)。默认 maxclients 10000 对这规模从一开始就低。不是泄漏,是配置/硬件规模不匹配。

**待办 (按性价比排序)**:

1. **给 juicefs mount 设连接池上限** —— 从源头压连接数,比无限调大 maxclients 治本。
   - 查 juicefs 1.3.1 mount 是否支持 `--max-conns` / metadata 连接池相关参数 (go-redis `PoolSize` 默认 `10 * runtime.GOMAXPROCS`,512 核 → 5120)。
   - 若支持: 在所有 mount 点 (160/150/144) 显式设一个合理上限 (如 256/512),重挂生效。重挂会短暂中断该机 JFS,排期做。
   - 若不支持: 只能靠 maxclients 留足余量 + 监控。

2. **评估 ops state 从 6380 拆到独立 redis** —— 消除"juicefs 池打满 → ops 跟着挂"的共生耦合。
   - ops state 量极小 (state hash + index set + checks list),单独跑个轻量 redis (甚至复用 6379) 即可。
   - 改 `config.yaml` 的 `state.redis.url` 指向新实例 + 数据迁移 (state-* key 量小,SCAN+MIGRATE 或重建)。
   - 权衡: 多一个要维护的 redis vs 故障隔离。优先级看共生事故是否再发。

3. **连接数监控告警** —— 当前打满前无告警 (跟 server-topology "监控=人工" 一致)。
   - 简单版: cron 每 5min `redis-cli INFO clients` 的 `connected_clients` 超阈值 (如 40000) 发飞书。
   - 复用 `ops/infra/notify/feishu_send.py`。


## 派生层 fs 惯性收尾 (fs→pg 迁移遗留, Partially Done)

**背景**: 派生层 (index/metrics/datasources/bcorr) + state 已迁 Postgres (Phase G, 2026-07-04),
但读写路径还带着 fs 时代 "扫全表 → 内存过滤 / 查询时懒算+缓存" 的惯性。同库 (state + derived
都在 `ops` 库, host 15432),很多 "分开读再内存合并" 现在其实能下推 SQL 或 JOIN。

本轮(commit `bbc5462`)已完成 **第 5 条**: check 归档补写 datasources+bcorr,四组入库即完整;
`--refresh-*` 独立成 `ops refresh`。之后 **第 1 条已完成** (`ops list` sort/limit/metrics 阈值下推 SQL,
见 `ops/services/list/CLAUDE.md` "SQL 下推" 段 + `ops/infra/derived/base.py:metric_get`/`sort_key`)。
剩下 3 条按性价比排序如下。

### ~~1. `ops list` 把全表拉进内存再 Python 过滤/排序/截断~~ ✅ 已完成

sort / metrics 阈值 / limit 已下推 `DerivedStore.get_all` (pg 拼 `WHERE/ORDER BY/LIMIT`,
json 内存镜像同语义)。`has_index=True` 下推 `author IS NOT NULL`;limit 有 `can_push_limit`
gate (无 status/field/tables 时才下推,否则内存 `[:n]` 兜底);`!=` 不下推 (apply_filters 未实现);
数值键真相源统一到 `base.metric_get`/`sort_key`,pg 的 `_METRIC_EXPR` 逐键镜像。等价性已硬校验
(top-n == full head,7593 行 PG + json 单测)。

### ~~2. state + derived 分两次读 + Python 按 name 合并 → 一条 JOIN (纯收益)~~ ✅ 已完成

派生层与状态层同库 (PG host 15432),`list` 的联合读收拢到
`ops/infra/query.py:query_factors`(返回 `FactorRow = (DerivedRecord, status,
last_fail_stage)`)。后端探测:两边都是 postgres 且同一 conninfo → 走
`PostgresDerivedStore.join_state`(`factor_derived d LEFT JOIN factor_state s`,
`--status` 精确下推 `s.status = %s`,author/has_index/field/tables/metrics/sort/
limit 全下推);否则(json 回退 / 跨库 PG)保留"两次读 + 内存按 name 合并"。
`--status` 进 SQL 后不再挡 `-n` 下推(gate 现只看 field/tables)。派生层谓词
`_derived_where(prefix=...)` 被 get_all 与 join_state 共用,`_metric_expr(prefix)`
同源生成无别名/`d.` 别名两版,杜绝 drift。等价性硬校验(JOIN vs 旧两读 vs
强制 json 回退,7593 行全通过)。

### ~~3. `load_metrics` / `load_bcorr` / `load_datasources` 各自 get_all 扫全库 (顺手)~~ ✅ 已完成

`health` 的两次全表扫描(`load_metrics` + `load_datasources`)合成一次
`_load_derived_maps`(单 `get_all`,同一 DerivedRecord 上取 metrics presence +
fields/tables)。`load_metrics` 仍被 check/report 复用,未删。

### 4. `LibraryScanner` 的 index_built_at 水位 + alpha_src mtime 比对 (最伤筋动骨,压后)

现状 (`ops/core/library.py`): 整套 "扫 alpha_src 目录 → walk 算 dump_days → publish index →
比 mtime 判新鲜" 是 fs-scan-and-cache 范式。db 真相源下,index 本该由 submit/check/rm 这些
**知道因子何时变化的命令增量维护**,而不是 list 时按需全盘重扫。

复杂点:
- `dump_days` 依赖 alpha_dump (本地 sidecar,**不在 JFS/PG**),必须扫盘。所以 index 无法完全脱离 fs ——
  要拆成 "src 侧字段 (author/delay/has_pnl,可事件驱动写 PG)" vs "dump_days (仍需扫本地 sidecar)"。
- 事件驱动改造要在 submit/check/rm/restage/cancel 每个写路径插 index upsert,面广。
- **建议最后做**,且先决定 dump_days 是否还要留在 index 里 (alpha_dump 退役 roadmap 一旦推进,这条自然简化)。

### 排期建议

~~1 (list 下推)~~ ✅ ~~2+3 (JOIN + health)~~ ✅ 已完成 (`ops/infra/query.py` +
`PostgresDerivedStore.join_state`)。
4 (index 事件驱动) 面广且与 alpha_dump 退役耦合 → 压后,等 alpha_dump 方向明朗再动。
