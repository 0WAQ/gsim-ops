"""Compliance stage:逐日持仓合规(个股集中度 + 多空/总持股数下限)。

**判定规则(2026-07-16 重做,数据定策见 docs/design/compliance-survey.md)**:
全历史逐日检查,不再截尾窗。三段:

1. **跳过无效日**:空 / 全 NaN / 零敞口(total==0)—— 缺数据的早期天天然免疫,
   不算违规(旧 checker 的 `check_window=762` 尾窗正是为规避早期暖机而设,现由
   "跳过无效日"从根上解决,尾窗连同其判定基数随数据起始漂移的毛病一并退役;
   **别再加回 check_window** —— 全史每日是有意为之)。
2. **违规容忍**:个股 max 占比 / 多空 / 总持股四条阈值,任一违反记该日为违规日;
   全史违规日数 > `violation_tolerance`(默认 10)才拒。摸底数据(7972 因子)显示
   违规两极分化 —— active 因子的违规都是 ≤2 天的早期毛刺,持续违规(≥24 天)全在
   已拒因子,中间是 2~24 的巨大空档,故小容忍度即可放行毛刺、拦住真违规。
3. **严重违规**:单日个股 max 占比 > `max_position_pct × hard_position_mult`
   (默认 2× = 10%)立即拒,不吃容忍额度 —— 防"平时干净、某天单票半仓"的
   灾难日被容忍度放过。

逐日四元组的 numpy 表达式与摸底脚本 `scripts/compliance_survey.py` 逐位一致
(该脚本已五问对抗验证,violations.csv 是本规则的影子回归材料)。

"全史"的真实上界 = long_backtest 所产 dump 的窗口(`xml_prepare.LONG_BACKTEST_WINDOW`,
非本 checker 决定):上调那个窗口端点时,本判定的基数随之静默扩张,届时重看容忍度。
"""
from collections import Counter
from pathlib import Path

import numpy as np
from loguru import logger

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
        # 违规日容忍上限(全史违规日 > 此值才拒);严重违规线 = max 占比上限 × 倍数
        self.violation_tolerance: int = c.get("violation_tolerance", 10)
        self.hard_position_pct: float = (
            self.max_position_pct * c.get("hard_position_mult", 2.0))

    def _day_stat(self, npy_file: Path) -> DayStat | None:
        """单日 dump 向量 → DayStat;无效日(空/全 NaN/零敞口)→ None(跳过)。

        逐日 numpy 表达式与 compliance_survey.py 逐位一致,不要偏离。
        np.load 失败**向上抛**(不再静默当无效日):容忍机制下丢一个违规日可能
        恰好翻转判定(viol_days 11→10 = 拒变过),读失败必须显形 —— 调用方计数
        告警,与 dumpscan.py "真错误不静默"同一条原则。"""
        data: np.ndarray = np.load(npy_file)
        if data.size == 0 or np.all(np.isnan(data)):
            return None

        valid_data = data[~np.isnan(data)]
        total_abs: np.float64 = np.sum(np.abs(valid_data))
        if total_abs == 0:                        # 零敞口 = 无效日
            return None

        long_positions = valid_data[valid_data > 0]
        short_positions = valid_data[valid_data < 0]
        max_abs = np.max(np.abs(valid_data, dtype=np.float64))
        # ±inf 坏权重日:max/total = inf/inf = NaN,四条阈值比较恒 False(与
        # survey/profile 逐位一致);严重违规侧不继承这个洞 —— check() 用
        # isfinite 显式立拒(库内存量数据无 inf,已核 summary.csv,无影子发散
        # 面)。errstate 压 NaN 除法在 worker 日志里的 RuntimeWarning 噪音。
        with np.errstate(invalid="ignore"):
            return DayStat(
                date=npy_file.name[0:8],
                max_pos_pct=float(max_abs / total_abs),
                long_count=long_positions.size,
                short_count=short_positions.size,
                avg_long_pct=float(np.sum(long_positions) / total_abs * 100),
                avg_short_pct=float(np.sum(np.abs(short_positions)) / total_abs * 100),
            )

    def _violations(self, d: DayStat) -> list[tuple[str, str]]:
        """该日违反的阈值规则,(规则标签, 明细) 列表(空 = 合规)。

        标签供拒绝消息里的分规则计数 —— fail_reason 是被拒因子唯一的持久记录
        (compliance 测量有意不进 PG:单因子层是卫生闸,真约束在 combo 层),
        消息必须一行自足(风格契约见 base.CheckFail)。"""
        v: list[tuple[str, str]] = []
        if d.max_pos_pct > self.max_position_pct:
            v.append(("单票集中",
                      f"个股占比 {d.max_pos_pct*100:.2f}% > {self.max_position_pct*100}%"))
        total = d.long_count + d.short_count
        if total < self.min_total_stocks:
            v.append(("总数不足",
                      f"总持股 {total}(多{d.long_count}+空{d.short_count}) < {self.min_total_stocks}"))
        if d.long_count < self.min_long_stocks:
            v.append(("多头不足", f"多头 {d.long_count} < {self.min_long_stocks}"))
        if d.short_count < self.min_short_stocks:
            v.append(("空头不足", f"空头 {d.short_count} < {self.min_short_stocks}"))
        return v

    def check(self, factor: AlphaMetadata) -> CompResult:
        npy_files = v2npy_files(factor.alpha_dir)
        if not npy_files:
            raise CheckSkip("未找到 v2 版本的 npy 文件")

        days: list[DayStat] = []
        # 严重违规(单日超 2× 上限)命中即刻拒,不看容忍度;普通违规逐日累计,
        # 全貌(分规则天数/最长连违/最近违规日)进 fail_reason —— 一行自足
        severe_hit: str | None = None
        severe_days = 0
        viol_days = 0
        rule_days: Counter[str] = Counter() # 规则标签 → 违规日数
        streak = max_streak = 0             # 连违按有效日序列算(无效日不断链)
        last_viol: str | None = None
        last_detail = ""                    # 最近一个违规日的具体数字(佐证)
        bad_reads: list[str] = []           # np.load 失败的文件(损坏/权限)

        for npy_file in npy_files:          # 全历史,不截尾窗
            try:
                d = self._day_stat(npy_file)
            except Exception as e:
                # 读失败 ≠ 无效日:跳过但显形(丢的可能恰是压秤的违规日)
                bad_reads.append(f"{npy_file.name}({type(e).__name__})")
                continue
            if d is None:                   # 无效日:缺数据的早期天天然跳过
                continue
            days.append(d)

            if not np.isfinite(d.max_pos_pct):
                # inf 坏权重日(max/total=inf/inf=NaN):阈值比较测不到,
                # 但"单票 inf"正是严重违规要挡的灾难形态,显式立拒不继承旧洞
                severe_days += 1
                severe_hit = severe_hit or f"个股占比非有限值(inf 坏权重) ({d.date})"
            elif d.max_pos_pct > self.hard_position_pct:
                severe_days += 1
                severe_hit = severe_hit or (
                    f"单日个股占比={d.max_pos_pct*100:.2f}% > "
                    f"严重违规线{self.hard_position_pct*100:.1f}% ({d.date})")
            viol = self._violations(d)
            if viol:
                viol_days += 1
                streak += 1
                max_streak = max(max_streak, streak)
                last_viol = d.date
                last_detail = "; ".join(m for _, m in viol)
                for tag, _ in viol:
                    rule_days[tag] += 1
            else:
                streak = 0

        if bad_reads:
            logger.warning(
                "compliance factor={} 有 {} 个 dump 文件读取失败被跳过"
                "(容忍边界附近可能影响判定): {}{}",
                factor.name, len(bad_reads), ", ".join(bad_reads[:5]),
                " ..." if len(bad_reads) > 5 else "")

        if not days:
            raise CheckSkip("持仓全空")

        # 严重违规优先:单日灾难不因"总违规天数少"被容忍度放过
        if severe_hit is not None:
            raise CheckFail(
                f"{severe_hit} | 超线共{severe_days}天, "
                f"全史违规{viol_days}/{len(days)}天")

        if viol_days > self.violation_tolerance:
            breakdown = ", ".join(f"{t}{n}天" for t, n in rule_days.most_common())
            raise CheckFail(
                f"违规天数={viol_days}/{len(days)} > 容忍{self.violation_tolerance} | "
                f"{breakdown}, 最长连违{max_streak}天, "
                f"最近{last_viol}: {last_detail}")

        # 通过:CompResult 是接口一致性占位(流水线只关心是否抛),全史均值口径
        return CompResult(
            np.mean([d.avg_long_pct for d in days], dtype=np.float64),
            np.mean([d.avg_short_pct for d in days], dtype=np.float64),
            int(np.mean([d.long_count for d in days])),
            int(np.mean([d.short_count for d in days])),
            len(days))
