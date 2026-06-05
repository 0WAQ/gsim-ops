import shutil
from pathlib import Path

from .base import *
from ops.utils.func import md5sum
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.checkpoint import *


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
            raise CheckpointSkip(f"old={'OK' if old else 'NONE'} new={'OK' if new else 'NONE'}")

        if old != new:
            raise CheckpointFail()

        return PointResult()
    
    def clean(self, factor: AlphaMetadata):
        shutil.rmtree(factor.alpha_dir, ignore_errors=True)
        Path(factor.pnl_file).unlink(missing_ok=True)
        shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)