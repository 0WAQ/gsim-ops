# Approve

人工放行被 correlation stage 拒绝的因子,`REJECTED → ACTIVE`,不重跑 check。

## 为什么存在(定位:因子库多样性 / 数据覆盖的人工豁免)

自动流水线只优化**业绩 + 低相关**——correlation stage 卡业绩硬门槛(ret/shrp/tvr)
和相关性(bcorr 超阈值且打不过同池竞品,见 `checker/correlation_checker.py`
`_check_beat`,只看 fitness/ret/shrp 三项)。这套目标有个**根本盲区:它完全不看
数据使用覆盖**。

后果:一个用了库里稀缺数据(某张表 / 某个字段几乎没别的因子碰)的因子,若恰好和某老
因子相关、业绩又不占优,流水线**必拒且无自动路径可救**——哪怕它正是因子库最需要的
(它扩的是数据覆盖的多样性)。approve 就是对抗这个盲区的**唯一人工闸**:人判定某因子
对数据覆盖有独立价值,于是明知它相关/业绩不占优仍放行。

**这个价值与人工/机器是否分池无关,长期存在**——分池只改"和谁比相关性",不改"流水线
只认业绩、不认数据覆盖"这个盲区。配合 `ops list --filter-by field=X / tables=X`
(反查覆盖缺口)使用:先查哪些数据没几个因子用,再 approve 放一个补缺口的因子进来。

## 适用范围

仅 `status == REJECTED` 且 `last_fail_stage == "correlation"`。其他失败阶段
(checkbias / checkpoint / compliance)是因子**质量/正确性**问题,不属多样性豁免范畴,
不允许 approve。

**放行宽度是整个 correlation stage(业绩门槛 + 相关性),不收窄到只放 bcorr**——因为
"为覆盖多样性保留一个因子"本就可能意味着接受它业绩差一点(如 ret 8% 低于 10% 线,但它
开了一块没人碰的数据)。这是有意的宽度,不是滑坡。

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

---

Tests: `tests/test_lifecycle_cmds.py` (correlation-rejected approval, batch -u filters, non-correlation-reject skip).
