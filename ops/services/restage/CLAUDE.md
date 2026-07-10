# Restage

把已入库因子召回 staging,等待重跑 check(原代码不变)。**restage 本身不跑回测**——只搬源 + 翻 SUBMITTED,下一次 `ops check` 才真正重跑。

## 支持的来源状态

- **ACTIVE** (默认): 源 = `alpha_src/<name>/`
- **REJECTED** (`-s rejected`): 源 = `alpha_src/<name>/`(REJECTED src 与 ACTIVE 同库)

## 操作流程

1. `_resolve_targets` — 按 name / user / status 筛选目标(批量走 `repo.find(author=..., status=...)` 单条三表 JOIN 下推,2026-07-09 退役 info.list + state.list 内存交集;author 显示自 `Factor.identity`)
2. `_locate_source` — 按状态定位因子源目录
3. 显示计划，apt-install 风格确认 (`-y` 跳过)
4. `_restage_one` — move src → staging, rewrite XML module path, transition state → SUBMITTED,
   **删除 factor_snapshot 行**(`repo.discard_snapshot`;2026-07-07:离库即旧快照失效;不删则 re-check 通过后
   archive 的 insert 撞 name UNIQUE 被吞,快照永远停在旧代码,full-review P0-1。
   删失败不阻断,archive 侧有 stale 自愈兜底)

**批量守卫**(2026-07-07):`--status` 的 argparse 默认值是 None(不是 'active'),
批量模式必须显式给 `-u` 和/或 `-s`,否则拒绝 —— 原先默认值让守卫永远不触发,
裸 `ops restage -y` 会召回全库 ACTIVE 因子。name 与 `-u` 互斥(与 approve/cancel/clear 对齐)。

## 语义区分

- `ops restage`: 原代码不变,召回 staging 待重跑 check。version 不变。
- `ops submit --overwrite`: 新代码从 dropbox 覆盖,version += 1。

## Destructive 行为(2026-07-08 PV7:产物分两个面)

- **check 面(pnl + bcorr 池副本)一律回收**(ACTIVE/REJECTED 都是,走
  `repo.purge_artifacts(name, ArtifactScope.CHECK)`,2026-07-09 收编 Repository):
  离库即失效 —— 旧 pnl 留在池里是**自鬼影**
  (重检时新 pnl 对自己旧 pnl corr≈1,高相关分支要求打败几乎相同的自己 → 必拒),
  与"离库删 snapshot"(R1)同构。`submit --overwrite` 同款回收。
- **服务面(dump / feature)= 最后一次入库版本的 last-known-good**,生产 combo
  在重检窗口内继续消费,默认保留;`--purge` = 立即下架(`ArtifactScope.SERVING`);
  REJECTED 召回无服务价值,一律自动清。
- 双保险:correlation checker 对 bcorr 结果**排除自名**(防删除失败残留再造自鬼影)。
- 搬源是 `shutil.move`:召回后 staging 是 src **唯一副本**(cancel 的 entered_at 守卫由此而来)

## 并发安全

每个因子操作包裹在 `factor_lock`;被占用则跳过(warn + locked 计数)。
**2026-07-07 Wave 3**:批量骨架收敛到 `ops/services/_batch.py`(confirm / 锁循环 /
汇总 / 失败双通道记录),并修复 TOCTOU —— 确认提示挂起期间状态可变,action 在
**锁内重取记录复验资格**(不过则 SkipFactor 跳过);状态转移用
`transition(expect=...)` CAS 双保险(FOR UPDATE 行锁内校验 from-status,冲突抛
StateConflict 按跳过处理)。`run_*` 返回 `BatchResult`(done/skipped/failed/locked),
测试可断言"正确拒绝"。行为测试见 `tests/test_batch.py`(json 后端,无需 PG)。

## 崩溃恢复

先 move 再 transition — 崩在中间(src 已离开 alpha_src、state 未翻）留下 orphan。reconcile
已下线;此类残留不自动修复,必要时人工 `ops rm` / 后续 `ops doctor`。因子若已进 staging,
下次 `ops check` 会照常扫到并重跑。

---

Tests: `tests/test_restage.py` (ACTIVE/REJECTED recall, --purge, unsupported status/missing source skip).
