---
name: project-cli-command-redesign
description: CLI 子命令合理性重审的结论 —— submit 吸收 resubmit、recheck 改名 restage 等
metadata: 
  node_type: memory
  type: project
  originSessionId: 02d7c1d1-953d-4031-8f88-5db321590f79
---

2026-07-04 与用户系统性重审 ops 子命令合理性(不谈实现,只谈命令边界是否合理)。核心判断:**这套命令是"贴着实现长出来的",切分轴选错了 —— 多处用系统内部状态(因子存不存在、入没入过库、代码变没变)当命令边界,逼用户替系统做判断。**

**已落地的两个重构**(2026-07-04, branch `feat/derived-postgres`, commit 84de970 + 6c158fd):

1. **submit 吸收 resubmit,合并为一个命令。** ✅ done。submit 与 resubmit 骨架几乎相同,差异仅镜像守卫 + version 加不加一。合并后 submit 内部按 `store.get(name)` 分派:新因子 put version=1;已入库因子**默认跳过**(submit-新因子心智 + 破坏性 opt-in),`--overwrite` 才 version+1 覆盖。`submit_one` 返回三态 pass/skip/fail。顺带修分裂 bug:overwrite 路径也获得 discovery_method 校验 + npy_index 共享。删 cli/resubmit.py + services/resubmit/,不留别名。

2. **recheck 改名 restage。** ✅ done。纯改名,行为不变。cli/recheck.py→restage.py、services/recheck/→restage/、run_recheck→run_restage、_recheck_one→_restage_one、子命令 recheck→restage。**关键:sudo 写命令声明同步(2026-07-10 起 WRITE_COMMANDS 手抄已删,写性在 cli 注册处 mark_write 声明)(漏了 restage 不自动提权,root-owned alpha_src EACCES)**。restage 名副其实——它不跑回测,只召回 alpha_src→staging 翻 SUBMITTED 等下次 ops check。

3. **rm 从软删墓碑改为彻底硬删 + 移除 DELETED 状态。** ✅ done。DELETED 当初是"方便留存"的软删标记,但后续需求证明无用。用户的状态机哲学:因子要么存在(active/rejected/未来 decay 等),要么被删而不存在——删除不是一种状态。`ops rm` 现在彻底删因子 6 个落点:alpha_src/pnl/dump/feature + ~~factor_state 行 + factor_derived 行~~ **factor_info 行 (级联删 state + snapshot, 2026-07-06 三表重构后)**。~~rm 是 DerivedStore.delete 的首个调用者~~ (derived 已被 snapshot 取代)。取消 `--force`(不再有半删档)。`FactorStatus.DELETED` 枚举 + `FactorRecord.deleted_at` 字段 + factor_state.deleted_at 列(schema + 活库 ALTER DROP)全部移除。清理消费点:restage 去 `-s deleted` 复活路径、list/health 去默认 DELETED 过滤、status/pack choices、sync 的 pull 跳过。默认交互确认 + `-y`,单因子接口(不加批量)。

**仍待讨论(未拍板)**:
- ~~rm / cancel 这对~~ → 已厘清:rm=彻底删已入库因子(6 落点),cancel=删未入库 SUBMITTED 的 staging+state record,语义不同且都正确,不合并。
- status:plans.md 已记 basically 被 list -s / info 覆盖,只剩单因子 history 独占,待折叠。
- ~~clear 补丁型~~ → 已厘清并保留,逻辑不动(用户说先放)。clear 不是给 submit 不健壮擦屁股(submit 现在 skip/fail/异常都 rmtree 回滚 staging,正常流程不产孤儿)。真实定位:**crash residue 清理**——进程非正常终止(SIGKILL/OOM/断电)卡在 copytree 与回滚/store.put 之间留下的 staging 孤儿(无 state record)。与 cancel --force(清 CHECKING 残留)、restage 崩溃注释同族,属 reconcile 下线后"崩溃残留不自动修、人工命令兜底"路线。**遗留文档债**(未修):clear docstring/CLAUDE.md 仍把成因写成"submit parse 失败留下",已过时。**方向性未定**:clear + cancel --force 是否归入未来统一的 `ops doctor`(多处 docstring 已提及该设想,未实现)。
- ~~approve 合理性~~ → 已厘清并保留(文档已正名,commit 见下)。approve 不是"correlation 人工审批",真实定位是**因子库多样性 / 数据覆盖的人工豁免**:自动流水线只认业绩+低相关,盲区是不看数据使用覆盖(_check_beat 只比 fitness/ret/shrp)。一个用了稀缺数据但相关/业绩不占优的因子会被 correlation stage 必拒且无自动路径可救——approve 是唯一人工闸。此价值与人工/机器是否分池无关,长期存在。放行宽度=整个 correlation stage(业绩+相关性),不收窄(为覆盖保留因子本就可能接受业绩差一点)。配合 `ops list --filter-by field/tables`(反查覆盖缺口)成套使用。
- health --fix 职责溢出:从"检查"跑去"生产 derived 数据"。**(2026-07-06 update: ops health 计划整体删除, 记在 `scripts/postgres/MIGRATION_TODO.md`)**
- ~~**derived 写入路径不对称**(最早那条线,未动):index/datasources/bcorr 只在 ops list/refresh 旁路生产,不在因子产出那一刻;backfill/run 不落 metrics。rm 已成为 DerivedStore.delete 首个调用者。~~ **(2026-07-06 三表重构解决:metrics/datasources/bcorr 改为 check archive 阶段一次性写 factor_snapshot,不再旁路生产;`ops refresh` 删除;metrics 语义变为入库时不可变快照。derived 层降为僵尸层,仅 LibraryScanner index 缓存仍用。详见 [[project_factor_library_storage_architecture]])**

**背景关联**:讨论中捋清了因子入库全流程的两个 PG 表写入图(见对话),关键发现:派生 4 组(index/metrics/datasources/bcorr)写入路径高度不对称、大多寄生在 ops list 旁路而非因子产出那一刻;DerivedStore.delete() 全库无调用者 → cancel 硬删 state 却留 derived 残行。参见 [[project_factor_command_semantics]] [[project_factor_state_machine]] [[project_factor_library_storage_architecture]]。

**2026-07-13 命令面收敛更新**(开放项多数已拍板落地):
- **`ops doctor` 已落地**(v1,8 族对账:池鬼影/stale 快照/时间线不变量/info 孤儿/src·staging 漂移/产物孤儿/本机 dump 孤儿)——缺省纯只读报告,`--fix <族>` 逐族确认修复(五道闸删除管道)。原"clear/cancel --force 是否归入统一 doctor"的设想:doctor **不复制**第二套删除逻辑,只报告并转介既有命令。
- **`ops health` 已删除**(2026-07-07 Wave 2:--fix 写没人读的僵尸表;对账职能已由 doctor 落地)。
- **`ops sync` / `ops refresh` 已删除**(sync=S3 模型被 JFS 取代;refresh=三表重构后快照不可重算)。
- **`ops backfill` 已退役删除**(2026-07-13 legacy 清理批:bootstrap 使命完成,留着 = src 孤儿整批复活成 ACTIVE 的风险;`HISTORY_OPS`/DB `chk_op` 保留 'backfill' 枚举值 —— 存量事件是历史事实)。
- **`ops status` 尚未折叠**(仍与 list -s / info 部分重叠,单因子 history 独占)。
详见根 `CLAUDE.md` 子命令表 + Removed subcommands 段。
