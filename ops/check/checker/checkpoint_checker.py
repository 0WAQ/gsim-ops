from .base import *
from ...common.utils import md5sum
from ...common.config import Config
from ...common.runner import Runner
from ...common.alpha.metadata import AlphaMetadata
from ...common.alpha.results.checkpoint import *


class CheckpointSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("checkpoint", *args)

class CheckpointFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("checkpoint", *args)


class CheckpointChecker(Checker):
    def __init__(self, config: Config):
        self.config = config

    def _get_v1md5(self, factor: AlphaMetadata) -> str | None:
        file = factor.get_last_v1npy_file()
        md5 = None
        if file:
            md5 = md5sum(file)
        return md5

    def _get_v2md5(self, factor: AlphaMetadata) -> str | None:
        file = factor.get_last_v2npy_file()
        md5 = None
        if file:
            md5 = md5sum(file)
        return md5

    def check(self, factor: AlphaMetadata) -> PointResult:
        old = self._get_v2md5(factor)
        Runner.run_backtest(factor.xml_file, self.config)
        new = self._get_v2md5(factor)

        if not old or not new:
            raise CheckpointSkip()
        
        if old != new:
            raise CheckpointFail()

        return PointResult()