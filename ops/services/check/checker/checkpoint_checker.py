import shutil
from pathlib import Path

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.checkpoint import PointResult
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.utils.func import md5sum

from .base import Checker, CheckFail, CheckSkip


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
            raise CheckSkip(f"old={'OK' if old else 'NONE'} new={'OK' if new else 'NONE'}")

        if old != new:
            # 原 CheckpointFail() 不带消息,报告里 fail_reason 是空串
            raise CheckFail(f"断点续跑与整跑结果不一致: v2 md5 {old[:8]}… != {new[:8]}…")

        return PointResult()
    
    def clean(self, factor: AlphaMetadata):
        shutil.rmtree(factor.alpha_dir, ignore_errors=True)
        Path(factor.pnl_file).unlink(missing_ok=True)
        shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)