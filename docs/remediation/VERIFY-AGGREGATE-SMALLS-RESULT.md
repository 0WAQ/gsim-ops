# 增量验证结果 · 小件收官批(160 单机)

**执行**:server-160(10.9.100.160),2026-07-11
**分支**:`claude/factor-aggregate-smalls`
**160 rev**:`e3d538c`(手册 commit)/ 代码 tip `3c275f7`(基于 main `2507f40`)

**总判定**:✅ 全绿。阶段 0-4 全部通过,Total 前后一致(8252),金丝雀零残留,
三表级联清零,created_at 时区正确。阶段 5(150/144)按手册跳过。

---

## 阶段 0 · 部署 + 静态门禁

`git status -sb`(仅 pre-existing 未跟踪文件,无跟踪改动):
```
## claude/factor-aggregate-smalls...origin/claude/factor-aggregate-smalls
?? docs/reports/check/check-fguo-20260709-235302.json
?? docs/reports/check/check-lhw-20260710-031451.json
?? docs/reports/check/check-xmf-20260710-122605.json
?? docs/reports/check/check-zxu-20260710-192411.json
?? pgreadonlysetup.sql
```

`git log --oneline -2`:
```
e3d538c docs: 小件收官批验证手册(160 执行者用)
3c275f7 refactor: 小件收官批 —— S8 metric 注册表 + AlphaMetadata 去 I/O + results 空壳清理 + created_at 收敛
```

静态门禁:
```
=== ruff ===        All checks passed!
=== pyright ===     0 errors, 0 warnings, 0 informations
=== lint-imports === Contracts: 7 kept, 0 broken.
=== checkpoint del === ImportError: cannot import name 'checkpoint' from 'ops.core.alpha.results'
```

**注**:手册预期 `ModuleNotFoundError`,实际命令 `from ...results import checkpoint`
抛 `ImportError: cannot import name 'checkpoint'`。同一根因(空壳模块已删),
`ModuleNotFoundError` 本身是 `ImportError` 子类;差异仅在于手册作者设想的 import
写法(`import ...results.checkpoint` 才会给 ModuleNotFoundError)。验证目标
(checkpoint 空壳已删)成立。

## 阶段 1 · fast suite 含 PG 组

```
108 passed, 8 skipped, 6 deselected in 3.35s
```
passed = 108,与预期一致(基线 106 + 本批新增 2);0 failed(硬线满足)。

两个点名新用例(单独复跑确认):
```
tests/test_pure.py::test_metric_registry_is_single_source PASSED
tests/test_pure.py::test_dumpscan_layout_and_order PASSED
============================== 2 passed in 0.45s ===============================
```

## 阶段 2 · e2e(真 gsim + cc)

```
6 passed, 116 deselected in 109.95s (0:01:49)
```
6 passed,与预期一致。compliance(v2npy_files)/ checkpoint(last_v2npy_file)换
dumpscan 后,逐 stage 确定性失败因子真跑到这两个 stage,行为级回归通过。

## 阶段 3 · 只读冒烟(生产 config)

```
=== list Total ===
Total: 8252 factors
```
Total 8252,与上轮基线一致。

`--sort-by bcorr | head -5`(bcorr 列按绝对值降序,原文头两行):
```
 name                     author   delay   ret%   shrp    mdd%    tvr%   fitness   bcorr   fail_stage
 AlphaFguo20260402LLM006  fguo         1   8.73   2.32   10.57   19.37      1.56    1.00   correlation
 AlphaFguo20260402LLM007  fguo         1   8.73   2.32   10.57   19.37      1.56    1.00   correlation
```

```
=== ret>30,shrp>2 Total ===   Total: 55 factors
=== bcorr>0.3 json len ===    8097
```

`--filter-by "ret=>30"` 报错原文:
```
Unknown operator: '=>' (did you mean '>='). Supported: !=, <, <=, =, >, >=
```

`--sort-by delay` 报错原文(choices 从注册表派生,delay 不在):
```
ops list: error: argument --sort-by: invalid choice: 'delay' (choose from ret, shrp, mdd, tvr, fitness, bcorr)
```

**bcorr 交叉核对**(注册表 SQL/内存两半 vs PG 真值):
```
SELECT count(*) FROM factor_snapshot n JOIN factor_state s ON s.name=n.name
 WHERE s.status != 'submitted' AND abs(n.max_bcorr) > 0.3;
 count
-------
  8097
```
PG count 8097 = json 长度 8097,三方一致。

## 阶段 4 · 金丝雀行为环路(生产库,轻量版)

前置:`NOPASSWD-OK`;金丝雀零残留;两份 config + dropbox 金丝雀按 VERIFY-PV7
阶段 0 重建(`config.verify.yaml` corr=1.01 / `config.verify-pv7.yaml` corr=0.7;
本批 4a 只用 verify.yaml)。

### 4a · 入库全通(compliance/checkpoint 走 dumpscan)

submit:`✔ AlphaWbaiCanary001 → submitted (version=1)`

check(config.verify.yaml)汇总行:
```
[1/1] AlphaWbaiCanary001  → lib
✔ 通过 :    1
报告 : docs/reports/check/check-AlphaWbaiCanary001-20260711-101722.json
```

`ops status`:
```
  name           AlphaWbaiCanary001
  status         active
  submitted_at   2026-07-11T10:16:47
  entered_at     2026-07-11T10:17:21
  check_history  (1)
    [1] 2026-07-11T10:17:08 → 2026-07-11T10:17:21  PASS
```

check 报告 rollup(报告存 rollup 非逐 stage;`failed_stage: None` = 流水线跑完
compliance+checkpoint 全程):
```
summary: {'total': 1, 'pass': 1, 'fail': 0, 'error': 0, 'locked': 0}
outcome: pass  passed: True  failed_stage: None  fail_reason: None
```
判定:7 stage 全过 → ACTIVE。checkpoint 通过 = last_v2npy_file 行为证据;
compliance 通过 = v2npy_files 时序窗口证据。

### 4b · created_at 时区/格式核对

```
SELECT created_at, now(), (now() - created_at) < interval '1 hour' AS fresh
 FROM factor_info WHERE name = 'AlphaWbaiCanary001';
       created_at       |              now              | fresh
------------------------+-------------------------------+-------
 2026-07-11 02:16:47+00 | 2026-07-11 02:18:57.046863+00 | t
```
fresh = t。时区正确:本地 10:16:47 CST 存为 02:16:47+00 UTC(同一时刻,tz-aware,
非偏 8h 的 UTC 误写)。

`ops info | head`(节选):
```
Factor: AlphaWbaiCanary001  (author: wbai, status: active)
  Metrics (入库时快照): ret% 12.42 / shrp 1.18 / mdd% 40.41 / tvr% 78.29 / fitness 0.47
  snapshot_at: 2026-07-11T10:17:21   (= entered_at)
  Data Sources: Basedata, Interval5m / Interval5m.close, volume
```

### 4c · 清理

`ops rm -y` 输出(节选):
```
  ✔ 已删除 alpha_dump/AlphaWbaiCanary001
  ✔ 已删除 alpha_src/AlphaWbaiCanary001/
  ✔ 已删除 alpha_pnl/AlphaWbaiCanary001
  ✔ 已删除 pnl_manual/AlphaWbaiCanary001
  ✔ 已删除 factor_info (级联删除 state + snapshot)
```

三表零行:
```
 ?column? | count
----------+-------
 info     |     0
 state    |     0
 snap     |     0
```

清 config/dropbox/report 后零残留复查(全部无输出);`ops list` Total:
```
Total: 8252 factors
```
Total 回到基线 8252。`git status -sb` 干净(仅 pre-existing 未跟踪文件)。

## 阶段 5 · 150/144

按手册跳过(smalls 未合 main,不滚三机;混版本兼容性已在手册头部判定)。

## 结论

阶段 0-4 全绿,无任何一步偏离预期。合 main 前置齐备。
160 rev:`e3d538c`(手册)/ 代码 `3c275f7`。

---

## 三机滚存到 main(2026-07-11)

PR #7 已合 main(`cda9dbb`,含小件收官批)。三机各自
`git checkout main && git pull origin main && uv sync --group dev`,
跑 fast suite + lint-imports + `ops list` Total。三机 rev 一致,0 failed 硬线满足,
Total 三机一致 = 基线 8252。

**注**:150/144 首轮 `git pull` 均撞 GitHub 出网故障(150 `curl 28` 连接超时;
144 `GnuTLS recv error (-110)`),由 wbai 手动 pull 到 `cda9dbb` 后重试其余步骤,
未自行重试网络操作。150/144 非交互 SSH 的 PATH 不含 `~/.local/bin`,`ops list`
用 uv-tool 绝对路径 `~/.local/bin/ops` 取(生产 ops 走 uv tool 独立环境)。

### server-160

```
---REV---   cda9dbb docs: 小件收官批验证判读 + PR #7 合 main 收账(JOURNAL)
---FAST---  108 passed, 8 skipped, 6 deselected in 3.03s
---LINT---  Contracts: 7 kept, 0 broken.
---TOTAL--- Total: 8252 factors
```

### server-150

```
---REV---   cda9dbb docs: 小件收官批验证判读 + PR #7 合 main 收账(JOURNAL)
---FAST---  108 passed, 8 skipped, 6 deselected in 3.12s
---LINT---  Contracts: 7 kept, 0 broken.
---TOTAL--- Total: 8252 factors
```

### intel-workstation-144

```
---REV---   cda9dbb docs: 小件收官批验证判读 + PR #7 合 main 收账(JOURNAL)
---FAST---  108 passed, 8 skipped, 6 deselected in 62.50s (0:01:02)
---LINT---  Contracts: 7 kept, 0 broken.
---TOTAL--- Total: 8252 factors
```

(144 fast suite 62.5s —— 跨段 LAN→IDC 读盘慢,属正常;passed 数与 160/150 一致。)

### 结论

三机 rev 全部 `cda9dbb`,fast suite 均 108 passed / 0 failed,lint-imports 均 7/7,
`ops list` Total 三机一致 = 8252。小件收官批滚存三机收官。

