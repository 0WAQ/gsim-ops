from abc import ABC, abstractmethod

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.base import Result


class CheckFail(Exception):
    """因子质量失败信号 → REJECTED(retryable stage 除外)。

    result(可选):checker 在失败前已测得的结果对象
    (如 CorrResult —— metrics + bcorr),流水线捕获后落"测得快照"
    (factor_snapshot:最近一次 check 测得的表现,被拒也写)。
    不携带则按该 stage 没测出指标处理(checkbias/checkpoint 等)。

    失败发生在哪个 stage 由流水线在捕获时按"当前正在跑的 stage"归因,
    exception 自己不携带 stage —— 若异常自带 stage 字符串,checker 代码复制到
    新 stage 时旧字符串跟着走就是静默路由错误;流水线是唯一捕获方且始终知道
    当前 stage,由它归因不可能错位。
    """

    def __init__(self, reason, result=None):
        super().__init__(reason)
        self.result = result


class CheckSkip(Exception):
    """环境/数据不足、无法判定的信号 → revert SUBMITTED 待重跑。

    stage 归因同 CheckFail,由流水线在捕获时完成。
    """


class Checker(ABC):
    @abstractmethod
    def check(self, factor: AlphaMetadata) -> Result | None:
        ...

    def clean(self, factor: AlphaMetadata) -> None:
        """stage 通过后的清理钩子(默认 no-op)。流水线对每个 stage 统一调用;
        目前只有 CheckpointChecker 实现(清掉断点短回测的 dump/pnl/checkpoint
        残留,防污染 long_backtest)。"""
