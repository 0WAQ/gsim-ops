from abc import ABC, abstractmethod

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.base import Result


class CheckFail(Exception):
    def __init__(self, stage: str, *args: object):
        self.stage = stage
        super().__init__(*args)
    
    def __repr__(self):
        if len(self.args) == 0:
            return ""
        if len(self.args) == 1:
            print(self.args[0])
            return repr(self.args[0])
        return repr(self.args)

class CheckSkip(Exception):
    def __init__(self, stage: str, *args: object):
        self.stage = stage
        super().__init__(*args)

    def __repr__(self):
        if len(self.args) == 0:
            return ""
        if len(self.args) == 1:
            return repr(self.args[0])
        return repr(self.args)


class Checker(ABC):
    @abstractmethod
    def check(self, factor: AlphaMetadata) -> Result | None:
        ...

    def clean(self, factor: AlphaMetadata) -> None:
        """stage 结束后的清理钩子(默认 no-op)。pipeline 对 checkpoint 调用;
        原先该方法不在 ABC 上、只有 CheckpointChecker 恰好实现 —— 未声明的契约
        (full-review 第二部分)。"""
