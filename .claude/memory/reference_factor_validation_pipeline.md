---
name: factor-validation-pipeline
description: gsim 因子入库检测流程的各阶段检查项和入库标准
metadata: 
  node_type: memory
  type: reference
  originSessionId: 0dc2ca03-0ee3-4cac-b874-f0ee5ec0c49d
---

因子入库前需通过 `ops check` 的 7 阶段验证管道:`validate → checkbias → checkpoint → long_backtest → compliance → correlation → to_lib (入库 commit)`。

代码 `ops/services/check/check.py:249-275` 跑 6 个 checker, 通过后 `to_lib()` 入库, 算 7。

## 关键检查项

### CheckBias
每次调用 `generate()` 前，截断数据为 `[0, di)`，确保因子不使用未来数据。

### CheckPoint
检测因子在任意一天停止后能否恢复执行（跨进程）。

**常见问题**: 因子依赖 `self.prev` 等状态变量，但未持久化。

**解决方案**: 实现 `checkpointSave()` 和 `checkpointLoad()`：

```python
import pickle

class AlphaExample(AlphaBase):
    def checkpointSave(self, fh):
        pickle.dump(self.prev, fh)
    
    def checkpointLoad(self, fh):
        self.prev = pickle.load(fh)
```

### 性能指标 (correlation stage)

- **年化收益率**: ret% ≥ 10
- **夏普**: shrp > 2.0
- **换手率**: delay=1 tvr% ≤ 50, delay=0 tvr% ≤ 60(2026-06-05 加入,代替原单一 40 上限)
- **相关性**: bcorr < 0.7,否则需在 fitness/ret/shrp 至少 2 项打败高相关竞品

阈值都在 `config.yaml -> checker.correlation`(`ret% / tvr_d0% / tvr_d1% / shrp / corr_threshold`),按需调整。

### 仓位指标 (compliance stage, 配在 `config.yaml -> checker.compliance`)

- 个股最大持仓比例: `max_position_pct` ≤ 0.05
- 总持股数量 (long + short): `min_total_stocks` ≥ 100
- 多头最小持股数量: `min_long_stocks` ≥ 50
- 空头最小持股数量: `min_short_stocks` ≥ 50

代码: `ops/services/check/checker/compliance_checker.py`。**不检查多/空比例**, 只检查个数 + 单股最大占比。判定(2026-07-16 重做, PR #22): 全史逐日, 空/全NaN/零敞口天跳过不计; 全史违规日 > violation_tolerance(10)才拒; 单日个股占比 > max_position_pct × hard_position_mult(2×=10%, 含 inf 坏权重日)= 严重违规立拒, 不吃容忍; dump 读失败计数告警跳过(不静默当无效日)。

**判定重做已收官**(2026-07-16, PR #22 合 main):先测量后定策 —— 全库 7972 因子摸底 + 违规画像(`scripts/compliance_survey.py` / `compliance_profile.py`,材料存 `report/compliance-survey/`);影子对比 active 零状态变化,22 条 compliance-rejected → 严重违规仍拒 5 / 超容忍仍拒 5 / 转放行毛刺 12。compliance 测量不进 PG(单因子层是卫生闸,真约束在 combo 层)。详见 `docs/design/compliance-survey.md`。

## 回测区间

- **validate/checkbias 短回测**: 20241201 起(validate 20241201-02,checkbias 20241201-31)
- **长回测**: 20150101 - 20251231(完整历史)

## 失败归档(recycle 已退役)

**recycle 目录已于 2026-07 退役**(commit f576fd0)。失败因子的 src 归档到 `alpha_src/`(与 ACTIVE 同库),状态靠 state 的 `status` + factor_history 最近失败事件区分,不靠目录位置(v2b 起 last_fail_stage/last_fail_reason 列已删,读侧走 `Factor.last_fail` 派生)。REJECTED 因子召回重跑用 `ops restage -s rejected`。**别再找 recycle 目录 / reason.txt**。

**Why:** 这些标准确保入库因子的质量和生产可用性。CheckPoint 尤其重要,因为生产环境可能随时重启。

**How to apply:**
- 开发因子时,如果使用状态变量,必须实现 checkpoint 方法
- `ops check` 失败原因看 `ops status <name>`(factor_history 最近 check 事件的 failed_stage/fail_reason,fail_reason 一行自足)或 check 报告 `docs/reports/check/`,不看 recycle
- 相关性检测用 `/usr/local/gsim/dataops/bcorr` 对比同 discovery_method 池(automated/manual 分池,见 `resolve_bcorr_pools`)
