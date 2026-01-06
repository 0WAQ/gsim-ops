#!/usr/bin/env python3
"""
因子持仓合规性检测脚本 (快速失败版 + 平均持仓统计)
检测项目：
1. 个股最大持仓不得超过 5%
2. 多+空持股数量不得小于 100 只
3. 多头持股数量不得小于 50 只
4. 空头持股数量不得小于 50 只

新增功能：
- 返回平均多头/空头持仓比例
- 返回平均多头/空头持股数量

注意：只检测 v2 版本的文件
"""
import numpy as np
from typing import Any
from pathlib import Path

from ...common.config import Config
from ...common.alpha.metadata import AlphaMetadata
from ...common.alpha.results.compliance import *

class ComplianceChecker:
    def __init__(self, config: Config):
        self.max_position_pct: float = config.compliance["max_position_pct"]
        self.min_total_stocks: int = config.compliance["min_total_stocks"]
        self.min_long_stocks: int = config.compliance["min_long_stocks"]
        self.min_short_stocks: int = config.compliance["min_short_stocks"]
    
    def _check_position(self, npy_file: Path) \
            -> tuple[dict[str, np.float64 | int], str | None] | None:
        """
        检查单个 alpha 文件的持仓
        Returns:
            stats: {
                'avg_long_pct': float,      # 平均多头持仓比例
                'avg_short_pct': float,     # 平均空头持仓比例
                'long_count': int,          # 多头数量
                'short_count': int          # 空头数量
            },
            error_message: str | None
        """
        try:
            data: np.ndarray = np.load(npy_file)
        except:
            return None

        # 检查数据是否为空或全为 NaN
        if data.size == 0:
            return None
        
        valid_data = data[~np.isnan(data)]
        if valid_data.size == 0:
            return None

        # 计算总金额 (买入 + 卖出的绝对值)
        total_abs_amount = np.sum(np.abs(valid_data))
        if total_abs_amount == 0:
            return None
        
        date = npy_file.name[0:8]

        # 分离多空持仓
        long_positions = valid_data[valid_data > 0]
        short_positions = valid_data[valid_data < 0]
        
        long_count = long_positions.size
        short_count = short_positions.size
        
        # 计算平均持仓比例
        avg_long_pct = (np.sum(long_positions) / total_abs_amount * 100) if long_count > 0 else 0
        avg_short_pct = (np.sum(np.abs(short_positions)) / total_abs_amount * 100) if short_count > 0 else 0

        # 统计信息
        stats = {
            'avg_long_pct': avg_long_pct,
            'avg_short_pct': avg_short_pct,
            'long_count': long_count,
            'short_count': short_count,
        }

        # 1. 检查个股最大持仓
        max_abs_position = np.max(np.abs(valid_data))
        max_position_pct = max_abs_position / total_abs_amount
        if max_position_pct > self.max_position_pct:
            return stats, \
                f"{date} : 个股最大持仓 {max_position_pct*100:.2f}% 超过 {self.max_position_pct*100}%"
        
        # 2. 检查总持股数量
        total_stock_count = long_count + short_count
        if total_stock_count < self.min_total_stocks:
            return stats, \
                f"{date} : 总持股数量 {total_stock_count} 只 (多头 {long_count} + 空头 {short_count}) 少于 {self.min_total_stocks} 只"
        
        # 3. 检查多头持股数量
        if long_count < self.min_long_stocks:
            return stats, \
                f"{date} : 多头持股数量 {long_count} 只少于 {self.min_long_stocks} 只"
        
        # 4. 检查空头持股数量
        if short_count < self.min_short_stocks:
            return stats, \
                f"{date} : 空头持股数量 {short_count} 只少于 {self.min_short_stocks} 只"

        return stats, None
    
    def _calculate_avg_stats(self, stats_list: list[dict[str, Any]]) -> dict[str, Any]:
        """计算平均统计信息"""
        if not stats_list:
            return {
                'avg_long_pct': 0,
                'avg_short_pct': 0,
                'avg_long_count': 0,
                'avg_short_count': 0
            }

        avg_long_pct = np.mean([s['avg_long_pct'] for s in stats_list])
        avg_short_pct = np.mean([s['avg_short_pct'] for s in stats_list])
        avg_long_count = np.mean([s['long_count'] for s in stats_list])
        avg_short_count = np.mean([s['short_count'] for s in stats_list])

        return {
            'avg_long_pct': avg_long_pct,
            'avg_short_pct': avg_short_pct,
            'avg_long_count': avg_long_count,
            'avg_short_count': avg_short_count
        }
    
    def check_one(self, factor: AlphaMetadata) -> tuple[CompStatus, str, CompResult | None]:
        npy_files = factor.get_v2npy_files()
        if not npy_files:
            return CompStatus.SKIP, "未找到 v2 版本的 npy 文件", None

        # 收集持仓信息
        pos_stats: list[dict[str, np.float64 | int]] = []
        
        # 检查所有文件, 一旦发现问题立即返回
        for npy_file in npy_files:
            ret = self._check_position(npy_file)
            if ret is None:
                continue

            pos_stat, error = ret
            pos_stats.append(pos_stat)

            # 若任意一天持仓不符合条件, 则退出
            if error is not None:
                avg_stats = self._calculate_avg_stats(pos_stats)
                return CompStatus.FAIL, error, \
                       CompResult(avg_stats['avg_long_pct'],
                                  avg_stats['avg_short_pct'],
                                  avg_stats['avg_long_count'],
                                  avg_stats['avg_short_count'])
        

        # 所有文件都通过, 计算平均值
        avg_stats: dict[str, np.float64] = self._calculate_avg_stats(pos_stats)
        return CompStatus.PASS, "", \
               CompResult(avg_stats['avg_long_pct'],
                          avg_stats['avg_short_pct'],
                          avg_stats['avg_long_count'],
                          avg_stats['avg_short_count'],
                          len(npy_files))
