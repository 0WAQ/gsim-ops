from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.correlation import *


class CorrelationSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("correlation", *args)

class CorrelationFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("correlation", *args)


class CorrelationChecker(Checker):
    def __init__(self, config: Config):
        self.ret: float = config.correlation["ret%"]
        self.tvr: float = config.correlation["tvr%"]
        self.shrp: float = config.correlation["shrp"]
        self.corr_threshold: float = config.correlation["corr_threshold"]
        self.config = config    # TODO:

        # 缓存因子库的指标
        self._prod_metrics_cache: dict[str, Metrics] = {}
 
    def passed(self, m: Metrics) -> bool:
        if m.ret >= self.ret and m.shrp > self.shrp:
            return True
        return False

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
    
    def check(self, factor: AlphaMetadata) -> CorrResult:
        # 1. 运行 bcorr
        corrs = Runner.run_bcorr(factor.pnl_file, self.config)
        if corrs is None:
            raise CorrelationSkip("bcorr 运行失败")
        if not corrs:
            raise CorrelationSkip("无相关性数据")

        # 2. 获取当前因子指标
        metrics = Runner.run_simsummary(factor.pnl_file, self.config)
        if not metrics:
            raise CorrelationSkip("无法获取因子指标")
        
        # 3. 判断是否满足要求
        if not self.passed(metrics):
            raise CorrelationFail(CorrResult(metrics))

        # 4. 找出最大相关系数
        max_corr_factor, max_corr = max(corrs, key=lambda x: abs(x[1]))
        
        # 5. 如果相关性低，直接通过
        if abs(max_corr) < self.corr_threshold:
            return CorrResult(metrics, max_corr, max_corr_factor, 0)

        # 6. 找出所有高相关因子
        high_corr_factors = [(factor_name, abs(corr)) for factor_name, corr in corrs 
                            if abs(corr) >= self.corr_threshold]
        
        # 7. 检查是否能打败所有高相关因子
        for competitor_name, corr in high_corr_factors:
            competitor_metrices = self._get_prod_factor_metrics(competitor_name)
            if not competitor_metrices:
                continue

            # 未打败
            if not self._check_beat(metrics, competitor_metrices):
                raise CorrelationFail(CorrResult(
                                        metrics,
                                        max_corr, max_corr_factor,
                                        len(high_corr_factors),
                                        (competitor_name, corr, competitor_metrices)))

        return CorrResult(metrics,
                          max_corr,max_corr_factor,
                          len(high_corr_factors))
