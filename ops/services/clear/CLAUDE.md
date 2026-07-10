# Clear

清理 staging 里的孤儿目录(state 无 record)。

## 孤儿从哪来

`ops submit` 流程: `copy_to_staging` 先把因子目录复制到 staging,再 `submit_one`
里 normalize_xml + parse_factor + `repo.register`。若 parse 抛错(命名不规范 /
XML 异常 / py syntax error),staging 目录已就位但 state 没写入 → 孤儿。

## 与 ops cancel 的分工

| | `ops cancel` | `ops clear` |
|---|---|---|
| 适用 | state 有 record (SUBMITTED / CHECKING) | state 无 record (只有 staging 目录) |
| 清理 | staging + state record | 仅 staging 目录 |
| 反向触发 | state 无 record → 报错让用 clear | state 有 record → 报错让用 cancel |

两个命令互不重叠,任何一个 staging 目录只属于其中一个的职责。

## 操作流程

1. `_scan_staging_orphans` — 扫 `staging/Alpha*/` 子目录,过滤掉有 state record 的
2. `_resolve_targets`:
   - 单因子 `clear AlphaXxx`: 校验目录在 + state 无 record,否则报错
   - 批量 `clear`: 全部孤儿
   - 批量 `clear -u <user>`: `infer_author_from_dir` 推断 author 过滤(`ops/core/factormeta.py`,与 submit 的 parse_factor 同一函数;2026-07-09 自 submit/parser.py 迁入)
3. apt 风格确认 (`-y` 跳过)
4. `_clear_one`: `repo.unstage(name)`(2026-07-10 收编 Repository,与 cancel/rm 共用)

## 不动的产物

孤儿按定义只有 staging 一处文件,其它产物(alpha_src / pnl / dump / feature)
压根没产生过,无需处理。

## 并发安全

每个因子操作包裹在 `factor_lock`;被占用则跳过(warn + locked 计数)。
**2026-07-07 Wave 3**:批量骨架收敛到 `ops/services/_batch.py`(confirm / 锁循环 /
汇总 / 失败双通道记录),并修复 TOCTOU —— 确认提示挂起期间状态可变,action 在
**锁内重取记录复验资格**(不过则 SkipFactor 跳过);状态转移用
`transition(expect=...)` CAS 双保险(FOR UPDATE 行锁内校验 from-status,冲突抛
StateConflict 按跳过处理)。`run_*` 返回 `BatchResult`(done/skipped/failed/locked),
测试可断言"正确拒绝"。行为测试见 `tests/test_batch.py`(json 后端,无需 PG)。

## 跨机一致性

staging 在 JFS 共享挂载点,任意一台 ops 节点 clear 一次,所有节点立即看到。

---

Tests: `tests/test_lifecycle_cmds.py` (orphan-only deletion, batch -u filters by inferred author).
