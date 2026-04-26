import numpy as np
from pathlib import Path
from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.compliance import *


class ComplianceSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("compliance", *args)

class ComplianceFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("compliance", *args)


class Position:
    def __init__(self,
                 long_pct: np.float64,
                 shrot_pct: np.float64,
                 long_count: int,
                 shrot_count: int):
        self.long_pct = long_pct
        self.short_pct = shrot_pct
        self.long_count = long_count
        self.short_count = shrot_count

class ComplianceChecker:
    def __init__(self, config: Config):
        self.config = config
        self.max_position_pct: float = config.compliance["max_position_pct"]
        self.min_total_stocks: int = config.compliance["min_total_stocks"]
        self.min_long_stocks: int = config.compliance["min_long_stocks"]
        self.min_short_stocks: int = config.compliance["min_short_stocks"]
    
    def _check_position(self, npy_file: Path) -> Position | None:
        try:
            data: np.ndarray = np.load(npy_file)
        except Exception:
            return None

        # 检查数据是否为空或全为 NaN
        if data.size == 0 or np.all(np.isnan(data)):
            return None
        
        # 计算总金额 (买入 + 卖出的绝对值)
        valid_data = data[~np.isnan(data)]
        total_abs_amount: np.float64 = np.sum(np.abs(valid_data))
        if total_abs_amount == 0:
            return None
        
        date = npy_file.name[0:8]

        # 分离多空持仓
        long_positions = valid_data[valid_data > 0]
        short_positions = valid_data[valid_data < 0]
        
        long_count = long_positions.size
        short_count = short_positions.size

        # 1. 检查个股最大持仓
        max_abs_position = np.max(np.abs(valid_data, dtype=np.float64))
        max_position_pct = max_abs_position / total_abs_amount
        if max_position_pct > self.max_position_pct:
            raise ComplianceFail(
                f"{date}: 个股最大持仓 {max_position_pct*100:.2f}% 超过 {self.max_position_pct*100}%")
        
        # 2. 检查总持股数量
        total_stock_count = long_count + short_count
        if total_stock_count < self.min_total_stocks:
            raise ComplianceFail(
                f"{date}: 总持股数量 {total_stock_count} 只 (多头 {long_count} + 空头 {short_count}) 少于 {self.min_total_stocks} 只")
        
        # 3. 检查多头持股数量
        if long_count < self.min_long_stocks:
            raise ComplianceFail(
                f"{date}: 多头持股数量 {long_count} 只少于 {self.min_long_stocks} 只")

        # 4. 检查空头持股数量
        if short_count < self.min_short_stocks:
            raise ComplianceFail(
                f"{date}: 空头持股数量 {short_count} 只少于 {self.min_short_stocks} 只")

        # 计算平均持仓比例
        avg_long_pct = np.sum(long_positions) / total_abs_amount * 100 \
                        if long_count > 0 else np.float64(0)
        
        avg_short_pct = np.sum(np.abs(short_positions)) / total_abs_amount * 100 \
                        if short_count > 0 else np.float64(0)

        return Position(avg_long_pct, avg_short_pct, long_count, short_count)

    def check(self, factor: AlphaMetadata) -> CompResult:
        try:
            # Long Backtest
            Runner.run_backtest(factor.xml_file, self.config)

            npy_files = factor.get_v2npy_files()
            if not npy_files:
                raise ComplianceSkip("未找到 v2 版本的 npy 文件")

            # 收集持仓信息
            positions: list[Position] = []
            
            # 检查所有文件, 一旦发现问题立即返回
            for npy_file in npy_files:
                position = self._check_position(npy_file)
                if position is None:
                    continue
                positions.append(position)

            if not positions:
                raise ComplianceSkip("持仓全空") # TODO:

            # 计算平均持仓信息
            return CompResult(np.mean([p.long_pct for p in positions], dtype=np.float64),
                            np.mean([p.short_pct for p in positions], dtype=np.float64),
                            np.mean([p.long_count for p in positions], dtype=int),
                            np.mean([p.short_count for p in positions], dtype=int),
                            len(npy_files))

        except BacktestError as e:
            raise ComplianceFail(e)