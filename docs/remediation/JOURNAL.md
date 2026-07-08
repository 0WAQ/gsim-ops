# 整改日志(remediation journal)

分支 `claude/remediation-wave0`,基于 `docs/reports/full-review-20260707.md`(下称
full-review)。**每条记录:改了什么 / 为什么 / 为什么不是别的方案 / 如何验证。**
条目编号在 commit message 里引用。

**本分支范围**:P0 数据正确性修复(R 系列)+ Wave 0 纯删除(W 系列)+ 测试修复与
CI(T 系列)。**明确不做、留待后续决断的**:见文末"遗留决断"。

---

## R1 · 修复"重新入库永远拿不到新快照"(full-review P0-1)

**改动**:
- `restage._restage_one`:transition → SUBMITTED 后删除 `factor_snapshot` 行
  (`ops/services/restage/restage.py`);
- `submit_one` 的 `--overwrite` 路径:transition 后删除旧快照(`submit.py`);
- `check._persist_derived`:insert 前若存在同名快照行,warn 日志 + delete 后再
  insert(stale 自愈,`check.py`)。

**为什么**:快照的文档语义是"入库事件的不可变快照"(`snapshot_at = entered_at`),
因子**离开** ACTIVE 时旧快照即失效。原实现 insert-only + `except Exception` 吞掉
UNIQUE 冲突 → restage/overwrite 后再入库,快照永远停在旧代码,`--filter-by field=`
反查失明、check 报告显示旧指标,且 `ops refresh` 已删无修复路径。三处配合:删除的
正位在"离库动作"(restage/overwrite),archive 侧自愈兜住两类残留(迁移期给 REJECTED
建过的快照行、删除步骤崩掉)。

**为什么不是别的方案**:
- *insert 改 upsert*:会静默掩盖"不该存在旧行"的信号,且违反"快照只有 insert/delete"
  的语义约定 —— 自愈路径带 warn 日志,异常残留仍可见;
- *只在 archive 侧删*:那样"ACTIVE 因子的快照必与 entered_at 对应"这个不变量在
  restage 后的窗口期内是坏的,且 submit --overwrite 的语义("旧代码作废")得不到表达。

**崩溃窗口分析**:删除放在 transition 之后 —— 若中间崩溃,留下"SUBMITTED + 旧快照"
(无害:没有读路径消费 SUBMITTED 的快照,且 archive 自愈会清);若放在 move 之前崩溃,
会留下"ACTIVE 因子无快照"(有害),故不采用。

**验证**:无 PG 环境,静态验证 import/parser 通过;补充 PG 行为测试属 T 系列遗留
(I2 基建重建后补 `test_snapshot_replaced_on_rearchive`)。

## R2 · 修复裸 `ops restage` = 全库召回(full-review 第一部分 1.2)

**改动**:`ops/cli/restage.py` 的 `--status` 默认值 `'active'` → `None`;
`_resolve_targets` 补批量守卫(-u/-s 至少给一个)+ name 与 `-u` 互斥
(与 approve/cancel/clear 对齐,原先静默忽略是 clone-and-edit 漂移)。

**为什么**:守卫 `if not args.user and not args.status` 因默认值恒真而是死代码——
`ops restage -y` 会把**全库 ACTIVE 因子**搬出 alpha_src 进 staging。守卫的错误文案
本身就说明选择器本应必填。批量语义保留:`-u wbai` 缺省仍按 active(文档承诺)。

**验证**:16 个子命令 parser 冒烟通过;`_resolve_targets` 逻辑人工推演三分支
(name / -u 缺省 active / 全缺 → 拒绝)。

## R3 · 修复 restage→cancel 删唯一源码 + cancel 泄漏孤儿 factor_info

**改动**(`ops/services/cancel/cancel.py`、`ops/infra/info/{base,pg_store}.py`):
- 资格判定增加 **`entered_at` 非空一律拒绝**(单因子报错给出 rm/check 指引;批量归
  Skipped);
- `_cancel_one` 在删 state 后**同时删 factor_info**(FK 级联方向是 info→state,
  原实现每次 cancel 泄漏一行任何命令都够不到的孤儿身份行);
- `InfoStore.delete` 返回值 `None → bool`(rowcount>0),顺带修好 `rm.py:82`
  "确认信息永不打印"的说谎分支(full-review 第三部分 D3 的返回值约定统一,先落这两处)。

**为什么**:「SUBMITTED(新提交)」与「SUBMITTED(曾入库、restage 召回)」是被压成一个
状态的两个状态(full-review 第三部分 §3.1)。cancel 的前提"SUBMITTED 无产物"只对
前者成立;后者的源码唯一副本在 staging(restage 是 move),cancel 的 rmtree 是数据
丢失。判据用 `entered_at 非空 = 曾入库`(报告建议的不变量,不引入新状态)。info 行
删除的安全性由该守卫保证(走到删除的必然从未入库)。

**为什么不是别的方案**:*cancel 曾入库因子时把 src 搬回 alpha_src* 也可行但引入了
第二种"归还"语义与 check 扫描竞态;拒绝 + 指引到既有命令(rm / check)让每个动作
保持单一语义。

**验证**:parser + import 冒烟;lifecycle 的 PG 行为测试待 I2。

## R4 · 修复 `to_lib` 对单文件 pnl 用 rmtree(full-review 第一部分 1.2)

**改动**:`check.py to_lib`:`pnl_dst.is_dir() → rmtree;exists() → unlink()`。

**为什么**:`alpha_pnl/<name>` 是单文件(根 CLAUDE.md 明文警告的 Errno 20 反模式),
restage 默认保留 pnl → re-archive 必踩 `NotADirectoryError`,整个 archive 中断在
状态已 ACTIVE、产物搬了一半的窗口里。保留目录分支是防远古目录形态残留。

**验证**:import 冒烟;该路径的行为测试需 gsim(e2e 组),标注遗留。

---

## W1 · 删除零引用死文件(full-review 第三部分 §五/G5/G7/G8)

**删除**(全部经三轮审查 + 专项 grep 确认零 importer;git 历史永远可找回):
- `ops/core/alpha/report.py`(AlphaReport,零引用)
- `ops/core/alpha/results/checkbias.py`(全 `...` 空壳;同时移除 check.py 与
  checkbias_checker.py 的对应 star import —— 该文件只被无用 star import 拉进)
- `ops/infra/notify/`(email.py:294 行里 ~280 行在字符串字面量里;feishu_send.py:
  零 importer + 硬编码密钥。**注意:密钥已在 git 历史里,删除≠撤销,须轮换**)
- `ops/utils/exception/`(0 字节空目录)
- `ops/tools/{state_migrate,state_to_pg,derived_migrate}.py`(三代一次性迁移器,
  任务均已执行完毕;第三个的迁移目标 derived 层本身待删)
- `tests/{test_all_services,test_end_to_end}.py`(脚本式测试:print 当断言、硬编码
  生产 conninfo、绕过全部 fixture;其场景由 I2 重建为契约测试)

**为什么是删除而不是修**:每一项要么零调用者、要么服务于已退役世代。Lava Flow 的
解法是 Contract(拆除),对僵尸做维护是负价值(full-review 第三部分假重复清单)。

## W2 · utils 瘦身(G5/V)

**改动**:`ops/utils/utils.py` 收缩为仅 `LowerAction`(删 Remote/Local/Gsim——
paramiko stub、`sys.exit` 工具函数、与 infra/gsim/runner 整段重复的硬编码路径旧
runner);`ops/utils/func.py` 删 `debug()`(无限循环地雷)。

**为什么**:9 个 CLI 模块为一个 3 行 argparse Action 每次启动付 paramiko import 税;
回测能力从此只有一个家(infra/gsim/runner.py)。LowerAction 迁往 `ops/cli/common.py`
属第二部分 H 工作流,此处先最小化。

## W3 · 幽灵状态 DECAYING/RETIRED 移除(S10/G13)

**改动**:`FactorStatus` 删除两成员;list/status 两处色板删除对应条目;
`cli/pack.py` 的手抄 choices 改为从 enum 派生;docs/factor-state-machine.md 与
submit/CLAUDE.md 同步。

**为什么**:enum + CLI 接受、DB CHECK 拒收、无任何 transition 产生 —— 为从未发货的
未来预留的接口,唯一效果是让用户选到一个必然空结果/必然报错的值。**DB 约束是权威**;
将来真做衰退生命周期时,enum 与 CHECK 约束同一提交里一起加。顺带消灭一处
"CLI choices 手抄字符串"多真相源。

## W4 · 说谎的 CLI 表面(V 表)

**改动**:
- `check --retry` 删除(解析后从未读取;retry 语义早由"validate/long_backtest 失败
  自动回 SUBMITTED + 下次无条件重扫"取代)—— cli + service 形参/属性一并删;
- `run --pack` 删除(epilog 宣传 "run + pack",服务层零读取);
- `sync pull` 的 `--force-state/--force-overwrite` 移到 push-only(for 循环盲目
  复制给了 pull,而 `pull()` 签名根本没有这两个参数;`run_sync` 用 getattr 读,
  安全);
- `list`:epilog `--sort` → `--sort-by`(不存在的旗标)、`--filter-by` help 的
  `table=` → `tables=`(非法键)、`--sort-by` choices 删 `delay`(接受后被服务层
  静默忽略;要支持须同时进 `_SORTABLE_KEYS` 与 snapshot `_METRIC_EXPR`);
- `rm` epilog:"factor_state 行 + factor_derived 行" → 实际行为(factor_info 级联
  state+snapshot;rm 从不碰 factor_derived —— 那是泄漏,随 Wave 2 层删除一起消失)。

**为什么**:帮助文本是接口的一部分;教用户使用不存在/无效的旗标比没有文档更糟。

## W5 · 依赖大扫除(§五)

**改动**:`pyproject.toml` 删除 mlflow(全树最重)/pandas/lxml/lxml-stubs/scp/
zstandard/colorama/argparse(py2 backport,遮蔽 stdlib)/setuptools/wheel(build
关注面)+ 随 W1/W2 死代码退役的 paramiko/requests;`uv lock` 重锁。
**保留**:boto3/tqdm(legacy sync 仍活)、redis(回退后端)—— 随 Wave 1 决断退役。

**验证**:`uv sync` 后全模块 import 扫描 NONE 失败;fast suite 绿。

## W6 · StateStore 契约对齐(第一部分 P1 表 LSP 违反)

**改动**:`StateStore.list` ABC 删除 `author` 参数(PG 实现早已没有);
`JsonStateStore.list` 同步(其 author 过滤读 `r.author`,字段已删,本就必炸)。
redis 后端**不修**——整体属 Wave 1 决断(修好或删除)。

---

## T1 · 修复红了三周的 fast suite(第一部分 1.3)

**改动**:`test_pure.py` / `test_state_store_pg.py` 的 `FactorRecord` 构造去掉
author/submitted_by,list(author=) 断言移除;`test_library_id_isolation` 删除
(library_id 分区已不存在,隔离模型整体待 I2 重建)。

**基线→结果**:10 failed / 9 passed → **14 passed / 63 skipped / 0 failed**。

## T2 · PG fixtures 诚实化

**改动**:conftest 的 `state_store`/`derived_store` fixtures 改为显式
`pytest.skip`(带原因),不再以 TypeError 的形式"假装可用"。

**为什么不直接修**:正确形态是 per-test schema 隔离(`CREATE SCHEMA t_<uuid>` +
search_path)+ FK 种子行(factor_state.name REFERENCES factor_info),必须对着真
PG 迭代验证 —— 盲改只会产出第二批坏测试。这是 full-review 第二部分 I2 工件,
须在有 ops_test 访问的环境完成。skip 原因里写明了指引。

## T3 · 最简 CI(行动清单 #5)

**改动**:`.github/workflows/ci.yml` —— uv + `pytest -m "not slow"`。PG 组自动
skip,e2e 排除。

**为什么最简**:先让"红测试落分支"从无声变有声;ruff/pyright/import-linter 门禁
按第二部分 A 的节奏跟进(需要先烧掉存量违规,不在本批)。

---

## 遗留决断(本分支明确不做,需要另行拍板)

| 项 | 内容 | 阻塞点 |
|---|---|---|
| ~~**Wave 1 回退决断**~~ | **已执行(见下方 Wave 1 章节,F1-F6)**:删除路线 | ⚠ 密钥轮换仍未完成(git 历史),见 F1 |
| ~~**Wave 2 僵尸拆除**~~ | **已执行(见下方 Wave 2 章节,V1-V4)** | ~~生产验证~~(PV1-PV6 已完成)⚠ 手动跑 migrate_drop_derived.sql + JSON 消费方适配 |
| **I2 测试基建** | per-schema 隔离 + info 种子行 + 契约测试补齐(含 R1 的行为测试) | 需要可达的 ops_test PG |
| 死 config 键清理 | recycle/thres/stats/max_workers/authors/notification/users 等 | 触碰 Config 必填键集,与 G(Config 治理)一起做 |
| bcorr.cpp 归属 | ops 仓里的 C++ 源与 gsim 部署二进制的关系 | 需要作者确认 |

---

# Wave 1 · 回退决断:删除假保险(2026-07-07,同分支)

**决断**:按 full-review 建议选择**删除**而非修复。理由:三个"回退"路径
(prod-legacy 配置 / redis state / json-as-rollback)经三轮审查确认**全部早已
不可用**——修复它们等于为一个已被 JFS+PG 取代的世界重建两套存储栈,且每一套都
需要持续维护与测试才能算"保险";而未经验证的回退不是冗余,是负债。

## F1 · 删除 sync 栈 + S3 + prod-legacy 配置

**删除**:`ops/services/sync/`(sync.py 546 行 + diff/merge/etag_cache + CLAUDE.md)、
`ops/cli/sync.py`、`ops/infra/s3.py`、`config.prod-legacy.yaml`、boto3/tqdm 依赖;
main.py 注销 sync 子命令。

**为什么**:sync 只对 prod-legacy 配置有意义,而该配置在三表拆分后已不能运行
大多数命令(query_factors 抛 NotImplementedError、info/snapshot store 抛
ValueError);sync 自身还在推送再上一个世代的文件(metrics.json,其 merge 函数
是空壳)。JFS 是多机共享的现役方案。

**⚠ 遗留义务(不可跳过)**:`config.prod-legacy.yaml` 里的 MinIO
access/secret key 与(已删的)feishu_send.py 里的 APP_SECRET **仍在 git 历史中**,
删除文件≠撤销凭据,**必须在服务端轮换**。轮换前该 endpoint(公网可路由 + 明文
HTTP)视同已泄露。

## F2 · 删除 redis state 后端

**删除**:`ops/infra/store/redis_store.py`、`default_store` 的 redis 分支、
config.py 的 state.redis 解析(~35 行三层密码 fallback)、config.yaml 的
state.redis 块、`ensure_redis_password` 钩子(sudo.py + main.py)、
`OPS_STATE_REDIS_PASSWORD` 出 `_PRESERVE_ENV`、redis 依赖。

**为什么**:三表拆分后 RedisStateStore 读写已删字段,每次 put 必 AttributeError
——"回退"从未可用。**边界再次强调:redis-sentinel 实例是 JuiceFS metadata 后端,
不可停;本次删除的只是 ops 侧代码/配置/依赖,对 JFS 零影响。**

## F3 · json 后端正名为 dev/test

**改动**:保留 `JsonStateStore`,文档从"紧急回退"改为"单机 dev/test 后端"。

**为什么不删**:测试套件的无 PG 层建立在它之上(test_pure 全绿),且 I2 计划以
同一模式补 Json info/snapshot store。它单机语义正确、有测试覆盖 —— 与另外两个
假保险有本质区别。但它**不承诺**多机正确性,故明确"非生产回退"。

## F4 · factor_lock:静默降级改硬错误

**改动**:`state_backend=postgres` 但 conninfo 不可用时抛 RuntimeError,不再
静默退回单机 fcntl;未知 backend 同样硬错误。

**为什么**:静默降级 = 跨机互斥无声消失,三机并发 check 同一因子的防线在最需要
它的时刻(配置/密码出问题)恰好失效,且无任何告警。宁可停下来让人修配置。

## F5 · 锁键去 library_id 维(S18)

**改动**:advisory lock 键从 `(hashtext(library_id), hashtext(name))` 改为
`(hashtext('ops:factor_lock'), hashtext(name))` 固定命名空间。

**为什么**:library_id 曾随 config 文件不同(alphalib vs alphalib-juicefs),
两个进程锁的不是同一把锁 —— 跨机互斥在混用 config 的窗口期失效。单库世界里
锁键不该有 library 维度。**部署注意**:新旧键不同,滚动升级期间新旧版本 ops
互不互斥,升级时确保无 in-flight check。

## F6 · sudo 顺带修复(与 F2 同文件)

- `run` 补进 WRITE_COMMANDS(它改写 alpha_src XML + gsim 写 pnl/dump,一直缺席
  → JFS 下非 root EACCES,full-review 第一部分确认的高危项);
- sudo 去掉 `-E`(它保留整个用户环境,让 `--preserve-env=<白名单>` 形同虚设)。

## Wave 1 验证

- fast suite:14 passed / 0 failed(与 Wave 0 后一致);
- 全模块 import 扫描零失败;15 个子命令 parser 正常,`ops sync` 确认消失;
- `uv lock` 重锁,redis/boto3/tqdm 及其传递依赖移出。

## Wave 1 后的世界(一句话)

state 只有一个生产后端(PG)+ 一个声明过的 dev/test 后端(json);锁只有一种
跨机语义;没有任何文档承诺不存在的回退。下一步 Wave 2(僵尸 derived 拆除 +
list 纯 PG 判据)。

---

# Wave 2 · 僵尸拆除:因子集正位 + derived 层退役(2026-07-07,同分支)

## V1 · list/info 判据正位:因子集的正主是 PG

**改动**:
- `query_factors`(ops/infra/query.py)成为**"库内因子集"的唯一定义处**:
  `status` 缺省 = `factor_state.status != 'submitted'`;合并逻辑改为以 state
  为基、info 提供身份;**limit 不再下推**(P0-5 修复:旧实现无 ORDER BY 下推
  LIMIT 进 snapshot 单表 → PG 返回任意 N 行 → 行数错乱 + metrics 空白;三表
  内存合并模型下 limit 只能合并后截断,由 list.py 的 [:n] 执行)。
- `run_list` 删除 `scanner.scan()` 白名单与 `--refresh` 旗标 —— **list 零扫盘,
  纯 PG catalog 查询**(全库扫盘从每次 ~25s 降为 0)。
- **JSON 输出变更(破坏性,需通知消费方)**:`has_pnl`/`dump_days` 键移除
  (实时物理事实,唯一来源是全库扫盘,与 catalog 查询语义冲突;单因子看
  `ops info`),新增 `status` 键。
- `ops info` 存在性判据从"alpha_src 目录存在"改为 **factor_info 行存在**
  (S5:原先同一因子可 status 存在、info not found);物理事实改单因子现场
  stat,src 目录缺失时**显式提示漂移**而不是 not found;标题栏新增 status。

**为什么**:"X 是不是库内因子"在代码里曾有 6 种答案(full-review S5)。收敛
原则:**成员资格的正主是 PG**,磁盘是产物存储;PG 与磁盘的漂移是对账问题
(未来 ops doctor),不该让每次查询付对账税。

**行为变化点(验收时注意)**:list 现在会列出"PG 有记录但盘上目录被手工删了"
的因子(以前被扫盘白名单静默吞掉)——这是故意的:漂移应当可见。

## V2 · derived 僵尸层整层删除

**删除**:`ops/infra/derived/`(~700 行)、LibraryScanner 的
`_store/_load_index_from_store/_publish_index` 缓存路径、
`services/list/metrics.py`(整个,唯一消费者是 health)、
`services/list/datasource.py` 的 refresh/load 半边(纯解析函数保留——
submit/check/backfill 在用)、config 的 `derived` 段与
`derived_backend/derived_postgres_conninfo` 属性、`tests/test_derived_store_pg.py`
与 test_pure 的 JsonDerivedStore/metric_get 测试段。

**为什么**:该层唯一的"活"联系是 scanner 索引缓存——而它自三表迁移起就是坏的
(derived_meta 被重建为无 library_id 形状,get_meta 每次 UndefinedColumn 被
except 吞掉 → 缓存永久失效,每台机器每次 list 都白付 ~25s 扫盘,P0-4)。
被"写者"而非"读者"续命,是反转的死代码。顺带消灭:rm 泄漏 factor_derived 行、
metric_get/_METRIC_EXPR 的"三处不能 drift"注释契约少了一处。

**生产库遗留**:`factor_derived`/`derived_meta` 两张表还在 ops 库里。清理脚本
`scripts/postgres/migrate_drop_derived.sql`,**手动执行**,前置条件写在脚本头
(三机 ops 均已更新 + snapshot 行数 spot-check)。

## V3 · health 命令删除

**删除**:`cli/health.py`、`services/health/`、main.py 注册。

**为什么**:CLAUDE.md 早已标记"计划删除";其 `--fix` 读 snapshot、写僵尸
derived 表,修复永不生效、问题每轮重现(S15)——比没有更糟的假修复。对账
职能(盘面 vs PG 漂移、孤儿产物)归未来 `ops doctor`(设计输入见 full-review
第三部分 Repository 产物面 orphans()/dump_stats())。

## V4 · 空库 bootstrap 修复(P0-3/S2)

**改动**:`scripts/postgres/init/01-schema.sql` 重写为三表结构(逐字镜像三个
pg_store 的 `_SCHEMA`,FK 依赖序 info→state→snapshot),原文件是迁移前的
两表旧世界,150/144 起新库会直接起错。文件头标注了"与 store 常量是镜像多
真相源,G-wave 收敛"。

**验证**:fast suite 绿(5 passed / 54 skipped;通过数下降是删掉了僵尸层
自身的 9 个测试);全模块 import 零失败;15 个 parser 正常,health 确认消失。
**待生产验证**:`ops list`(耗时应从 ~25s 降到亚秒级,且不再需要 --refresh)、
`ops info <name>`、JSON 消费方对输出变更的适配。

---

# Wave 3 · 工具链落地:品味变成机器可执行(2026-07-07,分支 claude/remediation-wave3)

## A1 · ruff + pyright 进 CI(full-review 第二部分 A)

**改动**:
- dev 依赖加 ruff/pyright;`[tool.ruff]` 首批规则 `F, E7, I, UP006/007/035/045,
  B006/B008`(对准本库实际病灶;不开 D 系——与双语 docstring 约定冲突,守则 J10);
- **基线 218 个违规 → 0**:103 个自动修复(import 排序 + 死导入),其余手工;
- **pyright 10 错 → 0**(Wave 0-2 的删除已消掉原 38 错中的大部分);
- CI 增加 `ruff check` + `pyright` 两道门禁,与 pytest 并列。

## A2 · 31 处 star import 烧尽(craft B1)

**改动**:全部改显式导入;四个 service `__init__.py` 统一为
`from .<mod> import run_<cmd>` + `__all__`(守则 J9);checker 包 `__init__`
显式导出三个基类名。**check.py 那条"必须在 star import 之后 import 否则被
遮蔽"的承重注释随之删除**——import 顺序不再是承重结构。

## A3 · pyright 揪出的真 bug 顺手正修(不压制)

| 修复 | 说明 |
|---|---|
| `Checker.clean()` 声明进 ABC(默认 no-op) | pipeline 一直在调一个 ABC 上不存在的方法,只有 CheckpointChecker 恰好实现——未声明的契约(craft 文档 abstraction 项) |
| snapshot list 的 `LIMIT` 参数化 | 消掉 f-string 拼 SQL 的注入面/负数崩溃点(full-review V 表);动态 WHERE/ORDER 结构全来自白名单,值参数化,定点豁免并注释 |
| lock.py `fetchone()` 判 None | 原 `[0]` 直接下标 |
| pack.py `mms: dict[tuple[str], ...]` → `dict[str, ...]` | 错误注解(键实际是 "v1"/"v2") |
| restage `runnable` 收窄为 `(rec, src)` 对 | 消 Path|None 传参 |
| `md5sum` 裸 `except:` → `(OSError, ValueError)` | 原先连 KeyboardInterrupt 都吞 |
| `dict.get(discovery_method or "")`、status Optional.author、LowerAction 签名对齐 | Optional 漏判三处 |
| `FactorRecord.updated_at: str \| None` | 与 `_ts_out` 的诚实类型对齐 |

**验证**:ruff 0 / pyright 0 / fast suite 5 passed 0 failed / 14 个 parser 正常。
从此任何新增 star import、死导入、Optional 漏判都会在 CI 红掉——
"好方言无人执法"的时代结束(full-review 第二部分总评)。

## C1 · 批量命令骨架 `_batch.py` + transition CAS(craft C / full-review §3.2)

**改动**:
- 新增 `ops/services/_batch.py`:`confirm_or_abort` / `apply_locked` /
  `BatchResult` / `SkipFactor`。restage/approve/cancel/clear 四个命令的
  确认交互、锁循环、汇总(~200 行四份手抄)收敛于此;
- **TOCTOU 修复落地**:action 在锁内**重取记录复验资格**(确认提示挂起的几分钟
  里因子可能已被 check 转 ACTIVE / restage 召回 / rm 删除),复验不过按跳过处理
  并说明原因;cancel 的资格谓词函数化(`_ineligible_reason`),resolve 与锁内
  复验共用同一真相源;
- **`StateStore.transition(expect=)` CAS**:PG 在 FOR UPDATE 行锁内校验
  from-status,不符抛 `StateConflict`(json 同语义)—— 原 transition 无任何
  守卫,任何状态可被翻成任何状态。approve 用 `expect=REJECTED`,restage 用
  `expect=召回前状态`;骨架把 StateConflict 归入 skipped(并发变更不是错误);
- **写命令失败强制留痕**:骨架的异常分支 printer.error **且** logger.exception
  ——原先 8 个写命令全不 import loguru,失败零诊断痕迹(craft E2);
- `run_restage/approve/cancel/clear` 返回 `BatchResult`——测试从此能断言
  "正确拒绝"而非"跑完后状态没变"的代理断言(craft 测试工艺项);
- 顺带:`ops/utils/clock.py` 落地时间戳单一真相源(S12,13 处副本 → 1 处定义:
  3 个 _now 定义改 delegate、check.py 8 处内联 + submit/backfill 各 1 处替换、
  approve 的跨后端私有导入消灭)。

**为什么 SkipFactor 是控制流异常而不是返回值**:资格复验深埋在各命令自己的
action 里,骨架只需要知道"跳过+原因";异常让 action 保持平铺直叙,避免每个
命令再发明一套 (ok, reason) 返回协议 —— 这正是 CheckFail/CheckSkip 在 check
流水线里被 craft 报告判定为合理的同一形状。

**验证**:新增 `tests/test_batch.py` 11 个行为测试(json 后端无需 PG:CAS
通过/冲突/无守卫兼容、四种结局路由、单因子失败不阻断批次、确认交互)——
锁循环语义第一次有了可断言的测试。全套件 16 passed / ruff 0 / pyright 0 /
四命令 parser 正常。

---

# Wave 4 · check 流水线内科手术:Stage 表 + XML I/O 收敛(2026-07-07,分支 claude/remediation-stage-table)

## P1 · Stage 表:stage 身份收敛到 PIPELINE(full-review S11 / craft B2)

**改动**:
- 新增 `ops/services/check/stages.py`:`@dataclass(frozen=True) Stage(name,
  make_checker, prepare, retryable, keep_artifacts_on_fail)` + `PIPELINE` 元组
  (6 行,每行一个 stage 的全部身份);`STAGES` / `RETRYABLE_STAGES` /
  `KEEP_ARTIFACTS_STAGES` 全部派生,`CORRELATION` 常量导出;
- `check.py`:`__init__` 的 6 个命名 checker 属性 → `self.checkers: dict`
  (按 PIPELINE 构造,DI 注入语义不变);`_run_one_locked` 的 **6 段复制粘贴
  运行块 → for-loop**(emit_start → prepare → check → clean → emit_done,
  correlation 的返回值捕获给 archive 落 bcorr);`on_reject` 内嵌的
  `_LATE_STAGES` → 表派生的 `KEEP_ARTIFACTS_STAGES`,签名改
  `(factor, failed_stage: str)`(它只需要 stage 名);
- `clean()` 钩子从"只对 checkpoint 调"变为**每个 stage 通过后统一调**
  (ABC 默认 no-op,只有 CheckpointChecker 实现 —— 行为不变,契约归一);
- approve 的 `_CORRELATION = "correlation"` 手抄字面量 → import `CORRELATION`
  (它判 last_fail_stage 用的本来就是 check 流水线的词汇,依赖显式化)。

**为什么**:一个 stage 的身份原先散在 ≥5 处靠注释 "Must match" 手工同步
(STAGES 元组 / _RETRYABLE / _LATE / 12 个异常子类字面量 / 6 段运行块),
新增 stage 改 5 处漏 1 处即**静默路由错误**(不报错,只是走错 REJECTED/retry
分支)。现在新增 stage = PIPELINE 加一行。

**为什么不是别的方案**:
- *Enum + 各处 match*:枚举只统一名字,顺序/重试策略/prepare 绑定仍散落;
- *保留 6 段块只抽公共函数*:消不掉"新增 stage 要同时改块和集合"的双写;
- *checker 自带 stage 属性*:身份仍写在 6 个文件里,表的意义(一眼看全流水线)
  丢失。

## P2 · 异常归因反转:流水线盖章,12 个异常子类删除

**改动**:`CheckFail`/`CheckSkip` 构造签名去掉 stage 参数;12 个单行子类
(ValidateFail/Skip … CorrelationFail/Skip)全删,checker 直接
`raise CheckFail("原因")`;`_run_one_locked` 捕获时按 `current_stage` 归因
(loop 外的 archive 段兜底归因 "archive")。顺带删掉三个坏 `__repr__`:
`CheckFail.__repr__` 里有一行**遗留调试 print**(每次 repr 往 stdout 喷参数,
会撕 Live 表)、CheckSkip/BacktestError 的 len 判断逻辑写反(`len>1` 返回
`args[0]`);`CheckpointFail()` 原先不带消息 → 报告里 fail_reason 空串,
现在给出 md5 摘要对比。

**为什么**:stage 字符串硬编码在异常子类里是 S11 的主要漂移源 —— checker
代码被复制到新 stage 时旧字符串跟着走,路由静默错位。流水线是这两个异常的
**唯一捕获方**(全库 grep 验证无第三方 catch),且捕获点永远知道当前跑到哪个
stage,由它归因**结构上不可能错位**。

**为什么不是别的方案**:
- *保留 stage 参数、raise 时手写*(`raise CheckFail("validate", ...)`):字面量
  只是从子类挪到 raise 点,漂移源不灭;
- *checker 基类注入 self.stage*:checker 与 stage 强绑定,而 checker 本可被
  两个 stage 复用(validate/long_backtest 已经是同构的 Runner.run_backtest)。

**兼容性**:CheckFail/CheckSkip 无外部消费方(测试 fake checker 同步改);
`str(e)` 展示行为不变(Exception 默认 __str__)。

## P3 · xml_prepare 响亮化(行为变更,验收注意)

**改动**:
- 4 个 stage prepare 的整段 `except Exception: ...` **吞错全部删除**;每个
  prepare 缩成一行声明式 `_apply(factor, window=…, dump_pnl=…, dump_alpha=…)`;
- 回测窗口从散落的裸字符串 → 命名常量(`VALIDATE_WINDOW` / `CHECKBIAS_WINDOW`
  / `LONG_BACKTEST_WINDOW`),全流水线窗口只此三处定义;
- `prepare_for_archive`:删掉写死 `/mnt/storage/alphalib`(旧库路径!)的
  @module 写入 —— 它随后必被 `to_lib → rewrite_module_path` 覆盖,属无效写入,
  且用的还是 JFS 迁移前的老路径;保留的 pnl/dump "拆雷" 目的地命名为
  `ARCHIVED_XML_SCRATCH`(/tmp/alphalib,防手动重跑入库 XML 砸生产);
- `save_xml(factor)` 的 `open("r+")+truncate` 写法 → 统一 `xmlio.save_xml`。

**行为变更**:stage prepare 失败(XML 缺键 / JFS 写失败)原先被静默吞掉,
stage 拿着**上一个 stage 的窗口**继续跑 —— validate 可能跑成全历史(30min+),
checkbias 可能在错误区间做前视检查,**结果全不可信但显示 pass**。现在异常
直接抛,走 unexpected 臂:revert SUBMITTED + 留 staging + 完整日志。恶性
静默换良性响亮。注:恒定坏 XML 会反复 SUBMITTED↔error 循环,但这与其它
unexpected 异常同性质,操作员看报告/日志即见,好过静默错跑。

## P4 · XML I/O + 因子目录搬迁去重(full-review S 组手抄事实)

**改动**:
- 新增 `ops/utils/xmlio.py`:`load_xml` / `save_xml` —— unparse 参数
  (`pretty=True, encoding, full_document=False`)从 **7 处手抄**收敛到 1 处
  (check/restage/run/normalize/checkbias_checker/xml_prepare;漏抄
  `full_document=False` 会给 XML 加声明头,gsim 不认);
- 新增 `ops/utils/factor_dir.py`:`clean_pycache` / `rewrite_module_path` ——
  check.py 与 restage.py 的两份克隆(S 组 clone-and-edit)合一;
- `metadata.py` / `submit/parser.py` 的 xmltodict.parse 一并走 `load_xml`
  (顺带统一了 metadata.py 原先依赖系统默认编码的 `open()` 读)。

**为什么不是 edit_xml 上下文管理器**:考虑过 `with edit_xml(path) as cfg`,
放弃 —— 一半调用点需要条件保存(normalize 只在 changed 时写)或异常包裹
(run 的 restore 臂要 log 不抛),统一不了;两个函数 + 调用点显式 save
反而诚实。

## Wave 4 验证

- ruff 0 / pyright 0;fast suite **23 passed**(16 → 23);14 个 parser 冒烟正常;
- **新增 `tests/test_check_routing_json.py`(7 用例,json 后端,CI 常跑)**:
  流水线 5 个非 pass 结局第一次进 CI —— PG 版 routing 测试自三表拆分起一直
  skip(I2 未建),此前这台机器上流水线控制流是零覆盖。同时钉住 Wave 4 两个
  行为点:归因盖章(fake checker 抛不带 stage 的异常,断言 last_fail_stage
  正确)、prepare 失败响亮化(patch save_xml 抛 OSError → SUBMITTED +
  checker 零调用)。conftest 抽出 `write_factor`(config 无关的因子模板工厂,
  与 PG 组共用,防模板克隆)+ `json_config` fixture(CACHE_ROOT/LOCK_DIR
  隔离到 tmp);
- pass→archive 路径(需 PG snapshot store)仍归 PG 组,I2 后补;e2e(真 gsim)
  待生产环境跑 `uv run pytest -m e2e` 验证。

**Wave 4 遗留(记录,不在本分支做)**:
- `_scan_factors` 构造 `AlphaMetadata` 不容错:staging 里**一个** XML 缺
  Universe/Portfolio 节点的因子会让整个 `ops check` 在扫描期崩掉(本次写
  smoke 时踩到)。修复属扫描健壮性,与 prepare 响亮化不同层,归 ops doctor /
  scan 加固;
- validate 与 long_backtest 的 checker 同构(仅窗口不同),可合一为参数化
  BacktestChecker —— 等 Stage 表稳定后顺手做。

(e2e 已 grep 验证:不 import 任何被删异常类,断言全走 state/文件落点,无需改;
本地无 gsim 未跑,生产验证时照常 `uv run pytest -m e2e`。)

## D1 · 文档漂移清扫(audit-driven,2026-07-08)

**起因**:用户问"之前的分支有没有更新文档"。docs-auditor 全量核对(16 个
CLAUDE.md + docs/ + plans.md + tests/README,对照代码逐条验证)结论:四波主体
文档更新到位(11/16 CLAUDE.md 零漂移),但扫出 **4 条高危 + ~20 条中低危**,
集中在三类死角:低频维护段(根 CLAUDE.md 依赖表/技术债表)、旧稿复制的
crash-recovery 段(还把 state 说成 redis)、面向研究员的 docs/(没跟上
restage-move 语义与 --retry 删除)。

**修复**(docs-updater 执行 + 人工收尾,13 个文件):
- **高危 4 条**:factor-state-machine 的 restage 行改"移入 staging,alpha_src
  不再保留"(原文"拷贝保留"会诱导删掉唯一源码 —— 正是 R3 cancel 守卫防的事故);
  根 CLAUDE.md `list --author` 死 flag → `-u`;core/CLAUDE.md 的
  `_modify_always()` 构造函数写盘描述重写(现实:构造无写盘,niodatapath 由
  prepare_for_initial 落盘);gsim-factor-validation 的 `ops check --retry`
  死 flag → 裸 check 重扫。
- **中低危**:依赖表换 pyproject 实际七项;技术债表删指向已删代码的条目;
  两处 redis crash-recovery 段 → PG;restage 文档与 confirm 提示的
  "REJECTED 默认保留产物"谎言修正(代码一律自动清 dump+feature+pnl);
  infra JsonStateStore 方法表对齐;Notify 节删除;tests/README 隔离模型
  诚实化(library_id 分区已随三表失效,PG fixtures 待 I2);plans.md 全部
  **加注不删史**(Architecture Refactor 标已落地、health/sync 待办标已退役、
  "拆独立 redis" 标过时);check/CLAUDE.md 遗留的"index 组扫盘补全"矛盾句删除;
  `.claude/` 两个 skill/agent 的 --retry 建议修正;combo 决策文档加注。
- **唯一代码改动**:restage.py 模块 docstring(删 sync 传播段,改 PG/JFS
  跨机语义)+ `_print_plan` 确认提示 —— 原提示在即将自动清 REJECTED 产物时
  打印"默认保留 dump/feature/pnl",确认时刻对操作员说谎,按实际行为分状态提示。

**明确不动**:两处部署事实待用户确认(alpha_dump 是否有 .local sidecar
bind-mount vs config.yaml 指 JFS;/mnt/storage/alphalib 是 JFS 软链还是待清理
旧数据 —— docs/README 与根 CLAUDE.md 互斥,仓库内无法裁决);docs/reports/ 与
本 JOURNAL(历史记录,漂移即历史);plans.md 的未来设计段原样保留。

**验证**:ruff 0 / pyright 0 / 23 passed;残留词自查(--retry / _modify_always /
--author / bulk_upsert / infra/ssh / infra/notify / redis state)在非历史文档中
清零。

**教训(进遗留决断)**:文档漂移集中在"没有对应代码 owner 的文档"(docs/ 面向
研究员的两份、plans.md、根 CLAUDE.md 的表格段)。改代码时顺手改同目录
CLAUDE.md 的习惯已经生效(11/16 零漂移),但跨目录文档没有触发器 ——
后续可把 /audit-docs 挂成低频例行(如合 main 前必跑)。

**D1 后记(2026-07-08,用户确认部署事实)**:两处存疑已裁决并落根 CLAUDE.md ——
① `/mnt/storage/alphalib` 是**软链**指向本机实际 alphalib 路径(各机挂载点不同),
docs/README 的说法为准,"旧数据待清理"的旧句删除;② alpha_dump 实体在
`<挂载点>.local/alpha_dump`(本地盘 sidecar,不进 JFS),`alphalib/alpha_dump`
是指向它的**软链** —— "本地 sidecar"与 config.yaml 的 JFS 路径引用由软链调和,
两处原说法各对一半,现合并为唯一表述。

---

# 生产验证第一轮(2026-07-08,server-160,分支 claude/remediation-wave0)

## PV1 · 结果判读:27 处红全在测试侧,生产代码零失败

用户在 160 跑第 1-2 层验证:fast suite 26 failed / 25 passed,e2e 5/6。
**逐条归因后无一指向生产代码**:

- ✅ 正面信号:`test_check_routing` 真 PG 上 **12/12 全绿**(流水线路由 + snapshot
  落库 + pnl 分流);e2e 5 条失败路径(validate/checkbias/checkpoint/compliance/
  correlation)全部按预期路由;**e2e pass 路径的流水线本身跑到了 ACTIVE**
  (`AlphaWbaiPass → lib`),挂的是测试断言的 import。
- ❌ 13 × `FactorRecord(author=)` TypeError:测试用三表拆分前的旧签名 —— 这些
  pg-marked 测试在无 PG 环境永远 skip,旧签名从未被执行到(I2 预告的问题现形);
- ❌ 11 × ForeignKeyViolation:测试裸 `store.put`,没先种 factor_info 父行;
- ❌ 3 × 残留污染:ops_test 是**生产库克隆**(7607 行真因子 + 迁移期约束名
  `factor_state_new_name_fkey`)+ 按 library_id 删行的 teardown 是 no-op,
  上轮测试残留(AlphaEnsure/AlphaWbaiNew/AlphaWbaiNoDm)让断言前提破产;
- ❌ 1 × e2e import:`ops.infra.derived` 已删(Wave 2),e2e 断言段漏改
  (e2e 标 slow,开发容器跑不了,漏网)。

## PV2 · 修复(相当于 I2-lite:让 PG 组第一次真正可跑)

- **e2e 断言换正主**:derived store → `default_snapshot_store`(metrics/fields/
  rm 级联三处);e2e conftest 删掉指向已删 derived 层的 config 块;
- **conftest 三表引导**:`pg_conninfo` 可达后按 FK 依赖序 info → state →
  snapshot 各连一次 —— 空库上 factor_state 的内联 FK 引用 factor_info,
  state store 先连会 UndefinedTable(V4 只修了 docker 01-schema.sql,
  没覆盖 store 自建路径);
- **隔离模型过渡方案**:`library_id` fixture 的 teardown 从"按 library_id
  删行"(no-op)改为**测试前后各清一次 ops_test 三表**(`wipe_test_db`:删
  factor_info,FK 级联;带 `current_database()='ops_test'` 双保险,防误指
  生产库)。串行安全;并行 per-schema 隔离仍归 I2 正式件。e2e conftest 同款
  内联(pytest 非包模式跨 conftest import 不可靠,接受 10 行双胞胎);
- **新增 `seed_factor` fixture**:种 factor_info + factor_state 一步到位;
  四个测试文件 24 处裸 `store.put(FactorRecord(...))` 全部换装,author 断言
  改读 factor_info(`test_ensure_record` 的 `rec.author` 在 DB 干净后本会换成
  AttributeError 挂,一并修);批量 -u 测试的跨作者用例显式 `author="mhe"`。

**为什么不是直接上 per-schema**:per-schema 要求三 store 支持 search_path 注入
(构造器/池层面改生产代码),属 I2 正式件;当前目标是"验证期间让既有行为测试
跑起来",清库隔离零生产代码改动即可达成。

**前置要求(用户侧)**:ops_test 现在是生产克隆,里面 7607 行会被 wipe 清掉 ——
重建为空库更干净:`DROP DATABASE ops_test; CREATE DATABASE ops_test OWNER ops`
(表由 conftest 引导自建,schema 即三表新世界,不再带迁移期约束名)。

**验证**:本地(无 PG)5 passed / collection 零 import 错;PG 组的真验证依赖
160 复跑(测试基建改动本身没有无 PG 的自证路径 —— 这正是 I2 的病根,记录之)。

## PV3 · 复跑确认(2026-07-08,server-160)

ops_test 重建为空库后复跑:**fast suite 51 passed / 8 skipped / 0 failed**
(8 skip = test_state_store_pg 整组,等 I2 正式件,by design);
**e2e pass 路径 PASSED**(2:27,全 6 条路径至此全部绿)。

意义:submit / restage / cancel / approve / clear / rm / check 的行为测试
**第一次在真 PG 上全绿** —— 三表拆分(2026-07-06)以来这些测试从未真正执行过。
第 1-2 层验证通过,进入第 3 层(金丝雀写路径,重点 R1-R4)。

顺带修正:e2e 实测全套 ~6:40、pass 路径单跑 ~2:27(tests/README 原写 ~85s,
应为早期窗口更短时的测量;数字已更新)。

## PV4 · 只读冒烟结果 + BrokenPipeError 修复(2026-07-08)

**冒烟结果**:`ops list` 7485 个因子(与快照迁移数精确吻合)、3.9s(原 ~25s;
零扫盘达成,余下是启动 + 三查询内存 JOIN + rich 渲染 7485 行,单条 SQL JOIN
的既有 TODO 可再压);JSON 键变更符合文档(has_pnl/dump_days 移除、status 新增);
`ops info` 正常,REJECTED 因子显示 07-04 迁移期存量快照属预期残留(R1 自愈覆盖)。

**修复**:`ops list --format json | head` 触发 BrokenPipeError → 两屏 traceback +
"ops crashed" 日志。下游管道提前关闭是正常 Unix 行为不是崩溃;main.py 加
BrokenPipeError 臂:stdout 换 /dev/null(防解释器退出二次 flush 再炸)+
退出码 141(128+SIGPIPE 管道约定),不打 traceback、不进 crashed 日志。
历史遗留 bug(所有分支都有),在验证分支修复后合并前传。

## PV5 · L3 金丝雀抓到遗留 P1:checkpoint 残留使 re-check 必炸(2026-07-08)

**发现过程**:L3-1 至 L3-5 全过(R2 裸批量拒绝 ✅、R3 曾入库 cancel 拒绝且
staging 完好 ✅、R1 前半离库删快照 ✅)。L3-6 二次 check 在 **checkbias 被拒**:
gsim `StatsSimpleV6.checkpointLoad` 崩 `io.UnsupportedOperation: not readable`。

**根因**(纠正执行者的两个误判:不是"alpha_dump sidecar 缺目录"——那是
on_reject 早期 stage 清 dump 的预期行为;也不是"checkpoint 未实现"——Phase F
是存放位置治理,机制本身工作):**首次 check 的 long_backtest 写的 checkpoint
文件无人善后** —— `CheckpointChecker.clean` 在 checkpoint 阶段后调用,清不到
其后 long_backtest 新写的;to_lib/on_reject 也不碰 checkpoint 目录。因子
restage 重检时,checkbias(短窗口 + dumpPnl=true)的 gsim 去 load 上一轮
全历史窗口的残留 → 崩。**为什么从未暴露**:e2e 每因子只 check 一次、routing
测试用 fake checker,"同一因子真 gsim 二检"路径零覆盖;而它恰是 restage /
submit --overwrite 的必经路径 —— 遗留 bug,非 R1-R4 引入,但堵死了 R1/R4
要保护的 re-check 流程。

**修复**:`_run_one_locked` 开跑前(锁内、transition CHECKING 后)
`shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)` —— 每轮 check 从
干净 checkpoint 目录开始。本轮内 checkbias → checkpoint 阶段的断点续跑语义
不受影响(那些文件在 wipe 之后才写)。

**为什么放锁内而不是 prepare_for_initial**:prepare_for_initial 在 pipeline
__init__ 扫描期执行、在 factor_lock **之外** —— 若另一进程正持锁 check 同一
因子,这里 wipe 会毁掉它正在用的 checkpoint(prepare_for_initial 在锁外改
staging XML 本身就是同族遗留隐患,记入遗留:归 pipeline 后续治理)。锁内
wipe 与"谁检查谁负责清场"语义一致。

**验证**:本地 fast suite 绿(fake checker 路径 rmtree 不存在目录无害);
真验证 = 160 重跑 L3(rm 金丝雀后从 L3-1 重来,两次 check ~6 分钟)。

**PV5 后记(2026-07-08,作者确认 gsim checkpoint 机制)**:恢复状态是两半配套的
—— `checkpoint_path/<name>/archive.bin` + **pnl 文件尾部几行**(按 checkpointDays
回读),下次运行读到两者则只增量跑尾部。L3-6 崩溃即两半错配:archive.bin 是首轮
long_backtest 的,配套 pnl 已被 to_lib 搬走;Stats 以写模式新开 pnl 输出,
checkpointLoad 回读尾行 → 对 write-only 文件 read → io.UnsupportedOperation。
validate 不崩是因 dumpPnl=false 时 Stats checkpoint 路径不激活。锁内 wipe 修复
成立(gsim 无 archive.bin 即全量跑;本轮内 checkbias→checkpoint 续跑两半同轮
配套,不受影响)。

**相邻缺口(记账,验证后再修)**:`prepare_for_archive` 拆雷只重定向
pnlDir/dumpAlphaDir,**未摘 checkpointDir/checkpointDays** —— 入库 XML 仍指向
check 时的 workspace checkpoint 路径;`ops run` 对入库因子重跑没有 wipe,同样
会产生/读取错配 archive.bin。候选修法:archive 时置 checkpointDays=0 或
checkpointDir 也指 /tmp scratch;与 `ops run` 是否该支持 checkpoint 一起拍板。

## PV6 · 第 3-4 层验证收官 + rm 池副本泄漏正修(2026-07-08)

**验证结果**(全文见 `docs/remediation/VERIFY-L3-L4-RESULT.md`,执行者独立产出):
R1-R4 四个 P0 修复在真实生产环境全部 ✅ —— 快照删除/换新的时间戳链
(entered_at/snapshot_at 两轮各自吻合且递增)、裸批量拒绝、曾入库 cancel 拒绝且
staging 完好、re-archive 无 Errno 20;L4 PG advisory lock 跨进程互斥生效;
基线因子数 7485 前后一致。**wave0-2(waves 0/1/2)生产验证完成**,进入多机
升级窗口(升级期间无 in-flight check;之后手动跑 migrate_drop_derived.sql)。

**顺产两个正修**:
- **rm 池副本泄漏**(报告遗留 #1):`to_lib` 按 discovery_method 把 pnl 分流进
  `pnl_automated|pnl_manual`,但 rm 的"彻底删除"不清它 —— 已删因子的 pnl 永远
  留在对比池参与后续 bcorr(金丝雀两轮实测)。修复:rm 删除清单加池副本
  (两池都查,来源可能变过);`test_rm_hard_deletes_all` 断言补齐。
- **sudoers 完整配置**回填手册:单行 NOPASSWD 不够 —— sudo 还会拒绝
  `--preserve-env` 的 OPS_* 白名单,需第二行
  `Defaults!<ops> env_keep += "OPS_*..."`(160 实测踩过);150/144 部署直接抄。

**待用户拍板(不擅自改)**:restage / 离库时是否也应清池副本("离库即出池")——
ACTIVE 因子被召回后,其池副本仍参与别人的 bcorr 对比;restage-rejected 清了
alpha_pnl 但池副本同样残留。语义涉及 bcorr 对比池的定义(池 = ACTIVE 集合?),
归作者决定后与 R5 同型处理。

**用户侧待办**:`sudo rm -f /tank/vault/alphalib/pnl_manual/AlphaWbaiCanary001`
(执行者的 NOPASSWD 只覆盖 ops 入口,裸 sudo rm 执行不了,本次金丝雀的池副本
仍在生产 manual 池里);dropbox 金丝雀目录顺手清。

## PV7 · 离库产物回收:两面模型 + 自鬼影修复(2026-07-08,作者拍板)

**起因**:作者问"都 restage 了,相关产物是不是也都得回收"。顺着推发现这不只是
卫生问题 —— 是**自鬼影相关陷阱**:`run_bcorr` 不排除同名因子、correlation
checker 也不跳过自己,因子 restage/`--overwrite` 后重检时,correlation 拿
**新 pnl** 对池里**自己的旧 pnl** 比(同代码 corr≈1.0 > 0.7)→ 进"高相关须
打败竞品"分支 → `_check_beat` 要求三项中两项**严格更优**,对手是几乎逐点相同
的自己 → 必拒。**生产阈值下 restage→recheck 不改代码的流程会被自己的鬼影
挡死**;金丝雀没撞上纯因 verify config 的 corr_threshold=1.01 绕过了高相关
分支。鬼影还害别人:新因子会被迫"打败"一个已离库因子的旧 pnl。

**两难的化解(作者的顾虑:生产可能在消费 feature vs 不抽走则状态不一致)**:
不一致不是必须消除,而是必须命名 —— 产物分两个面:
- **check 面**(snapshot + alpha_pnl + bcorr 池副本):喂 check 流水线自己,
  **离库即失效、一律回收**(与 R1"离库删 snapshot"同构);
- **服务面**(alpha_dump / alpha_feature):语义 = **最后一次成功入库版本的
  last-known-good**,与 check 状态解耦 —— 重检窗口内生产 combo 继续消费上一
  入库版本(蓝绿式连续性);重检过 → 新产物替换,被拒 → on_reject 清除,
  想立即下架 → `--purge`(旗标正名)。"状态 submitted 但 feature 在"从
  不一致变为"服务面滞后 check 面一个版本"的既定模型。

**改动**:
- `rm.py` 新增 `_recycle_check_artifacts(name, config)`(pnl + 两池副本,
  两池都查 —— 来源可能历史上变过);rm 自身换装;
- `restage._restage_one`:ACTIVE/REJECTED 一律回收 check 面;服务面维持
  "REJECTED 自动清 / ACTIVE 默认保留 + --purge 立即下架";
- `submit_one --overwrite`:删 snapshot 后同步回收 check 面;
- `correlation_checker`:bcorr 结果**过滤自名**(双保险,防删除失败残留;
  也修正 CorrResult.max_bcorr 可能被自身 1.0 污染的问题);
- 测试:restage ×3 / overwrite ×1 补种子与断言(PG 组,160 复跑覆盖);
- 文档:restage/submit/check 三处 CLAUDE.md 落两面模型。

**为什么不是只做自名排除**:排除自名救不了"别的新因子撞已离库因子的鬼影",
池成员资格必须与库成员资格一致(池 = ACTIVE 集合);回收是主修,排除是兜底。

**验证**:本地 import + fast suite 绿;PG 组断言 160 复跑通过(2026-07-08,
51+ passed 含池副本回收/overwrite 回收新断言);行为级验证 = 金丝雀 PV7 专项
(生产阈值 corr_threshold=0.7 下 restage→recheck 不撞自己 + 自名过滤双保险),
执行手册 `VERIFY-PV7.md`。

**行为级验证收官**(2026-07-08 18:40-18:59,160,`VERIFY-PV7-RESULT.md`):
验证点 A/B 双通过。
- A:restage 回收输出/文件断言全符(pnl+池副本回收、dump/feature 保留);
  生产阈值 re-check 走"打败竞品"分支,竞品为库内真因子 AlphaWbaiReversal
  (金丝雀 e2e 模板与其逻辑相同,bcorr=1.0 是真高相关),**不是自己** ——
  按判读规则通过。
- B:手工塞回旧 pnl 后,bcorr 原始输出含自名 1.0,过滤后 max_corr 仍为
  AlphaWbaiReversal,与 A 轮结果完全一致 —— 自名过滤在真 gsim 输出下生效。
- 附带收获:①金丝雀与真因子天然孪生,把"打败竞品"分支在生产阈值下跑了两遍
  (低相关直通分支本次未触发,但它是 L3-L4 及日常的主路径,风险低);
  ② PV7-4 人工塞入的池残留由收尾 `ops rm` 回收(零残留复查过),再次覆盖
  L3-7 修复。PV 系列(PV1-PV7)至此全部收官。
