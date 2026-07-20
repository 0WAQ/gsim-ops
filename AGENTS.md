# AGENTS.md

给 AI coding agent 的项目导览。读者预设对本项目零背景。文档与注释主语言为中文
(术语/标识符保留英文),本文件沿用同一约定。

## 项目概览

**ops** 是一个 Python CLI(包名 `ops`,入口 `ops.main:main`),负责 gsim alpha
量化因子的**验证、回测、生命周期管理与生产编排**。它不是回测引擎——回测
由外部 gsim 框架(`/usr/local/gsim/`)执行;ops 是编排者:决定哪些因子跑、
跑什么、结果落哪、状态怎么变。核心流程是把因子从研究员的 dropbox 提交进
staging,跑 6 stage + archive 段的验证流水线,入库后由产线接管:`ops produce` 日增生产
因子 dump/pnl(分组形态),combo 产线(`/nvme125/production/combo`)再聚合
供实盘消费。

技术栈:Python ≥ 3.10(根 `.python-version` 钉 3.10);包管理用 **uv**(不是 pip);
运行时依赖只有 loguru / numpy / psycopg(+psycopg-pool)/ pyyaml / rich /
xmltodict;dev 组有 pytest / ruff / pyright / import-linter。

存储三层分离是核心设计:

- **语义真相**(身份/状态/表现/审计/产线拓扑)→ Postgres(server-160 docker,
  host 15432,库 `ops`)。因子四表 `factor_info` / `factor_state` /
  `factor_snapshot` / `factor_history` + 产线三表 `produce_group` /
  `produce_group_member`(组 roster/ordinal/muted)/ `produce_single`
  (单产注册)。PG 是唯一真相源,零扫盘。
- **重型产物**(alpha_src / alpha_pnl / alpha_dump / alpha_feature / staging)→
  JuiceFS 共享挂载点(各机路径不同,见 `config.yaml` hosts 块);唯一本机的是
  alpha_dump(软链到 `<挂载点>.local` sidecar)。产线产物另落
  `/nvme125/production/{alpha,combo}`(170 本机,与旧 dataset 隔离)。
- 单机 dev/test 另有 `state.backend: json` 后端(JSON + fcntl),**不是生产回退**,
  也不存在任何"紧急回退"配置(redis state 后端已删除;注意 redis-sentinel 实例
  本身是 JuiceFS metadata 后端,与 ops 无关,**不可停**)。

## 常用命令(构建 / 检查 / 测试)

```bash
uv sync --group dev          # 装全部依赖(含 dev 组)
uv run ops --help            # CLI 帮助
uv run ruff check ops tests  # lint
uv run pyright ops           # 类型检查(只查 ops 包)
uv run lint-imports          # import-linter 分层契约(8 份全部 enforcing,红即挂)
uv run pytest -m "not slow"  # 快测套件(默认,CI 常跑)
uv run pytest -m pg          # 只跑需 PG 的测试
uv run pytest -m e2e -v      # 端到端(真 gsim + 真 cc 数据,约数分钟,环境不可达自动 skip)
uv run pytest tests/test_pure.py   # 纯函数测试,无需 PG
```

CI(`.github/workflows/ci.yml`,push 到 main/claude/**/feat/**/fix/** 及 PR 触发)
依次跑:uv sync → ruff → pyright → lint-imports → 断言测试 PG 可达 →
`pytest -m "not slow" -q`。改动合入前这五步必须在本地先绿。

## 代码组织与分层

入口 `ops/main.py`(argparse 分发)。**新增子命令 = 在 `SUBPARSER_REGISTRARS`
加一行 + 若写共享盘在注册函数里 `mark_write`(ops/cli/common.py)。**

四层 + utils 叶子,依赖单向,由 import-linter 契约强制(pyproject
`[tool.importlinter]`,CI 跑 `lint-imports`):

```
cli/  →  services/  →  infra/  →  core/  →  utils/
```

- `ops/cli/` — argparse 解析 + 终端渲染(rich)。结果渲染**只许在 cli**;
  services/core 禁引 rich(契约 C9),services 只经 `utils.printer` /
  `utils.live_table` 做过程叙事。
- `ops/services/` — 用例编排,**每个子命令一个包**(submit/check/restage/…)。
  各 service 包互相独立(契约 C3),共享能力下沉 core。每个包自带 `CLAUDE.md`
  讲模块细节——动手改某个命令前先读它。
- `ops/core/` — 领域模型,纯,无 I/O。关键类型:
  - `core/paths.py::FactorPaths` — 盘面布局**唯一正主**,任何地方不得手写
    `config.alpha_xxx / name`;布局事实由类型承载:src/staging/dump 是目录,
    **pnl/feature/池副本是单文件**(删除用 `unlink()`,不要 `rmtree()`)。
  - `core/factor.py::Factor` — 全库唯一叫"因子"的类型(identity/state/snapshot
    三切面)。
  - `core/state.py::FactorStatus` — 状态枚举(submitted/checking/active/
    rejected),与 DB `chk_status` 约束同一提交改;操作事件枚举 `HISTORY_OPS`
    与 DB `chk_op` 同一提交改。
  - `core/metrics.py::SNAPSHOT_METRICS` — 可过滤/排序 metric 键集与取值语义的
    唯一注册表,SQL 下推 / 内存取值 / CLI `--sort-by` choices 三方派生。
- `ops/infra/` — I/O 与外部系统:`config.py`(YAML 配置,`OPS_CONFIG` 环境变量
  优先,`OPS_*` > hosts > vars 三级变量解析)、`repository.py::FactorRepository`
  (**service 层读写因子的唯一门面**:get/find/register/transition/attach_snapshot/
  delete/archive/recall/unstage/purge_artifacts/lock)、`store/`(state PG/json
  后端)、`info/`、`snapshot/`、`pg.py`(**唯一建 PG 连接池的地方**,按
  (pid, conninfo) 去重,fork 安全)、`lock.py`(跨机 PG advisory lock,锁键固定
  命名空间 `ops:factor_lock`,json 后端才用 fcntl)、`sudo.py`(写命令自提权)、
  `gsim/runner.py`(shell out 到 gsim)。
- `ops/utils/` — 共享工具(叶子,不引其它层)。

关键契约除分层外还有:cli 不直引 infra/core(C2,接缝豁免集中在
`ops/cli/common.py`);services 只经 store 工厂、不引具体后端和 DB 驱动(C7/C8);
DB 驱动只在 infra(C8)。

### 验证流水线(ops check)

6 个 stage,**stage 身份的唯一真相源是 `ops/services/check/stages.py` 的
`PIPELINE` 元组**:`validate → checkbias → checkpoint → long_backtest →
compliance → correlation`(archive 不在 PIPELINE 内,是 for-loop 之后
pass 分支的动作)。路由三态:validate/long_backtest 失败回
SUBMITTED 留 staging 重试(retryable);其余失败置 REJECTED;compliance/
correlation 失败额外保留 pnl+dump。crash 靠 staging 重扫自愈(check 按 staging
目录扫,不看 state status)。

### 产线(ops produce + combo)

`ops produce --grouped` 是分组产线(设计 `docs/design/factor-produce-groups.md`):
**在产 = 组产(roster 入 PG produce_group 两表,组 XML 腿序冻结——checkpoint
按腿序号反序列化,唯一合法编辑是 `dumpAlphaFile` 静音翻转)+ 单产
(produce_single 注册表,"组大小为 1 的组");待产 = 可生产 − 在产,纯推导,
新到因子默认屏蔽**。组 XML 由 sibling `<Alpha>` 平铺(共享 init 提速),
`scripts/bootstrap_groups.py` 建组(dry-run 出样品,--apply 落盘写库)。
combo 产线(`/nvme125/production/combo`,设计 `docs/design/combo-production.md`):
4 combo(fguo/lhw/zxu + combo_eq)× 3 mode,XML 全部出自
`scripts/build_combo_xml.py`,勿手写。

### 状态机与词汇(schema v3)

状态四值:SUBMITTED → CHECKING → ACTIVE / REJECTED(restage 召回、approve
豁免、cancel/rm 删除)。**删除不是状态**——因子要么存在要么被 rm 删掉,无墓碑。
词汇:**在册**(有记录含被拒,`status != 'submitted'` = `ops list` 因子集)/
**在库**(ACTIVE)/ **入库**(变为在库那一刻,entered 事件)/ **测得快照**
(factor_snapshot = 最近一次 check 测得的表现,被拒也写)。

## 代码风格

- ruff:line-length 100,target py310,规则集 F / E7 / I / UP006/007/035/045 /
  B006/B008。不开 pydocstyle(与双语 docstring 约定冲突)。
- **SSOT 纪律**:每个事实族只有一个正主(正主表见根 `CLAUDE.md` "SSOT 表"节)。
  改代码/review 第一问——"你在问正主吗?"新增重复真相源前先收敛。
- **注释规范**(根 `CLAUDE.md` 有全文,要点):
  - 注释写**为什么**,不写是什么;中文行文,标识符/命令/术语保留英文。
  - 每模块开头一段 module docstring:是什么 + 边界 + 关键不变量(范本
    `ops/core/paths.py`、`ops/infra/repository.py`)。
  - 注释不当 changelog:日期/批次名/PR 号不进 inline;编年史在 git blame +
    `docs/remediation/JOURNAL.md`。判据:三个月后这行注释还成立吗?
  - 易 stale 的具体值(计数/机器数/阈值)不写死,注明来源或只写性质。
  - SSOT 锚点点明(`正主在 X` / `派生自 Y`)。
- **破坏性操作一律 opt-in**:默认路径永不删用户数据;破坏性变体藏在显式 flag
  或独立子命令后;批量操作默认 dry-run,`--apply` 才执行;rm/cancel/clear 默认
  交互确认(`-y` 跳过)。新增碰文件/状态/远端的命令必须遵守同一模式。
- 已退役命令(`cp`/`scp`/`compiler`/`resubmit`/`recheck`/`health`/`sync`/
  `refresh`/`backfill`)不要复活;`ops/services/` 下残留的同名目录只是
  `__pycache__` 残骸,无实际代码。

## 测试策略

测试在 `tests/`(pytest;`testpaths = ["tests"]`,`pythonpath = ["."]`),
细节见 `tests/README.md`。重点覆盖 check 流水线控制流、生命周期写命令的状态
转移与产物落点、PG store;不测 gsim 回测算法本身。

- marker:`pg`(需测试库 `ops_test`,不可达自动 skip)、`slow` + `e2e`(真 gsim
  + cc,默认不跑)。
- **PG 隔离**:每个 pytest session 在 `ops_test` 里建随机 schema
  `t_<hex>`(search_path 隔离,并行安全,session 结束 DROP CASCADE);advisory
  lock 用仅测试的 `state.lock_namespace` 注入(生产锁键固定,绝不能动)。
  **绝不碰生产 `ops` 库。**
- 本地无 PG 时:`docker compose -f docker-compose.test.yml up -d` 起测试实例
  (端口刻意 15433),按 `tests/README.md` export `OPS_TEST_PG_*`。
- **可测性接缝**:`CheckerPipeline.__init__` 接受 `checkers` 参数,测试注入
  fake checker(`tests/conftest.py:fake_checkers`)在指定 stage 抛
  CheckFail/CheckSkip,断言 state 转移 + 文件落点;pass 路径的 gsim simsummary
  由 `fake_metrics` fixture monkeypatch。所有测试数据路径相对 `tmp_path`。
- 新写命令注意:`tests/test_pure.py::test_write_command_declarations_match_registry`
  会把 `mark_write` 声明集与注册表对账。

## 安全注意事项

- **密钥不落库不落 git**:PG 密码经 `state.postgres.password_file` 指向
  `scripts/postgres/.env`(root-only,不进仓库);config 里绝不写明文密码。
  历史教训:MinIO 密钥曾入库,git 历史仍在,轮换是挂账——**任何新密钥绝不
  提交**。
- **sudo 自提权模型**:JFS 共享路径全部 root-owned(group `alpha-core` /
  `alpha-data` 只读),所有写走 root。写命令经 `cli` 注册处 `mark_write` 声明,
  入口 `maybe_elevate` 自动 `sudo --preserve-env=OPS_*` 重 exec;read-only 命令
  直通。绕过这个模型(手工 chmod / 改 owner)会破坏集中运维约定。
- **跨机并发**:同一因子的并发变更由 PG advisory lock 串行化;测试注入锁命名
  空间是唯一合法偏离,生产 config 绝不设置 `state.lock_namespace` /
  `state.postgres.options`。
- **doctor --fix 删除管道**走"五道闸"(锁内重验 / ACTIVE 绝缘 / 路径白名单 /
  形态闸),改动这些判定时保持"判错最多该删没删,不可能误删"的不变量。
- 脚本处理机器间数据传输时把本地 144 当 WAN 节点(跨段路由,调宽超时、避免
  chatty 协议)。

## 部署

- 无自动部署流水线;代码在多台执行机(北京 IDC 160/150/170、本地 144)滚存,
  rev 保持一致。生产默认配置是仓库根的 `config.yaml`(JFS + PG state)。
- PG schema 的正主是 `scripts/postgres/` 迁移脚本(含 `README.md` 迁移台账);
  代码侧 `ops/infra/schema.py::ensure_schemas` 只做幂等引导(FK 依赖序
  info → state → snapshot → produce_group 三表)。
- 本机部署形态由 `ops setup` 声明式管理(按 hostname 匹配 `config.yaml` hosts
  块,幂等补建缺失,`--check` 只读体检);盘 ↔ PG 一致性由 `ops doctor`
  对账(默认只读,`--fix` 逐族确认)。
- **AI 工具链**(kimi code):全局 memory 软链 `~/.kimi-code/AGENTS.md` →
  仓库 `.claude/memory/MEMORY.md`(memory 本体 git 滚存;新机跑
  `.claude/link-memory-kimi.sh` 幂等补建);项目级技能在 `.kimi-code/skills`
  (软链 `.claude/skills`)。

## 文档地图

- `docs/architecture.md` — 架构总览(分层/生命周期/存储/拓扑,先读这个)
- `CLAUDE.md` — 命令全集、SSOT 表、主机拓扑、技术债与演进史(维护者参考)
- `ops/<layer>/CLAUDE.md`、`ops/services/<cmd>/CLAUDE.md` — 各层/各命令模块导览
- `docs/components/` — 子部件详解;`docs/design/` — 设计文档
  (`factor-produce-groups.md` 分组产线、`combo-production.md` combo 产线);
  `docs/remediation/JOURNAL.md` — 编年史
- `.claude/plans.md` — 路线图
