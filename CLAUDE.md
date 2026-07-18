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
| server-145 | 10.9.100.145 | 北京托管机房 (IDC) | JFS client + **NFS 客户端** (`/datasvc/data/` 实际挂 160 NFS, 老说"数据节点不在 JFS 集群" 已校正, 实际两套都在); **JFS 卷对象存储落盘机**(alphalib 数据块实际存这台, 2026-07-09 确认 —— MinIO 密钥轮换挂账的实体在此); 无 ops 部署、无人在此写因子 |
| server-170 | 10.9.100.170 | 北京托管机房 (IDC) | JFS client (`/nvme125/alphalib`, 独立 12T nvme ZFS pool, cache 100G;2026-07-11 由 `ops setup --migrate-mount` 自 `/ext4` 迁入,**与 yifei clickhouse 脱盘**); sentinel 客户端 (本机不跑 redis/sentinel);计划中的 check 消费机 |
| intel-workstation-144 | 10.6.100.144 | 本地办公网 | JFS client (`/storage/vault/`, 跨段 LAN→IDC) + sentinel:26380 (纯投票); **本地 NFS owner** 导出给 10.6.100.145/146; **冷副本**, 只有 cc_2024 / cc_2025, 不在生产同步链路 |
| local-145 | 10.6.100.145 | 本地办公网 | 本地 NFS 客户端 (挂 144) — 注意跟北京 10.9.100.145 同号但不同机 |
| local-146 | 10.6.100.146 | 本地办公网 | 本地 NFS 客户端 (挂 144) |

**网络划分**: 10.6/16 (本地办公网, 144/145/146) / 10.9/16 (北京 IDC, 160/150/145/170) / 10.12/16 (上海中信 IDC, 147)。三段互通但 144 ↔ IDC 走跨段路由, 带宽和延迟显著差于 IDC 内部。**写并发场景把生产留在 IDC**, 144 主要做研究 + 跨地域容灾验证。任何"机器间数据传输"的脚本要把 144 当 WAN 节点考虑(超时调宽、避免无理由的 chatty 协议)。

**数据同步**: rawdata 在 147 抓取, 每日增量 CSV → 北京 160, 各地独立 build_cc (**不是 cc bytes 镜像**, 是 rawdata 同步 + 各地各跑)。本地 144 是冷副本, 早期 CSV 一次性推过去自建 cc_2024/cc_2025, 不在生产同步链路内。详见 memory [[reference-server-topology]]。

**JFS / Sentinel 拓扑**(2026-06-05 上线): JuiceFS 挂载点共享 alphalib 卷,挂载点 per-host 可不同(160/150 `/tank/vault/alphalib`,144 `/storage/vault/alphalib`,170 `/nvme125/alphalib`;正主是 config.yaml hosts 块,`ops setup --check` 可验); metadata 走 `redis-sentinel://160:26380,150:26380,144:26380/mymaster/0`。Redis Sentinel 实测 failover 9.12 s。新 client 用 `scripts/juicefs-poc/join.sh` 接入(挂载点/cache per-host,sidecar 必须是 `<挂载点>.local`)。详见 `scripts/juicefs-poc/README.md` + `.claude/plans.md` Phase B-8/C。

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
uv run ops list                      # List factors (default config.yaml → JFS)
uv run ops list -u wbai              # Filter by author (-u/--user)
uv run ops list --format json        # JSON output
uv run ops info <factor-name>        # Show factor details (入库时快照 metrics + snapshot_at)
uv run ops pack                      # Aggregate alpha_dump → alpha_feature (skip already-packed)
uv run ops pack --force              # Rewrite all factors
uv run ops pack --factor AlphaXxx    # Pack one factor
# ops produce(因子日增生产)v3 重构中,见 docs/design/factor-produce-v3.md
uv run ops rm AlphaXxx               # 彻底删除因子(src/pnl/dump/feature + factor_info 级联 state+snapshot,不可逆)
uv run ops rm AlphaXxx -y            # 跳过确认
uv run ops restage AlphaXxx          # 原代码不变,召回 staging 待重跑 check
uv run ops restage AlphaXxx -s rejected   # 召回 rejected 因子
uv run ops restage AlphaXxx --purge  # ACTIVE 召回时同时清 dump + feature(pnl 保留;REJECTED 召回一律自动清 dump/feature/pnl)
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
uv run ops setup                     # 拉平本机 alphalib 部署(幂等补建缺失目录/软链/权限组)
uv run ops setup --check             # 只读体检:✔/✘/⚠ 清单 + 退出码(FAIL→1)
uv run ops setup --migrate-mount     # 声明变更收敛:JFS 挂载点迁到 hosts 声明位置(TTY + 确认)
uv run ops doctor                    # 盘 ↔ PG 数据对账,纯只读报告(零 sudo;FAIL→1)
uv run ops doctor --family pool-ghost --format json  # 族过滤 + 全量明细 JSON
uv run ops doctor --fix snapshot-stale  # 按族修复(逐族确认;可修族见 --help)
uv run ops combo run <dir> --start 20250102 --end 20251231          # combo 端到端代测 (predict+backtest)
uv run ops combo run <dir> --start 20241210 --end 20241231 --predict-start 20241201 --stats simple  # 留 warmup, 单 stats
```

Test suite in `tests/` (pytest, dev group). Covers the check pipeline's control flow
(routing outcomes, on_reject artifact policy, scan/self-heal/lock), the factor-lifecycle
write commands (submit/restage/cancel/approve/clear/rm state transitions + artifact
handling), and state PG storage. `tests/e2e/` runs the real pipeline end-to-end
(real gsim + cc data) with fake factors that deterministically blow up at each stage —
marked `slow`+`e2e`, run via `uv run pytest -m e2e` (~85s). `uv sync --group dev &&
uv run pytest -m "not slow"` for the fast suite. PG tests need an `ops_test` db
(auto-skip if unreachable;per-session schema 隔离,并行安全;本地可用
`docker-compose.test.yml` 起,CI 里 postgres service 常跑 —— I2,2026-07-11);
see `tests/README.md`. Python 3.10+ required
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
| `status` | Query factor lifecycle state | `ops/cli/status.py` + `ops/services/status/` |
| `list` | List factors in the library | `ops/cli/list.py` + `ops/services/list/` |
| `info` | Show factor details | `ops/cli/info.py` + `ops/services/info/` |
| `pack` | Aggregate per-date `alpha_dump` files into per-factor `alpha_feature` matrices | `ops/cli/pack.py` + `ops/services/pack/` |
| `produce` | 因子日增生产(v3 重构中:归档即生产态 + checkpoint 续跑薄驱动,设计 `docs/design/factor-produce-v3.md`;v1 已退场) | (v3 落地时回归) |
| `setup` | 声明式管理本机 alphalib 部署:hosts 块按 hostname 匹配挂载点,缺省幂等补建(目录/软链/权限组),`--check` 只读体检。JFS 挂载本身归 join.sh | `ops/cli/setup.py` + `ops/services/setup/` |
| `doctor` | 盘 ↔ PG 数据对账(8 族:池鬼影/stale 快照/时间线不变量/info 孤儿/src·staging 漂移/产物孤儿/本机 dump 孤儿)。缺省纯只读;`--fix <族>` 逐族确认修复(五道闸删除管道) | `ops/cli/doctor.py` + `ops/services/doctor/` |

Removed subcommands: `cp`, `scp`, `compiler`, `resubmit`(并入 `submit --overwrite`), `recheck`(改名 `restage`), `health`(2026-07-07 Wave 2 退役: --fix 写的是没人读的僵尸表;对账职能已由 `ops doctor` 落地,2026-07-12), `sync`(2026-07-07 Wave 1 退役: S3 模型已被 JFS 取代且回退配置早已不可用), `refresh`(2026-07-06 删除 —— metrics/datasources/bcorr 改为入库时不可变快照,不再支持重算;需最新表现须重跑 backtest), `backfill`(2026-07-13 legacy 清理批退役: bootstrap 使命 2026-07-06 已完成,正常流程永不再补录;留着 = src 孤儿整批复活成 ACTIVE 的风险。`HISTORY_OPS`/DB `chk_op` 保留 'backfill' 枚举值 —— 存量事件是历史事实)。

### Design Principles

**Destructive operations are opt-in.** Default behavior never deletes user data. Every destructive path lives behind an explicit flag or a separate subcommand. Established patterns:

- `ops rm` hard-deletes an in-library factor entirely: src/pnl/dump/feature + factor_info row (级联删 state + snapshot). Irreversible, no tombstone. Interactive confirm by default (`-y` skips).
- `ops cancel` hard-deletes staging dir + state record for SUBMITTED (`--force` extends to CHECKING). No tombstone — factor never went live.
- `ops clear` deletes staging orphans (no state record), left by `ops submit` parse failures.
- `ops submit --overwrite` copies new code from dropbox to staging for an already-registered factor, version += 1 (default skips existing).
- Bulk operations default to dry-run; require `--apply` (or equivalent) to execute.
- State merge prefers data preservation over precision: tied `updated_at` keeps local.

When adding a new command that touches files, state, or remotes: default to the non-destructive path, surface the destructive variant behind a flag, and require explicit user authorization at the scope being acted on.

### 注释规范(Comment Conventions)

注释写**为什么**,不写**是什么**——代码本身说 what,注释只解释非显然的 why:决策理由、
不变量、陷阱、边界、反直觉之处。语言中文,标识符/命令/术语保留英文。

- **纯 why + 指针,注释不当 changelog**:只留**长期有效**的理由。历史考古(日期 /
  批次名 / PR 号 / `full-review §X` / `S8`)**不进 inline**——git blame +
  `docs/remediation/JOURNAL.md` 已是编年史;需溯源留一句指针(`见 JOURNAL F2`),不复述
  来龙去脉。判据:三个月后这行注释还成立吗?讲"当时改了什么"的删,讲"为什么现在这样"的留。
- **module docstring**:每模块开头一段——是什么 + 边界(负责 / 不负责什么)+ 关键不变量。
  读者入口(范本 `ops/core/paths.py`、`ops/infra/repository.py`)。
- **SSOT 锚点**:一处代码是某事实族正主、或依赖别处正主,点明(`正主在 X` / `派生自 Y`)。
- **防回潮墓志铭**:删除的字段/命令留注释**仅当**有"别加回来"价值(解释为什么不要重蹈);
  纯记账的删。
- **易 stale 的具体值**:可变事实(计数 / 机器数 / 阈值)不写死在注释里,注明来源
  (`见 config`)或只写性质/量级——写死的具体数是定时炸弹(如注释说"三机"实际已四机)。
- **禁止**:复述代码、装饰性分隔线堆砌、无触发条件的 TODO(TODO 带条件 + 替代方向)。

存量注释按本规范收敛(见 `docs/remediation/JOURNAL.md` 注释清理批)。

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
├── alpha_dump/     # Daily target positions    — alpha_dump/<name>/       目录(软链 → 本地 sidecar,见下)
└── alpha_feature/  # Aggregated alpha_dump     — alpha_feature/<name>.{v}.npy  单文件
```

**软链约定**(2026-07-08 确认 dump;2026-07-11 staging 共享化后更新):
- `/mnt/storage/alphalib` 是**软链**,指向本机实际 alphalib 路径(各机挂载点不同:
  160/150 `/tank/vault/`、144 `/storage/vault/`、170 `/nvme125/`)—— 老脚本/固定
  路径文档经它仍可用,不是旧数据副本。
- `alphalib/alpha_dump` 是**软链**,实体是 `<挂载点>.local/alpha_dump`(本地盘
  sidecar,每机一份,不进 JFS 不共享)—— dump 大文件有意留本机。
- **`alphalib/staging` 自 2026-07-11 起是 JFS 实目录(共享)**——共享 staging +
  队列消费部署(docs/design/shared-staging-queue.md):任意机器 submit 入队,170
  (消费机)check,任意机器看结果;"在哪台 submit 就必须在哪台 check"的绑定
  与"PG 不记因子在哪台 staging"的挂账一并消灭。历史形态(sidecar 软链)见
  JOURNAL 2026-07-11 校正条目。
- bcorr 分流池 `pnl_automated/` / `pnl_manual/` 是挂载点下**实目录 → JFS 共享**
  (对比池须全局一致)。`pnl_alphalib` 是 `alpha_pnl` 的别名(同一目录);
  `pnl_prod_path` 在 gsim_home 下,本机。
  **⇒ 五条数据路径里唯一本机的是 alpha_dump**,其余(src/pnl/feature/staging)
  + 分流池全部共享。

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

(2026-07-07 依赖大扫除后,见 `pyproject.toml` 注释)

- **numpy** - Data processing
- **xmltodict** - XML config manipulation
- **pyyaml** - Config parsing
- **loguru** - Logging
- **rich** - Terminal output (tables / live progress)
- **psycopg** / **psycopg-pool** - Postgres client (state + info + snapshot 真相源, `ops/infra/store/pg_store.py` / `ops/infra/info/pg_store.py` / `ops/infra/snapshot/pg_store.py`)

## 词汇表(schema v3 正名,2026-07-13;"因子库"专指成员集合)

| 术语 | 定义 | 实现锚点 |
|---|---|---|
| **在册** | 有档案记录、目录可见(含被拒) | `status != 'submitted'` = `ops list` 因子集 |
| **已归档** | 盘面产物落 alphalib | ACTIVE 与晚期 REJECTED 都发生 |
| **入库**(动作) | 从不在库变为在库的那一刻(入库时刻 = entered_at) | `transition(ACTIVE)`;history `entered` 事件 |
| **在库**(状态) | 因子库成员 = ACTIVE(combo 消费、bcorr 池范围) | `status='active'` |
| **已入库**(完成时) | 至少入库过一次,离库不清除 | `entered_at` 非空(cancel 守卫语义) |
| **测得快照** | 最近一次 check 测得的表现(被拒也写) | `factor_snapshot`,snapshot_at = 测得时刻 |

不变量:`created_at <= submitted_at`(首提逐字符相等;backfill 存量 submitted_at=NULL 除外)。

## SSOT 表(事实族 → 正主)

改代码/review 第一问:**"你在问正主吗?"** 每个事实族只有一个权威来源,其余都是
投影或缓存(full-review §8.2)。2026-07-11 起表中已无已知多真相源(原 ⚠ 行
metric 表达式已收敛,S8);新发现的多真相源加 ⚠ 行并在
`docs/design/factor-aggregate-plan.md` 记收敛计划。

| 事实族 | 正主(SSOT) | 备注 |
|---|---|---|
| 因子集(什么算在库) | PG `factor_state.status != 'submitted'` | 2026-07-07 起纯 PG,零扫盘 |
| 身份(author / discovery_method) | PG `factor_info` | 目录名推断只是 guess,不权威 |
| 测得表现(metrics/datasources/bcorr/delay) | PG `factor_snapshot` | v3 测得快照:最近一次 check 测得,被拒也写;snapshot_at = 测得时刻;每行不可变、新测量替换,无离线重算 |
| stage 身份 / 顺序 / 路由策略 | `services/check/stages.py` 的 `PIPELINE` | 新增 stage = 加一行 |
| 时间戳格式 | `ops/utils/clock.py::now_iso` | |
| 状态值 | `FactorStatus` 枚举 | 与 DB CHECK 约束同一提交改 |
| 操作事件(谁在何时对因子做了什么) | PG `factor_history`(op 枚举 = `core/state.py::HISTORY_OPS`,与 chk_op 同一提交改) | "最近失败"是其派生(`Factor.last_fail`);唯一活过 rm 的痕迹 |
| 依赖分层规则 | pyproject `[tool.importlinter]`(8/8 enforcing;cli 接缝豁免点 `ops/cli/common.py`) | 2026-07-09 进 CI;ratchet 已退役(阶段 3);C9 结果渲染归 cli(2026-07-11 展示层上收) |
| 盘面布局(src/pnl/dump/feature/staging/池副本/meta.json) | `ops/core/paths.py::FactorPaths` | 2026-07-09 收编(40+ 处拼接清零) |
| 因子领域类型 | `ops/core/factor.py::Factor`(identity/state/snapshot 三切面) | 2026-07-09 阶段 2;全库唯一叫"因子"的类型 |
| 因子记录读写 + 产物清理 | `ops/infra/repository.py::FactorRepository` | find 单条三表 JOIN;register 原子双表写;purge_artifacts 按 ArtifactScope 两面 |
| 三表 DDL(代码侧引导) | `ops/infra/schema.py::ensure_schemas`(FK 依赖序) | 生产 schema 正主是 scripts/postgres;store 构造零副作用 |
| 写命令集(sudo 提权名单) | `ops/cli/common.py::mark_write` 注册声明(args.is_write_command) | 2026-07-10 S16 完成;WRITE_COMMANDS 手抄删除,`maybe_elevate` 只消费声明 |
| metric 键集 + 取值语义(bcorr=abs) | `ops/core/metrics.py::SNAPSHOT_METRICS` 注册表 | 2026-07-11 S8 收敛:SQL 下推表达式 / list 内存取值(`metric_value`)/ CLI `--sort-by` choices 三方全部派生;新增可排序 metric = 注册表加一行 |

## Known Technical Debt (Deferred)

- ~~Stub files~~ **已清理**(2026-07-11 小件收官批:`results/checkpoint.py` 删除、Status/Results 空壳及三份子类删除;`results/base.py` 仅剩 `Result` 标记基类 + CompResult/CorrResult 两个真实结果)
- ~~`core/alpha/metadata.py` has I/O~~ **已迁出**(2026-07-11:alpha_dump 扫描两函数(v2npy_files/last_v2npy_file)迁 `ops/services/check/checker/dumpscan.py`,死代码 `get_last_v1npy_file`/`_get_v1md5` 删除;AlphaMetadata 构造仍读盘解析 XML,属工作台语义)
- ~~`ops sync` legacy fallback~~ **已退役删除**(2026-07-07 Wave 1,连同 `infra/s3.py`、boto3/tqdm、`config.prod-legacy.yaml`)
- **Postgres 三表结构 (2026-07-06, branch `feat/derived-postgres`)**: 因子数据落三张 PG 表(server-160 docker, host 15432),全部去掉 `library_id`(永远单库),`id SERIAL` 主键 + `name UNIQUE`:
  - `factor_info` — 身份信息 (author / discovery_method / created_at)。抽象层 `ops/infra/info/`。`discovery_method` 自 2026-07-13(legacy 清理批)起 **NOT NULL + CHECK IN ('automated','manual')** —— 'backfill' 值退役、NULL 归一,submit/check 两条写路径都硬校验缺失即拒。
  - `factor_state` — 生命周期状态 (status/version/时间戳)。去掉了 author 和 submitted_by(移到 factor_info);v2b(2026-07-12)再去 rejected_at/last_fail_*/check_history —— 迁 `factor_history`。抽象层 `ops/infra/store/`。
  - `factor_history` — **全操作审计事件表**(v2b):op ∈ submit/overwrite/check/approve/restage/cancel/rm/backfill/entered,一次操作一条记录,actor 可追溯,**无 FK 活过 ops rm**。发射走 FactorRepository/StateStore 同事务,漏记结构上不可能。
  - `factor_snapshot` — 入库时快照 (metrics + datasources + delay + bcorr + snapshot_at)。抽象层 `ops/infra/snapshot/`。(原 index 组的 has_pnl/dump_days 已删列 —— 可变物理事实与快照不可变冲突,需实时状态走 LibraryScanner 扫盘;delay 保留,入库时定死。)
  外键: `factor_state.name` / `factor_snapshot.name` 均 `REFERENCES factor_info(name) ON DELETE CASCADE`(删 info 级联删 state + snapshot)。联合读入口 `ops/infra/repository.py::FactorRepository.find`(单条三表 LEFT JOIN;2026-07-09 阶段 2 退役 query_factors/FactorRow,service 层经 Repository 读写,聚合类型见 `ops/core/factor.py`)。
  - **语义变更**: metrics/datasources/bcorr 从"可 `ops refresh` 重算的最新表现"变为"入库时不可变快照"(`snapshot_at = factor_state.entered_at`);`ops refresh` 命令已删除,需最新表现须重跑 backtest。
  - ~~过渡状态~~ **derived 僵尸层已删除**(2026-07-07 Wave 2, JOURNAL V2):`ops/infra/derived/` 整层 + LibraryScanner 索引缓存退役;生产库僵尸表清理用 `scripts/postgres/migrate_drop_derived.sql`(手动)。**list 因子集判据 = `factor_state.status != 'submitted'`(纯 PG,零扫盘)**;info 存在性判据 = factor_info。部署见 `scripts/postgres/README.md`。

## Plans & Roadmap

完整路线图见 `.claude/plans.md`。

**已完成的大事件**:
- 2026-06-04 ops state 进 Redis (`config.juicefs.yaml` 切 Redis backend;2026-07-07 redis state 后端整体退役)
- 2026-06-05 Redis Sentinel HA (3-node sentinel, 9.12s failover)
- 2026-06-05 JFS 上线 + 默认 config 切 JFS (`config.yaml` = JFS, `config.prod-legacy.yaml` 回退)
- 2026-07-04 Phase G: 派生层 (index/metrics/datasources/bcorr) 迁 Postgres (server-160 docker, host 15432), per-machine JSON 缓存退役; 读写数据流重构 (DerivedRecord 取代 FactorInfo god-object); **state (因子生命周期) 也迁 Postgres, PG 成唯一真相源** (state + derived 同库)。branch `feat/derived-postgres`, 部署 `scripts/postgres/`。**注意: 承载旧 state 的 Redis 同时是 JFS metadata 后端, 不可停 (停进程=挂因子库); ops 只是不再用它存 state。**
- 2026-07-04 `factor_lock` 迁跨机 PG advisory lock (branch 同上): 原 per-machine fcntl 挡不住跨机对同一因子的并发变更 (state 共享 PG + src/pnl 产物共享 JFS;staging 2026-07-11 起也共享 —— 多机扫同一 staging、多 worker 并发领任务,跨机锁正是防重复消费的机制)。postgres 后端走 `pg_try_advisory_lock` (专用连接, session 级, 连接断开自动释放, 无死锁残留); json dev/test 后端 fcntl(2026-07-07 起 postgres 缺 conninfo 硬错误、锁键去 library_id 维,见 JOURNAL F4/F5)。签名 `factor_lock(name, config)`。见 `ops/infra/lock.py` + memory [[project_factor_lock_cross_machine]]。
- 2026-07-04 CLI 子命令重审 (branch 同上): submit 吸收 resubmit (`--overwrite` 覆盖已入库因子, 默认跳过); recheck 改名 restage (名副其实, 只召回 staging 不跑 check); rm 改彻底硬删 + 移除 DELETED 状态/deleted_at 列 (因子要么存在要么删除, 删除不是状态); approve 正名为"数据覆盖多样性人工豁免"。见 memory [[project_cli_command_redesign]]。
- 2026-07-06 Postgres 双表 → 三表重构 (branch 同上): `factor_derived` + 旧 `factor_state` (含 author/submitted_by) 拆成 `factor_info` (身份) + `factor_state` (纯状态) + `factor_snapshot` (入库时快照)，全部去掉 `library_id`。**metrics/datasources/bcorr 语义从"可刷新最新表现"变为"入库时不可变快照"** (`snapshot_at = entered_at`)；`ops refresh` 命令删除。新增 `ops/infra/info/` + `ops/infra/snapshot/` store 抽象 + `ops/infra/query.py` 联合读。生产库 `ops` 迁移已执行 (migrate_to_snapshot.sql + backfill_discovery_method.py): factor_info 7594 / factor_state 7594 / factor_snapshot 7485; discovery_method automated 7259 / manual 226 / NULL 109 (未入库); 迁移中清理 108 脏因子 + 2 空壳 + 补 20 hwang 孤儿 state。旧 `derived/` 层代码当时保留 (LibraryScanner 仍用其做 index 缓存)(注: derived 层已于 2026-07-07 Wave 2 删除)。
- 2026-07-13 schema v2/v3 + legacy 清理批收官 (PR #14-#20, 全部合 main 四机滚存): v2a/v2b/v2c 三批 (factor_history 全操作审计表、state 瘦身、TEXT[]、约束/命名归一) + v3 词汇正名 + 测得快照 (factor_snapshot = 最近一次 check 测得表现, 被拒也写) + legacy 清理批 (472 snapshot_at 漂移拉正 + compliance 22 快照补跑 + discovery_method 129 归一并 **NOT NULL 收口** + `ops backfill` 退役 + doctor 加第八族 timeline-drift + `ops list` 混排加 status 列)。生产现状 8419 因子。见 `docs/remediation/JOURNAL.md` + `docs/design/schema-v3.md` + `docs/design/legacy-cleanup.md`。
- 2026-07-16 compliance 判定重做收官 (branch `claude/compliance-survey`): 先测量后定策 —— 全库 7972 因子逐日摸底 (`scripts/compliance_survey.py`/`compliance_profile.py`, 存档 `report/compliance-survey/`) 定出新规则: **全史每日 + 跳过无效日 (空/全NaN/零敞口) + 违规容忍 `violation_tolerance=10` + 严重违规立拒 (单日个股 > `max_position_pct × hard_position_mult` = 2× = 10%, 含 inf)**, 取代旧"尾窗 762 + 任一天违规即拒" (`check_window` 键退役); 影子对比 active 零状态变化, 22 条 compliance-rejected 中 12 条毛刺转放行 (不主动 restage)。compliance 测量不进 PG (单因子层是卫生闸, 真约束在 combo 层); fail_reason 统一风格契约 (`违反项 | 上下文`, 见 `checker/base.py::CheckFail`)。见 `docs/design/compliance-survey.md` + `.claude/plans.md` "Compliance 判定重做"节。

**仍在路上**:
- Phase D: alpha_src 接入 Git on JFS,改造 `ops submit/restage` 走 `git add/commit`(串行化复用现有 `factor_lock`,已是跨机 PG advisory lock)
- Phase E: `.state` merge 逻辑简化(其实在 Redis 后大部分逻辑已不需要)
- Phase F: checkpoint 落地(按设计原则放 JFS / 本地 SSD)
- Phase G 剩余: ~~反查命令 `ops query --field/--table`~~ (已改造 `ops list --filter-by field=/tables=` 下推 SQL 吃 GIN, 未新增命令) / ~~refresh_* 从 list 独立成 ops refresh~~ (已废弃: 三表重构后 metrics/datasources/bcorr 改为入库时快照, `ops refresh` 命令删除, 不再有重算路径) / PG 密码正规化 (挪 /etc root-only;~~分发 150/144~~ 2026-07-08 已随升级窗口 scp 完成) / ~~150/144 部署~~ (2026-07-08 完成: 三机 rev 一致 + 跨机锁四观测 + migrate_drop_derived 已执行, JOURNAL U1) / 分支合 main / 验稳后清 Redis 残留 state key (只 DEL state:*, 绝不 FLUSHDB — Redis 还扛 JFS) / ~~清理僵尸 derived 层~~ (2026-07-07 Wave 2 已删) / ~~`ops health` 删除~~ (Wave 2 已删) / ~~list 扫盘界定因子集~~ (Wave 2 已改纯 PG 判据, scan 退出热路径)
- Phase C 上线后剩余: 写入重试 wrapper / ~~sync deprecation warning~~(sync 已删) / sudo NOPASSWD wrapper / **MinIO key rotation(紧急:密钥曾入库,虽已删文件但在 git 历史)** / alpha_dump 退役
