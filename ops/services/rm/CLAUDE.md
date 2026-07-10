# Rm

彻底删除一个因子(不可逆)。没有软删/墓碑。

## 行为

一次删掉因子的全部落点:

- `alpha_src/<name>/`(源码目录,唯一代码副本)
- `staging/<name>/`(在途副本,如存在;2026-07-09 补 —— restage/overwrite 召回的
  因子代码在 staging,不清则记录级联删除后必成孤儿,且 `ops check` 按 staging
  目录扫描会自动补建记录**复活**刚删的因子,JOURNAL U3)
- `alpha_pnl/<name>`(PNL 单文件)
- `alpha_dump/<name>/`(dump 目录)
- `alpha_feature/<name>.{v1,v2}.npy`(feature)
- `pnl_automated|pnl_manual/<name>`(bcorr 分流池副本,单文件;2026-07-08 生产验证
  L3-7 实测泄漏后补 —— 不清则已删因子的 pnl 永远留在对比池参与后续 bcorr)
- `factor_info` PG 行 —— **级联删 `factor_state` + `factor_snapshot`**(外键 `ON DELETE CASCADE`)

删除后因子即不存在,要恢复只能重新 `ops submit`。产物删除走
`FactorRepository.purge_artifacts(name, scope)`(2026-07-09 收编原本包的
`_purge_artifacts`/`_recycle_check_artifacts`;`ArtifactScope.SERVING`=dump+feature,
`ArtifactScope.CHECK`=pnl+池副本,restage / submit --overwrite 同用);src 目录
单独 rmtree,staging 走 `repo.unstage`(2026-07-10,与 cancel/clear 共用),
state/snapshot 由 `repo.delete(name)` 删 factor_info 级联带走。
全程包在 `repo.lock`(factor_lock)内。

**存在性判据 = `factor_info` 有行**(`repo.get`,2026-07-09 起;原"问 state 有无
记录"会漏掉有 info 无 state 的异常孤儿 —— 那正是 rm 该能清走的东西)。

## 确认

默认交互确认,展示完整删除清单 + "不可逆" 字样;`-y` 跳过。单因子接口,不支持
`-u` 批量(避免一条命令删一片)。

## 与 ops cancel 的区别

| | `ops rm` | `ops cancel` |
|---|---|---|
| 适用状态 | 任何 factor_info 有行的因子(典型 ACTIVE/REJECTED;也承接被 cancel 守卫拒绝的"有归档的 SUBMITTED") | SUBMITTED(`--force` + CHECKING),且须**无任何归档产物** |
| 删除范围 | src/staging/pnl/dump/feature/池副本 + factor_info(级联 state + snapshot)全删 | staging 目录 + state record |
| 适用前提 | 因子有归档落点(曾入库或曾被 check 归档) | 纯新提交,除 staging 外零落点(entered_at / alpha_src 双守卫把关) |

---

Tests: `tests/test_lifecycle_cmds.py` (hard deletion of all five artifact dirs + factor_info 级联 state + snapshot).
