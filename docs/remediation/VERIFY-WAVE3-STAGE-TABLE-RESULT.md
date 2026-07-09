# wave3 + stage-table 滚存三机验证结果报告

**执行时间**: 2026-07-09 14:00 ~ 14:20  
**执行分支**: `claude/remediation-stage-table` @ `a85c26b`  
**执行人**: Claude (代理 wbai)  
**执行主机**: server-160(阶段 0-3、5)+ server-150 / intel-workstation-144(阶段 4 滚存)

---

## 机器现状勘误(核实于 2026-07-09,置于报告首位)

上一份 wave3 报告声称"三机 rev 一致(150/145 @ 4dec7a6)"。本次执行前逐台核实,该声明不成立,予以作废:

| 机器 | 执行前实际 rev | 说明 |
|---|---|---|
| server-160 | `a85c26b`(stage-table 顶端) | 已在目标分支,本轮直接验证 |
| server-150 | `7f5b710`(wave0) | **非** 4dec7a6;本轮由此滚存到 stage-table |
| intel-workstation-144 | `7f5b710`(wave0) | 同上,本轮滚存 |
| server-145(北京 10.9) | **NO-REPO**(无 ops 部署) | 上一份报告"145 一致"声明纯属虚构,作废 |

**结论**:上一份报告的"三机一致"与"145"相关声明作废,降级为"160 单机的 wave3 证据(e2e 绿 + 金丝雀迭代可信,跨机部分作废)"。本报告以本轮实测为准:执行后 160/150/144 三机 rev 均 = `a85c26b`。145 无 repo,不参与 ops 部署验证(与拓扑一致:145 无 ops 部署、无人在此写因子)。

---

## 阶段 0 · 160 部署 + 门禁

✅ **通过**

- rev: `a85c26b Merge branch 'claude/remediation-wave3' into claude/remediation-stage-table`
- 工作树: `## claude/remediation-stage-table`(干净)
- `git pull`: `Already up to date`
- `uv sync --group dev`: Resolved 26 / Audited 21

门禁三条(原始汇总行):

```
uv run ruff check ops tests   → All checks passed!
uv run pytest -m "not slow" -q → 69 passed, 8 skipped, 6 deselected in 5.68s
uv run pyright ops            → 0 errors, 0 warnings, 0 informations
```

新增测试文件已收集:`tests/test_batch.py`(test_transition_cas_pass / cas_conflict / no_expect_unguarded / apply_locked_routes_outcomes / apply_locked_failure_does_not_abort_batch / confirm_* …)、`tests/test_check_routing_json.py`。

## 阶段 1 · e2e 重跑(增量核心验证)

✅ **通过**

```
uv run pytest -m e2e -q → 6 passed, 77 deselected in 90.18s (0:01:30)
```

Stage 表重构(运行块 for-loop 化 + 归因盖章 + prepare 抛错)在真 gsim + 真 cc 数据下对各 stage 确定性失败因子的路由无回归。

## 阶段 2 · 只读冒烟(160)

✅ **通过**

- `ops list` → `Total: 7488 factors`(与基线一致)
- `ops list -u wbai` → 3 factors(AlphaWbaiReversal / AlphaZxu_260414_Ret_W_amo / AlphaZxu_260414_VOV)
- `ops status` → 正常渲染
- `ops info AlphaWbaiReversal` → 正常(rejected, snapshot_at 2026-07-04)
- `ops list --format json` → 正常,exit=0
- 前提:孪生真因子 `pnl_manual/AlphaWbaiReversal` 在池里(3c 前提满足)

## 阶段 3 · 金丝雀行为环路(160)

✅ **通过**  金丝雀 `AlphaWbaiCanary001`,基线 Total 7488。

**夹具卫生说明(非产品回归)**:3a 首次 submit 因 dropbox 目录残留上一轮会话的陈旧文件(`Config.WbaiCanary001.xml` / `Readme.WbaiCanary001.txt`,时间戳早于本轮重建)被正确拒绝(`.xml=2 各需恰好 1 个`)——submit 行为本身正确。重建 snippet 用 `mkdir(exist_ok=True)` 不清旧文件所致;`rm -rf` 目录后干净重建(1 py + 1 xml)即通过。记录在案供后续手册补一句"重建前先 rm -rf 金丝雀 dropbox 目录"。

### 3a · 入库(happy path,corr=1.01)

- `ops submit` → `✔ AlphaWbaiCanary001 → submitted (version=1)`(auto-fixed XML module stem PLACEHOLDER → AlphaWbaiCanary001)
- `ops check -c config.verify.yaml` → `[1/1] AlphaWbaiCanary001 → lib`,通过=1
- 速查:`status: active | version: 1 | entered_at: 2026-07-09T14:11:06`,`snap_at: 2026-07-09T14:11:06`(= entered_at),`pnl_manual/AlphaWbaiCanary001` 存在

### 3b · 批量确认交互(wave3 `_batch`)

**先答 `n`**(完整输出):

```
━━━ restage · 1 个因子 ━━━
  将 restage 1 个因子 → submitted:
    · AlphaWbaiCanary001                        active     author=wbai        ← /tank/vault/alphalib/alpha_src/AlphaWbaiCanary001
  (dump/feature 保留为服务面 last-known-good;pnl + bcorr 池副本一律回收)
  确认 restage 1 个因子? [y/N]   已取消
```

零副作用核对:`status: active`,`alpha_pnl/AlphaWbaiCanary001` 仍在。

**再答 `y`**(完整输出):

```
━━━ restage · 1 个因子 ━━━
  将 restage 1 个因子 → submitted:
    · AlphaWbaiCanary001                        active     author=wbai        ← /tank/vault/alphalib/alpha_src/AlphaWbaiCanary001
  (dump/feature 保留为服务面 last-known-good;pnl + bcorr 池副本一律回收)
  确认 restage 1 个因子? [y/N]     ✔ 已回收 alpha_pnl/AlphaWbaiCanary001
    ✔ 已回收 pnl_manual/AlphaWbaiCanary001
  ✔ AlphaWbaiCanary001 active → submitted
  汇总: 成功=1  失败=0  占用=0  跳过=0
```

apply_locked 核对:`status: submitted | version: 1`,`snap: None`,check 面(alpha_pnl/pnl_manual/pnl_automated)已回收,服务面 `alpha_dump/AlphaWbaiCanary001` 保留。

### 3c · 生产阈值 re-check(异常归因盖章 + on_reject 产物策略)

- `ops check -c config.verify-pv7.yaml`(corr=0.7)→ `[1/1] AlphaWbaiCanary001 → rejected/correlation: bcorr=1.0, ret=12.42%, shrp=1.18, mdd=40.41%, tvr=78.29%, ...`,未通过=1

三点核对(python 核对块原始输出):

```
status: rejected
fail_stage: correlation          ← 归因盖章(来自流水线 current_stage,非异常子类)
fail_reason: bcorr=1.0, ret=12.42%, shrp=1.18, mdd=40.41%, tvr=78.29%, fitness=0.47
snap: None                       ← REJECTED 不写快照
```

产物策略核对:
- late-stage 失败保留:`alpha_pnl/AlphaWbaiCanary001` ✓ + `alpha_dump/AlphaWbaiCanary001` ✓(两个都存在,KEEP_ARTIFACTS 由 PIPELINE 派生)
- `pnl_manual/AlphaWbaiCanary001` → 无输出(REJECTED 不拷池)

(核对块尾部有一处 psycopg `ConnectionPool.__del__` 解释器退出析构噪声,不影响判定 —— 所有 print 结果正常输出。)

### 3d · REJECTED 召回 → 再入库(闭环)

`ops restage -y`(REJECTED 自动清 dump+feature)完整输出:

```
  REJECTED 因子将自动清除 dump + feature
    ✔ 已删除 alpha_dump/AlphaWbaiCanary001
    ✔ 已回收 alpha_pnl/AlphaWbaiCanary001
  ✔ AlphaWbaiCanary001 rejected → submitted
  汇总: 成功=1  失败=0  占用=0  跳过=0
```

`ops check -c config.verify.yaml`(corr=1.01)→ `→ lib`,通过=1。闭环速查:`status: active`,`version: 1`,`snap_at: 2026-07-09T14:13:25`(快照重建),`pnl_manual/AlphaWbaiCanary001` 重现。

### 3e · 清理

`ops rm -y`:级联删 alpha_dump / alpha_src / alpha_pnl / pnl_manual / factor_info(级联 state + snapshot)。清 config.verify*.yaml + dropbox + report。

零残留三连(全部无输出):
```
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY   → 空
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY → 空
ls /tank/vault/alphalib/alpha_feature/$CANARY.*.npy                  → 空
```
`git status -sb` → `## claude/remediation-stage-table`(干净)。`ops list` → `Total: 7488 factors`(回到基线)。

## 阶段 4 · 150 / 144 滚动升级

✅ **通过**  严格串行执行(共享 `ops_test`,逐台跑,期间其它机器不跑测试)。

### server-150

- 执行前 rev: `7f5b710`(wave0)→ 升级后 `a85c26b`(与 160 一致)
- `uv sync --group dev`: 装 nodeenv/pyright/ruff 等 dev 组
- `uv tool install --editable . --force`: `Installed 1 executable: ops`
- `uv run pytest -m "not slow" -q` → `69 passed, 8 skipped, 6 deselected in 6.67s`
- `ops list` → `Total: 7488 factors`;`ops info AlphaWbaiReversal` → 正常(入口为 `ops`,需 PATH)

### intel-workstation-144(WAN 节点)

- 执行前 rev: `7f5b710`(wave0)→ 升级后 `a85c26b`(与 160 一致)
- WAN 耗时(`UV_HTTP_TIMEOUT=180`):`SYNC_WAN_SECONDS=161`(接近超时窗口,证实跨段路由慢,180s 设置必要)、`TOOLINSTALL_WAN_SECONDS=1`
- `uv tool install --editable . --force`: `Installed 1 executable: ops`
- `uv run pytest -m "not slow" -q` → `69 passed, 8 skipped, 6 deselected in 60.76s`(WAN 上 PG 往返慢,全绿)
- `ops list` → `Total: 7488 factors`;`ops info AlphaWbaiReversal` → 正常

**三机 rev 对照**:160 / 150 / 144 = `a85c26b`(一致);Total 均 7488。

## 阶段 5 · 事件遗留收口(160)

✅ **通过**

1. tmp 残渣:`ls .../alpha_feature/.Alpha*.npy.tmp` → 无 tmp 残渣(此前已清)
2. 补 pack(`INCIDENT-144-PACK.md` 遗留;IDC 机器、新代码):

| 步骤 | 结果 |
|---|---|
| `ops pack --dry-run`(前) | `扫描 3848 个因子,跳过已打包 3809 个,待处理 39` |
| `ops pack`(非 force) | `✔ 完成 : 39`([1/39]…[39/39] 全 ✔) |
| `ops pack --dry-run`(后) | `扫描 3848 个因子,跳过已打包 3848 个,待处理 0` |

---

## 总结

| 阶段 | 结果 |
|---|---|
| 0 · 160 门禁(ruff/pytest/pyright) | ✅ |
| 1 · e2e 重跑(6 passed) | ✅ |
| 2 · 只读冒烟 | ✅ |
| 3 · 金丝雀环路(3a-3e,含 batch 交互/归因盖章/产物策略/闭环) | ✅ |
| 4 · 150/144 滚存(三机 rev = a85c26b) | ✅ |
| 5 · tmp 清理 + 补 pack(39→0) | ✅ |

**全部 ✅**。三机(160/150/144)在堆叠顶端 `claude/remediation-stage-table` @ `a85c26b`,rev 一致、fast suite 全绿、e2e 全绿、金丝雀行为环路闭环、pack 补齐。**合 main 前置齐备**(合并动作另行安排,不在本手册)。

**遗留提醒**(供 wbai):
- 145(北京 10.9)无 ops 部署,不参与验证(与拓扑一致);上一份报告 145 相关声明已在本报告首节作废。
- 手册重建 dropbox 金丝雀的 snippet 建议补一句先 `rm -rf` 目录,避免陈旧文件干扰(本轮 3a 已遇到并处置)。
