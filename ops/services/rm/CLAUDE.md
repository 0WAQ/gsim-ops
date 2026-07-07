# Rm

彻底删除一个因子(不可逆)。没有软删/墓碑。

## 行为

一次删掉因子的全部落点:

- `alpha_src/<name>/`(源码目录,唯一代码副本)
- `alpha_pnl/<name>`(PNL 单文件)
- `alpha_dump/<name>/`(dump 目录)
- `alpha_feature/<name>.{v1,v2}.npy`(feature)
- `factor_info` PG 行 —— **级联删 `factor_state` + `factor_snapshot`**(外键 `ON DELETE CASCADE`)

删除后因子即不存在,要恢复只能重新 `ops submit`。dump+feature 复用 `_purge_artifacts`
(restage `--purge` 也用它);src/pnl 各自单独删,state/snapshot 由删 factor_info 级联带走(`default_info_store(config).delete(name)`)。全程包在 `factor_lock` 内。

## 确认

默认交互确认,展示完整删除清单 + "不可逆" 字样;`-y` 跳过。单因子接口,不支持
`-u` 批量(避免一条命令删一片)。

## 与 ops cancel 的区别

| | `ops rm` | `ops cancel` |
|---|---|---|
| 适用状态 | 已入库(ACTIVE/REJECTED 等) | SUBMITTED(`--force` + CHECKING) |
| 删除范围 | src/pnl/dump/feature + factor_info(级联 state + snapshot)全删 | staging 目录 + state record(无产物可删) |
| 因子曾入库 | 是 | 否(从未 ACTIVE) |

---

Tests: `tests/test_lifecycle_cmds.py` (hard deletion of all five artifact dirs + factor_info 级联 state + snapshot).
