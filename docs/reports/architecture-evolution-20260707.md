# 架构演化审查:沉积层、多真相源与领域模型重建(2026-07-07)

**定位**:三部曲收官。`project-review-20260707.md` 讲哪里会坏,`code-craft-20260707.md`
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

*五路并行侦查 + 三轮前序审查合成;与 `project-review-20260707.md`(bug)、
`code-craft-20260707.md`(工艺)配套。行号以 HEAD `f733db8` 为准。*
