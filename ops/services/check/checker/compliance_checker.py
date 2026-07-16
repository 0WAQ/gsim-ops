"""Compliance stage:逐日持仓合规(个股集中度 + 多空/总持股数下限)。

**判定规则(2026-07-16 重做,数据定策见 docs/design/compliance-survey.md)**:
全历史逐日检查,不再截尾窗。三段:

1. **跳过无效日**:空 / 全 NaN / 零敞口(total==0)—— 缺数据的早期天天然免疫,
   不算违规(旧 checker 的 `check_window=762` 尾窗正是为规避早期暖机而设,现由
   "跳过无效日"从根上解决,尾窗连同其判定基数随数据起始漂移的毛病一并退役;
   **别再加回 check_window** —— 全史每日是有意为之)。
2. **软线容忍**:个股 max 占比 / 多空 / 总持股四条阈值,任一违反记该日为违规日;
   全史违规日数 > `violation_tolerance`(默认 10)才拒。摸底数据(7972 因子)显示
   违规两极分化 —— active 因子的违规都是 ≤2 天的早期毛刺,持续违规(≥24 天)全在
   已拒因子,中间是 2~24 的巨大空档,故小容忍度即可放行毛刺、拦住真违规。
3. **硬顶**:单日个股 max 占比 > `max_position_pct × hard_position_mult`(默认 2×
   = 10%)立即拒,不吃容忍额度 —— 防"平时干净、某天单票半仓"的灾难日被容忍度放过。

逐日四元组的 numpy 表达式与摸底脚本 `scripts/compliance_survey.py` 逐位一致
(该脚本已五问对抗验证,violations.csv 是本规则的影子回归材料)。
"""
from pathlib import Path

import numpy as np

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.compliance import CompResult
from ops.infra.config import Config

from .base import Checker, CheckFail, CheckSkip
from .dumpscan import v2npy_files


class DayStat:
    """单个有效交易日的持仓摘要(无效日不产生 DayStat,见 _day_stat 返回 None)。"""

    def __init__(self, date: str, max_pos_pct: float,
                 long_count: int, short_count: int,
                 avg_long_pct: float, avg_short_pct: float):
        self.date = date
        self.max_pos_pct = max_pos_pct
        self.long_count = long_count
        self.short_count = short_count
        self.avg_long_pct = avg_long_pct
        self.avg_short_pct = avg_short_pct


class ComplianceChecker(Checker):
    def __init__(self, config: Config):
        self.config = config
        c = config.compliance
        self.max_position_pct: float = c["max_position_pct"]
        self.min_total_stocks: int = c["min_total_stocks"]
        self.min_long_stocks: int = c["min_long_stocks"]
        self.min_short_stocks: int = c["min_short_stocks"]
        # 软线违规日容忍上限(全史违规日 > 此值才拒);硬顶 = 软线 max 占比 × 倍数
        self.violation_tolerance: int = c.get("violation_tolerance", 10)
        self.hard_position_pct: float = (
            self.max_position_pct * c.get("hard_position_mult", 2.0))

    def _day_stat(self, npy_file: Path) -> DayStat | None:
        """单日 dump 向量 → DayStat;无效日(读失败/空/全 NaN/零敞口)→ None(跳过)。

        逐日 numpy 表达式与 compliance_survey.py 逐位一致,不要偏离。"""
        try:
            data: np.ndarray = np.load(npy_file)
        except Exception:
            return None
        if data.size == 0 or np.all(np.isnan(data)):
            return None

        valid_data = data[~np.isnan(data)]
        total_abs: np.float64 = np.sum(np.abs(valid_data))
        if total_abs == 0:                        # 零敞口 = 无效日
            return None

        long_positions = valid_data[valid_data > 0]
        short_positions = valid_data[valid_data < 0]
        max_abs = np.max(np.abs(valid_data, dtype=np.float64))
        return DayStat(
            date=npy_file.name[0:8],
            max_pos_pct=float(max_abs / total_abs),
            long_count=long_positions.size,
            short_count=short_positions.size,
            avg_long_pct=float(np.sum(long_positions) / total_abs * 100),
            avg_short_pct=float(np.sum(np.abs(short_positions)) / total_abs * 100),
        )

    def _soft_violations(self, d: DayStat) -> list[str]:
        """该日违反的软线规则(空 = 合规)。"""
        v: list[str] = []
        if d.max_pos_pct > self.max_position_pct:
            v.append(f"个股最大持仓 {d.max_pos_pct*100:.2f}% > {self.max_position_pct*100}%")
        total = d.long_count + d.short_count
        if total < self.min_total_stocks:
            v.append(f"总持股 {total}(多 {d.long_count}+空 {d.short_count})< {self.min_total_stocks}")
        if d.long_count < self.min_long_stocks:
            v.append(f"多头持股 {d.long_count} < {self.min_long_stocks}")
        if d.short_count < self.min_short_stocks:
            v.append(f"空头持股 {d.short_count} < {self.min_short_stocks}")
        return v

    def check(self, factor: AlphaMetadata) -> CompResult:
        npy_files = v2npy_files(factor.alpha_dir)
        if not npy_files:
            raise CheckSkip("未找到 v2 版本的 npy 文件")

        days: list[DayStat] = []
        # 硬顶命中即刻拒(不看容忍度);软线逐日累计违规日数
        hard_hit: str | None = None
        viol_days = 0
        viol_examples: list[str] = []       # 头几条软线违规,进日志

        for npy_file in npy_files:          # 全历史,不截尾窗
            d = self._day_stat(npy_file)
            if d is None:                   # 无效日:缺数据的早期天天然跳过
                continue
            days.append(d)

            if hard_hit is None and d.max_pos_pct > self.hard_position_pct:
                hard_hit = (f"{d.date}: 个股最大持仓 {d.max_pos_pct*100:.2f}% "
                            f"超硬顶 {self.hard_position_pct*100:.1f}%")
            soft = self._soft_violations(d)
            if soft:
                viol_days += 1
                if len(viol_examples) < 10:
                    viol_examples.append(f"{d.date}: {'; '.join(soft)}")

        if not days:
            raise CheckSkip("持仓全空")

        # 硬顶优先:单日灾难不因"总违规天数少"被容忍度放过
        if hard_hit is not None:
            raise CheckFail(f"硬顶违规(单日立拒): {hard_hit}")

        if viol_days > self.violation_tolerance:
            head = "; ".join(viol_examples)
            more = f" (另有 {viol_days - 10} 天)" if viol_days > 10 else ""
            raise CheckFail(
                f"全史 {viol_days}/{len(days)} 天违规,超容忍上限 "
                f"{self.violation_tolerance} 天: {head}{more}")

        # 通过:CompResult 是接口一致性占位(流水线只关心是否抛),全史均值口径
        return CompResult(
            np.mean([d.avg_long_pct for d in days], dtype=np.float64),
            np.mean([d.avg_short_pct for d in days], dtype=np.float64),
            int(np.mean([d.long_count for d in days])),
            int(np.mean([d.short_count for d in days])),
            len(days))
