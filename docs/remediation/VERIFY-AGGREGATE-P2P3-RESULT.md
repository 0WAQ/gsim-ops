# 验证结果 · Factor 聚合阶段 2/3(160 单机)

**分支**:`claude/factor-aggregate-phase3`
**执行日期**:2026-07-10

**结论(第二轮 · 最终)**:**全绿通过**。拉取含夹具修复的 tip `b0b548e`
后从阶段 1 重跑,阶段 0–4 全部符合预期(阶段 5 手册要求本次跳过)。

**首轮记录**:第一次跑(rev `90bfd9e`)阶段 1 fast suite 出现 2 个 FAILED
(`test_check_scan.py` 陈旧夹具,非生产 bug),按红线停在阶段 1。作者提交
`b0b548e` 修复夹具后本轮重跑。首轮详情保留在文末「附录 · 首轮(rev 90bfd9e)」。

---

# 第二轮(rev b0b548e · 最终)

**160 rev**:

```
b0b548e test: check_scan 的 _ensure_record 夹具随 store→Repository 迁移补齐
d501694 docs: 阶段 2/3 验证结果 — 阶段 1 fast suite 2 failed(...),按红线停在此步
90bfd9e docs: Factor 聚合阶段 2/3 的 160 验证执行手册(PG 组 + e2e + 金丝雀环路)
```

## 阶段 1 · fast suite 含 PG 组 —— 通过

`uv run pytest -m "not slow" -q` 汇总行:

```
101 passed, 8 skipped, 6 deselected in 2.50s
```

0 failed,与手册预期(160 上 101 上下)一致。手册点名用例首轮已全 PASSED
(见附录),本轮全量绿。

## 阶段 2 · e2e(真 gsim + cc)—— 通过

`uv run pytest -m e2e -q` 汇总行:

```
6 passed, 109 deselected in 75.61s (0:01:15)
```

archive 前后编排(_ensure_record→register、_persist_derived→attach_snapshot、
to_lib 身份断言)的逐 stage 确定性失败因子回归全过。

## 阶段 3 · 只读冒烟 —— 通过

`uv run ops list | tail -1`:

```
Total: 8252 factors
```

`uv run ops list -u wbai`(3 因子):

```
 name                        author   delay    ret%   shrp    mdd%    tvr%   fitness   bcorr   fail_stage
 AlphaWbaiReversal           wbai         0   12.44   1.19   40.41   78.29      0.47    0.67   correlation
 AlphaZxu_260414_Ret_W_amo   wbai         0   31.13   4.35    7.96   42.97      3.71    0.89
 AlphaZxu_260414_VOV         wbai         0   20.98   4.12    4.08   13.64      5.11    0.69
Total: 3 factors
```

`uv run ops list --filter-by "ret>30,shrp>2" | tail -1`(下推路径):

```
Total: 55 factors
```

`uv run ops list --filter-by "ret=>30"`(错误路径,原文):

```
Unknown operator: '=>' (did you mean '>='). Supported: !=, <, <=, =, >, >=
```

`uv run ops status AlphaWbaiReversal`(单因子,repo.get,check_history 11 条完整)
+ `uv run ops info AlphaWbaiReversal`(snapshot metrics + 物理状态)均正常显示。

Total=8252 与阶段 0 基线一致,repo.find 取代 query_factors 的生产读路径实证通过。

## 阶段 4 · 金丝雀行为环路 —— 通过

前置(server-160,NOPASSWD-OK,孪生真因子 `AlphaWbaiReversal` 在 pnl_manual 池,
金丝雀无残留,基线 Total=8252)全绿。两份 config 生成 corr_threshold=1.01/0.7。

### 4a · 入库(register 原子 + attach 强制 + 身份守卫不误伤)

`submit → check -c config.verify.yaml`:7 stage 全过 → `[1/1] AlphaWbaiCanary001 → lib`,
`✔ 通过 : 1`。正常因子 normalize 后目录名==@id,身份守卫**未**触发(无误伤验证点)。

三表核对(只读 SELECT,列 name/author/status/entered_at/snapshot_at/stamped):

```
('AlphaWbaiCanary001', 'wbai', 'active',
 datetime(2026,7,10,13,27,33, tz=UTC), datetime(2026,7,10,13,27,33, tz=UTC), True)
```

`stamped=True` —— attach_snapshot 强制 snapshot_at=entered_at 生效。
`ls pnl_manual/AlphaWbaiCanary001` 池副本存在。

### 4b · check 期间连接占用(P0 后遗症复核)

4c re-check 运行中并发轮询 6 次:

```
SELECT count(*) FROM pg_stat_activity WHERE datname='ops';
→ (1,) (1,) (1,) (1,) (1,) (1,)
```

个位数,远低于 100(get_pool 去重后)。

### 4c · 生产阈值 re-check → REJECTED(归因 + 产物策略)

`restage -y` 输出含 `✔ 已回收 alpha_pnl/...` + `✔ 已回收 pnl_manual/...`。
`check -c config.verify-pv7.yaml`(corr=0.7):

```
[1/1] AlphaWbaiCanary001  → rejected/correlation: bcorr=1.0, ret=12.42%, shrp=1.18, mdd=40.41%, tvr=78.29%, fitness=0.47
✘ 未通过 : 1
```

`ops status`:`last_fail = correlation — bcorr=1.0, ret=12.42%, shrp=1.18, ...`。

**自名过滤判读**:bcorr=1.0 的 fail metrics(ret=12.42%, shrp=1.18, mdd=40.41%,
tvr=78.29%)与孪生真因子 `AlphaWbaiReversal`(list 显示 ret=12.44, shrp=1.19)
一致 —— 竞品是**孪生真因子**,不是金丝雀自己;且 restage 已回收自副本,
`pnl_manual/AlphaWbaiCanary001` 无自副本。**判定 ✅**(没撞自己)。

产物策略:

```
alpha_pnl/AlphaWbaiCanary001   存在   （late-stage 保留）
alpha_dump/AlphaWbaiCanary001  存在   （late-stage 保留）
pnl_manual/AlphaWbaiCanary001  无输出 （REJECTED 不拷池）
```

### 4d · approve 语义 API

`ops approve AlphaWbaiCanary001`(不带 -y)确认交互原文:

```
将 approve 1 个因子 → active:
  · AlphaWbaiCanary001                        author=wbai        rejected_at=2026-07-10T21:33:33
确认 approve 1 个因子? [y/N]   ✔ AlphaWbaiCanary001 rejected → active
```

`author=wbai` 来自 repo.find/get。`ops status`:status=active,check_history(3)。
末条(PG `check_history->-1`)原文:

```
{'passed': True, 'started_at': '2026-07-10T21:37:39', 'fail_reason': 'approved',
 'finished_at': '2026-07-10T21:37:39', 'failed_stage': None}
```

`ops info` Metrics 段:

```
├── Metrics (入库时快照)
│   └── —  (未入库或入库时未生成 metrics)
└── Data Sources (入库时)
    └── —  (未入库或入库时未解析 datasources)
```

合法无快照的 ACTIVE —— approve 不写快照,占位符正确。

### 4e · REJECTED 闭环 + rm 全落点

`restage -y`(回收 alpha_pnl)→ `check -c config.verify.yaml`(corr=1.01):
`[1/1] → lib`,`✔ 通过 : 1`(stale snapshot 自愈路径)。

`ops rm -y` 输出含五条 `✔ 已删除`(alpha_dump / alpha_src / alpha_pnl /
pnl_manual / factor_info 级联 state+snapshot)。零残留核对:

```
文件（alpha_src/alpha_dump/staging/alpha_pnl/pnl_manual/pnl_automated）: 全无输出
PG:  ('info', 0)  ('state', 0)  ('snap', 0)      ← repo.delete 级联
```

### 4f · cancel 级联一步(二号金丝雀,不跑 check)

`submit AlphaWbaiCanary002` → status=submitted。`ops cancel`(不带 -y)交互原文:

```
将 cancel 1 个因子(删 staging + 删 state record):
  · AlphaWbaiCanary002                        submitted  author=wbai        submitted_at=2026-07-10T21:39:53
确认 cancel 1 个因子? [y/N]     ✔ 已删除 staging/AlphaWbaiCanary002/
  ✔ 已删除 factor_info + 级联 state record AlphaWbaiCanary002
```

级联新语义核心断言(info 不得剩孤儿行):

```
PG:  ('info', 0)  ('state', 0)
staging/AlphaWbaiCanary002:  无输出
```

### 4g · 清理

删两份 config + dropbox 金丝雀 + check 报告。`ops list | tail -1`:

```
Total: 8252 factors
```

回到基线;工作树仅剩会话开始即存在的无关 untracked 文件。

## 阶段 5 · 150/144 —— 本次跳过(手册明示,phase2/3 未合 main)

---

# 附录 · 首轮(rev 90bfd9e)

首轮阶段 0 全绿(ruff/pyright/lint-imports 7 kept/ratchet 已删),阶段 1 出现
2 个 FAILED 后按红线停止:

```
2 failed, 99 passed, 8 skipped, 6 deselected
FAILED tests/test_check_scan.py::test_ensure_record_creates_submitted - AttributeError
FAILED tests/test_check_scan.py::test_ensure_record_does_not_overwrite - AttributeError
```

`AttributeError: 'PostgresStateStore' object has no attribute 'record'`
(`check.py:256` 调 `repo.record()`)。根因:这两个用例把 `_store(config)`
(→ `PostgresStateStore`)当第二参传给 `pipe._ensure_record(factor, store)`,
而阶段 2/3 重构后该函数期望 `FactorRepository`。生产路径(`check.py:324-325`
传 `self._repo()`)无影响 —— 判定为陈旧测试夹具,非生产 bug。作者 `b0b548e`
修复夹具后即进入上方第二轮。

首轮手册点名用例全部 PASSED:`test_repository.py` PG 组 14 个(register 原子 /
find 因子集与过滤 / include_submitted / info 孤儿现形 / attach 强制 entered_at +
stale 自愈 / attach 无 entered_at 拒绝 / delete 级联)、`test_check_routing_json.py`
点名 4 个、`test_lifecycle_cmds.py` + `test_factor_paths.py`(合跑 44 passed)。

---

## 附:阶段 0 静态门禁(两轮一致)

## 阶段 0 · 部署 + 静态门禁 —— 通过

`git status -sb`(tracked 干净;untracked 均为会话开始即存在、与本分支无关的文件):

```
## claude/factor-aggregate-phase3...origin/claude/factor-aggregate-phase3
?? docs/reports/check/check-fguo-20260709-235302.json
?? docs/reports/check/check-lhw-20260710-031451.json
?? docs/reports/check/check-xmf-20260710-122605.json
?? docs/reports/check/check-zxu-20260710-192411.json
?? pgreadonlysetup.sql
```

`git log --oneline -3`:

```
90bfd9e docs: Factor 聚合阶段 2/3 的 160 验证执行手册(PG 组 + e2e + 金丝雀环路)
34e7aee feat(cli): cli/common 接缝 + 7/7 契约 enforcing,ratchet 退役;status/cancel/pack 塌缩(阶段 3 第一批)
9b74816 feat(repository): Factor 聚合 + FactorRepository 落地,C3 清零转 enforcing(阶段 2)
```

`uv sync --group dev`:Resolved 29 packages,新装 click 8.4.2 / grimp 3.15 / import-linter 2.13。

`uv run ruff check ops tests`:

```
All checks passed!
```

`uv run pyright ops`:

```
0 errors, 0 warnings, 0 informations
```

`uv run lint-imports`(本次新验收):

```
C1 layers: cli -> services -> infra -> core -> utils KEPT
C2 cli must not import infra or core (directly) KEPT
C3 service packages are independent KEPT
C5 utils is a leaf KEPT
C6 infra must not import presentation KEPT
C7 services use store factories, not concrete backends KEPT
C8 db drivers only in infra KEPT

Contracts: 7 kept, 0 broken.
```

`ls scripts/ci/ contracts-baseline.toml`(预期都不存在):

```
ls: cannot access 'scripts/ci/': No such file or directory
ls: cannot access 'contracts-baseline.toml': No such file or directory
```

阶段 0 全项符合预期。
