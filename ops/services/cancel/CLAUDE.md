# Cancel

撤回未入库的因子。针对场景:QR 提交后发现因子不合规,在 `ops check` 之前撤掉。

## 适用范围

- 默认: `SUBMITTED`
- `--force`: 同时允许 `CHECKING`,用于清理崩溃 / 中断的 check 残留

## 与 ops rm 的区别

| | `ops cancel` | `ops rm` |
|---|---|---|
| 适用状态 | SUBMITTED (`--force` + CHECKING) | ACTIVE / REJECTED |
| state 处理 | **硬删** record | 软删,留 DELETED tombstone |
| 因子曾入库 | 否(从未 ACTIVE) | 是 |

因子从未 ACTIVE 过,所以不需要 tombstone 传播。

## 操作流程

1. `_resolve_targets` — 按 name / user 筛选,状态不匹配
   - 单因子:报错退出
   - 批量 (`-u`):归入 `Skipped` 段,不阻断
2. apt 风格确认 (`-y` 跳过)
3. `_cancel_one`:
   - `shutil.rmtree(staging/<name>/)`
   - `store.delete(name)` 硬删

## 不动的产物

`alpha_src / alpha_pnl / alpha_dump / alpha_feature` 都不动。
SUBMITTED 因子按定义没有这些产物;CHECKING 残留若有 dump,留给后续 gc。

## 并发安全

每个因子操作包裹在 `factor_lock`。被占用(check 正在跑)则跳过(warn + locked 计数)。

## 崩溃恢复

先删 staging 再删 state record — 崩在中间留下 orphan state(SUBMITTED、无文件)。
reconcile 已下线,不再自动清理;但 `ops check` 按 staging 目录扫描,该 orphan 不影响后续流程,
必要时人工 `ops rm` / 后续 `ops doctor` 处理。
