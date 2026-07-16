# Plans

Deferred / not-yet-started plans. See `CLAUDE.md` for current architecture.

## 迁移完成定义(全局纪律,full-review §8.3)

> 一次迁移包含 Expand、Migrate、**Contract** 三步;Contract = 旧代码删除 + 旧配置键
> 删除 + 文档更新 + 回退承诺移除(或回退路径有测试)。**前两步完成、第三步未完成的
> 迁移不算完成**,不得据此开启下一段迁移。(Waves 0-4 整改即按此纪律执行并收官。)

**排期中的大工程**:Factor 聚合(领域模型立正主,full-review 路线图 Wave 5)——
施工图 `docs/design/factor-aggregate-plan.md`(目标模型 / 三阶段迁移 / import-linter 契约基线)。

## Architecture Refactor (已基本落地 — 保留作历史设计记录)

> 状态注 (2026-07-08):分层架构已是现状(`cli/` → `services/` → `core/` + `infra/`,`utils/` 共享),`common/` 已不存在,构造器写盘副作用已拆到 `services/check/xml_prepare.py`。下文细节(notify/ssh 模块、cp 子命令等)与现状不符,勿按此文施工。

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
- New `ops health` command for library integrity checks(曾落地,2026-07-07 Wave 2 退役;对账职能归未来 ops doctor)

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
| 3 | 6-7 | New `ops health` command: orphan factors, dump gaps, PNL missing, source missing, file integrity(曾落地,2026-07-07 Wave 2 退役 → 未来 ops doctor)|

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

**Phase 2: 因子质量监控** — Rolling IC/IR, coverage, autocorrelation, correlation drift. Persistent store (Postgres for state/derived since 2026-07-04). `ops monitor` command (cron). Threshold alerts via Feishu.

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

The CLI surface has grown flat: `submit / check / list / info / health / pack / sync / rm / status / backfill`(注:health/sync 已于 2026-07-07 退役). The factor-lifecycle ones (`submit`, `check`, `rm`, `info`, `list`, `status`, `backfill`) all act on a single factor (or a query over factors) and naturally belong under one namespace. Plan: introduce `ops factor <verb>` as the canonical home, keep flat aliases for back-compat during transition.

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

`pack`, `sync`, `health` stay top-level — they operate on the library, not a single factor.(注:sync/health 已于 2026-07-07 退役,顶层现仅 pack。)

**Why**: discoverability (`ops factor --help` enumerates everything one can do *to* a factor), and prepares the codebase for similar groupings later (`ops dataset ...`, `ops job ...`).

**~~Soft-delete model~~ (SUPERSEDED 2026-07-04)**: 原计划让 `ops rm` 打 `FactorStatus.DELETED` tombstone + `ops sync gc` 回收。**已废弃并反向实现**:DELETED 状态 + `deleted_at` 已从代码彻底移除,`ops rm` 现在是**彻底硬删**(src/pnl/dump/feature + factor_info 行,级联删 state + snapshot,不可逆,无墓碑)。设计哲学:因子要么存在(active/rejected/未来 decay)要么被删除,删除不是一种状态。`ops sync gc` 也不再需要(sync 整体退役中)。

**Execution waves** (when picked up — 仅剩 namespace 部分):
| Wave | Description |
|---|---|
| 1 | Add `ops factor` parent parser; register existing subcommands as both flat (legacy) and nested (`factor X`). |
| 2 | Implement `ops factor run` (re-run + re-pack one factor in place). |
| 3 | Drop the flat aliases (one-shot deprecation, after team is on the new shape). |

## ops pack Incremental Mode (Not Started)

把 `ops pack` 从"全量重写 alpha_feature/{name}.{v}.npy"升级成"按需只覆写指定日期那一行"。这是 Phase 3 的 roadmap 项,设计已完成,工程量小但**暂缓实施**(2026-06-02 决定)。

**为什么需要**:
- 当前每次 `ops pack` 都重写整文件(170 MB/因子)。在 JuiceFS 上意味着所有 chunk 都脏化,全量上传,40x 浪费
- 增量模式下 mmap('r+') 只写指定行 → 单 chunk(4 MB)脏化 → S3 增量 ~4 MB
- 是 Phase E 切到 JuiceFS 后,日更场景成本的主要决定因素

**为什么暂缓**:
- 现有 `ops sync` 模型下,即使做了增量 pack,sync 那侧仍按文件级 size+mtime 比对,等大小判断会漏掉(等做完 JuiceFS 迁移再做才能真正吃到收益)(注:sync 已于 2026-07-07 Wave 1 退役,此条顾虑已消)
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

**根因**: 160/150 是 512 核机器,juicefs (go-redis) 连接池按核数 × 倍数算,单 mount 进程持有 5000+ socket 连到共生的 6380 (JFS metadata + 事发时的 ops state;2026-07-04 起 state 已迁 PG,6380 现仅 JFS metadata)。默认 maxclients 10000 对这规模从一开始就低。不是泄漏,是配置/硬件规模不匹配。

**待办 (按性价比排序)**:

1. **给 juicefs mount 设连接池上限** —— 从源头压连接数,比无限调大 maxclients 治本。
   - 查 juicefs 1.3.1 mount 是否支持 `--max-conns` / metadata 连接池相关参数 (go-redis `PoolSize` 默认 `10 * runtime.GOMAXPROCS`,512 核 → 5120)。
   - 若支持: 在所有 mount 点 (160/150/144) 显式设一个合理上限 (如 256/512),重挂生效。重挂会短暂中断该机 JFS,排期做。
   - 若不支持: 只能靠 maxclients 留足余量 + 监控。

2. ~~**评估 ops state 从 6380 拆到独立 redis**~~ —— **已过时 (2026-07-08 注)**: ops state 已迁 Postgres (2026-07-04,redis state 后端 2026-07-07 Wave 1 删除,`state.redis.url` 配置项不复存在),6380 只剩 JFS metadata,共生耦合已消。原设计留档:
   - ops state 量极小 (state hash + index set + checks list),单独跑个轻量 redis (甚至复用 6379) 即可。
   - 改 `config.yaml` 的 `state.redis.url` 指向新实例 + 数据迁移 (state-* key 量小,SCAN+MIGRATE 或重建)。
   - 权衡: 多一个要维护的 redis vs 故障隔离。优先级看共生事故是否再发。

3. **连接数监控告警** —— 当前打满前无告警 (跟 server-topology "监控=人工" 一致)。
   - 简单版: cron 每 5min `redis-cli INFO clients` 的 `connected_clients` 超阈值 (如 40000) 发飞书。
   - 通知实现待定(原 `ops/infra/notify/feishu_send.py` 已随 notify/ 删除,2026-07-07)。


## Compliance 判定重做(2026-07-13 立项,先测量后定策;分支 claude/compliance-redesign)

**起因**:用户观察 compliance 判定有问题。走查现状(`checker/compliance_checker.py`):
数据 = long_backtest 全历史 dump(工作区 v2 npy,逐日带符号持仓金额向量);
判定 = 尾部 762 个文件窗口内逐日查四项(个股 max|w|/Σ|w| > 5% / 总持股 < 100 /
多头 < 50 / 空头 < 50),**任一天任一项违规 → 整因子 REJECTED**;
空/NaN/总额 0 的天**静默跳过**。

**已确认的缺陷清单**(2026-07-13 讨论):
1. 判定基数漂移:窗口按文件数截尾,空天再跳过 —— "检查了多少天"完全取决于
   数据起始时间(回测固定 2015-2025,但不少数据源起始晚,前段全空);
2. 跳过天无底线:窗口里只剩 30 天可读也照判,分母缩水不告警(用户点名认同);
3. 零容忍:762 天 1 天边界值(5.01%)= 300 天 20% 同罪;
4. 结构性假设多空:空头 < 50 必拒 ⇒ 纯多头因子永远过不了(硬编码产品假设);
5. 5% 口径:分母是当日总敞口 Σ|w|,非 booksize/净值;
6. dump_alpha 依赖继承:long_backtest 的 prepare 只声明 dump_pnl,
   dumpAlphaFile=true 是从 checkpoint 的 prepare 继承的(隐式耦合,脆弱)。

**方向(用户拍板)**:应该检查每一天,再做违规判定;**但起始日/容忍度/
有效天数下限等一切阈值先不定** —— 没有"全库多少因子会撞线"的分布数据,
无法评估任何政策(先测量后定策)。

**摸底方案(已定)**:
- 存**阈值无关的逐日原始统计**(每因子每交易日四列:总敞口 Σ|w| /
  最大单股占比 / 多头持股数 / 空头持股数)—— 任何候选政策之后都是对
  缓存的秒级查询;**不存**"当前阈值下的违规计数"(只能回答一个问题);
- 形式:per-factor npz(~125KB,全库 ~1GB)+ 汇总 CSV(首末有效日/
  有效天数/gap 数/maxpos 分位数/多空持股分位数)+ 无 feature 覆盖名单;
  **先文件不进 PG**(has_pnl/dump_days 与 ops health 两个僵尸前例 ——
  等新模型定了、确认有长期消费者再议 PG 化);
- 数据源 = alpha_feature v2(JFS 共享一处读全库,不跨机凑 dump sidecar)。
  格式事实已核:裸 memmap 无 npy 头(shape 按文件大小推,3900×H)、
  delay=1 有 -1 行偏移(分布统计无影响)、全 NaN 行 = 无 dump 日;
- 成本:~1.3TB 顺序读,一次性小时级,断点续跑(已有 npz 跳过),160 nohup。

**当前状态**(2026-07-15,分支 `claude/compliance-survey`,runbook
`docs/design/compliance-survey.md`):脚本已沙盘 + 五问对抗验证过关。加了 dump 回落源
(覆盖被拒因子的完整判定域)+ `--sample N`(只从有源因子抽,可复现)。**feature↔dump
等价性确认**:pack 是纯字节搬运(verify_sample 自证),survey 四列 = checker 同款表达式,
计数/max/跳过逐位一致、total_abs 仅差 ~1e-16 FP;delay=1 feature 读只早 ≤1 交易日的日期
标签、不碰分布。对抗验证收口三修(commit de53235):dump 越界崩溃守卫 / `total_min` 列
补齐第四阈值 / nanmax 哨兵收窄。

**抽检已完成**(2026-07-15,160,`--sample 8` seed=0):8/8 出统计、自检全过,
仪器验证 OK。判读注意:feature 源 = 已过关的安全内区(选择偏差),指标远离阈值是
预期,推不出阈值松紧 —— 定策靠边界人口(被拒 dump + 活因子窗外早期天),见
runbook"判读注意"。

**全量摸底 + 定策 + checker 重写已完成**(2026-07-16,详见 runbook
`docs/design/compliance-survey.md` "定策与 checker 重写"节):
- 全量 7972 因子(`--source auto`)+ 违规画像(`scripts/compliance_profile.py`,
  对抗验证过关):全库仅 35 个有违规日(0.44%),两极分化 —— active 违规者 12 个
  全是 ≤2 天早期毛刺,持续违规(≥24 天)全在已拒,中间 2~24 巨大空档;
- **已拍政策**:四阈值不变 + 全史每日 + 跳过无效日 + 容忍 K=10 + 硬顶 2×(10%);
  缺陷 1/2/3 由"全史+跳无效日+容忍"根治,缺陷 4(纯多头豁免)数据证明无客户不做,
  缺陷 5(5% 口径)维持现状(用户拍:约束不变);
- checker 落地(commit 9b43df3 起,分支 `claude/compliance-survey`),影子对比
  active 零状态变化(0 触硬顶、12 毛刺全在容忍内);评审收口:inf 日硬拒(不继承
  软线 NaN 洞)、dump 读失败计数告警(不静默当无效日)、14 例单测。

**剩余**:①22 条已被拒 compliance 因子的 dump 源新旧对比(回归材料,须在持有其
dump 的机器跑;coverage-missing 里还有 123 个 active 双缺源,影子对它们是盲区);
②执行者把 `violations.csv` 推入 repo(回归材料存档);③合并前在有 gsim 的机器跑
`uv run pytest -m e2e`;④缺陷 6(long_backtest 的 prepare 显式声明
dump_alpha=True —— compliance 数据来源仍踩隐式继承)。
