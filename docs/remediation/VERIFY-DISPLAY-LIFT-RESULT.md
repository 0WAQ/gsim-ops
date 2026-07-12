# VERIFY: claude/display-lift 展示层上收复验

- 分支: `claude/display-lift` @ `00beef1` (refactor: 展示层上收 —— list/status/info 渲染迁 cli,C9 契约钉住)
- 复验机: 160 (server-160, 生产 JFS + PG)
- 日期: 2026-07-12
- 结论: **全绿**。list/status/info 三条纯读命令渲染与 main 语义一致;filter 错误路径行为符合预期。仅跑读命令,未碰写命令,无窗口。

## 结论要点

- 展示层上收 diff 核心限于 `ops/cli/{list,status,info}.py`(承接渲染)+ `ops/services/{list,status,info}/*.py`(瘦身),行为不变。
- `ops/core/factor.py` 分支与 main 完全一致(`git diff main...` 空),因此 list/list-json 输出里的 `stale 快照` WARNING 是既有数据层行为(AlphaWbaiReversal / Fguo* 的 `snapshot_at != entered_at`),非本分支引入。
- filter 错误信息整行(而非仅标签)红是唯一预期视觉变化;piped 输出无 ANSI(rich 对非 TTY 自动关色),内容正确,红线仅在真实终端渲染。

## pytest

```
134 passed, 6 deselected in 3.58s
```

(预期 134 passed;deselected 6 为 slow/e2e,符合 `-m "not slow"`)

## list

### `ops list | tail -5`
```
 AlphaZxu_260709_HighRetMktCubeCoK_d…   zxu          1    17.68    3.21     6.66    32.50      2.37    0.56
 AlphaZxu_260709_RetAmpExpMomCoP_del…   zxu          1    18.88    3.69     7.37    31.19      2.87    0.69
 AlphaZxu_260709_RetMktCubeCoK_delay1   zxu          1    34.43    4.48    11.43    43.57      3.98    0.68

Total: 8252 factors
```

### `ops list -u wbai --sort-by shrp -n 5`
```
WARNING | ops.core.factor:__post_init__:89 - Factor AlphaWbaiReversal: snapshot_at=2026-07-04T02:18:55 != entered_at=None (stale 快照,需对账)

 name                        author   delay    ret%   shrp    mdd%    tvr%   fitness   bcorr   fail_stage
 ─────────────────────────────────────────────────────────────────────────────────────────────────────────
 AlphaZxu_260414_Ret_W_amo   wbai         0   31.13   4.35    7.96   42.97      3.71    0.89
 AlphaZxu_260414_VOV         wbai         0   20.98   4.12    4.08   13.64      5.11    0.69
 AlphaWbaiReversal           wbai         0   12.44   1.19   40.41   78.29      0.47    0.67   correlation

Total: 3 factors
```
(WARNING 为既有数据层行为,见上;排序/表格正常)

### `ops list --format json -n 2`  (stdout 经 json.load 校验)
```
records: 2
[
  {
    "name": "AlphaCchangAmtAwareRev",
    "author": "cchang",
    "status": "active",
    "delay": 1,
    "metrics": {"ret%": 14.33, "tvr%": 35.27, "shrp": 2.63, "mdd%": 13.53, "fitness": 1.67},
    "datasources": {"fields": ["ashareeodprices.s_dq_adjclose", "ashareeodprices.s_dq_amount", "industry", "volume"], "tables": ["Basedata", "ashareeodprices"]},
    "bcorr": {"max_bcorr": 0.63943, "max_bcorr_factor": "AlphaJzhangNEMF"}
  },
  {
    "name": "AlphaCchangIntraOvnFusion",
    "author": "cchang",
    "status": "active",
    "delay": 1,
    "metrics": {"ret%": 21.89, "tvr%": 30.61, "shrp": 3.5, "mdd%": 15.67, "fitness": 2.96},
    "datasources": null,
    "bcorr": {"max_bcorr": 0.69136, "max_bcorr_factor": "AlphaZxu_260415_RSJ_delay1"}
  }
]
```
(JSON 良构,恰好 2 条;stderr 的 WARNING 洪流是加载全集时数据层告警,与 `-n` 截断的 payload 无关)

### `ops list -s rejected -n 5`  (fail_stage 列)
```
 name                      author   delay     ret%    shrp     mdd%    tvr%   fitness   bcorr   fail_stage
 ──────────────────────────────────────────────────────────────────────────────────────────────────────────
 AlphaFguo20260401LLM001   fguo         1   -16.40   -3.60   182.62   25.79     -2.87    0.49   correlation
 AlphaFguo20260401LLM002   fguo         1     8.12    2.37     6.69   11.29      2.01    0.46   correlation
 AlphaFguo20260401LLM003   fguo         1    -7.18   -2.03   101.73   22.85     -1.14    0.49   correlation
 AlphaFguo20260401LLM004   fguo         1     6.39    1.47    12.42    8.76      1.26    0.36   correlation
 AlphaFguo20260401LLM005   fguo         1   -26.66   -4.50   294.50   21.03     -5.07    0.35   correlation

Total: 5 factors
```
(fail_stage 列正常填充 correlation)

### `ops list --filter-by "ret=>30,bogus=1"`  (错误路径)
```
Unknown operator: '=>' (did you mean '>='). Supported: !=, <, <=, =, >, >=
Unknown filter key: 'bogus'. Supported: bcorr, field, fitness, mdd, ret, shrp, tables, tvr
```
两行错误信息 + 无表格输出,符合预期。整行红仅在真实 TTY 渲染(piped 无 ANSI)。

## status

### `ops status | tail -8`
```
 AlphaZxu_260706_OperEffGap_delay1        sub…   2026-07-10T19…
 AlphaZxu_260706_ValueGap_delay1          sub…   2026-07-10T19…
 AlphaZxu_260706_XlTradeRetCorr_delay1    act…   2026-07-10T19…
 AlphaZxu_260709_HighRetMktCubeCoK_delay1 act…   2026-07-10T19…
 AlphaZxu_260709_RetAmpExpMomCoP_delay1   act…   2026-07-10T19…
 AlphaZxu_260709_RetMktCubeCoK_delay1     act…   2026-07-10T19…
━━━━━━━━━━━━━━ (rule) ━━━━━━━━━━━━━━
```

### `ops status AlphaWbaiReversal`  (含 11 条 check_history)
```
━━━━ 因子状态 · AlphaWbaiReversal ━━━━
  name           AlphaWbaiReversal
  author         wbai
  status         rejected
  submitted_at   2026-04-27T17:40:10
  entered_at     —
  rejected_at    2026-06-05T12:40:51
  updated_at     2026-06-05T12:40:51
  last_fail      correlation — shrp=1.19 <= 2.0; tvr%=78.29 > 60.0 (delay=0) | ret=12.44%, shrp=1.19, mdd=40.41%, tvr=78.29%, fitness=0.47
  check_history  (11)
    [1] ... FAIL  (correlation ...)
    [2] ... SKIP  (checkpoint)
    [3] ... FAIL  (correlation ...)
    [4]/[5] SKIP (checkpoint old=NONE new=NONE)
    [6] FAIL (checkbias: io.UnsupportedOperation: not readable)
    [7]/[8]/[9] FAIL (validate: module 'gsim.stats' has no attribute 'StatsSimpleV6')
    [10] SKIP (checkpoint old=NONE new=NONE)
    [11] FAIL (correlation: shrp=1.19 <= 2.0; tvr%=78.29 > 60.0 ...)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
(详情面板 + 完整 check_history 多行 traceback 渲染正常)

### `ops status AlphaNopeNotExist`
```
未找到因子: AlphaNopeNotExist
```

## info

### `ops info AlphaWbaiReversal`  (树渲染)
```
Factor: AlphaWbaiReversal  (author: wbai, status: rejected)
├── Paths
│   ├── Source:      /tank/vault/alphalib/alpha_src/AlphaWbaiReversal
│   ├── Dump:        /tank/vault/alphalib/alpha_dump/AlphaWbaiReversal
│   └── PNL:         /tank/vault/alphalib/alpha_pnl/AlphaWbaiReversal
├── Statistics
│   ├── Dump Days:   2674
│   ├── Date Range:  20150105 ~ 20251231
│   └── Has PNL:     Yes
├── Metrics (入库时快照)
│   ├── ret%:        12.44
│   ├── shrp:        1.19
│   ├── mdd%:        40.41
│   ├── tvr%:        78.29
│   ├── fitness:     0.47
│   └── snapshot_at: 2026-07-04T02:18:55
└── Data Sources (入库时)
    ├── Tables:      Basedata, Interval5m
    └── Fields:      Interval5m.close, volume
```
(树结构完整,四段 Paths/Statistics/Metrics/Data Sources 正常)

### `ops info AlphaNopeNotExist`
```
Factor not found: AlphaNopeNotExist (factor_info 无记录)
用 ops list / ops status 确认名字;盘上目录与 PG 的漂移属对账问题
```
