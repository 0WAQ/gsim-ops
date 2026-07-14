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

代码: `ops/services/check/checker/compliance_checker.py`。**不检查多/空比例**, 只检查个数 + 单股最大占比。现行判定窗口 = 尾部 762 个 dump 文件, 空/NaN 天静默跳过, 窗口内任一天任一项违规即整因子 REJECTED。

**⚠ 判定重做在路线图**(2026-07-13 立项, `.claude/plans.md` "Compliance 判定重做"):现行"尾窗按文件数截 + 空天静默跳过"使判定基数随数据起始时间漂移。用户拍板方向 = 检查每一天再判违规;**但阈值/起始日/容忍度全部先不定**——无全库分布数据无法评估政策(先测量后定策)。摸底脚本 `scripts/compliance_survey.py`(阈值无关逐日统计,未跑)。checker 未改前上述行为仍为当前生产事实。

## 回测区间

- **validate/checkbias 短回测**: 20241201 起(validate 20241201-02,checkbias 20241201-31)
- **长回测**: 20150101 - 20251231(完整历史)

## 失败归档(recycle 已退役)

**recycle 目录已于 2026-07 退役**(commit f576fd0)。失败因子的 src 归档到 `alpha_src/`(与 ACTIVE 同库),状态靠 state 的 `status`/`last_fail_stage`/`last_fail_reason` 区分,不靠目录位置。REJECTED 因子召回重跑用 `ops restage -s rejected`。**别再找 recycle 目录 / reason.txt**。

**Why:** 这些标准确保入库因子的质量和生产可用性。CheckPoint 尤其重要,因为生产环境可能随时重启。

**How to apply:**
- 开发因子时,如果使用状态变量,必须实现 checkpoint 方法
- `ops check` 失败原因看 state(`ops status <name>` 的 last_fail_stage/reason)或 check 报告 `docs/reports/check/`,不看 recycle
- 相关性检测用 `/usr/local/gsim/dataops/bcorr` 对比同 discovery_method 池(automated/manual 分池,见 `resolve_bcorr_pools`)
