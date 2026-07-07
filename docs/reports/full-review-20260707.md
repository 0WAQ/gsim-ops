# gsim-ops 全面审查报告(2026-07-07)

> 一人 + AI 演化两月的量化因子生命周期管理系统(ops CLI)的全面体检。
> 由三次独立审查合并而成,原为三份文档,现合订。

**审查方法总览**:四轮多 agent 审查,累计 **300+ 个并行 agent**——
① 10 子系统全景扫读 + 6 视角 PG 迁移 bug 猎杀(medium/high 发现逐条对抗验证,
独立 verifier 以"推翻它"为目标复核);② 7 视角代码工艺审查 + 事实核查;
③ 5 路专项侦查(沉积层 / 多真相源 / 分层依赖 / 退化器官 / 存储形状泄漏)。
所有发现均附 `file:line` 证据;各部分行号以其页脚标注的 HEAD 为准。

## 执行摘要

- **正确性(第一部分)**:PG 迁移引入 6 个 P0(重新入库快照永久陈旧、紧急回退全部失效、
  空库 bootstrap 起旧 schema、跨机索引缓存静默死亡、`list -n` 结果错乱、approve 因子
  反查隐身);迁移外另有裸 `ops restage` 全库召回、restage→cancel 删唯一源码等数据
  丢失路径;HEAD 上 fast 测试套件 10 failed,无 CI;S3/Feishu 密钥入库(**建议轮换**)。
- **工艺(第二部分)**:骨架好于作者自评,病根是"好方言无人执法"——pyright 配置在库
  但 38 错无人跑、31 处 star import 靠加载顺序注释续命、五个生命周期命令是同一模板的
  五份手抄(~200 行重复)、63 处 `except Exception` 其中 ~27 处无日志静默吞。
  给出 9 条工作主线 + 13 条风格守则 + 动手顺序表。
- **演化(第三部分)**:病名 **Lava Flow / 只做 Expand-Migrate 不做 Contract**。
  全量清点:13 个概念平行世代共存(所有文档化"回退"已全部失效)、22 个多真相源事实族、
  ~42 条违规 import 边(8 份分层契约今日 7 份红)、10+ 死依赖与 6 个说谎旗标;
  领域模型碎成 12 个存储形状投影,零个"因子"正主。给出 Factor 聚合 + FactorRepository
  目标模型(12 类型 → 4;8/16 命令塌缩至 20 行内)、四波 Contract 路线图、
  import-linter 契约草案、SSOT 正主表、迁移完成定义。

**最优先五件事**:①修快照陈旧 + approve 补快照;②修裸 restage 守卫 + cancel 源码
删除路径;③轮换入库密钥;④决断回退(修好或删掉,含 01-schema.sql 对齐三表);
⑤修测试 + 上 CI(pytest + ruff + pyright + import-linter)。

## 总目录

- **第一部分 · 严重 Bug 与架构评估** —— PG 迁移 P0 清单 / 迁移外严重问题 / 测试与 CI
  现状 / 架构评估(不是屎山,是三个系统性习惯)/ 命令设计与生命周期 / 行动清单
- **第二部分 · 代码工艺** —— 工具链先行 / 消灭注释契约 / 批量命令骨架 / 存储层门面 /
  错误处理政策 / 资源惯用法 / Config 治理 / CLI 套件 / 测试金字塔 / 13 条风格守则 /
  假重复清单 / 动手顺序
- **第三部分 · 架构演化** —— 术语地图 / 沉积层 G1-G13 / 多真相源 S1-S22 /
  分层失守 / 退化器官 / 领域模型碎片与 16 命令泄漏矩阵 / Factor 聚合目标模型 /
  四波 Contract 路线图 / 守护机制(import-linter 契约 + SSOT 表 + 完成定义)

---

# 第一部分 · 严重 Bug 与架构评估

**审查范围**:全仓库(HEAD = `d36dead`,branch `claude/project-review-yrf1di`),重点关注
2026-07-04 ~ 07-06 的文件系统 → Postgres 迁移(三表拆分 / advisory lock / SQL 下推)。

**方法**:两轮多 agent 审查——10 个子系统全景扫读 + 6 视角 PG 迁移专项猎杀(共 219 个
agent),medium/high 发现逐条对抗验证(独立 verifier 以"推翻它"为目标复读代码及调用方);
部分 verifier 因会话额度中断,由两轮结果交叉印证 + 人工读码补核。每条发现均附
`file:line` 证据。**本次审查未修改任何业务代码。**

**三个审查目标**(来自作者):
1. 有没有严重 bug,特别是刚完成的 PG 迁移;
2. 架构怎么样(作者自评"屎山");
3. 各命令设计、因子生命周期细节问题。

---

## 一、严重 Bug

### 1.1 PG 迁移引入的问题(按优先级)

#### 🔴 P0-1 重新入库永远拿不到新快照(确定性,无修复路径)

`factor_snapshot` 是 insert-only(`name UNIQUE`,无 ON CONFLICT,
`ops/infra/snapshot/pg_store.py:101`),而 restage / `submit --overwrite` 都不删旧快照行。
再次 check 通过时:state 先翻 ACTIVE(`ops/services/check/check.py:411`),然后
`_persist_derived` 的 insert 撞 UNIQUE 被 `except Exception: logger.exception` 吞掉
(`check.py:214-215`)。

后果:
- 新代码入库后,快照仍是旧代码的 metrics / fields / tables / bcorr;
- `snapshot_at ≠ entered_at`,破坏文档定义的不变量;
- `ops list --filter-by field=<新字段>` 永远查不到该因子;
- check 报告(`check/report.py:58` 读 snapshot_store)展示的是上一轮的指标;
- `ops refresh` 已删除,**没有任何修复路径**(除 `ops rm` 全删或手工 SQL)。

触发面:restage、restage -s rejected、submit --overwrite 都是一等公民工作流;且
`migrate_to_snapshot.sql:177`(`WHERE ret IS NOT NULL`)给迁移时的 REJECTED 因子也建了
快照行,这些因子 restage → 通过同样撞车。三个独立 verifier 全票确认。

**修法方向**:restage / overwrite 时删 snapshot 行;或把 insert 改为按入库事件的 upsert。

#### 🔴 P0-2 "紧急回退" config.prod-legacy.yaml 实际是死的

文档多处承诺"紧急回退用 `-c config.prod-legacy.yaml`",但:

- `query_factors` 非 PG 后端直接 `NotImplementedError`(`ops/infra/query.py:62`)
  → list / info / status / health 全崩;
- `RedisStateStore` 读写 `rec.author` / `rec.submitted_by`——字段已随三表拆分删除,
  **每次 put 都 AttributeError**(`ops/infra/store/redis_store.py:41`);
- `FactorRecord.from_dict` 是严格 `cls(**d)`(`ops/core/state.py:59`),旧 JSON state
  文件里的 `author` / `submitted_by` 键让 json 后端直接炸;
- legacy 配置下 `default_info_store` 抛 ValueError(`ops/infra/info/__init__.py:17-19`),
  `ops rm` 等命令不可用。

**结论:回退方案是纸面安全网。要么修好,要么删掉回退承诺**——假的逃生门比没有更危险。

#### 🔴 P0-3 全新建库 bootstrap 是旧 schema

`scripts/postgres/init/01-schema.sql:59-77` 仍建**迁移前**的两表结构(factor_derived +
带 library_id/author 的 factor_state),无 factor_info / factor_snapshot / FK / CHECK。
150/144 部署(Phase G 待办)如果 `docker compose up` 起新库,起出来的就是旧世界。

附带:store 的 `_init_schema` 有 FK 顺序问题——在空库上先构造 state/snapshot store 会因
factor_info 不存在而失败(`ops/infra/store/pg_store.py:41`,`ops/infra/query.py:65` 恰好
先建 snapshot store)。

#### 🔴 P0-4 跨机索引缓存迁移后永久失效(完全静默)

迁移把 `derived_meta` 重建为无 `library_id` 列的新表(`migrate_to_snapshot.sql:79,193-197`),
但 `ops/infra/derived/pg_store.py:332` 的 `get_meta` 仍 `WHERE library_id = %s` → 每次
UndefinedColumn。`ops/core/library.py:157` 的 `except Exception: return None` 把异常吞掉:

- 不崩,但**每次 `ops list` / `info` / `health` 都退化为全量扫盘 ~25 秒,三台机器都是**;
- `_publish_index` / `set_meta` 同样静默失败(`library.py:173-174`),缓存永远不会自愈;
- 没有任何日志或报错提示这件事发生了。

#### 🔴 P0-5 `ops list -n` 结果错乱

LIMIT 无条件下推进 snapshot 查询且**无 ORDER BY**(`ops/infra/query.py:85` +
`ops/infra/snapshot/pg_store.py:161`):PG 返回任意 N 行快照,再与 info / 扫盘白名单做
内存交集 → 返回行数不对、被列出的因子 metrics 显示空白。两轮独立确认。

#### 🔴 P0-6 approve 放行的因子永远没有快照

on_reject 不写 snapshot,approve 只翻状态(`ops/services/approve/approve.py:99-112`)→
多样性豁免进来的 ACTIVE 因子在 `--filter-by field= / tables=` 反查里**永久隐身**。而
approve 存在的意义恰恰是数据覆盖多样性——这些因子正是最需要被反查到的。

同族:cancel 只删 factor_state 不删 factor_info(级联方向是 info→state),每次 cancel
泄漏一行孤儿 `factor_info`(`ops/services/cancel/cancel.py:115`),无命令可清。

#### 🟠 P1 其它确认的迁移问题

| 问题 | 位置 |
|---|---|
| `factor_info.created_at` 未做本地时区打标(state/snapshot 有 `_ts_in`,info 没有)→ UTC 容器里全部偏 8h | `ops/infra/info/pg_store.py:59` |
| 每次 `default_*_store()` 新建 `ConnectionPool(open=True)` 且从不关闭;check 每因子建 3-4 个池 | `ops/services/check/check.py:345`、`ops/infra/info/__init__.py:20` |
| advisory lock 专用连接若在 30min 长回测中断开,锁**静默释放**而 check 继续跑,跨机互斥失效 | `ops/infra/lock.py:70` |
| backend 非 postgres 或 conninfo 拿不到时,factor_lock **静默降级**单机 fcntl,无告警 | `ops/infra/lock.py:103-110` |
| `tables=` glob→LIKE 丢了 `%`/`_` 转义(表名下划线遍地,变单字符通配);旧 derived 层的 `_glob_to_like` 有转义,移植时丢了 | `ops/infra/snapshot/pg_store.py:145`(对照 `derived/pg_store.py:17-24`) |
| LIMIT 用 f-string 拼进 SQL(当前入口是 argparse type=int,暂不可注入,但是坏习惯) | `ops/infra/snapshot/pg_store.py:167` |
| `transition` 无 from-status 守卫,任何状态可被翻成任何状态(与 TOCTOU 家族叠加,见 §3) | `ops/infra/store/pg_store.py:175` 一带 |
| hashtext 32-bit 锁键可能碰撞(良性:表现为误报 FactorLocked 跳过,但与真实竞争不可区分) | `ops/infra/lock.py:72-77` |
| StateStore ABC 声明 `list(author, status)`,PG 实现只收 `status`(LSP 违反) | `ops/infra/store/base.py:14` |
| FactorStatus 的 DECAYING/RETIRED:enum 有、CLI choices 有、DB CHECK 约束拒收——幽灵状态 | `ops/core/state.py:10`、`cli/pack.py:28` |

### 1.2 迁移之外、同样严重

| 级别 | 问题 | 位置 |
|---|---|---|
| 🔴 | **裸 `ops restage` = 全库 restage**:`-s` 默认 `'active'` 使守卫 `if not args.user and not args.status` 永远不触发;`ops restage -y` 会把所有 ACTIVE 因子搬出 alpha_src 进 staging | `ops/cli/restage.py:37` + `ops/services/restage/restage.py:89` |
| 🔴 | **restage → cancel = 源码丢失**:restage 是把 `alpha_src/<name>` **搬**(非拷)进 staging,状态 SUBMITTED 完全符合 cancel 资格,`rmtree(staging/<name>)` 删掉唯一源码副本,factor_info 成孤儿、pnl/feature 成无主产物 | `ops/services/cancel/cancel.py:102-118` |
| 🔴 | 并发 submit 竞态:输家的回滚 `shutil.rmtree(staged)` 删掉赢家刚 stage 好的目录 → SUBMITTED 状态 + 无 staging 目录 | `ops/services/submit/submit.py:207` |
| 🔴 | `to_lib` 对 `alpha_pnl/<name>`(**单文件**)用 `shutil.rmtree`——根 CLAUDE.md 明文警告的 Errno 20 反模式;restage 后 re-archive(pnl 保留)必踩 | `ops/services/check/check.py:231-233` |
| 🔴 | `run` 不在 `WRITE_COMMANDS`,但它改写 alpha_src XML(`run.py:24`)+ mkdir + gsim 写 alpha_pnl → JFS 集中运维模型下非 root 直接 EACCES,不会自动提权 | `ops/infra/sudo.py:31` |
| 🔴 | check 在 `CheckerPipeline.__init__` 时、**factor_lock 之外**改写全部 staging 因子的 XML:另一台机器再跑 `ops check` 会改到正在被检的因子 | `ops/services/check/check.py:74-76` |
| 🔴 | worker 在发出首个 stage_start 前死亡(如 PG 不可达抛 OperationalError)→ `ops check` **永久挂起**(只 catch FactorLocked) | `ops/services/check/check.py:338-342` + `ops/utils/live_table.py:185-193` |
| 🔴 | 归档 XML 写死退役路径:`Alpha @module = /mnt/storage/alphalib/...`、pnlDir/dumpAlphaDir 在 `/tmp/alphalib`(带 TODO) | `ops/services/check/xml_prepare.py:81-83` |
| 🔴 | **密钥入库**:活的 S3 key + 公网可路由 endpoint(明文 HTTP)提交在 `config.prod-legacy.yaml:118-121`;Feishu APP_ID/APP_SECRET 硬编码在 `feishu_send.py:124-125`。**建议轮换这两组凭据**,不只是移出代码 | 见左 |
| 🟠 | correlation 取 `corrs[-1]` 当最大相关,但多池 bcorr 输出只是顺序拼接、非全局有序 → 可能选错最大相关、放过真实超阈值因子 | `ops/services/check/checker/correlation_checker.py:94-99` |
| 🟠 | reject 路径先删 staging 再 transition:中间崩溃留下 CHECKING 卡死因子,无自愈路径 | `ops/services/check/check.py:460` |
| 🟠 | 归档窗口:state 先 ACTIVE 再做三次 move,中途崩溃 = ACTIVE 但产物不全 | `ops/services/check/check.py:217-234` |
| 🟠 | 所有命令 exit code 永远 0(服务层无 `sys.exit`)→ cron/脚本无法判断失败 | `ops/main.py:61`(全服务层 grep 确认) |
| 🟠 | `xml_prepare` 所有 stage 函数裸 `except Exception` 吞错:save_xml 失败(EACCES 等)后静默用错误配置跑检查 | `ops/services/check/xml_prepare.py:29-86` |
| 🟠 | gsim 超时(config.timeout=1800s ≈ 长回测耗时)抛 TimeoutExpired 未被 Runner 转成 BacktestError → 被归类为 unexpected error | `ops/infra/gsim/runner.py:120-129` |
| 🟠 | 硬编码回测日期正在过期:validate/checkbias 固定 2024-12,长回测 end=20251231(今天 2026-07) | `ops/services/check/xml_prepare.py:31-54` |
| 🟠 | checkbias AST 注入盲区:`__init__` 里只认单目标 `ast.Assign`(注解赋值/多目标/非 init 赋值逃逸);firewall 对 delay=0 3D 数据 slice 索引直接放行 | `checkbias_checker.py:61-70`、`firewall.py:89-91` |
| 🟠 | `ops pack --factor <name>` 对不存在/空的 dump 目录静默写出两个 ~171MB 全 NaN 矩阵 | `ops/services/pack/pack.py:60-62,137` |
| 🟠 | sync pull 的 SUBMITTED 安全过滤因大小写不匹配是死代码(比较 `"SUBMITTED"`,存储是小写) | `ops/services/sync/sync.py:317` |

### 1.3 测试与 CI 现状

- **HEAD 上 fast suite 红:10 failed / 9 passed / 64 skipped**(`uv run pytest -m "not slow"`)。
  原因:三表重构后测试未跟上——FactorRecord 已无 author(`tests/test_pure.py:132` 等)、
  PG fixture 传已删除的 `library_id`(`tests/conftest.py:90`,TypeError)、rm 测试断言僵尸
  derived 层(`tests/test_lifecycle_cmds.py:170`)。
- PG 组靠 ops_test 不可达时的 auto-skip 掩盖损坏;隔离模型(per-test library_id 分区)
  在三表 schema 下已不成立(`tests/conftest.py:71-80`)。
- **零测试覆盖**:sync(546 行)、pack、combo、新 PG 三 store(info/snapshot/query 的
  SQL 下推)、sudo 提权、advisory lock 跨机语义。
- **无 CI**(`.github/workflows` 不存在)——红 suite 落在分支上无人知晓。

---

## 二、架构评估

**结论:不是屎山,骨架比作者自评好。** 4 层分层(cli→services→core+infra)真实执行:
CLI 层几乎零逻辑;"破坏性操作 opt-in"原则贯彻一致;每个 service 带 CLAUDE.md 且大体与
代码对应——同类内部工具里算高水位。旧 plans.md 里"Architecture Refactor (Not Started)"
的目标结构实际已全部落地(该条目本身忘了标记完成)。

真正的问题是三个**系统性习惯**,不是结构:

### 2.1 "迁移到一半"是常态,旧半场从不拆除

当前同时存在:PG(活)/ redis state(死代码,文档称回退)/ json state(对旧数据格式炸)/
derived 僵尸层(仍在热路径且已坏,§1.1 P0-4)/ sync+S3(legacy)/ `ops health`(计划删
但还注册,`--fix` 写进没人读的僵尸表 `factor_derived`,`ops/services/list/metrics.py:47`)。

**每一层"保留作回退"的旧代码实际都已不能用,但它们的存在制造了有退路的错觉。**
最该还的债是拆除,不是兼容:PG 已是唯一后端,让代码诚实反映这一点。

### 2.2 静默吞错是房间里的大象

本次确认的严重 bug 中——快照陈旧(P0-1)、索引缓存失效(P0-4)、xml_prepare 失败、
`_load_index_from_store`、sync 大小写死代码——**全部**由 `except Exception: pass/log`
把"会报错的小问题"演化成"无声的数据腐烂"。这是风格问题:建议全库审一遍裸
`except Exception`,原则改为"要么能自愈,要么响亮地死"。

### 2.3 契约靠默契,不靠类型/单一真相源

- argparse Namespace 裸穿到 service 层(CLI 改参数名 → 运行时才炸);
- ABC 与实现签名漂移(§1.1 表末两行);
- `_SORTABLE_KEYS`、CLI choices、DB CHECK 三处各自维护 → DECAYING/RETIRED 幽灵状态、
  `--sort-by delay` 静默忽略、`!=` 操作符解析接受但无实现(过滤器**静默变宽**,危险)。

### 2.4 其它结构性观察

- `ops list` 的因子集仍由 `scanner.scan()` 扫盘界定(memory 已记录),抵消 PG 迁移核心
  收益;索引缓存坏掉后代价变成每次 25s;
- `pyproject.toml` 声明从未 import 的依赖:mlflow(重)、zstandard、colorama、scp、
  PyPI `argparse` 1.4(Python2 时代 backport,可能遮蔽标准库);
- `ops/utils/utils.py` 大部分是死代码(Remote/Local/Gsim 复制品),但 9 个 CLI 模块为了
  一个 3 行的 LowerAction 每次付 paramiko import 成本;
- 文档反而是做得最好的部分,但开始漂移:README 停在"6-stage/7 命令/JSON state"时代;
  根 CLAUDE.md 的 `ops list --author` flag 不存在;`config.yaml` 头部还自称"PoC 配置,
  不要拿来跑生产"——它就是生产默认。

**优先序建议:不要重构。先拆除死代码、把吞错改响、补 CI。结构本身不需要动。**

---

## 三、命令设计与因子生命周期

状态机(SUBMITTED→CHECKING→ACTIVE/REJECTED)简洁,命令分段清楚。核心问题一个,
一致性问题一批:

### 3.1 核心建模缺口

**「SUBMITTED(新提交)」和「SUBMITTED(曾入库、被 restage 召回)」是两个不同状态被压成
一个。** cancel 的前提假设"SUBMITTED 因子按定义没有产物"(`cancel.py:14`)只对前者成立;
后者有 pnl/dump/feature,且源码唯一副本在 staging。由此派生:

- cancel 删唯一源码(§1.2);
- 快照残留(§1.1 P0-1);
- approve/cancel TOCTOU 可把它翻成 ACTIVE(transition 无 from-status 守卫)。

**建议引入不变量而非新状态:`entered_at 非空 = 曾入库`。** cancel 对曾入库因子要么拒绝,
要么把 src 搬回 alpha_src 而不是删除。

### 3.2 TOCTOU 家族(cancel / approve / restage / clear 同构)

目标在交互确认**前** resolve;锁内不 re-fetch;transition 无条件更新。确认提示挂着的
几分钟里状态可任意变(例:确认期间因子被 check 通过转 ACTIVE,cancel 仍按旧 rec 硬删)。
**修一处模式四处受益:锁内重取 + `transition(from_status=...)` 条件更新(CAS)。**

### 3.3 快照语义需要两条补充规则才自洽

1. 凡离开 ACTIVE(restage / overwrite)必须删快照,re-archive 时重写——否则"不可变
   快照"退化为"第一次的快照";
2. approve 进 ACTIVE 必须补写快照(可标记来源 approved)——否则反查体系对豁免因子失明。

### 3.4 一致性小账本(单独都小,合起来是"体感屎"的主要来源)

| 类别 | 具体 |
|---|---|
| 短旗含义漂移 | `-s`:list/status/restage/pack=status,submit/run=start-date;`-f` 三种含义;`-r` 两种 |
| dest 不一致 | status 的 `-u` → `args.author`,其它 9 个命令 → `args.user` |
| 大小写归一化 | 9 个命令 `-u` 走 LowerAction,run/pack 不走(与 info_store 里小写 author 匹配失败) |
| 互斥规则 | approve/cancel/clear:name+`-u` 报错;restage:静默忽略 `-u` —— 应统一报错 |
| 确认缺失 | `backfill` 全库写 info+state,无确认无 `-y`,与"批量默认 dry-run"原则冲突 |
| 帮助文本教错 | `--filter-by` help 示例 `table=`(非法键,合法是 `tables=`);epilog 写 `--sort`(实为 `--sort-by`) |
| 静默 no-op | `ops check --retry` 解析但从未读取;`ops run --pack` 同;`--sort-by delay` 接受后忽略 |
| 静默变宽 | `--filter-by "shrp!=1"` 的 `!=` 解析通过但无实现分支 → 条件被忽略,结果比预期多 |

---

## 四、行动清单(按序)

1. **修 P0-1**(restage/overwrite 删 snapshot 或改 upsert)+ **P0-6**(approve 补快照)
   —— 数据正确性;
2. **修裸 restage dead guard + cancel 源码删除路径**(§3.1 不变量)—— 两条最容易误操作的
   数据丢失路径;
3. **轮换 S3 与 Feishu 凭据**,从 repo 移除;
4. **决断回退策略**:修好 prod-legacy 路径,或删 redis/json store + 回退文档;
   同步 `01-schema.sql` 到三表 + 修 `_init_schema` FK 顺序;
5. **修测试 + 加最简 CI**(`pytest -m "not slow"`),防止红 suite 再次无声落分支;
6. **修索引缓存**(get_meta 适配新 derived_meta),或按原计划改
   `factor_state.status != 'submitted'` 纯 PG 判据、删 scan;
7. **全库清一遍裸 `except Exception`**;
8. **拆僵尸**:derived 层、health、sync、utils.py 死代码、未用依赖。

---

*报告由 Claude Code 多 agent 审查生成;发现均经对抗验证或人工读码复核,引用行号以
HEAD `d36dead` 为准。*


# 第二部分 · 代码工艺:优雅与工程最佳实践

**定位**:本文与 第一部分(bug 报告)互补——那份讲"哪里会坏",
本文讲"怎么写得优雅"。不重复 bug,只谈软件工程最佳实践。

**方法**:7 个工艺视角并行审查(Python 惯用法 / 抽象与 API / 重复与 DRY / 错误处理
纪律 / 资源生命周期 / 一致性约定 / 测试工艺),每个视角的事实性断言(行号、计数)
由独立 agent 复核,少数计数误差已修正。所有断言以 HEAD `7a764f8` 为准。

---

## 〇、总评

**这是一个"一人两方言"的代码库**(git:51 个 commit 里 50 个同一作者)。2026-07 世代
的代码(rm/restage/approve/cancel/clear、PG 三 store)有一套真实存在且相当好的 house
style:模块 docstring 以 `ops rm — 彻底删除一个因子` 开头、`run_<cmd>(args)` 入口、
`_verb_noun` 私有助手、banner/confirm/lock-loop 脚手架、每目录 CLAUDE.md。旧世代
(check 流水线、run、utils/、xml_prepare)则是 star import、emoji 打印、硬编码路径、
一个平行的死 Gsim runner。

**惯用法基线高于平均**:PEP 604 union 为主、领域模型全用 dataclass、pathlib 几乎
全覆盖(仅 3 个文件 9 处 `os.path`)、全库只有 1 个裸 `except:`、零可变默认参数、
513 处 f-string 且零 `%`/`.format` 混用。

**系统性短板只有一个:好方言没有任何工具在守。** `pyrightconfig.json` 躺在仓库里但
pyright 不在 dev 依赖、当前 38 个报错(其中包含真实 schema 漂移);没有 ruff/black
配置(有个游离的 `.ruff_cache` 说明手跑过但从未采纳),6 条候选规则试跑 219 个发现
(105 个可自动修复);多处"单一真相源"契约靠注释维持("三处不能 drift"、"Must match
keys used in _run_one_locked")。**增长模式是 clone-and-edit**:五个生命周期命令是
同一模板的五份手抄,四个 PG store 各自手卷相同的 pool/DDL/时间戳管道。

下面按杠杆从高到低组织成 9 条工作主线(A–I)+ 一组风格守则(J)。同一问题被多个
视角命中的只写一次。

---

## A. 工具链先行(最高杠杆,一次搞定)

**现状**:`pyrightconfig.json` 存在但无人执行 → 38 错误,其中有**真实漂移**:
`redis_store.py:41` 仍写 `rec.author`(字段已删,pyright 直接报
`Cannot access attribute "author"`)、`store/base.py:14` ABC 与 PG 实现签名不符
(reportIncompatibleMethodOverride)——这两条正是 bug 报告里"回退后端已死"的问题,
**一个被执行的类型检查器本可以在提交当天就拦住它们**。

**动作**:
1. `uv add --group dev ruff pyright`;
2. `[tool.ruff]` target-version=py310,line-length=100;`lint.select = ["F","E7","I","UP006","UP007","UP035","UP045","B006","B008"]`;
   第一阶段 `ruff check --fix`(I001+F401 共 105 个自动修复),第二阶段烧掉 F403/F405(见 B1);
3. 修完 38 个 pyright 错误后,把 `uv run ruff check ops && uv run pyright && uv run pytest -m "not slow"`
   接进 CI(bug 报告行动清单第 5 条的同一个 CI);
4. 跳过 pydocstyle/D 系规则——它们会和双语 docstring 约定打架(见 J10)。

---

## B. 消灭"靠注释维持的契约"

### B1. 31 处 star import,和一条靠加载顺序活着的注释

check.py 一个文件堆了 7 个 `import *`,并被迫写下这条注释(check.py:30-32):
> "Imported AFTER the `results.*` star imports so its Status doesn't get shadowed
> by the stub Status enum in core/alpha/results/base.py."

**import 顺序是承重结构**——这是 star import 文化的极限形态。全库 31 处(ruff F403),
82 处经由 star import 解析的名字(F405)。printer.py 没有 `__all__`,所以
`from ops.utils.printer import *` 顺带把 `shutil`、`Console`、`_console` 注进了
check.py 和 run.py 的命名空间。

**动作**:全部改显式导入(checker 们只需要 `from .base import Checker, CheckFail,
CheckSkip`;check.py 只需要 ~6 个 printer 名字);包级 `__init__.py` 用显式名 +
`__all__`;删掉 results/base.py 里空的 stub `Status` 枚举(空枚举没人能用)——
遮蔽问题随之消失,那条注释可以删了。

### B2. stage 身份在 ≥5 处各自维护 → 一张 Stage 表

现状:`STAGES` 元组(check.py:49,带注释"Must match keys used in _run_one_locked")、
`_RETRYABLE_STAGES`(:45)、`_LATE_STAGES`(藏在 on_reject 里,:264)、12 个单行异常
子类各自硬编码 stage 字符串(`ValidateFail` → `super().__init__("validate", ...)`)、
`_run_one_locked` 里六段复制的 4 行块(:364-400)。没有任何代码 catch 具体子类
(grep 证实)——12 个子类是纯开销。

**动作**(三个视角同一结论,测试视角还指出注入 seam 也会因此变干净):

```python
@dataclass(frozen=True)
class Stage:
    name: str
    prepare: Callable[[AlphaMetadata], None]
    make_checker: Callable[[Config], Checker]
    retryable: bool = False              # 取代 _RETRYABLE_STAGES
    keep_artifacts_on_fail: bool = False # 取代 _LATE_STAGES

PIPELINE: tuple[Stage, ...] = (
    Stage("validate", prepare_for_validate, ValidateChecker, retryable=True),
    Stage("checkbias", prepare_for_checkbias, CheckbiasChecker),
    ...
)
STAGES = tuple(s.name for s in PIPELINE)   # 派生,不再手抄
```

`_run_one_locked` 变成一个循环;路由读 `e.stage.retryable`;on_reject 读
`stage.keep_artifacts_on_fail`;12 个异常子类删除,直接 `raise CheckFail(stage, msg)`;
测试的 fake-checker 注入走 `{name: checker}` 字典,契约不再是"六个平行属性名"。

### B3. 时间戳:13 份拷贝 + 一次跨后端私有导入

`def _now() -> str: datetime.now().isoformat(timespec="seconds")` 在
json/pg/redis 三个 store 里逐字定义三次;同一表达式在 check.py 内联 8 次
(305/314/347/407/428/445/461/481)、submit.py:83、backfill.py:69;**approve.py:20
从 json_store 私有导入 `_now`——而生产跑的是 PG 后端**。`_ts_in`/`_ts_out` 在
state 和 snapshot 两个 pg_store 里逐字重复 26 行,snapshot 的 docstring 写着
"与 state_store 一致"——一致性靠注释。

**动作**:`ops/utils/clock.py` 提供 `now_iso()`;`ops/infra/pg.py`(见 D)收编
`ts_in`/`ts_out`,把不变量写进一处 docstring:"时间戳为 naive 本地 ISO 秒;PG 写入
时打本地 tz、读出时剥离"。bug 报告里 info store 的 8 小时偏移,根因就是这个约定
没有单一定义点,新 store 抄漏了。

---

## C. 一个批量命令骨架(收益最大的单笔重构)

restage/approve/cancel/clear 四个 `run_*` 是同一骨架的四份手抄(rm 是近亲),
757 行里约 200 行近乎逐字重复:

- `_resolve_targets` ×4:同一段 `info_store.list(author=...) 取 name 集合 →
  store.list() → 取交集 → sort` 舞步(cancel 和 approve 只差资格谓词);
- `_print_plan` ×4:同样的 N+1 `info_store.get(name)` 查 author;
- 确认块 ×4:`ans = input(f"  确认 {verb} {n} 个因子? [y/N] ")...` 6 行;
- lock 循环 ×4(approve.py:147-152 与 restage.py:214-219 逐字节相同,另两处
  近乎相同);
- 复制漂移已经发生:**restage 缺了其它三个命令都有的 name+`-u` 互斥检查**——
  clone-and-edit 的经典病征。

**动作**:`ops/services/_batch.py`,用小的可组合函数而非模板方法类:

```python
@dataclass
class BatchResult:
    done: list[str]
    skipped: list[tuple[str, str]]   # (name, reason)
    failed: list[tuple[str, str]]

def confirm_or_abort(verb: str, n: int, yes: bool) -> bool: ...

def apply_locked(targets, config, action: Callable[[T], None], *,
                 name_of=attrgetter("name"), verb: str) -> BatchResult:
    # 独占:with factor_lock(...);FactorLocked → warn+计数;
    # Exception → printer.error 且 logger.exception(强制,见 E)
```

每个命令只保留资格谓词和 `_x_one` 动作。**一石多鸟**:bug 报告里的 TOCTOU 修复
(锁内重取 + from-status CAS)只需要在这一处落地;8 个写命令从不 import loguru 的
问题(失败零诊断痕迹)在 helper 里一次解决;`run_*` 返回 `BatchResult` 后,测试
可以断言 `res.skipped == [("AlphaX", "status=active")]` 而不是"跑完后状态没变"
这种把'正确拒绝'和'静默没做'混为一谈的代理断言;exit code(见 E)也从这里出。

---

## D. 存储层:一个门面、一个池、一套约定

### D1. FactorRepository 门面

每个写命令手工构造 2-3 个 store 并手工 join;`query_factors` 是第四份手卷 join,
自带 TODO 承认该是一条 SQL。**没有任何对象拥有"一个因子"这个聚合**。

**动作**:`ops/infra/repository.py`:

```python
class FactorRepository:            # 拥有唯一的 ConnectionPool
    def get(self, name) -> FactorView | None: ...       # info+state+snapshot
    def find(self, *, author=None, status=None, field=None, ...) -> list[FactorView]:
        ...                        # 真正的三表 LEFT JOIN,退役 query_factors
    def register(self, info, record): ...               # submit 双表写,一个事务
    def transition(self, name, to, *, expect: FactorStatus | None = None): ...
    def delete(self, name) -> bool: ...                  # info 删,级联
```

三个 ABC 降级为内部 row-gateway;service 层只见 Repository。bug 报告里
"info+state 写无事务"、"list -n 下推错乱"、"批量交集逻辑"都在这一层一次修对。

### D2. 每进程一个池(注册表),DDL 滚出构造函数

四个 PG store 都在 `__init__` 里 `ConnectionPool(open=True)` + `CREATE TABLE`;
~25 个 `default_*_store()` 调用点,全库 **0 个 `.close()`**;max_size 在 4 与 10
之间随意漂移。check 的 archive 路径每因子在 fork 出的 worker 里造 3-4 个池。

```python
# ops/infra/pg.py
@lru_cache(maxsize=None)
def get_pool(conninfo: str) -> ConnectionPool: ...
# fork 守卫:os.getpid() 变了就清缓存(父进程的池在子进程不可用)

def ensure_schema(pool, ddl: str) -> None: ...   # 每 pool+ddl 只跑一次
```

`default_*_store` 工厂内部改走注册表,调用点一行不改。三个 store 因共享 conninfo
而自然共享一个池。

### D3. 返回值约定统一

同一动词三种契约:`StateStore.delete -> bool`("Returns True if it existed")、
`InfoStore.delete -> None`、`SnapshotStore.delete -> None`。**rm.py:82 已经被咬**:
`if default_info_store(config).delete(name):` 永远为假,确认信息永不打印。

**规则**:查询(`get`/`list`)返回 None/空;变更统一 `-> bool` 或抛
`ops/infra/errors.py` 里的类型化异常(`FactorNotFound`、`SnapshotAlreadyExists`)。
调用方 catch 具体异常,不再 catch Exception。

### D4. 两个同名 FactorInfo

`core/library.py:18`(扫盘产物:src_path/has_pnl/dump_days)与 `infra/info/base.py:7`
(factor_info 表行:author/discovery_method)同名不同物,并在 list.py:105-135 的
同一个函数里相遇。**把扫盘产物改名 `ScannedFactor`**,其 `author` 字段改名
`author_guess`——它来自目录名正则(library.py:37),不是权威身份,名字应当说出这一点。

---

## E. 错误处理:从"两个代码库"到一份成文政策

**诊断**:check 流水线内部的错误边界设计得**真心不错**——CheckFail/CheckSkip 携带
stage,check.py:423-488 把四种结局(skip→SUBMITTED、可重试失败→SUBMITTED、质量失败
→REJECTED、意外→logger.exception+回退)路由得清清楚楚,printer vs loguru 双通道在
log.py 里有成文分工。**边界之外全靠即兴**:63 处 `except Exception` + 1 处裸
`except:`,其中 ~27 处无日志静默吞掉(旗舰:xml_prepare 五个 prepare_* 全部
`except Exception: ...`);8 个写命令 service 从不 import loguru——而 check 的
汇总语却告诉用户"完整失败原因见 ~/.cache/ops/logs/ops.log"。异常谱系四个无关根
(CheckFail/CheckSkip、BacktestError 用 `.stage` 鸭子类型模仿而不继承、ScriptError、
FactorLocked(RuntimeError));所有 `run_*` 返回 None,`ops sync push` 打印
"✘ 失败: N" 然后 exit 0——对 cron 和脚本全盲。全库没有任何针对 PG/JFS 瞬断的重试
(只有 Redis 的 CAS 循环),一次 PG 抖动就把 30 分钟的 check 扔进 catch-all。

**成文政策**(写进根 CLAUDE.md,评审时执行):

1. **异常谱系**:`ops/errors.py` — `OpsError` 根;`UserError`(输入错/因子不存在:
   只打印消息,exit 2);`InfraError`(gsim/PG/JFS 环境:logger.exception + 一行
   消息,exit 3)。BacktestError/ScriptError 归于 InfraError,FactorLocked、
   CheckFail/CheckSkip 归于 OpsError。main() 建立唯一错误边界与 exit-code 契约;
   批量命令按 BatchResult.failed 数量决定 exit code。
2. **`except Exception` 只允许出现在三个高度**:(a) 进程边界(main);(b) 批量
   per-item 边界(C 的 helper,强制记日志);(c) 声明过的降级路径——必须
   logger.warning/exception 且在 docstring 写明降级契约("解析失败时返回 []")。
   清理/finally 路径 catch `OSError`,不 catch Exception。
   **`except Exception: pass` / `...` 一律禁止**;确属故意的抑制用
   `contextlib.suppress(OSError)` 把意图写在字面上。
3. **重试**:`ops/infra/retry.py` 一个 `retry_transient(attempts=3, on=(OperationalError,
   PoolTimeout))` 装饰器,**只**应用在 PG store 的连接咽喉点(不在调用点、不碰
   gsim 子进程——30 分钟的回测不是"瞬态")。
4. **双通道纪律**:printer 只准 ops/cli 与 ops/services 命令模块导入;ops/infra 与
   ops/core 只说异常 + loguru。现行违例:feishu_send.py(infra)import printer 且
   `sys.exit(1)` ×4;utils.Gsim 在工具类里打 "✅/❌"。
5. **删副作用 `__repr__`**:`CheckFail.__repr__` 里有一个 `print(self.args[0])`
   (checker/base.py:15)——任何 f-string/日志 repr 它都会向 stdout 喷字;
   BacktestError 的 `__repr__` 逻辑与之相反。两个都删,默认 repr 严格更好。

---

## F. 资源生命周期:把老代码已会的惯用法提取出来

**有趣的事实**:JSON 时代的代码(json_store、etag_cache、merge)是全库工艺最好的
——fcntl 守卫的读改写、tmp+fsync+os.replace、陈旧 tmp 清理,三样全对。但这些惯用法
是**按模块复制**而不是提取,于是最新的代码(PG store、report writer、XML 改写)
反而退化回裸写。

1. **`ops/utils/fs.py` — atomic_write 一处定义**:tmp+mkstemp+fsync+replace 现有
   4 份 JSON 实现(其中 derived/json_store.py:82 **悄悄丢了** 其它三份都有的 fsync)
   + pack 的 numpy 变体;~12 个裸写点(report.py、meta.json 保存、XML 保存)逐个换成
   `atomic_write_text/json/bytes`。
2. **`edit_xml` 上下文管理器**:`xmltodict.parse → 改 dict → unparse(pretty=True,
   full_document=False) → write` 在 6 个文件 8 个 unparse 点上有三种序列化实现。
   `with edit_xml(path) as cfg: cfg["gsim"]["Universe"]["@startdate"] = ...` 一统;
   xml_prepare 四个克隆 prepare_* 顺势变成"stage → XML 覆盖表"的数据(和 B2 的
   Stage 表合流),吞异常的 try 块只在一处决定一次。
3. **子进程脊柱**:runner.py 四个方法三种失败契约(run_bcorr 连 TimeoutExpired 都
   吞掉返回 None);统一为一个 `_run(cmd, timeout, ...)`:`start_new_session=True` +
   超时 `os.killpg(SIGKILL)` 收割整棵 gsim 进程树,超时/非零退出翻译成类型化
   InfraError。utils.py 里那个硬编码 `/usr/local/gsim/.venv/bin/python` 的死
   Gsim 类整个删除。
4. **进程池 worker 用模块级函数**:check.py `pool.submit(self.run_one, ...)` 每个
   任务 pickle 整个 CheckerPipeline(含全部 metadatas 和六个 checker);pack.py
   已经示范了正确写法(模块级函数 + 显式参数 + 每进程缓存的 config),照抄即可。
5. **memmap 生命周期**:pack 三处用 `del mm` 引用计数技巧;一个 `open_memmap()`
   上下文管理器收编(顺手修掉 `mms: dict[tuple[str], np.memmap]` 这个错误注解)。

---

## G. Config 治理

- **47 个公有属性**在 `__init__` 里命令式赋值(config.py:50-164),跨 7 个领域;
  死重量:`max_workers`/`thres`/`recycle`/`pnl_pool_path`/`sync_remote` 零使用。
- **它自己的调用方都不信任它**:15 处 `getattr(config, "state_backend", None)` 式
  防御访问(lock.py:103、store/__init__.py:19、sudo.py:103-109 等)——而这些属性
  **全部无条件被设置**,默认值是死代码,唯一效果是把拼写错误变成静默 None。
- **`Config.load` 有 22 个调用点**:一次 `ops health --fix` 解析 config.yaml 5+ 次
  (main→sudo ×2→scanner 重载→refresh_* 再载)。
- **魔法数没有名字**:validate 窗口 "20241201" 在两个函数里各写一遍;长回测窗口
  xml_prepare 写 20150101-20251231,cli/run.py 默认却是 20100101-20251231——两处
  各自硬编码,已经漂移。

**动作**:Config 改 `@dataclass(frozen=True)` + `from_yaml()` classmethod;按域切片
(`ComplianceCfg`、`CorrelationCfg`、`StateBackendCfg`…),构造时校验(缺阈值在
`ops` 启动时带着 yaml 路径报错——Ousterhout 的 define errors out of existence);
checker 收自己的切片而不是整个神对象;house rule:**`run_<cmd>` 做唯一一次
`Config.load`,往下全部传 `config: Config`,不传 config_path**;回测窗口进
`checker.windows` 配置块或至少一个常量头。

---

## H. CLI 一致性套件

`ops/cli/common.py`:`add_config_arg`(现状 18 处逐字复制)、`add_yes_arg`(5 处)、
`add_user_arg`(9 处,统一 LowerAction + dest='user',顺带修掉 status 命令独有的
dest='author' 和 run/pack 漏掉的小写归一)、`status_choices()`(从 FactorStatus 派生,
删掉 pack.py:28 手抄的字符串列表——它还包含 DB 已拒收的 decaying/retired)、
`add_factor_name_arg`(4 处)。LowerAction 从 utils/utils.py 搬来——**9 个 CLI 模块
为一个 3 行的 argparse Action 每次启动付一次 paramiko import**。

补上 16 个子命令里 8 个缺失的 `help=`(`ops --help` 现在一半是空白)。短旗词汇表
写进 ops/cli/CLAUDE.md:`-s`=--status、日期只用长旗 `--start/--end`、`-f`=--factor-name。

**明确不做的**(假 DRY,见 J 末尾):不要中心化 `-s`/`-f` 的声明——它们在不同命令
里语义不同,共享 helper 会把历史包袱固化成 API。

---

## I. 测试工艺:补上金字塔的中间层

现有底子好:conftest 有成文隔离模型、工厂 fixture(make_factor/make_args)、
fake-checker 是真正的 DI seam、e2e 用确定性假因子按 stage 引爆。**塌的是中间层**:

1. **info/snapshot 无 JSON 实现 → 所有 service 测试都要真 PG**。补
   `JsonInfoStore`/`JsonSnapshotStore`(镜像 JsonStateStore,复用 atomic-write;
   test-only 可免 fcntl),挂在现有 `state.backend: json` 开关后;conftest 分裂出
   `test_config_json`(纯 tmp_path,submit/cancel/restage/routing 全部脱离 PG)。
2. **PG 隔离改用 per-test schema**(现行 library_id 分区的列已被三表迁移删掉):
   `CREATE SCHEMA "t_<uuid>"` + conninfo 带 `options='-c search_path=t_<uuid>'`,
   store 的 `CREATE TABLE IF NOT EXISTS` 自动落进该 schema,teardown 一句
   `DROP SCHEMA ... CASCADE`。**零 store 代码改动**,天然并行安全。
3. **删掉两个脚本式测试文件**(test_all_services.py / test_end_to_end.py:
   print 当断言、硬编码生产 conninfo、绕过全部 fixture、无 pg marker 所以无
   auto-skip),场景改写成正经契约测试:`test_info_store_pg.py`、
   `test_snapshot_store_pg.py`(insert-only 不变性、list 下推)、
   `test_query_factors.py`、一个级联删除测试。
4. **共享构造器**:`_store(config)` 在 6 个文件里逐字定义 → fixture;
   `FactorRecord(...)` 手工构造 29 次(单文件最多 15 次)→ `make_record(name, **kw)`
   + `seed_factor(store, config, name, status, artifacts=...)`。
5. **参数化矩阵**:reject 路由只测了 compliance 和 checkbias 各一——
   `@pytest.mark.parametrize("stage", ["compliance","correlation"])`(late)与
   `["checkbias","checkpoint"]`(early)补全;cancel 的 (status, force, survives)
   五元组一张表。
6. **后端契约套件**:json 与 PG 的 state store 测试是两份手抄(delete/list_filters
   逐字节相同)→ 一个 `state_store_any` fixture 参数化两后端,同一契约跑两遍。
7. **e2e**:`_seed_pool_competitor` 是个 body 为 `pass` 的死函数,真实种池逻辑在两个
   测试里内联重复、各付一次全量回测 → session-scoped fixture 缓存竞品 pnl;
   correlation 测试的 `assert status in (REJECTED, SUBMITTED)` 二选一断言收紧为
   确定的 REJECTED。

---

## J. 风格守则(13 条,浓缩)

1. **无 star import**(B1;ruff F403/F405 守门)。
2. **ruff + pyright 进 CI**(A)。
3. **批量命令走 `_batch.py` 骨架**(C)。
4. **Config 每进程加载一次**,向下传对象不传路径(G)。
5. **CLI 声明套件 + 短旗词汇表**(H)。
6. **一个 Console、一套状态色板**:`Console(width=...)` 逐字复制 5 处;
   `_STATUS_STYLE` 定义了**两份且颜色互相矛盾**(list.py:21 SUBMITTED=yellow,
   status.py:16 SUBMITTED=green)——"REJECTED 是什么颜色"应该恰好在一个文件里有答案
   (printer.py 或 core/state.py 旁的纯数据)。
7. **一个时间真相源**(B3)。
8. **三个 store ABC 同动词同契约**(D3),PG 管道进 `infra/pg.py`(D2)。
9. **service `__init__.py` 统一一种写法**:现存四种风格(相对+__all__ / 绝对+__all__ /
   裸 star / 空文件);统一为 `from .<mod> import run_<cmd>` + `__all__`。
10. **双语 docstring 是模式不是混乱,把它写成规则**:现状 23 中文 / 17 英文 / 84 缺失,
    分裂是干净的时间分层——2026-07 世代全部以 `ops rm — 彻底删除一个因子` 式中文
    开头。规则:命令模块与 infra store 必须有模块 docstring;首行
    `ops <cmd> — <一句话语义>`;运维理由/语义/变更记录用中文(团队的细微差别在
    中文里),机械的参数/返回契约可用英文;**重构时永不把既有中文理由翻译成英文**。
    84 个缺失的补上。
11. **utils/ 不是杂物抽屉**:一个能力恰好一个家。删 `Gsim`(硬编码路径的死 runner)、
    `Remote`(stub)、`debug()`(无限循环)、空的 exception 包;LowerAction 归 CLI;
    `check_path_exists` 里的 `sys.exit` 改为抛 FileNotFoundError——工具函数报告错误,
    入口点决定进程命运。改完后 `grep -rn 'utils.utils'` 应为零。
12. **给数字起名字**:出现两次的字面量、或运维总有一天要改的字面量,必须有名字
    (G 的窗口配置)。
13. **入口点统一 `run_<subcommand>(args) -> None`**:16 个里 15 个已符合,run 子命令的
    `run_factors` 改名 `run_run`(机械性比美观值钱);8 个缺失的 `-> None` 注解补齐。

### 假重复清单(长得像、但**不要**合并)

- **derived/ 层与 snapshot 层的管道相似** → 不要为它做任何 dedup,该层整体待删
  (bug 报告 §2.1),对僵尸做重构是负价值;
- **submit 与 check 的循环**只有表面纹理相似(submit 是 dropbox 扫描+回滚语义,
  check 是进程池+路由)→ 各留各的,最多共享 confirm/printer 小件;
- **cancel 与 clear 的目标解析**:state-record 在场/缺席之分是设计本身 → 只共享
  机械脚手架,永不合并解析逻辑;
- **`-s`/`-f` 短旗**:语义随命令不同,中心化声明会固化不一致而不是消除它。

---

## 动手顺序建议

| 步 | 内容 | 工作量 | 提前解锁 |
|---|---|---|---|
| 1 | A 工具链 + B1 star import + J6/J7 小件 | 1-2 天 | 此后所有改动有守门员 |
| 2 | E 错误政策(errors.py + main 边界 + exit code) | 1 天 | 批量骨架依赖 BatchResult |
| 3 | C 批量骨架(顺手落 TOCTOU 修复) | 1-2 天 | 五个命令瘦身,测试可断言 |
| 4 | D2 池注册表 + D3 返回值约定 | 1 天 | 修池泄漏 |
| 5 | B2 Stage 表 + F2 edit_xml(check 流水线内科手术) | 2-3 天 | check.py 从 594 行瘦身 |
| 6 | I 测试中间层(Json store + schema 隔离) | 2 天 | 此后重构有安全网 |
| 7 | D1 FactorRepository(最大单件,最后动) | 3-5 天 | 收编 query_factors/事务 |
| 8 | G Config、H CLI、J 其余 | 陆续 | 机械性,随手做 |

一句话总结:**这个库不缺品味——新世代代码证明作者知道好代码长什么样;缺的是把
品味变成机器可执行的规则(工具链、共享内核、成文政策),让旧世代赶上新世代,
让下一次"迁移到一半"不再产生两种方言。**

---

*七视角多 agent 审查 + 事实复核生成;行号以 HEAD `7a764f8` 为准。与
第一部分(bug 与架构)配套阅读。*


# 第三部分 · 架构演化:沉积层、多真相源与领域模型重建

**定位**:三部曲收官。第一部分 讲哪里会坏,第二部分
讲怎么写好,本文回答:**系统病在哪一类、该变成什么样、按什么顺序做减法**。

**方法**:5 个专项侦查并行全库扫描(沉积层 / 多真相源 / 分层依赖 / 退化器官 / 存储形状
泄漏),与前三轮已验证结果合并。所有 file:line 以 HEAD `f733db8` 为准。

**核心诊断一句话**:这不是"屎山",是**只做 Expand-Migrate、从不做 Contract 的演化模式**
在四次存储迁移后留下的地质剖面——每个概念平均 2~4 个平行世代共存,每个事实平均 2~9 个
真相源,分层只在目录上存在。

---

## 一、术语地图(这病叫什么)

| 层次 | 术语 | 出处 | 对应本库的现象 |
|---|---|---|---|
| 总病名 | **软件熵增 / Lehman 第二定律** | Lehman, 1974 | "复杂度必然上升,除非有专门工作去降低它"——减法必须刻意 |
| 反模式 | **Lava Flow(熔岩流)** | 《AntiPatterns》1998;Hadlow *The Lava Layer* | 每次迁移留一层凝固的旧代码"当回退",永不清除 |
| 机制 | **Expand–Contract(Parallel Change)只做了一半** | 业界标准迁移模式 | 每次重构停在 Expand+Migrate,Contract(删旧)永远排在"下次" |
| 领域建模 | **统一语言 / 聚合根缺失**(DDD);**Transaction Script + Row Gateway**(PoEAA) | Evans;Fowler | 12 个按存储介质命名的因子投影,没有"因子"正主;service 直接对表行编程 |
| 架构 | **依赖规则未被强制**(Clean Architecture);**fitness function**(演进式架构) | Martin;Ford et al. | 分层是目录税;import-linter 可让它变成 CI 契约 |
| 数据 | **SSOT(单一真相源)违反** | — | 22 个事实族多头权威(§三) |
| 根因 | **战术编程被 AI 加速** | Ousterhout | 战术产出快了一个量级,战术债堆积也快了一个量级 |

**关键谬误点名**:*未经验证的回退不是冗余,是负债*。本次扫描证实:**所有文档化的
"回退路径"已经全部是坏的**(prod-legacy 配置、redis/json 后端、template 配置、
health --fix、sync 的 metrics.json)——回退只作为风险存在,不作为保险存在。

---

## 二、病灶 G:平行世代沉积层(每个概念几代同堂)

格式:概念 | 世代 | 正主 | 旧代状态 | Contract 动作。

| # | 概念 | 共存世代 | 正主 | 旧代状态 | Contract |
|---|---|---|---|---|---|
| G1 | **state 存储** | json(`store/json_store.py`)→ redis(`redis_store.py`)→ PG(`pg_store.py`) | PG | **两个回退都已坏**:redis 读写已删字段 `rec.author`(redis_store.py:41,每次 put AttributeError);json 对旧格式炸(`from_dict` 严格 `cls(**d)`,state.py:59) | 删 json/redis store + 回退文档 |
| G2 | **派生数据** | derived 宽表(`infra/derived/`,~700 行)→ factor_snapshot 三表 | snapshot | 僵尸,且**靠"写者"续命而非读者**:唯一活联系是 LibraryScanner 索引缓存(library.py:115-175)——而它也是坏的(§三 S1);`upsert_bcorr`/`join_state`/`delete` 已零调用者;**`ops rm` 泄漏 factor_derived 行**(DerivedStore.delete 无人调) | 删整层 + 修 scanner(§五 Wave 2) |
| G3 | **跨机同步** | rclone(`sync_remote` 键,零读者)→ S3 sync(546+行 + `infra/s3.py`)→ JFS | JFS | sync 自我声明 legacy;**还在推送再上一个世代的文件**(`STATE_FILES` 含 metrics.json/datasources.json,其 merge 函数是"防旧调用者崩溃"的空壳,merge.py:153-163);s3.py 9 个公有方法 5 个零调用 | 删 sync 栈 + boto3/tqdm |
| G4 | **锁** | 单机 fcntl → PG advisory | PG advisory | fcntl 仅为两个已死后端存在;且 backend/conninfo 不满足时**静默降级**(lock.py:103-110) | 随 G1 删;降级改硬错 |
| G5 | **回测 runner** | `utils.Gsim`(硬编码 `/usr/local/gsim`、3600s)vs `infra/gsim/Runner`(配置驱动) | Runner | Gsim 零 importer,纯死 | 删,连带 Remote/Local |
| G6 | **因子集定义** | 扫盘 scan vs factor_state | PG | scan 是活的但代码自注 STOPGAP(list.py:231-235) | `status != 'submitted'` 判据,删 scan |
| G7 | **通知** | `notify/email.py`(294 行,~280 行在 `'''` 字符串里)+ 仓内 feishu_send.py(零 importer、硬编码密钥)vs 仓外 `/home/wbai/gsim-ops/jiance/feishu_send.py`(config 实际指向) | 仓外脚本(事实上) | **死代码劫持活配置**:email.py 是 `authors`/`summary_emails`/`feishu_script` 等必填 config 键的唯一"读者"(在字符串里) | 删 email.py + 仓内 feishu;config 键改可选或删 |
| G8 | **一次性迁移器** | `tools/state_migrate.py`(json→redis)→ `state_to_pg.py`(redis→PG)→ `derived_migrate.py`(→derived,目标本身已退役) | — | 三代全保留,任务均已完成 | 删三个;`bcorr/bcorr.cpp` 另定归属 |
| G9 | **缓存地层** | `~/.cache/ops/lib/<lib>/` 下五代文件:factor_state.json / derived.json / local_etag_cache.json / {index,metrics,datasources,bcorr}.json / locks/ | 各随其主 | `cache_path(legacy_hash=)` 兼容参数**零调用者**(cache.py:20-38 分支不可达) | 随各世代删;先删 legacy_hash |
| G10 | **配置文件** | config.yaml(头部自称"PoC 不要跑生产"——它就是生产)vs config.prod-legacy.yaml(**已不能运行大多数命令**,含明文 S3 密钥)vs template/config.yaml(缺 5 个必填键,`Config.load` 直接 KeyError) | config.yaml | 后两个一个是假保险一个是坏模板 | 删 prod-legacy(先轮换密钥);重生成 template;改 header |
| G11 | **测试世代** | pytest 纪律套件 vs 脚本式 test_all_services/test_end_to_end(print 断言、硬编码生产库) | 前者 | 后者绕过全部 fixture;另 test_derived_store_pg.py 钉死僵尸层行为;test_check_routing 断言的是 check **已不再走**的 derived 写路径(:68,111,248-260) | 删/改写脚本二件套;僵尸测试随 G2 删 |
| G12 | **报告目录** | 根 `report/`(2026-06 cc 审计残留)vs `docs/reports/` | docs/reports | 平行两个家 | 并入后删根目录 |
| G13 | **向前沉积**(为从未发货的未来建的) | `DECAYING/RETIRED` 状态(enum+CLI 有、DB CHECK 拒收、无 transition 产生)、`pack_one_incremental`(零调用者,CLI 接线"暂缓")、plans.md 里"flat+nested 双 CLI 注册"计划 | — | 接口先行、实现未至 | 删或接线;双注册计划自带 Contract 步骤再做 |

---

## 三、病灶 S:多真相源(22 个事实族,按危害排序)

| # | 事实 | 权威数 | 编码位置(代表) | 漂移证据 | 应属正主 |
|---|---|---|---|---|---|
| S1 | **derived_meta 表形状** | 3 | init SQL(带 library_id)/ migrate SQL(去掉)/ derived/pg_store.py:335(仍查 library_id) | **生产库上索引水位永久不可读写**,异常被吞 → 每次 list/health 全量扫盘 ~25s,静默 | 单一 DDL 源 |
| S2 | **factor_state/info/snapshot 表形状** | 4 | init SQL(**旧两表形状**)/ migrate SQL / store 内 DDL 字符串 / tests fixture(传已删的 library_id) | 空库 bootstrap 起旧世界;索引名漂移(`ix_fs_status` vs `idx_factor_state_status`,迁移库上双冗余索引);FK 建表顺序无人负责 | init SQL 从 store `_SCHEMA` 生成 |
| S3 | **FactorRecord 字段集** | 5 | dataclass(正主)/ pg `_COLS`(对齐)/ redis(旧字段)/ json(旧字段)/ sync merge 原始 dict | 两个回退后端因此全坏(=G1) | dataclass |
| S4 | **盘面布局**(alpha_src/name、pnl 单文件、feature 命名、meta.json 路径) | ~40+ 处散布 | src×9、pnl×9、dump×10、feature×9、staging×5、meta.json 6 处两种常量;sync 还有布局的反函数(sync.py:274-283) | "pnl 是单文件"restage/rm 遵守、**check.py:231 违反**(rmtree→Errno 20);xml_prepare.py:81 写死 `/mnt/storage` 旧根进归档 XML | `FactorPaths` 模块独家 |
| S5 | **"X 是不是因子"** | **6 种答案** | list=扫盘∩PG;status/rm/cancel=state 行;info=alpha_src 目录(info.py:29);clear=staging∩无 state;run=两目录并集;sync=json 键 | 同一因子 `status` 存在、`info` "not found" | Repository.exists() 一种语义 |
| S6 | **author** | 4 算法 | library.py:37 正则 vs parser.py:18-33 字符走查(**对 AlphaLLM010 类名字给不同答案**)vs meta.json vs factor_info | `-u wbai` 在 check(按 submitted_by!)/restage/clear 选出不同集合 | factor_info;推断只配叫 `author_guess` |
| S7 | **delay** | 4 | pack 读 meta.json 原始 dict 默认 1 / scanner 读 meta.json 默认 None / factor_snapshot.delay / XML `@delay` 默认 1(+factor_derived 第 5 份) | 默认值不一致;提交后改 XML 无人对账 | snapshot(入库定死),读侧统一 |
| S8 | **metric 键集与语义**(bcorr=abs 等) | 6 | derived/base `_SORTABLE_KEYS`(8 键)/ derived pg `_METRIC_EXPR` / derived json / snapshot pg(6 键)/ list.py(6 键)/ cli choices(**7 键含 delay**) | `--sort-by delay` 接受后静默无效;注释自认"三处不能 drift" | core 单一注册表,派生 Python getter+SQL expr+choices |
| S9 | **glob→LIKE 转换** | 3 | derived 版(正确:转义 %_、处理 ?、[] 跳过)/ snapshot 版(**裸 replace**)/ 内存 fnmatch | `tables=a_b*` 结果集与 fnmatch 语义不同,且 query_factors 丢行不可恢复 | derived 版提升共享 util |
| S10 | **FactorStatus 值集** | 5 | enum(6 值)/ DB CHECK ×2(4 值)/ pack CLI 手抄 6 串 / restage `_SUPPORTED_STATUSES` | DECAYING/RETIRED 幽灵态;**sync.py:317 比较大写 "SUBMITTED"**(存储为小写)→ 过滤器死 | enum 派生一切 |
| S11 | **stage 身份** | ≥5 组 | STAGES 元组 / _RETRYABLE / _LATE / 12 个异常子类字面量 / 12 处 emit 调用 / approve `_CORRELATION` | 靠注释"Must match"同步 | Stage 表(craft 文档 B2) |
| S12 | **时间戳格式** | 13+1 | `_now` ×3 定义 + 8 处内联 + approve 跨后端私有导入;merge.py 的 EPOCH 常量与字典序比较**静默依赖该格式** | info store 抄漏 tz 打标 → 8h 偏移 | `now_iso()` + pg.ts_in/out |
| S13 | **回测窗口/地平线** | 4 | 长回测 20150101(xml_prepare)vs run 默认 20100101(已漂移);**end=20251231 编码 4 处**(xml_prepare / run 默认 / pack 的 PACK_L=3900 数组长度 / docstring) | 年度必改事实无 owner,漏一处=静默截断 | config `checker.windows` |
| S14 | **discovery_method 值集** | 6 | submit 校验 `("automated","manual")` / check 分池 dict / runner 分支 / 注释 / **backfill 写第三个值 `"backfill"`** / backfill 脚本从**旧根 pnl 路径**推断 | "backfill" 因子静默落全库池;脚本混用两代根(ALPHA_SRC 新根、PNL 旧根) | core 枚举 + 单一值→池映射 |
| S15 | **metrics/datasources 归属** | 2 表 | health 读 snapshot、`--fix` 写 derived(metrics.py:47) | 修复永不生效,问题每轮重现 | snapshot(或随 health 删) |
| S16 | **WRITE_COMMANDS(谁写盘)** | 手抄集 vs 实际行为 | sudo.py:31-41 | `run` 缺席但写 alpha_src(EACCES);approve 在列但只碰 PG | 子命令注册时声明 `writes=True`,集合派生 |
| S17 | **PG 连接参数** | ≥6 | config state 块 + derived 块(同文件两份)/ config.py 默认 / tests / backfill 脚本硬编码 host+密钥路径 | — | config 单块 |
| S18 | **library_id** | 2 值 | config.yaml=`alphalib-juicefs` vs prod-legacy=`alphalib`;sync/CLAUDE.md 声称共享(drift) | **advisory 锁 key 含 hashtext(library_id)→ 两 config 的进程锁不同的锁,legacy 回退窗口跨机互斥失效** | 单库单值;锁 key 去掉 library 维 |
| S19 | **状态色板** | 3 | list.py:21 vs status.py:16(SUBMITTED yellow vs green)+ live_table 第三套 | 同状态两命令两色 | printer 单一 STATUS_STYLE |
| S20 | **人→邮箱映射** | 4 份 | `users:` 块(零读者)+ `authors:` 块 ×2 文件,成员不一致 | 唯一消费者是字符串里的死代码 | 删或单块 |
| S21 | **meta.json 字段集** | 3 读法 | FactorMeta dataclass(严格 `cls(**json)`)vs pack 原始 `.get("delay",1)` vs scanner `.get("delay")` | 加/删字段对新旧文件不对称地炸 | FactorMeta 宽容化,独家读者 |
| S22 | **阈值/并发度** | 2~3 | check_window 762 双源;`mode.max_workers` 加载但**零使用**(run/check 硬编码 `min(20,…)` ×2、sync 8 ×2、pack 默认 10);`backtest.stats` yaml 值被 xml_prepare 硬编码 `StatsSimpleV6` 绕过 | 配置在场但不接线=另一种谎言 | 接线或删键 |

---

## 四、病灶 L:分层失守(依赖规则的实测)

层依赖矩阵实测(import 边计数):**宣称的 `cli→services→core+infra` 在图上是环**
(core→infra→utils→infra)。真守住的只有两条:infra 从不 import 上层;
psycopg/redis/boto3 从不出 infra。

| 规则 | 今日违例 | 代表 |
|---|---|---|
| core 纯净(不引 infra、无 I/O) | 3 条 import + 大面积 I/O | **LibraryScanner 整个是伪装成 core 的 I/O 服务**(library.py:59-217);metadata.py 构造函数 glob+open;factormeta 自带持久化 |
| utils 是叶子 | 2 | log.py→infra.cache(**构成层级环**);utils.py→infra.gsim |
| cli 只碰 services | 19 | ×16 `get_default_config_path`、×3 FactorStatus;`services/run/__init__.py` 是**空文件**,cli 只能深挖模块 |
| service 包相互独立 | 9 条跨包边(4 条拉 `_` 私有名) | 病根:**datasource AST 解析+npy 索引这个三方共用的领域能力住在 `services/list/` 下**;author 推断住 submit;artifact 删除住 rm |
| infra 不做展示/进程控制 | 2+ | feishu_send import printer + `sys.exit(1)`×4;sudo.py 自建 rich Console;**sudo.py 硬编码 CLI 子命令词表**(无 import 的向上依赖) |
| services 不绕工厂钉具体后端 | 2 | approve.py:20 从 json 后端偷 `_now`;sync/merge 钉死 JsonStateStore |
| 展示归 cli | 大面积落空 | 14 个 service 文件 16 条 printer/live_table import,4 个 service 直接 rich 自建渲染——**"cli = argparse + output" 的 output 半边是空的** |
| 领域规则不下沉 infra | 3 | bcorr 分池资格规则在 runner.py:30-40;查询合并策略在 query.py:92-94(有 snapshot 条件时丢无 snapshot 行);`transition()` 纯机械 setattr——**状态机合法性哪层都不校验,只存在于 CLAUDE.md 的图里** |

**合计 ~42 条违规 import 边,8 份 import-linter 契约今天 7 份会红**(契约草案见 §六)。

---

## 五、病灶 V:退化器官(存在但死/不可达/说谎的表面)

**说谎的 CLI 表面**(解析但无效,或宣传不存在的行为):

| 项 | 证据 | Contract |
|---|---|---|
| `check --retry` | self.retry 赋值后零读取(retry 语义已被 _RETRYABLE_STAGES 自动路由取代) | 删 |
| `run --pack` | epilog 宣传"run + pack",service 零读取 | 删或接线 |
| `sync pull --force-state/--force-overwrite` | **挂错子解析器**:循环给 push/pull 都加了,pull() 签名根本没有这两参 | 移到 push-only |
| `list --sort-by delay` / `--filter-by "x!=y"` | 接受后静默无效(S8/已知) | 对齐 |
| `health --fix` | 修进没人读的表(S15) | 随 health 删 |
| rm epilog | 声称删 "factor_derived 行"——实际不碰它(反而泄漏,G2) | 改文案 + 修泄漏 |

**死配置键**(Config 属性零读者):`pnl_pool_path`、`recycle`(还被设为必填!)、
`thres`(双死:yaml 键不读、硬编码值也没人读)、`stats`、`max_workers`、`dry_run`、
`authors`、`summary_emails`、`send_author_email`、`feishu_script`、`sync_remote`;
yaml 有但 config.py 根本不读:`users:` 块、`backtest.index_ret`、`mode.dates`。

**死依赖**(pyproject,零 import):`mlflow`(全树最重)、`pandas`、`lxml`+`lxml-stubs`、
`scp`、`zstandard`、`colorama`、`argparse`(遮蔽 stdlib 的 py2 backport)、
`setuptools`/`wheel`(build 关注面混进 runtime);**僵尸依赖**:`paramiko`(仅死 Remote)、
`requests`(仅死 feishu_send)——随死代码删除自然脱落。净删 10~12 个。

**死文件/死内容**:`core/alpha/report.py`(零 importer)、`results/checkbias.py`(仅被
无用 star import 拉进)、`results/` 各文件里的 `*Status`/`*Results` 空壳(CorrStatus/
CompStatus/PointStatus 全零引用)、`utils/exception/`(0 字节目录)、`notify/` 整目录、
`Metrics.to_dict/from_dict`、`run/find.py:find_factor_dir`、`combo ALL_STATS`(定义了
却让 `--stats` 自由串通过)、s3.py 5/9 方法、`checkpoint _get_v1md5` + `metadata
get_last_v1npy_file` 死链。

---

## 六、病灶 D:领域模型碎片(16 命令 × 存储形状泄漏矩阵摘要)

12 个因子投影、两对同名撞车(FactorInfo ×2、FactorRow ×2)之外,命令级实测:

- **store 构造**:check 一次跑动 3 类 store 7 个构造点(含每因子每 worker 重建);
  list 同时走 4 条后端路径(三表 + scanner→derived);
- **info→state 双表写编排在 submit/backfill/check 各抄一遍**,字段策略互不相同
  (backfill 写 ACTIVE + discovery_method="backfill" + 无 submitted_at)——没有
  `register` 原语;
- **`_persist_derived` 靠调用顺序偷 `entered_at` 当 `snapshot_at`**——写序不变量
  靠人肉维持(check.py:173-177,406-414);
- **report.py 对每个因子做三 store N+1 点查**(report.py:56-58);
- **`rm` 问 factor_state "存在吗"、删的却是 factor_info 级联**——两张表回答同一个
  存在性问题(rm.py:43 vs :82);
- **塌缩测算**:引入 `repo.get/find/register/transition/delete` + 渲染层后,
  **8/16 命令服务层可塌缩到 <20 行**(status/info/approve/cancel/clear/rm/list/
  backfill),submit/restage/health/run 大幅瘦身,check/pack 收编存储编排、保留
  真实业务,combo 天然无关。

**Repository 必须暴露的完整方法集**(由 16 命令实际用到的操作并集推导):

记录面:`get(name)→Factor`、`find(author/status/fail_stage/field/tables/metrics/
sort/limit)`、`register(...)`(原子 info+state)、`transition(name, to, *, expect=)`
(带 CAS)、`record_check`、`attach_snapshot`(内部强制 snapshot_at==entered_at)、
`delete(name, scope=FULL|UNBORN|STAGING_ONLY)`(修 cancel 孤儿)、`exists(name)`
(一种语义)、`lock(name)`。

产物面:`paths(name)→{src,pnl,dump,feature[],staging}`(S4 的唯一 owner)、
`stage/unstage`、`archive`(staging→lib 移动 + XML 重指 + pnl 分流,一个事务性操作)、
`recall`、`purge_artifacts(name, {...})`、`iter_staging/iter_library`、
`meta(name)→FactorMeta`(S21 唯一读者)、`dump_stats/has_pnl`、`orphans()`。

**目标类型表(12 → 4)**:

| 现有 | 归宿 |
|---|---|
| FactorInfo(表)/ FactorRecord / FactorSnapshot | Repository 内部行网关,不出现在 service import |
| FactorInfo(扫盘)/ FactorRow(query)/ DerivedRecord | **消亡**(扫盘降级为 doctor 对账工具) |
| AlphaMetadata | 保留,正名为 check 的"工作台视图",构造函数去 I/O |
| FactorMeta | 保留:因子目录身份证文件格式,Repository 独家读写 |
| 新增 | `Factor` 聚合(identity+state+snapshot facets)——全库唯一叫"因子"的类型 |

---

## 七、Contract 路线图(Expand 早就做完了,以下全是减法)

**完成定义(先立规矩)**:任何一波的验收标准 = 旧路径**物理删除** + 文档同批更新 +
CI 绿。没删完不关单。

### Wave 0 — 纯删除,零依赖,1~2 天
死依赖 10 个、死文件(report.py / checkbias.py / notify/ / exception/ / utils 三死类 /
三个迁移器 / 死方法群)、6 个说谎旗标、死配置键、`cache_path(legacy_hash)`、
根 `report/` 目录合并。顺手:修 4 处说谎帮助文本、README 三处世代错误。
**同批上 CI**:ruff + pyright + `pytest -m "not slow"` + import-linter(先只挂
今天就绿的 C8,其余契约挂 warning 基线)。

### Wave 1 — 回退决断,1~2 天
轮换 S3/Feishu 密钥 → 删 config.prod-legacy.yaml、json/redis store、sync 栈、
s3.py、fcntl 回退(留显式 json-dev 模式则单独声明)、`ensure_redis_password` 钩子、
所有"紧急回退"文档承诺。锁 key 去 library_id 维(S18)。
*不可谈判的前置*:redis 实例本身是 JFS metadata,**只删 ops 代码,不动 redis 进程**。

### Wave 2 — 僵尸 derived 拆除 + 因子集正位,2~3 天
list 改 `factor_state.status != 'submitted'` 纯 PG 判据,删 scanner 白名单;
scanner 降级为 doctor 的对账工具(顺便修 S1 或让它随层消亡);删 `infra/derived/`
整层 + metrics.py/datasource.py 的 refresh 半边 + health 命令 + derived config 块 +
test_derived_store_pg + 修 rm 泄漏。init/01-schema.sql 重生成为三表(修 S2)。

### Wave 3 — SSOT 收敛,3~5 天(与 craft 文档 B/C 合流)
`FactorPaths`(S4)、Stage 表(S11)、`now_iso`+pg.ts(S12)、metric 注册表(S8)、
glob→LIKE 用正确版(S9)、状态 enum 对齐 DB(S10)、discovery_method 枚举 + 修
backfill 的 "backfill" 值与混根脚本(S14)、WRITE_COMMANDS 从注册派生(S16)、
窗口进 config(S13)、批量命令骨架 `_batch.py`(锁内重取 + `transition(expect=)`
CAS 落地)。

### Wave 4 — 领域模型立正主,1~2 周
`Factor` 聚合 + `FactorRepository`(§六方法集);8 个命令塌缩;datasource 能力从
`services/list/` 迁到共享领域模块(消 9 条跨包边);展示层上收(printer 出 services);
import-linter 契约逐份转红为绿并转 enforcing。

每一波结束:更新根 CLAUDE.md 的 SSOT 表与本文件的清单勾选。

---

## 八、守护机制(防止第五代沉积)

### 8.1 import-linter 契约(现成草案,今日 7/8 红)

```toml
[tool.importlinter]
root_package = "ops"

[[tool.importlinter.contracts]]        # C1 主分层(infra 高于 core:infra 存取 domain 模型合法)
name = "layers: cli -> services -> infra -> core -> utils"
type = "layers"
layers = ["ops.cli", "ops.services", "ops.infra", "ops.core", "ops.utils"]
# 今日 FAIL ×5:core→infra ×3(library.py:9,116; metadata.py:5)、utils→infra ×2(log.py:25; utils.py:9)

[[tool.importlinter.contracts]]        # C2
name = "cli must not import infra or core"
type = "forbidden"
source_modules = ["ops.cli"]
forbidden_modules = ["ops.infra", "ops.core"]
# 今日 FAIL ×19

[[tool.importlinter.contracts]]        # C3
name = "service packages are independent"
type = "independence"
modules = ["ops.services.submit", "ops.services.check", "ops.services.list",
  "ops.services.rm", "ops.services.restage", "ops.services.approve",
  "ops.services.cancel", "ops.services.clear", "ops.services.health",
  "ops.services.backfill", "ops.services.status", "ops.services.info",
  "ops.services.pack", "ops.services.sync", "ops.services.run", "ops.services.combo"]
# 今日 FAIL ×9(4 条拉 _private 名)

[[tool.importlinter.contracts]]        # C5
name = "utils is a leaf"
type = "forbidden"
source_modules = ["ops.utils"]
forbidden_modules = ["ops.core", "ops.infra", "ops.services", "ops.cli"]
# 今日 FAIL ×2

[[tool.importlinter.contracts]]        # C6
name = "infra must not import presentation"
type = "forbidden"
source_modules = ["ops.infra"]
forbidden_modules = ["ops.utils.printer", "ops.utils.live_table", "rich"]
# 今日 FAIL ×2

[[tool.importlinter.contracts]]        # C7
name = "services use store factories, not concrete backends"
type = "forbidden"
source_modules = ["ops.services"]
forbidden_modules = ["ops.infra.store.json_store", "ops.infra.store.pg_store",
  "ops.infra.store.redis_store", "psycopg", "redis", "boto3"]
# 今日 FAIL ×2(approve._now、sync.JsonStateStore)

[[tool.importlinter.contracts]]        # C8 —— 今日唯一全绿
name = "db drivers only in infra"
type = "forbidden"
source_modules = ["ops.cli", "ops.services", "ops.core", "ops.utils"]
forbidden_modules = ["psycopg", "psycopg_pool", "redis", "boto3"]
```

linter 抓不到、需 review 守的:core 的文件 I/O、services 直用 rich、sudo 的 CLI
词表、`_METRIC_EXPR` 镜像、状态机合法性(用 `transition(expect=)` CAS 补)。

### 8.2 SSOT 表(放根 CLAUDE.md,review 第一问:"你在问正主吗?")

§三第 6 列即初稿。核心行:因子集=factor_state;身份=factor_info;快照=factor_snapshot;
布局=FactorPaths;stage=Stage 表;时间=now_iso;状态值=FactorStatus 枚举;
写命令集=注册声明。

### 8.3 迁移完成定义(放 plans.md 顶部)

> 一次迁移包含 Expand、Migrate、**Contract** 三步;Contract = 旧代码删除 + 旧配置
> 键删除 + 文档更新 + 回退承诺移除(或回退路径有测试)。前两步完成、第三步未完成的
> 迁移,状态标记为 **IN PROGRESS**,不得开始下一次同域迁移。

---

*五路并行侦查 + 三轮前序审查合成;与 第一部分(bug)、
第二部分(工艺)配套。行号以 HEAD `f733db8` 为准。*
