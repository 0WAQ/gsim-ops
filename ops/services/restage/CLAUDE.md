# Restage

把已入库因子召回 staging,等待重跑 check(原代码不变)。**restage 本身不跑回测**——只搬源 + 翻 SUBMITTED,下一次 `ops check` 才真正重跑。

## 支持的来源状态

- **ACTIVE** (默认): 源 = `alpha_src/<name>/`
- **REJECTED** (`-s rejected`): 源 = `alpha_src/<name>/`(REJECTED src 与 ACTIVE 同库)
- **DELETED** (`-s deleted`): 源 = `alpha_src/<name>/`(soft-delete 保留 src);已被 `--force` 清则无法 restage,需 `ops submit`

## 操作流程

1. `_resolve_targets` — 按 name / user / status 筛选目标
2. `_locate_source` — 按状态定位因子源目录
3. 显示计划，apt-install 风格确认 (`-y` 跳过)
4. `_restage_one` — move src → staging, rewrite XML module path, transition state → SUBMITTED

## 语义区分

- `ops restage`: 原代码不变,召回 staging 待重跑 check。version 不变。
- `ops submit --overwrite`: 新代码从 dropbox 覆盖,version += 1。

## Destructive 行为

- 默认仅搬源 + 翻状态；dump / feature / pnl 保留
- `--purge`: 清除 dump + feature（pnl 始终保留）
- purge 复用 `rm.py` 的 `_purge_artifacts`
- REJECTED restage 额外清 `alpha_pnl/<name>` 单文件（离开 REJECTED 后 pnl 无意义）

## 并发安全

每个因子操作包裹在 `factor_lock` 中。被占用则跳过（warn + locked 计数）。

## 崩溃恢复

先 move 再 transition — 崩在中间(src 已离开 alpha_src、state 未翻）留下 orphan。reconcile
已下线;此类残留不自动修复,必要时人工 `ops rm` / 后续 `ops doctor`。因子若已进 staging,
下次 `ops check` 会照常扫到并重跑。
