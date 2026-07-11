# I2 复验结果(160 执行者;分支 claude/i2-test-isolation)

**结论:全绿。** per-session schema 隔离在生产同款环境(server-160,ops_test
在生产 PG 实例 10.9.100.160:15432)复验通过:pg 组 0 skipped、并行两进程互不
干扰、ops_test 零残留(t_* schema 自清)、e2e 在新隔离下仍绿。全程只碰
ops_test 库,未对生产 ops 库执行任何语句;数据路径全在 pytest tmp。

- 执行机:server-160(hostname `server-160`,user `wbai`)
- HEAD:`0f6d6bf`(claude/i2-test-isolation,与 origin 一致)
- 日期:2026-07-11

## 阶段 1 · 同步 + 全量 fast suite

`uv sync --group dev` 通过(Resolved 29 / Audited 24)。

```
uv run pytest -m "not slow" -q
134 passed, 6 deselected in 3.96s

uv run pytest -m pg -q
57 passed, 83 deselected in 2.60s
```

预期 134 passed / 0 skipped、57 passed / 0 skipped —— 符合。

## 阶段 2 · 并行不撞(I2 核心命题)

两个 pytest 进程并行同跑 pg 组:

```
=== par1 tail ===
57 passed, 83 deselected in 3.01s
=== par2 tail ===
57 passed, 83 deselected in 3.12s
```

预期各 57 passed —— 符合。并行两进程各持有独立 t_* schema + 独立
lock_namespace,互不干扰,"测试须串行"纪律作废。

## 阶段 3 · ops_test 零残留

并行两轮跑完后核对:

```
t_* schemas: []
残留表: [('public', 'factor_info'), ('public', 'factor_state'), ('public', 'factor_snapshot')]
```

- `t_* schemas: []` —— 符合预期,per-session schema 全部 DROP CASCADE 自清,
  零残留。
- 残留表:`public.factor_{info,state,snapshot}` 三表 —— I2 之前的测试建在
  public 下的历史残留,属预期。**未删除**(判读留给判读方;I2 后测试永不
  再碰 public,只落随机 t_* schema)。

e2e 跑完后追加核对 `t_* schemas: []`,同样零残留。

## 阶段 4 · e2e(新隔离下的真 pipeline)

```
tests/e2e/test_e2e_pipeline.py::test_e2e_pass_to_active PASSED           [ 16%]
tests/e2e/test_e2e_pipeline.py::test_e2e_validate_fail PASSED            [ 33%]
tests/e2e/test_e2e_pipeline.py::test_e2e_checkbias_fail PASSED           [ 50%]
tests/e2e/test_e2e_pipeline.py::test_e2e_checkpoint_fail PASSED          [ 66%]
tests/e2e/test_e2e_pipeline.py::test_e2e_compliance_fail PASSED          [100%]
tests/e2e/test_e2e_pipeline.py::test_e2e_correlation_fail PASSED         [100%]

6 passed, 134 deselected in 102.10s (0:01:42)
```

预期 6 passed —— 符合(用时 102s,快于手册 ~7min 估计)。真 gsim + 真 cc
数据,六种假因子在各 stage 确定性暴雷 + good 因子走到 ACTIVE 均按预期路由。

## 判读

四阶段全部符合预期,无一步偏离。per-session schema 隔离基建成立:
测试与生产 ops 命令、与其它机器测试并行都安全,不需要窗口。历史残留
public 三表已贴出,交判读方决定是否清理。全绿,可提 PR。
