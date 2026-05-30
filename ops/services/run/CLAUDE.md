# Run

手动运行因子 backtest（独立于 check pipeline）。

## 用途

在指定日期范围内跑因子 backtest + simsummary，不走 check pipeline 的状态流转。适合调试、验证因子在特定时间段的表现。

## 流程

1. `scan_factors` — 扫描 `alpha_src` + `staging`，按 user / factor_name 过滤
2. 对每个因子:
   - `_override_dates` — 临时修改 XML 的 startdate/enddate
   - `Runner.run_backtest` — 跑 gsim backtest
   - `Runner.run_simsummary` — 输出 metrics
   - `_restore_dates` — finally 恢复原始日期（即使 backtest 失败）

## 并发

`ProcessPoolExecutor(max_workers=min(20, total))`，每个因子包裹在 `factor_lock` 中。

## 与 check 的区别

- 不修改 state（不 transition）
- 不做 checkbias / compliance / correlation
- 不 move 文件
- 纯粹跑 backtest + 输出结果
