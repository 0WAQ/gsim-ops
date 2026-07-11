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

## U1 · 多机升级窗口:150/144 → wave0 + 僵尸表 migration(2026-07-08)

**结果**(`VERIFY-UPGRADE-150-144-RESULT.md`):三机(160/150/144)rev 一致
`7f5b710`;跨机 PG advisory 锁(F5 固定命名空间锁键)首次三机真互斥,四观测
(150/144 持锁期 FactorLocked + 释放后 ACQUIRED)全符;`migrate_drop_derived.sql`
备份先行后执行,`\dt` 仅剩三正规表,三机只读回归 Total 7488 不变。Phase G 的
"150/144 部署"与"僵尸表清理"两项落账;`.env` 密码已 scp 分发(正规化挪
/etc root-only 仍待办)。144 走 `OPS_ALPHALIB_ROOT=/storage/vault/alphalib`
env 覆盖(该机制 + sudoers env_keep 首次实战)。

**开窗前事件:144 孤儿 pack(已结案,`INCIDENT-144-PACK.md`)**。阶段 0a 探测
发现 144 有 20 个 ppid=1、7 天龄的 `ops pack` worker(root)。取证定性空转僵尸
(cputime 冻结 + fd 无写句柄);其 7/1 写入共享 JFS 的 5757 个 feature 经代码侧
核实为 **backfill 非覆盖**(裸 pack 无 --force 跳过 v1+v2 齐全者,即这些 feature
此前不存在);wbai 确认本人操作、alpha_dump 任意时刻/机器等价 → 无污染,结案。
遗留:3 个 `.tmp` 残渣(root 清)+ 窗口后在 IDC 机器补一次裸 `ops pack`
(队列未跑完的缺失 + 2 个半对)。

**过程教训与记账**:
- **测试串行红线被实测验证**:144 一个误判为已死、实际后台存活的 pytest 与
  150 的测试撞共享 `ops_test`(wipe_test_db 互清),150 一轮 3 failed;kill +
  严格串行后三机各 51 passed/8 skipped/0 failed。→ I2 正式件(per-schema/
  per-machine 测试库隔离)的又一动机。
- **pack 两笔账进 ops doctor / 整改候选**:①孤儿 worker 是无人兜底的 crash
  residue 类别(staging 有 `ops clear`,pack 没有;现版本同为 ProcessPoolExecutor
  风险仍在);②pack 无数据源/机器角色守卫,冷副本一条裸命令即可写共享
  alpha_feature → 候选:批量写入前 apt 风格确认(与其它批量命令对齐)。
- **WAN 基线(144)**:fetch/pull 20s;`uv sync` 需 `UV_HTTP_TIMEOUT=180`
  (~6min);L1 测试 335s(IDC 的 ~40×);`ops list` 10s。
- **入口与文档勘误**:150/144 生产入口是 uv tool install 的 `ops`,项目 venv
  无 console script(`uv run ops` 不可用,与 160 不同);`ops list` 作者过滤是
  `-u/--user`,根 CLAUDE.md 示例笔误已修(手册头部已加勘误注)。

**后续解锁**:验稳后清 Redis 残留 state key(只 DEL state:*,绝不 FLUSHDB);
PG 密码正规化;wave3/stage-table 增量验证与三机滚存;补 pack;MinIO/Feishu
密钥轮换(与本窗口无关,持续挂账,紧急)。

## U2 · wave3 + stage-table 增量验证收官:三机滚存到堆叠顶端(2026-07-09)

**结果**(`VERIFY-WAVE3-STAGE-TABLE-RESULT.md`,rev `a85c26b`):
- 160 门禁全绿:ruff 0 / pyright 0 / fast suite **69 passed**(增量 =
  `test_batch` + `test_check_routing_json`,数目与 wave0 的 51 相加吻合);
- **e2e 重跑 6 passed / 90s** —— Stage 表重构(运行块 for-loop 化 + 归因盖章 +
  prepare 抛错)在真 gsim + 真 cc 数据下无回归,这次确认跑在含 `stages.py`
  的代码上;
- 金丝雀环路 3a-3e 全过(证据为原始输出):批量确认交互(n 零副作用 / y 走
  锁内复验+CAS)、生产阈值 REJECTED 的**归因盖章**(fail_stage=correlation
  由流水线 current_stage 盖,异常子类已删)+ late-stage 产物策略(pnl+dump
  保留 / 不拷池 / 无快照)、REJECTED 召回闭环再入库(快照重建);
- 150/144 从 wave0 `7f5b710` 滚存,**三机 rev = `a85c26b`**,fast suite 全绿
  (144 WAN:uv sync 161s 需 UV_HTTP_TIMEOUT=180、pytest 61s);
- 144 pack 事件遗留收口:tmp 已清 + 补 pack 39 → 0(`INCIDENT-144-PACK.md`
  关闭)。**合 main 前置齐备。**

**上一份 wave3 报告的勘误**(已记入 RESULT 首节):其"三机 rev 一致
(150/145 @ 4dec7a6)"经逐台核实不成立(150 实为 wave0 `7f5b710`;145
NO-REPO,该机是 JFS 对象存储落盘机,无 ops 部署),相关声明作废;该报告降级
为"160 单机 wave3 证据"(e2e + 金丝雀迭代可信)。教训:**验证报告必须贴命令
原始输出,转述不作数**(该报告另有两处照原样执行必报错的快照命令)。

**验证中挖出的新问题(待办)**:
1. **bcorr 池存量鬼影实锤**:`AlphaWbaiReversal` 当前 status=rejected,但
   `pnl_manual` 池副本与 07-04 旧 snapshot 仍在(离库发生在 PV7 回收代码部署
   之前,存量残留)。后果演示:金丝雀 3c 正是被这个**已离库因子**的鬼影 pnl
   拒掉的 —— 不在库里的因子在给新因子把关。PV7 回收只管未来动作;存量需一次
   性对账:池成员 − ACTIVE 集合 = 鬼影清单(只读审计脚本已交 wbai),确认后
   清理;长期机制归 `ops doctor`。
2. 手册改进:金丝雀 dropbox 重建 snippet 补 `rm -rf`(本轮 3a 实测撞上一轮
   残留被 ".xml=2" 拒收 —— submit 行为正确,夹具不卫生;VERIFY-PV7.md 已改)。
3. 小账:PG 核对 snippet 退出时有 psycopg `ConnectionPool.__del__` 析构噪声
   (无害;后续给连接池加显式 close)。

## U3 · PG 连接打爆事故 + cancel 孤儿缺口 + Wave5 阶段 0/1 落地(2026-07-09)

**事故一:PG 连接打爆(P0,生产实测)**。`ops check` 在 `run_one` 里**每因子**
构造 state/info/snapshot 三个 store,每个 store `ConnectionPool(open=True)` 新建
一池占 1 连接、到 worker 进程退出才释放 —— 20 个 fork worker 处理大批因子把
PG(默认 `max_connections=100`)打满,`FATAL: too many clients already`,连带
cancel 等命令全部连不上;check 在途因子 PoolTimeout → revert SUBMITTED(无数据
损坏:连接错误只会让写"发不出去",不会写出错值)。**修复分两个 PR**:
#3(atexit 退出收尾,治 `__del__` 刷屏 —— 但对运行中途累积无效,单独不治本)、
#4(`get_pool` 按 (pid, conninfo) 去重,三表同库塌成一池,worker 连接 3K→1;
`ensure_schema` 每池一次)。生产复跑确认无泄漏。相邻账:check 每因子建 store
的对象翻新仍在(连接已无害),收编归 Repository(阶段 2)。

**事故二:cancel 留孤儿(缺口,生产实测 143 个)**。恢复操作中
`submit --overwrite`(QR 重提)把一批**曾 REJECTED** 的旧因子转回 SUBMITTED,
随后批量 cancel 删除记录 —— R3 的 `entered_at` 守卫按设计放行(它们从未入库),
但这些因子在 REJECTED 时 src 已归档进 alpha_src(late-stage 拒绝还留 pnl/dump),
cancel 只删记录 + staging,**alpha_src 归档变成任何命令都够不到的孤儿**(143 个
4月因子目录)。数据零丢失(alpha_src/dropbox 均在),经 submit --overwrite 重提
恢复。**修复**:cancel 资格判定新增产物守卫 —— `alpha_src/<name>` 存在即拒绝
(单因子报错 / 批量 skipped),指引 `ops rm`(全落点删除)。同时排除了
"cancel 守卫有洞"的怀疑:PG transition 是读-改-写,entered_at 在 overwrite 中
被保留,曾 ACTIVE 因子始终被正确拦截。

**Wave5 阶段 0 + 阶段 1 大半**(`docs/factor-aggregate-plan.md` 勾选为准):
- import-linter 进 CI:C1/C5/C6/C7/C8 **五份 enforcing**(先清了 C1/C5/C6 的
  5 条边:CACHE_ROOT 正主迁 `utils/cachedir.py`、sudo 去 rich、core 两处 Config
  转 TYPE_CHECKING)+ C2=18/C3=9 ratchet 基线(`scripts/ci/import_baseline.py`,
  只降不升,清零转 enforcing);
- D4 改名 ScannedFactor/author_guess;D3 `infra/errors.py`(StateConflict 迁居、
  FactorNotFound 替代裸 KeyError、snapshot delete → bool);
- 阶段 1 剩:FactorPaths(单独一轮)、DDL 滚出 store `__init__`。

**对抗评审追加修复**(13 agent 多维评审 + 逐条证伪,0 条误报):
- **P1:rm 不删 staging → 守卫指引"用 ops rm"会复活因子**。被 cancel 守卫拦下
  的因子 staging 里必然有代码;rm 后记录没了但 staging 留着,`ops check` 按目录
  扫描 + `_ensure_record` 自动补建记录,刚删的因子被复活重新入库。此坑自 R3 的
  entered_at 守卫指引 rm 起就存在,本次被放大后修正:**rm 的"全落点"清单补
  `staging/<name>/`**(确认清单 + 删除动作 + 测试断言同批)。
- **P1:ratchet 脚本 fail-open**。首版不查 returncode、不校验契约头出现 ——
  lint-imports 自身跑挂(配置丢失/TOML 语法错/包名漂移)解析出 0 条 < 基线 →
  exit 0,门禁静默失效。加固为 fail-closed:rc ∉ {0,1} 挂、无 "Contracts:"
  汇总行挂、BASELINE 契约头未出现挂、未知契约头重置归属;四条故障路径实测
  全部 exit 1。
- P2×5:cancel/submit/rm 三份 CLAUDE.md 同步产物守卫与 rm↔cancel 新分界、
  plan §2.4 D3/D4 回填、附录 A 补 exclude_type_checking_imports 指引。

**验证**:ruff/pyright 0、enforcing 契约 5/5 绿、基线脚本锚定 + 四故障路径
fail-closed、fast suite 绿;cancel 守卫 2 个新用例 + rm staging 断言(PG 组,
待 160 复跑);e2e 待 160。

**阶段 1 收尾:FactorPaths(同日第二批)**。`ops/core/paths.py` 成为盘面布局
唯一正主(SSOT S4):40+ 处 `config.alpha_xxx / name` 拼接全量收编清零
(rm/restage/cancel/clear/submit/run/info/check/pack/library scanner),pack 的
ProcessPool worker 改直收冻结可 pickle 的 FactorPaths;"pnl/池副本/feature 是
单文件、src/staging/dump 是目录"从文档警告变为类型承载;meta.json 文件名常量
统一(backfill/run.find/check 三处私有字面量并入)。布局契约测试
`tests/test_factor_paths.py`(4 用例,无 I/O)。根 CLAUDE.md SSOT 表"盘面布局"
行转正。**阶段 1 至此完成**(DDL 滚出 store __init__ 一项并入阶段 2)。

**FactorPaths 对抗评审(第二轮,6 agent)确认 1 个真 P2 并修复**:to_lib 的
src 归档三步中,`rmtree(paths.src)` / `rewrite_module_path(paths.src)` 迁移后锚定
`factor.name`(XML @id),而 `shutil.move(factor.dir, config.alpha_src)` 落点仍由
**目录名**决定 —— 非等价替换。目录名 ≠ @id 时(手工放置 staging / 中断 submit
留下 stale XML,normalize_factor_xml 只在 submit 强制 @id := 目录名),rmtree 删
的是 alpha_src/<@id> 即**另一个在库因子的唯一源码**(不可逆),rewrite 随后静默
no-op。修复双闸:① `run_one` 入口在**任何状态写入之前**(_ensure_record/
transition 之前,否则光"跑一下"就把 @id 撞名的在库因子打成 CHECKING)检查
`factor.dir.name == factor.name`,发散整单拒绝返回 error;② to_lib 兜底断言 +
move 落点显式改 `paths.src`(rmtree/move/rewrite 三步同锚点,不变量由代码承载);
dump 的 move 落点同步显式化为 `paths.dump`(等价改写:alpha_dir 本就按 @id 命名,
但消灭 directory-target 落点语义)。新用例
`test_identity_divergence_refused_before_state`(imposter 目录 + 在库 victim,
断言零状态写入、victim 源码毫发无损、零 stage 执行)。

## U4 · Wave5 阶段 2:Factor 聚合 + FactorRepository,C3 清零转 enforcing(2026-07-09)

**一句话**:领域模型立正主 —— 全库第一次有了叫"因子"的类型(`ops/core/factor.py::
Factor`,identity/state/snapshot 三切面聚合),存储门面 `ops/infra/repository.py::
FactorRepository` 收编三表读写与产物清理;C3(service 包独立)9 条violation边
全消,**契约清零转 enforcing(6/6 绿)**,ratchet 基线只剩 C2=18。

**九条边的四组消法**:
1. **datasource ×4**(submit.parser/submit/backfill/check → list.datasource):
   整模块迁 `ops/core/datasource.py`(`_build_npy_index` 正名 `build_npy_index`),
   `services/list/datasource.py` 物理删除。
2. **parser ×2**(clear/backfill → submit.parser):`parse_factor` /
   `infer_author_from_dir` 迁 `ops/core/factormeta.py`(产物与构造器同模块;
   Config 走 TYPE_CHECKING 纯类型引用),`services/submit/parser.py` 物理删除。
3. **purge/recycle ×2**(submit/restage → rm.rm):收编为
   `repo.purge_artifacts(name, ArtifactScope)` —— PV7 产物两面模型进类型
   (CHECK=pnl+池副本,离库一律回收;SERVING=dump+feature,--purge/REJECTED 才清)。
4. **approve→check ×1**:CORRELATION 常量迁 core/state.py + 语义 API
   `FactorRecord.correlation_rejected()`;stages.py re-export、PIPELINE 引同一
   常量(单一定义;顺序/路由 SSOT 仍是 stage 表)。

**Repository 记录面**:`get`(Factor 全景)/`find`(**单条三表 LEFT JOIN**,退役
`query_factors` 三次查+内存合并,`infra/query.py`+`FactorRow` 物理删除;snapshot
下推表达式经 `snapshot_where`/`metric_order_expr` 与单表 list 共享,S8 的 SQL 半边
不再镜像)/`register`(**info+state 单事务原子写**,收编 submit/backfill/
check._ensure_record 三份手抄 —— 原先顺序两次调用,崩中间留"有 info 无 state"
半截)/`record`/`transition`/`append_check`/`attach_snapshot`(snapshot_at :=
entered_at 由 repo 强制,调用方不再自填;stale 自愈随迁)/`discard_snapshot`/
`delete`(info 级联)/`exists`(=info 有行,消"问 state 删 info")/`lock`。
消费方迁移:list/info/approve/restage/rm/submit/backfill/check 八处;approve、
restage 的批量 resolve 从"两次查+内存交集"变单条 JOIN。

**Factor 聚合的不变量修正**(对施工图 §3.1):"ACTIVE ⇒ snapshot 非空"在本域
**不成立** —— `ops approve` 合法产生无快照的 ACTIVE(REJECTED 不写快照,approve
只翻状态)。软校验收窄为 "snapshot 存在 ⇒ snapshot_at == entered_at"(warn 不
炸,U2 鬼影在读路径现形)。

**DDL 滚出 store __init__**(阶段 1 顺延件):store 构造零副作用;
`ops/infra/schema.py::ensure_schemas` 按 FK 依赖序(info→state→snapshot)引导,
Repository 首次触达 PG 懒调用、tests fixture 显式调用 —— 原先空库上"先构造
state store 就 UndefinedTable"的隐式顺序依赖消失。顺带 `ts_in/ts_out` 双镜像
收敛 `infra/pg.py`。**语义变化留痕**:不经 Repository 直用 `default_*_store`
的路径(status/cancel/pack)在**全新空库**上不再自动建表 —— 生产三表已存在,
新环境走 scripts/postgres 或任一 repo 路径命令。

**fork 安全设计点**:check 的 `_repo()` 每调现构造、不挂 self —— 父进程实例若
已 materialize 懒加载 store,其 PG 池引用在 fork 子进程里是死的(pg.py 的 fork
钩子只重置注册表,救不了已捏在手里的引用);现构造 + get_pool 按 (pid, conninfo)
去重 → 子进程拿自己的池。

**json 后端可测性红利**:register 只写 state、discard no-op、get 合成 identity ——
check `_ensure_record` 不再硬碰 PG info store(routing 测试原先被迫逐用例 seed),
新增无 seed 的 crash 恢复用例。新测试 `tests/test_repository.py`(json 组 7 用例
CI 常跑 + PG 组 5 用例待 160)。

**顺延记账**:`repo.archive/recall`(收编 check.to_lib / restage 的搬运+XML 重指
+pnl 分流)移阶段 3 首批 —— to_lib 刚做完身份发散 P2 修复,金丝雀环路未复跑,
一次只动一件事。**待 160**:PG 组(test_repository 5 + 存量)+ e2e + 金丝雀环路。

**对抗评审(第二轮,14 agent:6 维度评审 + 8 finding 逐条证伪)**:证伪 1、
确认 7(全 P3,合并同题后 6 项),全部当轮修复:
1. **懒引导缺口**(两 reviewer 撞车):`repo._state` 不触发 `ensure_schemas`,
   而 submit/backfill/check 的第一个 PG 触点恰是 `repo.record` —— 空库上
   UndefinedTable,与"首次触达 PG 自动引导"的承诺不符。修:`_state` 在 PG 后端
   下先触 `_conninfo`;check 的裸 `default_store` 全部改经 Repository。
2. **check 前置段异常逃逸 + LiveDriver 挂死**(HEAD 既有):`_run_one_locked`
   的 try 只包 stage 循环,之前的 `_ensure_record`/`transition` 在 PG 不可达时
   异常穿透 ProcessPool;`_watch_futures` 只对 in-flight 行合成 done,全表
   PENDING(20 worker 全崩在前置段)时 remaining 永不归零 → 命令挂死画面冻结。
   修:`run_one` 兜底泛捕获(worker 是唯一能归因 future→因子名的地方)+
   `_watch_futures` 补 pending 回退。
3. **`_persist_derived` try 边界扩大**:attach 内的 state 读被吞 → PG 瞬时故障
   从"unexpected 臂 revert 自愈"变"静默入库且不可变快照永久缺失"。修:state 读
   (`repo.record` + entered_at 守卫)提回吞噬 try 之外,恢复旧可靠性语义。
4. **glob→LIKE 下推丢行(S9,HEAD 既有)**:`?`/`[seq]` 被当 LIKE 字面量,
   SQL 预筛比 fnmatch 更窄,行被永久丢(违反"下推纯为预筛")。修:
   `_glob_to_like` 转义 `\ % _`、`?`→`_`、含 `[` 整体放弃下推 —— 共享
   `snapshot_where` 一处修,list 单表与 find JOIN 两径同治;**S9 至此落地**。
5. **typo 比较符静默吞**:`ret=>30` 过正则但两侧都无分支,旧路径淘汰无快照
   因子、新路径保留 —— 双静默且不等价。修:`parse_filters` 白名单校验响亮拒绝。
6. **e2e conftest 未随 DDL 滚出补引导**:独跑 `-m e2e` 在全新 ops_test 上
   UndefinedTable。修:`gsim_available` 探测通过后 `ensure_schemas`。
新用例 4 个(typo 比较符 / glob 预筛纯度 / 前置段崩溃发 done / 全 PENDING
解锁)。门禁终态:46 passed、契约 6/6 KEPT、C2=18 持平、pyright 0。

## U5 · Wave5 阶段 3 第一批:cli 接缝 + 7/7 契约 enforcing,ratchet 退役(2026-07-09)

分支 `claude/factor-aggregate-phase3`(基于阶段 2 tip)。

**C2 清零转 enforcing**:18 条边(14× `get_default_config_path` 手抄 + 4×
FactorStatus choices)收敛到 `ops/cli/common.py` 单一接缝(re-export
FactorStatus + STATUS_CHOICES + add_config_arg),pyproject 契约定点 ignore
common 两条 import —— 其余 cli 文件再碰 infra/core 即红。**至此 7/7 契约全部
enforcing**,ratchet 基建(contracts-baseline.toml + scripts/ci/import_baseline.py
+ CI step)完成历史使命,物理删除。

**命令塌缩(第一批)**:`find` 加 `include_submitted`("任何记录"语义)后 ——
status 塌缩到 repo.get / repo.find 单条 JOIN(退役 store.list + info.list 内存
合并;json 后端单因子模式从"构造 info store 即炸"变为可用);cancel 迁
Repository,删除从"先 state 再 info"两步改 `repo.delete` 级联一步(崩在两步
之间泄漏孤儿 info 行的窗口消灭);pack 的 -u/--status 过滤同款塌缩。
approve/restage/rm/info/list/backfill 已在阶段 2 迁毕。

**类型普查收官**:live_table 的展示类 FactorRow → `LiveRow`(与领域 Factor
区分);全仓 grep 无 FactorRow / 无扫盘 FactorInfo,12 投影 → 计划内的保留集
(Factor 三件套 + FactorRecord + AlphaMetadata/FactorMeta/ScannedFactor)。

**顺延**:archive/recall 收编(to_lib/restage 搬运)仍等金丝雀环路在 160 复跑
通过后做(阶段 3 第二批)—— 归档路径连续改过身份守卫 + repo 迁移,未经生产
验证前不再叠加改动。**待 160**:PG 组(新增 include_submitted 用例)+ 金丝雀
+ e2e + 三机滚存冒烟。

**对抗评审(阶段 3,14 agent:4 维度 + 逐条证伪)**:确认 4(全 P3)、证伪 1,
当轮全修:
1. **find 的 factor_state 边是 INNER JOIN**:"任何记录"语义漏 info 孤儿
  (info 有行 state 无行 —— register 事务化前的半截写入,7-06 迁移实测 20 个),
  本批新增的 status/cancel"需对账"分支不可达、docstring"三表 LEFT JOIN"对
  state 边不实。修:改真 LEFT JOIN + `_row_to_factor` 对 NULL 行产出
  state=None;缺省因子集判据(`!= 'submitted'` 对 NULL 为假)天然排除孤儿,
  ops list 不受影响;status 单因子模式对孤儿给显式提示(原打"未找到")。
  新 PG 用例 `test_pg_find_surfaces_info_orphans`。
2. pyproject 契约节头注释仍教已删除的 ratchet 流程 → 改述 7/7 enforcing。
3. plan 附录 A 的"正主"声明仍指 contracts-baseline.toml → 同步。
4. 提交纪律:8 个文档改动须与代码同批 staged(commit 前 git add -A 兜住)。
另修 memory 两处漂移(query_factors/内存交集的过时描述,评审与 docs-updater
双双诊断)。门禁终态:46 passed、7/7 契约 KEPT、pyright 0。

**160 验证收官(2026-07-10,VERIFY-AGGREGATE-P2P3-RESULT)**:执行者两轮闭环 ——
首轮按红线停在 fast suite 2 failed(test_check_scan 两用例给 _ensure_record 传
裸 store,签名迁移漏更的陈旧夹具;PG 组"本地 skip、160 真跑"的盲区实证),
b0b548e 修复后第二轮全绿:101 passed / e2e 6 passed / 只读冒烟 Total=8252 持平
(`=>` typo 报错原文命中)/ 金丝雀 4a-4g 全过(register 原子 stamped=t、check 期
连接数 1、correlation 归因与自名过滤判读、approve 合法无快照 ACTIVE、rm 级联
三表零行、cancel 级联零孤儿 info)。**阶段 2 + 阶段 3 第一批至此生产验证完毕**;
archive/recall(阶段 3 第二批)前置解除。侧记:AlphaWbaiReversal(rejected)仍在
pnl_manual 池 —— bcorr 池存量鬼影挂账再次现形(审计 snippet 已交,待清)。

## U6 · Wave5 阶段 3 第二批:archive/recall 收编 + S16 写命令派生(2026-07-10)

分支 `claude/factor-aggregate-phase3b`(基于合并后 main b17de9c;阶段 2 + 阶段 3
第一批经 PR #5 合入)。

**产物面收编完成**(施工图 §3.2 的 stage/unstage/archive/recall 中,有真实
消费者的三个):
- `repo.archive`:check.to_lib 的全部搬运(src→alpha_src + @module 重指 +
  dump/pnl 搬库 + 按来源分流池副本 + pnl 单文件三分支)收编 Repository,
  **身份兜底断言随迁**(第一道闸不动,仍在 run_one 入口);to_lib 变薄调用。
- `repo.recall`:restage 的搬运半边(存在性/占用守卫 + move + 重指)收编;
  文件数校验、产物两面回收、CAS transition、discard 仍是 restage 政策。
- `repo.unstage`:cancel/clear/rm 三处 staging rmtree 收编(返回 bool,幂等)。
- 有意不建:`stage`(submit 的 dropbox→staging copy 含覆盖警告 UX,留 service)、
  `iter_*/orphans/meta`(doctor 未立项,不预建空壳 —— W3 幽灵教训)。

**S16 完成(SSOT 表 ⚠ 行清零)**:`sudo.py::WRITE_COMMANDS` 手抄集合删除,
写命令在注册处 `mark_write(parser)` 声明(cli/common),`maybe_elevate` 消费
`args.is_write_command` —— 单一定义;`run` 曾因手抄名单缺席在 JFS 下 EACCES
(full-review 1.2),此类漂移从机制上消灭。声明集测试钉住 10 命令。

**测试**:json 组 +5(archive 搬运/分流、身份拒绝、recall 往返/守卫、unstage
幂等、写命令声明集)。门禁:51 passed、7/7 契约 KEPT、pyright 0。

**待 160**:金丝雀环路复跑(archive/recall 是 to_lib/restage 的等价收编,
须按 VERIFY-AGGREGATE-P2P3 阶段 1/2/4 在本分支重验后方可合 main)。

**对抗评审(第二批,4 agent)**:archive/recall/unstage 两个对等维度 **0 finding**
(逐位对等成立);唯一确认 1 个 P3 —— S16 声明集测试手抄了 main.py 的 14 个注册
函数,新命令不会自动进测试(又造一面镜子)。修:注册表提升为
`ops/main.py::SUBPARSER_REGISTRARS` 模块级单一正主,main 与测试共同迭代它,
测试只钉"哪 10 个是写命令"这一个真正的决策。门禁终态:51 passed、7/7 契约、
pyright 0。

**160 验证收官(第三轮,2026-07-10)**:phase3b(c280b18)按 P2P3 手册复跑
阶段 1/2/4 全绿 —— 106 passed / e2e 6 passed / 金丝雀 4a-4g 与第二轮**逐位一致**
(4a stamped=t、4b 连接数 1、4c 回收+归因+产物策略、4d approve、4e rm 三表零行、
4f cancel 级联零孤儿)。archive/recall/unstage 收编与 S16 的行为等价性获生产
实证;合 main 前置齐备。

**三机滚存收官(2026-07-10)**:PR #5/#6 合 main 后,160/150/144 全部对齐
`4ffa4fd` —— fast suite 三机各 106 passed(144 走跨段路由 48s 属预期)、
Total=8252 三机一致、lint 7/7。混版本兼容性判定获实证(150/144 自旧 main
8455a66 直升)。**Factor 聚合工程阶段 0-3(两批)至此全部入产**。剩余收官件:
8 命令行数核对、S8 list 内存 metric 镜像;挂账不变:MinIO/Feishu 密钥轮换
(最紧急)、bcorr 池鬼影清理。

## U7 · 小件收官批:S8 注册表 + AlphaMetadata 去 I/O + results 空壳 + created_at(2026-07-11)

分支 `claude/factor-aggregate-smalls`(基于 main 2507f40)。四个小件一批,全部
是"linter 抓不到、需 review 守"清单上的项:

**S8 收官(SSOT 表最后一个 ⚠ 行清零)**:metric 事实族(键集 + 取值语义)唯一
定义收敛到 `ops/core/metrics.py::SNAPSHOT_METRICS` 注册表(MetricSpec:column +
absolute)。三个消费方全部派生:snapshot pg_store 的 SQL 下推表达式(原
`_METRIC_EXPR` 手抄映射删除)、list 内存兜底 `metric_value`(原 `_metric_get`
镜像删除,`_SORTABLE_KEYS` 由注册表生成)、CLI `--sort-by` choices(原 cli/list.py
手抄键列表 —— 注册表外的第三份拷贝 —— 经 cli/common `METRIC_SORT_KEYS` 派生,
C2 接缝新增一条定点 ignore)。测试钉两件事:键集决策本身 + bcorr 的 abs 语义
SQL/内存逐位一致。

**AlphaMetadata 去 I/O(按计划边界的降级路径执行)**:alpha_dump 工作区扫描
迁出 `ops/services/check/checker/dumpscan.py`(v2npy_files/last_v2npy_file 两
函数,唯一消费方 compliance/checkpoint 所在包);`get_last_v1npy_file` 连同唯一
"调用方" `_get_v1md5` 是死代码,直接删除。dumpscan 不再裸吞 OSError(目录缺失
返回空是正常语义,真错误冒泡 unexpected 臂,两 caller 终态等价 —— 均 revert
SUBMITTED);`last_v2npy_file` **保持"只看最新月份"的原判定语义**(初稿曾加
跨月回退,对抗评审 B/C 两路独立指出会把陈旧残留卷进 checkpoint 断点比对 ——
误 REJECT / 误 PASS 两个方向都有边缘可达路径,而回退对两个消费方零收益,已
还原;仅保留非日期目录过滤的健壮性修正)。同布局的另两份走查在 core/library.py
(doctor 对账域),收敛归 ops doctor 工程,dumpscan docstring 已交叉引用。
构造读盘解析 XML 属工作台语义,按"明确不做"记账保留。

**results 空壳清理(病灶 V)**:`checkpoint.py` 整文件删除(PointResult/
PointStatus/PointResults,CheckpointChecker.check 返回 None);Status 空 Enum、
Results 空集合类及 CompStatus/CorrStatus/CompResults/CorrResults 六个子类删除
(定义至今零消费);`Result` 保留为标记基类(Checker ABC 契约),CompResult/
CorrResult 两个真实结果保留。

**created_at 两径格式收敛**:`PostgresInfoStore` 读路径改 `ts_out`(原直接
isoformat 带 +08:00 后缀,repo.get 与 repo.find 的 identity.created_at 格式
不一致);写路径 `upsert_on` 改 `ts_in`(naive ISO string 打本地 tz,与 state/
snapshot store 同款),缺省回退带 tz 的当前时刻。

**"<20 行"核对结论**:8 命令单因子存储编排全部达标(_clear_one≈2/_cancel_one≈13/
backfill_one≈20/_approve_one≈21);run_* 入口 24-67 行,超出部分全是批量骨架与
rich 渲染 —— 归"展示层上收"独立中件,非存储编排残留。

**批内对抗评审(6 finder:逐行/删除行为/跨文件/复用简化/效率深度/规范)**:
确认修 3 —— last_v2npy_file 跨月回退还原(上述)、info store created_at 缺省
改 `ts_in(created_at or now_iso())` 单一路径(初稿内联 datetime.now().astimezone()
绕开时间戳 SSOT,复用角度指出)、文档"三方法迁"措辞与代码(两函数)不符;
记账不修 2 —— dumpscan 与 library.py 布局走查重复(doctor 域)、list 展示列
`_bcorr` 读带符号原值属展示关切(注册表管过滤/排序的 abs 语义,评审确认非镜像)。
证伪若干:metric 注册表 SQL/内存/CLI 三方逐位等价、结果空壳删除零残留引用、
checkpoint 返回值本就被流水线丢弃。

门禁:7/7 契约 KEPT(C2 ignore +1)、ruff 干净、pyright 0、fast suite 53 passed
(+2:注册表/dumpscan)。行为面变化仅两处,均为修正:OSError 不再静默吞
(终态等价)、created_at 格式统一(读侧 naive local ISO 与 repo.find 对齐,
写侧缺省走 now_iso;全 repo 无消费方解析旧带 tz 格式,评审 A/C 双路核实)。
PG 组用例与金丝雀环路留给执行者环节兜底。

**160 验证收官 + 合 main(2026-07-11,VERIFY-AGGREGATE-SMALLS-RESULT)**:执行者
一轮全绿 —— 静态门禁三绿(空壳删除断言实际抛 ImportError 而非手册预期的
ModuleNotFoundError,执行者正确判定同根因等价:`from pkg import name` 写法本就
抛 ImportError,是手册设想的 import 写法之误,非偏离);fast suite 108 passed
(基线 106 + 新增 2,两个点名新用例单独复跑 PASSED);e2e 6 passed;只读冒烟
Total=8252 持平、`=>` 与 `--sort-by delay` 报错原文命中、**bcorr 过滤三方交叉
一致(ops list json 8097 = PG `abs(max_bcorr)>0.3` count 8097)**;金丝雀 7 stage
全通(checkpoint/compliance 走 dumpscan 的生产实证)、created_at SELECT
`02:16:47+00` = 本地 `10:16:47` CST 同一时刻 fresh=t、rm 三表零行、Total 回基线。
PR #7 合 main(merge `3119496`)。侧记:160 仓库根有未跟踪 `pgreadonlysetup.sql`
(含 ops_reader 密码),已提醒挪出仓库目录防误提交。**待办:三机滚存对齐 rev。**

**三机滚存收官(2026-07-11)**:160/150/144 全部对齐 main `cda9dbb`,fast suite
三机各 108 passed / 0 failed(144 62.5s 跨段读盘属常态),lint-imports 三机 7/7,
`ops list` Total 三机一致 = 8252。侧记:150/144 首轮 pull 撞 GitHub 出网故障
(curl 28 / GnuTLS -110),wbai 手动 pull 后续步照跑,纪律无偏离;150/144
非交互 SSH 用 `~/.local/bin/ops` 绝对路径(uv tool 独立环境)。
**小件收官批三机入产 —— Factor 聚合工程(阶段 0-3 + 收官批)全部关单**:
SSOT 表零 ⚠、7/7 契约 enforcing、Known Technical Debt 的 stub / metadata-I/O
两项清零。挂账优先级不变:①MinIO/Feishu 密钥轮换(最紧急)②bcorr 池存量
鬼影清理 ③中件三项(展示层上收 / ops doctor / I2 测试基建)。

**部署事实校正:staging 是本机 sidecar,不共享(2026-07-11,用户指出 + 160/150
实测)**:`ls -la` 两机一致 —— `alphalib/staging -> ../alphalib.local/staging`
(软链,zfs 本机盘),与 alpha_dump 同款部署(2026-07-08 只记了 dump,漏了
staging)。**真共享的只有 alpha_src / alpha_pnl / alpha_feature**。推论:check
绑定 submit/restage 所在机器(staging 是唯一副本且本机);"三机并发 check 同一
因子"的跨机锁原立项表述不成立(锁必要性不变:跨机撞的是共享 state/产物)。
已修:根 CLAUDE.md(软链约定 + 锁理由)、config.yaml 两处注释、clear/check/
restage 层文档、memory factor_lock 带更正注。scripts/juicefs-poc/README.md 305
行一直是对的(文档漂移源头是 CLAUDE.md 侧)。**新挂账**:PG 状态不记因子躺在
哪台机器的 staging(doctor 候选);此事实是"多机 submit/check 自动化"功能
(讨论中)的设计前提。
