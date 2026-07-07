---
name: factor-command-semantics
description: "因子提交/召回命令语义(2026-07-04 后):submit(新因子, --overwrite 覆盖)/restage(原代码召回重跑)。resubmit 已删, recheck 已改名 restage"
metadata: 
  node_type: memory
  type: project
  originSessionId: 02d7c1d1-953d-4031-8f88-5db321590f79
---

## 命令语义(2026-07-04 CLI 重审后)

- **ops submit**: 从 dropbox 提交因子。新因子 → put version=1。**已入库同名因子默认跳过**;`--overwrite` 才 version+1 覆盖(新代码,旧 alpha_src 保留作对比)。来源: dropbox。(原 `resubmit` 已并入,不再是独立命令。)
- **ops restage**: 原代码不变,把已入库因子(ACTIVE/REJECTED)从 alpha_src 召回 staging + 翻 SUBMITTED,等下次 `ops check` 重跑。**restage 本身不跑回测**。version 不变。(原名 `recheck`,名不副实故改名。)

**历史**:2026-05 曾是 submit/resubmit/recheck 三个独立命令,靠"因子存不存在 / 代码变没变"切分,逼用户替系统做判断。2026-07-04 重审:submit 内部按 `store.get(name)` 分派吸收 resubmit;recheck 只是"召回 staging"故改名 restage。审计链(check_history)仍不断。

**其它生命周期命令**:approve(REJECTED→ACTIVE 的数据覆盖多样性人工豁免,不重跑)、cancel(撤回未入库 SUBMITTED,删 staging + 硬删 state)、clear(清 staging crash 孤儿)、rm(彻底硬删已入库因子,删 factor_info 级联 state+snapshot,不可逆,无墓碑)。

**注 (2026-07-06 三表重构)**: metrics/datasources/bcorr 语义从"可 `ops refresh` 重算的最新表现"变为"入库时不可变快照";`ops refresh` 命令**已删除**。因子数据落三张 PG 表 (factor_info/state/snapshot)。restage/cancel/approve 批量模式 (`-u`) 先从 info_store 取 author 集合再与 state 交集 (因 FactorRecord 已无 author 字段)。详见 [[project_factor_library_storage_architecture]]。

**How to apply:** 改这条线记住:submit 一个命令管新提交+覆盖(--overwrite);restage 管原代码召回;两者终点都是 SUBMITTED@staging 等 check。别再提 resubmit/recheck。

相关: [[factor-state-machine]] [[project-cli-command-redesign]]
