# gsim-ops 项目审查报告(2026-07-07)

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
