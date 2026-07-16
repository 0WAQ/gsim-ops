import numpy as np

from .base import Result


class CompResult(Result):
    """compliance stage 的持仓摘要(有效日全史均值;流水线不消费,接口占位)。"""

    def __init__(self, avg_long_pct: np.float64, avg_short_pct: np.float64,
                 long_count: int, short_count: int,
                 total_checked: int = 0):
        self.avg_long_pct = avg_long_pct
        self.avg_short_pct = avg_short_pct
        self.long_count = long_count
        self.short_count = short_count
        self.total_checked = total_checked
