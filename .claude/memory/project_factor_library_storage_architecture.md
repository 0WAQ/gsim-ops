---
name: project_factor_library_storage_architecture
description: "因子库存储架构长期方向 (2026-07-03 定): 三层分离, JFS 职责收窄不拆, Postgres 当真相源, Redis 降级为缓存"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6171a8a8-01f1-4004-bcd4-cef65b017af2
---

2026-07-03 与用户敲定的因子库存储架构长期方向 (起因: 想给因子库加查询功能, 追问到底层选型)。

**核心判断: 因子库是三层被现状粘在一起的东西, 该分开**
- 语义真相 (身份/状态/血缘/版本/指标) → DB, 不需要文件
- 重型产物 (feature 矩阵/PNL/dump) → JFS, 因为 gsim 只吃文件, 这是 gsim 存在一天就成立的边界
- 派生索引/分析 (查询索引/相关性/IC 时序) → DB, 现在还在 per-machine JSON, 该搬

**JFS 职责收窄但不拆**: JFS 不是为存储存在, 是为"给文件耦合的 gsim 提供跨机一致文件视图"。它只该服务重型产物那一层, 不该继续兼职元数据库。现状 meta.json / per-machine JSON 缓存住在文件系统只是历史惯性。

**重写 gsim IO 脱离文件系统 = 不现实**: gsim 一部分是 .so 二进制无源码控制, 且还有 [[project_incident_gsim_code_drift]] 未解决 (147 vs 160 .so 双向漂移)。连两机 gsim 一致都没搞定, 谈重写存储层是空中楼阁。所以"从头搭不依赖文件系统的库"这条路现在就是死的。短期长期都留 JFS。

**存储选型结论 (Postgres 当真相源)**:
- 用户诉求: 要支持多写 (并发不高) + 数据量小各库速度差不多 + 图方便/为未来。三点交汇 = Postgres (唯一"多写+通用+未来可扩"全占的 OLTP)。
- ClickHouse/DuckDB 是 OLAP, 多写别扭当不了真相源; 等分析真的重到 PG 扛不住, 再作为只读分析副本从 PG 灌 (170 上 yifei 已有 ClickHouse, 可复用或不掺和待定)。
- **Redis 从"真相源"降级成"可丢弃读缓存"**: 现状 Redis 是 ops state 的 source of truth (挂了虽能从 JFS 重建但它是权威)。上 PG 后真相在 PG, Redis 挂了不致命只影响一次查询快慢。这直接减轻 [[project_incident_redis_maxclients]] 那类"拿 Redis 当关键路径"的压力。
- **缓存这层先别实装**: 千级因子元数据 PG 直查就是毫秒级, 加 Redis 缓存反而引入缓存失效难题。架构留位, YAGNI, 热到扛不住再贴。

**健壮性铁律**: 派生层/索引/分析里任何东西都必须可从 JFS 一等数据 (+PG) 重建。两级可重建 = 系统健壮。这条决定所有下游设计。

**落地路径**: 见 CLAUDE.md Plans "Phase G"。store 抽象层已足够干净 (ABC base.py, default_store 单点分发), 插 pg_store 后端零业务改动。派生层 (index/metrics/datasources/bcorr) 是独立第二条迁移线。相关: [[project_factor_state_machine]] [[project_ops_roadmap_ideas]] [[reference_factor_library_system_design]]

**已落地 (2026-07-03/04, branch `feat/derived-postgres`, 2 commits)**: 派生层全部迁 PG 完成。
- PG 部署: Docker `postgres:17` 在 server-160, host 端口 **15432** (避开默认 5432), named volume `ops-pg-data`, 配置在 `scripts/postgres/` (compose + init SQL + backup.sh + proxy-up/teardown)。**数据绝不放 JFS, 只本地 ext4**。备份走 `pg_dump` 逻辑备份 (跨版本跨机安全, 别搬 volume 物理目录)。
- 代码: `ops/infra/derived/` (DerivedStore ABC + pg_store/json_store + `default_derived_store` 分发, 仿 `infra/store/`)。config 加 `derived.backend` (默认 json 回退, postgres 生产) + `derived_postgres_conninfo` (复用 redis 三层密码 fallback)。metrics/datasource/bcorr/library.py 内部改走 store, **签名不变调用方零改动**。
- ~~schema: 单张 `factor_derived` 宽表 (library_id, name) 主键 + 四组独立 UPSERT + GIN(fields/tables) 反查索引; `derived_meta` 表存 `index_built_at` 水位。~~ **已过时，见 2026-07-06 三表重构**
- index 跨机新鲜度: `alpha_src` mtime (共享 JFS 三机一致) vs PG `index_built_at`。谁先见 mtime 变谁付一次扫盘 + republish, 其余读 PG。
- 实测: PG 读全库 0.096s; index 热路径 **24.8s → 0.136s** (~180x); 冷扫盘仍 ~24s (首台/失效后)。
- ~~迁移工具 `ops/tools/derived_migrate.py`: 把 legacy `~/.cache/ops/lib/<lib>/{index,metrics,datasources,bcorr}.json` 灌进 store。已迁 7593 因子。~~ **已过时，见 2026-07-06 三表迁移工具**
- **遗留 TODO**: (1) 密码正规化 —— 现 password_file 指向 repo 内 `scripts/postgres/.env` 仅 160 有, 应挪 `/etc/` root-only + 分发 150/144; (2) 150/144 部署 (补密码 + `uv tool install` 带 psycopg); (3) ~~反查命令 `ops query --field/--table` 待做 (SQL 下推本身已由 `ops list --filter-by` 落地, 见下, 未新增独立命令)~~; (4) 分支未合 main。见 [[project_uv_tool_env_deps]]。

**读写数据流重构 (2026-07-04, commit 28bab00, 同分支)**: 迁 PG 后顺手把"适配文件系统"的代码改成"适配数据库"。**两层适配**要分清:
- 第一层 (前两 commit) = **存储介质适配**: JSON 文件 → PG。只换后端, 逻辑没动 (load_metrics 还返回 dict)。很多迁移只做到这层就停 = "把数据库当文件系统用", 迁了等于没迁。
- 第二层 (本 commit) = **数据访问范式适配**: 从"读进内存拼对象" (FactorInfo god-object + load→merge→用 三步, 文件时代烙印) 改成"数据库一次查出要的形状" (`get_all()` 返回完整行, `get(name)` 主键直取)。删了 FactorInfo 派生字段 + merge_*×3 + to_dict/from_dict; refresh_* 收 names 不收 FactorInfo; 读侧 (list/info/health) 直接消费 DerivedRecord。
- **第三层查询下推 (2026-07-04, commit a450f39, 同分支) —— 已落地**: `get_all` 把 field/tables(GIN)/metrics 阈值/sort/limit 下推 SQL(pg 后端),json 后端内存镜像同语义。下推纯预筛,上层 apply_filters/sort/`[:n]` 仍全量兜底,结果逐位等价。数值键取值/排序有单一 Python 真相源 `base.metric_get`/`sort_key`,pg 的 `_METRIC_EXPR` 逐键镜像。limit 有 gate(有 status/field/tables 过滤时不下推,因它减行非纯预筛)。至此"数据库的活在 Python 干"的半文件系统思维清除。
- 验证: 无测试套件, 靠 pre-refactor baseline diff (list/info/health 输出 + filter/sort/json 全一致); 顺带修了 `ops list` PG 后端下输出乱序 (dict 插入序 → 字母序)。

**state 迁 PG (2026-07-04, commit cf9f17c, 同分支) —— 单一真相源达成**: state (因子生命周期: status/version/check_history/时间戳) 从 Redis 迁到 PG。至此 **PG 是唯一真相源** (state + derived 同库 factor_state / factor_derived, 同主键 (library_id,name) —— **注: 此双表同主键结构已被 2026-07-06 三表重构取代, 见文末**), 不再"两个真相源数据库"。#1 架构不自洽解决。
- `PostgresStateStore` (ops/infra/store/pg_store.py): `SELECT FOR UPDATE` 事务替代 Redis WATCH/MULTI/EXEC; check_history JSONB 列; TIMESTAMPTZ 列。
- **修了真时区 bug**: naive `datetime.now()` 存进 TIMESTAMPTZ 被 PG 当 UTC (CST 机器偏 8h)。`_ts_in` 写时 `.astimezone()` 打本地 tz, `_ts_out` 读时转回本地 naive isoformat, 与 Redis `_now()` 格式对称。往返字符串一致。
- 迁移 `ops/tools/state_to_pg.py`: Redis→PG 7682 条, 对账全等 (status/version/check_history 长度/fail_stage)。put(record, stamp=False) 保留原 updated_at。
- #2/#3 保守收尾: **不改 list 因子集驱动** (仍 = alpha_src 有 index 的 = 7593, 行为不变)。`author is not None` 正名为"有 index 组"显式检查 (非 hack)。探查发现 state(7682) 与 alpha_src index(7593) 本就不一致 (109 staging-only submit + 20 无 state 孤儿), 属数据漂移归 health 管, 不塞 list。
- **关键运维事实: 承载 ops state 的 Redis (redis://mymaster...:26380/0, sentinel) 同时是 JuiceFS `/tank/vault/alphalib` 的 metadata 后端。停 Redis 进程 = 挂掉整个因子库文件。ops 迁 PG 后只是"停用 Redis 的 state key", Redis 进程必须为 JFS 继续活着。残留 state:* key 留作回退, 验稳后可精确 DEL (绝不 FLUSHDB)。**
- config.yaml state.backend: postgres (redis 段保留作回退)。Redis 降缓存但暂不实装 (直接 PG)。

**遗留 TODO** (更新): 反查命令 `ops query --field/--table` 待做 (SQL 下推本身已落地 commit a450f39: field/tables/metrics/sort/limit 全下推); refresh_* 从 list 独立成 ops refresh (已落地 commit bbc5462); 150/144 部署 (补 PG 密码 + uv tool install 带 psycopg); 分支 `feat/derived-postgres` 未合 main; 验稳后清 Redis 残留 state key; #4 时序监控用另开的 factor_metrics_history 表 (钉死: 不往 factor_derived 加 ret_<date> 列)。

**双表 → 三表重构 (2026-07-06, commit a51c85e, 同分支) —— metrics 语义从"可刷新"改为"入库时不可变快照"**: 旧的 `factor_state` (含 author/submitted_by) + `factor_derived` 宽表拆成三张表, 全部去掉 `library_id` (永远单库), `id SERIAL` 主键 + `name UNIQUE`:
- **factor_info** (`ops/infra/info/`): 身份 (author/discovery_method/created_at)。
- **factor_state** (`ops/infra/store/`): 纯状态机 (status/version/时间戳/last_fail_*/check_history), **去掉 author 和 submitted_by** (移到 factor_info); `FactorRecord` dataclass 同步删这俩字段。
- **factor_snapshot** (`ops/infra/snapshot/`): 入库时快照 (metrics + datasources + index + bcorr 四组 + `snapshot_at`)。
- 外键: state.name / snapshot.name 均 `REFERENCES factor_info(name) ON DELETE CASCADE` (删 info 级联删 state+snapshot; `ops rm` 走这条)。
- **语义变更 (核心)**: ret/shrp/mdd/tvr/fitness 从"可 `ops refresh` 重算的最新表现"变为"入库时不可变快照" (`snapshot_at = factor_state.entered_at`)。`ops refresh` 命令**已删除** (cli/refresh.py + services/refresh/ 删除)。需最新表现须重跑 backtest。archive 阶段 `_persist_derived` 一次性 insert 四组进 snapshot (index 组延后由 LibraryScanner 扫盘补)。
- **联合读**: `ops/infra/repository.py::FactorRepository.find` 单条三表 LEFT JOIN 返回 `Factor` 聚合(2026-07-09 阶段 2 退役 query.py 的 query_factors/FactorRow 三次查 + 内存合并;health 命令 Wave 2 已退役)。
- **生产迁移已执行**: `scripts/postgres/migrate_to_snapshot.sql` + `backfill_discovery_method.py`。结果 factor_info 7594 / factor_state 7594 / factor_snapshot 7485; discovery_method automated 7259 / manual 226 / NULL 109 (未入库 submitted)。迁移中清理: 108 个无 metrics 无 pnl 脏因子 (51 active + 57 rejected, checkbias/checkpoint 早期失败) + 2 个 zxu 空壳; 20 个 hwang 孤儿 (只在 derived 无 state, 2026-07-03 18:33 某次批量操作只写 derived 绕过正常流程) 由迁移脚本自动补 state (status=active)。备份 `scripts/postgres/ops_backup_before_migration_20260707.sql` (gitignore, 本地保留)。
- **derived 层现状 = 僵尸层**: 代码保留 (LibraryScanner 仍用 `ops/infra/derived/` 做 index 缓存, `ops list` line 228 有冗余 scanner.scan()), 但 metrics/datasources/bcorr 已被 snapshot 取代。待清理项记在 `scripts/postgres/MIGRATION_TODO.md` (含 ops health 计划删除)。**line 34/46 描述的双表结构已不成立**。**(注: derived 层 + LibraryScanner 索引缓存已于 2026-07-07 Wave 2 整层删除; list 因子集判据改纯 PG `status != 'submitted'` 零扫盘; ops health 已删。下方 schema v2/v3 段续)**

**schema v2/v3 + legacy 清理批 (2026-07-11 ~ 07-13, 全部合 main 四机滚存, PR #14-#20)** —— 三表结构在生产上继续演进 + 语义再定:
- **v2b: factor_state 瘦身 + factor_history 全操作审计表**。state 去掉 `rejected_at/last_fail_stage/last_fail_reason/check_history`(line 58 的 last_fail_*/check_history 已不在 state);新增 `factor_history`(op ∈ submit/overwrite/check/approve/restage/cancel/rm/backfill/entered,一次操作一条,actor 可追溯,**刻意无 FK 活过 ops rm**,发射与业务写同事务)。"最近失败"= `Factor.last_fail` 从事件表派生;置 ACTIVE 自动发 'entered' 事件。
- **v3: 测得快照 (语义再变)**。factor_snapshot 从"入库时不可变快照"(`snapshot_at = entered_at`)改为 **"最近一次 check 测得的表现" —— correlation/compliance 失败也写**(v2b 审计表卸掉快照的"入库见证"兼职是解锁前提);`snapshot_at = 该次 check 事件 at`。这让被拒因子在 list/approve 也能看到指标(起因: zxu 被拒因子 list 整行空)。词汇表正名: 在册/已归档/入库(动作)/在库(状态)/已入库,不变量 `created_at <= submitted_at`。doctor snapshot-stale 判据随之重定义(锚最近 check 事件, legacy 锚 entered_at)。
- **legacy 清理批: discovery_method NOT NULL + backfill 退役**。`factor_info.discovery_method` 从可空 + 三值('automated'/'manual'/'backfill')收口为 **NOT NULL + CHECK IN ('automated','manual')**;line 63 的 "NULL 109" 存量全部归一(池位置判 + 人工名单),'backfill' 值退役(`HISTORY_OPS`/DB chk_op 保留枚举, 存量事件是历史事实)。`ops backfill` 命令退役删除。doctor 加第八族 `timeline-drift`(created_at <= submitted_at 不变量)。`ops list` 混排加 status 列。
- 生产现状 8419 因子。执行台账 `scripts/postgres/README.md`;设计/结果 `docs/design/schema-v3.md` + `docs/design/legacy-cleanup.md` + `docs/remediation/JOURNAL.md`。
