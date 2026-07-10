# Factor 聚合施工图(领域模型立正主,full-review 路线图 Wave 5)

> 2026-07-09。本文是 full-review(`docs/reports/full-review-20260707.md`)第三部分
> 六/七/八节的**实现级施工图 + 基线回填**:目标模型与病灶证据以报告为准,本文
> 负责三件事 —— ①对照 2026-07-09 的 main(Waves 0-4 已合并)回填"哪些病灶已
> 治好、哪些还在";②把迁移路线细化到可验收的阶段;③安放三件守护机制
> (import-linter 契约实测版 / SSOT 表 → 根 CLAUDE.md / 迁移完成定义 → plans.md)。

## 一、需求回顾(为什么要做)

wbai 原话(2026-07-07):

> 目前 ops list 仍然使用 FactorScanner 去全盘扫描,完全忽视了 Postgres;且我们
> 目前有很多各种各样的所谓 Alpha*、Factor* 的结构,他们非常的乱;你看到文件结构
> 分明、架构分为几层之类的,但是**在实际命令实现的一些细节中完全没有"职责分层"**。

诊断(full-review 第三部分):分层只存在于目录树上,不存在于依赖关系里;领域
模型碎成 12 个按存储介质命名的投影,**零个类型代表"因子"这个概念本身**;16 个
命令各自手工构造 store、手工 join、手工拼路径。

三个具体痛点的现状:
- "list 全盘扫描忽视 PG" —— **已治**(Wave 2:list 因子集判据 = 纯 PG,scan
  退出热路径);
- "Alpha*/Factor* 结构乱" —— **部分治**(12 个投影死了 5 个,还剩 7 个,含一对
  同名撞车的 FactorInfo);
- "命令实现无职责分层" —— **未治**,本施工图的主体。

## 二、现状基线(2026-07-09,main @ 8b28ada)

### 2.1 路线图回填(报告第三部分·七)

| 报告路线图 | 状态 | 落点 |
|---|---|---|
| Wave 0 纯删除 + CI | ✅ | W1-W6 / T1-T3(JOURNAL) |
| Wave 1 回退决断 | ✅ | F1-F6;**⚠ MinIO/Feishu 密钥轮换仍未执行** |
| Wave 2 僵尸拆除 + 因子集正位 | ✅ | V1-V4 + migrate_drop_derived 已在生产执行(U1) |
| Wave 3 SSOT 收敛 | **部分** | 已做:Stage 表(S11)、`_batch`+CAS、now_iso(S12,`utils/clock.py`)、异常归因、xmlio/factor_dir、discovery_method 硬校验(S14 部分)、窗口命名常量(S13 半步,进 xml_prepare 未进 config)、~~FactorPaths(S4)~~(2026-07-09 阶段 1)、~~glob→LIKE(S9)~~(2026-07-09 阶段 2:`_glob_to_like` 转义+`?`→`_`+字符类跳过下推,`snapshot_where` 一处修)。**未做:metric 注册表(S8,SQL 半边已收敛、list 内存镜像仍在)、WRITE_COMMANDS 派生(S16)** |
| Wave 4 领域模型立正主 | **未动** | 即本文 —— 因分支命名占用,以下称 **Wave 5** |

### 2.2 类型普查:12 → 7(目标 4)

| 现存类型 | 位置 | 归宿 |
|---|---|---|
| `FactorRecord` | core/state.py | Repository 内部行网关(不再出现在 service import) |
| `FactorInfo`(表) | infra/info/base.py | 同上 |
| `FactorSnapshot` | infra/snapshot/base.py | 同上 |
| `FactorRow` | infra/query.py | **消亡**(随 query_factors 被 `repo.find` 单条 SQL 取代) |
| `FactorInfo`(扫盘) | core/library.py | **改名 `ScannedFactor`**(D4,author→author_guess),随 LibraryScanner 降级为 doctor 对账工具 |
| `AlphaMetadata` | core/alpha/metadata.py | 保留,正名 check 工作台视图;构造/方法去 I/O(known debt:get_v2npy_files 扫盘) |
| `FactorMeta` | core/factormeta.py | 保留:meta.json 身份证格式,Repository 独家读写 |
| **新增** `Factor` 聚合 | ops/core/factor.py | **全库唯一叫"因子"的类型**(identity + state + snapshot 三切面) |

已消亡(Waves 0-2):DerivedRecord、FactorIndexEntry 等 5 个。

### 2.3 import-linter 实测基线(本日,直接依赖口径)

报告草案(§8.1)两处需修正才能运行:①顶层须加
`include_external_packages = true`;②所有 forbidden 契约须加
`allow_indirect_imports = true`(否则 cli→services→core 的合法间接链恒红,
契约永不可能绿 —— 报告原意是禁**直接** import)。修正后实测:

| 契约 | 审查时 | **今日** | 违例明细 |
|---|---|---|---|
| C1 layers(cli→services→infra→core→utils) | 5 红 | **3 红** | utils.log→infra.cache;core.library→infra.config;core.alpha.metadata→infra.config |
| C2 cli 不得直接 import infra/core | 19 红 | **18 红** | 14× cli/*→infra.config(全是 `Config` 类型注解/加载);4× cli/{list,pack,restage,status}→core.state(FactorStatus choices) |
| C3 service 包相互独立 | 9 红 | **9 红**(构成已变) | submit→rm(PV7 回收 helper)、restage→rm(同)、submit→list ×2 + backfill→list + check→list(datasource/npy_index)、backfill→submit + clear→submit(parser)、approve→check(stages.CORRELATION) |
| C5 utils 是叶子 | 2 红 | **1 红** | utils.log→infra.cache |
| C6 infra 不碰展示层 | 2 红 | **1 红** | infra.sudo→rich |
| C7 services 只用 store 工厂 | 2 红 | **✅ 绿** | — |
| C8 db driver 只在 infra | 绿 | **✅ 绿** | — |

**合计:2 绿 5 红,32 条边**(审查时 7/8 红)。修正版 TOML 全文见附录 A。

> **2026-07-09 更新**:阶段 0/1 落地后 C1/C5/C6 已清零,五份 enforcing
> 进 CI(pyproject),C2=18/C3=9 挂 ratchet 基线(contracts-baseline.toml)。
> **同日阶段 2**:C3 九条边全消清零转 enforcing(6/6 契约绿),基线只剩 C2=18。
> 类型普查推进:FactorRow 消亡、扫盘 FactorInfo 已改名(D4)、表 FactorInfo 降级
> 为 core FactorIdentity 的别名、`Factor` 聚合落地 —— 12 → 5(目标 4,剩
> live_table 的展示用同名 FactorRow 属 UI 行,阶段 3 顺手正名)。

### 2.4 其余 D 系列病灶现状

- **D1 Repository 门面**:未动。submit/backfill/check 仍各抄一遍 info+state 双表
  写;`query_factors` 仍是三次查 + 内存 join(自带 TODO);rm 仍"问 state 删 info"。
- **D2 连接池**:**核心已落地**(2026-07-09,`ops/infra/pg.py` `get_pool` +
  `ensure_schema`):按 `(pid, conninfo)` 去重(三表同库塌成一池,治生产 P0
  `too many clients already` —— check 每因子建池、20 worker 打爆 PG 默认 100 连接)
  + atexit 退出收尾(治 `__del__` 刷屏)+ fork 隔离。**剩:DDL 彻底滚出 store
  `__init__`、`max_size` 参数化**,归阶段 1 收尾。
- **D3 返回值约定**:**已落地**(2026-07-09,`infra/errors.py` + delete 三家
  统一 bool,详见 §3.3 与阶段 1 勾选)。
- **D4 双 FactorInfo 同名**:**已落地**(2026-07-09,ScannedFactor/author_guess)。
- **PV7 新语义待收编**:产物两面模型(check 面 `_recycle_check_artifacts` /
  服务面 `_purge_artifacts`)目前住在 `services/rm/rm.py` 被 submit/restage 跨包
  借用(C3 的 3 条边)—— 它们本质是**产物面领域操作**,归宿是 Repository。

## 三、目标模型(实现级)

### 3.1 `Factor` 聚合(ops/core/factor.py)

```python
@dataclass(frozen=True)
class Factor:
    identity: FactorIdentity       # name/author/discovery_method/created_at
    state: FactorState             # status/version/时间戳/last_fail_*
    snapshot: Snapshot | None      # 入库时不可变快照(未入库 = None)
```

不变量在类型层表达:`state.status == ACTIVE ⇒ snapshot is not None 且
snapshot.at == state.entered_at`(构造时校验,坏数据 warn 不炸 —— 存量迁移期
残留见 U2 鬼影记录)。service 层只见 `Factor`,三张表的 dataclass 降级为
Repository 内部行网关。

### 3.2 `FactorRepository`(ops/infra/repository.py)

方法集 = 报告§六推导 + PV7 后的语义更新:

**记录面**:`get(name) -> Factor | None`、`find(author=,status=,fail_stage=,
field=,tables=,metric 排序/过滤,limit=) -> list[Factor]`(单条三表 LEFT JOIN,
退役 query_factors/FactorRow)、`register(identity, *, submitted_at)`(原子
info+state,一个事务 —— submit/backfill/check 三份手抄编排收编)、
`transition(name, to, *, expect=)`(现 CAS 直接搬入)、`attach_snapshot(...)`
(内部强制 snapshot_at == entered_at,含 stale 自愈)、`delete(name)`(info 级联)、
`exists(name)`(一种语义,消灭"问 state 删 info")、`lock(name)`(factor_lock 门面)。

**产物面**(PV7 两面模型进类型):

```python
class ArtifactScope(Flag):
    CHECK   = auto()   # alpha_pnl + bcorr 池副本(离库即失效,一律回收)
    SERVING = auto()   # alpha_dump + alpha_feature(last-known-good,--purge 才动)

repo.purge_artifacts(name, scope: ArtifactScope)   # 收编 _purge/_recycle 两个跨包 helper
repo.paths(name) -> FactorPaths                     # S4 唯一 owner
repo.stage/unstage/archive/recall(...)              # staging↔lib 移动 + XML 重指 + pnl 分流
repo.iter_staging()/iter_library()/orphans()        # doctor 的对账原语
repo.meta(name) -> FactorMeta                       # meta.json 独家读写
```

### 3.3 支撑件

- **`FactorPaths`**(S4):**已落地**(2026-07-09,`ops/core/paths.py`)——
  `src/pnl(单文件!)/dump/feature[]/staging/池副本` 的唯一拼法,40+ 处散布路径
  收编清零;"pnl 是单文件"从 CLAUDE.md 警告降级为类型事实。
- **`ops/infra/pg.py` 池注册表**(D2):**已落地**(2026-07-09,分两步:
  atexit 退出收尾消 `__del__` 刷屏;`get_pool(conninfo)` 按 (pid, conninfo) 去重
  + `ensure_schema` 建表一次 —— 治生产 P0 连接打爆,PR #4)。剩 DDL 彻底滚出
  store `__init__`、max_size 参数化。
- **`ops/infra/errors.py`**(D3):**已落地**(2026-07-09):`StateConflict`
  迁居 + `FactorNotFound`(KeyError 子类保兼容);delete 统一 `-> bool`。
  `SnapshotAlreadyExists` 等新异常待 Repository 有 raise 方时再加(不预建空壳)。
- **D4 改名**:**已落地**(2026-07-09):core/library.py 的
  `FactorInfo → ScannedFactor`,`author → author_guess`(目录名正则猜测,不是
  权威身份);LibraryScanner 去 config_path 死参 + from_config_path 运行期依赖。

## 四、迁移路线(Expand-Migrate-Contract,每阶段独立可验收)

**总纪律**(沿承 remediation):验收 = 旧路径**物理删除** + 文档同批更新 + CI 绿
+ 契约红线数降到目标值;没删完不关单。行为验证复用金丝雀手册
(`docs/remediation/VERIFY-WAVE3-STAGE-TABLE.md` 阶段 3 环路)。全程 PG 表结构、
JFS 布局、CLI 表面**零变化** —— 纯代码组织重构,不需要升级窗口,三机滚存即可。

### 阶段 0 · 守护先行(半天)✅ 2026-07-09 完成

- ~~import-linter 进 CI~~:**比原计划更进一步** —— 阶段 1 的 5 条边先清了,
  C1/C5/C6/C7/C8 **五份直接 enforcing**(pyproject `[tool.importlinter]`,
  CI `lint-imports` 红即挂);C2/C3 走 ratchet 基线(`contracts-baseline.toml`
  + `scripts/ci/import_baseline.py`,违例数只降不升,清零转 enforcing);
- ~~SSOT 表进根 CLAUDE.md、完成定义进 plans.md~~(随本文档首版已落);
- 运行注记:TYPE_CHECKING 块内 import 经 `exclude_type_checking_imports`
  排除 —— core 对 Config 的纯类型引用不算运行期违例(连类型也摘掉是阶段 2)。

### 阶段 1 · 前置小件(1-2 天)✅ 2026-07-09 完成(DDL 滚出 __init__ 一项顺延阶段 2)

- ~~D4 改名 ScannedFactor/author_guess~~ ✅(顺带删掉 LibraryScanner 未用的
  config_path 形参与 from_config_path 运行期 Config 依赖);
- ~~D2 池注册表 + fork 守卫 + 显式 close~~ ✅(生产 P0 提前拉动,见 U3);
  剩 DDL 彻底滚出 store `__init__`、max_size 参数化;
- ~~D3 errors.py~~ ✅(`ops/infra/errors.py`:StateConflict 迁居 +
  FactorNotFound(KeyError 子类保兼容);snapshot delete → bool 与
  State/Info 对齐。**调整**:SnapshotAlreadyExists 不预建 —— 只定义有 raise
  方的异常,W3 幽灵状态的教训;Repository 落地时按需加);
- ~~清 5 条散边~~ ✅(CACHE_ROOT 正主迁 `ops/utils/cachedir.py`;sudo 降级
  stderr print;core 两处 Config 转 TYPE_CHECKING);
- ~~FactorPaths 落地~~ ✅(`ops/core/paths.py`,40+ 处拼接全量收编:rm/restage/
  cancel/clear/submit/run/info/check(to_lib/on_reject/scan)/pack(worker 直收
  FactorPaths,可 pickle)/library scanner;meta.json 文件名常量统一;布局契约
  测试 `tests/test_factor_paths.py`);
- **计划外新增:cancel 产物守卫**(2026-07-09 生产实测缺口,见 JOURNAL U3):
  alpha_src 有归档的因子拒绝 cancel(只删记录会留孤儿),指引 ops rm;
- 验收:C1/C5/C6 → **0 红** ✅;fast suite 绿 ✅;e2e 待 160。

### 阶段 2 · Repository 门面(3-5 天,最大单件)✅ 2026-07-09 主体完成

- ~~`Factor` 聚合 + Repository 记录面~~ ✅(`ops/core/factor.py`:
  FactorIdentity/FactorSnapshot dataclass 正名迁 core + `Factor` 三切面聚合;
  `ops/infra/repository.py`:get/find/register/record/transition/append_check/
  attach_snapshot/discard_snapshot/delete/exists/lock);`query_factors` +
  `FactorRow` 物理删除 ✅(find = 单条三表 LEFT JOIN,snapshot 下推表达式经
  `snapshot_where`/`metric_order_expr` 与单表 list 共享,不再镜像 S8 的 SQL 半边);
  - **§3.1 不变量修正**:"ACTIVE ⇒ snapshot 非空"不成立 —— `ops approve`
    合法产生无快照的 ACTIVE(REJECTED 不写快照,approve 只翻状态)。实际软校验
    收窄为 "snapshot 存在 ⇒ snapshot_at == entered_at"(warn 不炸);
- ~~产物面收编~~ **半**:`_purge_artifacts`/`_recycle_check_artifacts` 迁入
  `purge_artifacts(scope: ArtifactScope)` ✅(rm/restage/submit --overwrite 三个
  调用方全迁);**`archive/recall` 收编 to_lib/restage 顺延**——to_lib 刚经历
  身份发散 P2 修复(JOURNAL U3),金丝雀环路待复跑,一次只动一件事;归阶段 3
  首批(彼时 to_lib 已被生产验证)。
- ~~datasource/npy_index 迁共享领域模块~~ ✅(`ops/core/datasource.py`;顺带
  `parse_factor`/`infer_author_from_dir` 迁 `ops/core/factormeta.py`,
  submit/parser.py 与 list/datasource.py 物理删除);
- ~~approve→check 边~~ ✅(CORRELATION 迁 core/state.py + 语义 API
  `FactorRecord.correlation_rejected()`;stages.py re-export,PIPELINE 引用同一
  常量 —— 单一定义,顺序/路由 SSOT 仍在 stage 表);
- ~~DDL 滚出 store `__init__`~~ ✅(阶段 1 顺延件:`ops/infra/schema.py::
  ensure_schemas` 按 FK 依赖序引导;Repository 首次触达 PG 懒调用、测试 fixture
  显式调用;store 构造零副作用。ts_in/ts_out 双镜像同批收敛到 infra/pg.py);
- 验收:C3 → **0 红** ✅(9 条边全消,契约转 enforcing,6/6 绿;基线只剩
  C2=18);submit/backfill/check 的双表写只剩 `repo.register` 一个入口 ✅
  (且升级为单事务原子);fast suite 42 passed ✅;**PG 组
  (tests/test_repository.py 5 用例 + 存量)+ 金丝雀环路复跑待 160**。

### 阶段 3 · 命令塌缩 + 契约全绿(3-5 天)

- **首批(阶段 2 顺延)**:`repo.archive/recall` 收编 check.to_lib / restage
  的移动+XML 重指+pnl 分流(前置:金丝雀环路在含身份守卫的 to_lib 上复跑通过);
- 8 个命令(status/info/approve/cancel/clear/rm/list/backfill)服务层塌缩到
  <20 行(资格谓词 + `_x_one` 动作 + `_batch` 骨架 + Repository);
  submit/restage/check/pack 收编存储编排、保留真实业务;
- cli 层瘦身:Config 加载与 FactorStatus choices 下沉到 `cli/common.py` 单点
  (C2 的 18 条边收敛为 1 个豁免模块,或契约改写为 forbidden 白名单);
- import-linter 全部转 enforcing,删基线脚本;
- 验收:**7/7 契约绿**;12→4 类型完成(grep 无 FactorRow/扫盘 FactorInfo);
  8 命令行数达标;金丝雀环路 + e2e + 三机滚存冒烟绿。

**总工期估计:1.5-2 周**(与报告"Wave 4:1-2 周"一致)。

## 五、明确不做(本工程边界)

- 不动 PG 表结构、JFS 布局、CLI 参数表面(纯内部重构);
- 不做 Web API/新功能;
- `ops doctor` 不在本工程(但 Repository 的 orphans/iter_* 原语为它铺路);
- AlphaMetadata 彻底去 I/O 若牵动 check 流水线过深,允许降级为"只挪
  get_v2npy_files 两个方法"并记账。

## 附录 A · import-linter 契约(历史基线记录)

> **2026-07-09 起正主在 pyproject `[tool.importlinter]`(enforcing 五份)与
> `contracts-baseline.toml`(C2/C3 ratchet)**,本附录仅保留为基线测量记录。
> 若单独运行本附录配置,顶层还需 `exclude_type_checking_imports = true`
> (core 对 Config 的 TYPE_CHECKING 纯类型引用靠它豁免,缺了 C1 会红 2 条)。

```toml
[tool.importlinter]
root_package = "ops"
include_external_packages = true
exclude_type_checking_imports = true

[[tool.importlinter.contracts]]
name = "C1 layers: cli -> services -> infra -> core -> utils"
type = "layers"
layers = ["ops.cli", "ops.services", "ops.infra", "ops.core", "ops.utils"]
# 2026-07-09 基线:3 红(阶段 1 清零)

[[tool.importlinter.contracts]]
name = "C2 cli must not import infra or core (directly)"
type = "forbidden"
allow_indirect_imports = true
source_modules = ["ops.cli"]
forbidden_modules = ["ops.infra", "ops.core"]
# 基线:18 红(阶段 3 清零)

[[tool.importlinter.contracts]]
name = "C3 service packages are independent"
type = "independence"
modules = ["ops.services.submit", "ops.services.check", "ops.services.list",
  "ops.services.rm", "ops.services.restage", "ops.services.approve",
  "ops.services.cancel", "ops.services.clear", "ops.services.backfill",
  "ops.services.status", "ops.services.info", "ops.services.pack",
  "ops.services.run", "ops.services.combo"]
# 基线:9 红(阶段 2 清零)。注:_batch.py 是共享骨架不在名单内,合法。

[[tool.importlinter.contracts]]
name = "C5 utils is a leaf"
type = "forbidden"
allow_indirect_imports = true
source_modules = ["ops.utils"]
forbidden_modules = ["ops.core", "ops.infra", "ops.services", "ops.cli"]
# 基线:1 红(阶段 1 清零)

[[tool.importlinter.contracts]]
name = "C6 infra must not import presentation"
type = "forbidden"
allow_indirect_imports = true
source_modules = ["ops.infra"]
forbidden_modules = ["ops.utils.printer", "ops.utils.live_table", "rich"]
# 基线:1 红(阶段 1 清零)

[[tool.importlinter.contracts]]
name = "C7 services use store factories, not concrete backends"
type = "forbidden"
allow_indirect_imports = true
source_modules = ["ops.services"]
forbidden_modules = ["ops.infra.store.json_store", "ops.infra.store.pg_store",
  "psycopg", "redis", "boto3"]
# ✅ 今日绿,阶段 0 直接 enforcing

[[tool.importlinter.contracts]]
name = "C8 db drivers only in infra"
type = "forbidden"
allow_indirect_imports = true
source_modules = ["ops.cli", "ops.services", "ops.core", "ops.utils"]
forbidden_modules = ["psycopg", "psycopg_pool", "redis", "boto3"]
# ✅ 今日绿,阶段 0 直接 enforcing
```

原草案的 C4 在报告中未编号占位,沿用报告缺省;linter 抓不到、需 review 守的:
core 的文件 I/O、services 直用 rich、`_METRIC_EXPR` 镜像、sudo WRITE_COMMANDS
手抄名单(S16,阶段 3 顺手从命令注册派生)。
