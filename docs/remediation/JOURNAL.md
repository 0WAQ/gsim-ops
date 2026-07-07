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
| **Wave 1 回退决断** | 删 config.prod-legacy.yaml + json/redis store + sync 栈 + fcntl 回退,或修好它们 | 运营决策:确认不再需要 S3 回退;**先轮换 S3/Feishu 密钥**(已在 git 历史) |
| **Wave 2 僵尸拆除** | list 改纯 PG 判据删 scan、删 derived 层、删 health | 依赖 list 判据切换在生产验证 |
| **I2 测试基建** | per-schema 隔离 + info 种子行 + 契约测试补齐(含 R1 的行为测试) | 需要可达的 ops_test PG |
| 死 config 键清理 | recycle/thres/stats/max_workers/authors/notification/users 等 | 触碰 Config 必填键集,与 G(Config 治理)一起做 |
| bcorr.cpp 归属 | ops 仓里的 C++ 源与 gsim 部署二进制的关系 | 需要作者确认 |
