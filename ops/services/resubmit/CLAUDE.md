# Resubmit

将因子打回 staging 重新审查。

## 支持的来源状态

- **ACTIVE** (默认): 源 = `alpha_src/<name>/`
- **REJECTED** (`-s rejected`): 源 = `recycle/{user}/{stage}/<name>/`
- **DELETED** (`-s deleted`): 优先 `alpha_src`(soft-delete 保留 src），否则 recycle

## 操作流程

1. `_resolve_targets` — 按 name / user / status 筛选目标
2. `_locate_source` — 按状态定位因子源目录
3. 显示计划，apt-install 风格确认 (`-y` 跳过)
4. `_resubmit_one` — move src → staging, rewrite XML module path, transition state → SUBMITTED

## Destructive 行为

- 默认仅搬源 + 翻状态；dump / feature / pnl 保留
- `--purge`: 清除 dump + feature（pnl 始终保留）
- purge 复用 `rm.py` 的 `_purge_artifacts`

## 并发安全

每个因子操作包裹在 `factor_lock` 中。被占用则跳过（warn + locked 计数）。

## 崩溃恢复

先 move 再 transition — 崩在中间由 reconcile 修复。
