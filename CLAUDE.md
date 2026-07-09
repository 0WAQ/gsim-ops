# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ops** is a Python CLI for alpha factor validation, backtesting, and lifecycle management. It orchestrates a 7-stage validation pipeline for quantitative trading factors before they enter the production factor library.

## Hosts / Network Topology

| Host | IP | 位置 | 角色 |
|---|---|---|---|
| **147** | 10.12.174.152 | 上海中信托管机房 | rawdata 抓取出口 (wind/datayes/citics 内网) + cc first build + **实盘 combo 机器**; 内网隔离, ops 代码不在这台 |
| server-160 | 10.9.100.160 | 北京托管机房 (IDC) | JFS master, ZFS pool `/tank/vault/`, redis-jfs:6380 master + sentinel:26380; **NFS owner** (`/datasvc/data/`, 导出给 150/145); **yifei L2 feature 生产节点** (每日 20:00 后) |
| server-150 | 10.9.100.150 | 北京托管机房 (IDC) | JFS client + **NFS 客户端** (透明读 160 的 `/datasvc/data/`) + redis-jfs:6380 replica + sentinel:26380 |
| server-145 | 10.9.100.145 | 北京托管机房 (IDC) | JFS client + **NFS 客户端** (`/datasvc/data/` 实际挂 160 NFS, 老说"数据节点不在 JFS 集群" 已校正, 实际两套都在) |
| server-170 | 10.9.100.170 | 北京托管机房 (IDC) | JFS client (`/ext4/alphalib`, cache 100G); sentinel 客户端 (本机不跑 redis/sentinel); 与 yifei clickhouse 同盘 `/ext4` (2026-06-24 接入) |
| intel-workstation-144 | 10.6.100.144 | 本地办公网 | JFS client (`/storage/vault/`, 跨段 LAN→IDC) + sentinel:26380 (纯投票); **本地 NFS owner** 导出给 10.6.100.145/146; **冷副本**, 只有 cc_2024 / cc_2025, 不在生产同步链路 |
| local-145 | 10.6.100.145 | 本地办公网 | 本地 NFS 客户端 (挂 144) — 注意跟北京 10.9.100.145 同号但不同机 |
| local-146 | 10.6.100.146 | 本地办公网 | 本地 NFS 客户端 (挂 144) |

**网络划分**: 10.6/16 (本地办公网, 144/145/146) / 10.9/16 (北京 IDC, 160/150/145/170) / 10.12/16 (上海中信 IDC, 147)。三段互通但 144 ↔ IDC 走跨段路由, 带宽和延迟显著差于 IDC 内部。**写并发场景把生产留在 IDC**, 144 主要做研究 + 跨地域容灾验证。任何"机器间数据传输"的脚本要把 144 当 WAN 节点考虑(超时调宽、避免无理由的 chatty 协议)。

**数据同步**: rawdata 在 147 抓取, 每日增量 CSV → 北京 160, 各地独立 build_cc (**不是 cc bytes 镜像**, 是 rawdata 同步 + 各地各跑)。本地 144 是冷副本, 早期 CSV 一次性推过去自建 cc_2024/cc_2025, 不在生产同步链路内。详见 memory [[reference-server-topology]]。

**JFS / Sentinel 拓扑**(2026-06-05 上线): JuiceFS 挂载点共享 alphalib 卷,挂载点 per-host 可不同(160/150 `/tank/vault/alphalib`,144 `/storage/vault/alphalib`,170 `/ext4/alphalib`); metadata 走 `redis-sentinel://160:26380,150:26380,144:26380/mymaster/0`。Redis Sentinel 实测 failover 9.12 s。新 client 用 `scripts/juicefs-poc/join.sh` 接入(挂载点/cache per-host,sidecar 必须是 `<挂载点>.local`)。详见 `scripts/juicefs-poc/README.md` + `.claude/plans.md` Phase B-8/C。

**JFS vs NFS 分工**: JFS 只服务 alphalib (因子库多机多写场景, 2026-06 新增); cc / dm / L2 feature 走老 NFS (单 owner 多读, 各地 owner 各管各的, 早期方案保留)。两套存储分场景共存。

## Commands

```bash
uv sync                              # Install dependencies (uses uv, not pip)
uv run ops --help                    # CLI help
uv run ops submit -u wbai -s 20260401            # Submit a day's factors from dropbox
uv run ops submit -u wbai -s 20260401 -f Alpha   # Submit one factor
uv run ops submit -u wbai -s 20260401 --overwrite  # 已入库同名因子改提新代码(version += 1;默认跳过)
uv run ops check                                 # Run 7-stage pipeline on staging
uv run ops status AlphaXxx                       # Query factor lifecycle state
uv run ops status -u wbai --status submitted     # Filter by author/state
uv run ops backfill --dry-run                    # Preview backfill on alpha_src/
uv run ops backfill                              # Generate meta.json + ACTIVE for legacy factors
uv run ops list                      # List factors (default config.yaml → JFS)
uv run ops list -u wbai              # Filter by author (-u/--user)
uv run ops list --format json        # JSON output
uv run ops info <factor-name>        # Show factor details (入库时快照 metrics + snapshot_at)
uv run ops pack                      # Aggregate alpha_dump → alpha_feature (skip already-packed)
uv run ops pack --force              # Rewrite all factors
uv run ops pack --factor AlphaXxx    # Pack one factor
uv run ops rm AlphaXxx               # 彻底删除因子(src/pnl/dump/feature + factor_info 级联 state+snapshot,不可逆)
uv run ops rm AlphaXxx -y            # 跳过确认
uv run ops restage AlphaXxx          # 原代码不变,召回 staging 待重跑 check
uv run ops restage AlphaXxx -s rejected   # 召回 rejected 因子
uv run ops restage AlphaXxx --purge  # 同时清除 dump + feature(保留 src/pnl)
uv run ops restage -u wbai           # 批量:wbai 所有 active 因子(apt 风格确认)
uv run ops restage -u wbai -y        # 批量,跳过确认
uv run ops approve AlphaXxx          # 多样性豁免:放行 correlation-rejected 因子 (REJECTED → ACTIVE)
uv run ops approve -u wbai           # 批量:wbai 所有 correlation-rejected 因子
uv run ops approve -u wbai -y        # 批量,跳过确认
uv run ops cancel AlphaXxx           # 撤回未入库的 submitted 因子(删 staging + 硬删 state)
uv run ops cancel AlphaXxx --force   # 同时允许 CHECKING(清崩溃 / 中断的 check 残留)
uv run ops cancel -u wbai            # 批量:wbai 所有 submitted 因子
uv run ops cancel -u wbai -y         # 批量,跳过确认
uv run ops clear AlphaXxx            # 清 staging 孤儿(state 无 record 的目录)
uv run ops clear                     # 扫全部孤儿
uv run ops clear -u lhw -y           # 按 author 推断过滤,跳过确认
uv run ops combo run <dir> --start 20250102 --end 20251231          # combo 端到端代测 (predict+backtest)
uv run ops combo run <dir> --start 20241210 --end 20241231 --predict-start 20241201 --stats simple  # 留 warmup, 单 stats
```

Test suite in `tests/` (pytest, dev group). Covers the check pipeline's control flow
(routing outcomes, on_reject artifact policy, scan/self-heal/lock), the factor-lifecycle
write commands (submit/restage/cancel/approve/clear/rm state transitions + artifact
handling), and state/derived PG storage. `tests/e2e/` runs the real pipeline end-to-end
(real gsim + cc data) with fake factors that deterministically blow up at each stage —
marked `slow`+`e2e`, run via `uv run pytest -m e2e` (~85s). `uv sync --group dev &&
uv run pytest -m "not slow"` for the fast suite. PG tests need an isolated `ops_test` db
(auto-skip if unreachable); see `tests/README.md`. Python 3.10+ required
(see `.python-version`). Package manager is **uv** (not pip).

```bash
uv sync          # Install dependencies
uv add <pkg>     # Add new dependency
uv run <cmd>     # Run command in venv
```

## Architecture

Entry point: `ops/main.py` (argparse dispatcher). CLI registration in `ops/cli/*.py`, business logic in `ops/services/*/`.

Project is organized in 4 layers: `cli/` (argparse + output) → `services/` (orchestration) → `core/` (data models) + `infra/` (I/O, external systems). `utils/` for shared utilities.

| Subcommand | Purpose | Module |
|------------|---------|--------|
| `submit` | Copy new factors from dropbox to staging, generate `meta.json`, mark SUBMITTED. `--overwrite`: 已入库同名因子改提新代码,version += 1(默认跳过) | `ops/services/submit/` |
| `restage` | Recall ACTIVE/REJECTED factor back to staging for re-check (code unchanged; doesn't run check itself) | `ops/services/restage/` |
| `approve` | 多样性豁免:放行 correlation-rejected 因子(为数据覆盖,非质量),REJECTED → ACTIVE(不重跑 check) | `ops/services/approve/` |
| `cancel` | 撤回未入库的 SUBMITTED 因子(删 staging + 硬删 state record) | `ops/services/cancel/` |
| `clear` | 清理 staging 孤儿(state 无 record 的目录,进程非正常终止的 crash residue) | `ops/services/clear/` |
| `combo` | QR combo 端到端代测(predict+backtest, 占位符注入, 无状态) | `ops/services/combo/` |
| `check` | 7-stage validation pipeline (runs in-place on staging) | `ops/services/check/` |
| `run` | Run backtest on factors in library | `ops/services/run/` |
| `status` | Query factor lifecycle state | `ops/services/status/` |
| `backfill` | One-shot: generate `meta.json` + ACTIVE for existing factors in `alpha_src/` | `ops/services/backfill/` |
| `list` | List factors in the library | `ops/cli/list.py` + `ops/services/list/` |
| `info` | Show factor details | `ops/cli/info.py` + `ops/services/info/` |
| `pack` | Aggregate per-date `alpha_dump` files into per-factor `alpha_feature` matrices | `ops/cli/pack.py` + `ops/services/pack/` |

Removed subcommands: `cp`, `scp`, `compiler`, `resubmit`(并入 `submit --overwrite`), `recheck`(改名 `restage`), `health`(2026-07-07 Wave 2 退役: --fix 写的是没人读的僵尸表;对账职能归未来 ops doctor), `sync`(2026-07-07 Wave 1 退役: S3 模型已被 JFS 取代且回退配置早已不可用), `refresh`(2026-07-06 删除 —— metrics/datasources/bcorr 改为入库时不可变快照,不再支持重算;需最新表现须重跑 backtest)。

### Design Principles

**Destructive operations are opt-in.** Default behavior never deletes user data. Every destructive path lives behind an explicit flag or a separate subcommand. Established patterns:

- `ops rm` hard-deletes an in-library factor entirely: src/pnl/dump/feature + factor_info row (级联删 state + snapshot). Irreversible, no tombstone. Interactive confirm by default (`-y` skips).
- `ops cancel` hard-deletes staging dir + state record for SUBMITTED (`--force` extends to CHECKING). No tombstone — factor never went live.
- `ops clear` deletes staging orphans (no state record), left by `ops submit` parse failures.
- `ops submit --overwrite` copies new code from dropbox to staging for an already-registered factor, version += 1 (default skips existing).
- Bulk operations default to dry-run; require `--apply` (or equivalent) to execute.
- State merge prefers data preservation over precision: tied `updated_at` keeps local.

When adding a new command that touches files, state, or remotes: default to the non-destructive path, surface the destructive variant behind a flag, and require explicit user authorization at the scope being acted on.

### Default Config (2026-06-05 上线后)

`config.yaml` (project root) 是当前 default,指向 JFS (`/tank/vault/alphalib/`) +
Postgres state 后端。`ops xxx` 不带 `-c` 自动走它。

**没有"紧急回退"配置**(2026-07-07 Wave 1):`config.prod-legacy.yaml` 与 json/redis
回退路径经审计确认早已不可用(假保险),连同 sync 栈一并删除;见
`docs/remediation/JOURNAL.md` F1-F3。state 的 `backend: json` 是单机 dev/test 后端,
不是生产回退。

## Key Concepts

### Gsim Backtest Framework

Located at `/usr/local/gsim/`. The core backtesting engine that ops interacts with.

```bash
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml          # backtest
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /pnl   # PNL summary
/usr/local/gsim/dataops/bcorr pnl1 pnl2                                    # correlation
```

### User Factor Workspace

```
/mnt/storage/dropbox/{unix_id}/{yyyymmdd}/Alpha{UnixId}{FactorName}/
```

### Factor Library Structure

**生产数据共享在 JuiceFS 挂载点上**(`/tank/vault/alphalib/`,144 上是 `/storage/vault/alphalib/`):

```
/tank/vault/alphalib/
├── alpha_src/      # Factor source code        — alpha_src/<name>/        目录
├── alpha_pnl/      # Backtest results (PNL)    — alpha_pnl/<name>         单文件 ⚠
├── alpha_dump/     # Daily target positions    — alpha_dump/<name>/       目录(本地 sidecar, 不进 JFS)
└── alpha_feature/  # Aggregated alpha_dump     — alpha_feature/<name>.{v}.npy  单文件
```

`/mnt/storage/alphalib/` 是旧 prod 数据,保留作紧急回退,稳定 1 周后清理。

**⚠ alpha_pnl/<name> 是单文件,不是目录**。删除用 `Path.unlink()`,不要用 `shutil.rmtree()`(`Errno 20: Not a directory`)。alpha_feature 同理是单文件。只有 alpha_src / alpha_dump 是目录。

**权限模型(集中运维)**: 共享路径 owner 一律 root,group `alpha-core`(alpha_src)/`alpha-data`(其它)只读且仅作跨机 label。**所有写都走 root**;ops 通过 `ops/infra/sudo.py` self-elevate 自动 sudo 提权。详见 `scripts/juicefs-poc/README.md`。

### Factor Directory Structure

Each factor in `alpha_src/` contains:
```
AlphaXxx/
├── AlphaXxx.py           # Factor code (inherits gsim.AlphaBase)
├── Config.Xxx.xml        # Gsim config file
└── Readme.Xxx.txt        # Backtest report
```

**Data Source Tracking**: DO NOT trust XML `<Data>` declarations — parse Python code for actual `dr.getData('xxx')` calls.

## Key Dependencies

- **paramiko** / **scp** - SSH connections and file transfer
- **pandas** / **numpy** - Data processing
- **lxml** / **xmltodict** - XML config manipulation
- **colorama** - Terminal colors
- **tqdm** - Progress bars
- **pyyaml** - Config parsing
- **psycopg** - Postgres client (state + info + snapshot 真相源, `ops/infra/store/pg_store.py` / `ops/infra/info/pg_store.py` / `ops/infra/snapshot/pg_store.py`)

## Known Technical Debt (Deferred)

- **Stub files**: `core/alpha/results/base.py`, `results/checkpoint.py`, `results/checkbias.py`
- **Dead code**: `infra/notify/email.py` is commented out
- **Debug residual**: `utils/func.py` has a `debug()` with infinite loop
- **Feishu credentials hardcoded**: `infra/notify/feishu_send.py` — move to config/env later
- **`core/alpha/metadata.py` has I/O**: `_modify_always()`, `save()`, `get_v2npy_files()` — extract to services/infra
- ~~`ops sync` legacy fallback~~ **已退役删除**(2026-07-07 Wave 1,连同 `infra/s3.py`、boto3/tqdm、`config.prod-legacy.yaml`)
- **Postgres 三表结构 (2026-07-06, branch `feat/derived-postgres`)**: 因子数据落三张 PG 表(server-160 docker, host 15432),全部去掉 `library_id`(永远单库),`id SERIAL` 主键 + `name UNIQUE`:
  - `factor_info` — 身份信息 (author / discovery_method / created_at)。抽象层 `ops/infra/info/`。
  - `factor_state` — 生命周期状态 (status/version/时间戳/last_fail_*/check_history)。去掉了 author 和 submitted_by(移到 factor_info)。抽象层 `ops/infra/store/`。
  - `factor_snapshot` — 入库时快照 (metrics + datasources + delay + bcorr + snapshot_at)。抽象层 `ops/infra/snapshot/`。(原 index 组的 has_pnl/dump_days 已删列 —— 可变物理事实与快照不可变冲突,需实时状态走 LibraryScanner 扫盘;delay 保留,入库时定死。)
  外键: `factor_state.name` / `factor_snapshot.name` 均 `REFERENCES factor_info(name) ON DELETE CASCADE`(删 info 级联删 state + snapshot)。联合读入口 `ops/infra/query.py:query_factors`(当前三次查 + 内存按 name JOIN,TODO 优化为单条 SQL LEFT JOIN)。
  - **语义变更**: metrics/datasources/bcorr 从"可 `ops refresh` 重算的最新表现"变为"入库时不可变快照"(`snapshot_at = factor_state.entered_at`);`ops refresh` 命令已删除,需最新表现须重跑 backtest。
  - ~~过渡状态~~ **derived 僵尸层已删除**(2026-07-07 Wave 2, JOURNAL V2):`ops/infra/derived/` 整层 + LibraryScanner 索引缓存退役;生产库僵尸表清理用 `scripts/postgres/migrate_drop_derived.sql`(手动)。**list 因子集判据 = `factor_state.status != 'submitted'`(纯 PG,零扫盘)**;info 存在性判据 = factor_info。部署见 `scripts/postgres/README.md`。

## Plans & Roadmap

完整路线图见 `.claude/plans.md`。

**已完成的大事件**:
- 2026-06-04 ops state 进 Redis (`config.juicefs.yaml` 切 Redis backend;2026-07-07 redis state 后端整体退役)
- 2026-06-05 Redis Sentinel HA (3-node sentinel, 9.12s failover)
- 2026-06-05 JFS 上线 + 默认 config 切 JFS (`config.yaml` = JFS, `config.prod-legacy.yaml` 回退)
- 2026-07-04 Phase G: 派生层 (index/metrics/datasources/bcorr) 迁 Postgres (server-160 docker, host 15432), per-machine JSON 缓存退役; 读写数据流重构 (DerivedRecord 取代 FactorInfo god-object); **state (因子生命周期) 也迁 Postgres, PG 成唯一真相源** (state + derived 同库)。branch `feat/derived-postgres`, 部署 `scripts/postgres/`。**注意: 承载旧 state 的 Redis 同时是 JFS metadata 后端, 不可停 (停进程=挂因子库); ops 只是不再用它存 state。**
- 2026-07-04 `factor_lock` 迁跨机 PG advisory lock (branch 同上): 原 per-machine fcntl 挡不住三机并发 check 同一因子 (state 共享 PG + staging 共享 JFS)。postgres 后端走 `pg_try_advisory_lock` (专用连接, session 级, 连接断开自动释放, 无死锁残留); json dev/test 后端 fcntl(2026-07-07 起 postgres 缺 conninfo 硬错误、锁键去 library_id 维,见 JOURNAL F4/F5)。签名 `factor_lock(name, config)`。见 `ops/infra/lock.py` + memory [[project_factor_lock_cross_machine]]。
- 2026-07-04 CLI 子命令重审 (branch 同上): submit 吸收 resubmit (`--overwrite` 覆盖已入库因子, 默认跳过); recheck 改名 restage (名副其实, 只召回 staging 不跑 check); rm 改彻底硬删 + 移除 DELETED 状态/deleted_at 列 (因子要么存在要么删除, 删除不是状态); approve 正名为"数据覆盖多样性人工豁免"。见 memory [[project_cli_command_redesign]]。
- 2026-07-06 Postgres 双表 → 三表重构 (branch 同上): `factor_derived` + 旧 `factor_state` (含 author/submitted_by) 拆成 `factor_info` (身份) + `factor_state` (纯状态) + `factor_snapshot` (入库时快照)，全部去掉 `library_id`。**metrics/datasources/bcorr 语义从"可刷新最新表现"变为"入库时不可变快照"** (`snapshot_at = entered_at`)；`ops refresh` 命令删除。新增 `ops/infra/info/` + `ops/infra/snapshot/` store 抽象 + `ops/infra/query.py` 联合读。生产库 `ops` 迁移已执行 (migrate_to_snapshot.sql + backfill_discovery_method.py): factor_info 7594 / factor_state 7594 / factor_snapshot 7485; discovery_method automated 7259 / manual 226 / NULL 109 (未入库); 迁移中清理 108 脏因子 + 2 空壳 + 补 20 hwang 孤儿 state。旧 `derived/` 层代码保留 (LibraryScanner 仍用其做 index 缓存)。

**仍在路上**:
- Phase D: alpha_src 接入 Git on JFS,改造 `ops submit/restage` 走 `git add/commit`(串行化复用现有 `factor_lock`,已是跨机 PG advisory lock)
- Phase E: `.state` merge 逻辑简化(其实在 Redis 后大部分逻辑已不需要)
- Phase F: checkpoint 落地(按设计原则放 JFS / 本地 SSD)
- Phase G 剩余: ~~反查命令 `ops query --field/--table`~~ (已改造 `ops list --filter-by field=/tables=` 下推 SQL 吃 GIN, 未新增命令) / ~~refresh_* 从 list 独立成 ops refresh~~ (已废弃: 三表重构后 metrics/datasources/bcorr 改为入库时快照, `ops refresh` 命令删除, 不再有重算路径) / PG 密码正规化 (挪 /etc root-only;~~分发 150/144~~ 2026-07-08 已随升级窗口 scp 完成) / ~~150/144 部署~~ (2026-07-08 完成: 三机 rev 一致 + 跨机锁四观测 + migrate_drop_derived 已执行, JOURNAL U1) / 分支合 main / 验稳后清 Redis 残留 state key (只 DEL state:*, 绝不 FLUSHDB — Redis 还扛 JFS) / ~~清理僵尸 derived 层~~ (2026-07-07 Wave 2 已删) / ~~`ops health` 删除~~ (Wave 2 已删) / ~~list 扫盘界定因子集~~ (Wave 2 已改纯 PG 判据, scan 退出热路径)
- Phase C 上线后剩余: 写入重试 wrapper / ~~sync deprecation warning~~(sync 已删) / sudo NOPASSWD wrapper / **MinIO key rotation(紧急:密钥曾入库,虽已删文件但在 git 历史)** / alpha_dump 退役
