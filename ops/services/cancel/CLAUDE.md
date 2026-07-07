# Cancel

撤回未入库的因子。针对场景:QR 提交后发现因子不合规,在 `ops check` 之前撤掉。

## 适用范围

- 默认: `SUBMITTED`
- `--force`: 同时允许 `CHECKING`,用于清理崩溃 / 中断的 check 残留
- **`entered_at` 非空一律拒绝**(2026-07-07):曾入库因子被 restage 召回后也是
  SUBMITTED,但 restage 是 move 不是 copy —— staging 里是**唯一源码副本**,cancel
  的 rmtree 会毁掉它(full-review 第一部分 1.2)。此类因子要么 `ops rm` 彻底删,
  要么 `ops check` 重新入库。批量模式归入 Skipped 段。

## 与 ops rm 的区别

| | `ops cancel` | `ops rm` |
|---|---|---|
| 适用状态 | SUBMITTED (`--force` + CHECKING) | ACTIVE / REJECTED |
| 删除范围 | staging 目录 + **硬删** state record | src/pnl/dump/feature + factor_info(级联 state + snapshot)全删 |
| 因子曾入库 | 否(从未 ACTIVE) | 是 |

因子从未 ACTIVE 过,没有产物/快照可清,只删 staging + state record。

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

`alpha_src / alpha_pnl / alpha_dump / alpha_feature` 都不动。
SUBMITTED 因子按定义没有这些产物;CHECKING 残留若有 dump,留给后续 gc。

## 并发安全

每个因子操作包裹在 `factor_lock`。被占用(check 正在跑)则跳过(warn + locked 计数)。

## 崩溃恢复

先删 staging 再删 state record — 崩在中间留下 orphan state(SUBMITTED、无文件)。
reconcile 已下线,不再自动清理;但 `ops check` 按 staging 目录扫描,该 orphan 不影响后续流程,
必要时人工 `ops rm` / 后续 `ops doctor` 处理。

---

Tests: `tests/test_lifecycle_cmds.py` (SUBMITTED/--force CHECKING deletion, batch -u filters).
