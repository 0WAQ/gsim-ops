# Approve

人工审批 correlation 失败因子,`REJECTED → ACTIVE`。

## 适用范围

仅 `status == REJECTED` 且 `last_fail_stage == "correlation"`。其他失败阶段
(checkbias / checkpoint / compliance) 是因子质量问题,不允许 approve。

## 操作流程

1. `_resolve_targets` — 按 name / user 筛选
   - 单因子:状态或失败阶段不匹配 → 报错退出(明确指定的失败要响亮)
   - 批量(`-u`):不匹配的归入 `Skipped` 段,不阻断
2. apt 风格确认(`-y` 跳过)
3. `_approve_one`:
   - `store.transition(name, ACTIVE, entered_at=..., last_fail_stage=None, last_fail_reason=None)`
   - `append_check(CheckRecord(passed=True, fail_reason="approved"))` 留痕

## 不做的事

- 不动 `alpha_src` / `alpha_pnl` / `alpha_dump` / `alpha_feature`
  (correlation 失败时 check.py on_reject 已保留这些产物)
- 不动 `version`
- 不重跑任何 check 阶段
- 不替换 / 不降级库内既有因子

## 并发安全

每个因子操作包裹在 `factor_lock`。被占用则跳过(warn + locked 计数)。
