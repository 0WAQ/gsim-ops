# 验证流水线

`ops check` 对 staging 里每个因子顺序跑 7 个 stage。**stage 身份 / 顺序 / 路由的唯一真相源**
是 [`ops/services/check/stages.py`](../../ops/services/check/stages.py) 的 `PIPELINE` 元组
(新增 stage = 加一行)。checker 与 DataFirewall 深度见
[`../../ops/services/check/CLAUDE.md`](../../ops/services/check/CLAUDE.md)。

## 7 个 stage

| # | Stage | 回测窗口 | 作用 | 失败路由 |
|---|---|---|---|---|
| 0 | validate | 最小(2 天) | 验证代码/配置能启动 | **retryable** → SUBMITTED |
| 1 | checkbias | 一个月 | AST 注入 `@DataFirewall`,检前视偏差 | REJECTED |
| 2 | checkpoint | 5 天断点 | 断点续跑稳定性 | REJECTED |
| 3 | long_backtest | 全历史(2015–2025) | 纯跑,无检查,产 pnl+dump | **retryable** → SUBMITTED |
| 4 | compliance | —(读 long_backtest dump,全史逐日) | 仓位约束(见下) | REJECTED + 保产物 |
| 5 | correlation | — | 业绩门槛 + bcorr(见下) | REJECTED + 保产物 |
| 6 | archive | — | 测得快照落库,搬入 alpha_src | — |

窗口常量在 [`xml_prepare.py`](../../ops/services/check/xml_prepare.py)(`VALIDATE_WINDOW` 等);
每 stage 的 prepare 声明式改写 XML 窗口/dump 开关。

## 路由三态

由 Stage 表的 `retryable` / `keep_artifacts_on_fail` 声明派生:

- **retryable 失败**(validate / long_backtest,多属环境/配置问题)→ revert SUBMITTED,留
  staging,下次 `ops check` 无条件重扫自动重试。
- **REJECTED**(checkbias/checkpoint/compliance/correlation/archive)→ 因子质量问题,src 归档
  alpha_src,QR 须改代码重提。
- **keep_artifacts_on_fail**(compliance/correlation)→ 额外保留 pnl+dump(数据完整有分析
  价值);checkbias/checkpoint 失败清 dump/feature(短期数据不完整)。
- prepare 落盘失败 / 非 CheckFail 异常 → unexpected 臂,revert SUBMITTED + 完整日志。

**异常归因**:`CheckFail`/`CheckSkip` 不携带 stage——流水线按"当前正在跑的 stage"盖章
(`current_stage`)。原先 12 个硬编码 stage 的异常子类已删。

## checkbias DataFirewall

[`checkbias_checker.py`](../../ops/services/check/checker/checkbias_checker.py) 用 AST 把
`@DataFirewall(delay=X, data_attrs={...})` 注入因子的 `generate`,运行期只 wrap `dr.getData()`
的结果(用户 buffer 不 wrap)。前视规则按 delay + 数据维度:delay≥1 不许 `data[di]`;delay=0
日频不许 `data[di]`、日内可到 14:30。框架级静态数据(`STATIC_TAGS={'ipodate'}`)与 `valid`
(ALWAYS_ALLOW_DI)特殊放行。注入写临时 `{factor}_firewall.py`,不碰原 .py。

## compliance 门槛

[`compliance_checker.py`](../../ops/services/check/checker/compliance_checker.py),阈值在
`config.yaml -> checker.compliance`:

| 项 | 门槛 | 层 |
|---|---|---|
| 个股最大持仓 `max\|w\|/Σ\|w\|` | ≤ 5% | 普通违规 |
| 总持股数 | ≥ 100 | 普通违规 |
| 多头持股数 | ≥ 50 | 普通违规 |
| 空头持股数 | ≥ 50 | 普通违规 |
| 单日个股最大持仓 | ≤ `max_position_pct × hard_position_mult`(2× = 10%) | **严重违规(立拒)** |

判定(2026-07-16 重做,数据定策见
[`../design/compliance-survey.md`](../design/compliance-survey.md)):long_backtest 的
每日 dump **全史逐日**查(尾窗 762 已退役 —— 判定基数随数据起始漂移且漏检窗外违规);
空/全 NaN/零敞口天跳过不计(缺数据的早期天天然免疫);四条阈值任一违反记该日违规,
全史违规日 > `violation_tolerance`(10)才 REJECTED(放行早期毛刺);严重违规(单日超 2× 线)命中
(或 inf 坏权重日)立拒不吃容忍。dump 文件读失败跳过但计数告警(不静默当无效日)。

## correlation 门槛

[`correlation_checker.py`](../../ops/services/check/checker/correlation_checker.py),阈值在
`config.yaml -> checker.correlation`:

| 项 | 门槛 |
|---|---|
| ret%(年化) | ≥ 10.0 |
| shrp | > 2.0 |
| tvr%(换手,delay 分桶) | ≤ 60(d0)/ ≤ 50(d1) |
| bcorr | `abs(max_bcorr) < 0.7`,否则须**打败竞品** ≥2 项 |

对比池按 `discovery_method` 分池(`resolve_bcorr_pools`,automated/manual 各比各的;来源未知
回退全库——2026-07-13 discovery NOT NULL 后 check 路径恒有值,此支降为兜底)。bcorr 排除自名
(因子不与自己比)。任一项不达标 → REJECTED(日志含违反项)。

## 测得快照(archive + 失败路径)

archive 段:`transition` 设 `entered_at` → `_persist_derived` 采集四组(metrics / datasources /
bcorr / delay)→ `repo.attach_snapshot` 一次 insert `factor_snapshot`。**v3**:correlation
失败(CorrResult 已有 bcorr,零额外计算)、compliance 失败(补跑一次 simsummary)也写快照,
`measured_at` = 该次 check 事件 at——所以被拒因子在 `ops list` 也有指标。见
[data-model.md](data-model.md) 的 factor_snapshot。

## 并发

`ProcessPoolExecutor`(max 20 workers),每因子非阻塞 `factor_lock`(跨机 PG advisory,见
[topology.md](topology.md))。worker 里 Repository 按需现构造(fork 池安全)。

→ 回 [架构总览](../architecture.md#4-验证流水线ops-check)
