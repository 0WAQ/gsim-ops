#!/usr/bin/env python3
"""
因子相关性检测脚本 - 精简版 (集成用)
"""
import sys
from ...common.config import Config
from ...common.runner import Runner
from ...common.alpha.metadata import AlphaMetadata
from ...common.alpha.results.correlation import *

class CorrelationChecker:
    def __init__(self, config: Config):
        self.corr_threshold: float = config.correlation["corr_threshold"]
        self.config = config    # TODO:

        # 缓存因子库的指标
        self._prod_metrics_cache: dict[str, Metrics] = {}
 
    def _get_prod_factor_metrics(self, factor_name: str) -> Metrics | None:
        """获取生产因子库指标 (带缓存)"""
        if factor_name in self._prod_metrics_cache:
            return self._prod_metrics_cache[factor_name]
        factor_path = self.config.pnl_prod_path / factor_name
        if not factor_path.exists():
            return None
        
        metrics = Runner.run_simsummary(factor_path, self.config)
        if metrics:
            self._prod_metrics_cache[factor_name] = metrics
        return metrics
    
    def _check_beat(self, metrics: Metrics, other: Metrics):
        """检查是否打败竞争因子 (至少2项优于)"""
        if not other:
            return False
        
        win_count = sum([
            metrics.fitness > other.fitness,
            metrics.ret > other.ret,
            metrics.shrp > other.shrp
        ])
        
        return win_count >= 2
    
    def check_one(self, factor: AlphaMetadata) -> tuple[CorrStatus, str, CorrResult | None]:
        # 1. 运行 bcorr
        corrs = Runner.run_bcorr(factor.pnl_file, self.config)
        if corrs is None:
            return CorrStatus.ERROR, "bcorr 运行失败", None
        if not corrs:
            return CorrStatus.ERROR, "无相关性数据", None

        # 2. 获取当前因子指标
        metrics = Runner.run_simsummary(factor.pnl_file, self.config)
        if not metrics:
            return CorrStatus.ERROR, "无法获取因子指标", None
        
        # 3. 找出最大相关系数
        max_corr_factor, max_corr_raw = max(corrs, key=lambda x: abs(x[1]))
        max_corr = abs(max_corr_raw)
        
        # 4. 如果相关性低，直接通过
        if max_corr < self.corr_threshold:
            result = CorrResult(max_corr, max_corr_factor, metrics, 0)
            return CorrStatus.PASS, "", result

        # 5. 找出所有高相关因子
        high_corr_factors = [(fname, abs(corr)) for fname, corr in corrs 
                            if abs(corr) >= self.corr_threshold]
        
        # 6. 检查是否能打败所有高相关因子
        beat_all = True
        unbeaten_factors: list[tuple[str, float, Metrics]] = []
        
        for competitor_name, corr in high_corr_factors:
            other = self._get_prod_factor_metrics(competitor_name)
            if not other:
                continue
            if not self._check_beat(metrics, other):
                beat_all = False
                unbeaten_factors.append((competitor_name, corr, other))
        
        return CorrStatus.BEAT if beat_all else CorrStatus.FAIL, "", \
               CorrResult(max_corr, max_corr_factor,
                        metrics, len(high_corr_factors),
                        unbeaten_factors[0] if unbeaten_factors else None)
