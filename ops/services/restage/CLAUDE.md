# Restage

把已入库因子召回 staging,等待重跑 check(原代码不变)。**restage 本身不跑回测**——只搬源 + 翻 SUBMITTED,下一次 `ops check` 才真正重跑。

## 支持的来源状态

- **ACTIVE** (默认): 源 = `alpha_src/<name>/`
- **REJECTED** (`-s rejected`): 源 = `alpha_src/<name>/`(REJECTED src 与 ACTIVE 同库)

## 操作流程

1. `_resolve_targets` — 按 name / user / status 筛选目标(批量 `-u` 先 `info_store.list(author=...)` 取 name 集合,再与 `store.list(status=...)` 取交集;author 从 factor_info 读)
2. `_locate_source` — 按状态定位因子源目录
3. 显示计划，apt-install 风格确认 (`-y` 跳过)
4. `_restage_one` — move src → staging, rewrite XML module path, transition state → SUBMITTED,
   **删除 factor_snapshot 行**(2026-07-07:离库即旧快照失效;不删则 re-check 通过后
   archive 的 insert 撞 name UNIQUE 被吞,快照永远停在旧代码,full-review P0-1。
   删失败不阻断,archive 侧有 stale 自愈兜底)

**批量守卫**(2026-07-07):`--status` 的 argparse 默认值是 None(不是 'active'),
批量模式必须显式给 `-u` 和/或 `-s`,否则拒绝 —— 原先默认值让守卫永远不触发,
裸 `ops restage -y` 会召回全库 ACTIVE 因子。name 与 `-u` 互斥(与 approve/cancel/clear 对齐)。

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

---

Tests: `tests/test_restage.py` (ACTIVE/REJECTED recall, --purge, unsupported status/missing source skip).
