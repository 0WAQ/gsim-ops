# Cancel

撤回未入库的因子。针对场景:QR 提交后发现因子不合规,在 `ops check` 之前撤掉。

## 适用范围

- 默认: `SUBMITTED`
- `--force`: 同时允许 `CHECKING`,用于清理崩溃 / 中断的 check 残留
- **`entered_at` 非空一律拒绝**(2026-07-07):曾入库因子被 restage 召回后也是
  SUBMITTED,但 restage 是 move 不是 copy —— staging 里是**唯一源码副本**,cancel
  的 rmtree 会毁掉它(full-review 第一部分 1.2)。此类因子要么 `ops rm` 彻底删,
  要么 `ops check` 重新入库。批量模式归入 Skipped 段。
- **`alpha_src/<name>` 存在一律拒绝**(2026-07-09,JOURNAL U3):曾被 check 归档
  的因子(典型:REJECTED 后 `submit --overwrite` 重提,entered_at 为空)在
  alpha_src 有归档、late-stage 拒绝还留 pnl/dump —— cancel 只删记录会把这些产物
  变成孤儿(生产实测 143 个)。指引 `ops rm`(rm 已含 staging,全落点删除)。
  批量模式归入 Skipped 段。

## 与 ops rm 的区别

| | `ops cancel` | `ops rm` |
|---|---|---|
| 适用状态 | SUBMITTED (`--force` + CHECKING),且**无任何归档产物** | 任何有 state 记录的因子(典型 ACTIVE/REJECTED;也承接被 cancel 守卫拒绝的"有归档的 SUBMITTED") |
| 删除范围 | staging 目录 + **硬删** state record | src/staging/pnl/dump/feature/池副本 + factor_info(级联 state + snapshot)全删 |
| 适用前提 | 纯新提交,除 staging 外零落点(entered_at / alpha_src 双守卫把关) | 因子有归档落点(曾入库或曾被 check 归档) |

cancel 的前提是"纯新提交:除 staging 外无任何落点"——上面两道产物守卫
(entered_at / alpha_src)就是在保证走到删除这步的因子确实如此。

## 操作流程

1. `_resolve_targets` — 按 name / user 筛选,状态不匹配
   - 单因子:报错退出
   - 批量 (`-u`):先 `info_store.list(author=...)` 取 name 集合,与 `store.list()` 取交集;归入 `Skipped` 段,不阻断。显示 author 从 `info_store.get(name)` 取
2. apt 风格确认 (`-y` 跳过)
3. `_cancel_one`:
   - `shutil.rmtree(staging/<name>/)`
   - `store.delete(name)` 硬删
   - `info_store.delete(name)`(2026-07-07:FK 级联方向是 info→state,不删则每次
     cancel 泄漏一行孤儿 factor_info 且任何命令都够不到;entered_at 守卫保证走到
     这里的因子从未入库,身份行可安全移除)

## 不动的产物

`alpha_src / alpha_pnl / alpha_dump / alpha_feature` 都不动 —— 正因如此,资格
判定拒绝任何有 alpha_src 归档的因子("SUBMITTED 无产物"对 REJECTED 后重提的
因子不成立,U3 事故即此,143 个孤儿)。CHECKING 残留若有 dump,留给后续 gc。

## 并发安全

每个因子操作包裹在 `factor_lock`;被占用则跳过(warn + locked 计数)。
**2026-07-07 Wave 3**:批量骨架收敛到 `ops/services/_batch.py`(confirm / 锁循环 /
汇总 / 失败双通道记录),并修复 TOCTOU —— 确认提示挂起期间状态可变,action 在
**锁内重取记录复验资格**(不过则 SkipFactor 跳过);状态转移用
`transition(expect=...)` CAS 双保险(FOR UPDATE 行锁内校验 from-status,冲突抛
StateConflict 按跳过处理)。`run_*` 返回 `BatchResult`(done/skipped/failed/locked),
测试可断言"正确拒绝"。行为测试见 `tests/test_batch.py`(json 后端,无需 PG)。

## 崩溃恢复

先删 staging 再删 state record — 崩在中间留下 orphan state(SUBMITTED、无文件)。
reconcile 已下线,不再自动清理;但 `ops check` 按 staging 目录扫描,该 orphan 不影响后续流程,
必要时人工 `ops rm` / 后续 `ops doctor` 处理。

---

Tests: `tests/test_lifecycle_cmds.py` (SUBMITTED/--force CHECKING deletion, batch -u filters, archived-artifact guard 单因子拒绝 + 批量 skip)。
