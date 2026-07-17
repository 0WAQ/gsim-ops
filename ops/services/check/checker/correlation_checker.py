from pathlib import Path

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.correlation import CorrResult
from ops.core.metrics import Metrics
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, resolve_bcorr_pools

from .base import Checker, CheckFail, CheckSkip


class CorrelationChecker(Checker):
    def __init__(self, config: Config):
        self.ret: float = config.correlation["ret%"]
        self.tvr_d0: float = config.correlation["tvr_d0%"]
        self.tvr_d1: float = config.correlation["tvr_d1%"]
        self.shrp: float = config.correlation["shrp"]
        self.corr_threshold: float = config.correlation["corr_threshold"]
        self.config = config    # TODO:

        # 缓存因子库的指标
        self._prod_metrics_cache: dict[str, Metrics] = {}

    def _tvr_cap(self, delay: int) -> float:
        return self.tvr_d0 if delay == 0 else self.tvr_d1

    def _gate_violations(self, m: Metrics, delay: int) -> list[str]:
        v: list[str] = []
        if m.ret < self.ret:
            v.append(f"ret%={m.ret:.2f} < {self.ret}")
        if m.shrp <= self.shrp:
            v.append(f"shrp={m.shrp:.2f} <= {self.shrp}")
        cap = self._tvr_cap(delay)
        if m.tvr > cap:
            v.append(f"tvr%={m.tvr:.2f} > {cap} (delay={delay})")
        return v

    def _get_prod_factor_metrics(self, factor_name: str,
                                 pools: list[Path]) -> Metrics | None:
        """获取竞品因子指标 (带缓存);在同类对比池里逐个找 pool/factor_name"""
        if factor_name in self._prod_metrics_cache:
            return self._prod_metrics_cache[factor_name]
        for pool in pools:
            factor_path = pool / factor_name
            if not factor_path.exists():
                continue
            metrics = Runner.run_simsummary(factor_path, self.config)
            if metrics:
                self._prod_metrics_cache[factor_name] = metrics
                return metrics
        return None
    
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
        # 0. 按因子来源解析同类对比池 (automated/manual 各比各的, legacy 回退全库)
        pools = resolve_bcorr_pools(self.config, factor.discovery_method)

        # 1. 运行 bcorr
        corrs = Runner.run_bcorr(factor.pnl_file, self.config, pools=pools)
        if corrs is None:
            raise CheckSkip("bcorr 运行失败")
        # 排除自己:restage/--overwrite 若有旧 pnl 残留在池里,自相关≈1 会把
        # 重检因子挡死("打败几乎相同的自己"不可能)。离库回收是主修,
        # 此处是防删除失败残留的双保险 —— 因子永远不该和自己比相关性。
        corrs = [(n, c) for n, c in corrs if n != factor.name]
        if not corrs:
            raise CheckSkip("无相关性数据")

        # 2. 获取当前因子指标
        metrics = Runner.run_simsummary(factor.pnl_file, self.config)
        if not metrics:
            raise CheckSkip("无法获取因子指标")

        # 3. 判断是否满足要求
        violations = self._gate_violations(metrics, factor.delay)
        if violations:
            # 业绩门槛失败:result 携带测得值
            # (bcorr 此时已算出,corrs[-1] 是排序后的最大值)
            max_f, max_c = corrs[-1]
            raise CheckFail(f"{'; '.join(violations)} | {metrics}",
                            result=CorrResult(metrics, max_c, max_f))

        # 4. 找出最大相关系数 (bcorr 输出已排序，取最后一行)
        max_corr_factor, max_corr = corrs[-1]

        # 5. 如果相关性低，直接通过
        if abs(max_corr) < self.corr_threshold:
            return CorrResult(metrics, max_corr, max_corr_factor, 0)

        # 6. 找出所有高相关因子
        high_corr_factors = [(factor_name, abs(corr)) for factor_name, corr in corrs 
                            if abs(corr) >= self.corr_threshold]
        
        # 7. 检查是否能打败所有高相关因子
        for competitor_name, corr in high_corr_factors:
            competitor_metrices = self._get_prod_factor_metrics(competitor_name, pools)
            if not competitor_metrices:
                continue

            # 未打败(消息风格契约见 base.CheckFail:违反项 | 上下文。
            # 原来直接 str(CorrResult) 只倾倒数据、不说拒因,已改)
            if not self._check_beat(metrics, competitor_metrices):
                _cr = CorrResult(
                    metrics,
                    max_corr, max_corr_factor,
                    len(high_corr_factors),
                    (competitor_name, corr, competitor_metrices))
                c = competitor_metrices
                raise CheckFail(
                    f"bcorr={corr:.2f} >= {self.corr_threshold} (vs {competitor_name}) "
                    f"且未打败(fitness/ret/shrp 三项需胜二) | "
                    f"本因子 fitness={metrics.fitness}, ret={metrics.ret}%, shrp={metrics.shrp}; "
                    f"竞品 fitness={c.fitness}, ret={c.ret}%, shrp={c.shrp}",
                    result=_cr)

        return CorrResult(metrics,
                          max_corr,max_corr_factor,
                          len(high_corr_factors))
