from ...metrics import Metrics
from .base import Result


class CorrResult(Result):
    """correlation stage 的结果:业绩指标 + bcorr。

    流水线捕获后喂 `_persist_derived` —— max_bcorr/max_bcorr_factor 随快照
    落 factor_snapshot(入库时 bcorr,零额外计算)。
    """

    def __init__(self,
                metrics: Metrics,
                max_bcorr: float | None = None,
                max_bcorr_factor: str | None = None,
                high_corr_count: int | None = None,
                unbeaten_example: tuple[str, float, Metrics] | None = None):
        self.metrics = metrics
        self.max_bcorr = max_bcorr
        self.max_bcorr_factor = max_bcorr_factor
        self.high_corr_count = high_corr_count
        self.unbeaten_example = unbeaten_example

    def __repr__(self):
        if self.max_bcorr is None:
            return f"{self.metrics}"
        return f"bcorr={self.max_bcorr}, {self.metrics}"

    def __str__(self):
        return self.__repr__()
